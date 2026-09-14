from copy import deepcopy
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock
import sqlite3
import unittest

from classes.ContinuousAssayScope import active_sources, current_sources, plan_rows
from classes.ContinuousAssays import apply_event, boundaries, corrected_streams, for_calculation
from setup.ContinuousAssayHistory import ContinuousAssayHistory


def source(build):
    return dict(build=build, balance=1000, grade_streams={'adjusted_product': {'SS': {'fe': 60}}},
                defined_fields={'modelled_rom_wmt': 1000, 'modelled_product_dmt': 800})


def feed(identity='A', **changes):
    return dict(dict(source_id=identity, source=identity, source_type='stockpile', opf='OPF1',
                     parent_stockpile=identity, start_datetime='2026-09-13 08:00',
                     end_datetime='2026-09-13 10:00', source_actual_tonnes=500), **changes)


class ContinuousAssayScopeTests(unittest.TestCase):
    def test_only_positive_active_inventory_sources_are_eligible_with_half_open_time_bounds(self):
        rows = [feed(), feed('CHUNK', source_type='amt'), feed('DIRECT', source_type='grade_block'),
                feed('LATER', start_datetime='2026-09-13 09:01'),
                feed('ENDED', end_datetime='2026-09-13 09:00'),
                feed('EMPTY', source_actual_tonnes=0), feed('BAD', source_actual_tonnes=float('nan'))]
        self.assertEqual(active_sources(rows, '2026-09-13 09:00'), {'OPF1': ['A', 'CHUNK']})

    def test_selected_plan_never_falls_back_to_primary_or_manual(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'scope.db'
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute('CREATE TABLE optimisation_plan_blend_report (plan_id, source_id, opf, source_type, start_datetime, end_datetime, source_actual_tonnes)')
                for plan, name in [('Primary', 'A'), ('Alternative', 'B')]:
                    connection.execute('INSERT INTO optimisation_plan_blend_report VALUES (?,?,?,?,?,?,?)',
                                       (plan, name, 'OPF1', 'stockpile', '2026-09-13 08:00', '2026-09-13 10:00', 500))
            state = dict(time_mode_choice=1, selected_optimisation_plan_id='Alternative')
            self.assertEqual(current_sources(state, path, '2026-09-13 09:00'), {'OPF1': ['B']})
            self.assertEqual(plan_rows(path, 'Missing'), [])
            self.assertEqual(current_sources({**state, 'time_mode_choice': 2}, path, '2026-09-13 09:00'), {})

    def service(self):
        assay = dict(opf='OPF1', brand='SS', period_start='2026-09-13 06:00', period_end='2026-09-13 07:00',
                     sampled_at='2026-09-13 08:00', last_updated='2026-09-13 08:30', dmt=800, grades={'fe': 60.2})
        warehouse = Mock(return_value=dict(status='fresh', records=[assay], request={}))
        service = ContinuousAssayHistory(SimpleNamespace(fetch=warehouse))
        service.actual_feed = Mock(return_value=[dict(SOURCE='BUILD-A', opf='OPF1', time='2026-09-13 06:30', wmt=1000)])
        return service, warehouse

    def state(self):
        return dict(time_mode_choice=1, mine_input_choice='MINE', opf_input_choice='OPF1', crusher_input_choice='PC1',
                    stockpile_data={'A': source('BUILD-A'), 'INACTIVE': source('BUILD-I')},
                    _continuous_plan_rows=[feed()])

    def test_disabled_set_time_and_no_active_blend_do_not_query_the_warehouse(self):
        for changes in ({'time_mode_choice': 2}, {'time_mode_choice': None},
                        {'continuous_assay_settings': {'enabled': False}}, {'_continuous_plan_rows': []}):
            service, warehouse = self.service()
            result = service.refresh({**self.state(), **changes}, '2026-09-13 09:00')
            self.assertEqual(result['timeline'], [])
            warehouse.assert_not_called()
            service.actual_feed.assert_not_called()

    def test_only_active_sources_are_estimated_and_inactive_assay_components_are_withheld(self):
        service, warehouse = self.service()
        state = self.state()
        result = service.refresh(state, '2026-09-13 09:00')
        self.assertEqual(set(result['catalog']), {'A'})
        self.assertEqual(result['active_sources'], ['A'])
        self.assertTrue(result['timeline'])
        self.assertEqual(set(result['timeline'][0]['offsets']['fe']), {'A'})
        service.actual_feed.return_value.append(dict(SOURCE='BUILD-I', opf='OPF1', time='2026-09-13 06:40', wmt=100))
        rejected = service.refresh(state, '2026-09-13 09:00')
        self.assertFalse(rejected['timeline'])
        self.assertTrue(any('attribution' in row.get('reason', '') for row in rejected['audit']))

    def test_saved_corrections_cannot_apply_in_replay_or_after_active_blend_changes(self):
        service, _ = self.service()
        state = self.state()
        bundle = service.refresh(state, '2026-09-13 09:00')
        self.assertTrue(for_calculation(bundle, state['stockpile_data'], [], time_mode=1, active_sources=['A']))
        self.assertFalse(for_calculation(bundle, state['stockpile_data'], [], time_mode=2, active_sources=['A']))
        self.assertFalse(for_calculation(bundle, state['stockpile_data'], [], time_mode=1, active_sources=['INACTIVE']))
        streams = state['stockpile_data']['A']['grade_streams']
        config = dict(time_mode_choice=2, continuous_assay_state=bundle)
        self.assertIs(corrected_streams(streams, 'A', config, '2026-09-13 09:01', 'OPF1', 'SS'), streams)
        self.assertEqual(boundaries(config), [])
        config['time_mode_choice'] = 1
        self.assertTrue(boundaries(config))
        legacy = deepcopy(bundle)
        legacy.pop('active_sources')
        self.assertFalse(for_calculation(legacy, state['stockpile_data'], [], time_mode=1, active_sources=['A']))

    def test_direct_tip_cannot_inherit_an_inventory_correction_with_the_same_name(self):
        service, _ = self.service()
        state = self.state()
        bundle = service.refresh(state, '2026-09-13 09:00')
        streams = state['stockpile_data']['A']['grade_streams']
        event = SimpleNamespace(is_grade_block=True, is_stockpile=False, source_name='A', stockpile='A',
                                grade_streams=streams, source_properties={})
        apply_event(event, dict(time_mode_choice=1, continuous_assay_state=bundle,
                    continuous_assay_opf='OPF1', current_steady_state_datetime='2026-09-13 09:01'), 'SS')
        self.assertIs(event.grade_streams, streams)
