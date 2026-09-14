import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pickle
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PyQt5.QtWidgets import QApplication

from GUI.WorkflowSubmissions import return_to, pending, can_submit, order
from GUI.WorkflowViews import WorkflowViews
from GUI.SupportSubmission import prepare
from tests import test_workflow_navigation as navigation


class WorkflowResubmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def host(self, role='planner', post=False):
        host = navigation.WorkflowNavigationTests().progression_host(role)
        self.addCleanup(host.deleteLater)
        host.updated_stockpile_data = {'A': {'amt': True, 'balance': 100}}
        host.AMT_stockpile_data = {'A': [{'HEX': 'a', 'FINAL_WMT': 100}]}
        host.hex_sequence_table = [{'footprint': 'A', 'hex': 'chunk', 'balance': 100, 'member_hexes': ['a']}]
        host.multi_feed_configuration = {'amt_reconcile_after_chunking': post}
        host.grade_reconciliation_registry = {'sources': {'approved': {'detail': {'factors': 'unchanged'}}}}
        host._combined_opf_profile_cache = ('valid', {'prepared': True})
        host.active_scenario_id = 'site'
        host.site_scenarios = {'site': {}}
        return host

    def test_redirect_locks_downstream_tasks_and_reuses_evidence_in_both_orders(self):
        for role in ('planner', 'support'):
            for post in (False, True):
                with self.subTest(role=role, post=post):
                    host = self.host(role, post)
                    registry, chunks, cache = host.grade_reconciliation_registry, host.hex_sequence_table, host._combined_opf_profile_cache
                    host.show_page('calendar')
                    return_to(host, 'grade_reconciliation', 'New source needs approval')
                    self.assertEqual(pending(host)[0], 'grade_reconciliation')
                    self.assertEqual('amt_stockpiles' in pending(host), not post)
                    views = WorkflowViews(host)
                    for page in pending(host)[1:]:
                        host.set_page_enabled(page, True)
                        views.available(page, True)
                        host.show_page(page, force=True)
                        self.assertFalse(host.is_page_enabled(page))
                    self.assertIs(host.workspace_tabs.currentWidget(), host.page_widgets['grade_reconciliation'])
                    with patch('GUI.WorkflowMessages.WorkflowMessageBox.information'), patch('GUI.OPFProfileLoading.ensure') as expensive:
                        self.assertFalse(host.store_calendar_inputs())
                        expensive.assert_not_called()
                    self.assertIs(host.grade_reconciliation_registry, registry)
                    self.assertIs(host.hex_sequence_table, chunks)
                    self.assertIs(host._combined_opf_profile_cache, cache)
                    with patch('GUI.WorkflowViews.input_readiness', return_value={'destination_progress': (True, '')}):
                        for page in list(pending(host)):
                            host.advance_workspace(page)
                    self.assertFalse(pending(host))
                    self.assertTrue(host.is_page_enabled('calendar'))
                    self.assertIs(host.grade_reconciliation_registry, registry)

    def test_return_to_an_earlier_task_survives_save_restore_and_result_refresh(self):
        from GUI.WorkflowDependencies import input_revision
        host = self.host()
        original_revision = input_revision(host)
        return_to(host, 'grade_reconciliation', 'Update grades')
        resubmission_revision = input_revision(host)
        self.assertNotEqual(original_revision, resubmission_revision)
        host.advance_workspace('grade_reconciliation')
        self.assertEqual(input_revision(host), resubmission_revision)
        return_to(host, 'stockpile_inventories', 'Select new source')
        self.assertEqual(pending(host)[0], 'stockpile_inventories')
        saved = pickle.loads(pickle.dumps(host.site_scenarios['site']))
        restored = self.host()
        restored.workflow_submission_state = saved['workflow_submission_state']
        restored.restore_page_states({page: True for page in order(restored)})
        view = WorkflowViews(restored)
        view.results = {'optimised': True, 'manual': True}
        view.apply_results()
        self.assertFalse(restored.is_page_enabled('calendar'))
        self.assertFalse(restored.is_page_enabled('optimised_blend_sequence'))
        self.assertTrue(restored.is_page_enabled('stockpile_inventories'))

    def test_support_redirect_uses_workspace_owner_and_optional_steps_stay_skipped(self):
        host = self.host()
        host.updated_stockpile_data = {}
        host.stockpile_data_AMT_column = {}
        host.file_path_choice = ''
        return_to(host, 'data_streams', 'Apply saved streams')
        self.assertEqual(pending(host)[:2], ['grade_reconciliation', 'product_targets'])
        self.assertNotIn('amt_stockpiles', pending(host))
        self.assertNotIn('destination_progress', pending(host))
        self.assertIs(host.tabs.currentWidget(), host.workspace_navigation_page)
        self.assertTrue(can_submit(host, 'grade_reconciliation'))

    def test_reviewing_an_earlier_tab_without_a_dependency_prompt_does_not_invalidate(self):
        host = self.host()
        host.show_page('calendar')
        host.show_page('grade_reconciliation')
        self.assertFalse(pending(host))
        self.assertTrue(host.is_page_enabled('calendar'))

    def test_background_dependency_error_returns_to_its_task_and_invalidates_later_submissions(self):
        from GUI.BackgroundTasks import Worker
        from classes.PlanningPrerequisites import PlanningPrerequisiteError
        host = self.host()
        def fail():
            raise PlanningPrerequisiteError('Select the new source', 'stockpile_inventories')
        worker = Worker(host, fail, ':memory:')
        worker.run()
        with patch('GUI.InitialiseGUI.QMessageBox.critical'):
            host.show_error_popup(worker.error)
        self.assertEqual(pending(host)[0], 'stockpile_inventories')
        self.assertFalse(host.is_page_enabled('calendar'))
        self.assertNotIn('Traceback', worker.error['message'])

    def test_selecting_an_amt_during_repair_adds_its_required_submission(self):
        host = self.host()
        host.updated_stockpile_data = {}
        return_to(host, 'stockpile_inventories', 'Select sources')
        self.assertNotIn('amt_stockpiles', pending(host))
        host.updated_stockpile_data = {'A': {'amt': True, 'balance': 100}}
        host.advance_workspace('stockpile_inventories')
        self.assertIn('amt_stockpiles', pending(host))

    def test_saved_support_submission_is_automatic_and_reused_until_settings_change(self):
        from tests.test_combined_opf_reconciliation import source_state
        from classes.FieldDefinitions import legacy_aps_mappings
        state = source_state()
        state.update(access_role='planner', selected_data_stream='adjusted_rom',
                     data_stream_planning_categories={'rom': 'Custom feed'},
                     grade_reconciliation_registry={'sources': {'accepted': {'detail': {}}}})
        host = SimpleNamespace(**state)
        approved = deepcopy(host.grade_reconciliation_registry)
        with patch('classes.FieldDefinitions.legacy_aps_mappings', wraps=legacy_aps_mappings) as mapping:
            for task in ('grade_reconciliation', 'amt_stockpiles', 'calendar', 'calendar'):
                prepare(host, task)
            mapping.assert_called_once()
            host.field_mappings[0]['source_field'] = 'Changed source field'
            prepare(host, 'calendar')
            self.assertEqual(mapping.call_count, 2)
        self.assertEqual(host.grade_reconciliation_registry, approved)
        self.assertEqual(host.selected_data_stream, 'adjusted_rom')
        self.assertEqual(host.data_stream_planning_categories['rom'], 'Custom feed')
        self.assertTrue(host.aps_grade_field_mappings)

    @patch('GUI.InitialiseGUI.QMessageBox.warning')
    def test_calendar_redirect_requires_real_submissions_without_another_factor_search(self, warning):
        from PyQt5.QtWidgets import QPushButton
        from GUI.ReconciliationReview import ReconciliationReview
        from tests.test_manual_grade_reconciliation import state
        from tests.test_amt_reconciliation_grain import submit
        from classes.ReconciliationFactorResolver import ReconciliationFactorResolver
        from classes.ApprovedReconciliation import missing_sources
        host = self.host()
        values = state()
        submit(values)
        host.__dict__.update(values)
        host.stockpile_data_AMT_column = {'SP': True}
        host.reconciliation_review = ReconciliationReview(host)
        host.reconciliation_review.set_context(values['reconciliation_settings'], values['opf_input_choice'], ['FB'])
        host.data_streams_submit_button = QPushButton(host)
        host.show_error_popup = Mock()
        host.run_background_task = lambda message, work, done, *args, **kwargs: done(work())
        host.data_stream_pending_build_targets = {}
        host.data_stream_reconciliation = Mock()
        host.capture_recon_factor_table = Mock()
        host.capture_data_stream_configuration = Mock()
        for name in ('data_streams_tab_index', 'stockpile_tab_index', 'define_fields_tab_index',
                     'map_fields_tab_index', 'guidance_schedules_tab_index'):
            setattr(host, name, name)
        with patch('GUI.InitialiseGUI.QMessageBox.information'):
            host.store_calendar_inputs()
        self.assertEqual(pending(host)[0], 'grade_reconciliation')
        host.start_confidence_search_review()
        self.assertFalse(missing_sources(vars(host)))
        approved = deepcopy(host.grade_reconciliation_registry)
        with patch.object(ReconciliationFactorResolver, 'resolve_source', side_effect=AssertionError('repeated search')):
            host.handle_data_streams_submit()
            self.assertEqual(pending(host)[0], 'amt_stockpiles')
            host.store_AMT_chunk_settings = Mock(return_value=True)
            host.validate_AMT_participation = Mock()
            self.assertTrue(host.store_hex_sequence_table())
            self.assertEqual(pending(host)[0], 'product_targets')
            host.store_product_targets = Mock(return_value=True)
            with patch('GUI.WorkflowViews.input_readiness', return_value={'destination_progress': (True, '')}):
                host.handle_product_targets_submit()
            host.destination_allocation_context = Mock(return_value={'accepted': True})
            host.continue_from_destination_progress()
            host.store_solver_config_inputs = Mock(return_value=True)
            host.handle_decision_levers_submit()
            self.assertEqual(pending(host)[0], 'calendar')
            host.validate_active_ratio_group_for_run = Mock(return_value=(False, 'stop before solving'))
            with patch('GUI.InitialiseGUI.QMessageBox.information'):
                host.store_calendar_inputs()
            host.validate_active_ratio_group_for_run.assert_called_once()
        self.assertEqual(host.grade_reconciliation_registry, approved)
        host.show_error_popup.assert_not_called()
        warning.assert_not_called()
