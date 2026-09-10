"""Quality-direction captions preserve numerical bounds and saved evidence."""

import csv
import pickle
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests.test_product_quality_limits import build, target_window, model
from tests.test_product_quality_reporting import history
from tests import test_soft_product_grades as soft
from tests import test_opf_production_report as assay
from classes.ProductBuildProgress import ProductBuildProgress
from classes.ProductTargets import migrate_product_target_state
from classes.SoftProductGrades import analyte_audit
from GUI.ProductQualityReview import ProductQualityReview
from GUI.ManualBlendDash import DrawOptimisedGradeProfiles
from PyQt5.QtWidgets import QApplication


class QualityCaptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self, rows):
        view = target_window(rows)
        self.addCleanup(view.deleteLater)
        return view

    def item(self, view, label):
        return view.product_build_table.item(0, view.product_build_headers.index(label))

    def test_saved_limits_display_correct_quality_labels_and_edits_round_trip(self):
        original = build(target_mode="soft", **{
            f"target_{a}_{part}": value for a in ("si", "al", "p", "mn")
            for part, value in (("lql", 0), ("target", .5), ("hql", 1))})
        view = self.window([original])
        self.assertEqual(float(self.item(view, "Fe LQL").text()), 57)
        self.assertEqual(float(self.item(view, "Fe HQL").text()), 60)
        for a in ("Si", "Al", "P", "Mn"):
            self.assertEqual(float(self.item(view, a + " HQL").text()), 0)
            self.assertEqual(float(self.item(view, a + " LQL").text()), 1)
            self.assertIn("HQL ≤ Target ≤ LQL", self.item(view, a + " HQL").toolTip())
            self.item(view, a + " LQL").setText("0.9")
        saved = view.read_product_targets_from_table(False)
        state = migrate_product_target_state(pickle.loads(pickle.dumps({"product_targets": saved})))
        restored = self.window(state["product_targets"])
        for a in ("Si", "Al", "P", "Mn"):
            self.assertEqual(saved[0][f"target_{a.lower()}_hql"], .9)
            self.assertEqual(float(self.item(restored, a + " LQL").text()), .9)
            self.assertEqual(float(self.item(restored, a + " HQL").text()), 0)
        self.item(restored, "P HQL").setText("")
        self.assertIsNone(restored.read_product_targets_from_table(False)[0]["target_p_lql"])

    def test_validation_message_uses_the_edited_contaminant_quality_label(self):
        view = self.window([build(target_si_lql=3, target_si_target=4, target_si_hql=5)])
        self.item(view, "Si LQL").setText("3.5")
        with patch("GUI.InitialiseGUI.QMessageBox.warning") as warning:
            self.assertIsNone(view.read_product_targets_from_table())
        self.assertIn("Target must not exceed LQL", warning.call_args.args[-1])
        self.item(view, "Si LQL").setText("5")
        self.item(view, "Si HQL").setText("4.5")
        with patch("GUI.InitialiseGUI.QMessageBox.warning") as warning:
            self.assertIsNone(view.read_product_targets_from_table())
        self.assertIn("Target must be at least HQL", warning.call_args.args[-1])

    def test_contaminant_ui_limits_keep_real_solver_constraints_and_penalties(self):
        view = self.window([build(target_mode="soft", target_si_target=4)])
        self.item(view, "Si HQL").setText("3")
        self.item(view, "Si LQL").setText("5")
        row = view.read_product_targets_from_table(False)[0]
        fixture = soft.SoftGradeTests()
        for grade in (2, 6):
            solved = fixture.solve([fixture.event("OUTSIDE", grade_si=grade)], row,
                                   soft_grade_preferences={"target_weight": 0})
            self.assertEqual(fixture.selected_sources(solved), set())
        for grade in (3, 4, 5):
            solved = fixture.solve([fixture.event("ACCEPTED", grade_si=grade)], row)
            self.assertEqual(fixture.selected_sources(solved), {"ACCEPTED"})
        row["target_si_limit_mode"] = "soft"
        solved = fixture.solve([fixture.event("SOFT_BREACH", grade_si=6)], row)
        audit = next(r for r in solved["diagnostics"]["product_quality"] if r["analyte"] == "si")
        self.assertEqual(fixture.selected_sources(solved), {"SOFT_BREACH"})
        self.assertAlmostEqual(audit["limit_penalty"], 50000)
        self.assertAlmostEqual(audit["limit_penalty"], analyte_audit(row, "si", 600, 100)["limit_penalty"])

    def test_saved_audit_and_csv_show_quality_limits_and_breaches_for_both_directions(self):
        row = build(target_mode="soft", target_fe_limit_mode="soft", target_si_limit_mode="soft",
                    target_si_lql=.5, target_si_target=.6, target_si_hql=.8)
        report = ProductBuildProgress.annotate(history(), [row])
        original = report.copy(deep=True)
        view = ProductQualityReview()
        self.addCleanup(view.deleteLater)
        view.set_report(report)
        fe = next(r for r in view.rows if r["analyte"] == "fe")
        si = next(r for r in view.rows if r["analyte"] == "si")
        self.assertEqual((fe["lql"], fe["hql"], fe["lql_breach"], fe["hql_breach"]), (57, 60, 1, 0))
        self.assertEqual((si["lql"], si["hql"], si["hql_breach"]), (.8, .5, 0))
        self.assertAlmostEqual(si["lql_breach"], .2)
        view.render()  # Reloading does not reverse the displayed values twice.
        self.assertEqual(next(r for r in view.rows if r["analyte"] == "si")["lql"], .8)
        self.assertTrue(report.equals(original))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quality.csv"
            with patch("GUI.ProductQualityReview.QFileDialog.getSaveFileName", return_value=(str(path), "CSV")):
                view.export_csv()
            with path.open(encoding="utf-8-sig", newline="") as handle:
                exported = next(r for r in csv.DictReader(handle) if r["analyte"] == "si")
            self.assertEqual(float(exported["lql"]), .8)
            self.assertAlmostEqual(float(exported["lql_breach"]), .2)
            self.assertNotIn("below_lql", exported)

    def test_manual_grade_profile_uses_contaminant_quality_captions(self):
        value = model([build(target_mode="soft", target_si_lql=3, target_si_target=4, target_si_hql=5)])
        value.results = history()
        value.group_grade_block_rows = lambda frame: frame
        report = value.build_product_build_report()
        profile = DrawOptimisedGradeProfiles.__new__(DrawOptimisedGradeProfiles)
        data = profile.transform_product_build_data(report, ["Grade Si"])
        self.assertTrue(all("HQL 3; LQL 5" in text for text in data["quality_caption"]))

    def test_production_chart_legend_styles_and_details_match_contaminant_quality(self):
        with tempfile.TemporaryDirectory() as directory:
            service = assay.ProductAssayHistory(cache_directory=directory, clock=lambda: assay.END)
            service._query = Mock(return_value=assay.observations())
            runner = assay.DeferredRunner()
            view = assay.OPFProductionReport(service=service, run_async=runner)
            self.addCleanup(view.deleteLater)
            targets = assay.targets()
            for row in targets:
                row.update(target_si_lql=3, target_si_target=4, target_si_hql=5)
            assay.report_context(view, targets=targets)
            view.request_refresh()
            runner.finish()
            lines = view.figure.axes[1].lines
            for grade, style in ((3, ":"), (5, "--")):
                bounds = [line for line in lines if list(line.get_ydata()) == [grade, grade]]
                self.assertTrue(bounds)
                self.assertTrue(all(line.get_linestyle() == style for line in bounds))
            self.assertIn("SiO₂: 5.0 / 4.0 / 3.0", view.target_details.toPlainText())
            self.assertIn("HQL ≤ Target ≤ LQL", view.notes.text())


if __name__ == "__main__":
    unittest.main()
