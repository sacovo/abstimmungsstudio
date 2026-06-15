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

        y_final = predict_missing_results(projection, results, mask, alpha=0.0)

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


class KantonalImportTests(TestCase):
    @patch("abst.store.requests.get")
    def test_fetch_results_kantonal_includes_zaehlkreise(self, mock_get):
        from abst.store import fetch_results_kantonal
        from abst.models import Kanton
        
        Kanton.objects.create(name="Zürich", short="ZH", kanton_id=1, lang_code="de")

        # Mock JSON data
        mock_json = {
            "abstimmtag": "20260614",
            "kantone": [
                {
                    "geoLevelnummer": "1",
                    "geoLevelname": "Zürich",
                    "vorlagen": [

                        {
                            "vorlagenId": 1234,
                            "vorlagenTitel": [{"langKey": "de", "text": "Test Vorlage"}],
                            "vorlageBeendet": True,
                            "vorlageAngenommen": True,
                            "resultat": {"gebietAusgezaehlt": True},
                            "gemeinden": [
                                {
                                    "geoLevelnummer": "1",
                                    "geoLevelname": "Aeugst am Albis",
                                    "resultat": {
                                        "gebietAusgezaehlt": True,
                                        "jaStimmenInProzent": 50.0,
                                        "jaStimmenAbsolut": 100,
                                        "neinStimmenAbsolut": 100,
                                        "stimmbeteiligungInProzent": 50.0,
                                        "anzahlStimmberechtigte": 400
                                    }
                                }
                            ],
                            "zaehlkreise": [
                                {
                                    "geoLevelnummer": "10230",
                                    "geoLevelname": "Winterthur Altstadt",
                                    "resultat": {
                                        "gebietAusgezaehlt": True,
                                        "jaStimmenInProzent": 60.0,
                                        "jaStimmenAbsolut": 300,
                                        "neinStimmenAbsolut": 200,
                                        "stimmbeteiligungInProzent": 50.0,
                                        "anzahlStimmberechtigte": 1000
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        
        mock_response = mock_get.return_value
        mock_response.json.return_value = mock_json
        
        results, vorlagen = fetch_results_kantonal("http://mock-url.json")
        
        self.assertEqual(len(vorlagen), 1)
        self.assertEqual(vorlagen[0].vorlagen_id, 1234)
        self.assertTrue(vorlagen[0].has_zk)
        
        # Should have 2 results: 1 gemeinde + 1 zaehlkreis
        self.assertEqual(len(results), 2)
        
        geo_ids = [r.geo_id for r in results]
        self.assertIn(1, geo_ids)
        self.assertIn(10230, geo_ids)
        
        # Verify zaehlkreis data
        zk_res = next(r for r in results if r.geo_id == 10230)
        self.assertEqual(zk_res.result.ja_stimmen, 300)
        self.assertEqual(zk_res.result.nein_stimmen, 200)
        self.assertEqual(zk_res.result.stimmbeteiligung, 50.0)


class MCPToolsTests(TestCase):
    def setUp(self):
        import datetime
        from abst.models import GeoStand, Abstimmungstag, Vorlage, Gemeinde
        self.gs = GeoStand.objects.create(url="http://test.com", date=datetime.date(2026, 6, 14))
        self.tag = Abstimmungstag.objects.create(
            date=datetime.date(2026, 6, 14),
            name="Test Voting Day",
            stand=self.gs
        )
        self.vorlage = Vorlage.objects.create(
            name="Test Vote",
            vorlagen_id=9999,
            tag=self.tag,
            region="CH",
            finished=True
        )
        self.gemeinde = Gemeinde.objects.create(
            name="Test Commune",
            geo_id=1,
            kanton="ZH",
            kanton_id=1,
            stand=self.gs
        )

    def test_get_current_votes(self):
        from abst.management.commands.run_mcp import get_current_votes
        res = get_current_votes()
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["vorlagen_id"], 9999)
        self.assertEqual(res[0]["name"], "Test Vote")

        res_filtered = get_current_votes(region="ZH")
        self.assertEqual(len(res_filtered), 0)

        res_filtered_ch = get_current_votes(region="CH")
        self.assertEqual(len(res_filtered_ch), 1)

    @patch("abst.management.commands.run_mcp.get_abst_result_total")
    @patch("abst.management.commands.run_mcp.get_national_timeline")
    def test_get_vote_results(self, mock_timeline, mock_total):
        import polars as pl
        from abst.management.commands.run_mcp import get_vote_results
        
        mock_total.return_value = pl.DataFrame([
            {"status": "final", "ja_stimmen": 100, "nein_stimmen": 50, "anzahl_stimmberechtigte": 200}
        ])
        mock_timeline.return_value = []
        
        res = get_vote_results(9999)
        self.assertEqual(res["vorlage_id"], 9999)
        self.assertEqual(res["counted"]["ja_stimmen"], 100)
        self.assertEqual(res["counted"]["ja_prozent"], 66.67)

    @patch("abst.management.commands.run_mcp.get_correlations")
    def test_perform_correlation_analysis(self, mock_correlations):
        from abst.management.commands.run_mcp import perform_correlation_analysis
        mock_correlations.return_value = [{"id": "foo", "name": "Foo", "coefficient": 0.8}]
        
        res = perform_correlation_analysis(9999, "ja_prozent")
        self.assertEqual(res[0]["id"], "foo")
        self.assertEqual(res[0]["coefficient"], 0.8)

    @patch("abst.management.commands.run_mcp.get_commune_stats")
    def test_get_commune_statistics(self, mock_stats):
        import polars as pl
        from abst.management.commands.run_mcp import get_commune_statistics
        
        mock_stats.return_value = pl.DataFrame([
            {"geo_id": 1, "pop_total_2024": 1234.0}
        ])
        
        res = get_commune_statistics(9999, ["pop_total_2024"])
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["geo_id"], 1)
        self.assertEqual(res[0]["name"], "Test Commune")
        self.assertEqual(res[0]["pop_total_2024"], 1234.0)

    @patch("abst.management.commands.run_mcp.get_abst_results")
    def test_get_commune_results_for_vote(self, mock_results):
        import polars as pl
        from abst.management.commands.run_mcp import get_commune_results_for_vote
        
        mock_results.return_value = pl.DataFrame([
            {"geo_id": 1, "status": "final", "ja_stimmen": 60, "nein_stimmen": 40, "anzahl_stimmberechtigte": 120, "ja_prozent": 60.0, "stimmbeteiligung": 83.33}
        ])
        
        res = get_commune_results_for_vote(9999)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["name"], "Test Commune")
        self.assertEqual(res[0]["yes_pct"], 60.0)

    @patch("abst.behavior.calculate_behavior")
    def test_perform_waehlerwanderung(self, mock_calc):
        from abst.management.commands.run_mcp import perform_waehlerwanderung
        mock_calc.return_value = {"matrix": [[0.5]]}
        
        res = perform_waehlerwanderung(9999, source_type="election", wahlen_scope="lager")
        self.assertEqual(res["matrix"], [[0.5]])

    def test_api_key_middleware(self):
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import JSONResponse
        from abst.management.commands.run_mcp import APIKeyMiddleware

        async def dummy_endpoint(request):
            return JSONResponse({"success": True})

        app = Starlette(routes=[
            Route("/test", dummy_endpoint, methods=["GET", "POST", "OPTIONS"]),
            Route("/messages", dummy_endpoint, methods=["POST"]),
        ])
        app.add_middleware(APIKeyMiddleware, keys=["secret-key-1", "secret-key-2"])

        client = TestClient(app)

        # 1. Test missing API key
        resp = client.get("/test")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json(), {"error": "Unauthorized: Invalid or missing API key."})

        # 2. Test invalid API key
        resp = client.get("/test", headers={"Authorization": "Bearer invalid-key"})
        self.assertEqual(resp.status_code, 401)

        # 3. Test valid Bearer token
        resp = client.get("/test", headers={"Authorization": "Bearer secret-key-1"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"success": True})

        # 4. Test valid X-API-Key header
        resp = client.get("/test", headers={"X-API-Key": "secret-key-2"})
        self.assertEqual(resp.status_code, 200)

        # 5. Test valid query parameter
        resp = client.get("/test?api_key=secret-key-1")
        self.assertEqual(resp.status_code, 200)

        # 6. Test CORS OPTIONS request passes through without key
        resp = client.options("/test")
        self.assertEqual(resp.status_code, 200)

        # 7. Test /messages endpoint does not require API key
        resp = client.post("/messages")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"success": True})

    def test_mcp_doc_requires_login(self):
        from django.urls import reverse
        url = reverse("abst:mcp_doc")
        
        # Unauthorized client (should redirect to login)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url)

        # Authorized client
        from django.contrib.auth.models import User
        User.objects.create_user(username="testuser", password="password")
        self.client.login(username="testuser", password="password")
        
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_waehlerwanderung_info_accessible(self):
        from django.urls import reverse
        url = reverse("abst:waehlerwanderung_info")
        
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)







