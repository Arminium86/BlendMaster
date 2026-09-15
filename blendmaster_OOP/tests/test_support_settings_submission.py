"""Support settings save before Workspace data/reconciliation exists."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from GUI.SupportSubmission import submit_settings
from GUI.WorkflowSubmissions import pending, task_status


class SupportSettingsTests(unittest.TestCase):
    def host(self):
        return SimpleNamespace(access_role='support', stockpile_data={},
            capture_data_stream_configuration=Mock(),
            capture_guidance_schedule_controls=Mock(),
            validate_active_ratio_group_for_run=Mock(return_value=(True, '')),
            save_active_scenario_state=Mock())

    def test_settings_save_without_inventory_approval_or_workspace_submission(self):
        for page, owner in (('guidance_settings', 'guidance_schedules'), ('data_streams', 'grade_reconciliation')):
            host = self.host()
            with patch('classes.ApprovedReconciliation.require_approved', side_effect=AssertionError('approval required')):
                self.assertTrue(submit_settings(host, page))
            self.assertEqual(task_status(host, page), 'ready')
            self.assertEqual(pending(host)[0], owner)
            host.save_active_scenario_state.assert_called_once()
            if page == 'guidance_settings':
                host.capture_guidance_schedule_controls.assert_called_once_with(refresh_routes=False)

    def test_invalid_settings_do_not_submit_and_planner_cannot_save(self):
        host = self.host()
        host.capture_data_stream_configuration.side_effect = ValueError('invalid category')
        with patch('PyQt5.QtWidgets.QMessageBox.warning'):
            self.assertFalse(submit_settings(host, 'data_streams'))
        host.save_active_scenario_state.assert_not_called()
        self.assertEqual(task_status(host, 'data_streams'), 'resubmit')
        host.access_role = 'planner'
        with self.assertRaises(PermissionError):
            submit_settings(host, 'guidance_settings')

    def test_mapping_submission_without_inventory_does_not_prepare_or_enable_workspace(self):
        from GUI.InitialiseGUI import UserInputs
        host = self.host()
        host.capture_map_fields_table = Mock()
        host.apply_canonical_field_mappings = Mock()
        host.apply_grade_streams_to_inventory = Mock()
        host.prepare_data_streams = Mock()
        host.show_page = Mock()
        host.data_streams_tab_index = 'data_streams'
        host.set_page_enabled = Mock()
        with patch('GUI.InitialiseGUI.QTimer.singleShot') as schedule:
            UserInputs.handle_map_fields_submit(host)
        host.apply_canonical_field_mappings.assert_not_called()
        host.apply_grade_streams_to_inventory.assert_not_called()
        schedule.assert_not_called()
        self.assertEqual(task_status(host, 'map_fields'), 'ready')
        self.assertNotIn(('data_streams', True), [call.args for call in host.set_page_enabled.call_args_list])
