import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
from GUI.OptimisationReuse import signature, reusable, begin, completed
from GUI.InitialiseGUI import UserInputs


class OptimisationReuseTests(unittest.TestCase):
    def host(self):
        host = SimpleNamespace(active_scenario_id='site', start_time_choice='2026-09-15',
            calendar_inputs={'rate': {'Period_1': 100}, 'site_context': {
                'transport_opening_history': {'records': [{'tonnes': 20}]},
                'continuous_assay_state': {'profiles': {'OPF': {'fe': 60}}}}},
            stockpile_data={'A': {'balance': 100, 'grade_streams': {'rom': {'fe': 60}}}},
            hex_sequence_table_argument=[{'hex': 'A1', 'sequence': 1}], solver_config={'gap': .1})
        host.included_stockpile_data = lambda: host.stockpile_data
        return host

    def test_effective_changes_invalidate_but_submissions_and_observation_times_do_not(self):
        host = self.host()
        initial = signature(host)
        host.workflow_submission_state = {'revision': 100, 'completed': ['calendar']}
        host.stockpile_data['A']['snapshot_datetime'] = 'later'
        self.assertEqual(initial, signature(host))
        changes = [lambda h: h.stockpile_data['A'].update(balance=101),
                   lambda h: h.stockpile_data['A']['grade_streams']['rom'].update(fe=61),
                   lambda h: h.stockpile_data.update(B={'balance': 5}),
                   lambda h: h.hex_sequence_table_argument[0].update(sequence=2),
                   lambda h: h.calendar_inputs['rate'].update(Period_1=101),
                   lambda h: h.solver_config.update(gap=.2),
                   lambda h: h.calendar_inputs['site_context']['transport_opening_history']['records'][0].update(tonnes=21),
                   lambda h: h.calendar_inputs['site_context']['continuous_assay_state']['profiles']['OPF'].update(fe=61)]
        for change in changes:
            candidate = self.host()
            change(candidate)
            self.assertNotEqual(initial, signature(candidate))

    @patch('GUI.WorkflowViews.result_presence', return_value={'optimised': True})
    def test_only_completed_runs_with_saved_results_are_reusable(self, presence):
        host = self.host()
        current = signature(host)
        self.assertFalse(reusable(host, current))
        begin(host, current)
        self.assertFalse(reusable(host, current))
        completed(host, {'status': 'complete'})
        self.assertTrue(reusable(host, current))
        presence.return_value = {'optimised': False}
        self.assertFalse(reusable(host, current))
        presence.return_value = {'optimised': True}
        for outcome in ({'status': 'failed'}, {'status': 'partial'}, {'status': 'complete', 'partial_plan_restored': True}):
            begin(host, current)
            completed(host, outcome)
            self.assertFalse(reusable(host, current))
        begin(host, current)
        completed(host, {})
        self.assertTrue(reusable(host, current))
        begin(host, current)  # A failed/aborted replacement cannot reuse an older success.
        self.assertFalse(reusable(host, current))

    def test_full_dataframe_content_is_hashed_including_rows_hidden_by_repr(self):
        host = self.host()
        data = pd.DataFrame({'tonnes': range(1000)})
        host.calendar_inputs['site_context']['history'] = data
        before = signature(host)
        data.loc[500, 'tonnes'] = 9999
        self.assertNotEqual(before, signature(host))

    def test_actual_movement_dates_and_lineage_dates_remain_significant(self):
        host = self.host()
        host.database_view_expit_payload_transactions = [{'transaction_datetime': '2026-09-15', 'tonnes': 10}]
        before = signature(host)
        host.database_view_expit_payload_transactions[0]['transaction_datetime'] = '2026-09-16'
        self.assertNotEqual(before, signature(host))
        host.stockpile_data['A']['lineage'] = {'as_of': '2026-09-15'}
        before = signature(host)
        host.stockpile_data['A']['lineage']['as_of'] = '2026-09-16'
        self.assertNotEqual(before, signature(host))

    @patch('classes.ApprovedReconciliation.require_approved')
    @patch('GUI.OPFProfileLoading.ensure', return_value=False)
    @patch('GUI.WorkflowActuals.ensure', return_value=False)
    @patch('GUI.WorkflowViews.result_presence', return_value={'optimised': True})
    def test_calendar_repeat_keeps_outputs_and_does_not_launch_solver(self, *mocks):
        host = self.host()
        context = deepcopy(host.calendar_inputs['site_context'])
        host.active_site_context = lambda: context
        host.validate_active_ratio_group_for_run = lambda: (True, '')
        host.current_multi_feed_configuration = lambda: {'mode': 'single'}
        host.main_table = Mock()
        host.main_table.columnCount.return_value = 2
        host.main_table.rowCount.return_value = 1
        host.main_table.horizontalHeaderItem.return_value.text.return_value = 'Period_1'
        host.main_table.item.return_value.text.return_value = 'Rate'
        host.main_table.item.return_value.data.return_value = None
        host.get_main_table_cell_text = lambda *args: 100
        host.normalized_calendar_input_value = lambda key, value: value
        host.store_stockpile_constraint_inputs = lambda: True
        host.normalized_solver_config = lambda value: value
        host.planning_period_count = lambda: 1
        host.product_brand_options = lambda: ['FB']
        host.run_program = Mock()
        host.store_calendar_inputs = lambda: UserInputs.store_calendar_inputs(host)
        for name in ('clear_decision_point_output', 'show_error_popup', 'save_active_scenario_state',
                     'update_decision_point_tab_state', 'run_background_task', 'execute_run_program',
                     'finish_run_program', 'handle_run_program_error', 'advance_workspace'):
            setattr(host, name, Mock())
        UserInputs.store_calendar_inputs(host)
        host.run_background_task.assert_called_once()
        completed(host, {'status': 'complete'})
        host.clear_decision_point_output.reset_mock()
        UserInputs.store_calendar_inputs(host)
        host.run_background_task.assert_called_once()
        host.clear_decision_point_output.assert_not_called()
        host.advance_workspace.assert_called_once_with('calendar')
