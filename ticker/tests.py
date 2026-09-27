"""Tests for the significance gate.

The detector decides whether the ticker speaks at all, so it is the part worth
pinning down. It is a pure function over snapshot dicts, so these need no
database, no Influx and no vote day.
"""

from django.test import SimpleTestCase, TestCase

from abst.models import Abstimmungstag, GeoStand, Vorlage
from ticker.events import CITY_MIN_SHARE, detect
from ticker.models import TickerEvent
from ticker.service import dismiss_event, pending_events, sync_events


def snapshot(**overrides) -> dict:
    """A mid-afternoon federal vote heading for a clear No."""
    base = {
        "vorlage_id": 1,
        "name": "Testvorlage",
        "region": "CH",
        "doppeltes_mehr": False,
        "finished": False,
        "angenommen": False,
        "ja_staende": 0.0,
        "nein_staende": 0.0,
        "gemeinden_final": 400,
        "gemeinden_total": 2100,
        "total_voters": 1_000_000,
        "counted_share": 20.0,
        "counted_yes": 44.0,
        "projected_yes": 44.0,
        "ci_10": 42.0,
        "ci_90": 46.0,
        "mae": 1.0,
        "projected_bet": 45.0,
        "kantone": [],
        "large": [],
        "outliers": [],
    }
    base.update(overrides)
    return base


def kinds(events) -> list[str]:
    return [e["kind"] for e in events]


class QuietByDefaultTests(SimpleTestCase):
    def test_no_projection_means_no_events(self):
        self.assertEqual(detect(snapshot(projected_yes=None), None), [])

    def test_too_little_counted_means_no_events(self):
        self.assertEqual(detect(snapshot(counted_share=1.0), None), [])

    def test_unchanged_state_produces_no_new_keys(self):
        # detect() is stateless: a standing fact like "decided" is re-reported
        # every time, and the stable key is what stops it being posted twice.
        current = snapshot()
        first = detect(current, None)
        again = detect(current, current)
        self.assertEqual(
            {e["key"] for e in again} - {e["key"] for e in first}, set()
        )

    def test_small_drift_is_not_worth_a_post(self):
        events = detect(snapshot(projected_yes=44.8), snapshot())
        self.assertNotIn("shift", kinds(events))


class FirstSignalTests(SimpleTestCase):
    def test_first_projection_is_reported_once(self):
        events = detect(snapshot(), None)
        self.assertIn("first_signal", kinds(events))

    def test_first_signal_does_not_repeat_after_a_post(self):
        events = detect(snapshot(), snapshot())
        self.assertNotIn("first_signal", kinds(events))

    def test_a_curtain_raiser_does_not_swallow_the_first_projection(self):
        # An entry written before counting starts carries a snapshot with no
        # projection. The first real projection must still be reported.
        before_counting = snapshot(
            counted_share=0.0, projected_yes=None, ci_10=None, ci_90=None
        )
        events = detect(snapshot(), before_counting)
        self.assertIn("first_signal", kinds(events))

    def test_an_empty_previous_snapshot_is_treated_as_no_post(self):
        events = detect(snapshot(), {})
        self.assertIn("first_signal", kinds(events))


class DecidedTests(SimpleTestCase):
    def test_one_sided_band_is_decided(self):
        events = detect(snapshot(ci_10=42.0, ci_90=46.0), None)
        self.assertIn("decided", kinds(events))

    def test_band_across_fifty_is_not_decided(self):
        events = detect(snapshot(projected_yes=49.5, ci_10=47.0, ci_90=52.0), None)
        self.assertNotIn("decided", kinds(events))

    def test_too_early_to_call(self):
        events = detect(snapshot(counted_share=5.0), None)
        self.assertNotIn("decided", kinds(events))


class MovementTests(SimpleTestCase):
    def test_crossing_fifty_is_a_flip(self):
        events = detect(snapshot(projected_yes=51.0), snapshot(projected_yes=48.0))
        flip = [e for e in events if e["kind"] == "flip"]
        self.assertEqual(len(flip), 1)
        self.assertEqual(flip[0]["severity"], 3)

    def test_big_move_on_the_same_side_is_a_shift(self):
        events = detect(snapshot(projected_yes=41.0), snapshot(projected_yes=44.0))
        self.assertIn("shift", kinds(events))

    def test_a_flip_is_not_also_reported_as_a_shift(self):
        events = detect(snapshot(projected_yes=52.0), snapshot(projected_yes=44.0))
        self.assertIn("flip", kinds(events))
        self.assertNotIn("shift", kinds(events))

    def test_flip_keys_differ_so_a_second_flip_is_seen(self):
        first = detect(snapshot(projected_yes=51.0, gemeinden_final=400), snapshot(projected_yes=48.0))
        second = detect(snapshot(projected_yes=48.0, gemeinden_final=900), snapshot(projected_yes=51.0))
        self.assertNotEqual(first[0]["key"], second[0]["key"])


class PlaceTests(SimpleTestCase):
    def city(self, **overrides):
        place = {
            "geo_id": 261,
            "name": "Zürich",
            "status": "final",
            "yes": 58.0,
            "bet": 50.0,
            "voters": 250_000,
            "residual": 6.0,
        }
        place.update(overrides)
        return place

    def test_large_city_reporting_is_an_event(self):
        events = detect(snapshot(large=[self.city()]), snapshot())
        self.assertIn("city_in", kinds(events))

    def test_a_city_still_counting_is_not(self):
        events = detect(snapshot(large=[self.city(status="predicted")]), snapshot())
        self.assertNotIn("city_in", kinds(events))

    def test_a_small_place_is_not_news(self):
        below = int(1_000_000 * CITY_MIN_SHARE / 100) - 1
        events = detect(snapshot(large=[self.city(voters=below)]), snapshot())
        self.assertNotIn("city_in", kinds(events))

    def test_outliers_are_surfaced_with_low_severity(self):
        outlier = {
            "geo_id": 2701,
            "name": "Riehen",
            "kanton": "BS",
            "yes": 61.0,
            "expected": 52.0,
            "residual": 9.0,
            "voters": 15_000,
        }
        events = detect(snapshot(outliers=[outlier]), snapshot())
        found = [e for e in events if e["kind"] == "outlier"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], 1)


class StaendemehrTests(SimpleTestCase):
    def test_volksmehr_without_staendemehr_is_flagged(self):
        events = detect(
            snapshot(
                doppeltes_mehr=True,
                projected_yes=52.0,
                ci_10=51.0,
                ci_90=53.0,
                ja_staende=9.0,
                nein_staende=14.0,
            ),
            None,
        )
        self.assertIn("staende_split", kinds(events))

    def test_agreement_is_not_flagged(self):
        events = detect(
            snapshot(
                doppeltes_mehr=True,
                projected_yes=52.0,
                ci_10=51.0,
                ci_90=53.0,
                ja_staende=18.0,
                nein_staende=5.0,
            ),
            None,
        )
        self.assertNotIn("staende_split", kinds(events))

    def test_too_few_cantons_counted_is_not_flagged(self):
        events = detect(
            snapshot(
                doppeltes_mehr=True,
                projected_yes=52.0,
                ci_10=51.0,
                ci_90=53.0,
                ja_staende=1.0,
                nein_staende=2.0,
            ),
            None,
        )
        self.assertNotIn("staende_split", kinds(events))


class FinalTests(SimpleTestCase):
    def test_final_result_is_the_only_event_left(self):
        events = detect(
            snapshot(
                finished=True,
                angenommen=False,
                large=[
                    {
                        "geo_id": 261,
                        "name": "Zürich",
                        "status": "final",
                        "yes": 58.0,
                        "voters": 250_000,
                        "residual": 6.0,
                    }
                ],
            ),
            snapshot(),
        )
        self.assertEqual(kinds(events), ["final"])
        self.assertEqual(events[0]["severity"], 3)

    def test_final_reports_the_direction(self):
        events = detect(snapshot(finished=True, angenommen=True, projected_yes=56.0), None)
        self.assertIn("Ja", events[0]["summary"])


class SyncEventTests(TestCase):
    """The persistence layer is what actually guarantees "say it once"."""

    def setUp(self):
        stand = GeoStand.objects.create(date="2026-09-27", url="https://example.invalid/s")
        tag = Abstimmungstag.objects.create(
            date="2026-09-27", name="Testtag", stand=stand
        )
        self.vorlage = Vorlage.objects.create(
            name="Testvorlage", vorlagen_id=999001, region="CH", tag=tag
        )

    def test_the_same_observation_is_only_created_once(self):
        first = sync_events(self.vorlage, snapshot())
        second = sync_events(self.vorlage, snapshot())
        self.assertTrue(first)
        self.assertEqual(second, [])

    def test_a_dismissed_event_does_not_come_back(self):
        created = sync_events(self.vorlage, snapshot())
        event = next(e for e in created if e.kind == "decided")
        dismiss_event(event, "zu früh")

        sync_events(self.vorlage, snapshot())
        event.refresh_from_db()
        self.assertEqual(event.status, TickerEvent.Status.DISMISSED)
        self.assertEqual(pending_events(self.vorlage).filter(kind="decided").count(), 0)

    def test_finishing_retires_everything_except_the_result(self):
        sync_events(self.vorlage, snapshot())
        self.assertGreater(pending_events(self.vorlage).count(), 1)

        sync_events(self.vorlage, snapshot(finished=True, angenommen=False))

        remaining = list(pending_events(self.vorlage))
        self.assertEqual([e.kind for e in remaining], ["final"])
