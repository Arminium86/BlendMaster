from copy import deepcopy
from datetime import datetime
import tempfile
import unittest
from unittest.mock import Mock, MagicMock

from classes.DestinationBuildOrder import build_order
from classes.DestinationProgress import resolve_progress, progress_settings
from setup.RecentDestinationActivity import RecentDestinationActivity, DestinationActivityUnavailable
from tests.test_destination_build_order import fixture


START = datetime(2026, 9, 9, 6)
AREAS = {"SP1": "CR1", "SP2": "CR1", "SP3": "CR2"}


def actual(destination="SP2", when="2026-09-09 05:00", **changes):
    return dict(INTERNAL_ID=changes.pop("INTERNAL_ID", destination + when), OPERATION="Christmas Creek", SOURCE="CC_PITA_01_396_118_405_HG12",
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

    def test_scenario_codes_match_warehouse_operation_names_without_cross_site_leakage(self):
        service = self.service()
        for site, operation in (("CC", "Christmas Creek"), ("CB", "Cloudbreak"), ("KV", "Kings"),
                                ("VK", "Kings"), ("FT", "Firetail"), ("EW", "Eliwana"), ("IB", "Iron Bridge")):
            with self.subTest(site=site):
                request = service.request(site, START, 24, AREAS, "sig")
                row = actual(); row["OPERATION"] = operation
                other = actual(); other["OPERATION"] = "Christmas Creek Stockyard"
                records, _ = service.normalize([row, other], request)
                self.assertEqual(len(records), 1)
                self.assertEqual(request["operation"], operation.upper())

    def test_query_binds_warehouse_name_and_old_code_only_cache_cannot_hide_activity(self):
        service = self.service()
        request = service.request("CC", START, 24, AREAS, "sig")
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        service.inventory_loader = Mock()
        service.inventory_loader.connect_snowflake_with_service_account.return_value = connection
        RecentDestinationActivity._query(service, request)
        self.assertEqual(cursor.execute.call_args.args[1][2], "CHRISTMAS CREEK")
        connection.close.assert_called_once()
        legacy = dict(request); legacy.pop("operation")
        self.assertNotEqual(service._path(legacy), service._path(request))
        from classes.DestinationBuildOrder import digest
        service._write_cache(dict(request=legacy, records=[], data_signature=digest([]), fetched_at=START.isoformat(), warnings=[]))
        result = service.fetch("CC", START, 24, AREAS, "sig")
        self.assertEqual(result["status"], "fresh")
        self.assertEqual(len(result["records"]), 1)

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
            self.assertEqual(lane["ambiguous"], len(rows) == 2)
        empty = resolve_progress(order, {"records": []})[0]
        self.assertEqual(empty["selection_basis"], "Unconfirmed")
        self.assertIsNone(empty["previous"])
        self.assertIsNone(empty["next"])

    def test_detection_filters_destination_by_rom_area_and_material_2wp_order(self):
        order = build_order(fixture(), AREAS)
        records = [dict(rom_area="CR1", material_type="HG", destination="SP2", observed_at="2026-09-09T04:00:00", wmt=50),
                   dict(rom_area="CR1", material_type="HG", destination="SP4", observed_at="2026-09-09T05:00:00", wmt=90),
                   dict(rom_area="CR2", material_type="BA", destination="SP2", observed_at="2026-09-09T05:00:00", wmt=90)]
        result = resolve_progress(order, {"status": "fresh", "records": records})
        self.assertEqual(result[0]["detected_destination"], "SP2")
        self.assertEqual(result[0]["activity_wmt"], 50)
        self.assertEqual(len(result[0]["evidence"]), 1)
        self.assertIn("excluded from detection", result[0]["warnings"][0])
        self.assertIsNone(result[1]["detected_destination"])
        self.assertEqual(result[1]["current"]["destination"], "SP3")
        self.assertEqual(len(records), 3)  # Full actual-movement audit is retained.

    def test_successful_empty_lookup_assumes_first_but_failures_do_not(self):
        from classes.DestinationProgress import ASSUMED_FIRST_BUILD_BASIS
        order = build_order(fixture(), AREAS)
        for status in ("fresh", "cached"):
            row = resolve_progress(order, {"status": status, "records": []})[0]
            self.assertEqual(row["selection_basis"], ASSUMED_FIRST_BUILD_BASIS)
            self.assertIsNone(row["detected_destination"])
            self.assertEqual((row["current"]["destination"], row["current"]["build_instance"]), ("SP1", 1))
            self.assertEqual(row["next"]["destination"], "SP2")
            self.assertFalse(row["ambiguous"])
            selected = {row["lane_key"]: row["sequence"][-1]["instance_id"]}
            manual = resolve_progress(order, {"status": status, "records": []}, selected)[0]
            self.assertEqual(manual["current"]["build_instance"], 2)
            self.assertEqual(manual["selection_basis"], "User selected")
        for activity in ({}, {"status": "offline_cached", "records": [], "error": "offline"},
                         {"status": "fresh", "records": [], "error": "failed"}, {"status": "unavailable", "records": []}):
            self.assertIsNone(resolve_progress(order, activity)[0]["current"])

    def test_successful_ambiguous_activity_does_not_assume_first(self):
        order = build_order(fixture(), AREAS)
        for destinations in (("SP1",), ("SP1", "SP2")):
            records = [dict(rom_area="CR1", material_type="HG", destination=d, observed_at="2026-09-09T05:00:00", wmt=100) for d in destinations]
            row = resolve_progress(order, {"status": "fresh", "records": records})[0]
            self.assertIsNone(row["current"])
            self.assertTrue(row["ambiguous"])

    def test_settings_precision_zero_and_validation(self):
        configured = progress_settings({"remaining_wmt": {"a": 0, "b": 123.456789}, "lookback_hours": 12.5})
        self.assertEqual(progress_settings(deepcopy(configured)), configured)
        for changes in ({"remaining_wmt": {"a": -1}}, {"schema_version": 2}, {"lookback_hours": 0}):
            with self.assertRaises(ValueError):
                progress_settings(changes)


if __name__ == "__main__":
    unittest.main()
