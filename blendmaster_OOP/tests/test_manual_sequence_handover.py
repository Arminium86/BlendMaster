import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace, MethodType
from unittest.mock import Mock, patch
import pandas as pd
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QTableWidget, QTableWidgetItem, QComboBox, QDateTimeEdit, QMessageBox
from GUI.InitialiseGUI import UserInputs
from classes.ManualBlendRules import ManualBlendRules
from classes.ManualBlendPlanner import ManualBlendPlanner, ManualBlendPlanningError
from classes.OptimisedToManualPlan import OptimisedToManualPlan
from tests import test_optimised_manual_prepopulation as fixture_module


class SequenceHandoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_view(self):
        fixture = fixture_module.OptimisedManualPrepopulationTests()
        start = datetime(2025, 1, 1, 6, 0, 15)
        rows, payloads = [], []
        for state in (1, 2):
            at = start + timedelta(hours=.26 * (state - 1))
            for source, tonnes, kind in [('SP1', 10, 'stockpile'), ('GB1', 16, 'grade_block')]:
                row = fixture.report_row(state, source, tonnes, kind, source_id=f'DT{state}' if kind == 'grade_block' else source,
                                         start=at, duration=.26, crusher_tonnes=26)
                row['blend_ID'] = 1
                rows.append(row)
            payloads.append(dict(source='GB1', direct_tip_id=f'DT{state}', payload=16,
                                 direct_tip_eligible=True, delivered_datetime=at+timedelta(minutes=1), source_grade_fe=64))
        transfer = OptimisedToManualPlan(pd.DataFrame(rows)).build()
        table = QTableWidget(2, 7)
        table.setHorizontalHeaderLabels(['Blend ID', 'Origin', 'Start Datetime', 'Duration (hrs)', 'End Datetime', 'Early Start Flag', 'Remaining Hrs'])
        for i in range(2):
            combo = QComboBox(); combo.addItem('1'); table.setCellWidget(i, 0, combo)
            table.setCellWidget(i, 2, QDateTimeEdit())
            for j in (1, 3, 4, 5, 6):
                table.setItem(i, j, QTableWidgetItem())
        view = SimpleNamespace(blend_sequence_table=table,
            saved_blends_for_schedule=transfer['blend_definitions'],
            stored_blend_sequence_table_for_gantt=deepcopy(transfer['sequence_rows']),
            update_blend_id=Mock(), update_early_start_conditional_format=Mock(),
            start_or_update_dash_manual_chart_thread=Mock(), load_manual_gantt_chart=Mock(),
            set_page_enabled=Mock(), grade_profile_tab_index=1)
        for name in ('collect_blend_sequence_table_rows', 'preserve_optimised_sequence_metadata',
                     'update_remaining_hrs', 'apply_manual_gantt_rows_to_table',
                     'populate_blend_sequence_table_if_project_is_loaded', 'submit_blend_sequence_table_to_gantt'):
            setattr(view, name, MethodType(getattr(UserInputs, name), view))
        def generate(**kwargs):
            planner = ManualBlendPlanner(view.stored_blend_sequence_table_for_gantt,
                view.saved_blends_for_schedule, {'SP1': {'balance': 20, 'grade_fe': 60}}, [], pd.DataFrame(payloads), {}, [], 100)
            states = planner.build_steady_states()
            allocations = OptimisedToManualPlan.direct_tip_allocations(states, transfer['direct_tip_rows'])
            view.report = planner.build_report(states, allocations)
        view.generate_manual_blend_plan = generate
        return view, transfer

    def test_exact_hydrate_reload_and_submit_preserves_depleted_stock_and_direct_tip(self):
        view, transfer = self.make_view()
        # Displayed .3 + .3 exceeds the exact .52-hour recipe maximum.
        self.assertGreater(sum(r['Duration (hrs)'] for r in transfer['sequence_rows']), .52)
        view.populate_blend_sequence_table_if_project_is_loaded()
        view.populate_blend_sequence_table_if_project_is_loaded()
        self.assertEqual('0.00', view.blend_sequence_table.item(1, 6).text())
        self.assertFalse(view.blend_sequence_table.item(1, 6).flags() & Qt.ItemIsEditable)
        self.assertEqual(transfer['sequence_rows'][1]['_exact_end'], view.stored_blend_sequence_table_for_gantt[1]['_exact_end'])
        with patch.object(QMessageBox, 'warning') as warning, patch.object(QMessageBox, 'information'):
            self.assertTrue(view.submit_blend_sequence_table_to_gantt())
        warning.assert_not_called()
        self.assertAlmostEqual(20, view.report.loc[view.report.source == 'SP1', 'source_actual_tonnes'].sum())
        self.assertAlmostEqual(32, view.report.loc[view.report.source == 'GB1', 'source_actual_tonnes'].sum())

    def test_hydration_does_not_fire_datetime_edits(self):
        view, transfer = self.make_view()
        changed = Mock()
        view.blend_sequence_table.cellWidget(0, 2).dateTimeChanged.connect(changed)
        view.apply_manual_gantt_rows_to_table(transfer['sequence_rows'])
        changed.assert_not_called()

    def test_remaining_hours_refresh_does_not_emit_user_edits(self):
        view, transfer = self.make_view()
        view.apply_manual_gantt_rows_to_table(transfer['sequence_rows'])
        changed = Mock()
        view.blend_sequence_table.cellChanged.connect(changed)
        view.update_remaining_hrs()
        changed.assert_not_called()

    def test_real_duration_edit_drops_fixed_quantities(self):
        view, transfer = self.make_view()
        view.apply_manual_gantt_rows_to_table(transfer['sequence_rows'])
        view.blend_sequence_table.item(0, 3).setText('0.2')
        rows = view.preserve_optimised_sequence_metadata(view.collect_blend_sequence_table_rows())
        self.assertNotIn('_fixed_steady_state', rows[0])
        self.assertTrue(rows[1]['_fixed_steady_state'])

    def test_failed_submission_keeps_last_accepted_exact_metadata(self):
        view, transfer = self.make_view()
        view.apply_manual_gantt_rows_to_table(transfer['sequence_rows'])
        accepted = deepcopy(view.stored_blend_sequence_table_for_gantt)
        view.blend_sequence_table.item(0, 3).setText('0.2')
        view.generate_manual_blend_plan = Mock(side_effect=ManualBlendPlanningError('Stockpile is depleted'))
        with patch.object(QMessageBox, 'warning'):
            self.assertFalse(view.submit_blend_sequence_table_to_gantt())
        self.assertEqual(accepted, view.stored_blend_sequence_table_for_gantt)

    def test_rounded_microsecond_intervals_validate_exact_overlap(self):
        rows = [dict(**{'Blend ID': '1', 'Start Datetime': '2025-01-01 06:00', 'End Datetime': '2025-01-01 06:00'},
                     _fixed_steady_state=True, _exact_start='2025-01-01T06:00:00.123456', _exact_end='2025-01-01T06:00:10.123456'),
                dict(**{'Blend ID': '2', 'Start Datetime': '2025-01-01 06:00', 'End Datetime': '2025-01-01 06:00'},
                     _fixed_steady_state=True, _exact_start='2025-01-01T06:00:09.123456', _exact_end='2025-01-01T06:00:20.123456')]
        self.assertEqual(1, len(ManualBlendRules.overlapping_blend_bar_conflicts(rows)))
        rows[1]['_exact_start'] = rows[0]['_exact_end']
        self.assertEqual([], ManualBlendRules.overlapping_blend_bar_conflicts(rows))


if __name__ == '__main__':
    unittest.main()
