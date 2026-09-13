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
        host.stockpile_data = {'SP': source()}
        host.continuous_assay_settings = dict(enabled=True)
        host.site_scenarios = {'test': dict(stockpile_data=host.stockpile_data,
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

    def test_scheduled_preparation_uses_the_same_compact_detached_request(self):
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
        write.assert_called_once()
        host.deleteLater()
