"""Phase 0 characterisation fixtures.

Task 2 of the BlendMaster major implementation plan. These tests pin current
behavior so Phase 1 to 5 changes are provably intentional. They are
characterisation tests: they assert what the code does today, including two
known defects that later tasks will correct.

Where an assertion documents behavior that must change, it is marked with a
``CHANGES IN`` note naming the task that will update it. Those assertions are
expected to be edited by that task, not deleted.

Contracts: docs/AUTHORITATIVE_DATA_AND_BEHAVIOR_CONTRACTS.md
"""

import json
import io
import os
import pathlib
import sys
import unittest
from datetime import datetime

import pandas as pd


PACKAGE_ROOT = os.path.dirname(os.path.dirname(__file__))
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

from classes.ExpitDataHandler import ExpitDataHandler  # noqa: E402
from classes.MaterialDestinationPlan import MaterialDestinationPlan  # noqa: E402
from classes.ProductBuildLanes import (  # noqa: E402
    ANALYTES as LANE_ANALYTES,
    BYPRODUCT_LANES,
    PRODUCT_LANE,
    lane_actual_tonnes_column,
    lane_grade_column,
)
from classes.ExpitSequenceReconciler import (  # noqa: E402
    grade_block_key,
    is_route_only_waste,
    polygon_lookup_name,
)
from classes.GradeBlockIdentity import (  # noqa: E402
    grade_block_material_type,
    parent_grade_block_name,
)
from classes.GradeStreams import ANALYTES, DEFAULT_STREAM, STREAMS  # noqa: E402
from classes.ProductTargetModes import migrate_target_row
from setup.AMTGradeBlockLineage import align_amt_grade_block_lineage  # noqa: E402
from setup.AMTSpatialReconciliation import reconcile_amt_hex_rows  # noqa: E402
from setup.DataStreamReconciliation import DataStreamReconciliation  # noqa: E402
from setup.PlanningPlanTargets import PlanningPlanTargets  # noqa: E402


# Canonical eight-token name from the clarification answers. The leading
# Reserves and mine tokens are model constants and are intentionally dropped.
CANONICAL_BLOCK = "Reserves/CC1/BUL01/01/396/118/405/LG12_313"


class GradeBlockIdentityCharacterisation(unittest.TestCase):
    """Section 4 of the contracts document."""

    def test_canonical_name_reduces_to_six_operational_tokens(self):
        self.assertEqual(
            grade_block_key(CANONICAL_BLOCK),
            "BUL01|1|396|118|405|LG12",
        )

    def test_business_levels_map_positionally_onto_parser_tokens(self):
        pit, stage, bench, blast, flitch, material_slice = (
            grade_block_key(CANONICAL_BLOCK).split("|")
        )
        self.assertEqual(pit, "BUL01")
        self.assertEqual(stage, "1")
        self.assertEqual(bench, "396")
        self.assertEqual(blast, "118")
        self.assertEqual(flitch, "405")
        self.assertEqual(material_slice, "LG12")

    def test_path_and_underscore_representations_converge(self):
        self.assertEqual(
            grade_block_key(CANONICAL_BLOCK),
            grade_block_key("BUL01_01_0396_118_0405_LG12"),
        )

    def test_polygon_name_restores_snowflake_zero_padding(self):
        self.assertEqual(
            polygon_lookup_name(CANONICAL_BLOCK),
            "BUL01_01_0396_118_0405_LG12",
        )

    def test_numeric_tokens_normalise_across_leading_zeroes(self):
        self.assertEqual(
            grade_block_key("BUL01/1/396/118/405/LG12"),
            grade_block_key("BUL01/01/0396/118/0405/LG12"),
        )

    def test_reserves_prefix_is_dropped_case_insensitively(self):
        self.assertEqual(
            grade_block_key("RESERVES/CC1/BUL01/01/396/118/405/LG12"),
            grade_block_key("Reserves/CC1/BUL01/01/396/118/405/LG12"),
        )

    def test_parent_name_strips_only_the_aps_slice_suffix(self):
        self.assertEqual(
            parent_grade_block_name(CANONICAL_BLOCK),
            "Reserves/CC1/BUL01/01/396/118/405/LG12",
        )

    def test_material_type_is_the_leading_letters_of_the_slice(self):
        self.assertEqual(grade_block_material_type(CANONICAL_BLOCK), "LG")
        self.assertEqual(grade_block_material_type("BA72_63"), "BA")

    def test_waste_routes_are_identified_for_exclusion(self):
        self.assertTrue(is_route_only_waste("WASTE"))
        self.assertTrue(is_route_only_waste("mine waste"))
        self.assertFalse(is_route_only_waste("LG12"))

    def test_empty_and_malformed_names_degrade_without_raising(self):
        self.assertEqual(grade_block_key(""), "")
        self.assertEqual(grade_block_key(None), "")
        self.assertEqual(polygon_lookup_name("TOO/FEW/TOKENS"), "")


def amt_row(hex_id, raw_wmt, easting, inventory_balance, unattributed=0):
    """One AMT hexagon row on a shared east-west line."""
    return {
        "FOOTPRINT": "TEST_FP",
        "HEX": hex_id,
        "RAW_WMT": raw_wmt,
        "SOURCEHEXEASTING": easting,
        "SOURCEHEXNORTHING": 0,
        "INVENTORY_BALANCE_WMT": inventory_balance,
        "UNATTRIBUTED_MOVEMENT_WMT": unattributed,
    }


class AMTFootprintBalanceCharacterisation(unittest.TestCase):
    """Section 13 of the contracts document.

    Covers positive, zero and negative footprint balances against positive,
    zero, and unavailable inventory.
    """

    @staticmethod
    def totals(rows):
        corrected = reconcile_amt_hex_rows(rows)
        return corrected, sum(row["FINAL_WMT"] for row in corrected)

    def test_positive_footprint_scales_to_positive_inventory(self):
        corrected, total = self.totals([
            amt_row("h1", 100, 0, 120),
            amt_row("h2", 50, 1, 120),
        ])
        self.assertAlmostEqual(total, 120.0)
        self.assertAlmostEqual(corrected[0]["FINAL_WMT"], 80.0)
        self.assertAlmostEqual(corrected[1]["FINAL_WMT"], 40.0)
        self.assertEqual(
            corrected[0]["SPATIAL_RECON_STATUS"],
            "OK_SPATIAL_AND_INVENTORY_RECONCILED",
        )

    def test_positive_footprint_with_zero_inventory_zeroes_every_hex(self):
        corrected, total = self.totals([
            amt_row("h1", 100, 0, 0),
            amt_row("h2", 50, 1, 0),
        ])
        self.assertAlmostEqual(total, 0.0)
        self.assertTrue(all(row["FINAL_WMT"] == 0.0 for row in corrected))

    def test_unavailable_inventory_retains_spatially_reconciled_tonnes(self):
        corrected, total = self.totals([
            amt_row("h1", 100, 0, None),
            amt_row("h2", 50, 1, None),
        ])
        self.assertAlmostEqual(total, 150.0)
        self.assertEqual(
            corrected[0]["SPATIAL_RECON_STATUS"],
            "WARN_INVENTORY_BALANCE_UNAVAILABLE_SPATIAL_ONLY",
        )

    def test_raw_audit_tonnes_survive_reconciliation(self):
        corrected, _ = self.totals([
            amt_row("h1", 100, 0, 120),
            amt_row("h2", 50, 1, 120),
        ])
        self.assertAlmostEqual(corrected[0]["RAW_WMT"], 100.0)
        self.assertAlmostEqual(corrected[1]["RAW_WMT"], 50.0)
        self.assertAlmostEqual(corrected[0]["RAW_STOCKPILE_WMT"], 150.0)

    def test_no_negative_final_balance_reaches_chunking(self):
        corrected, _ = self.totals([
            amt_row("negative", -50, 0, 200),
            amt_row("donor", 300, 1, 200),
        ])
        self.assertTrue(all(row["FINAL_WMT"] >= 0.0 for row in corrected))

    def test_negative_footprint_cannot_receive_inventory_tonnes(self):
        """Task 9 fixes the former allocation of 200 t onto a -70 t footprint."""
        corrected, total = self.totals([
            amt_row("h1", -50, 0, 200),
            amt_row("h2", -20, 1, 200),
        ])
        self.assertAlmostEqual(corrected[0]["RAW_STOCKPILE_WMT"], -70.0)
        self.assertAlmostEqual(total, 0.0)
        self.assertTrue(all(row["FINAL_WMT"] == 0.0 for row in corrected))

    def test_zero_footprint_cannot_receive_inventory_tonnes(self):
        """A zero raw footprint must not absorb positive inventory (Q46/Q47)."""
        corrected, total = self.totals([
            amt_row("h1", 0, 0, 200),
            amt_row("h2", 0, 1, 200),
        ])
        self.assertAlmostEqual(corrected[0]["RAW_STOCKPILE_WMT"], 0.0)
        self.assertAlmostEqual(total, 0.0)
        self.assertTrue(all(row["FINAL_WMT"] == 0.0 for row in corrected))


def lineage_hex(final_wmt, components):
    """One AMT hex row carrying encoded grade-block lineage."""
    return {
        "FOOTPRINT": "TEST_FP",
        "HEX": "hex1",
        "FINAL_WMT": final_wmt,
        "GRADE_BLOCK_LINEAGE_JSON": json.dumps(components),
    }


def lineage_component(grade_block, inbound_wmt, fe=None, matched=True):
    component = {
        "grade_block": grade_block,
        "inbound_wmt": inbound_wmt,
        "match_method": (
            "EXPIT_GRADEBLOCK_ID_MATCHED" if matched else "UNMATCHED"
        ),
    }
    if fe is not None:
        component["properties"] = {"grade_block_fe": fe}
    return component


class AMTHexLineageCharacterisation(unittest.TestCase):
    """Section 5.3.1 of the contracts document.

    Q39 requires per-lineage resolution then tonne-weighting to the hex. The
    existing aligner already produces that weighting, which is the seam Task 7
    will apply spatial factors through.
    """

    @staticmethod
    def aligned(row):
        result = align_amt_grade_block_lineage([row])[0]
        lineage = result["GRADE_BLOCK_LINEAGE_JSON"]
        if isinstance(lineage, str):
            lineage = json.loads(lineage)
        return result, lineage

    def test_multiple_grade_blocks_tonne_weight_to_the_hex(self):
        result, _ = self.aligned(lineage_hex(100.0, [
            lineage_component("GB1", 40.0, fe=60.0),
            lineage_component("GB2", 60.0, fe=50.0),
        ]))
        # 0.4 * 60 + 0.6 * 50
        self.assertAlmostEqual(result["MODELLED_GRADE_BLOCK_FE"], 54.0)
        self.assertAlmostEqual(
            result["MODELLED_GRADE_BLOCK_FE_COVERAGE_PCT"], 100.0
        )

    def test_inbound_shares_and_remaining_tonnes_follow_final_balance(self):
        _, lineage = self.aligned(lineage_hex(100.0, [
            lineage_component("GB1", 40.0, fe=60.0),
            lineage_component("GB2", 60.0, fe=50.0),
        ]))
        self.assertAlmostEqual(lineage[0]["inbound_share"], 0.4)
        self.assertAlmostEqual(lineage[1]["inbound_share"], 0.6)
        self.assertAlmostEqual(lineage[0]["remaining_wmt"], 40.0)
        self.assertAlmostEqual(lineage[1]["remaining_wmt"], 60.0)

    def test_shares_are_preserved_when_the_hex_is_scaled_down(self):
        _, lineage = self.aligned(lineage_hex(50.0, [
            lineage_component("GB1", 40.0, fe=60.0),
            lineage_component("GB2", 60.0, fe=50.0),
        ]))
        self.assertAlmostEqual(lineage[0]["remaining_wmt"], 20.0)
        self.assertAlmostEqual(lineage[1]["remaining_wmt"], 30.0)

    def test_partial_coverage_renormalises_onto_the_covered_subset(self):
        """Uncovered tonnes do not dilute the grade toward zero."""
        result, _ = self.aligned(lineage_hex(100.0, [
            lineage_component("GB1", 40.0, fe=60.0),
            lineage_component(None, 60.0, matched=False),
        ]))
        self.assertAlmostEqual(result["MODELLED_GRADE_BLOCK_FE"], 60.0)
        self.assertAlmostEqual(
            result["MODELLED_GRADE_BLOCK_FE_COVERAGE_PCT"], 40.0
        )
        self.assertAlmostEqual(result["LINEAGE_COVERAGE_PCT"], 40.0)

    def test_absent_lineage_yields_null_grade_not_zero(self):
        """Sources without lineage must fall back to global factors (Q32)."""
        result, _ = self.aligned(lineage_hex(100.0, []))
        self.assertIsNone(result["MODELLED_GRADE_BLOCK_FE"])
        self.assertIsNone(result["LINEAGE_COVERAGE_PCT"])

    def test_zero_final_balance_leaves_no_remaining_lineage(self):
        _, lineage = self.aligned(lineage_hex(0.0, [
            lineage_component("GB1", 40.0, fe=60.0),
        ]))
        self.assertAlmostEqual(lineage[0]["remaining_wmt"], 0.0)


def target_build(build_id, brand, tonnes, fe_min, start, byproduct=""):
    """One 2WP-imported product build row."""
    build = {
        "build_id": build_id,
        "brand": brand,
        "byproduct": byproduct,
        "target_tonnes": tonnes,
        "planning_target_tonnes": tonnes,
        "planning_period_start": start,
        "planning_period_end": start,
    }
    for grade in ANALYTES:
        build[f"target_{grade}_min"] = None
        build[f"target_{grade}_max"] = None
    build["target_fe_min"] = fe_min
    return build


class ProductTargetImportCharacterisation(unittest.TestCase):
    """Section 8 of the contracts document."""

    def test_consecutive_same_brand_rows_group_and_tonne_weight(self):
        grouped = PlanningPlanTargets.group_builds_by_brand([
            target_build(1, "CCFB", 1000, 58.123, datetime(2026, 9, 1)),
            target_build(2, "CCFB", 2000, 58.456, datetime(2026, 9, 2)),
            target_build(3, "CCSF", 500, 60.0, datetime(2026, 9, 3)),
        ])
        self.assertEqual(len(grouped), 2)
        self.assertAlmostEqual(grouped[0]["target_tonnes"], 3000.0)
        # (1000 * 58.123 + 2000 * 58.456) / 3000
        self.assertAlmostEqual(grouped[0]["target_fe_min"], 58.345)
        self.assertAlmostEqual(grouped[1]["target_tonnes"], 500.0)

    def test_grouping_retains_full_precision_not_three_decimals(self):
        """Q51: three decimals are display only; maths keeps full precision."""
        grouped = PlanningPlanTargets.group_builds_by_brand([
            target_build(1, "CCFB", 1000, 58.1, datetime(2026, 9, 1)),
            target_build(2, "CCFB", 2000, 58.2, datetime(2026, 9, 2)),
        ])
        weighted = grouped[0]["target_fe_min"]
        self.assertNotEqual(weighted, round(weighted, 3))
        self.assertEqual(format(weighted, ".3f"), "58.167")

    def test_non_consecutive_brands_do_not_merge(self):
        grouped = PlanningPlanTargets.group_builds_by_brand([
            target_build(1, "CCFB", 1000, 58.0, datetime(2026, 9, 1)),
            target_build(2, "CCSF", 1000, 60.0, datetime(2026, 9, 2)),
            target_build(3, "CCFB", 1000, 58.0, datetime(2026, 9, 3)),
        ])
        self.assertEqual(
            [group["brand"] for group in grouped], ["CCFB", "CCSF", "CCFB"]
        )

    def test_opposite_bounds_are_emitted_as_falsy_zero_not_none(self):
        """Q56: 2WP gives an Fe floor only, so the max must stay open.

        The grouper emits 0.0 rather than None for an unset bound. That value
        is falsy and CaseModeller coerces it with ``or 100`` when building
        solver settings, so the bound reaches the solver open. Task 12 must
        preserve that coercion when adding LQL/HQL, because a literal 0.0 max
        arriving at the solver would make every build infeasible.
        """
        grouped = PlanningPlanTargets.group_builds_by_brand([
            target_build(1, "CCFB", 1000, 58.0, datetime(2026, 9, 1)),
        ])
        self.assertAlmostEqual(grouped[0]["target_fe_min"], 58.0)
        self.assertEqual(grouped[0]["target_fe_max"], 0.0)
        self.assertFalse(grouped[0]["target_fe_max"])
        self.assertEqual(float(grouped[0]["target_fe_max"] or 100), 100.0)

    def test_lump_and_fines_group_into_independent_lanes(self):
        grouped = PlanningPlanTargets.group_builds_by_brand([
            target_build(1, "CBFB", 1000, 58.0, datetime(2026, 9, 1), "fines"),
            target_build(2, "CBFB", 400, 62.0, datetime(2026, 9, 1), "lump"),
            target_build(3, "CBFB", 1000, 58.0, datetime(2026, 9, 2), "fines"),
        ])
        lanes = {}
        for build in grouped:
            lanes.setdefault(build["byproduct"], 0.0)
            lanes[build["byproduct"]] += build["target_tonnes"]
        self.assertAlmostEqual(lanes["fines"], 2000.0)
        self.assertAlmostEqual(lanes["lump"], 400.0)

    def test_quality_fields_are_open_for_legacy_rows_without_explicit_specifications(self):
        """Task 12 adds optional fields without inferring targets from hard bounds."""
        grouped = PlanningPlanTargets.group_builds_by_brand([
            target_build(1, "CCFB", 1000, 58.0, datetime(2026, 9, 1)),
        ])
        for optional in ("target_fe_lql", "target_fe_hql", "target_fe_target"):
            self.assertIn(optional, grouped[0])
            self.assertIsNone(grouped[0][optional])


class GlobalReconciliationCharacterisation(unittest.TestCase):
    """Section 5 of the contracts document.

    Standard global reconciliation is the terminal fallback for the advanced
    spatial resolution added in Tasks 5 to 7, so its current behavior must
    stay reachable and unchanged.
    """

    @staticmethod
    def daily_frame(opf="CB_OPF", brand="CCFB"):
        rows = []
        for shift_date, feed, product, blend, regression in (
            ("2026-07-30", 100.0, 40.0, 1.1, 0.9),
            ("2026-07-29", 300.0, 60.0, 1.3, 1.1),
        ):
            row = {
                "DERIVED_OPERATION": opf,
                "BRAND": brand,
                "SHIFT_DATE": pd.Timestamp(shift_date),
                "FEED_WMT": feed,
                "PROD_WMT": product,
            }
            for suffix in ("FE", "SIO2", "AL2O3", "P", "MN"):
                row[f"BLEND_RECON_{suffix}"] = blend
                row[f"REGRESSION_RECON_{suffix}"] = regression
            rows.append(row)
        return pd.DataFrame(rows)

    def test_lookback_ladder_is_the_documented_sequence(self):
        self.assertEqual(
            DataStreamReconciliation.LOOKBACK_DAYS, (7, 14, 21, 28, 30)
        )
        self.assertEqual(
            DataStreamReconciliation.CB_CAMPAIGN_LOOKBACK_DAYS,
            (7, 14, 21, 28, 30, 60),
        )

    def test_shortest_valid_window_wins_and_weights_by_feed_tonnes(self):
        factors, warnings = DataStreamReconciliation().aggregate(
            self.daily_frame(), "CB OPF", ["CCFB"], datetime(2026, 8, 1, 12)
        )
        record = factors["CCFB"]
        # (100 * 1.1 + 300 * 1.3) / 400
        self.assertAlmostEqual(record["blend"]["fe"]["calculated"], 1.25)
        self.assertAlmostEqual(record["regression"]["fe"]["calculated"], 1.02)
        self.assertEqual(
            record["lookback_days"]["fe"], {"blend": 7, "regression": 7}
        )
        self.assertEqual(warnings, [])

    def test_factors_resolve_per_analyte_within_one_brand(self):
        factors, _ = DataStreamReconciliation().aggregate(
            self.daily_frame(), "CB OPF", ["CCFB"], datetime(2026, 8, 1, 12)
        )
        record = factors["CCFB"]
        for analyte in ANALYTES:
            self.assertIn(analyte, record["blend"])
            self.assertIn(analyte, record["regression"])

    def test_factor_grain_carries_no_spatial_component_yet(self):
        """CHANGES IN Task 6.

        Today a factor is keyed only by OPF, brand and analyte. Task 6 adds
        the pit/stage/bench/blast/flitch/material resolution layer above this.
        """
        factors, _ = DataStreamReconciliation().aggregate(
            self.daily_frame(), "CB OPF", ["CCFB"], datetime(2026, 8, 1, 12)
        )
        record = factors["CCFB"]["blend"]["fe"]
        for absent in ("grade_block", "fallback_level", "confidence"):
            self.assertNotIn(absent, record)


class GradeStreamCharacterisation(unittest.TestCase):
    """Section 6 of the contracts document."""

    def test_five_analytes_are_fixed(self):
        self.assertEqual(ANALYTES, ("fe", "si", "al", "p", "mn"))

    def test_stream_families_and_default_are_stable(self):
        self.assertEqual(STREAMS, (
            "insitu",
            "modelled_rom",
            "adjusted_rom",
            "modelled_product",
            "adjusted_product",
        ))
        self.assertEqual(DEFAULT_STREAM, "adjusted_product")


class TwoWPColumnContractCharacterisation(unittest.TestCase):
    """Section 2 of the contracts document.

    Task 20 extends destination guidance to filter on Destination.Type and to
    order destinations per expit material type. These assertions pin the
    columns that extension must keep reading.
    """

    def test_every_contracted_column_is_already_requested(self):
        for column in (
            "Source.Type",
            "Source.FullName",
            "Source.Pit",
            "MutexParcel.ORETYPE",
            "Time.StartTime",
            "Time.EndTime",
            "Mining.wetTonnes",
            "Destination.Type",
            "Destination.Name",
            "Destination.FullName",
            "Agent.Name",
        ):
            self.assertIn(column, ExpitDataHandler.TRANSACTION_COLUMNS)

    def test_guidance_version_is_pinned(self):
        """CHANGES IN Task 20. The version must increment with the contract."""
        self.assertEqual(ExpitDataHandler.DESTINATION_GUIDANCE_VERSION, 3)

    def test_guidance_requires_its_documented_columns(self):
        """A missing contracted column must fail loudly, not silently."""
        frame = pd.DataFrame([{
            "Source.Type": "Block",
            "Source.FullName": CANONICAL_BLOCK,
            "Time.StartTime": "2026-09-01 06:00",
            # Destination and tonnes columns deliberately omitted.
        }])
        buffer = io.StringIO()
        frame.to_csv(buffer, index=False)
        buffer.seek(0)
        with self.assertRaises(ValueError) as caught:
            ExpitDataHandler.build_2wp_destination_guidance(buffer)
        self.assertIn("destination-guidance column", str(caught.exception))

    def test_empty_guidance_returns_the_documented_empty_shape(self):
        frame = pd.DataFrame(columns=[
            "Source.Type", "Source.FullName", "Source.Pit",
            "Destination.Type", "Destination.Name", "Destination.FullName",
            "Time.StartTime", "Time.EndTime", "Mining.wetTonnes",
        ])
        buffer = io.StringIO()
        frame.to_csv(buffer, index=False)
        buffer.seek(0)
        guidance = ExpitDataHandler.build_2wp_destination_guidance(buffer)
        self.assertEqual(
            guidance["matching_version"],
            ExpitDataHandler.DESTINATION_GUIDANCE_VERSION,
        )
        self.assertEqual(guidance["source_destinations"], {})
        self.assertEqual(guidance["pit_destinations"], {})
        self.assertEqual(guidance["last_destination"], {})


class MaterialDestinationPlanCharacterisation(unittest.TestCase):
    """Section 9 of the contracts document."""

    def test_plan_columns_are_stable(self):
        """CHANGES IN Task 25 and Task 27.

        Task 25 updates this plan for destination progress and Task 27 adds
        published backup destinations. The current column list is the baseline.
        """
        self.assertEqual(MaterialDestinationPlan.COLUMNS, [
            "plan_type",
            "plan_id",
            "grade_block",
            "planned_2wp_destination",
            "fallback_destination",
            "two_wp_destination_resolution",
            "assigned_destination",
            "alternate_destination_1",
            "alternate_destination_2",
            "assigned_destination_type",
            "source_tonnes",
            "assigned_tonnes",
            "assigned_ratio",
            "assignment_source",
        ])

    def test_no_backup_destination_column_exists_yet(self):
        """CHANGES IN Task 27."""
        self.assertNotIn(
            "backup_destination", MaterialDestinationPlan.COLUMNS
        )

    def test_no_remaining_capacity_column_exists_yet(self):
        """CHANGES IN Task 22."""
        self.assertNotIn(
            "remaining_capacity_wmt", MaterialDestinationPlan.COLUMNS
        )


class ProductBuildLaneCharacterisation(unittest.TestCase):
    """Sections 8 and 12 of the contracts document.

    Task 4 adapts current single-crusher, single-OPF behavior into one
    topology lane without changing solver results, and Task 29 adds the owning
    OPF property. These assertions pin the single-lane baseline.
    """

    def test_lane_names_are_stable(self):
        self.assertEqual(PRODUCT_LANE, "product")
        self.assertEqual(BYPRODUCT_LANES, ("lump", "fines"))

    def test_lane_column_naming_is_stable(self):
        self.assertEqual(lane_grade_column(PRODUCT_LANE, "fe"), "source_grade_fe")
        self.assertEqual(
            lane_actual_tonnes_column(PRODUCT_LANE),
            "product_build_actual_tonnes",
        )

    def test_lane_analytes_match_the_grade_stream_analytes(self):
        self.assertEqual(LANE_ANALYTES, ANALYTES)

    def test_product_build_rows_have_no_owning_opf_property(self):
        """CHANGES IN Task 29.

        Combined-OPF mode needs each Product Targets row to declare whether it
        is fed by one OPF or several. Today a build carries a single ``opf``
        string set at import and nothing that expresses sharing.
        """
        build = target_build(1, "CCFB", 1000, 58.0, datetime(2026, 9, 1))
        for absent in ("contributing_opfs", "is_combined_build", "opf_mode"):
            self.assertNotIn(absent, build)

    def test_legacy_target_build_migrates_to_explicit_hard_mode(self):
        """Task 16: legacy bounds acquire a Hard mode without opting into Soft."""
        build = target_build(1, "CCFB", 1000, 58.0, datetime(2026, 9, 1))
        migrated = migrate_target_row(build)
        self.assertEqual(migrated["target_mode"], "hard")
        self.assertEqual(migrated["product_target_schema_version"], 2)
        self.assertEqual({key: migrated[key] for key in build}, build)


class SolverFormulationCharacterisation(unittest.TestCase):
    """Section 8.1 of the contracts document."""

    def test_solver_is_linear_and_cannot_express_a_quadratic_objective(self):
        """Invariant 7. Task 17 must stay piecewise-linear.

        Optimizer imports LpProblem/LpVariable from PuLP and solves with CBC,
        a mixed integer *linear* programme. A quadratic penalty is therefore
        not expressible without replacing the solver.
        """
        source = (
            pathlib.Path(PACKAGE_ROOT) / "classes" / "Optimizer.py"
        ).read_text(encoding="utf-8")
        self.assertIn("from pulp import", source)
        self.assertIn("PULP_CBC_CMD", source)
        self.assertIn("LpMinimize", source)

    def test_open_grade_bounds_are_coerced_before_reaching_the_solver(self):
        """Guards the 0.0-versus-open bound hazard for Task 12.

        CaseModeller turns a falsy max bound into 100 so an unset 2WP maximum
        does not constrain the solve to zero grade.
        """
        source = (
            pathlib.Path(PACKAGE_ROOT) / "classes" / "CaseModeller.py"
        ).read_text(encoding="utf-8")
        self.assertIn('float(setting.get("target_fe_max", 100) or 100)', source)
