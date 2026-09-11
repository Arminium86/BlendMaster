import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
import pickle
from types import SimpleNamespace
from unittest.mock import patch
import pandas as pd
from tests import test_optimised_manual_prepopulation as fixture
from tests import test_manual_blend_planner as manual
from classes.ManualRatioRounding import round_feed_ratios, recalculate_rounded_plan
from classes.ManualRatioRounding import rounding_audit_rows
from classes.ManualBlendPlanner import ManualBlendPlanner, ManualBlendPlanningError
from classes.OptimisedToManualPlan import OptimisedToManualPlan
from classes.ProductQualityReport import quality_report_rows
from classes.ProductQualityLimits import with_quality_configuration
from GUI.ManualRatioRoundingControls import ManualRatioRoundingControls
from PyQt5.QtWidgets import QApplication
from PyQt5.QtWidgets import QMessageBox
from GUI.InitialiseGUI import UserInputs


def scenario(direct=False):
    f = fixture.OptimisedManualPrepopulationTests()
    rows = [f.report_row(1, "SP1", 43.6), f.report_row(1, "SP2", 56.4 if not direct else 43.2)]
    if direct:
        rows.append(f.report_row(1, "GB1", 13.2, "grade_block", source_id="DT1"))
    report = pd.DataFrame(rows)
    m = manual.ManualBlendPlannerTests()
    m.setUp()
    payloads = pd.DataFrame([dict(source="GB1", payload=50, direct_tip_id="DT1", direct_tip_eligible=True,
        delivered_datetime=datetime(2025, 1, 1, 6, 5), source_grade_fe=64)]) if direct else None
    transfer = OptimisedToManualPlan(report, m.stockpiles).build()
    planner = ManualBlendPlanner(transfer["sequence_rows"], transfer["blend_definitions"], m.stockpiles, [], payloads, {}, [], 100)
    return report, planner


class RoundingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_two_steps_source_order_total_and_zero(self):
        self.assertEqual(round_feed_ratios([.436, .564], 5), [.45, .55])
        self.assertEqual(round_feed_ratios([.335, .335, .33], 5), [.35, .35, .3])
        self.assertEqual(round_feed_ratios([0, .5, .5], 20), [0, .6, .4])
        with self.assertRaises(ValueError):
            round_feed_ratios([.5, .5], 3)

    def test_recalculates_and_restores_from_frozen_rounded_inputs(self):
        original, planner = scenario()
        untouched = original.copy()
        transfer, states, allocations, report = recalculate_rounded_plan(original, planner)
        self.assertTrue(original.equals(untouched))
        for actual, expected in zip(report.source_actual_tonnes, [45, 55]):
            self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(report.iloc[0].crusher_actual_grade_fe, 58.9)
        self.assertEqual(report.original_source_feed_ratio.tolist(), [.436, .564])
        restored = pickle.loads(pickle.dumps(transfer))
        replay = ManualBlendPlanner(restored["sequence_rows"], restored["blend_definitions"], planner.stockpile_data, [], None, {}, [], 100)
        replay_report = replay.build_report(replay.build_steady_states(), {})
        self.assertEqual(replay_report.source_actual_tonnes.tolist(), report.source_actual_tonnes.tolist())
        self.assertEqual(replay_report.rounded_source_feed_ratio.tolist(), [.45, .55])

    def test_direct_tip_rounds_as_part_of_full_feed_and_cannot_invent_payloads(self):
        original, planner = scenario(True)
        transfer, states, allocations, report = recalculate_rounded_plan(original, planner)
        for source, expected in (("SP1", 45), ("SP2", 45), ("GB1", 10)):
            self.assertAlmostEqual(report.loc[report.source == source, "source_actual_tonnes"].sum(), expected)
        self.assertAlmostEqual(sum(allocations[states[0]["state_key"]].values()), 10)
        planner.payload_transactions["payload"] = 5
        transfer, states, allocations, report = recalculate_rounded_plan(original, planner)
        self.assertEqual(states[-1]["end_datetime"], datetime(2025, 1, 1, 6, 30))
        self.assertAlmostEqual(report.source_actual_tonnes.sum(), 50)
        self.assertAlmostEqual(report.loc[report.source == "GB1", "source_actual_tonnes"].sum(), 5)
        for amount in report.loc[report.source != "GB1", "source_actual_tonnes"]:
            self.assertAlmostEqual(22.5, amount)
        self.assertIn("Shortened", rounding_audit_rows(transfer["sequence_rows"])[0]["Timing adjustment"])
        self.assertAlmostEqual(transfer["rounding_unfilled_hours"], .5)
        # A shorter window must still contain the required deliveries.
        planner.payload_transactions["delivered_datetime"] = datetime(2025, 1, 1, 6, 50)
        transfer, _, allocations, report = recalculate_rounded_plan(original, planner)
        self.assertEqual(set(report.source), {"SP1", "SP2"})
        self.assertAlmostEqual(report.source_actual_tonnes.sum(), 100)
        self.assertTrue(all(not selected for selected in allocations.values()))
        self.assertIn("Reclaim-only fallback", rounding_audit_rows(transfer["sequence_rows"])[0]["Timing adjustment"])

    def test_direct_tip_duration_rechecks_deliveries_excluded_by_shortening(self):
        original, planner = scenario(True)
        first = planner.payload_transactions.iloc[0].to_dict()
        planner.payload_transactions = pd.DataFrame([
            {**first, "payload": 2},
            {**first, "direct_tip_id": "DT2", "payload": 3, "delivered_datetime": datetime(2025, 1, 1, 6, 40)},
            {**first, "direct_tip_id": "DT3", "payload": 100, "direct_tip_eligible": False},
        ])
        transfer, states, _, report = recalculate_rounded_plan(original, planner)
        self.assertEqual(states[-1]["end_datetime"], datetime(2025, 1, 1, 6, 12))
        self.assertAlmostEqual(report.source_actual_tonnes.sum(), 20)
        self.assertAlmostEqual(report.loc[report.source == "GB1", "source_actual_tonnes"].sum(), 2)
        # Replay/save-load keeps the shortened window and quantities.
        restored = pickle.loads(pickle.dumps(transfer))
        replay = ManualBlendPlanner(restored["sequence_rows"], restored["blend_definitions"],
                                    planner.stockpile_data, [], planner.payload_transactions, {}, [], 100)
        replay_states = replay.build_steady_states()
        allocations = OptimisedToManualPlan.direct_tip_allocations(replay_states, restored["direct_tip_rows"])
        replay_report = replay.build_report(replay_states, allocations)
        for amount, replayed in zip(report.source_actual_tonnes, replay_report.source_actual_tonnes):
            self.assertAlmostEqual(amount, replayed)
        self.assertEqual(rounding_audit_rows(restored["sequence_rows"]), rounding_audit_rows(transfer["sequence_rows"]))

    def test_direct_tip_limited_duration_moves_next_recipe_and_retains_calendar_anchor(self):
        for anchored in (False, True):
            original, planner = scenario(True)
            planner.payload_transactions["payload"] = 5
            f = fixture.OptimisedManualPrepopulationTests()
            original = pd.concat([original, pd.DataFrame([f.report_row(2, "SP2", 100)])], ignore_index=True)
            if anchored:
                planner.periods = {"period_1_start": datetime(2025, 1, 1, 7), "period_1_end": datetime(2025, 1, 1, 8)}
            _, states, _, report = recalculate_rounded_plan(original, planner)
            self.assertEqual(states[0]["end_datetime"], datetime(2025, 1, 1, 6, 30))
            self.assertEqual(states[1]["start_datetime"], datetime(2025, 1, 1, 7 if anchored else 6, 0 if anchored else 30))
            self.assertEqual(states[-1]["end_datetime"], datetime(2025, 1, 1, 8))
            self.assertAlmostEqual(report.source_actual_tonnes.sum(), 150 if anchored else 200)
            self.assertGreaterEqual(report.source_closing_balance.min(), -1e-7)

    def test_direct_tip_limit_uses_all_sources_and_excludes_end_boundary_delivery(self):
        original, planner = scenario(True)
        original.loc[original.source == "SP2", "source_actual_tonnes"] = 33.2
        f = fixture.OptimisedManualPrepopulationTests()
        original = pd.concat([original, pd.DataFrame([f.report_row(1, "GB2", 10, "grade_block")])], ignore_index=True)
        payload = planner.payload_transactions.iloc[0].to_dict()
        planner.payload_transactions = pd.DataFrame([
            {**payload, "payload": 5},
            {**payload, "source": "GB2", "direct_tip_id": "DT2", "payload": 1.5},
        ])
        _, states, _, report = recalculate_rounded_plan(original, planner)
        self.assertEqual(states[-1]["end_datetime"], datetime(2025, 1, 1, 6, 18))
        self.assertAlmostEqual(report.source_actual_tonnes.sum(), 30)
        planner.payload_transactions.loc[1, "delivered_datetime"] = datetime(2025, 1, 1, 6, 18)
        _, _, allocations, fallback_report = recalculate_rounded_plan(original, planner)
        self.assertEqual(set(fallback_report.source), {"SP1", "SP2"})
        self.assertTrue(all(not selected for selected in allocations.values()))
        planner.payload_transactions = pd.DataFrame()
        _, _, allocations, fallback_report = recalculate_rounded_plan(original, planner)
        self.assertEqual(set(fallback_report.source), {"SP1", "SP2"})
        self.assertTrue(all(not selected for selected in allocations.values()))

    def test_duration_limit_keeps_feasible_prefix_at_product_build_boundary(self):
        original, planner = scenario(True)
        planner.payload_transactions["payload"] = 5
        planner.product_build_settings = [{"target_tonnes": 25, "brand": "FB"}]
        transfer, states, _, report = recalculate_rounded_plan(original, planner)
        self.assertEqual(states[-1]["end_datetime"], datetime(2025, 1, 1, 7))
        self.assertEqual(states[0]["end_datetime"], datetime(2025, 1, 1, 6, 15))
        self.assertAlmostEqual(report.source_actual_tonnes.sum(), 100)
        self.assertNotIn("GB1", set(report.source))
        self.assertAlmostEqual(transfer["rounding_unfilled_hours"], 0)

    def test_depletion_moves_boundary_and_recalculates_downstream_to_horizon(self):
        report, planner = scenario()
        report["source_closing_balance"] = [0, 900]
        planner._inventory_template["SP1"][0]["balance"] = 43.6
        planner.stockpile_data["SP1"]["balance"] = 43.6
        f = fixture.OptimisedManualPrepopulationTests()
        report = pd.concat([report, pd.DataFrame([f.report_row(2, "SP2", 100)])], ignore_index=True)
        transfer, states, allocations, result = recalculate_rounded_plan(report, planner)
        expected = datetime(2025, 1, 1, 6) + timedelta(hours=43.6 / 45)
        self.assertEqual(states[0]["end_datetime"], expected)
        self.assertEqual(states[1]["start_datetime"], expected)
        self.assertEqual(states[-1]["end_datetime"], datetime(2025, 1, 1, 8))
        self.assertAlmostEqual(result.source_actual_tonnes.sum(), 200, places=5)
        self.assertGreaterEqual(result.source_closing_balance.min(), -1e-6)

    def test_same_blend_id_is_rounded_per_state_and_build_boundary_is_recomputed(self):
        report, planner = scenario()
        f = fixture.OptimisedManualPrepopulationTests()
        report = pd.concat([report, pd.DataFrame([f.report_row(2, "SP1", 51), f.report_row(2, "SP2", 49)])], ignore_index=True)
        report["blend_ID"] = 1
        planner.product_build_settings = [{"target_tonnes": 50, "brand": "FB"}]
        transfer, states, allocations, result = recalculate_rounded_plan(report, planner)
        self.assertEqual(states[0]["end_datetime"], datetime(2025, 1, 1, 6, 30))
        self.assertEqual(transfer["blend_count"], 2)
        self.assertEqual(transfer["blend_definitions"][1]["Source Ratios"], "0.500000, 0.500000")

    def test_controls_defaults_and_settings_round_trip(self):
        view = ManualRatioRoundingControls()
        self.addCleanup(view.deleteLater)
        self.assertEqual(view.settings(), {"enabled": False, "increment": 5})
        view.enabled.setChecked(True)
        view.increment.setCurrentIndex(view.increment.findData(10))
        saved = pickle.loads(pickle.dumps(view.settings()))
        view.set_settings({})
        view.set_settings(saved)
        self.assertEqual(view.settings(), {"enabled": True, "increment": 10})

    def test_sources_rounded_to_zero_are_still_in_review_audit(self):
        report, planner = scenario()
        report["source_actual_tonnes"] = [1, 99]
        transfer, _, _, result = recalculate_rounded_plan(report, planner, 5)
        self.assertEqual(set(result.source), {"SP2"})
        audit = rounding_audit_rows(transfer["sequence_rows"])
        zero = next(r for r in audit if r["Source"] == "SP1")
        self.assertEqual((zero["Original ratio (%)"], zero["Rounded ratio (%)"]), (1, 0))

    def test_manual_hard_and_soft_breaches_are_reported_without_blocking(self):
        for mode in ("hard", "soft"):
            report, planner = scenario()
            planner.product_build_settings = [with_quality_configuration(dict(build_id=1, brand="FB", target_tonnes=1000,
                target_mode=mode, target_fe_min=59, target_fe_max=60, target_fe_lql=59, target_fe_target=59.5,
                target_fe_hql=60, target_fe_limit_mode="soft"))]
            _, _, _, result = recalculate_rounded_plan(report, planner)
            self.assertAlmostEqual(result.source_actual_tonnes.sum(), 100)
            audit = quality_report_rows(result, analyte="fe")[0]
            self.assertEqual(audit["quality_status"], "Hard limit breached" if mode == "hard" else "Soft limit breached")

    def test_mapped_product_tonnes_additive_properties_and_build_boundary_recalculate(self):
        report, planner = scenario()
        planner.source_property_kinds.update(modelled_rom_wmt="additive", modelled_product_wmt="additive", revenue="additive")
        planner.required_source_property_keys.add("revenue")
        planner.product_build_settings = [with_quality_configuration(dict(build_id=1, brand="FB", target_tonnes=30))]
        for source, coefficient in (("SP1", .5), ("SP2", .8)):
            chunk = planner._inventory_template[source][0]
            chunk["source_properties"].update(modelled_rom_wmt=1000, modelled_product_wmt=1000 * coefficient, revenue=2000)
        _, states, _, result = recalculate_rounded_plan(report, planner)
        self.assertAlmostEqual(result.product_build_source_tonnes.sum(), 66.5)
        self.assertAlmostEqual(result.source_property_revenue.sum(), 200)
        expected = datetime(2025, 1, 1, 6) + timedelta(hours=30 / 66.5)
        self.assertEqual(states[0]["end_datetime"], expected)

    def test_changed_depletion_does_not_move_calendar_boundary(self):
        report, planner = scenario()
        report["source_actual_tonnes"] = [46.4, 53.6]
        report["source_closing_balance"] = [0, 900]
        planner._inventory_template["SP1"][0]["balance"] = 46.4
        planner.periods = {"preplan_end": datetime(2025, 1, 1, 7), "period_1_end": datetime(2025, 1, 1, 18)}
        f = fixture.OptimisedManualPrepopulationTests()
        report = pd.concat([report, pd.DataFrame([f.report_row(2, "SP2", 100)])], ignore_index=True)
        _, states, _, _ = recalculate_rounded_plan(report, planner)
        self.assertEqual(states[0]["end_datetime"], datetime(2025, 1, 1, 7))
        self.assertEqual(states[1]["start_datetime"], datetime(2025, 1, 1, 7))

    def test_failed_conversion_preserves_previous_plan_and_gantt(self):
        report, planner = scenario()
        old_sequence = [{"Blend ID": "old"}]
        old_legend = [{"Sources": "OLD"}]
        fake = SimpleNamespace(saved_blends_for_schedule=[{"Blend ID": "old"}],
            stored_blend_sequence_table_for_gantt=old_sequence, manual_direct_tip_allocations={},
            blend_config_table_inputs={}, updated_stockpile_data=planner.stockpile_data,
            fetch_optimised_blend_report=lambda _: report, active_manual_plan_id="Primary",
            calendar_crusher_rate_values=lambda: {"Preplan": 100},
            set_manual_crusher_rate_inputs=lambda _: None,
            create_manual_blend_planner=lambda: planner, manual_gantt_legend_and_tooltip=old_legend,
            manual_ratio_rounding={"enabled": True, "increment": 5})
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), patch.object(QMessageBox, "warning"), \
                patch("GUI.InitialiseGUI.recalculate_rounded_plan", side_effect=ManualBlendPlanningError("Unavailable payload")):
            self.assertFalse(UserInputs.prepopulate_manual_from_optimised_result(fake))
        self.assertEqual(fake.stored_blend_sequence_table_for_gantt, old_sequence)
        self.assertEqual(fake.manual_gantt_legend_and_tooltip, old_legend)

    def test_depletion_cannot_extend_into_an_explicit_planned_gap(self):
        report, planner = scenario()
        report["source_actual_tonnes"] = [46.4, 53.6]
        report["source_closing_balance"] = [0, 900]
        planner._inventory_template["SP1"][0]["balance"] = 46.4
        f = fixture.OptimisedManualPrepopulationTests()
        report = pd.concat([report, pd.DataFrame([f.report_row(2, "SP2", 100, start=datetime(2025, 1, 1, 8))])], ignore_index=True)
        _, states, _, result = recalculate_rounded_plan(report, planner)
        self.assertEqual(states[0]["end_datetime"], datetime(2025, 1, 1, 7))
        self.assertEqual(states[1]["start_datetime"], datetime(2025, 1, 1, 8))
        self.assertAlmostEqual(result.source_actual_tonnes.sum(), 200)


if __name__ == "__main__":
    unittest.main()
