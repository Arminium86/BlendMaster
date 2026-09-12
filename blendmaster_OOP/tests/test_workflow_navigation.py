import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PyQt5.QtWidgets import QApplication, QMainWindow, QTabWidget, QWidget
from GUI.InitialiseGUI import UserInputs
from GUI.WorkflowNavigation import WORKSPACE, VIEWS, SUPPORT


class WorkflowNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def host(self, role):
        host = UserInputs.__new__(UserInputs)
        QMainWindow.__init__(host)
        host.access_role = role
        host.tabs = QTabWidget(host)
        host.handle_main_tab_changed = lambda page: None
        host.setup_navigation()
        for key in reversed((*WORKSPACE, *VIEWS, *SUPPORT)):
            if key not in host.page_widgets:
                host.register_page(key, host.workspace_tabs, QWidget(), key)
            host.set_page_enabled(key, True)
        return host

    def test_role_visibility_and_order_survive_legacy_page_restore(self):
        for role in ('planner', 'support', 'owner', 'agent'):
            with self.subTest(role=role):
                h = self.host(role)
                h.restore_page_states({'reports': True, 'data_streams': True})
                self.assertEqual(h.tabs.isTabVisible(h.tabs.indexOf(h.setup_navigation_page)), role != 'planner')
                for order, tabs in ((WORKSPACE,h.workspace_tabs), (VIEWS,h.results_tabs), (SUPPORT,h.setup_tabs)):
                    indices = [h.page_locations[key][1] for key in order if key in h.page_locations]
                    self.assertEqual(indices, sorted(indices))
                for key in SUPPORT:
                    tabs, index = h.page_locations[key]
                    self.assertEqual(tabs.isTabVisible(index), role != 'planner' and key != 'reports')
                self.assertTrue(h.is_page_enabled('grade_reconciliation'))
                self.assertTrue(h.is_page_enabled('blend_plan'))
                h.deleteLater()

    def test_forced_navigation_and_support_operations_cannot_bypass_planner_role(self):
        h = self.host('planner')
        h.show_page('calendar')
        previous = h.tabs.currentWidget(), h.workspace_tabs.currentWidget()
        h.show_page('site_model', force=True)
        self.assertEqual((h.tabs.currentWidget(),h.workspace_tabs.currentWidget()), previous)
        for method in ('capture_data_stream_configuration', 'submit_multi_feed_setup'):
            with self.assertRaises(PermissionError):
                getattr(h,method)()
        h.show_page('data_streams')
        self.assertIs(h.workspace_tabs.currentWidget(), h.page_widgets['grade_reconciliation'])
        h.deleteLater()

    def test_returning_to_a_navigation_group_enters_its_selected_page(self):
        h = self.host('support')
        h.show_page('material_flow')
        h.show_page('calendar')
        entered = []
        h.handle_main_tab_changed = entered.append
        h.tabs.setCurrentWidget(h.setup_navigation_page)
        self.assertEqual(entered[-1], 'material_flow')
        h.deleteLater()

    def test_destination_page_reuses_prepared_evidence_and_refreshes_invalid_context(self):
        h = SimpleNamespace(scenario_switch_in_progress=False,
            destination_progress_tab_index='destination', database_view_tab_index='database',
            sqlite_reports_tab_index='reports', sync_destination_progress_context=Mock(),
            destination_progress=SimpleNamespace(snapshot={'accepted': True}, request_refresh=Mock()))
        with patch('GUI.InitialiseGUI.QTimer.singleShot') as schedule:
            UserInputs.handle_main_tab_changed(h, 'destination')
            h.sync_destination_progress_context.assert_called_once()
            schedule.assert_not_called()
            # Synchronising a changed site/start/file clears the accepted snapshot.
            h.sync_destination_progress_context.side_effect = lambda: setattr(h.destination_progress, 'snapshot', None)
            UserInputs.handle_main_tab_changed(h, 'destination')
            schedule.assert_called_once_with(0, h.destination_progress.request_refresh)
