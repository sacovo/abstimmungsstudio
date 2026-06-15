import csv
import io
import itertools
import time
import requests
import pandas as pd
import numpy as np

from django.core.management.base import BaseCommand
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn, TimeElapsedColumn
from rich.panel import Panel

from abst.store import store_commune_stats

console = Console()
base_url = "https://www.pxweb.bfs.admin.ch/api/v1/de"

def get_bfs_mapping(progress, task):
    url = "https://www.agvchapp.bfs.admin.ch/api/communes/correspondances"
    params = {
        "startPeriod": "01-01-2013",
        "endPeriod": "15-06-2026"
    }
    progress.update(
        task, description="[cyan]Fetching municipal fusions from AGVCH...")
    res = requests.get(url, params=params)
    if res.status_code != 200:
        console.print(
            f"[red]Error fetching AGVCH correspondences: {res.status_code}[/red]")
        return {}

    mapping = {}
    f = io.StringIO(res.text)
    reader = csv.DictReader(f)
    for row in reader:
        init_code = row.get("InitialCode")
        term_code = row.get("TerminalCode")
        if init_code and term_code:
            mapping[init_code.zfill(4)] = term_code.zfill(4)

    progress.update(
        task, description=f"[green]Loaded {len(mapping)} mappings from AGVCH correspondences.")
    return mapping


def parse_json_stat(res_json):
    dim_ids = res_json["id"]
    sizes = res_json["size"]
    dimensions = res_json["dimension"]
    values = res_json["value"]

    dim_categories = {}
    for d_id in dim_ids:
        cat = dimensions[d_id]["category"]
        if isinstance(cat["index"], list):
            dim_categories[d_id] = cat["index"]
        else:
            sorted_cats = sorted(cat["index"].items(), key=lambda x: x[1])
            dim_categories[d_id] = [x[0] for x in sorted_cats]

    records = []
    for indices in itertools.product(*[range(s) for s in sizes]):
        flat_idx = 0
        multiplier = 1
        for idx, size in zip(reversed(indices), reversed(sizes)):
            flat_idx += idx * multiplier
            multiplier *= size

        val = values[flat_idx]
        if val is not None:
            record = {}
            for d_idx, d_id in enumerate(dim_ids):
                code = dim_categories[d_id][indices[d_idx]]
                record[d_id] = code
            record["value"] = val
            records.append(record)

    return pd.DataFrame(records)


def fetch_table(table_id, payload):
    url = f"{base_url}/{table_id}/{table_id}.px"
    try:
        # Sleep 0.4 seconds to respect API rate limits (50 calls / 15 seconds)
        time.sleep(0.4)
        res = requests.post(url, json=payload, timeout=60)
        if res.status_code == 200:
            return parse_json_stat(res.json())
        else:
            console.print(
                f"[red]Error {res.status_code} querying {table_id}: {res.text[:300]}[/red]")
    except Exception as e:
        console.print(f"[red]Exception querying {table_id}: {e}[/red]")
    return pd.DataFrame()


def get_baseline_communes(bfs_mapping, progress, task):
    pop_payload = {
        "query": [
            {"code": "Jahr", "selection": {
                "filter": "item", "values": ["2024"]}},
            {"code": "Kanton (-) / Bezirk (>>) / Gemeinde (......)",
             "selection": {"filter": "all", "values": ["*"]}},
            {"code": "Bevölkerungstyp", "selection": {
                "filter": "item", "values": ["1"]}},
            {"code": "Staatsangehörigkeit (Kategorie)", "selection": {
                "filter": "item", "values": ["-99999", "2"]}},
            {"code": "Geschlecht", "selection": {
                "filter": "item", "values": ["-99999"]}},
            {"code": "Alter", "selection": {
                "filter": "item", "values": ["-99999"]}}
        ],
        "response": {"format": "json-stat2"}
    }

    progress.update(
        task, description="[cyan]Fetching baseline population from STATPOP...")
    df_pop = fetch_table("px-x-0102010000_101", pop_payload)
    if df_pop.empty:
        console.print(
            "[bold red]Failed to load baseline population data.[/bold red]")
        return {}

    communes = {}
    url_meta = f"{base_url}/px-x-0102010000_101/px-x-0102010000_101.px"
    meta = requests.get(url_meta).json()

    labels = {}
    for var in meta.get("variables", []):
        if var.get("code") == "Kanton (-) / Bezirk (>>) / Gemeinde (......)":
            labels = dict(zip(var["values"], var["valueTexts"]))
            break

    reg_col = "Kanton (-) / Bezirk (>>) / Gemeinde (......)"
    for _, row in df_pop.iterrows():
        r_code = row[reg_col]
        label = labels.get(r_code, r_code)
        if label.startswith("......"):
            clean_name = label.replace("......", "")
            if clean_name.startswith(r_code):
                clean_name = clean_name[len(r_code):].strip()

            mapped_code = bfs_mapping.get(r_code.zfill(4), r_code.zfill(4))

            if mapped_code not in communes:
                communes[mapped_code] = {
                    "bfs_code": mapped_code,
                    "commune_name": clean_name
                }

    progress.update(
        task, description=f"[green]Created baseline with {len(communes)} modern political communes.")
    return communes


class Command(BaseCommand):
    help = "Imports Swiss commune statistics from BFS APIs into InfluxDB"

    def handle(self, *args, **options):
        console.print(Panel.fit(
            "[bold blue]Swiss Commune Statistics Django Ingestor[/bold blue]\nConsolidated Self-Contained Ingestor", border_style="cyan"))

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console
        ) as progress:

            main_task = progress.add_task("[yellow]Initializing...", total=11)

            # 1. AGVCH correspondences
            bfs_mapping = get_bfs_mapping(progress, main_task)
            progress.advance(main_task)

            # 2. Baseline communes directory
            communes = get_baseline_communes(bfs_mapping, progress, main_task)
            if not communes:
                self.stderr.write(self.style.ERROR("Pipeline aborted. Could not establish commune baseline."))
                return
            progress.advance(main_task)

            modern_bfs_codes = set(communes.keys())
            reg_col = "Kanton (-) / Bezirk (>>) / Gemeinde (......)"

            # 3. STATPOP Basic Demographics (2014 & 2024)
            progress.update(
                main_task, description="[cyan]Fetching basic demographic trends (STATPOP)...")
            pop_payload = {
                "query": [
                    {"code": "Jahr", "selection": {
                        "filter": "item", "values": ["2014", "2024"]}},
                    {"code": reg_col, "selection": {
                        "filter": "all", "values": ["*"]}},
                    {"code": "Bevölkerungstyp", "selection": {
                        "filter": "item", "values": ["1"]}},
                    {"code": "Staatsangehörigkeit (Kategorie)", "selection": {
                        "filter": "item", "values": ["-99999", "2"]}},
                    {"code": "Geschlecht", "selection": {
                        "filter": "item", "values": ["-99999"]}},
                    {"code": "Alter", "selection": {
                        "filter": "item", "values": ["-99999"]}}
                ],
                "response": {"format": "json-stat2"}
            }
            df_pop = fetch_table("px-x-0102010000_101", pop_payload)

            if not df_pop.empty:
                df_pop["mapped_code"] = df_pop[reg_col].apply(
                    lambda x: bfs_mapping.get(str(x).zfill(4), str(x).zfill(4)))
                df_pop = df_pop[df_pop["mapped_code"].isin(modern_bfs_codes)]
                pop_grouped = df_pop.groupby(["mapped_code", "Jahr", "Staatsangehörigkeit (Kategorie)"])[
                    "value"].sum().to_dict()

                for code in modern_bfs_codes:
                    pop_2024 = pop_grouped.get((code, "2024", "-99999"), 0)
                    pop_2014 = pop_grouped.get((code, "2014", "-99999"), 0)
                    foreign_2024 = pop_grouped.get((code, "2024", "2"), 0)

                    communes[code]["pop_total_2024"] = int(pop_2024)
                    communes[code]["pop_foreign_2024"] = int(foreign_2024)
                    communes[code]["pop_foreign_ratio_pct_2024"] = round(
                        foreign_2024 / pop_2024 * 100, 2) if pop_2024 > 0 else 0.0

                    pop_chg_abs = pop_2024 - pop_2014
                    pop_chg_pct = (pop_chg_abs / pop_2014 *
                                   100) if pop_2014 > 0 else 0.0
                    communes[code]["pop_change_abs_10yr"] = int(pop_chg_abs)
                    communes[code]["pop_change_pct_10yr"] = round(pop_chg_pct, 2)
            progress.advance(main_task)

            # 4. Detailed Age Years (0 to 100)
            progress.update(
                main_task, description="[cyan]Fetching detailed age years (STATPOP)...")
            age_chunks = [
                [str(x) for x in range(0, 20)],
                [str(x) for x in range(20, 40)],
                [str(x) for x in range(40, 60)],
                [str(x) for x in range(60, 80)],
                [str(x) for x in range(80, 101)]
            ]

            age_pop_2024 = {code: {} for code in modern_bfs_codes}
            for c_idx, chunk in enumerate(age_chunks):
                progress.update(
                    main_task, description=f"[cyan]Fetching detailed age years (STATPOP) - chunk {c_idx+1}/5...")
                age_payload = {
                    "query": [
                        {"code": "Jahr", "selection": {
                            "filter": "item", "values": ["2024"]}},
                        {"code": reg_col, "selection": {
                            "filter": "all", "values": ["*"]}},
                        {"code": "Bevölkerungstyp", "selection": {
                            "filter": "item", "values": ["1"]}},
                        {"code": "Staatsangehörigkeit (Kategorie)", "selection": {
                            "filter": "item", "values": ["-99999"]}},
                        {"code": "Geschlecht", "selection": {
                            "filter": "item", "values": ["-99999"]}},
                        {"code": "Alter", "selection": {
                            "filter": "item", "values": chunk}}
                    ],
                    "response": {"format": "json-stat2"}
                }
                df_age = fetch_table("px-x-0102010000_101", age_payload)
                if not df_age.empty:
                    df_age["mapped_code"] = df_age[reg_col].apply(
                        lambda x: bfs_mapping.get(str(x).zfill(4), str(x).zfill(4)))
                    df_age = df_age[df_age["mapped_code"].isin(modern_bfs_codes)]
                    age_grouped = df_age.groupby(["mapped_code", "Alter"])[
                        "value"].sum().to_dict()
                    for (code, age), val in age_grouped.items():
                        age_pop_2024[code][f"pop_age_{age}_2024"] = int(val)

            for code in modern_bfs_codes:
                for age in range(0, 101):
                    col_name = f"pop_age_{age}_2024"
                    communes[code][col_name] = age_pop_2024[code].get(col_name, 0)

                # Calculate standard age buckets
                pop_total = communes[code].get("pop_total_2024", 0)

                # 0-17
                age_0_17 = sum(communes[code].get(f"pop_age_{age}_2024", 0) for age in range(0, 18))
                communes[code]["age_bucket_0_17_2024"] = age_0_17
                communes[code]["age_bucket_0_17_ratio_pct_2024"] = round(
                    age_0_17 / pop_total * 100, 2) if pop_total > 0 else 0.0

                # 18-29
                age_18_29 = sum(communes[code].get(f"pop_age_{age}_2024", 0) for age in range(18, 30))
                communes[code]["age_bucket_18_29_2024"] = age_18_29
                communes[code]["age_bucket_18_29_ratio_pct_2024"] = round(
                    age_18_29 / pop_total * 100, 2) if pop_total > 0 else 0.0

                # 30-49
                age_30_49 = sum(communes[code].get(f"pop_age_{age}_2024", 0) for age in range(30, 50))
                communes[code]["age_bucket_30_49_2024"] = age_30_49
                communes[code]["age_bucket_30_49_ratio_pct_2024"] = round(
                    age_30_49 / pop_total * 100, 2) if pop_total > 0 else 0.0

                # 50-64
                age_50_64 = sum(communes[code].get(f"pop_age_{age}_2024", 0) for age in range(50, 65))
                communes[code]["age_bucket_50_64_2024"] = age_50_64
                communes[code]["age_bucket_50_64_ratio_pct_2024"] = round(
                    age_50_64 / pop_total * 100, 2) if pop_total > 0 else 0.0

                # 65+ (65-100)
                age_65_plus = sum(communes[code].get(f"pop_age_{age}_2024", 0) for age in range(65, 101))
                communes[code]["age_bucket_65_plus_2024"] = age_65_plus
                communes[code]["age_bucket_65_plus_ratio_pct_2024"] = round(
                    age_65_plus / pop_total * 100, 2) if pop_total > 0 else 0.0

            progress.advance(main_task)

            # 5. Marital Status (STATPOP 2024)
            progress.update(
                main_task, description="[cyan]Fetching marital status (STATPOP)...")
            status_payload = {
                "query": [
                    {"code": "Jahr", "selection": {
                        "filter": "item", "values": ["2024"]}},
                    {"code": reg_col, "selection": {
                        "filter": "all", "values": ["*"]}},
                    {"code": "Bevölkerungstyp", "selection": {
                        "filter": "item", "values": ["1"]}},
                    {"code": "Geschlecht", "selection": {
                        "filter": "item", "values": ["-99999"]}},
                    {"code": "Zivilstand", "selection": {
                        "filter": "item", "values": ["1", "2", "3", "4"]}},
                    {"code": "Altersklasse", "selection": {
                        "filter": "item", "values": ["-99999"]}}
                ],
                "response": {"format": "json-stat2"}
            }
            df_status = fetch_table("px-x-0102010000_103", status_payload)
            if not df_status.empty:
                df_status["mapped_code"] = df_status[reg_col].apply(
                    lambda x: bfs_mapping.get(str(x).zfill(4), str(x).zfill(4)))
                df_status = df_status[df_status["mapped_code"].isin(
                    modern_bfs_codes)]
                status_grouped = df_status.groupby(["mapped_code", "Zivilstand"])[
                    "value"].sum().to_dict()

                status_map = {'1': 'single', '2': 'married',
                              '3': 'widowed', '4': 'divorced'}
                for code in modern_bfs_codes:
                    pop_total = communes[code].get("pop_total_2024", 0)
                    for s_code, s_name in status_map.items():
                        val = status_grouped.get((code, s_code), 0)
                        communes[code][f"pop_marital_{s_name}_2024"] = int(val)
                        communes[code][f"pop_marital_{s_name}_ratio_pct_2024"] = round(
                            val / pop_total * 100, 2) if pop_total > 0 else 0.0
            progress.advance(main_task)

            # 6. Births, Deaths & Migration Components
            progress.update(
                main_task, description="[cyan]Fetching births, deaths, and migration components...")
            comp_payload = {
                "query": [
                    {"code": "Jahr", "selection": {
                        "filter": "item", "values": ["2024"]}},
                    {"code": reg_col, "selection": {
                        "filter": "all", "values": ["*"]}},
                    {"code": "Staatsangehörigkeit (Kategorie)", "selection": {
                        "filter": "item", "values": ["0"]}},
                    {"code": "Geschlecht", "selection": {
                        "filter": "item", "values": ["0"]}},
                    {"code": "Demografische Komponente", "selection": {"filter": "item", "values": [
                        "1", "2", "4", "5", "6", "7", "8", "9"
                    ]}}
                ],
                "response": {"format": "json-stat2"}
            }
            df_comp = fetch_table("px-x-0102020000_201", comp_payload)
            if not df_comp.empty:
                df_comp["mapped_code"] = df_comp[reg_col].apply(
                    lambda x: bfs_mapping.get(str(x).zfill(4), str(x).zfill(4)))
                df_comp = df_comp[df_comp["mapped_code"].isin(modern_bfs_codes)]
                comp_grouped = df_comp.groupby(["mapped_code", "Demografische Komponente"])[
                    "value"].sum().to_dict()

                for code in modern_bfs_codes:
                    births = comp_grouped.get((code, "1"), 0)
                    deaths = comp_grouped.get((code, "2"), 0)
                    in_mig_intl = comp_grouped.get((code, "4"), 0)
                    out_mig_intl = comp_grouped.get((code, "7"), 0)
                    in_mig_int = comp_grouped.get(
                        (code, "5"), 0) + comp_grouped.get((code, "6"), 0)
                    out_mig_int = comp_grouped.get(
                        (code, "8"), 0) + comp_grouped.get((code, "9"), 0)

                    communes[code]["pop_comp_births_2024"] = int(births)
                    communes[code]["pop_comp_deaths_2024"] = int(deaths)
                    communes[code]["pop_comp_in_migration_international_2024"] = int(
                        in_mig_intl)
                    communes[code]["pop_comp_out_migration_international_2024"] = int(
                        out_mig_intl)
                    communes[code]["pop_comp_in_migration_internal_2024"] = int(
                        in_mig_int)
                    communes[code]["pop_comp_out_migration_internal_2024"] = int(
                        out_mig_int)

                    # Normalize by population
                    pop_total = communes[code].get("pop_total_2024", 0)
                    communes[code]["pop_comp_births_rate_per_1000_2024"] = round(
                        births / pop_total * 1000, 2) if pop_total > 0 else 0.0
                    communes[code]["pop_comp_deaths_rate_per_1000_2024"] = round(
                        deaths / pop_total * 1000, 2) if pop_total > 0 else 0.0
                    
                    net_migration = (in_mig_intl + in_mig_int) - (out_mig_intl + out_mig_int)
                    communes[code]["pop_comp_net_migration_ratio_pct_2024"] = round(
                        net_migration / pop_total * 100, 2) if pop_total > 0 else 0.0
            progress.advance(main_task)

            # 7. Land Use (Arealstatistik SDMX GET API)
            progress.update(
                main_task, description="[cyan]Fetching land use data (Arealstatistik SDMX)...")
            areal_url = "https://disseminate.stats.swiss/rest/data/CH1.AREA,DF_AREA_NOAS,1.0.0/1+2+3+4..?format=csvfile"
            res_areal = requests.get(areal_url)
            if res_areal.status_code == 200:
                f = io.StringIO(res_areal.text)
                reader = csv.DictReader(f)

                areal_temp = {}
                for row in reader:
                    if row.get("REGION_REF") == "POLG":
                        region = row.get("REGION").zfill(4)
                        category = row.get("NOAS")
                        period = row.get("PERIOD")
                        val_str = row.get("OBS_VALUE")
                        fj = row.get("FJ")

                        if not region or not category or not period or not val_str:
                            continue

                        mapped_code = bfs_mapping.get(region, region)
                        if mapped_code not in modern_bfs_codes:
                            continue

                        try:
                            val = float(val_str)
                        except ValueError:
                            continue

                        if mapped_code not in areal_temp:
                            areal_temp[mapped_code] = {}
                        if period not in areal_temp[mapped_code]:
                            areal_temp[mapped_code][period] = {
                                "fj": fj, "total_ha": 0.0}

                        areal_temp[mapped_code][period][category] = areal_temp[mapped_code][period].get(
                            category, 0.0) + val
                        areal_temp[mapped_code][period]["total_ha"] += val

                category_map = {'1': 'settlement', '2': 'agriculture',
                                '3': 'forest', '4': 'unproductive'}
                for code in modern_bfs_codes:
                    if code in areal_temp:
                        periods_data = areal_temp[code]
                        sorted_periods = sorted(periods_data.keys(), reverse=True)

                        found = False
                        for idx, p in enumerate(sorted_periods):
                            pdata = periods_data[p]
                            has_vals = any(cat in pdata for cat in [
                                           '1', '2', '3', '4'])
                            if has_vals:
                                total_area = 0.0
                                for cat_code, cat_name in category_map.items():
                                    val = pdata.get(cat_code, 0.0)
                                    communes[code][f"area_{cat_name}_ha"] = round(
                                        val, 2)
                                    total_area += val

                                communes[code]["area_total_ha"] = round(
                                    total_area, 2)
                                for cat_name in category_map.values():
                                    val = communes[code].get(f"area_{cat_name}_ha")
                                    communes[code][f"area_{cat_name}_ratio_pct"] = round(
                                        val / total_area * 100, 2) if total_area > 0 and val is not None else 0.0
                                communes[code]["area_survey_period"] = p
                                communes[code]["area_survey_year"] = pdata.get(
                                    "fj")

                                delta_period = None
                                if idx + 1 < len(sorted_periods):
                                    prev_p = sorted_periods[idx + 1]
                                    prev_pdata = periods_data[prev_p]
                                    delta_period = f"{prev_p} to {p}"

                                    for cat_code, cat_name in category_map.items():
                                        curr_val = pdata.get(cat_code, 0.0)
                                        prev_val = prev_pdata.get(cat_code, 0.0)
                                        communes[code][f"area_{cat_name}_change_ha"] = round(
                                            curr_val - prev_val, 2)
                                else:
                                    for cat_name in category_map.values():
                                        communes[code][f"area_{cat_name}_change_ha"] = None

                                communes[code]["area_change_period"] = delta_period
                                found = True
                                break

                        if not found:
                            for cat_name in category_map.values():
                                communes[code][f"area_{cat_name}_ha"] = None
                                communes[code][f"area_{cat_name}_change_ha"] = None
                            communes[code]["area_total_ha"] = None
                            communes[code]["area_survey_period"] = None
                            communes[code]["area_survey_year"] = None
                            communes[code]["area_change_period"] = None
                    else:
                        for cat_name in category_map.values():
                            communes[code][f"area_{cat_name}_ha"] = None
                            communes[code][f"area_{cat_name}_change_ha"] = None
                        communes[code]["area_total_ha"] = None
                        communes[code]["area_survey_period"] = None
                        communes[code]["area_survey_year"] = None
                        communes[code]["area_change_period"] = None
            progress.advance(main_task)

            # 8. Businesses & Employees (STATENT 2013 & 2023)
            progress.update(
                main_task, description="[cyan]Fetching businesses and employees (STATENT)...")
            ent_payload = {
                "query": [
                    {"code": "Jahr", "selection": {
                        "filter": "item", "values": ["2013", "2023"]}},
                    {"code": "Gemeinde", "selection": {
                        "filter": "all", "values": ["*"]}},
                    {"code": "Wirtschaftssektor", "selection": {
                        "filter": "item", "values": ["999", "1", "2", "3"]}},
                    {"code": "Beobachtungseinheit", "selection": {
                        "filter": "item", "values": ["1", "2"]}}
                ],
                "response": {"format": "json-stat2"}
            }
            df_ent = fetch_table("px-x-0602010000_102", ent_payload)
            if not df_ent.empty:
                df_ent["mapped_code"] = df_ent["Gemeinde"].apply(
                    lambda x: bfs_mapping.get(str(x).zfill(4), str(x).zfill(4)))
                df_ent = df_ent[df_ent["mapped_code"].isin(modern_bfs_codes)]
                ent_grouped = df_ent.groupby(["mapped_code", "Jahr", "Wirtschaftssektor", "Beobachtungseinheit"])[
                    "value"].sum().to_dict()

                sector_map = {'999': 'total', '1': 'sector1',
                              '2': 'sector2', '3': 'sector3'}
                unit_map = {'1': 'businesses', '2': 'employees'}

                for code in modern_bfs_codes:
                    for s_code, s_name in sector_map.items():
                        for u_code, u_name in unit_map.items():
                            val = ent_grouped.get(
                                (code, "2023", s_code, u_code), 0)
                            communes[code][f"{u_name}_{s_name}_2023"] = float(val)

                    bus_total = communes[code].get("businesses_total_2023", 0.0)
                    for s_name in ['sector1', 'sector2', 'sector3']:
                        val = communes[code].get(f"businesses_{s_name}_2023", 0.0)
                        communes[code][f"businesses_{s_name}_ratio_pct_2023"] = round(
                            val / bus_total * 100, 2) if bus_total > 0 else 0.0

                    emp_total = communes[code].get("employees_total_2023", 0.0)
                    for s_name in ['sector1', 'sector2', 'sector3']:
                        val = communes[code].get(f"employees_{s_name}_2023", 0.0)
                        communes[code][f"employees_{s_name}_ratio_pct_2023"] = round(
                            val / emp_total * 100, 2) if emp_total > 0 else 0.0

                    pop_total = communes[code].get("pop_total_2024", 0)
                    communes[code]["businesses_density_per_1000_2023"] = round(
                        bus_total / pop_total * 1000, 2) if pop_total > 0 else 0.0
                    communes[code]["employees_density_per_1000_2023"] = round(
                        emp_total / pop_total * 1000, 2) if pop_total > 0 else 0.0

                    bus_2023 = ent_grouped.get((code, "2023", "999", "1"), 0)
                    bus_2013 = ent_grouped.get((code, "2013", "999", "1"), 0)
                    bus_chg_abs = bus_2023 - bus_2013
                    bus_chg_pct = (bus_chg_abs / bus_2013 *
                                   100) if bus_2013 > 0 else 0.0

                    emp_2023 = ent_grouped.get((code, "2023", "999", "2"), 0)
                    emp_2013 = ent_grouped.get((code, "2013", "999", "2"), 0)
                    emp_chg_abs = emp_2023 - emp_2013
                    emp_chg_pct = (emp_chg_abs / emp_2013 *
                                   100) if emp_2013 > 0 else 0.0

                    communes[code]["businesses_change_abs_10yr"] = float(
                        bus_chg_abs)
                    communes[code]["businesses_change_pct_10yr"] = round(
                        bus_chg_pct, 2)
                    communes[code]["employees_change_abs_10yr"] = float(
                        emp_chg_abs)
                    communes[code]["employees_change_pct_10yr"] = round(
                        emp_chg_pct, 2)
            progress.advance(main_task)

            # 9. Vehicles (2014 & 2024 Passenger Cars)
            progress.update(
                main_task, description="[cyan]Fetching registered passenger cars...")
            veh_payload = {
                "query": [
                    {"code": "Jahr", "selection": {
                        "filter": "item", "values": ["2014", "2024"]}},
                    {"code": "Gemeinde", "selection": {
                        "filter": "all", "values": ["*"]}},
                    {"code": "Fahrzeuggruppe", "selection": {
                        "filter": "item", "values": ["1"]}},
                    {"code": "Treibstoff", "selection": {
                        "filter": "all", "values": ["*"]}}
                ],
                "response": {"format": "json-stat2"}
            }
            df_veh = fetch_table("px-x-1103020100_111", veh_payload)
            if not df_veh.empty:
                df_veh["mapped_code"] = df_veh["Gemeinde"].apply(
                    lambda x: bfs_mapping.get(str(x).zfill(4), str(x).zfill(4)))
                df_veh = df_veh[df_veh["mapped_code"].isin(modern_bfs_codes)]
                veh_grouped = df_veh.groupby(["mapped_code", "Jahr"])[
                    "value"].sum().to_dict()

                for code in modern_bfs_codes:
                    cars_2024 = veh_grouped.get((code, "2024"), 0)
                    cars_2014 = veh_grouped.get((code, "2014"), 0)

                    cars_chg_abs = cars_2024 - cars_2014
                    cars_chg_pct = (cars_chg_abs / cars_2014 *
                                    100) if cars_2014 > 0 else 0.0

                    communes[code]["vehicles_passenger_cars_2024"] = int(cars_2024)
                    communes[code]["vehicles_passenger_cars_change_abs_10yr"] = int(
                        cars_chg_abs)
                    communes[code]["vehicles_passenger_cars_change_pct_10yr"] = round(
                        cars_chg_pct, 2)
                    
                    pop_total = communes[code].get("pop_total_2024", 0)
                    communes[code]["vehicles_passenger_cars_ratio_per_1000"] = round(
                        cars_2024 / pop_total * 1000, 2) if pop_total > 0 else 0.0
            progress.advance(main_task)

            # 10. Tourism HESTA (2024)
            progress.update(
                main_task, description="[cyan]Fetching hotel nights and arrivals (HESTA)...")
            tour_payload = {
                "query": [
                    {"code": "Jahr", "selection": {
                        "filter": "item", "values": ["2024"]}},
                    {"code": "Monat", "selection": {
                        "filter": "item", "values": ["YYYY"]}},
                    {"code": "Gemeinde", "selection": {
                        "filter": "all", "values": ["*"]}},
                    {"code": "Herkunftsland", "selection": {
                        "filter": "item", "values": ["0"]}},
                    {"code": "Indikator", "selection": {
                        "filter": "all", "values": ["*"]}}
                ],
                "response": {"format": "json-stat2"}
            }
            df_tour = fetch_table("px-x-1003020000_101", tour_payload)
            if not df_tour.empty:
                df_tour["mapped_code"] = df_tour["Gemeinde"].apply(
                    lambda x: bfs_mapping.get(str(x).zfill(4), str(x).zfill(4)))
                df_tour = df_tour[df_tour["mapped_code"].isin(modern_bfs_codes)]
                tour_grouped = df_tour.groupby(["mapped_code", "Indikator"])[
                    "value"].sum().to_dict()

                for code in modern_bfs_codes:
                    arrivals = tour_grouped.get((code, "1"), 0)
                    nights = tour_grouped.get((code, "2"), 0)
                    communes[code]["tourism_arrivals_2024"] = float(arrivals)
                    communes[code]["tourism_nights_2024"] = float(nights)

                    # Normalize by population
                    pop_total = communes[code].get("pop_total_2024", 0)
                    communes[code]["tourism_arrivals_ratio_per_resident_2024"] = round(
                        arrivals / pop_total, 3) if pop_total > 0 else 0.0
                    communes[code]["tourism_nights_ratio_per_resident_2024"] = round(
                        nights / pop_total, 3) if pop_total > 0 else 0.0
            else:
                for code in modern_bfs_codes:
                    communes[code]["tourism_arrivals_2024"] = 0.0
                    communes[code]["tourism_nights_2024"] = 0.0
            # Calculate derived metrics (Bevölkerungsdichte: pop_total_2024 / area_settlement_ha)
            for code, data in communes.items():
                pop_total = data.get("pop_total_2024", 0)
                area_settlement = data.get("area_settlement_ha")
                if area_settlement and area_settlement > 0:
                    data["pop_density_per_settlement_ha"] = round(pop_total / area_settlement, 3)
                else:
                    data["pop_density_per_settlement_ha"] = 0.0

            progress.advance(main_task)

            # 11. Write to InfluxDB
            progress.update(
                main_task, description="[cyan]Writing commune statistics to InfluxDB...")
            commune_list = list(communes.values())
            stored_count = store_commune_stats(commune_list)
            
            progress.update(
                main_task, description=f"[green]Successfully stored stats for {stored_count} communes.")
            progress.advance(main_task)

        self.stdout.write(self.style.SUCCESS(
            f"Import completed: stats for {stored_count} communes loaded and written to InfluxDB."
        ))
