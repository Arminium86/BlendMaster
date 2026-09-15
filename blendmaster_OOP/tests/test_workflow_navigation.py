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

    def test_support_editors_survive_empty_workspace_and_legacy_disabled_gates(self):
        from GUI.WorkflowNavigation import SUPPORT_CONFIGURATION
        from GUI.InputPreparationLocks import acquire, release
        for role in ('support', 'planner'):
            h = self.host(role)
            h.workflow_submission_state = {'required': list(WORKSPACE)}
            h.restore_page_states({page: False for page in SUPPORT_CONFIGURATION})
            for page in SUPPORT_CONFIGURATION:
                self.assertEqual(h.is_page_enabled(page), role == 'support', page)
            self.assertFalse(h.is_page_enabled('grade_reconciliation'))
            if role == 'support':
                held = acquire(h, readable_results=True)
                h.set_page_enabled('data_streams', False)
                self.assertFalse(h.page_widgets['data_streams'].isEnabled())
                release(h, held)
                self.assertTrue(h.page_widgets['data_streams'].isEnabled())
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

    def test_hidden_group_changes_do_not_start_page_refreshes(self):
        h = self.host('support')
        h.show_page('calendar')
        h.handle_main_tab_changed = Mock()
        h.results_tabs.setCurrentWidget(h.page_widgets['expit_sequence'])
        h.handle_main_tab_changed.assert_not_called()
        h.tabs.setCurrentWidget(h.results_navigation_page)
        h.handle_main_tab_changed.assert_called_once_with('expit_sequence')
        h.deleteLater()

    def progression_host(self, role):
        h = self.host(role)
        for name, page in (('product_build_tab_index', 'product_targets'), ('decision_levers_tab_index', 'decision_levers'),
                           ('calendar_tab_index', 'calendar'), ('database_view_tab_index', 'database_view')):
            setattr(h, name, page)
        for name in ('populate_product_build_table', 'activate_manual_setup_tab', 'prepare_data_streams',
                     'load_solver_config_inputs', 'setup_calendar', 'sync_destination_progress_context',
                     'save_active_scenario_state'):
            setattr(h, name, Mock())
        h.stockpile_data_AMT_column = {'A': True}
        h.file_path_choice = '2wp.csv'
        return h

    def test_both_roles_progress_through_workspace_without_entering_support_or_views(self):
        for role in ('planner', 'support'):
            with self.subTest(role=role), patch('GUI.WorkflowViews.input_readiness', return_value={'destination_progress': (True, '')}):
                h = self.progression_host(role)
                for current, expected in zip(WORKSPACE, WORKSPACE[1:]):
                    h.show_page(current)
                    self.assertEqual(h.advance_workspace(current), expected)
                    self.assertIs(h.tabs.currentWidget(), h.workspace_navigation_page)
                    self.assertIs(h.workspace_tabs.currentWidget(), h.page_widgets[expected])
                self.assertIsNone(h.advance_workspace(WORKSPACE[-1]))
                h.prepare_data_streams.assert_called_once_with()
                if role == 'support':
                    h.show_page('define_fields')
                    self.assertIs(h.tabs.currentWidget(), h.setup_navigation_page)
                h.deleteLater()

    def test_optional_inputs_are_skipped_without_skipping_workspace_review_steps(self):
        for role in ('planner', 'support'):
            h = self.progression_host(role)
            h.stockpile_data_AMT_column = {}
            h.file_path_choice = ''
            self.assertEqual(h.advance_workspace('grade_reconciliation'), 'product_targets')
            self.assertEqual(h.advance_workspace('product_targets'), 'decision_levers')
            h.stockpile_data_AMT_column = {'A': True}
            h.AMT_stockpile_data = {'A': [{'FINAL_WMT': 0, 'RAW_WMT': 0, 'INVENTORY_BALANCE_WMT': 100}]}
            self.assertEqual(h.next_workspace_page('grade_reconciliation'), 'product_targets')
            h.deleteLater()

    def test_background_and_project_restoration_do_not_advance_the_user(self):
        h = self.progression_host('support')
        for flag in ('project_load_restore_in_progress', 'scenario_switch_in_progress', 'project_load_keep_site_configuration_visible'):
            setattr(h, flag, True)
            self.assertIsNone(h.advance_workspace('amt_stockpiles'))
            setattr(h, flag, False)
        h.site_workflow_controller = SimpleNamespace(active=True)
        self.assertIsNone(h.advance_workspace('calendar'))
        h.activate_manual_setup_tab.assert_not_called()
        h.deleteLater()

    def test_product_submission_stays_on_invalid_inputs_and_then_opens_destination_review(self):
        for role in ('planner', 'support'):
            h = self.progression_host(role)
            h.show_page('product_targets')
            h.store_product_targets = Mock(return_value=False)
            h.handle_product_targets_submit()
            h.save_active_scenario_state.assert_not_called()
            self.assertIs(h.workspace_tabs.currentWidget(), h.page_widgets['product_targets'])
            h.store_product_targets.return_value = True
            with patch('GUI.WorkflowViews.input_readiness', return_value={'destination_progress': (True, '')}):
                h.handle_product_targets_submit()
            h.save_active_scenario_state.assert_called_once()
            self.assertIs(h.workspace_tabs.currentWidget(), h.page_widgets['destination_progress'])
            h.deleteLater()

    def test_destination_continue_requires_current_review_and_never_refreshes_implicitly(self):
        from GUI.DestinationProgressSetup import DestinationProgressSetup
        h = self.progression_host('support')
        h.destination_progress = DestinationProgressSetup(h, run_async=Mock())
        h.destination_progress.continueRequested.connect(h.continue_from_destination_progress)
        h.destination_allocation_context = Mock(return_value=None)
        h.show_page('destination_progress')
        h.destination_progress.continue_button.click()
        self.assertIs(h.workspace_tabs.currentWidget(), h.page_widgets['destination_progress'])
        h.save_active_scenario_state.assert_not_called()
        h.destination_allocation_context.return_value = {'current': True}
        h.destination_progress.continue_button.click()
        self.assertIs(h.workspace_tabs.currentWidget(), h.page_widgets['decision_levers'])
        h.destination_progress.run_async.assert_not_called()
        h.deleteLater()

    def test_amt_submission_advances_only_after_profile_publication(self):
        h = self.progression_host('planner')
        h.show_page('amt_stockpiles')
        h.draw_AMT_map = SimpleNamespace(return_hex_sequence=lambda: [{'footprint': 'A', 'hex': 'chunk', 'balance': 100}])
        h.validate_AMT_participation = Mock()
        h.prune_zeroed_amt_chunks = Mock()
        h.store_AMT_chunk_settings = Mock(return_value=True)
        h.apply_cb_split_to_amt_chunks = Mock()
        h.populate_total_AMT_stockpile_balances = Mock()
        with patch('GUI.OPFProfileLoading.ensure', return_value=True) as ensure:
            self.assertTrue(h.store_hex_sequence_table())
            self.assertIs(h.workspace_tabs.currentWidget(), h.page_widgets['amt_stockpiles'])
            h.save_active_scenario_state.assert_not_called()
            ensure.call_args.args[1]()
        self.assertIs(h.workspace_tabs.currentWidget(), h.page_widgets['product_targets'])
        h.save_active_scenario_state.assert_called_once()
        h.deleteLater()

    def test_manual_sequence_continuation_selects_the_manual_plan_for_each_site_mode(self):
        for mode in ('single', 'multi_tipping_point', 'combined_opf'):
            h = self.progression_host('support')
            h.multi_feed_configuration = {'mode': mode}
            h.blend_plan_workflow_tabs = QTabWidget()
            h.blend_plan_workflow_tabs.addTab(QWidget(), 'Optimised')
            h.blend_plan_page, h.manual_operational_blend_plans = QWidget(), QWidget()
            h.blend_plan_workflow_tabs.addTab(h.blend_plan_page, 'Manual')
            h.blend_plan_workflow_tabs.addTab(h.manual_operational_blend_plans, 'Manual per point')
            h.advance_workspace('blend_sequence')
            expected = h.blend_plan_page if mode == 'single' else h.manual_operational_blend_plans
            self.assertIs(h.blend_plan_workflow_tabs.currentWidget(), expected)
            h.blend_plan_workflow_tabs.deleteLater()
            h.deleteLater()
