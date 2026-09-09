"""Task 14: timestamp, weighting, bounded warehouse reads and offline cache."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from setup.ProductAssayHistory import ProductAssayHistory, ProductAssayUnavailable, awst


START = datetime(2026, 9, 6)
END = START + timedelta(days=1)


def raw(**values):
    return dict(OPF="CBOPF", BRAND=" CBSF ", OBSERVED_AT=START + timedelta(hours=7),
                SHIFT_DATE=START, SHIFT="Day", DMT=100, WMT=110, FE=58, SIO2=4,
                AL2O3=2, P=0, MN=None, SAMPLED_AT=START + timedelta(hours=10),
                LAST_UPDATED=END, **values) if not values else {**raw(), **values}


class ProductAssayHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = ProductAssayHistory(cache_directory=self.directory.name, clock=lambda: END)
        self.service._query = Mock(return_value=[raw()])

    def fetch(self, **kwargs):
        return self.service.fetch(START, END, ["CB OPF"], **kwargs)

    def records(self, rows):
        return self.service.normalize(rows, self.service.request(START, END, ["CB OPF", "CC OPF01"]))[0]

    def test_aware_inputs_convert_to_awst_and_bounds_are_half_open(self):
        self.assertEqual(awst(datetime(2026, 9, 5, 16, tzinfo=timezone.utc)), START)
        records = self.records([raw(OBSERVED_AT=START), raw(OBSERVED_AT=END), raw(OBSERVED_AT=START-timedelta(seconds=1))])
        self.assertEqual(len(records), 1)

    def test_production_time_remains_distinct_from_sample_time(self):
        result = self.fetch()["records"][0]
        self.assertEqual(result["observed_at"], "2026-09-06T07:00:00")
        self.assertEqual(result["sampled_at"], "2026-09-06T10:00:00")
        self.assertEqual((result["opf"], result["brand"]), ("CB_OPF", "CBSF"))
        self.assertEqual(result["grades"]["p"], 0)
        self.assertIsNone(result["grades"]["mn"])

    def test_incomplete_or_wrong_scope_cached_records_are_not_rendered(self):
        for change in ({"brand": None}, {"grades": {}}, {"observed_at": END.isoformat()}, {"opf": "CC_OPF01"}):
            with self.subTest(change=change):
                self.service._query.side_effect = None
                payload = self.fetch(force_refresh=True)
                payload["records"][0].update(change)
                self.service._write_cache(payload)
                self.service._query.side_effect = RuntimeError("offline")
                with self.assertRaises(ProductAssayUnavailable):
                    self.fetch(force_refresh=True)

    def test_invalid_dates_and_out_of_range_grades_do_not_become_zero(self):
        records, warnings = self.service.normalize([raw(OBSERVED_AT=None), raw(FE=101, P=-1, DMT=0)], self.service.request(START, END, ["CB OPF"]))
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0]["grades"]["fe"])
        self.assertIsNone(records[0]["grades"]["p"])
        self.assertGreaterEqual(len(warnings), 3)

    def test_requests_reject_empty_opf_reversed_and_excessive_windows_before_query(self):
        for a, b, opfs in ((END, START, ["CB"]), (START, START, ["CB"]), (START, START+timedelta(days=94), ["CB"]), (START, END, [])):
            with self.subTest(a=a, b=b), self.assertRaises(ValueError):
                self.service.fetch(a, b, opfs)
        self.service._query.assert_not_called()

    def test_query_uses_parameters_and_always_closes_connection_and_cursor(self):
        row = raw()
        loader, cursor = Mock(), Mock()
        connection = loader.connect_snowflake_with_service_account.return_value
        connection.cursor.return_value = cursor
        cursor.description = [(column,) for column in row]
        cursor.fetchall.return_value = [tuple(row.values())]
        service = ProductAssayHistory(loader, self.directory.name)
        result = service.fetch(START, END, ["CBOPF"])
        params = cursor.execute.call_args.args[1]
        self.assertIn("+08:00", params[0])
        self.assertEqual(json.loads(params[2]), ["CB_OPF"])
        self.assertEqual(result["records"][0]["grades"]["fe"], 58)
        cursor.close.assert_called_once()
        connection.close.assert_called_once()

    def test_query_failure_closes_resources(self):
        loader, cursor = Mock(), Mock()
        connection = loader.connect_snowflake_with_service_account.return_value
        connection.cursor.return_value = cursor
        cursor.execute.side_effect = RuntimeError("offline")
        service = ProductAssayHistory(loader, self.directory.name)
        with self.assertRaises(ProductAssayUnavailable):
            service.fetch(START, END, ["CB"])
        cursor.close.assert_called_once()
        connection.close.assert_called_once()

    def test_cache_is_detached_fresh_and_force_refresh_requeries(self):
        result = self.fetch()
        result["records"][0]["grades"]["fe"] = 0
        cached = self.fetch()
        self.assertEqual(cached["status"], "cached")
        self.assertEqual(cached["records"][0]["grades"]["fe"], 58)
        self.service._query.assert_called_once()
        self.fetch(force_refresh=True)
        self.assertEqual(self.service._query.call_count, 2)

    def test_expired_cache_requires_query_and_outage_returns_exact_cache_with_timestamp(self):
        original = self.fetch()
        self.service.clock = lambda: END + timedelta(days=1)
        self.service._query.side_effect = RuntimeError("offline")
        result = self.fetch()
        self.assertEqual(result["status"], "offline_cached")
        self.assertEqual(result["fetched_at"], original["fetched_at"])
        self.assertEqual(result["records"], original["records"])
        self.assertEqual(result["error"], "offline")
        with self.assertRaises(ProductAssayUnavailable):
            self.service.fetch(START, END, ["CC OPF01"])
        with self.assertRaises(ProductAssayUnavailable):
            self.service.fetch(START-timedelta(hours=1), END, ["CB OPF"])

    def test_empty_success_replaces_old_records_and_is_distinct_from_outage(self):
        self.fetch()
        self.service._query.return_value = []
        empty = self.fetch(force_refresh=True)
        self.assertEqual(empty["status"], "fresh")
        self.assertEqual(empty["records"], [])
        self.service._query.side_effect = RuntimeError("offline")
        self.assertEqual(self.fetch(force_refresh=True)["records"], [])

    def test_corrupt_cache_is_ignored_and_capacity_is_bounded(self):
        self.service.CACHE_ENTRIES = 2
        for n in range(3):
            self.service.fetch(START-timedelta(hours=n), END, ["CB OPF"])
        files = list(Path(self.directory.name).glob("*.json"))
        self.assertEqual(len(files), 2)
        path = self.service._path(self.service.request(START, END, ["CB OPF"]))
        path.write_text("not json", encoding="utf-8")
        self.service._query.side_effect = RuntimeError("offline")
        with self.assertRaises(ProductAssayUnavailable):
            self.fetch()

    def test_weighted_averages_use_valid_dmt_per_analyte_and_preserve_zero(self):
        records = self.records([raw(FE=50, DMT=100, MN=.2), raw(FE=60, DMT=300, MN=None), raw(FE=90, DMT=0)])
        row = self.service.aggregate(records, "shift")[0]
        self.assertEqual(row["grades"]["fe"], 57.5)
        self.assertEqual(row["grades"]["mn"], .2)
        self.assertEqual(row["grades"]["p"], 0)
        self.assertEqual(row["coverage"]["mn"], .25)
        self.assertEqual(row["dmt"], 400)

    def test_raw_records_keep_observations_and_shift_handles_midnight(self):
        records = self.records([raw(OBSERVED_AT=START+timedelta(hours=1), SHIFT_DATE=START-timedelta(days=1), SHIFT="Night"),
                                raw(OBSERVED_AT=START+timedelta(hours=2), SHIFT_DATE=None, SHIFT=None)])
        self.assertEqual(len(self.service.aggregate(records, "observations")), 2)
        shifted = self.service.aggregate(records, "shift")
        self.assertEqual(len(shifted), 1)
        self.assertEqual(shifted[0]["timestamp"], "2026-09-05T18:00:00")
        self.assertEqual(self.service.aggregate(records, "day")[0]["timestamp"], START.isoformat())

    def test_alias_is_resolved_per_opf_and_ambiguity_is_not_silently_combined(self):
        records = self.records([raw(), raw(OPF="CCOPF01", BRAND="CCSF"), raw(OPF="CCOPF01", BRAND="OTHER_SF")])
        selected, warnings = self.service.select_brand(records, "SF")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["warehouse_brand"], "CBSF")
        self.assertEqual(selected[0]["brand"], "SF")
        self.assertEqual(len(warnings), 1)

    def test_combined_series_is_weighted_from_records_not_average_of_opf_averages(self):
        records = self.records([raw(DMT=100, FE=50), raw(OPF="CCOPF01", BRAND="CCSF", DMT=300, FE=60)])
        records, _ = self.service.select_brand(records, "SF")
        rows = self.service.aggregate(records, "day", combined=True, expected_opfs=["CB_OPF", "CC_OPF01"])
        combined = next(r for r in rows if r["opf"] == "Combined")
        self.assertEqual(len(rows), 3)
        self.assertEqual(combined["grades"]["fe"], 57.5)
        self.assertEqual(combined["contributing_opfs"], ["CB_OPF", "CC_OPF01"])


if __name__ == "__main__":
    unittest.main()
