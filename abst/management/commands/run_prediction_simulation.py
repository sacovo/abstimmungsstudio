import json
import os
import random
import time
from collections import defaultdict
import numpy as np
from django.core.management.base import BaseCommand, CommandError

from abst.geo import get_geo_id_list
from abst.models import Gemeinde, Kanton, PredictionEvaluationReport, Vorlage, Zaehlkreis
from abst.predict import create_models, predict_results
from abst.store import get_abst_results, get_stimmberechtigte


class Command(BaseCommand):
    help = (
        "Runs an extensive prediction accuracy simulation using regional cantonal votes for cantons, "
        "national votes for CH, and stratified random subset iterations per step size."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--regions",
            type=str,
            default="CH ZH BE AG VD SG GR TI",
            help="Space or comma-separated list of regions (e.g. CH ZH BE).",
        )
        parser.add_argument(
            "--num-votes",
            type=int,
            default=3,
            help="Number of random finished votes to evaluate per region.",
        )
        parser.add_argument(
            "--iterations",
            type=int,
            default=5,
            help="Number of different random commune subset iterations per step size.",
        )
        parser.add_argument(
            "--years",
            type=str,
            help="Optional year or year range to filter votes (e.g., 2024 or 2024-2026).",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear previous reports before running.",
        )

    def handle(self, *args, **options):
        # 1. Parse options
        regions = []
        for part in options["regions"].replace(",", " ").split():
            regions.append(part.upper())

        num_votes = options["num_votes"]
        iterations = options["iterations"]

        # Parse years option
        start_year = None
        end_year = None
        if options["years"]:
            parts = options["years"].split("-")
            try:
                start_year = int(parts[0])
                if len(parts) > 1:
                    end_year = int(parts[1])
                else:
                    end_year = start_year
            except ValueError:
                raise CommandError(f"Invalid years format: {options['years']}. Use format YYYY or YYYY-YYYY.")

        if options["clear"]:
            self.stdout.write("Clearing existing prediction evaluation reports...")
            PredictionEvaluationReport.objects.all().delete()

        # 2. Fetch eligible voters metadata to sort municipalities by size
        df_stimmberechtigte = get_stimmberechtigte()
        stimm_dict = dict(
            zip(
                df_stimmberechtigte["geo_id"].to_list(),
                df_stimmberechtigte["anzahl_stimmberechtigte"].to_list(),
            )
        )

        # Pre-cache canton lookups
        cantons = {k.kanton_id: k for k in Kanton.objects.all()}
        cantons_by_short = {k.short: k for k in Kanton.objects.all()}

        # Set up random seed for reproducibility
        random.seed(42)
        np.random.seed(42)

        # 3. Run simulation for each region
        for region in regions:
            self.stdout.write(f"\nRunning simulation for region: {region}...")

            # Select finished votes for this specific region
            region_votes_qs = Vorlage.objects.filter(finished=True, region=region)
            if start_year and end_year:
                region_votes_qs = region_votes_qs.filter(
                    tag__date__year__range=(start_year, end_year)
                )

            if not region_votes_qs.exists():
                self.stdout.write(self.style.WARNING(f"No finished votes found for region {region}. Skipping."))
                continue

            available_count = region_votes_qs.count()
            actual_num_votes = min(num_votes, available_count)
            votes = list(region_votes_qs.order_by("?")[:actual_num_votes])
            vote_ids = [v.vorlagen_id for v in votes]

            self.stdout.write(f"Selected {actual_num_votes} votes for {region}: {vote_ids}")

            # Ensure projection models exist for all selected votes' tags
            for v in votes:
                tag = v.tag
                if not tag.projection or not tag.projection_bet:
                    self.stdout.write(f"Generating SVD projection models for tag: {tag.name}...")
                    create_models(tag)

            # Get target municipalities for step calculations (excluding parent municipalities of counting districts)
            # Find a representative stand from the first vote
            stand = votes[0].tag.stand

            # Load geo metadata mapping for names and cantons
            gemeinden = {
                g.geo_id: g for g in Gemeinde.objects.filter(stand=stand)
            }
            zaehlkreise = {
                z.geo_id: z for z in Zaehlkreis.objects.filter(gemeinde__stand=stand)
            }

            canton_mapping = {}
            for g_id, g in gemeinden.items():
                canton_mapping[g_id] = g.kanton_id
            for z_id, z in zaehlkreise.items():
                canton_mapping[z_id] = z.gemeinde.kanton_id

            def get_geo_info(geo_id):
                if geo_id in gemeinden:
                    g = gemeinden[geo_id]
                    return g.name, g.kanton, g.kanton_id
                elif geo_id in zaehlkreise:
                    z = zaehlkreise[geo_id]
                    k = cantons.get(z.gemeinde.kanton_id)
                    canton_name = k.name if k else z.gemeinde.kanton
                    return z.name, canton_name, z.gemeinde.kanton_id
                return f"ID {geo_id}", "Unknown", 0

            zk_parents = set(
                Gemeinde.objects.filter(stand=stand)
                .exclude(zaehlkreis=None)
                .values_list("geo_id", flat=True)
            )

            if region == "CH":
                region_geo_ids = get_geo_id_list(stand)
            else:
                kanton_obj = cantons_by_short.get(region)
                if not kanton_obj:
                    self.stdout.write(self.style.WARNING(f"Canton {region} not found. Skipping."))
                    continue
                region_geo_ids = get_geo_id_list(stand, kanton_id=kanton_obj.kanton_id)

            # Filter out parent municipalities to avoid double counting
            predictor_pool = [gid for gid in region_geo_ids if gid not in zk_parents]

            # Group the predictor pool by canton
            canton_groups = defaultdict(list)
            for gid in predictor_pool:
                c_id = canton_mapping.get(gid)
                if c_id:
                    canton_groups[c_id].append(gid)

            # Sort the communes in each canton by size ascending
            for c_id in canton_groups:
                canton_groups[c_id].sort(key=lambda gid: stimm_dict.get(gid, 0))

            total_munis = len(predictor_pool)
            self.stdout.write(f"Total municipalities (excluding parent districts) in {region}: {total_munis}")

            # Define step sizes
            if region == "CH":
                candidate_steps = [10, 20, 50, 100, 200, 500]
            else:
                candidate_steps = [5, 10, 20, 30, 50, 75, 100, 150, 200]

            # Keep steps less than 80% of total municipalities (so prediction is non-trivial)
            steps = [s for s in candidate_steps if s < 0.8 * total_munis]
            if len(steps) < 3:
                steps = [
                    max(1, int(total_munis * 0.1)),
                    max(2, int(total_munis * 0.25)),
                    max(3, int(total_munis * 0.5)),
                ]
            steps = sorted(list(set(steps)))
            self.stdout.write(f"Evaluating steps: {steps} with {iterations} iterations each")

            # Map to hold prediction errors per step size
            step_errors = {
                step: {"yes": [], "bet": []} for step in steps
            }

            # Pre-load all actual results for the sampled votes
            vote_actual_dicts = {}
            for v in votes:
                df_act = get_abst_results(v.vorlagen_id)
                if df_act is None:
                    continue

                act_dict = {}
                for row in df_act.iter_rows(named=True):
                    act_dict[row["geo_id"]] = {
                        "ja_stimmen": row["ja_stimmen"],
                        "nein_stimmen": row["nein_stimmen"],
                        "anzahl_stimmberechtigte": row["anzahl_stimmberechtigte"],
                        "ja_prozent": row["ja_prozent"],
                        "stimmbeteiligung": row["stimmbeteiligung"],
                    }
                vote_actual_dicts[v.vorlagen_id] = act_dict

            # Structure to hold debug diagnostics JSON
            debug_data = {
                "region": region,
                "votes": []
            }

            # Run predictions for each vote
            for idx, v in enumerate(votes):
                self.stdout.write(f"  [{idx+1}/{actual_num_votes}] Vote: {v.name[:40]}... (ID: {v.vorlagen_id})")
                t_vote_start = time.perf_counter()

                actual_dict = vote_actual_dicts.get(v.vorlagen_id)
                if not actual_dict:
                    self.stdout.write(self.style.WARNING(f"    Missing actual results in DB. Skipping."))
                    continue

                v_stand = v.tag.stand
                v_zk_parents = set(
                    Gemeinde.objects.filter(stand=v_stand)
                    .exclude(zaehlkreis=None)
                    .values_list("geo_id", flat=True)
                )

                # Determine active municipalities for this vote
                v_used_geo_ids = set(get_geo_id_list(v_stand))

                # Determine the target region municipalities we want to aggregate (excluding parent municipalities)
                if region == "CH":
                    target_geo_ids = v_used_geo_ids
                else:
                    kanton_obj = cantons_by_short.get(region)
                    target_geo_ids = set(get_geo_id_list(v_stand, kanton_id=kanton_obj.kanton_id))

                # Aggregate actual total for target region
                def get_aggregated_stats(results_map):
                    total_ja = 0
                    total_nein = 0
                    total_stimmberechtigte = 0
                    for gid in target_geo_ids:
                        if gid in v_zk_parents:
                            continue
                        val = results_map.get(gid)
                        if val:
                            total_ja += val["ja_stimmen"]
                            total_nein += val["nein_stimmen"]
                            total_stimmberechtigte += val["anzahl_stimmberechtigte"]

                    gueltige = total_ja + total_nein
                    ja_p = (total_ja / gueltige * 100) if gueltige > 0 else 0.0
                    bet_p = (gueltige / total_stimmberechtigte * 100) if total_stimmberechtigte > 0 else 0.0
                    return ja_p, bet_p

                act_yes, act_bet = get_aggregated_stats(actual_dict)

                vote_debug = {
                    "vote_id": v.vorlagen_id,
                    "vote_name": v.name,
                    "actual_yes": act_yes,
                    "actual_bet": act_bet,
                    "steps": []
                }

                # Determine regional predictor pool for this vote's Stand (grouped by canton)
                if region == "CH":
                    v_region_geo_ids = get_geo_id_list(v_stand)
                else:
                    kanton_obj = cantons_by_short.get(region)
                    v_region_geo_ids = get_geo_id_list(v_stand, kanton_id=kanton_obj.kanton_id)

                v_predictor_pool = [gid for gid in v_region_geo_ids if gid not in v_zk_parents]

                # Group by canton
                v_canton_groups = defaultdict(list)
                for gid in v_predictor_pool:
                    c_id = canton_mapping.get(gid)
                    if c_id:
                        v_canton_groups[c_id].append(gid)

                # Sort by size
                for c_id in v_canton_groups:
                    v_canton_groups[c_id].sort(key=lambda gid: stimm_dict.get(gid, 0))

                t_predict_total = 0.0
                t_aggregate_total = 0.0

                # For each step, run prediction
                for step in steps:
                    if step > len(v_predictor_pool):
                        continue

                    step_debug = {
                        "step_size": step,
                        "iterations": []
                    }

                    for iteration in range(iterations):
                        # Stratified sampling to ensure cantonal/linguistic/cultural diversity
                        known_subset = []
                        active_cantons = list(v_canton_groups.keys())
                        random.shuffle(active_cantons)

                        canton_needs = defaultdict(int)
                        for i in range(step):
                            c_id = active_cantons[i % len(active_cantons)]
                            canton_needs[c_id] += 1

                        for c_id, need in canton_needs.items():
                            pool = v_canton_groups[c_id]
                            if need >= len(pool):
                                known_subset.extend(pool)
                            else:
                                K = min(len(pool), max(need + 5, int(need * 2)))
                                known_subset.extend(random.sample(pool[:K], need))

                        # Predict (timed)
                        t_pred_start = time.perf_counter()
                        predicted_results = predict_results(v.vorlagen_id, known_geo_ids=known_subset)
                        t_predict_total += (time.perf_counter() - t_pred_start)

                        if predicted_results is None:
                            continue

                        # Merge and Aggregate (timed)
                        t_agg_start = time.perf_counter()
                        merged_dict = {}
                        for gid in known_subset:
                            if gid in actual_dict:
                                merged_dict[gid] = actual_dict[gid]

                        for p in predicted_results:
                            gid = p.geo_id
                            if p.result is not None:
                                merged_dict[gid] = {
                                    "ja_stimmen": p.result.ja_stimmen,
                                    "nein_stimmen": p.result.nein_stimmen,
                                    "anzahl_stimmberechtigte": p.result.anzahl_stimmberechtigte,
                                    "ja_prozent": p.result.ja_prozent,
                                    "stimmbeteiligung": p.result.stimmbeteiligung,
                                }

                        # Aggregate predicted total for target region
                        pred_yes, pred_bet = get_aggregated_stats(merged_dict)
                        t_aggregate_total += (time.perf_counter() - t_agg_start)

                        # Calculate error
                        err_yes = pred_yes - act_yes
                        err_bet = pred_bet - act_bet

                        step_errors[step]["yes"].append(err_yes)
                        step_errors[step]["bet"].append(err_bet)

                        # Store individual commune details for the first vote and step 10/20 in iteration 0
                        commune_details = []
                        if idx == 0 and iteration == 0 and step in [10, 20]:
                            for gid in target_geo_ids:
                                if gid in v_zk_parents:
                                    continue
                                act_val = actual_dict.get(gid, {})
                                pred_val = merged_dict.get(gid, {})
                                name, canton_name, _ = get_geo_info(gid)
                                commune_details.append({
                                    "geo_id": gid,
                                    "name": name,
                                    "canton": canton_name,
                                    "is_predictor": gid in known_subset,
                                    "actual_yes": act_val.get("ja_prozent"),
                                    "actual_bet": act_val.get("stimmbeteiligung"),
                                    "predicted_yes": pred_val.get("ja_prozent") if gid not in known_subset else act_val.get("ja_prozent"),
                                    "predicted_bet": pred_val.get("stimmbeteiligung") if gid not in known_subset else act_val.get("stimmbeteiligung"),
                                })

                        step_debug["iterations"].append({
                            "iteration": iteration,
                            "known_subset": known_subset,
                            "pred_yes": pred_yes,
                            "pred_bet": pred_bet,
                            "err_yes": err_yes,
                            "err_bet": err_bet,
                            "communes": commune_details
                        })

                    vote_debug["steps"].append(step_debug)
                
                t_vote_end = time.perf_counter()
                self.stdout.write(
                    f"    Vote ID {v.vorlagen_id} completed in {t_vote_end - t_vote_start:.2f}s "
                    f"(predict_results: {t_predict_total:.2f}s, aggregate/merge: {t_aggregate_total:.2f}s)"
                )
                debug_data["votes"].append(vote_debug)

            # 4. Aggregate stats per step size
            steps_data = []
            for step in steps:
                yes_errs = step_errors[step]["yes"]
                bet_errs = step_errors[step]["bet"]

                if not yes_errs:
                    continue

                # Compute statistics
                yes_mae = float(np.mean(np.abs(yes_errs)))
                yes_mean = float(np.mean(yes_errs))
                yes_std = float(np.std(yes_errs))
                yes_p10 = float(np.percentile(yes_errs, 10))
                yes_p25 = float(np.percentile(yes_errs, 25))
                yes_p50 = float(np.percentile(yes_errs, 50))  # median
                yes_p75 = float(np.percentile(yes_errs, 75))
                yes_p90 = float(np.percentile(yes_errs, 90))

                bet_mae = float(np.mean(np.abs(bet_errs)))
                bet_mean = float(np.mean(bet_errs))
                bet_std = float(np.std(bet_errs))
                bet_p10 = float(np.percentile(bet_errs, 10))
                bet_p25 = float(np.percentile(bet_errs, 25))
                bet_p50 = float(np.percentile(bet_errs, 50))  # median
                bet_p75 = float(np.percentile(bet_errs, 75))
                bet_p90 = float(np.percentile(bet_errs, 90))

                steps_data.append(
                    {
                        "step_size": step,
                        "yes_mae": yes_mae,
                        "yes_mean": yes_mean,
                        "yes_std": yes_std,
                        "yes_p10": yes_p10,
                        "yes_p25": yes_p25,
                        "yes_p50": yes_p50,
                        "yes_p75": yes_p75,
                        "yes_p90": yes_p90,
                        "bet_mae": bet_mae,
                        "bet_mean": bet_mean,
                        "bet_std": bet_std,
                        "bet_p10": bet_p10,
                        "bet_p25": bet_p25,
                        "bet_p50": bet_p50,
                        "bet_p75": bet_p75,
                        "bet_p90": bet_p90,
                    }
                )

            # 5. Store Report
            report = PredictionEvaluationReport.objects.create(
                region=region,
                num_votes=len(votes),
                vote_ids=vote_ids,
                steps_data=steps_data,
            )

            # Save debug JSON to scratch folder
            scratch_dir = "/home/vscode/.gemini/antigravity-cli/brain/3b48b453-5634-4712-aad0-0141f159ac05/scratch"
            os.makedirs(scratch_dir, exist_ok=True)
            debug_path = os.path.join(scratch_dir, f"debug_predictions_{region}.json")
            with open(debug_path, "w", encoding="utf-8") as f:
                json.dump(debug_data, f, indent=2, ensure_ascii=False)
            self.stdout.write(f"Saved detailed debug predictions to {debug_path}")

            # Print summary
            if steps_data:
                first_step = steps_data[0]
                last_step = steps_data[-1]
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Successfully generated report for {region} using {len(votes)} votes and {iterations} iterations:\n"
                        f"  First Step ({first_step['step_size']} munis): Yes% MAE = {first_step['yes_mae']:.4f}%, Turnout% MAE = {first_step['bet_mae']:.4f}%\n"
                        f"  Last Step ({last_step['step_size']} munis): Yes% MAE = {last_step['yes_mae']:.4f}%, Turnout% MAE = {last_step['bet_mae']:.4f}%"
                    )
                )

        self.stdout.write(self.style.SUCCESS("\nAll simulation reports generated and saved successfully!"))
