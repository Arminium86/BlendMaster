"""Polling snapshots stay small and detached without weakening stale-result checks."""
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch
from classes.ContinuousAssays import source_catalog
from setup.ContinuousAssayHistory import request_snapshot
from GUI.ContinuousAssays import ContinuousAssayController
from tests import test_background_tasks as background


class LargeAudit:
    def __deepcopy__(self, memo):
        raise AssertionError('The assay request must not copy the full reconciliation audit')


def source(grade=60):
    return dict(build='BUILD-1', balance=100, grade_streams={'adjusted_product': {'SS': {'fe': grade}}},
                source_properties=dict(modelled_product_dmt=80, modelled_rom_wmt=100),
                reconciliation=LargeAudit())


class ContinuousAssayPollingTests(unittest.TestCase):
    setUpClass = classmethod(background.BackgroundTaskTests.setUpClass.__func__)

    def test_compact_snapshot_preserves_base_and_independent_opf_catalogs(self):
        state = dict(stockpile_data={'SP': source()}, updated_stockpile_data={},
                     hex_sequence_table=[dict(source(), hex='chunk', footprint='SP')])
        profiles = {opf: dict(inventory={'SP': source(grade)},
                    chunks={'chunk': dict(source(grade), hex='chunk', footprint='SP')},
                    factors=LargeAudit()) for opf, grade in (('OPF1', 60), ('OPF2', 62))}
        snapshot = request_snapshot(state, profiles)
        self.assertEqual(source_catalog(snapshot), source_catalog(state))
        for opf, old in profiles.items():
            new = snapshot['_continuous_opf_profiles'][opf]
            catalog = lambda profile: source_catalog(dict(stockpile_data=profile['inventory'],
                hex_sequence_table=list(profile['chunks'].values())))
            self.assertEqual(catalog(new), catalog(old))
        state['hex_sequence_table'][0]['grade_streams']['adjusted_product']['SS']['fe'] = 65
        profiles['OPF2']['chunks']['chunk']['grade_streams']['adjusted_product']['SS']['fe'] = 66
        self.assertEqual(source_catalog(snapshot)['CHUNK']['grades']['SS']['fe'], 60)
        self.assertEqual(snapshot['_continuous_opf_profiles']['OPF2']['chunks']['chunk']['grade_streams']['adjusted_product']['SS']['fe'], 62)
        deepcopy(snapshot)  # No hidden audit payload remains in the detached request.

    def host(self):
        host = background.Host()
        host.mine_input_choice = 'CC'
        host.time_mode_choice = 1
        host.stockpile_data = {'SP': source()}
        host.continuous_assay_settings = dict(enabled=True)
        host.site_scenarios = {'test': dict(stockpile_data=host.stockpile_data,
            time_mode_choice=1,
            continuous_assay_settings=host.continuous_assay_settings, mine_input_choice='CC')}
        host.site_workflow_controller = SimpleNamespace(active=False, batch=None)
        host.current_opf_profiles = Mock(return_value={})
        host.save_active_scenario_state = Mock()
        host.scenario_database_path = Mock(return_value='unused.db')
        host.continuous_assay_panel = SimpleNamespace(show_status=Mock())
        host.run_background_task = Mock()
        return host

    def test_polling_does_not_copy_audits_and_discards_result_after_source_edit(self):
        host = self.host()
        controller = ContinuousAssayController(host)
        controller.timer.stop()
        controller.service.refresh = Mock(return_value=dict(audit=[], checked_at='now'))
        controller.refresh_due()
        self.assertTrue(controller.running)
        _, work, done, _ = host.run_background_task.call_args.args
        host.stockpile_data['SP']['grade_streams']['adjusted_product']['SS']['fe'] = 65
        result = work()
        snapshot = controller.service.refresh.call_args.args[0]
        self.assertEqual(source_catalog(snapshot)['SP']['grades']['SS']['fe'], 60)
        with patch('GUI.ContinuousAssays.write_audit') as write, patch('GUI.WorkflowWorkspace.refresh_context'):
            done(result)
        write.assert_not_called()
        self.assertNotIn('continuous_assay_state', host.site_scenarios['test'])
        self.assertFalse(controller.running)
        host.deleteLater()

    def test_scheduled_preparation_uses_detached_request_and_discards_changed_inputs(self):
        host = self.host()
        controller = ContinuousAssayController(host)
        controller.timer.stop()
        controller.service.refresh = Mock(return_value=dict(audit=[], checked_at='now'))
        controller.prepare_current()
        _, work, done = host.run_background_task.call_args.args
        host.stockpile_data['SP']['grade_streams']['adjusted_product']['SS']['fe'] = 65
        value = work()
        self.assertEqual(source_catalog(controller.service.refresh.call_args.args[0])['SP']['grades']['SS']['fe'], 60)
        with patch('GUI.ContinuousAssays.write_audit') as write:
            done(value)
        write.assert_not_called()
        self.assertIn('discarded', host.continuous_assay_status)
        host.deleteLater()

    def test_poll_uses_current_inputs_without_capturing_the_full_model(self):
        host = self.host()
        host.stockpile_data = {'SP': source(63)}
        host.save_active_scenario_state.side_effect = AssertionError('Whole-model capture can rebuild OPF profiles on the UI thread')
        controller = ContinuousAssayController(host)
        controller.timer.stop()
        controller.service.refresh = Mock(return_value=dict(audit=[], checked_at='now'))
        controller.refresh_due()
        _, work, _, _ = host.run_background_task.call_args.args
        work()
        self.assertEqual(source_catalog(controller.service.refresh.call_args.args[0])['SP']['grades']['SS']['fe'], 63)
        host.current_opf_profiles.assert_not_called()  # No active plan feeds.
        host.deleteLater()

    def test_active_feed_waits_for_background_profiles_before_snapshotting(self):
        host = self.host()
        controller = ContinuousAssayController(host)
        controller.timer.stop()
        with patch('GUI.ContinuousAssays.current_sources', return_value={'OPF1': ['SP']}), patch('GUI.OPFProfileLoading.ensure', return_value=True) as ensure:
            controller.refresh_due()
        ensure.assert_called_once()
        host.save_active_scenario_state.assert_not_called()
        host.current_opf_profiles.assert_not_called()
        host.run_background_task.assert_not_called()
        self.assertFalse(controller.running)
        host.deleteLater()

    def test_disabled_and_set_time_skip_profiles_saves_and_jobs_even_when_forced(self):
        for mode, enabled in ((1, False), (2, True), (None, True)):
            host = self.host()
            host.time_mode_choice = mode
            host.continuous_assay_settings = dict(enabled=enabled)
            controller = ContinuousAssayController(host)
            controller.timer.stop()
            controller.refresh_due(force=True)
            controller.prepare_current()
            host.save_active_scenario_state.assert_not_called()
            host.current_opf_profiles.assert_not_called()
            host.run_background_task.assert_not_called()
            host.deleteLater()

    def test_not_due_skips_profile_lookup_and_model_capture(self):
        import time
        host = self.host()
        controller = ContinuousAssayController(host)
        controller.timer.stop()
        controller.last[host.active_scenario_id] = time.monotonic()
        controller.refresh_due()
        host.save_active_scenario_state.assert_not_called()
        host.current_opf_profiles.assert_not_called()
        host.run_background_task.assert_not_called()
        host.deleteLater()

    def test_mode_change_while_polling_discards_response(self):
        host = self.host()
        controller = ContinuousAssayController(host)
        controller.timer.stop()
        controller.service.refresh = Mock(return_value=dict(audit=[], checked_at='now'))
        controller.refresh_due()
        _, work, done, _ = host.run_background_task.call_args.args
        result = work()
        host.time_mode_choice = 2
        with patch('GUI.ContinuousAssays.write_audit') as write, patch('GUI.WorkflowWorkspace.refresh_context'):
            done(result)
        write.assert_not_called()
        host.deleteLater()
