from collections import defaultdict
from django.core.management.base import BaseCommand, CommandError

from abst.geo import get_geo_id_list
from abst.models import Gemeinde, Kanton, Vorlage, Zaehlkreis
from abst.predict import predict_results
from abst.store import get_abst_results


class Command(BaseCommand):
    help = (
        "Evaluates the prediction accuracy for a given vote using only a specified subset of "
        "municipalities as predictors."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "vote_id",
            type=int,
            help="The ID of the vote (vorlagen_id) to evaluate.",
        )
        parser.add_argument(
            "gemeinde_ids",
            type=str,
            nargs="+",
            help="List of Gemeinde / Zaehlkreis geo_ids (BFS IDs) to use as the predictor subset (can be space-separated or comma-separated).",
        )

    def handle(self, *args, **options):
        vote_id = options["vote_id"]

        # Parse Gemeinde IDs (supporting comma-separated and space-separated lists)
        parsed_ids = []
        for arg in options["gemeinde_ids"]:
            for part in arg.replace(",", " ").split():
                try:
                    parsed_ids.append(int(part))
                except ValueError:
                    raise CommandError(f"Invalid Gemeinde ID format: '{part}'")

        gemeinde_ids = list(set(parsed_ids))
        if not gemeinde_ids:
            raise CommandError("Please provide at least one Gemeinde ID as a predictor.")

        # 1. Fetch Vorlage
        try:
            vorlage = Vorlage.objects.select_related("tag", "tag__stand").get(vorlagen_id=vote_id)
        except Vorlage.DoesNotExist:
            raise CommandError(f"Vorlage with ID {vote_id} does not exist.")

        self.stdout.write(f"Evaluating prediction for Vorlage: {vorlage.name} (ID: {vote_id})")
        self.stdout.write(f"Date: {vorlage.tag.date} | Region: {vorlage.region}")

        # 2. Fetch all actual results from database
        df_actual = get_abst_results(vote_id)
        if df_actual is None or len(df_actual) == 0:
            raise CommandError(f"No actual results found for vote ID {vote_id} in the database.")

        # 3. Load geo metadata mapping for names and cantons
        gemeinden = {
            g.geo_id: g for g in Gemeinde.objects.filter(stand=vorlage.tag.stand)
        }
        zaehlkreise = {
            z.geo_id: z for z in Zaehlkreis.objects.filter(gemeinde__stand=vorlage.tag.stand)
        }
        cantons = {k.kanton_id: k for k in Kanton.objects.all()}

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

        # Determine active municipalities for this vote
        if vorlage.kantonal:
            try:
                kanton = Kanton.objects.get(short=vorlage.region)
                used_geo_ids = set(get_geo_id_list(vorlage.tag.stand, kanton_id=kanton.kanton_id))
            except Kanton.DoesNotExist:
                raise CommandError(f"Kanton '{vorlage.region}' not found for kantonal vote.")
        else:
            used_geo_ids = set(get_geo_id_list(vorlage.tag.stand))

        # Check for invalid predictor IDs
        invalid_ids = [gid for gid in gemeinde_ids if gid not in used_geo_ids]
        if invalid_ids:
            self.stdout.write(
                self.style.WARNING(
                    f"Warning: The following predictor IDs are not active in this vote and will be ignored: {invalid_ids}"
                )
            )
            gemeinde_ids = [gid for gid in gemeinde_ids if gid in used_geo_ids]
            if not gemeinde_ids:
                raise CommandError("None of the provided Gemeinde IDs are active/valid for this vote.")

        # Check for parent municipalities with sub-districts (Zaehlkreise)
        zk_parents = set(
            Gemeinde.objects.filter(stand=vorlage.tag.stand)
            .exclude(zaehlkreis=None)
            .values_list("geo_id", flat=True)
        )
        parent_warning_ids = [gid for gid in gemeinde_ids if gid in zk_parents]
        if parent_warning_ids:
            self.stdout.write(
                self.style.WARNING(
                    f"Warning: The following predictor IDs are parent municipalities with counting districts: {parent_warning_ids}. "
                    "Usually, counting districts (Zaehlkreise) should be used as predictors instead."
                )
            )

        # Build actual results dictionary
        actual_dict = {}
        for row in df_actual.iter_rows(named=True):
            gid = row["geo_id"]
            if gid in used_geo_ids:
                actual_dict[gid] = {
                    "ja_prozent": row["ja_prozent"],
                    "stimmbeteiligung": row["stimmbeteiligung"],
                    "ja_stimmen": row["ja_stimmen"],
                    "nein_stimmen": row["nein_stimmen"],
                    "anzahl_stimmberechtigte": row["anzahl_stimmberechtigte"],
                }

        # Check if all predictors have actual results
        missing_actuals = [gid for gid in gemeinde_ids if gid not in actual_dict]
        if missing_actuals:
            raise CommandError(f"Predictor municipalities {missing_actuals} do not have actual results in the database.")

        # 4. Perform Prediction using only the selected subset
        self.stdout.write(f"Performing prediction using {len(gemeinde_ids)} predictor municipalities...")
        predicted_results = predict_results(vote_id, known_geo_ids=gemeinde_ids)
        if predicted_results is None:
            raise CommandError(
                "Prediction failed. Make sure projection models exist for this vote's Stand, "
                "and that the selected predictor list is not empty."
            )

        # 5. Merge results (actuals for known, predictions for the rest)
        predicted_dict = {}
        for gid in gemeinde_ids:
            predicted_dict[gid] = actual_dict[gid]

        for p in predicted_results:
            gid = p.geo_id
            if p.result is not None:
                predicted_dict[gid] = {
                    "ja_prozent": p.result.ja_prozent,
                    "stimmbeteiligung": p.result.stimmbeteiligung,
                    "ja_stimmen": p.result.ja_stimmen,
                    "nein_stimmen": p.result.nein_stimmen,
                    "anzahl_stimmberechtigte": p.result.anzahl_stimmberechtigte,
                }

        # 6. Aggregate Totals
        def aggregate_totals(results_map):
            total_ja = 0
            total_nein = 0
            total_stimmberechtigte = 0

            canton_map = defaultdict(lambda: {"ja_stimmen": 0, "nein_stimmen": 0, "stimmberechtigte": 0})

            for gid, val in results_map.items():
                if gid in zk_parents:
                    # Skip parent municipalities of counting districts to avoid double counting
                    continue
                if gid not in used_geo_ids:
                    continue

                total_ja += val["ja_stimmen"]
                total_nein += val["nein_stimmen"]
                total_stimmberechtigte += val["anzahl_stimmberechtigte"]

                _, _, canton_id = get_geo_info(gid)
                if canton_id:
                    c_stats = canton_map[canton_id]
                    c_stats["ja_stimmen"] += val["ja_stimmen"]
                    c_stats["nein_stimmen"] += val["nein_stimmen"]
                    c_stats["stimmberechtigte"] += val["anzahl_stimmberechtigte"]

            gueltige = total_ja + total_nein
            ja_prozent = (total_ja / gueltige * 100) if gueltige > 0 else 0.0
            stimmbeteiligung = (gueltige / total_stimmberechtigte * 100) if total_stimmberechtigte > 0 else 0.0

            cantonal_aggregated = {}
            for canton_id, stats in canton_map.items():
                c_gueltige = stats["ja_stimmen"] + stats["nein_stimmen"]
                c_ja_prozent = (stats["ja_stimmen"] / c_gueltige * 100) if c_gueltige > 0 else 0.0
                c_stimmbeteiligung = (c_gueltige / stats["stimmberechtigte"] * 100) if stats["stimmberechtigte"] > 0 else 0.0
                cantonal_aggregated[canton_id] = {
                    "ja_prozent": c_ja_prozent,
                    "stimmbeteiligung": c_stimmbeteiligung,
                }

            return {
                "ja_prozent": ja_prozent,
                "stimmbeteiligung": stimmbeteiligung,
                "cantons": cantonal_aggregated,
            }

        actual_totals = aggregate_totals(actual_dict)
        pred_totals = aggregate_totals(predicted_dict)

        # 7. Compute prediction metrics (MAE) for predicted municipalities
        diff_ja_list = []
        diff_bet_list = []
        for gid, p_val in predicted_dict.items():
            if gid in gemeinde_ids:
                continue
            if gid in zk_parents:
                continue
            if gid not in used_geo_ids:
                continue
            if gid not in actual_dict:
                continue

            act = actual_dict[gid]
            diff_ja_list.append(abs(p_val["ja_prozent"] - act["ja_prozent"]))
            diff_bet_list.append(abs(p_val["stimmbeteiligung"] - act["stimmbeteiligung"]))

        mae_ja = sum(diff_ja_list) / len(diff_ja_list) if diff_ja_list else 0.0
        mae_bet = sum(diff_bet_list) / len(diff_bet_list) if diff_bet_list else 0.0
        num_evaluated = len(diff_ja_list)

        # 8. Print Predictor municipalities reports
        self.stdout.write("\n" + "=" * 90)
        self.stdout.write("PREDICTOR MUNICIPALITIES (MEASUREMENTS & REPRESENTATIVENESS)")
        self.stdout.write("=" * 90)
        self.stdout.write(
            f"{'Gemeinde (BFS ID)':<35} {'Canton':<8} {'Yes% (Meas)':<12} {'Yes% Delta':<14} {'Turnout% (Meas)':<16} {'Turnout% Delta':<14}"
        )
        self.stdout.write("-" * 90)
        for gid in sorted(gemeinde_ids):
            name, canton_name, _ = get_geo_info(gid)
            act = actual_dict[gid]
            delta_ja = act["ja_prozent"] - actual_totals["ja_prozent"]
            delta_bet = act["stimmbeteiligung"] - actual_totals["stimmbeteiligung"]

            self.stdout.write(
                f"{f'{name} ({gid})':<35} "
                f"{canton_name:<8} "
                f"{act['ja_prozent']:>10.2f}% "
                f"{delta_ja:>+13.2f}% "
                f"{act['stimmbeteiligung']:>15.2f}% "
                f"{delta_bet:>+13.2f}%"
            )

        # 9. Print Overall results comparison
        self.stdout.write("\n" + "=" * 90)
        self.stdout.write("OVERALL RESULTS & PREDICTION ACCURACY")
        self.stdout.write("=" * 90)
        self.stdout.write(
            f"{'Metric':<20} {'Predicted Total':<20} {'Actual Total':<20} {'Delta':<20}"
        )
        self.stdout.write("-" * 90)

        delta_total_ja = pred_totals["ja_prozent"] - actual_totals["ja_prozent"]
        delta_total_bet = pred_totals["stimmbeteiligung"] - actual_totals["stimmbeteiligung"]

        self.stdout.write(
            f"{'Yes%':<20} "
            f"{pred_totals['ja_prozent']:>18.2f}% "
            f"{actual_totals['ja_prozent']:>18.2f}% "
            f"{delta_total_ja:>+19.2f}%"
        )
        self.stdout.write(
            f"{'Turnout%':<20} "
            f"{pred_totals['stimmbeteiligung']:>18.2f}% "
            f"{actual_totals['stimmbeteiligung']:>18.2f}% "
            f"{delta_total_bet:>+19.2f}%"
        )
        self.stdout.write("-" * 90)
        self.stdout.write(f"Prediction quality on {num_evaluated} predicted municipalities:")
        self.stdout.write(f"  - Mean Absolute Error (MAE) Yes%:         {mae_ja:.4f}%")
        self.stdout.write(f"  - Mean Absolute Error (MAE) Turnout%:     {mae_bet:.4f}%")

        # 10. Print Cantonal reports if national
        is_national = vorlage.region == "CH"
        if is_national:
            self.stdout.write("\n" + "=" * 90)
            self.stdout.write("CANTONAL PREDICTIONS VS. ACTUALS")
            self.stdout.write("=" * 90)
            self.stdout.write(
                f"{'Canton':<8} "
                f"{'Pred Yes%':<12} {'Act Yes%':<12} {'Yes% Delta':<14} "
                f"{'Pred Turn%':<12} {'Act Turn%':<12} {'Turn% Delta':<14}"
            )
            self.stdout.write("-" * 90)

            # Gather all cantons sorted by short name
            all_canton_shorts = sorted(cantons.values(), key=lambda c: c.short)
            for k in all_canton_shorts:
                c_id = k.kanton_id
                act_c = actual_totals["cantons"].get(c_id)
                pred_c = pred_totals["cantons"].get(c_id)

                if act_c and pred_c:
                    c_delta_ja = pred_c["ja_prozent"] - act_c["ja_prozent"]
                    c_delta_bet = pred_c["stimmbeteiligung"] - act_c["stimmbeteiligung"]

                    self.stdout.write(
                        f"{k.short:<8} "
                        f"{pred_c['ja_prozent']:>10.2f}% "
                        f"{act_c['ja_prozent']:>10.2f}% "
                        f"{c_delta_ja:>+13.2f}% "
                        f"{pred_c['stimmbeteiligung']:>10.2f}% "
                        f"{act_c['stimmbeteiligung']:>10.2f}% "
                        f"{c_delta_bet:>+13.2f}%"
                    )
            self.stdout.write("=" * 90)
        self.stdout.write("\nReport execution completed successfully.\n")
