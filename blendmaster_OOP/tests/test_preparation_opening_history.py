"""Prepared publications must carry opening evidence for the current request version."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from GUI.SiteAutomation import SiteWorkflowController


class PreparationOpeningHistoryTests(unittest.TestCase):
    def test_legacy_opening_history_refreshes_before_assay_check_and_stage_completion(self):
        old = {'request': {'version': 1}, 'records': ['old']}
        fresh = {'request': {'version': 2}, 'records': ['actual']}
        host = SimpleNamespace(validate_AMT_participation=Mock(), file_path_choice='',
            included_stockpile_data=Mock(return_value={'SP': {}}), data_stream_target_errors=[],
            mine_input_choice='CC', start_time_choice='2026-08-18 19:51:49',
            transport_settings={'tipping_points': {'OPF01_PC': {'enabled': True,
                'conveyor_capacity_wmt': 250, 'cos_capacity_wmt': 1000}}},
            transport_opening_history=old, transport_setup=SimpleNamespace(show_history=Mock()),
            continuous_assay_controller=SimpleNamespace(prepare_current=Mock()),
            run_background_task=Mock())
        controller = SimpleNamespace(host=host, await_ready=Mock(), fail=Mock())
        service = Mock(request=Mock(return_value={'version': 2}), fetch=Mock(return_value=fresh))
        with patch('GUI.WorkflowDependencies.preparation_issues', return_value=[]), \
                patch('GUI.MaterialFlowIntegration.points_for_gui', return_value=[]), \
                patch('setup.TransportOpeningHistory.TransportOpeningHistory', return_value=service):
            SiteWorkflowController.stage_validate(controller)
            host.continuous_assay_controller.prepare_current.assert_not_called()
            controller.await_ready.assert_not_called()
            _, work, done, failed = host.run_background_task.call_args.args
            done(work())
        self.assertIs(service.fetch.call_args.kwargs['cached'], old)
        self.assertIs(host.transport_opening_history, fresh)
        host.transport_setup.show_history.assert_called_once_with(fresh)
        host.continuous_assay_controller.prepare_current.assert_called_once()
        controller.await_ready.assert_called_once()
        self.assertIs(failed, controller.fail)
