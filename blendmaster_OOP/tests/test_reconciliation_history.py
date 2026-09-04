import copy
import json
import pickle
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd

from classes.ExpitSequenceReconciler import grade_block_key
from setup.DataStreamReconciliation import DataStreamReconciliation
from setup.InventoryBuildLineage import InventoryBuildLineage, canonical_block
from setup.ReconciliationHistory import ReconciliationHistory


GB1 = "Reserves/CC/PIT01/01/453/426/456/LG01_123"
GB2 = "CC_PIT02_01_0453_426_0456_HG02"
BUILD = "ROM01_RP01_0001_26001"
OTHER_BUILD = "ROM01_RP01_0001_25001"
AS_OF = datetime(2026, 8, 22, 6)


def history_row(start="2026-08-20 06:00", *, sources=None, feed=1000.0, brand="CCFB"):
    start = pd.Timestamp(start)
    row = {
        "DERIVED_OPERATION": "CC_OPF01", "BRAND": brand,
        "PERIOD_START": start, "PERIOD_END": start + pd.Timedelta(hours=12),
        "SHIFT": "Day" if start.hour == 6 else "Night",
        "FEED_WMT": feed, "PROD_DMT": 600.0, "PROD_WMT": 700.0,
        "SOURCE_ROWS": 10, "CBFL_CAMPAIGN": 0,
        "FEED_SOURCES_JSON": sources if sources is not None else [
            {"source": BUILD, "feed_wmt": feed, "source_rows": 10},
        ],
    }
    for analyte in ("FE", "SIO2", "AL2O3", "P", "MN"):
        row[f"BLEND_RECON_{analyte}"] = 1.1
        row[f"REGRESSION_RECON_{analyte}"] = 0.9
    return row


def inbound(block, tonnes, *, build=BUILD, when="2026-08-19 18:00", balance=500.0):
    return {
        "BUILD": build, "FOOTPRINT": "ROM01_RP01_0001", "INVENTORY_WMT": balance,
        "GRADE_BLOCK_NAME": block, "AVAILABLE_AT": pd.Timestamp(when),
        "INBOUND_WMT": tonnes,
    }


def convert(rows=None, lineage=None, **kwargs):
    return ReconciliationHistory().build_samples(
        pd.DataFrame(rows if rows is not None else [history_row()]),
        pd.DataFrame(lineage if lineage is not None else [inbound(GB1, 400), inbound(GB2, 600)]),
        AS_OF, "CC OPF01", **kwargs,
    )


class HistorySampleTests(unittest.TestCase):
    def test_one_sample_per_kind_preserves_full_period_factors_and_composition(self):
        samples, warnings = convert()
        self.assertEqual(len(samples), 2)
        self.assertEqual(warnings, [])
        self.assertEqual([sample["kind"] for sample in samples], ["blend", "regression"])
        for sample in samples:
            self.assertEqual(sample["grain"], "shift")
            self.assertEqual(sample["feed_wmt"], 1000.0)
            self.assertEqual(sample["product_dmt"], 600.0)
            self.assertEqual(sample["source_rows"], 10)
            blocks = {b["grade_block_key"]: b for b in sample["contributing_blocks"]}
            self.assertEqual(blocks[grade_block_key(GB1)]["feed_wmt"], 400.0)
            self.assertEqual(blocks[grade_block_key(GB2)]["feed_wmt"], 600.0)
            self.assertEqual(blocks[grade_block_key(GB1)]["material_type"], "LG")
            self.assertEqual(len(blocks[grade_block_key(GB1)]["spatial_key"]), 6)
            self.assertEqual(sample["provenance"]["lineage_coverage"], 1.0)
        self.assertEqual(samples[0]["factors"]["fe"], 1.1)
        self.assertEqual(samples[1]["factors"]["fe"], 0.9)

    def test_both_kinds_keep_total_feed_weight_across_periods(self):
        first = history_row(feed=100)
        second = history_row("2026-08-20 18:00", feed=300)
        second["BLEND_RECON_FE"] = 1.3
        second["REGRESSION_RECON_FE"] = 1.1
        second["PROD_WMT"] = 10  # This must not become the advanced weight.
        samples, _ = convert([first, second])
        for kind, expected in (("blend", 1.25), ("regression", 1.05)):
            group = [sample for sample in samples if sample["kind"] == kind]
            self.assertEqual(len(group), 2)
            weighted = sum(s["factors"]["fe"] * s["feed_wmt"] for s in group) / sum(s["feed_wmt"] for s in group)
            self.assertAlmostEqual(weighted, expected)

    def test_build_turnover_and_later_inbound_cannot_leak_into_history(self):
        samples, _ = convert(lineage=[
            inbound(GB1, 100),
            inbound(GB2, 9999, build=OTHER_BUILD),
            inbound(GB2, 9999, when="2026-08-21 18:00"),
        ])
        self.assertEqual(samples[0]["contributing_blocks"], [{
            "grade_block_key": grade_block_key(GB1),
            "spatial_key": grade_block_key(GB1).split("|"),
            "material_type": "LG", "feed_wmt": 1000.0,
        }])

    def test_unidentified_inbound_keeps_unknown_share_instead_of_renormalizing(self):
        samples, warnings = convert(lineage=[inbound(GB1, 400), inbound("UNIDENTIFIED", 600)])
        sample = samples[0]
        self.assertEqual(sample["contributing_blocks"][0]["feed_wmt"], 400.0)
        self.assertEqual(sample["provenance"]["unattributed_feed_wmt"], 600.0)
        self.assertEqual(sample["provenance"]["lineage_coverage"], 0.4)
        self.assertTrue(warnings)
        self.assertEqual(sample["factors"]["fe"], 1.1)

    def test_missing_lineage_retains_factor_with_zero_coverage(self):
        samples, warnings = convert(lineage=[])
        self.assertEqual(samples[0]["contributing_blocks"], [])
        self.assertEqual(samples[0]["provenance"]["unattributed_feed_wmt"], 1000.0)
        self.assertTrue(any("no positive inbound" in warning for warning in warnings))

    def test_direct_block_feed_and_duplicate_payloads_preserve_parent_identity(self):
        row = history_row(sources=[
            {"source": GB1, "feed_wmt": 200},
            {"source": GB1.replace("_123", "_456"), "feed_wmt": 300},
            {"source": BUILD, "feed_wmt": 500},
        ])
        samples, warnings = convert([row], [inbound(GB2, 100)])
        self.assertFalse(warnings)
        self.assertEqual([b["feed_wmt"] for b in samples[0]["contributing_blocks"]], [500, 500])

    def test_complete_day_and_night_periods_respect_perth_timezone_and_window(self):
        rows = [history_row(start) for start in (
            "2026-08-19 18:00", "2026-08-20 06:00", "2026-08-21 18:00", "2026-08-22 06:00",
        )]
        samples, _ = ReconciliationHistory().build_samples(
            pd.DataFrame(rows), pd.DataFrame(), "2026-08-21T22:00:00Z", "CC OPF01", max_lookback_days=2,
        )
        self.assertEqual({s["period_start"] for s in samples}, {"2026-08-20T06:00:00", "2026-08-21T18:00:00"})

    def test_invalid_factors_stay_missing_instead_of_becoming_one(self):
        row = history_row()
        for suffix, value in (("FE", None), ("SIO2", float("nan")), ("AL2O3", float("inf")), ("P", 0), ("MN", -1)):
            row[f"BLEND_RECON_{suffix}"] = value
        samples, warnings = convert([row])
        self.assertTrue(all(value is None for value in samples[0]["factors"].values()))
        self.assertTrue(warnings)
        json.dumps(samples, allow_nan=False)

    def test_duplicate_periods_fail_instead_of_doubling_weights(self):
        with self.assertRaisesRegex(ValueError, "Duplicate reconciliation period"):
            convert([history_row(), history_row()])

    def test_inconsistent_source_totals_disable_lineage_but_preserve_total_weight(self):
        for sources in ([{"source": BUILD, "feed_wmt": 2000}],
                        [{"source": BUILD, "feed_wmt": float("inf")}],
                        [{"source": BUILD, "feed_wmt": 1100}, {"source": OTHER_BUILD, "feed_wmt": -100}]):
            with self.subTest(sources=sources):
                samples, warnings = convert([history_row(sources=sources)])
                self.assertEqual(samples[0]["feed_wmt"], 1000)
                self.assertEqual(samples[0]["contributing_blocks"], [])
                self.assertTrue(warnings)

    def test_brand_alias_is_explicit_and_never_randomly_substituted(self):
        samples, _ = convert(brands=["FB"])
        self.assertEqual(samples[0]["brand"], "CCFB")
        samples, warnings = convert(brands=["UNAVAILABLE"])
        self.assertEqual(samples, [])
        self.assertTrue(any("standard fallback" in warning for warning in warnings))
        samples, warnings = convert([history_row(), history_row(brand="OTHERFB")], brands=["FB"])
        self.assertFalse(samples)
        self.assertTrue(any("ambiguous" in warning for warning in warnings))

    def test_multiple_brands_share_period_composition_without_duplicate_samples(self):
        samples, _ = convert([history_row(), history_row(brand="CBSF")])
        self.assertEqual(len(samples), 4)
        self.assertEqual(len({s["provenance"]["sample_id"] for s in samples}), 4)
        self.assertTrue(all(s["feed_wmt"] == 1000 for s in samples))

    def test_inputs_remain_unchanged_and_samples_round_trip(self):
        row = history_row()
        row["FEED_SOURCES_JSON"] = json.dumps(row["FEED_SOURCES_JSON"])
        original = copy.deepcopy(row)
        samples, _ = convert([row])
        self.assertEqual(row, original)
        self.assertEqual(json.loads(json.dumps(samples, allow_nan=False)), samples)
        self.assertEqual(pickle.loads(pickle.dumps(samples)), samples)

    def test_bad_schema_or_feed_json_fails_loudly(self):
        row = history_row()
        del row["FEED_WMT"]
        with self.assertRaisesRegex(ValueError, "missing columns"):
            convert([row])
        for value in ("{broken", {}, "{}"):
            row = history_row()
            row["FEED_SOURCES_JSON"] = value
            with self.assertRaises(ValueError):
                convert([row])


class InventoryLineageTests(unittest.TestCase):
    def test_opening_balance_scales_only_known_lineage(self):
        lineage = InventoryBuildLineage(pd.DataFrame([inbound(GB1, 400), inbound("UNKNOWN", 600)]))
        record = lineage.opening_records([BUILD], AS_OF)[0]
        self.assertEqual(record["inventory_wmt"], 500)
        self.assertEqual(record["attributed_wmt"], 200)
        self.assertEqual(record["unattributed_wmt"], 300)
        self.assertEqual(record["contributing_blocks"][0]["feed_wmt"], 200)

    def test_aliases_and_signed_corrections_are_combined_before_weighting(self):
        alias = "CC_PIT01_01_0453_426_0456_LG01"
        lineage = InventoryBuildLineage(pd.DataFrame([inbound(GB1, 500), inbound(alias, -100), inbound(GB2, 600)]))
        fractions, warnings = lineage.composition(BUILD, AS_OF)
        self.assertEqual(fractions[grade_block_key(GB1)], 0.4)
        self.assertFalse(warnings)

    def test_negative_net_lineage_cannot_claim_full_coverage(self):
        samples, warnings = convert(lineage=[inbound(GB1, -100), inbound(GB2, 600)])
        self.assertEqual(samples[0]["contributing_blocks"], [])
        self.assertTrue(any("negative net lineage" in warning for warning in warnings))

    def test_missing_and_nonpositive_opening_balances_are_not_inflated(self):
        for balance in (None, 0, -20):
            with self.subTest(balance=balance):
                lineage = InventoryBuildLineage(pd.DataFrame([inbound(GB1, 400, balance=balance)]))
                record = lineage.opening_records([BUILD], AS_OF)[0]
                self.assertEqual(record["attributed_wmt"], 0)
                self.assertEqual(record["unattributed_wmt"], 0)

    def test_stockpile_and_malformed_names_are_not_grade_blocks(self):
        for name in (BUILD, "UNKNOWN", "A/B/C/D/E/26001", None, float("nan")):
            self.assertEqual(canonical_block(name), "")
        self.assertEqual(canonical_block(GB1), grade_block_key(GB1))
        self.assertEqual(canonical_block(grade_block_key(GB1)), grade_block_key(GB1))


class HistoryFetchTests(unittest.TestCase):
    def setUp(self):
        self.loader = MagicMock()
        self.connection = self.loader.connect_snowflake_with_service_account.return_value
        self.service = ReconciliationHistory(self.loader)

    def test_fetch_uses_bound_dates_and_build_names_and_closes_resources(self):
        query_results = [pd.DataFrame([history_row()]), pd.DataFrame([inbound(GB1, 100)])]
        with patch.object(self.service, "_query", side_effect=query_results) as query:
            samples, _ = self.service.fetch(AS_OF, "CC OPF01", ["FB"], max_lookback_days=2)
        self.assertEqual(len(samples), 2)
        self.assertEqual(query.call_args_list[0].args[2], ("2026-08-20 06:00:00", "2026-08-22 06:00:00", "CC_OPF01"))
        self.assertEqual(json.loads(query.call_args_list[1].args[2][1]), [BUILD])
        self.connection.close.assert_called_once()
        self.connection.cursor.return_value.close.assert_called_once()

    def test_sql_failure_closes_both_cursor_and_connection(self):
        cursor = self.connection.cursor.return_value
        cursor.execute.side_effect = [None, RuntimeError("query failure")]
        with self.assertRaisesRegex(RuntimeError, "query failure"):
            self.service.fetch(AS_OF, "CC OPF01")
        self.assertEqual(cursor.close.call_count, 2)
        self.connection.close.assert_called_once()

    def test_unavailable_connection_and_invalid_bounds_are_explicit(self):
        self.loader.connect_snowflake_with_service_account.return_value = None
        with self.assertRaises(ConnectionError):
            self.service.fetch(AS_OF, "CC OPF01")
        for days in (0, -1, 1.5, True, float("inf"), "invalid"):
            with self.subTest(days=days), self.assertRaises(ValueError):
                self.service.fetch(AS_OF, "CC OPF01", max_lookback_days=days)

    def test_lineage_batches_exact_builds_without_querying_per_block(self):
        builds = [f"ROM_260{index:03}" for index in range(205)]
        with patch.object(self.service, "_query", return_value=pd.DataFrame()) as query:
            self.service._fetch_lineage(self.connection, builds, AS_OF)
        self.assertEqual(query.call_count, 3)
        self.assertEqual([len(json.loads(call.args[2][1])) for call in query.call_args_list], [100, 100, 5])

    def test_empty_inventory_selection_does_not_connect(self):
        self.assertEqual(self.service.fetch_inventory_lineage(AS_OF, []), ([], []))
        self.loader.connect_snowflake_with_service_account.assert_not_called()

    def test_standard_entry_point_only_delegates_when_explicitly_requested(self):
        with patch.object(ReconciliationHistory, "fetch", return_value=([], [])) as fetch:
            DataStreamReconciliation(self.loader).fetch_history(AS_OF, "CC OPF01", ["FB"], max_lookback_days=10)
        fetch.assert_called_once_with(AS_OF, "CC OPF01", ["FB"], max_lookback_days=10)

    def test_both_sql_queries_support_snowflake_pyformat_binding(self):
        shift_sql = self.service.SQL_PATH.read_text(encoding="utf-8")
        rendered = shift_sql % ("start", "end", "opf")
        self.assertIn("LIKE 'OP1_%'", rendered)
        lineage_sql = self.service.LINEAGE_SQL_PATH.read_text(encoding="utf-8")
        self.assertIn("PARSE_JSON(builds)", lineage_sql % ("asof", "builds"))


if __name__ == "__main__":
    unittest.main()
