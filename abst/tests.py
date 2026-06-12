import numpy as np
from django.test import TestCase

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
