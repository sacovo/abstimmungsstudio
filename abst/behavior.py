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
    # Zürich
    'Zürich': 'Zürich',
    
    # Bern
    'Bern': 'Bern',
    'Bern / Berne': 'Bern',
    
    # Zentralschweiz
    'Luzern': 'Zentralschweiz',
    'Uri': 'Zentralschweiz',
    'Schwyz': 'Zentralschweiz',
    'Obwalden': 'Zentralschweiz',
    'Nidwalden': 'Zentralschweiz',
    'Zug': 'Zentralschweiz',
    
    # Aargau
    'Aargau': 'Aargau',
    
    # Ostschweiz
    'Thurgau': 'Ostschweiz',
    'St. Gallen': 'Ostschweiz',
    'Appenzell Ausserrhoden': 'Ostschweiz',
    'Appenzell Innerrhoden': 'Ostschweiz',
    'Schaffhausen': 'Ostschweiz',
    'Glarus': 'Ostschweiz',
    
    # Graubünden
    'Graubünden': 'Graubünden',
    'Graubünden / Grigioni / Grischun': 'Graubünden',
    
    # Jura
    'Jura': 'Jura',
    
    # Solothurn
    'Solothurn': 'Solothurn',
    
    # Basel
    'Basel-Stadt': 'Basel',
    'Basel-Landschaft': 'Basel',
    
    # Ticino
    'Ticino': 'Ticino',
    'Tessin': 'Ticino',
    
    # Vaud / Waadt
    'Vaud': 'Vaud',
    'Waadt': 'Vaud',
    
    # Fribourg / Freiburg
    'Fribourg': 'Fribourg',
    'Freiburg': 'Fribourg',
    'Fribourg / Freiburg': 'Fribourg',
    
    # Neuchâtel / Neuenburg
    'Neuchâtel': 'Neuchâtel',
    'Neuenburg': 'Neuchâtel',
    
    # Genève / Genf
    'Genève': 'Genève',
    'Genf': 'Genève',
    
    # Valais / Wallis
    'Valais': 'Valais',
    'Wallis': 'Valais',
    'Valais / Wallis': 'Valais',
}

_r_initialized = False
lphom = None
ro = None
pandas2ri = None
numpy2ri = None

def init_r():
    global _r_initialized, lphom, ro, pandas2ri, numpy2ri
    if _r_initialized:
        return
    try:
        import rpy2.robjects as ro_mod
        from rpy2.robjects.packages import importr
        from rpy2.robjects import pandas2ri as p2ri
        from rpy2.robjects import numpy2ri as np2ri
        
        ro = ro_mod
        pandas2ri = p2ri
        numpy2ri = np2ri
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
    wahlen_scope: str = "partei",
    return_df: bool = False
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
    gemeinden = Gemeinde.objects.filter(stand=target_vorlage.tag.stand).values("geo_id", "name", "kanton")
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
        df_joined = df_joined.fill_null(0.0).with_columns(
            pl.col("geo_id").cast(pl.Int32)
        )

        
        # Calculate absolute voter counts for each party using the target vote's stimmberechtigte
        # 1. Fetch dynamic election turnout
        from abst.store import query_election_turnout_df
        turnout_df = query_election_turnout_df()
        
        if turnout_df.is_empty():
            # Fallback to constant 46.6% if InfluxDB has not been imported yet
            df_joined = df_joined.with_columns(
                election_turnout=pl.lit(0.466)
            )
        else:
            turnout_df = turnout_df.with_columns(
                pl.col("geo_id").cast(pl.Int32),
                (pl.col("wahlbeteiligung") / 100.0).alias("election_turnout")
            ).select(["geo_id", "election_turnout"])
            
            df_joined = df_joined.join(turnout_df, on="geo_id", how="left")
            df_joined = df_joined.with_columns(
                pl.col("election_turnout").fill_null(0.466)
            )

        # 2. Map party ID columns to their names using dynamic election turnout
        for scope_name in unique_scope_names:
            df_joined = df_joined.with_columns(
                (pl.col("target_stimmberechtigte") * pl.col("election_turnout") * (pl.col(scope_name) / 100.0)).alias(scope_name)
            )
            
        # 3. Calculate non-voters (nichtwahler) dynamically
        df_joined = df_joined.with_columns(
            (pl.col("target_stimmberechtigte") * (1.0 - pl.col("election_turnout"))).alias("Nichtwähler")
        )

        
        source_cols = unique_scope_names + ["Nichtwähler"]
        source_labels = source_cols + ["Neuwähler"]
        target_labels = ["Ja (Ziel)", "Nein (Ziel)", "Enthaltung (Ziel)", "Exit"]
    else:
        raise ValueError(f"Invalid source_type: {source_type}")
        
    df_joined = df_joined.drop_nulls(subset=["target_ja", "target_nein", "target_enthaltung"])
    df_joined = df_joined.filter(pl.col("target_stimmberechtigte") > 0)
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
        
        # Filter out rows that have 0 or negative sums to avoid division-by-zero
        v1_sums = vote1.sum_horizontal()
        v2_sums = vote2.sum_horizontal()
        valid_mask = (v1_sums > 0) & (v2_sums > 0)
        vote1 = vote1.filter(valid_mask)
        vote2 = vote2.filter(valid_mask)
        
        if len(vote1) < 5:
            return np.zeros((expected_rows, expected_cols))
            
        with (ro.default_converter + pandas2ri.converter + numpy2ri.converter).context():
            v1_r = ro.conversion.py2rpy(vote1.to_pandas())
            v2_r = ro.conversion.py2rpy(vote2.to_pandas())
            res = lphom.lphom(v1_r, v2_r)
            res_names = list(ro.r.names(res))
            res_dict = {name: val for name, val in zip(res_names, res)}
            res_matrix = np.array(res_dict['VTM.complete.votes'])
            
        return res_matrix

        
    # Run ecological inference
    total_matrix = np.zeros((expected_rows, expected_cols))
    region_probs = {}
    alpha = 0.15
    
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
                
                # Calculate the smoothed probability matrix for this region
                col_sums = res_matrix.sum(axis=0)
                tot_sum = res_matrix.sum()
                reg_target_margins = col_sums / tot_sum if tot_sum > 0 else np.zeros(expected_cols)
                
                reg_prob = np.zeros_like(res_matrix)
                for r_idx in range(expected_rows):
                    row_sum = res_matrix[r_idx].sum()
                    if row_sum > 0:
                        row_prob = res_matrix[r_idx] / row_sum
                        reg_prob[r_idx] = (1.0 - alpha) * row_prob + alpha * reg_target_margins
                region_probs[region] = reg_prob
            except Exception as e:
                print(f"Error estimating behavior for region {region}: {e}")
    else:
        # Cantonal level, run directly
        if len(df_joined) < 5:
            raise ValueError("Not enough municipalities to execute the ecological inference algorithm.")
        res_matrix = estimate_voter_behavior_chunk(df_joined)
        total_matrix = pad_matrix(res_matrix, expected_rows, expected_cols)
        
        # Calculate the smoothed probability matrix for the canton
        col_sums = total_matrix.sum(axis=0)
        tot_sum = total_matrix.sum()
        reg_target_margins = col_sums / tot_sum if tot_sum > 0 else np.zeros(expected_cols)
        
        reg_prob = np.zeros_like(total_matrix)
        for r_idx in range(expected_rows):
            row_sum = total_matrix[r_idx].sum()
            if row_sum > 0:
                row_prob = total_matrix[r_idx] / row_sum
                reg_prob[r_idx] = (1.0 - alpha) * row_prob + alpha * reg_target_margins
        
        # Store for all unique regions in the cantonal df_joined (usually just one, e.g. the canton's region)
        unique_regions = df_joined["region"].unique().to_list()
        for r in unique_regions:
            region_probs[r] = reg_prob
            
    if total_matrix is None or np.all(total_matrix == 0):
        raise ValueError("Ecological inference calculation failed or returned all zeros.")
        
    # Apply post-inference smoothing/blending to prevent unrealistic 0%/100% transitions
    col_sums = total_matrix.sum(axis=0)
    tot_sum = total_matrix.sum()
    target_margins = col_sums / tot_sum if tot_sum > 0 else np.zeros(expected_cols)

    smoothed_matrix = np.zeros_like(total_matrix)
    for r_idx in range(expected_rows):
        row_sum = total_matrix[r_idx].sum()
        if row_sum > 0:
            row_prob = total_matrix[r_idx] / row_sum
            smoothed_prob = (1.0 - alpha) * row_prob + alpha * target_margins
            smoothed_matrix[r_idx] = smoothed_prob * row_sum
        else:
            smoothed_matrix[r_idx] = total_matrix[r_idx]

    total_matrix = smoothed_matrix

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
    
    res_dict = {
        "source_labels": source_labels,
        "target_labels": target_labels,
        "links": links,
        "matrix": total_matrix.tolist(),
        "total_votes": round(total_votes, 2)
    }
    if return_df:
        res_dict["df_joined"] = df_joined
        res_dict["source_cols"] = source_cols
        res_dict["region_probs"] = region_probs
    return res_dict

def generate_behavior_excel(target_id: int, source_type: str, source_id: int | None = None, wahlen_scope: str = "partei"):
    results = calculate_behavior(target_id, source_type, source_id, wahlen_scope, return_df=True)
    
    output = io.BytesIO()
    matrix = np.array(results["matrix"])
    expected_rows, expected_cols = matrix.shape
    
    df_matrix = pd.DataFrame(
        matrix,
        index=results["source_labels"],
        columns=results["target_labels"]
    )
    
    # Calculate row percentages (probabilities)
    matrix_pct = np.zeros_like(matrix)
    for r_idx in range(matrix.shape[0]):
        row_sum = matrix[r_idx].sum()
        if row_sum > 0:
            matrix_pct[r_idx] = matrix[r_idx] / row_sum
            
    df_matrix_pct = pd.DataFrame(
        matrix_pct,
        index=results["source_labels"],
        columns=results["target_labels"]
    )
    
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        # 1. Absolute counts sheet
        df_matrix.to_excel(writer, sheet_name="Wählerwanderung (Absolut)")
        workbook = writer.book
        worksheet_abs = writer.sheets["Wählerwanderung (Absolut)"]
        
        header_format = workbook.add_format({
            'bold': True,
            'text_wrap': True,
            'valign': 'top',
            'fg_color': '#D7E4BD',
            'border': 1
        })
        
        for col_num, value in enumerate(df_matrix.columns.values):
            worksheet_abs.write(0, col_num + 1, value, header_format)
            
        worksheet_abs.set_column(0, 0, 30) # Widen the index column
        worksheet_abs.set_column(1, len(df_matrix.columns), 18) # Widen value columns
        
        # 2. Percentage (probabilities) sheet
        df_matrix_pct.to_excel(writer, sheet_name="Wählerwanderung (Prozent)")
        worksheet_pct = writer.sheets["Wählerwanderung (Prozent)"]
        
        for col_num, value in enumerate(df_matrix_pct.columns.values):
            worksheet_pct.write(0, col_num + 1, value, header_format)
            
        worksheet_pct.set_column(0, 0, 30) # Widen the index column
        
        pct_format = workbook.add_format({
            'num_format': '0.0%',
            'border': 1
        })
        worksheet_pct.set_column(1, len(df_matrix_pct.columns), 18, pct_format)
        
        df_joined = results.get("df_joined")
        source_cols = results.get("source_cols")
        region_probs = results.get("region_probs")
        if df_joined is not None and len(df_joined) > 0 and source_cols is not None and region_probs is not None:
            import re
            
            def clean_lbl(lbl):
                return lbl.replace(" (Quelle)", "").replace(" (Ziel)", "")
            
            # Group by kanton and write sheets
            unique_kantons = sorted([k for k in df_joined["kanton"].unique().to_list() if k])
            
            header_format_kanton = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'top',
                'fg_color': '#DCE6F1',
                'border': 1
            })
            
            seen_sheets = {
                "wählerwanderung (absolut)", 
                "wählerwanderung (prozent)", 
                "kantonale übersicht (absolut)", 
                "kantonale übersicht (prozent)"
            }
            
            # Create Canton level summary tables
            canton_abs_data = []
            canton_prob_data = []
            
            for kanton in unique_kantons:
                df_kanton = df_joined.filter(pl.col("kanton") == kanton)
                if len(df_kanton) == 0:
                    continue
                
                row_region = df_kanton["region"][0]
                reg_matrix = region_probs.get(row_region)
                if reg_matrix is None:
                    continue
                
                canton_matrix_abs = np.zeros((expected_rows, expected_cols))
                for row in df_kanton.iter_rows(named=True):
                    row_totals = np.zeros(expected_rows)
                    for r_idx, col in enumerate(source_cols):
                        row_totals[r_idx] = float(row[col])
                    
                    target_total = float(row["target_ja"]) + float(row["target_nein"]) + float(row["target_enthaltung"])
                    source_total = sum(row_totals[:-1])
                    row_totals[-1] = max(0.0, target_total - source_total)
                    
                    for r_idx in range(expected_rows):
                        canton_matrix_abs[r_idx, :] += row_totals[r_idx] * reg_matrix[r_idx, :]
                
                abs_row = {"Kanton": kanton}
                prob_row = {"Kanton": kanton}
                
                for r_idx, src_lbl in enumerate(results["source_labels"]):
                    for c_idx, tgt_lbl in enumerate(results["target_labels"]):
                        col_key_abs = f"{clean_lbl(src_lbl)} ➔ {clean_lbl(tgt_lbl)}"
                        col_key_prob = f"{clean_lbl(src_lbl)} ➔ {clean_lbl(tgt_lbl)} (%)"
                        
                        abs_row[col_key_abs] = canton_matrix_abs[r_idx, c_idx]
                        prob_row[col_key_prob] = reg_matrix[r_idx, c_idx]
                        
                canton_abs_data.append(abs_row)
                canton_prob_data.append(prob_row)
            
            if canton_abs_data:
                df_canton_abs = pd.DataFrame(canton_abs_data)
                df_canton_prob = pd.DataFrame(canton_prob_data)
                
                # Write Kanton absolute sheet
                df_canton_abs.to_excel(writer, sheet_name="Kantonale Übersicht (Absolut)", index=False)
                worksheet_c_abs = writer.sheets["Kantonale Übersicht (Absolut)"]
                for col_num, col_name in enumerate(df_canton_abs.columns):
                    worksheet_c_abs.write(0, col_num, col_name, header_format_kanton)
                worksheet_c_abs.set_column(0, 0, 15)
                worksheet_c_abs.set_column(1, len(df_canton_abs.columns) - 1, 18)
                
                # Write Kanton probability sheet
                df_canton_prob.to_excel(writer, sheet_name="Kantonale Übersicht (Prozent)", index=False)
                worksheet_c_prob = writer.sheets["Kantonale Übersicht (Prozent)"]
                for col_num, col_name in enumerate(df_canton_prob.columns):
                    worksheet_c_prob.write(0, col_num, col_name, header_format_kanton)
                worksheet_c_prob.set_column(0, 0, 15)
                worksheet_c_prob.set_column(1, len(df_canton_prob.columns) - 1, 18, pct_format)
            
            # Write individual kanton sheets (Gemeinde-level base columns only, no transition probabilities)
            for kanton in unique_kantons:
                df_kanton = df_joined.filter(pl.col("kanton") == kanton)
                if len(df_kanton) == 0:
                    continue
                
                # Filter and rename columns based on source_type
                if source_type == 'vote':
                    df_kanton_sel = df_kanton.select([
                        pl.col("geo_id").alias("BFS-Nummer"),
                        pl.col("name").alias("Gemeinde"),
                        pl.col("target_stimmberechtigte").alias("Stimmberechtigte"),
                        pl.col("ja").alias("Ja (Quelle)"),
                        pl.col("nein").alias("Nein (Quelle)"),
                        pl.col("enthaltung").alias("Enthaltung (Quelle)"),
                        pl.col("target_ja").alias("Ja (Ziel)"),
                        pl.col("target_nein").alias("Nein (Ziel)"),
                        pl.col("target_enthaltung").alias("Enthaltung (Ziel)")
                    ]).sort("BFS-Nummer")
                else:
                    # 'election'
                    select_exprs = [
                        pl.col("geo_id").alias("BFS-Nummer"),
                        pl.col("name").alias("Gemeinde"),
                        pl.col("target_stimmberechtigte").alias("Stimmberechtigte"),
                    ]
                    for col in source_cols:
                        if col == "Nichtwähler":
                            select_exprs.append(pl.col(col).alias("Nichtwähler (Quelle)"))
                        else:
                            select_exprs.append(pl.col(col).alias(f"{col} (Quelle)"))
                    select_exprs.extend([
                        pl.col("target_ja").alias("Ja (Ziel)"),
                        pl.col("target_nein").alias("Nein (Ziel)"),
                        pl.col("target_enthaltung").alias("Enthaltung (Ziel)")
                    ])
                    df_kanton_sel = df_kanton.select(select_exprs).sort("BFS-Nummer")
                
                df_kanton_pd = df_kanton_sel.to_pandas()
                
                # Sanitize sheet name
                kanton_clean = re.sub(r'[\\/?*:\[\]]', '_', kanton)[:31]
                sheet_name = kanton_clean
                suffix = 1
                while sheet_name.lower() in seen_sheets:
                    suffix_str = f"_{suffix}"
                    sheet_name = kanton_clean[:31 - len(suffix_str)] + suffix_str
                    suffix += 1
                seen_sheets.add(sheet_name.lower())
                
                df_kanton_pd.to_excel(writer, sheet_name=sheet_name, index=False)
                worksheet_kanton = writer.sheets[sheet_name]
                
                for col_num, col_name in enumerate(df_kanton_pd.columns):
                    worksheet_kanton.write(0, col_num, col_name, header_format_kanton)
                
                worksheet_kanton.set_column(0, 0, 12)
                worksheet_kanton.set_column(1, 1, 25)
                worksheet_kanton.set_column(2, len(df_kanton_pd.columns) - 1, 15)
        
    output.seek(0)
    return output.getvalue()
