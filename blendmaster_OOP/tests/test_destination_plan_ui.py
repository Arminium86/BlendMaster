import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from GUI.InitialiseGUI import UserInputs
from GUI.MaterialDestinationPlanView import MaterialDestinationPlanView
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QCoreApplication, QEvent, Qt
from tests.test_opf_production_report import DeferredRunner, prepare_fonts
from tests.test_destination_plan_report import fixture, publish
from classes.DestinationPlanReport import write_publication, read_publication

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import pandas as pd
import unittest


class DestinationPlanUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        prepare_fonts(cls.app)

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = str(Path(self.temp.name)/"plans.db")
        for kind, plan_id in (("optimised", "Primary"), ("optimised", "Contingency 1"), ("manual", "Primary")):
            frames, allocation = publish(*fixture(), plan_type=kind, plan_id=plan_id)
            write_publication(frames, self.path, allocation["run"], allocation)
        self.runner = DeferredRunner()
        self.view = MaterialDestinationPlanView(run_async=self.runner)
        self.view.set_context(self.path, "a")
        self.view.resize(1450, 840)
        self.view.show()
        self.app.processEvents()

    def tearDown(self):
        self.view.hide()
        self.view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.temp.cleanup()

    def load(self):
        self.view.request_refresh()
        self.runner.finish()
        self.app.processEvents()

    def test_loading_plans_filtering_and_complete_row_details(self):
        self.view.request_refresh()
        self.assertTrue(self.view.progress.isVisible())
        self.assertFalse(self.view.export.isEnabled())
        self.runner.finish()
        self.assertEqual(self.view.plan.count(), 3)
        self.assertIn("Assigned 150.0 WMT", self.view.status.text())
        for index in range(3):
            self.view.plan.setCurrentIndex(index)
            self.assertEqual(self.view.tables[0].model().rowCount(), 3)
            self.assertEqual(self.view.tables[3].model().rowCount(), 1)
        self.view.search.setText("SP2")
        self.assertGreater(self.view.tables[0].model().rowCount(), 0)
        self.view.search.setText("no-such-destination")
        self.assertEqual(self.view.tables[0].model().rowCount(), 0)
        self.view.search.clear()
        table = self.view.tables[0]
        index = next(i for i, r in enumerate(table.model().records) if r["assigned_destination"] == "SP1")
        table.selectRow(index)
        self.assertIn("before first payload: 50.0", self.view.details.toPlainText())
        self.assertIn("Shortest stockpile-to-stockpile", self.view.details.toPlainText())
        self.assertIn("next", str(self.view.tables[3].model().records).lower())

    def test_failed_reload_keeps_only_same_context_saved_snapshot(self):
        self.load()
        self.view.loader = lambda path: (_ for _ in ()).throw(OSError("Locked example"))
        self.load()
        self.assertIn("Previously loaded saved results", self.view.status.text())
        self.assertEqual(self.view.tables[0].model().rowCount(), 3)
        self.view.set_context(self.path, "b")
        self.load()
        self.assertIn("No results available", self.view.status.text())
        self.assertEqual(self.view.tables[0].model().rowCount(), 0)

    def test_late_previous_site_response_cannot_replace_current_site(self):
        self.view.request_refresh()
        self.view.set_context(str(Path(self.temp.name)/"missing.db"), "b")
        self.view.request_refresh()
        self.runner.finish(0)
        self.assertTrue(self.view.pending)
        self.assertEqual(self.view.snapshot, {})
        self.runner.finish(0)
        self.assertFalse(self.view.pending)
        self.assertEqual(self.view.plan.count(), 0)
        self.assertIn("No published destination rows", self.view.status.text())

    def test_unavailable_and_ambiguous_inputs_stay_explicit(self):
        rows, report, ctx = fixture()
        ctx["settings"]["selected_instances"] = {}
        observed = (pd.Timestamp(ctx["start"])-pd.Timedelta(minutes=1)).isoformat()
        ctx["activity"] = dict(status="Cached", fetched_at=observed, records=[
            dict(movement_id=destination, observed_at=observed, destination=destination,
                 rom_area="CR1", material_type="HG", source_block="PIT_A|1|100|20|105|HG99", wmt=50)
            for destination in ("SP1", "SP2")])
        frames, run = publish(rows, report, ctx)
        snapshot = {k: frame.to_dict("records") for k, frame in frames.items()}
        snapshot["destination_allocation_runs"] = [run["run"]]
        self.view.loader = lambda _: snapshot
        self.load()
        self.assertIn("Unresolved 110.0 WMT", self.view.status.text())
        self.assertTrue(any(r["status"] == "Unresolved" for r in self.view.tables[0].model().records))
        self.assertEqual(self.view.tables[4].model().rowCount(), 2)
        self.assertIn("Cached", self.view.status.text())

    def test_export_current_plan_preserves_capacity_and_fallback_fields(self):
        self.load()
        self.view.plan.setCurrentIndex(self.view.plan.findText("Optimised · Contingency 1"))
        self.load()
        self.assertEqual(self.view.plan.currentText(), "Optimised · Contingency 1")
        path = str(Path(self.temp.name)/"export.csv")
        with patch("GUI.MaterialDestinationPlanView.QFileDialog.getSaveFileName", return_value=(path, "")):
            self.view.export_csv()
        exported = pd.read_csv(path)
        self.assertEqual(set(exported.plan_id), {"Contingency 1"})
        self.assertEqual(exported.assigned_tonnes.sum(), 150)
        self.assertEqual(exported.consumed_capacity_wmt.sum(), 110)
        self.assertIn("fallback_2_rule", exported)

    def test_numeric_display_rounds_without_losing_tooltip_precision(self):
        self.load()
        model = self.view.tables[0].model()
        model.records[0]["reported_wmt"] = 123.456789
        index = model.index(0, 3)
        self.assertEqual(model.data(index, Qt.DisplayRole), "123.5")
        self.assertEqual(model.data(index, Qt.ToolTipRole), "123.456789")


if __name__ == "__main__":
    unittest.main()
