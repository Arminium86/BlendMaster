import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt5.QtWidgets import QApplication, QPushButton, QVBoxLayout
from GUI.WorkflowTaskControls import WorkflowTaskControls
from GUI.WorkflowViews import WorkflowViews
from GUI.WorkflowSubmissions import submitted, return_to, task_status, edited, pending
from GUI.WorkflowActuals import ensure
from classes.DestinationBuildOrder import digest
from tests import test_workflow_navigation as navigation


class TaskControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def host(self, role='support'):
        host = navigation.WorkflowNavigationTests().progression_host(role)
        self.addCleanup(host.deleteLater)
        host._workflow_views = WorkflowViews(host)
        host.run_background_task = Mock()
        host._workflow_task_controls = WorkflowTaskControls(host)
        host._workflow_task_controls.timer.stop()
        return host, host._workflow_task_controls

    def button(self, host, page, action):
        widget = host.page_widgets[page]
        layout = QVBoxLayout(widget)
        button = QPushButton('Submit', widget)
        layout.addWidget(button)
        button.clicked.connect(action)
        return button

    def test_workspace_run_advances_for_both_roles_and_honours_button_validation(self):
        for role in ('support', 'planner'):
            host, controls = self.host(role)
            button = self.button(host, 'site_configuration', lambda: host.advance_workspace('site_configuration'))
            host.show_page('site_configuration')
            button.setEnabled(False)
            controls.execute()
            self.assertEqual(controls.current_page(), 'site_configuration')
            button.setEnabled(True)
            controls.execute()
            self.assertEqual(controls.current_page(), 'guidance_schedules')
            self.assertTrue(button.isHidden())
            self.assertEqual(task_status(host, 'site_configuration'), 'ready')

    def test_support_run_stays_put_including_background_completion(self):
        host, controls = self.host()
        def action():
            host.run_background_task('work', lambda: None, lambda result: host.advance_workspace('site_configuration'))
        self.button(host, 'site_model', action)
        host.show_page('site_model')
        controls.execute()
        complete = controls.host.run_background_task.__wrapped__.call_args.args[2]
        self.assertEqual(controls.current_page(), 'site_model')
        complete(None)
        self.assertEqual(controls.current_page(), 'site_model')
        self.assertEqual(task_status(host, 'site_model'), 'ready')
        self.assertEqual(pending(host)[0], 'site_configuration')

    def test_grade_run_updates_missing_evidence_then_requires_another_run_to_submit(self):
        host, controls = self.host('planner')
        submit = Mock()
        button = self.button(host, 'grade_reconciliation', submit)
        button.setEnabled(False)
        host.refresh_data_streams_button = QPushButton('Refresh factors', host)
        host.request_manual_grade_reconciliation = Mock()
        host.show_page('grade_reconciliation')
        controls.execute()
        host.request_manual_grade_reconciliation.assert_called_once()
        submit.assert_not_called()
        button.setEnabled(True)
        controls.execute()
        submit.assert_called_once()

    def test_planner_cannot_run_a_support_task_and_busy_run_cannot_repeat(self):
        host, controls = self.host('planner')
        action = Mock()
        self.button(host, 'site_model', action)
        host.show_page('site_model', force=True)
        controls.execute()
        action.assert_not_called()
        self.button(host, 'site_configuration', action)
        host.show_page('site_configuration')
        host._support_actuals_pending = True
        controls.execute()
        action.assert_not_called()

    def test_edit_invalidates_receipts_without_navigating_or_discarding_evidence(self):
        host, controls = self.host()
        host.grade_reconciliation_registry = {'retained': True}
        submitted(host, 'stockpile_inventories')
        submitted(host, 'calendar')
        host.show_page('stockpile_inventories')
        edited(host, 'stockpile_inventories')
        self.assertEqual(controls.current_page(), 'stockpile_inventories')
        self.assertEqual(task_status(host, 'stockpile_inventories'), 'next')
        self.assertEqual(task_status(host, 'calendar'), 'resubmit')
        self.assertEqual(host.grade_reconciliation_registry, {'retained': True})
        controls.refresh()
        tabs, index = host.page_locations['calendar']
        self.assertEqual(tabs.tabBar().tabData(index), 'resubmit')

    def test_empty_input_views_disabled_and_loaded_views_enabled(self):
        host, controls = self.host()
        views = host._workflow_views
        views.apply_input_results()
        for page in ('database_view', 'expit_sequence', 'opf_production_report'):
            self.assertFalse(host.is_page_enabled(page))
        host.database_view_rows = [{'source': 'A'}]
        host.expit_sequence_snapshot = {'actual_movements': [{'source': 'A'}]}
        host.opf_production_report = SimpleNamespace(snapshot={'records': [{'grade': 60}]})
        views.apply_input_results()
        for page in ('database_view', 'expit_sequence', 'opf_production_report'):
            self.assertTrue(host.is_page_enabled(page))

    def test_optional_tasks_hide_and_return_when_their_inputs_are_selected(self):
        host, controls = self.host()
        host.stockpile_data_AMT_column = {}
        host.updated_stockpile_data = {}
        host.file_path_choice = ''
        controls.refresh()
        for page in ('amt_stockpiles', 'destination_progress'):
            tabs, index = host.page_locations[page]
            self.assertFalse(tabs.isTabVisible(index))
        host.updated_stockpile_data = {'A': {'amt': True, 'balance': 100}}
        host.file_path_choice = '2wp.csv'
        controls.refresh()
        for page in ('amt_stockpiles', 'destination_progress'):
            tabs, index = host.page_locations[page]
            self.assertTrue(tabs.isTabVisible(index))


class ActualDependencyTests(unittest.TestCase):
    def host(self):
        return SimpleNamespace(access_role='planner', transport_settings={'tipping_points': {'C': {'enabled': True}}},
            active_scenario_id='one', start_time_choice='start', mine_input_choice='mine',
            transport_opening_history={'retained': True}, run_background_task=Mock(), save_active_scenario_state=Mock())

    @patch('GUI.WorkflowActuals.WorkflowMessageBox.information')
    @patch('GUI.MaterialFlowIntegration.points_for_gui', return_value=[{'name': 'C'}])
    @patch('GUI.WorkflowActuals.TransportOpeningHistory')
    def test_refresh_uses_saved_settings_then_waits_at_grade_review(self, service, points, message):
        host = self.host()
        service.return_value.request.return_value = {'request': 'new'}
        result = {'records': [{'source': 'new'}], 'data_signature': 'new'}
        service.return_value.fetch.return_value = result
        self.assertTrue(ensure(host))
        self.assertTrue(ensure(host))
        self.assertEqual(host.run_background_task.call_count, 1)
        _, work, done, failed = host.run_background_task.call_args.args
        with patch('GUI.WorkflowSubmissions.return_to') as redirect:
            done(work())
            self.assertEqual(redirect.call_args.args[1], 'grade_reconciliation')
        self.assertIs(host.transport_opening_history, result)
        self.assertFalse(host._support_actuals_pending)
        host.save_active_scenario_state.assert_called_once()

    @patch('GUI.WorkflowActuals.WorkflowMessageBox.information')
    @patch('GUI.MaterialFlowIntegration.points_for_gui', return_value=[])
    @patch('GUI.WorkflowActuals.TransportOpeningHistory')
    def test_cached_request_reused_and_stale_or_failed_responses_retain_previous_data(self, service, points, message):
        host = self.host()
        service.return_value.request.return_value = {'key': 1}
        cached = {'request': {'key': 1}, 'records': [], 'data_signature': digest([])}
        host.transport_opening_history = cached
        self.assertFalse(ensure(host))
        host.run_background_task.assert_not_called()
        self.assertTrue(ensure(host, force=True))
        done, failed = host.run_background_task.call_args.args[2:]
        failed('offline')
        self.assertIs(host.transport_opening_history, cached)
        self.assertFalse(host._support_actuals_pending)
        self.assertTrue(ensure(host, force=True))
        host.active_scenario_id = 'two'
        done({'records': ['stale']})
        self.assertIs(host.transport_opening_history, cached)


if __name__ == '__main__':
    unittest.main()
