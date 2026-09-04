"""Tests for the versioned Phase 0 configuration and audit schemas."""

import json
import os
import pickle
import sys
import unittest
from datetime import datetime, timezone


PACKAGE_ROOT = os.path.dirname(os.path.dirname(__file__))
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

from classes.PhaseSchemas import (  # noqa: E402
    AMT_OUTCOME_EXCLUDED,
    AMT_OUTCOME_RETAINED_INVENTORY_UNAVAILABLE,
    AMT_OUTCOME_ZEROED_NON_POSITIVE_RAW,
    FACTOR_LEVEL_GLOBAL,
    FACTOR_LEVEL_PIT_STAGE_BENCH_MATERIAL,
    FACTOR_METHOD_SPATIAL_COMPOSITIONAL,
    QUALITY_EVALUATION_CUMULATIVE_BUILD,
    QUALITY_LIMIT_MODE_SOFT,
    SCHEMA_ANALYTES,
    TARGET_MODE_SOFT,
    TOPOLOGY_EDGE_CONVEYOR,
    TOPOLOGY_MODE_ONE_LANE,
    TOPOLOGY_NODE_OPF,
    TOPOLOGY_NODE_SOURCE,
    TOPOLOGY_NODE_TIPPING_POINT,
    amt_footprint_audit,
    analyte_quality_limit,
    contributing_block,
    destination_build,
    destination_progress,
    is_readable,
    material_flow_topology,
    product_quality_limits,
    reconciliation_sample,
    resolved_factor,
    topology_edge,
    topology_node,
)


class SchemaVersionTests(unittest.TestCase):
    def test_unversioned_and_current_records_are_readable(self):
        self.assertTrue(is_readable({}, 1))
        self.assertTrue(is_readable({"schema_version": "1"}, 1))

    def test_future_and_malformed_records_are_not_readable(self):
        self.assertFalse(is_readable({"schema_version": 2}, 1))
        self.assertFalse(is_readable({"schema_version": 0}, 1))
        self.assertFalse(is_readable({"schema_version": "future"}, 1))
        self.assertFalse(is_readable(None, 1))


class ReconciliationSchemaTests(unittest.TestCase):
    def test_sample_retains_factors_and_contributing_block_keys(self):
        block = contributing_block(
            "BUL01|1|396|118|405|LG12",
            material_type="lg",
            feed_wmt=0,
        )
        sample = reconciliation_sample(
            "CB_OPF",
            "ccfb",
            "BLEND",
            datetime(2026, 9, 1, tzinfo=timezone.utc),
            datetime(2026, 9, 2, tzinfo=timezone.utc),
            factors={"FE": 1.02},
            feed_wmt=1000,
            source_rows=4,
            contributing_blocks=[block],
        )

        self.assertEqual(sample["brand"], "CCFB")
        self.assertEqual(sample["kind"], "blend")
        self.assertEqual(sample["period_start"], "2026-09-01T00:00:00+00:00")
        self.assertEqual(sample["factors"]["fe"], 1.02)
        self.assertIsNone(sample["factors"]["si"])
        self.assertEqual(sample["source_rows"], 4)
        self.assertEqual(block["feed_wmt"], 0.0)
        self.assertEqual(
            block["spatial_key"],
            ["BUL01", "1", "396", "118", "405", "LG12"],
        )

    def test_resolved_factor_shares_one_level_across_factor_maps(self):
        record = resolved_factor(
            "ROM_SP01",
            "inventory",
            "BUL01|1|396|118|405|LG12",
            "CB_OPF",
            "ccfb",
            hex_id="HEX-7",
            lineage_fraction=0.4,
            method=FACTOR_METHOD_SPATIAL_COMPOSITIONAL,
            resolution_level=FACTOR_LEVEL_PIT_STAGE_BENCH_MATERIAL,
            matched_spatial_key=["BUL01", 1, 396, "LG"],
            blend_factors={"fe": 1.02, "p": 0},
            regression_factors={"fe": 0.98},
            source_history=[{"period_start": "2026-08-01", "feed_wmt": 500}],
            source_rows=12,
            source_feed_wmt=500,
            lookback_days=30,
            fallback_reason="flitch not represented",
            confidence_percent=82.5,
            uncertainty_percent=17.5,
        )

        self.assertEqual(record["resolution_level"], FACTOR_LEVEL_PIT_STAGE_BENCH_MATERIAL)
        self.assertEqual(record["blend_factors"]["p"], 0.0)
        self.assertEqual(record["regression_factors"]["fe"], 0.98)
        self.assertEqual(record["source_rows"], 12)
        self.assertEqual(record["source_history"][0]["feed_wmt"], 500)
        self.assertFalse(record["manual_override"])

    def test_global_fallback_is_explicit_not_one(self):
        record = resolved_factor(
            "source",
            "inventory",
            "",
            "OPF1",
            "WPF",
            resolution_level=FACTOR_LEVEL_GLOBAL,
            blend_factors={"fe": 1.07},
            fallback_reason="no spatial sample",
        )
        self.assertEqual(record["resolution_level"], "global")
        self.assertEqual(record["blend_factors"]["fe"], 1.07)
        self.assertEqual(record["fallback_reason"], "no spatial sample")


class ProductQualitySchemaTests(unittest.TestCase):
    def test_hard_defaults_keep_all_bounds_open(self):
        record = product_quality_limits("OPF1", "wpf")

        self.assertEqual(record["target_mode"], "hard")
        self.assertEqual(tuple(record["limits"]), SCHEMA_ANALYTES)
        for limits in record["limits"].values():
            self.assertIsNone(limits["minimum"])
            self.assertIsNone(limits["maximum"])
            self.assertIsNone(limits["target"])

    def test_soft_mode_preserves_zero_and_three_decimal_targets(self):
        fe = analyte_quality_limit(
            minimum=0,
            target=57.123456,
            lql=56.5,
            hql=58.0,
            limit_mode=QUALITY_LIMIT_MODE_SOFT,
            target_source="2WP",
            target_penalty_weight=2,
            limit_penalty_weight=10,
        )
        record = product_quality_limits(
            "OPF1",
            "wpf",
            "fines",
            target_mode=TARGET_MODE_SOFT,
            evaluation_basis=QUALITY_EVALUATION_CUMULATIVE_BUILD,
            limits={"FE": fe},
        )

        self.assertEqual(record["lane"], "fines")
        self.assertEqual(record["limits"]["fe"]["minimum"], 0.0)
        self.assertEqual(record["limits"]["fe"]["target"], 57.123456)
        self.assertEqual(record["limits"]["fe"]["limit_mode"], "soft")
        self.assertEqual(record["limits"]["fe"]["target_source"], "2wp")


class AMTFootprintSchemaTests(unittest.TestCase):
    def test_exclusion_retains_who_when_and_raw_tonnes(self):
        record = amt_footprint_audit(
            "FP-01",
            excluded=True,
            exclusion_reason="survey issue",
            excluded_by="operator",
            excluded_at=datetime(2026, 9, 4, 8, 30),
            outcome=AMT_OUTCOME_EXCLUDED,
            raw_wmt=240,
            final_wmt=0,
            source_rows=8,
        )

        self.assertEqual(record["raw_wmt"], 240.0)
        self.assertEqual(record["final_wmt"], 0.0)
        self.assertEqual(record["excluded_at"], "2026-09-04T08:30:00")
        self.assertFalse(record["eligible_for_processing"])

    def test_non_positive_raw_outcome_cannot_reenter_processing(self):
        record = amt_footprint_audit(
            "FP-02",
            outcome=AMT_OUTCOME_ZEROED_NON_POSITIVE_RAW,
            raw_wmt=-70,
            inventory_wmt=200,
            final_wmt=0,
            outcome_reason="raw footprint total <= 0",
        )
        self.assertFalse(record["eligible_for_processing"])
        self.assertEqual(record["inventory_wmt"], 200.0)

    def test_missing_inventory_outcome_can_retain_positive_amt_tonnes(self):
        record = amt_footprint_audit(
            "FP-03",
            outcome=AMT_OUTCOME_RETAINED_INVENTORY_UNAVAILABLE,
            raw_wmt=100,
            spatially_reconciled_wmt=95,
            final_wmt=95,
        )
        self.assertTrue(record["eligible_for_processing"])


class TopologySchemaTests(unittest.TestCase):
    def test_one_lane_topology_uses_common_nodes_and_directed_edges(self):
        nodes = [
            topology_node("source:rom1", TOPOLOGY_NODE_SOURCE, label="ROM 1"),
            topology_node("tip:cr1", TOPOLOGY_NODE_TIPPING_POINT),
            topology_node("opf:1", TOPOLOGY_NODE_OPF),
        ]
        edges = [
            topology_edge("e1", "source:rom1", "tip:cr1", max_rate_wmtph=1200),
            topology_edge(
                "e2",
                "tip:cr1",
                "opf:1",
                edge_type=TOPOLOGY_EDGE_CONVEYOR,
                capacity_wmt=300,
                latency_hours=0.25,
            ),
        ]
        record = material_flow_topology(
            "site:cb",
            mode=TOPOLOGY_MODE_ONE_LANE,
            nodes=nodes,
            edges=edges,
        )

        self.assertEqual(record["mode"], "one_lane")
        self.assertEqual(record["edges"][1]["source_node_id"], "tip:cr1")
        self.assertEqual(record["edges"][1]["target_node_id"], "opf:1")
        self.assertEqual(record["edges"][1]["capacity_wmt"], 300.0)
        self.assertNotIn("position", record["nodes"][0])


class DestinationProgressSchemaTests(unittest.TestCase):
    def test_repeated_destinations_remain_distinct_build_instances(self):
        order = [
            destination_build("SP1", 0, build_instance=1, planned_wmt=1000),
            destination_build("SP2", 1, build_instance=1, planned_wmt=800),
            destination_build("SP1", 2, build_instance=2, planned_wmt=900),
        ]
        self.assertNotEqual(order[0]["build_id"], order[2]["build_id"])

    def test_progress_keeps_detected_activity_and_user_capacity_separate(self):
        order = [
            destination_build("SP1", 0),
            destination_build("SP2", 1),
            destination_build("SP3", 2),
        ]
        record = destination_progress(
            "ROM-A",
            "lg",
            build_order=order,
            active_build_index=1,
            detected_active_destination="SP2",
            user_entered_remaining_capacity_wmt=0,
            activity_evidence=[{"destination": "SP2", "last_inbound": "2026-09-03"}],
            ambiguity_warnings=["two movements share the latest timestamp"],
        )

        self.assertEqual(record["previous_destination"], "SP1")
        self.assertEqual(record["active_destination"], "SP2")
        self.assertEqual(record["next_destination"], "SP3")
        self.assertEqual(record["detected_active_destination"], "SP2")
        self.assertEqual(record["user_entered_remaining_capacity_wmt"], 0.0)
        self.assertEqual(record["remaining_capacity_wmt"], 0.0)


class SerializationTests(unittest.TestCase):
    def test_every_top_level_schema_is_json_and_pickle_serializable(self):
        records = [
            reconciliation_sample("OPF", "BRAND", "blend", "a", "b"),
            resolved_factor("s", "inventory", "key", "OPF", "BRAND"),
            product_quality_limits("OPF", "BRAND"),
            amt_footprint_audit("FP"),
            material_flow_topology("topology"),
            destination_progress("ROM", "LG"),
        ]
        for record in records:
            with self.subTest(schema=record["schema_version"]):
                self.assertEqual(json.loads(json.dumps(record)), record)
                self.assertEqual(pickle.loads(pickle.dumps(record)), record)


if __name__ == "__main__":
    unittest.main()
