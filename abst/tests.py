import numpy as np
from django.test import TestCase
from unittest.mock import patch

from abst.predict import predict_missing_results


class PredictTests(TestCase):
    def test_predict_missing_results(self):
        projection = np.array([[1.0, 2.0], [1.0, 3.0], [1.0, 4.0], [1.0, 5.0]])
        # Suppose coefficients should be [0.5, 0.5]
        # Then values:
        # row 0: 0.5*1 + 0.5*2 = 1.5
        # row 1: 0.5*1 + 0.5*3 = 2.0
        # row 2: 0.5*1 + 0.5*4 = 2.5
        # row 3: 0.5*1 + 0.5*5 = 3.0
        results = [1.5, 2.0, 0.0, 0.0]
        mask = [False, False, True, True]

        y_final = predict_missing_results(projection, results, mask)

        self.assertAlmostEqual(y_final[0], 1.5)
        self.assertAlmostEqual(y_final[1], 2.0)
        self.assertAlmostEqual(y_final[2], 2.5)
        self.assertAlmostEqual(y_final[3], 3.0)


class BehaviorTests(TestCase):
    def test_behavior_options_ordering(self):
        import datetime
        from abst.behavior import get_behavior_options
        from abst.models import GeoStand, Abstimmungstag, Vorlage

        gs = GeoStand.objects.create(url="http://test.com", date=datetime.date(2025, 1, 1))

        # 1. Target vote after the election date (2023-10-22)
        tag_after = Abstimmungstag.objects.create(
            date=datetime.date(2025, 11, 30),
            name="Tag After",
            stand=gs
        )
        target_after = Vorlage.objects.create(
            name="Target After",
            vorlagen_id=9001,
            tag=tag_after,
            region="CH",
            finished=True
        )

        # A past vote that should also be in the options
        past_tag = Abstimmungstag.objects.create(
            date=datetime.date(2024, 1, 1),
            name="Past Tag",
            stand=gs
        )
        past_vote = Vorlage.objects.create(
            name="Past Vote",
            vorlagen_id=9002,
            tag=past_tag,
            region="CH",
            finished=True
        )

        opts_after = get_behavior_options(target_after.vorlagen_id)
        # Election should be at the top of the dropdown
        self.assertTrue(len(opts_after) > 0)
        self.assertEqual(opts_after[0]["type"], "election")
        self.assertEqual(opts_after[0]["id"], "election_nrw2023")

        # Past vote should follow
        self.assertEqual(opts_after[1]["type"], "vote")
        self.assertEqual(opts_after[1]["vote_id"], past_vote.vorlagen_id)

        # 2. Target vote before the election date (2023-10-22)
        tag_before = Abstimmungstag.objects.create(
            date=datetime.date(2022, 11, 30),
            name="Tag Before",
            stand=gs
        )
        target_before = Vorlage.objects.create(
            name="Target Before",
            vorlagen_id=9003,
            tag=tag_before,
            region="CH",
            finished=True
        )

        opts_before = get_behavior_options(target_before.vorlagen_id)
        # Election should NOT be in the options
        for opt in opts_before:
            self.assertNotEqual(opt["type"], "election")

    @patch("abst.behavior.get_abst_results")
    def test_calculate_behavior_vote_smoothing(self, mock_get_abst_results):
        import datetime
        import polars as pl
        from abst.behavior import calculate_behavior
        from abst.models import GeoStand, Abstimmungstag, Vorlage, Gemeinde

        gs = GeoStand.objects.create(url="http://test.com", date=datetime.date(2025, 1, 1))
        tag = Abstimmungstag.objects.create(
            date=datetime.date(2025, 11, 30),
            name="Tag Test",
            stand=gs
        )
        target = Vorlage.objects.create(
            name="Target Vote",
            vorlagen_id=7001,
            tag=tag,
            region="CH",
            finished=True
        )
        source = Vorlage.objects.create(
            name="Source Vote",
            vorlagen_id=7002,
            tag=tag,
            region="CH",
            finished=True
        )

        # Create 6 municipalities
        for i in range(1, 7):
            Gemeinde.objects.create(
                geo_id=i,
                name=f"Gemeinde {i}",
                kanton="Zürich",
                kanton_id=1,
                stand=gs
            )

        # Mock results dataframes
        target_df = pl.DataFrame({
            "geo_id": list(range(1, 7)),
            "ja_stimmen": [100] * 6,
            "nein_stimmen": [100] * 6,
            "anzahl_stimmberechtigte": [300] * 6,
            "status": ["completed"] * 6,
            "ja_prozent": [50.0] * 6,
            "stimmbeteiligung": [66.7] * 6,
        })
        source_df = pl.DataFrame({
            "geo_id": list(range(1, 7)),
            "ja_stimmen": [120] * 6,
            "nein_stimmen": [80] * 6,
            "anzahl_stimmberechtigte": [300] * 6,
            "status": ["completed"] * 6,
            "ja_prozent": [60.0] * 6,
            "stimmbeteiligung": [66.7] * 6,
        })

        def side_effect(abst_id):
            if abst_id == 7001:
                return target_df
            elif abst_id == 7002:
                return source_df
            return None

        mock_get_abst_results.side_effect = side_effect

        results = calculate_behavior(7001, "vote", 7002)

        # Check structure
        self.assertIn("links", results)
        self.assertIn("matrix", results)
        self.assertIn("source_labels", results)
        self.assertIn("target_labels", results)

        matrix = results["matrix"]
        self.assertEqual(len(matrix), 4)  # 3 source cols + Neuwähler
        self.assertEqual(len(matrix[0]), 4)

        # Ensure no row sum has 100% of its voters transition to a single target choice
        for r_idx, row in enumerate(matrix):
            row_sum = sum(row)
            if row_sum > 0:
                row_percents = [val / row_sum for val in row]
                for p in row_percents:
                    self.assertLess(p, 0.99)

    @patch("abst.behavior.query_election_strengths_df")
    @patch("abst.behavior.get_abst_results")
    def test_calculate_behavior_election_smoothing(self, mock_get_abst_results, mock_query_election_strengths_df):
        import datetime
        import polars as pl
        from abst.behavior import calculate_behavior
        from abst.models import GeoStand, Abstimmungstag, Vorlage, Gemeinde, Partei

        gs = GeoStand.objects.create(url="http://test.com", date=datetime.date(2025, 1, 1))
        tag = Abstimmungstag.objects.create(
            date=datetime.date(2025, 11, 30),
            name="Tag Test",
            stand=gs
        )
        target = Vorlage.objects.create(
            name="Target Vote",
            vorlagen_id=7001,
            tag=tag,
            region="CH",
            finished=True
        )

        # Create 6 municipalities
        for i in range(1, 7):
            Gemeinde.objects.create(
                geo_id=i,
                name=f"Gemeinde {i}",
                kanton="Zürich",
                kanton_id=1,
                stand=gs
            )

        # Create parties
        Partei.objects.create(partei_id=1, name="SVP", kurzname="SVP")
        Partei.objects.create(partei_id=2, name="SP", kurzname="SP")

        # Mock results dataframes
        target_df = pl.DataFrame({
            "geo_id": list(range(1, 7)),
            "ja_stimmen": [100] * 6,
            "nein_stimmen": [100] * 6,
            "anzahl_stimmberechtigte": [300] * 6,
            "status": ["completed"] * 6,
            "ja_prozent": [50.0] * 6,
            "stimmbeteiligung": [66.7] * 6,
        })
        
        # 50% strength for SVP, 50% for SP
        election_df = pl.DataFrame({
            "geo_id": list(range(1, 7)),
            "1": [50.0] * 6,
            "2": [50.0] * 6,
        })

        mock_get_abst_results.return_value = target_df
        mock_query_election_strengths_df.return_value = election_df

        results = calculate_behavior(7001, "election", wahlen_scope="partei")

        # Check structure
        self.assertIn("links", results)
        self.assertIn("matrix", results)
        self.assertIn("source_labels", results)
        self.assertIn("target_labels", results)

        matrix = results["matrix"]
        self.assertEqual(len(matrix), 4)  # SVP, SP, Nichtwähler, Neuwähler
        self.assertEqual(len(matrix[0]), 4)

        # Ensure no row sum has 100% of its voters transition to a single target choice
        for r_idx, row in enumerate(matrix):
            row_sum = sum(row)
            if row_sum > 0:
                row_percents = [val / row_sum for val in row]
                for p in row_percents:
                    self.assertLess(p, 0.99)

    @patch("abst.behavior.get_abst_results")
    def test_generate_behavior_excel_vote(self, mock_get_abst_results):
        import datetime
        import io
        import polars as pl
        import pandas as pd
        from abst.behavior import generate_behavior_excel
        from abst.models import GeoStand, Abstimmungstag, Vorlage, Gemeinde

        gs = GeoStand.objects.create(url="http://test.com", date=datetime.date(2025, 1, 1))
        tag = Abstimmungstag.objects.create(
            date=datetime.date(2025, 11, 30),
            name="Tag Test",
            stand=gs
        )
        target = Vorlage.objects.create(
            name="Target Vote",
            vorlagen_id=7001,
            tag=tag,
            region="CH",
            finished=True
        )
        source = Vorlage.objects.create(
            name="Source Vote",
            vorlagen_id=7002,
            tag=tag,
            region="CH",
            finished=True
        )

        # Create municipalities in two cantons: Zürich and Bern
        Gemeinde.objects.create(geo_id=1, name="Zürich Stadt", kanton="Zürich", kanton_id=1, stand=gs)
        Gemeinde.objects.create(geo_id=2, name="Winterthur", kanton="Zürich", kanton_id=1, stand=gs)
        Gemeinde.objects.create(geo_id=3, name="Bülach", kanton="Zürich", kanton_id=1, stand=gs)
        Gemeinde.objects.create(geo_id=4, name="Uster", kanton="Zürich", kanton_id=1, stand=gs)
        Gemeinde.objects.create(geo_id=5, name="Horgen", kanton="Zürich", kanton_id=1, stand=gs)
        
        Gemeinde.objects.create(geo_id=6, name="Bern Stadt", kanton="Bern", kanton_id=2, stand=gs)
        Gemeinde.objects.create(geo_id=7, name="Biel", kanton="Bern", kanton_id=2, stand=gs)
        Gemeinde.objects.create(geo_id=8, name="Thun", kanton="Bern", kanton_id=2, stand=gs)
        Gemeinde.objects.create(geo_id=9, name="Köniz", kanton="Bern", kanton_id=2, stand=gs)
        Gemeinde.objects.create(geo_id=10, name="Burgdorf", kanton="Bern", kanton_id=2, stand=gs)

        # Mock results dataframes
        target_df = pl.DataFrame({
            "geo_id": list(range(1, 11)),
            "ja_stimmen": [100] * 10,
            "nein_stimmen": [100] * 10,
            "anzahl_stimmberechtigte": [300] * 10,
            "status": ["completed"] * 10,
            "ja_prozent": [50.0] * 10,
            "stimmbeteiligung": [66.7] * 10,
        })
        source_df = pl.DataFrame({
            "geo_id": list(range(1, 11)),
            "ja_stimmen": [120] * 10,
            "nein_stimmen": [80] * 10,
            "anzahl_stimmberechtigte": [300] * 10,
            "status": ["completed"] * 10,
            "ja_prozent": [60.0] * 10,
            "stimmbeteiligung": [66.7] * 10,
        })

        def side_effect(abst_id):
            if abst_id == 7001:
                return target_df
            elif abst_id == 7002:
                return source_df
            return None

        mock_get_abst_results.side_effect = side_effect

        excel_bytes = generate_behavior_excel(7001, "vote", 7002)
        
        import zipfile
        import re
        with zipfile.ZipFile(io.BytesIO(excel_bytes)) as z:
            workbook_xml = z.read("xl/workbook.xml").decode("utf-8")
            sheet_names = re.findall(r'name="([^"]+)"', workbook_xml)
        
        self.assertIn("Wählerwanderung (Absolut)", sheet_names)
        self.assertIn("Wählerwanderung (Prozent)", sheet_names)
        self.assertIn("Kantonale Übersicht (Absolut)", sheet_names)
        self.assertIn("Kantonale Übersicht (Prozent)", sheet_names)
        self.assertIn("Zürich", sheet_names)
        self.assertIn("Bern", sheet_names)


from unittest.mock import patch
from django.contrib.admin import AdminSite
from abst.admin import AbstimmungstagAdmin

class AdminActionTests(TestCase):
    def setUp(self):
        self.site = AdminSite()

    @patch("abst.admin.create_models")
    @patch("abst.admin.messages")
    def test_generate_projection_action(
        self, mock_messages, mock_create_models
    ):
        import datetime
        from abst.models import GeoStand, Abstimmungstag

        gs = GeoStand.objects.create(url="http://test.com", date=datetime.date(2025, 1, 1))
        tag = Abstimmungstag.objects.create(
            date=datetime.date(2025, 11, 30),
            name="Tag Test",
            stand=gs
        )

        admin_inst = AbstimmungstagAdmin(Abstimmungstag, self.site)
        
        # Mock request
        from django.test import RequestFactory
        factory = RequestFactory()
        request = factory.get(f"/admin/abst/abstimmungstag/{tag.id}/change/")

        # Call the action
        response = admin_inst.generate_projection(request, tag.id)
        
        # Assertions
        mock_create_models.assert_called_once_with(tag)
        mock_messages.success.assert_called_once()
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/admin/abst/abstimmungstag/{tag.id}/change/", response.url)



