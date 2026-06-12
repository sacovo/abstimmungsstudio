import io
import datetime
import numpy as np
import pandas as pd
import polars as pl
from django.conf import settings
from django.db.models import Q

from abst.models import Vorlage, Gemeinde, Partei
from abst.store import get_influx_client, get_abst_results

# Canton region mapping for national-level chunking
REGION_MAPPING = {
    'Zürich': 'Zürich',
    'Bern': 'Bern',
    'Luzern': 'Zentralschweiz',
    'Uri': 'Zentralschweiz',
    'Schwyz': 'Zentralschweiz',
    'Obwalden': 'Zentralschweiz',
    'Nidwalden': 'Zentralschweiz',
    'Zug': 'Zentralschweiz',
    'Aargau': 'Aargau',
    'Thurgau': 'Ostschweiz',
    'St. Gallen': 'Ostschweiz',
    'Appenzell Ausserrhoden': 'Ostschweiz',
    'Appenzell Innerrhoden': 'Ostschweiz',
    'Schaffhausen': 'Ostschweiz',
    'Glarus': 'Ostschweiz',
    'Graubünden': 'Graubünden',
    'Jura': 'Jura',
    'Solothurn': 'Solothurn',
    'Basel-Stadt': 'Basel',
    'Basel-Landschaft': 'Basel',
    'Ticino': 'Ticino',
    'Vaud': 'Vaud',
    'Fribourg': 'Fribourg',
    'Neuchâtel': 'Neuchâtel',
    'Genève': 'Genève',
    'Valais': 'Valais',
}

_r_initialized = False
lphom = None
ro = None
pandas2ri = None

def init_r():
    global _r_initialized, lphom, ro, pandas2ri
    if _r_initialized:
        return
    try:
        import rpy2.robjects as ro_mod
        from rpy2.robjects.packages import importr
        from rpy2.robjects import pandas2ri as p2ri
        
        ro = ro_mod
        pandas2ri = p2ri
        lphom = importr('lphom')
        _r_initialized = True
    except Exception as e:
        raise RuntimeError(f"R or rpy2 is not configured properly in this environment: {e}")

def get_behavior_options(target_id: int):
    target = Vorlage.objects.select_related("tag").get(vorlagen_id=target_id)
    options = []
    
    # 1. Elections (Top of dropdown)
    election_date = datetime.date(2023, 10, 22)
    if target.tag.date >= election_date:
        options.append({
            "id": "election_nrw2023",
            "name": f"Wahl: Nationalratswahl 2023 ({election_date.strftime('%d.%m.%Y')})",
            "type": "election",
            "election_year": 2023
        })
        
    # 2. Past votes
    past_votes_q = Q(finished=True, tag__date__lt=target.tag.date)
    if target.region != 'CH':
        past_votes_q &= (Q(region=target.region) | Q(region='CH'))
    else:
        past_votes_q &= Q(region='CH')
        
    past_votes = Vorlage.objects.filter(past_votes_q).order_by('-tag__date', 'vorlagen_id')
    
    # Add past votes
    for vote in past_votes:
        options.append({
            "id": f"vote_{vote.vorlagen_id}",
            "name": f"Abstimmung: {vote.name} ({vote.region}, {vote.tag.date.strftime('%d.%m.%Y')})",
            "type": "vote",
            "vote_id": vote.vorlagen_id
        })
        
    return options

def query_election_strengths_df():
    """
    Query wahlen_result from InfluxDB, pivoting by party_id.
    """
    with get_influx_client() as client:
        query_api = client.query_api()
        query = f'''
        from(bucket: "{settings.INFLUX_BUCKET}")
          |> range(start: -100y)
          |> filter(fn: (r) => r._measurement == "wahlen_result" and r._field == "partei_staerke")
          |> pivot(rowKey:["geo_id"], columnKey: ["partei_id"], valueColumn: "_value")
        '''
        result = query_api.query_data_frame(query)
        if isinstance(result, list):
            if len(result) == 0:
                return pl.DataFrame()
            result = pd.concat(result)
        if len(result) == 0:
            return pl.DataFrame()
        return pl.from_pandas(result)

def calculate_behavior(
    target_id: int, 
    source_type: str, 
    source_id: int | None = None, 
    wahlen_scope: str = "partei"
):
    # Initialize R and check dependencies
    init_r()
    
    target_vorlage = Vorlage.objects.select_related("tag__stand").get(vorlagen_id=target_id)
    target_df = get_abst_results(target_id)
    if target_df is None or target_df.is_empty():
        raise ValueError("Target vote has no results stored.")
        
    # Calculate enthaltung in target: eligible - yes - no
    target_df = target_df.with_columns(
        target_enthaltung=(pl.col("anzahl_stimmberechtigte") - pl.col("ja_stimmen") - pl.col("nein_stimmen"))
    ).select([
        pl.col("geo_id").cast(pl.Int32),
        pl.col("ja_stimmen").alias("target_ja"),
        pl.col("nein_stimmen").alias("target_nein"),
        pl.col("target_enthaltung").alias("target_enthaltung"),
        pl.col("anzahl_stimmberechtigte").alias("target_stimmberechtigte")
    ])
    
    # Get geodata for region grouping
    gemeinden = Gemeinde.objects.filter(stand=target_vorlage.tag.stand).values("geo_id", "kanton")
    if not gemeinden:
        raise ValueError("No municipality information found for target vote GeoStand.")
    df_geo = pl.DataFrame(list(gemeinden)).with_columns(
        pl.col("geo_id").cast(pl.Int32)
    )
    df_geo = df_geo.with_columns(
        pl.col("kanton").replace_strict(REGION_MAPPING, default='???').alias("region")
    )
    
    # Define source columns and labels
    if source_type == 'vote':
        if not source_id:
            raise ValueError("source_id is required when source_type is 'vote'")
        source_vorlage = Vorlage.objects.get(vorlagen_id=source_id)
        source_df = get_abst_results(source_id)
        if source_df is None or source_df.is_empty():
            raise ValueError("Source vote has no results stored.")
            
        source_df = source_df.with_columns(
            source_enthaltung=(pl.col("anzahl_stimmberechtigte") - pl.col("ja_stimmen") - pl.col("nein_stimmen"))
        ).select([
            pl.col("geo_id").cast(pl.Int32),
            pl.col("ja_stimmen").alias("ja"),
            pl.col("nein_stimmen").alias("nein"),
            pl.col("source_enthaltung").alias("enthaltung")
        ])
        
        # Source labels and columns
        source_cols = ["ja", "nein", "enthaltung"]
        source_labels = ["Ja (Quelle)", "Nein (Quelle)", "Enthaltung (Quelle)", "Neuwähler"]
        target_labels = ["Ja (Ziel)", "Nein (Ziel)", "Enthaltung (Ziel)", "Exit"]
        
        # Join dataframes
        df_joined = df_geo.join(source_df, on="geo_id", how="inner").join(target_df, on="geo_id", how="inner")
        df_joined = df_joined.drop_nulls(subset=["ja", "nein", "enthaltung", "target_ja", "target_nein", "target_enthaltung"])
        
    elif source_type == 'election':
        # National Council Election 2023
        wahlen_df = query_election_strengths_df()
        if wahlen_df.is_empty():
            raise ValueError("No election results found in database.")
            
        # Get parties dictionary to map party_id to target name depending on scope
        parties = Partei.objects.all()
        col_to_scope_name = {}
        for p in parties:
            pid_str = str(p.partei_id)
            if wahlen_scope == 'partei':
                col_to_scope_name[pid_str] = p.kurzname or p.name
            elif wahlen_scope == 'parteigruppe':
                if p.parteigruppen_name:
                    name = p.parteigruppen_name.strip()
                    name = name.replace("FDP. Die Liberalen", "FDP.Die Liberalen")
                    col_to_scope_name[pid_str] = name
            elif wahlen_scope == 'lager':
                if p.parteipolitische_lager_name:
                    name = p.parteipolitische_lager_name.strip()
                    name = name.replace("MItte", "Mitte")
                    col_to_scope_name[pid_str] = name
        
        # Find which columns in wahlen_df are in the mapping
        matching_cols = [c for c in wahlen_df.columns if c in col_to_scope_name]
        if not matching_cols:
            raise ValueError("No matching parties found in election results for selected scope.")
            
        wahlen_df = wahlen_df.with_columns(
            pl.col("geo_id").cast(pl.Int32)
        )
        
        # Group and sum columns of wahlen_df by their scope name
        unique_scope_names = sorted(list(set(col_to_scope_name[c] for c in matching_cols)))
        sum_exprs = []
        for scope_name in unique_scope_names:
            cols_to_sum = [pl.col(c) for c in matching_cols if col_to_scope_name[c] == scope_name]
            if cols_to_sum:
                sum_exprs.append(
                    pl.sum_horizontal([c.fill_null(0.0) for c in cols_to_sum]).alias(scope_name)
                )
                
        wahlen_df_scoped = wahlen_df.select([pl.col("geo_id")] + sum_exprs)
        
        # Join wahlen and target vote
        df_joined = df_geo.join(wahlen_df_scoped, on="geo_id", how="inner").join(target_df, on="geo_id", how="inner")
        # Drop rows where target columns are null
        df_joined = df_joined.drop_nulls(subset=["target_ja", "target_nein", "target_enthaltung", "target_stimmberechtigte"])
        # Fill missing party strengths with 0.0
        df_joined = df_joined.fill_null(0.0)
        
        # Calculate absolute voter counts for each party using the target vote's stimmberechtigte
        # Turnout for the 2023 National Council election is 46.6% (0.466)
        election_turnout = 0.466
        
        # Map party ID columns to their names
        for scope_name in unique_scope_names:
            df_joined = df_joined.with_columns(
                (pl.col("target_stimmberechtigte") * election_turnout * (pl.col(scope_name) / 100.0)).alias(scope_name)
            )
            
        # Calculate non-voters (nichtwahler) = stimmberechtigte * (1 - election_turnout)
        df_joined = df_joined.with_columns(
            (pl.col("target_stimmberechtigte") * (1.0 - election_turnout)).alias("Nichtwähler")
        )
        
        source_cols = unique_scope_names + ["Nichtwähler"]
        source_labels = source_cols + ["Neuwähler"]
        target_labels = ["Ja (Ziel)", "Nein (Ziel)", "Enthaltung (Ziel)", "Exit"]
    else:
        raise ValueError(f"Invalid source_type: {source_type}")
        
    df_joined = df_joined.drop_nulls(subset=["target_ja", "target_nein", "target_enthaltung"])
    if df_joined.is_empty():
        raise ValueError("No matching municipalities found between source and target.")
        
    expected_rows = len(source_cols) + 1
    expected_cols = 4  # ja, nein, enthaltung, exit

    def pad_matrix(mat, exp_rows, exp_cols):
        rows, cols = mat.shape
        if rows == exp_rows and cols == exp_cols:
            return mat
        padded = np.zeros((exp_rows, exp_cols))
        padded[:rows, :cols] = mat
        return padded

    # Helper to run lphom on a single region dataframe
    def estimate_voter_behavior_chunk(chunk_df):
        vote1 = chunk_df.select(source_cols)
        vote2 = chunk_df.select(["target_ja", "target_nein", "target_enthaltung"])
        
        with (ro.default_converter + pandas2ri.converter).context():
            v1 = ro.conversion.py2rpy(vote1.to_pandas())
            v2 = ro.conversion.py2rpy(vote2.to_pandas())
            res = lphom.lphom(v1, v2)
            
        res_dict = dict(zip(res.names(), res.values()))
        return np.array(res_dict['VTM.complete.votes'])
        
    # Run ecological inference
    total_matrix = np.zeros((expected_rows, expected_cols))
    
    # If done on national level, chunk by region
    if target_vorlage.region == 'CH':
        unique_regions = df_joined["region"].unique().to_list()
        for region in unique_regions:
            region_df = df_joined.filter(pl.col("region") == region)
            # Skip if too few rows (lphom needs enough rows to run, e.g. at least 5-10)
            if len(region_df) < 5:
                continue
            try:
                res_matrix = estimate_voter_behavior_chunk(region_df)
                res_matrix = pad_matrix(res_matrix, expected_rows, expected_cols)
                total_matrix += res_matrix
            except Exception as e:
                print(f"Error estimating behavior for region {region}: {e}")
    else:
        # Cantonal level, run directly
        if len(df_joined) < 5:
            raise ValueError("Not enough municipalities to execute the ecological inference algorithm.")
        res_matrix = estimate_voter_behavior_chunk(df_joined)
        total_matrix = pad_matrix(res_matrix, expected_rows, expected_cols)
        
    if total_matrix is None or np.all(total_matrix == 0):
        raise ValueError("Ecological inference calculation failed or returned all zeros.")
        
    # Format response as links for parallel categories (alluvial) diagram
    links = []
    for r_idx, src_lbl in enumerate(source_labels):
        for c_idx, tgt_lbl in enumerate(target_labels):
            val = float(total_matrix[r_idx, c_idx])
            if val > 0.1:
                links.append({
                    "source": src_lbl,
                    "target": tgt_lbl,
                    "value": round(val, 2)
                })
                
    total_votes = float(total_matrix.sum())
    
    return {
        "source_labels": source_labels,
        "target_labels": target_labels,
        "links": links,
        "matrix": total_matrix.tolist(),
        "total_votes": round(total_votes, 2)
    }

def generate_behavior_excel(target_id: int, source_type: str, source_id: int | None = None, wahlen_scope: str = "partei"):
    results = calculate_behavior(target_id, source_type, source_id, wahlen_scope)
    
    output = io.BytesIO()
    matrix = np.array(results["matrix"])
    
    df_matrix = pd.DataFrame(
        matrix,
        index=results["source_labels"],
        columns=results["target_labels"]
    )
    
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_matrix.to_excel(writer, sheet_name="Wählerwanderung")
        workbook = writer.book
        worksheet = writer.sheets["Wählerwanderung"]
        
        header_format = workbook.add_format({
            'bold': True,
            'text_wrap': True,
            'valign': 'top',
            'fg_color': '#D7E4BD',
            'border': 1
        })
        
        for col_num, value in enumerate(df_matrix.columns.values):
            worksheet.write(0, col_num + 1, value, header_format)
            
        worksheet.set_column(0, 0, 30) # Widen the index column
        worksheet.set_column(1, len(df_matrix.columns), 18) # Widen value columns
        
    output.seek(0)
    return output.getvalue()
