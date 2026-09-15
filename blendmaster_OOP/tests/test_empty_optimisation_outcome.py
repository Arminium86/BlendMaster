import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from GUI.InitialiseGUI import UserInputs
from GUI.WorkflowSubmissions import pending, task_status


class EmptyOptimisationOutcomeTests(unittest.TestCase):
    @patch('GUI.OptimisationOutcome.QMessageBox.information')
    def test_empty_run_keeps_calendar_unsubmitted_and_does_not_seed_manual_plan(self, notice):
        host = SimpleNamespace(workflow_submission_state={'completed': ['calendar', 'optimised_blend_sequence']},
            optimisation_reuse_receipt={'status': 'complete'}, _pending_optimisation_signature='old',
            calendar_workflow_status=Mock(), project_load_continuation_pending=False)
        for name in ('refresh_sqlite_reports', 'refresh_optimisation_plan_selectors', 'save_active_scenario_state',
                     'show_page', 'set_page_enabled', 'advance_workspace', 'prepopulate_manual_from_optimised_result'):
            setattr(host, name, Mock())
        periods = SimpleNamespace(run_outcome={'status': 'partial', 'result_row_count': 0,
                                               'message': 'No feasible blend found at state 0.'})
        UserInputs.finish_run_program(host, periods)
        self.assertEqual(host.last_run_outcome['status'], 'failed')
        self.assertEqual(pending(host)[0], 'calendar')
        self.assertNotEqual(task_status(host, 'calendar'), 'ready')
        self.assertIsNone(host.optimisation_reuse_receipt)
        host.advance_workspace.assert_not_called()
        host.prepopulate_manual_from_optimised_result.assert_not_called()
        self.assertIn('No optimised', notice.call_args.args[1])
        self.assertNotIn('successfully solved portion', notice.call_args.args[2])

    @patch('GUI.OptimisationOutcome.QMessageBox.information')
    @patch('GUI.WorkflowSubmissions.return_to')
    def test_empty_automated_run_finishes_as_failure(self, redirect, notice):
        from GUI.OptimisationOutcome import no_plan
        host = SimpleNamespace(last_run_outcome={'result_row_count': 0},
            site_workflow_controller=SimpleNamespace(active=True, fail=Mock()),
            refresh_sqlite_reports=Mock(), refresh_optimisation_plan_selectors=Mock(),
            save_active_scenario_state=Mock(), project_load_continuation_pending=True,
            finish_project_load_ui=Mock())
        no_plan(host)
        host.site_workflow_controller.fail.assert_called_once()
        host.finish_project_load_ui.assert_called_once_with(success=False)
