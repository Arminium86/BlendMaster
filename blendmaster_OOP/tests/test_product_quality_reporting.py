"""Quality results agree across solver, saved reports, native review and CSV."""

import csv
from contextlib import closing
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from tests import test_soft_product_grades as soft_tests, test_manual_blend_planner as manual_tests
from tests.test_product_quality_limits import build, model
from classes.ProductBuildProgress import ProductBuildProgress
from classes.ProductQualityReport import quality_report_rows
from classes.CaseModeller import CaseModeller
from classes.ManualBlendPlanner import ManualBlendPlanner
from execute.Run import Run
from GUI.ProductQualityReview import ProductQualityReview
from GUI.ManualBlendDash import DrawOptimisedGradeProfiles
from PyQt5.QtWidgets import QApplication


def history():
    rows = []
    for i, fe in enumerate((56, 60)):
        rows.append(dict(source=f"SP{i}", source_type="stockpile", source_actual_tonnes=100,
                         crusher_actual_tonnes=100, product_build_source_tonnes=100, steady_state_number=i,
                         blend_ID=i, blend_option=1, start_datetime=datetime(2026, 9, 1) + timedelta(hours=i),
                         end_datetime=datetime(2026, 9, 1) + timedelta(hours=i + 1),
                         **{f"source_grade_{a}": fe if a == "fe" else 1 for a in ("fe", "si", "al", "p", "mn")},
                         **{f"selected_grade_weight_{a}_tonnes": 50 for a in ("fe", "si", "al", "p", "mn")}))
    return pd.DataFrame(rows)


def settings(**kw):
    return build(target_tonnes=1000, target_mode="soft", target_fe_target=58,
                 target_fe_limit_mode="soft", target_evaluation_basis="cumulative_build", **kw)


class ProductQualityReportingTests(unittest.TestCase):
    def test_cumulative_penalties_and_dispersion_use_grade_weights_without_double_counting(self):
        report = ProductBuildProgress.annotate(history(), [settings()], solver_config={"soft_grade_preferences": {"similarity_mode": "both"}})
        rows = quality_report_rows(report, grain="cumulative_build", analyte="fe")
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["actual_grade"] for r in rows], [56, 58])
        self.assertEqual([r["grade_weight_tonnes"] for r in rows], [50, 100])
        self.assertEqual([r["applied_penalty"] for r in rows], [45000, -45000])
        self.assertEqual([r["source_dispersion_score"] for r in rows], [4, 4])
        self.assertEqual(rows[0]["quality_status"], "Soft limit breached")
        self.assertEqual(rows[1]["quality_status"], "Within limits")
        state = quality_report_rows(report, grain="steady_state", analyte="fe")
        self.assertEqual([r["actual_grade"] for r in state], [56, 60])
        self.assertTrue(all(r["applied_penalty"] is None for r in state))

    def test_saved_audit_matches_actual_cbc_objective_for_selected_mixture(self):
        fixture = soft_tests.SoftGradeTests()
        row = build(target_mode="soft", target_fe_target=58, target_fe_limit_mode="soft")
        config = {"similarity_mode": "both"}
        solved = fixture.solve([fixture.event("FE56", grade_fe=56)], row, soft_grade_preferences=config)
        raw = history().iloc[:1].copy()
        for a in ("fe", "si", "al", "p", "mn"):
            raw[f"selected_grade_weight_{a}_tonnes"] = 100
        reported = ProductBuildProgress.annotate(raw, [row], solver_config={"soft_grade_preferences": config})
        saved = quality_report_rows(reported, analyte="fe")[0]
        audit = fixture.fe(solved)
        for key in ("actual_grade", "target_penalty", "limit_penalty", "applied_penalty", "source_dispersion_score", "applied_similarity_penalty"):
            self.assertAlmostEqual(saved[key], audit[key], msg=key)

    def test_actual_case_recording_preserves_declared_dmt_for_the_next_cumulative_solve(self):
        fixture = soft_tests.SoftGradeTests()
        target = settings()
        source = fixture.event("DMT_SOURCE", grade_fe=56)
        source._source_properties = {"dmt": 500}
        weights = {f"adjusted_product_{a}": "dmt" for a in ("fe", "si", "al", "p", "mn")}
        result = fixture.solve([source], target, source_property_weights=weights)
        value = model([target])
        value.current_time = datetime(2026, 9, 1)
        value.steady_state_tracker = 0
        value.blend_option = value.blend_ID = 1
        value.period_tracker = "preplan"
        value.decision_point_results = pd.DataFrame()
        value.two_wp_active_blend_report_fields = lambda _: {}
        value.record_results(result)
        self.assertEqual(value.decision_point_results.iloc[0]["selected_grade_weight_fe_tonnes"], 50)
        value.product_build_runtime_states = [{"tonnes": 0, **{f"grade_{a}_metal": 0 for a in ("fe", "si", "al", "p", "mn")}}]
        value.update_product_build_runtime_state(value.decision_point_results)
        state = value.product_build_runtime_states[0]
        self.assertEqual(state["grade_fe_weight"], 50)
        self.assertEqual(state["grade_fe_metal"], 2800)
        value.results = value.decision_point_results
        reported = Run._case_blend_report(value)
        audit = quality_report_rows(reported, grain="cumulative_build", analyte="fe")[0]
        self.assertEqual(audit["applied_penalty"], fixture.fe(result)["applied_penalty"])

    def test_build_report_and_sqlite_keep_modes_penalties_precision_and_zero(self):
        value = model([settings(target_p_target=0)])
        value.results = history()
        value.group_grade_block_rows = lambda frame: frame
        report = value.build_product_build_report()
        self.assertEqual(report.iloc[0]["target_fe_limit_mode"], "soft")
        with tempfile.TemporaryDirectory() as temp:
            with closing(sqlite3.connect(str(Path(temp) / "report.db"))) as db:
                report.to_sql("product_build_report", db, index=False)
                restored = pd.read_sql_query("SELECT * FROM product_build_report", db)
        rows = quality_report_rows(restored, grain="cumulative_build", analyte="p")
        self.assertEqual(rows[0]["target"], 0)
        self.assertIsNone(rows[0]["lql"])
        self.assertEqual(restored.iloc[0]["target_mode"], "soft")

    def test_source_repetition_does_not_multiply_audit_rows(self):
        report = ProductBuildProgress.annotate(history(), [settings()])
        repeated = pd.concat([report, report, report], ignore_index=True)
        self.assertEqual(len(quality_report_rows(repeated)), 10)
        self.assertEqual(sum(r["applied_penalty"] or 0 for r in quality_report_rows(repeated, grain="cumulative_build")), 0)

    def test_soft_breach_does_not_trigger_hard_repair_or_failed_run(self):
        value = model([settings()])
        value.solver_config = dict(enable_product_build_repair_loop=True, allow_offspec_steady_states_for_product_build=True)
        value.product_build_offspec_steady_states = Mock(side_effect=AssertionError("must not repair Soft"))
        value.request_product_build_repair(0, "Soft breach")
        value.product_build_runtime_states = [dict(tonnes=1000, **{f"grade_{a}_metal": 56000 if a == "fe" else 1000 for a in ("fe", "si", "al", "p", "mn")})]
        self.assertFalse(value.product_build_grade_on_spec(value.product_build_runtime_states[0], value.product_build_settings[0]))
        self.assertEqual(Run._completed_offspec_product_build_names(value), [])

    def test_parent_grade_block_grouping_keeps_preaggregation_dispersion(self):
        raw = history()
        raw["source"] = ["Reserves/CC2/CAT03/01/453/426/456/LG09_453", "Reserves/CC2/CAT03/01/453/426/456/LG09_285"]
        raw["source_type"] = "grade_block"
        raw["steady_state_number"] = 0
        raw["blend_ID"] = 1
        raw["start_datetime"] = raw["start_datetime"].iloc[0]
        raw["end_datetime"] = raw["end_datetime"].iloc[0]
        value = model([settings()])
        value.results = raw
        value.solver_config = {"soft_grade_preferences": {"similarity_mode": "dispersion", "include_direct_tip": True}}
        report = Run._case_blend_report(value)
        self.assertEqual(len(report), 1)
        row = quality_report_rows(report, grain="cumulative_build", analyte="fe")[0]
        self.assertEqual(row["actual_grade"], 58)
        self.assertEqual(row["source_dispersion_score"], 4)

    def test_manual_plan_uses_saved_preferences_and_reports_both_grains(self):
        fixture = manual_tests.ManualBlendPlannerTests()
        fixture.setUp()
        planner = ManualBlendPlanner(fixture.sequence, fixture.blends, fixture.stockpiles, [], None, fixture.periods,
                                    [settings()], 100, {"solver_config": {"soft_grade_preferences": {"target_weight": 2, "similarity_mode": "dispersion"}}})
        report = planner.build_report(planner.build_steady_states(), {})
        rows = quality_report_rows(report, grain="cumulative_build", analyte="fe")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["actual_grade"] == 59 for r in rows))
        self.assertAlmostEqual(rows[0]["applied_penalty"], 20000)
        self.assertAlmostEqual(rows[0]["source_dispersion_score"], 2)

    def test_grade_profile_labels_soft_target_and_limit_mode(self):
        value = model([settings()])
        value.results = history()
        value.group_grade_block_rows = lambda frame: frame
        report = value.build_product_build_report()
        profile = DrawOptimisedGradeProfiles.__new__(DrawOptimisedGradeProfiles)
        data = profile.transform_product_build_data(report, ["Grade Fe"])
        self.assertTrue(all("Soft" in text and "soft limits" in text and "Target 58" in text for text in data["quality_caption"]))


class ProductQualityReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_native_filters_empty_state_and_csv_export_keep_exact_values(self):
        view = ProductQualityReview()
        self.addCleanup(view.deleteLater)
        self.assertIn("No quality", view.status.text())
        self.assertFalse(view.export.isEnabled())
        view.set_report(ProductBuildProgress.annotate(history(), [settings()]))
        view.grain.setCurrentIndex(view.grain.findData("cumulative_build"))
        view.analyte.setCurrentIndex(view.analyte.findData("fe"))
        self.assertEqual(view.table.rowCount(), 2)
        self.assertIn("1 limit breaches", view.status.text())
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "export.csv")
            with patch("GUI.ProductQualityReview.QFileDialog.getSaveFileName", return_value=(path, "CSV")):
                view.export_csv()
            with open(path, encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(float(rows[1]["applied_penalty"]), -45000)
        view.search.setText("no matching build")
        self.assertEqual(view.table.rowCount(), 0)
        self.assertFalse(view.export.isEnabled())

    def test_native_reads_current_optimised_and_manual_sqlite_without_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "audit.db")
            report = ProductBuildProgress.annotate(history(), [settings()])
            with closing(sqlite3.connect(path)) as db:
                report.to_sql("optimised_blend_report", db, index=False)
            view = ProductQualityReview(path)
            self.addCleanup(view.deleteLater)
            self.assertEqual(view.table.rowCount(), 10)
            view.plan.setCurrentIndex(1)
            self.assertEqual(view.table.rowCount(), 0)
            self.assertIn("No quality", view.status.text())


if __name__ == "__main__":
    unittest.main()
