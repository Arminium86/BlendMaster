import unittest
from copy import deepcopy
from unittest.mock import patch, Mock
from types import SimpleNamespace
from PyQt5.QtCore import QThread
from GUI.BackgroundTasks import run
from GUI.OPFProfileLoading import ensure
from classes.CombinedOPFReconciliation import evidence_signature, profile_signature
from tests import test_background_tasks as background
from tests.test_combined_opf_reconciliation import source_state, OPFS


class OPFProfileLoadingTests(unittest.TestCase):
    setUpClass = classmethod(background.BackgroundTaskTests.setUpClass.__func__)
    drain = background.BackgroundTaskTests.drain

    def host(self):
        host = background.Host()
        host.__dict__.update(source_state())
        host.multi_feed_configuration = dict(mode='combined_opf', tipping_points=[dict(opf=opf) for opf in OPFS])
        host.reconciliation_inventory_builds = lambda: ['B1']
        for opf, bundle in host.opf_reconciliation_inputs.items():
            bundle['signature'] = evidence_signature(vars(host), opf, ['B1'])
        host.run_background_task = lambda *args, **kwargs: run(host, *args, **kwargs)
        return host

    def test_profile_work_runs_outside_ui_and_cached_result_continues_on_ui(self):
        host, calls, delivered = self.host(), [], []
        ui = int(QThread.currentThreadId())

        def build(state, opfs, implementation):
            calls.append(int(QThread.currentThreadId()))
            return {'ready': True}

        with patch('GUI.OPFProfileLoading.build_profiles', side_effect=build):
            self.assertTrue(ensure(host, lambda: delivered.append(int(QThread.currentThreadId()))))
            self.assertEqual(delivered, [])
            self.drain(host)
            self.assertFalse(ensure(host, lambda: None))
        self.assertEqual(delivered, [ui])
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0], ui)
        self.assertEqual(host._combined_opf_profile_cache[0], profile_signature(vars(host), OPFS))
        host.deleteLater()

    def test_source_replacement_discards_old_profile_before_continuing(self):
        host, delivered, snapshots = self.host(), [], []

        def build(state, *args):
            snapshots.append(state['updated_stockpile_data']['SP']['balance'])
            return {'balance': snapshots[-1]}

        with patch('GUI.OPFProfileLoading.build_profiles', side_effect=build):
            ensure(host, lambda: delivered.append(host._combined_opf_profile_cache[1]))
            replacement = deepcopy(host.updated_stockpile_data)
            replacement['SP']['balance'] = 200
            host.updated_stockpile_data = replacement
            self.drain(host)
        self.assertEqual(snapshots, [100, 200])
        self.assertEqual(delivered, [{'balance': 200}])
        host.deleteLater()

    def test_plan_requested_during_profile_loading_continues_after_same_worker(self):
        host, completed = self.host(), []
        with patch('GUI.OPFProfileLoading.build_profiles', return_value={'ready': True}) as build:
            self.assertTrue(ensure(host, lambda: completed.append('view')))
            self.assertTrue(ensure(host, lambda: completed.append('calculation')))
            self.drain(host)
        build.assert_called_once()
        self.assertEqual(completed, ['view', 'calculation'])
        self.assertNotIn('_opf_profile_waiters', vars(host))
        host.deleteLater()

    def test_pending_profile_consumers_all_receive_failure_without_calculating(self):
        host, completed, failures = self.host(), Mock(), []
        with patch('GUI.OPFProfileLoading.build_profiles', side_effect=ValueError('missing evidence')):
            ensure(host, completed, on_error=lambda error: failures.append(('view', error)))
            ensure(host, completed, on_error=lambda error: failures.append(('calculation', error)))
            self.drain(host)
        completed.assert_not_called()
        self.assertEqual([name for name, _ in failures], ['view', 'calculation'])
        self.assertFalse(host._opf_profile_preparation_pending)
        host.deleteLater()

    def test_missing_evidence_defers_to_existing_reconciliation_workflow(self):
        host = self.host()
        host.opf_reconciliation_inputs = {}
        self.assertFalse(ensure(host, lambda: self.fail('Unexpected continuation')))
        self.assertEqual(host.background_tasks, [])
        host.deleteLater()

    def test_saved_cache_reopens_without_rebuilding_and_assay_updates_do_not_invalidate_it(self):
        import pickle
        from classes.CombinedOPFReconciliation import SOURCE_FIELDS
        host = self.host()
        with patch('GUI.OPFProfileLoading.build_profiles', return_value={'ready': True}) as build:
            ensure(host, lambda: None)
            self.drain(host)
            keys = (*SOURCE_FIELDS, 'multi_feed_configuration', '_combined_opf_profile_cache')
            saved = pickle.loads(pickle.dumps({key: vars(host).get(key) for key in keys}))
            restored = self.host()
            restored.__dict__.update(saved)
            restored.continuous_assay_state = {'revision': 'new-assay'}
            restored.continuous_assay_settings = {'enabled': False}
            self.assertFalse(ensure(restored, lambda: None))
            self.assertEqual(build.call_count, 1)
            restored.updated_stockpile_data['SP']['FE_ROM'] = 49
            self.assertTrue(ensure(restored, lambda: None))
            self.drain(restored)
            self.assertEqual(build.call_count, 2)
        host.deleteLater()
        restored.deleteLater()

    def test_restore_defers_until_chunks_are_final_then_prepares_once(self):
        host = self.host()
        host._defer_opf_profile_preparation = True
        with patch('GUI.OPFProfileLoading.build_profiles', return_value={'ready': True}) as build:
            self.assertFalse(ensure(host, lambda: None))
            host.hex_sequence_table = [{'hex': 'restored-chunk', 'balance': 100}]
            self.assertFalse(ensure(host, lambda: None))
            build.assert_not_called()
            host._defer_opf_profile_preparation = False
            ensure(host, lambda: None)
            self.drain(host)
            self.assertFalse(ensure(host, lambda: None))
            build.assert_called_once()
            self.assertEqual(build.call_args.args[0]['hex_sequence_table'][0]['hex'], 'restored-chunk')
        host.deleteLater()

    def test_failed_restore_releases_pending_state_before_reporting_error(self):
        from GUI.InitialiseGUI import UserInputs
        host = self.host()
        host.project_load_restore_in_progress = True
        host.finish_project_load_ui = Mock()
        host.show_error_popup = Mock()
        continued = Mock()
        with patch('GUI.OPFProfileLoading.build_profiles', side_effect=ValueError('Invalid saved source')):
            ensure(host, continued, on_error=lambda error: UserInputs.handle_loaded_profile_error(host, error))
            self.drain(host)
        self.assertFalse(host._opf_profile_preparation_pending)
        self.assertFalse(host.project_load_restore_in_progress)
        host.finish_project_load_ui.assert_called_once_with(success=False)
        host.show_error_popup.assert_called_once()
        continued.assert_not_called()
        host.deleteLater()

    def test_loaded_calendar_waits_for_amt_profile_publication(self):
        from GUI.InitialiseGUI import UserInputs
        host = SimpleNamespace(project_load_restore_in_progress=True,
            stockpile_data_AMT_column={'SP': True}, hex_sequence_table=[{'hex': 'chunk'}],
            store_hex_sequence_table=Mock(return_value=True),
            handle_loaded_profile_error=Mock(),
            database_has_saved_optimisation_results=Mock(return_value=False),
            project_load_saved_page_states={'calendar': True}, store_calendar_inputs=Mock(),
            finish_project_load_ui=Mock(), show_page=Mock())
        host.finish_project_load_after_chunks = lambda: UserInputs.finish_project_load_after_chunks(host)
        UserInputs.continue_project_load_after_stockpile_setup(host)
        self.assertTrue(host.project_load_restore_in_progress)
        host.database_has_saved_optimisation_results.assert_not_called()
        host.store_calendar_inputs.assert_not_called()
        host.store_hex_sequence_table.call_args.kwargs['on_complete']()
        self.assertFalse(host.project_load_restore_in_progress)
        host.store_calendar_inputs.assert_not_called()
        host.finish_project_load_ui.assert_called_once_with(success=True)
        host.show_page.assert_called_once_with('calendar', force=True)

    def test_stale_calendar_checkpoint_restores_without_launching_a_calculation(self):
        from GUI.InitialiseGUI import UserInputs
        for status in ('inputs_updated', 'aborted', 'failed'):
            host = SimpleNamespace(project_load_restore_in_progress=True,
                database_has_saved_optimisation_results=Mock(return_value=False),
                project_load_saved_page_states={'calendar': True},
                last_run_outcome={'status': status}, store_calendar_inputs=Mock(),
                finish_project_load_ui=Mock(), show_page=Mock())
            host.finish_project_load_after_chunks = lambda: UserInputs.finish_project_load_after_chunks(host)
            host.handle_loaded_profile_error = Mock()
            UserInputs.finish_project_load_after_chunks(host)
            self.assertFalse(host.project_load_restore_in_progress)
            self.assertFalse(host.project_load_continuation_pending)
            host.store_calendar_inputs.assert_not_called()
            host.finish_project_load_ui.assert_called_once_with(success=True)
            host.show_page.assert_called_once_with('calendar', force=True)
