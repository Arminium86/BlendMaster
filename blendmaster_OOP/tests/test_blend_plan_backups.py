import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from copy import deepcopy
from types import SimpleNamespace
import pickle
from classes.BlendPlanBackups import backup_choices, backup_publication, backup_unavailable_reason
from classes.ExpitDataHandler import ExpitDataHandler
from GUI.BlendPlanBackupControls import BlendPlanBackupControls
from GUI.InitialiseGUI import UserInputs
from PyQt5.QtWidgets import QApplication


class BackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_only_resolved_fallbacks_with_compatible_inventory_mapping_are_offered(self):
        rows = [{"fallback_1_destination": "Stockpiles/SP1", "fallback_2_destination": "Stockpiles/SP2"},
                {"fallback_1_destination": "Stockpiles/SP3", "fallback_2_destination": "Stockpiles/MISSING"}]
        original = deepcopy(rows)
        result = backup_choices(["CR1", "CR2"], rows, {"SP1": "CR1", "SP2": "CR2", "SP3": "CR1"})
        self.assertEqual(result, {"CR1": ["SP1", "SP3"], "CR2": ["SP2"]})
        self.assertEqual(rows, original)

    def test_publication_requires_current_choice_and_preserves_explicit_no_evidence(self):
        choices = {"CR1": ["SP1"], "CR2": []}
        for selection in ({}, {"CR1": "STALE"}):
            with self.assertRaises(ValueError):
                backup_publication(choices, selection)
        rows = backup_publication(choices, {"CR1": "SP1"})
        self.assertEqual(rows[0]["Backup destination"], "SP1")
        self.assertEqual(rows[1]["Backup destination"], "No eligible fallback")
        self.assertIn("direct tip", rows[0]["Applies to"])

    def test_refreshed_rch_fallbacks_do_not_belong_to_opf01(self):
        rows = [{"fallback_1_destination": "Stockpiles/OPF02_RP01_0201",
                 "fallback_2_destination": "Stockpiles/HAL01_RP01_0308"}]
        areas = {"OPF02_RP01_0201": "RCH", "HAL01_RP01_0308": "HAL CRUSHER"}
        matches = lambda area, point: ExpitDataHandler.crusher_destination_matches(area, "CC", point)
        choices = backup_choices(["OPF01_PC"], rows, areas, matches)
        self.assertEqual(choices, {"OPF01_PC": []})
        reason = backup_unavailable_reason(choices, rows, areas)
        self.assertIn("OPF01_PC", reason)
        self.assertIn("RCH", reason)
        self.assertIn("HAL CRUSHER", reason)
        self.assertNotIn("Refresh", reason)
        choices = backup_choices(["OPF02_PC"], rows, areas, matches)
        self.assertEqual(choices, {"OPF02_PC": ["OPF02_RP01_0201"]})
        self.assertEqual(backup_unavailable_reason(choices, rows, areas), "")

    def test_missing_mapping_and_missing_reconciliation_have_distinct_reasons(self):
        reason = backup_unavailable_reason({"OPF01_PC": []}, [{"fallback_1_destination": "SP1"}], {})
        self.assertIn("Nearest Crusher mapping is unavailable", reason)
        reason = backup_unavailable_reason({"OPF01_PC": []}, [{"status": "Unavailable", "reason": "Refresh reconciliation"}], {})
        self.assertEqual(reason, "Refresh reconciliation")

    def test_controls_no_plan_empty_stale_and_selection_round_trip(self):
        view = BlendPlanBackupControls()
        self.addCleanup(view.deleteLater)
        view.set_context({"CR1": ["SP1", "SP2"]}, {}, False)
        self.assertFalse(view.fields["CR1"].isEnabled())
        view.set_context({"CR1": []}, {})
        self.assertEqual(view.fields["CR1"].currentText(), "No eligible fallback")
        self.assertIn("No eligible backup destinations", view.status.text())
        reason = "Load or refresh Destination Reconciliation for this scenario before recalculating the plan."
        view.set_context({"CR1": []}, {}, unavailable_reason=reason)
        self.assertIn(reason, view.status.text())
        self.assertFalse(view.fields["CR1"].isEnabled())
        view.set_context({"CR1": ["SP1"]}, {"CR1": "SP2"})
        self.assertIn("Unavailable", view.fields["CR1"].currentText())
        events = []
        view.changed.connect(events.append)
        field = view.fields["CR1"]
        field.setCurrentIndex(field.findData("SP1"))
        saved = pickle.loads(pickle.dumps(events[-1]))
        view.set_context({"CR1": ["SP1", "SP2"]}, saved)
        self.assertEqual(view.fields["CR1"].currentData(), "SP1")

    def test_backup_choices_are_owned_by_manual_plan(self):
        view = SimpleNamespace(MANUAL_PLAN_STATE_FIELDS=UserInputs.MANUAL_PLAN_STATE_FIELDS,
                               active_manual_plan_id="Primary", manual_plan_states={},
                               blend_plan_backup_destinations={"CR1": "SP1"}, manual_input_revision='primary')
        UserInputs.capture_active_manual_plan_state(view)
        view.active_manual_plan_id = "Contingency 1"
        view.blend_plan_backup_destinations = {"CR1": "SP2"}
        view.manual_input_revision = 'contingency'
        UserInputs.capture_active_manual_plan_state(view)
        self.assertEqual(view.manual_plan_states["Primary"]["blend_plan_backup_destinations"], {"CR1": "SP1"})
        self.assertEqual(view.manual_plan_states["Contingency 1"]["blend_plan_backup_destinations"], {"CR1": "SP2"})
        self.assertEqual(view.manual_plan_states['Primary']['manual_input_revision'], 'primary')
        self.assertEqual(view.manual_plan_states['Contingency 1']['manual_input_revision'], 'contingency')


if __name__ == "__main__":
    unittest.main()
