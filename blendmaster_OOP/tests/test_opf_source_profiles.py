import unittest
from copy import deepcopy

from classes.OPFSourceProfiles import register_profiles, namespace_record, apply_opf_profile
from classes.CustomConstraints import merge_source_properties, scale_additive_source_properties
from classes.GradeStreams import legacy_grade_streams, apply_selected_stream
from tests import test_decision_levers as fixtures
from classes.MultiLaneOptimizer import MultiLaneOptimizer
from classes.PeriodManager import PeriodManager
from datetime import datetime


def configuration(opfs=('OPF1', 'OPF2')):
    cfg = {}
    register_profiles(cfg, {opf: dict(scenario_id=opf + '-scenario', fields=[dict(name='modelled_rom_wmt', kind='additive')], brands=['FB']) for opf in opfs})
    return cfg


class OPFSourceProfilesTests(unittest.TestCase):
    def test_inbound_mix_and_depletion_preserve_independent_opf_grades(self):
        cfg = configuration()
        opening = {'modelled_rom_wmt': 100}
        inbound = {'modelled_rom_wmt': 100}
        for opf, fe in [('OPF1', 60), ('OPF2', 50)]:
            row = dict(source_properties={'modelled_rom_wmt': 100}, grade_streams=legacy_grade_streams({'grade_fe': fe}))
            opening.update(namespace_record(row, opf, cfg))
            row['grade_streams'] = legacy_grade_streams({'grade_fe': fe + 4})
            inbound.update(namespace_record(row, opf, cfg))
        mixed = merge_source_properties(opening, 100, inbound, 100, cfg['source_property_kinds'], cfg['source_property_weights'])
        remaining = scale_additive_source_properties(mixed, .5, cfg['source_property_kinds'])
        for opf, expected in [('OPF1', 62), ('OPF2', 52)]:
            event = fixtures.DecisionLeverOptimizerTests.event('SP1', balance=100)
            event.source_properties = deepcopy(remaining)
            apply_opf_profile(event, opf, cfg)
            apply_selected_stream(event, 'adjusted_product', 'FB')
            self.assertAlmostEqual(event.grade_fe, expected)
            self.assertAlmostEqual(event.source_properties['modelled_rom_wmt'], 100)
            self.assertEqual(event._reconciliation_scenario, opf + '-scenario')

    def test_three_opfs_keep_their_own_reconciliation(self):
        opfs = ('OPF1', 'OPF2', 'OPF3')
        cfg = configuration(opfs)
        events = []
        for i, opf in enumerate(opfs):
            event = fixtures.DecisionLeverOptimizerTests.event(f'SP{i}', balance=1000)
            event.source_properties = {'modelled_rom_wmt': 1000}
            for mapped_opf in opfs:
                row = dict(source_properties={'modelled_rom_wmt': 1000}, grade_streams=legacy_grade_streams({'grade_fe': 50 + 5 * opfs.index(mapped_opf)}))
                event.source_properties.update(namespace_record(row, mapped_opf, cfg))
            events.append(event)
        settings = dict(mode='combined_opf', tipping_points=[dict(name=f'CR{i}', opf=opf, targets_by_period={'preplan': fixtures.DecisionLeverOptimizerTests.target()}) for i, opf in enumerate(opfs)],
                        source_subsets={f'SP{i}': f'CR{i}' for i in range(3)})
        periods = PeriodManager()
        periods.calculate_periods(datetime(2026, 1, 1))
        result = MultiLaneOptimizer(settings).run_blending_optimization(events, fixtures.DecisionLeverOptimizerTests.target(), 1, None, None, periods, 'preplan', solver_config=cfg)
        self.assertTrue(result['Linprog_result_object'].success)
        self.assertEqual({r['opf']: r['grade_fe'] for r in result['transactions']}, {'OPF1': 50, 'OPF2': 55, 'OPF3': 60})
        self.assertAlmostEqual(sum(r['actual_tonnes'] for r in result['transactions']), 300)

