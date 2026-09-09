from copy import deepcopy
from datetime import datetime
import tempfile
import unittest
from unittest.mock import Mock

from classes.DestinationBuildOrder import build_order
from classes.DestinationProgress import resolve_progress, progress_settings
from setup.RecentDestinationActivity import RecentDestinationActivity, DestinationActivityUnavailable
from tests.test_destination_build_order import fixture


START = datetime(2026, 9, 9, 6)
AREAS = {"SP1": "CR1", "SP2": "CR1", "SP3": "CR2"}


def actual(destination="SP2", when="2026-09-09 05:00", **changes):
    return dict(INTERNAL_ID=changes.pop("INTERNAL_ID", destination + when), OPERATION="CC", SOURCE="CC_PITA_01_396_118_405_HG12",
                DESTINATION_FMS=destination, DESTINATION=destination + "_BUILD", OBSERVED_AT=when, WMT=100,
                MOVEMENT_TYPE="ExPit", MOVEMENT_CLASSIFICATION="Expit Ore", MOVEMENT_SUBCLASSIFICATION="Expit Ore", **changes)


class DestinationActivityTests(unittest.TestCase):
    def service(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        service = RecentDestinationActivity(cache_directory=temp.name, clock=lambda: START)
        service._query = Mock(return_value=[actual()])
        return service

    def test_boundaries_filters_aliases_duplicates_and_timezone(self):
        service = self.service()
        request = service.request("CC", START, 12, AREAS, "sig")
        records = [actual(when="2026-09-08 18:00"), actual(when="2026-09-09 06:00"),
                   actual(when="2026-09-08 17:59"), actual(when="2026-09-08T21:00:00+00:00")]
        direct = actual(); direct["MOVEMENT_SUBCLASSIFICATION"] = "Expit Ore Direct Feed"
        other = actual(); other["OPERATION"] = "CB"
        negative = actual(); negative["WMT"] = -1
        result, warnings = service.normalize(records + [records[0], direct, other, negative], request)
        self.assertEqual([r["observed_at"] for r in result], ["2026-09-08T18:00:00", "2026-09-09T05:00:00"])
        self.assertEqual(result[0]["material_type"], "HG")
        self.assertTrue(warnings)

    def test_exact_cache_fresh_refresh_and_offline_states(self):
        service = self.service()
        args = ("CC", START, 12, AREAS, "signature")
        first = service.fetch(*args)
        self.assertEqual(first["status"], "fresh")
        self.assertEqual(service.fetch(*args)["status"], "cached")
        service._query.assert_called_once()
        service._query.side_effect = ConnectionError("offline")
        fallback = service.fetch(*args, force_refresh=True)
        self.assertEqual(fallback["status"], "offline_cached")
        self.assertEqual(fallback["records"], first["records"])
        for changed in [("CB", START, 12, AREAS, "signature"), ("CC", START, 13, AREAS, "signature"),
                        ("CC", START, 12, AREAS, "new-source"), ("CC", START, 12, {"SP2": "CR2"}, "signature")]:
            with self.assertRaises(DestinationActivityUnavailable):
                service.fetch(*changed)

    def test_empty_results_and_oversized_errors_are_not_stale_success(self):
        service = self.service()
        service._query.return_value = []
        args = ("CC", START, 12, AREAS, "signature")
        self.assertEqual(service.fetch(*args)["records"], [])
        service._query.side_effect = ValueError("Too many movements")
        with self.assertRaisesRegex(ValueError, "Too many"):
            service.fetch(*args, force_refresh=True)
        for hours in (0, -1, 745, float("nan")):
            with self.assertRaises(ValueError):
                service.request("CC", START, hours, AREAS, "sig")

    def test_recency_beats_tonnage_and_ambiguous_instances_remain_unconfirmed(self):
        service = self.service()
        request = service.request("CC", START, 12, AREAS, "sig")
        rows = [actual("SP1", "2026-09-09 03:00"), actual("SP2")]
        rows[0]["WMT"] = 10000
        records, _ = service.normalize(rows, request)
        order = build_order(fixture(), AREAS)
        lane = resolve_progress(order, {"records": records})[0]
        self.assertEqual(lane["detected_destination"], "SP2")
        self.assertEqual(lane["current"]["order_position"], 2)
        self.assertEqual(lane["previous"]["destination"], "SP1")
        self.assertEqual(lane["next"]["build_instance"], 2)
        self.assertTrue(lane["warnings"])
        records, _ = service.normalize([actual("SP1")], request)
        lane = resolve_progress(order, {"records": records})[0]
        self.assertEqual(lane["detected_destination"], "SP1")
        self.assertIsNone(lane["current"])
        self.assertTrue(lane["ambiguous"])
        chosen = lane["sequence"][-1]["instance_id"]
        reviewed = resolve_progress(order, {"records": records}, {lane["lane_key"]: chosen})[0]
        self.assertEqual(reviewed["current"]["build_instance"], 2)
        self.assertEqual(reviewed["selection_basis"], "User selected")

    def test_tied_and_out_of_order_activity_are_explicit(self):
        service = self.service()
        request = service.request("CC", START, 12, {**AREAS, "SP4": "CR1"}, "sig")
        order = build_order(fixture(), AREAS)
        for rows in ([actual("SP1"), actual("SP2")], [actual("SP4")]):
            records, _ = service.normalize(rows, request)
            lane = resolve_progress(order, {"records": records})[0]
            self.assertIsNone(lane["current"])
            self.assertTrue(lane["ambiguous"])
        empty = resolve_progress(order, {"records": []})[0]
        self.assertEqual(empty["selection_basis"], "Unconfirmed")
        self.assertIsNone(empty["previous"])
        self.assertIsNone(empty["next"])

    def test_settings_precision_zero_and_validation(self):
        configured = progress_settings({"remaining_wmt": {"a": 0, "b": 123.456789}, "lookback_hours": 12.5})
        self.assertEqual(progress_settings(deepcopy(configured)), configured)
        for changes in ({"remaining_wmt": {"a": -1}}, {"schema_version": 2}, {"lookback_hours": 0}):
            with self.assertRaises(ValueError):
                progress_settings(changes)


if __name__ == "__main__":
    unittest.main()
