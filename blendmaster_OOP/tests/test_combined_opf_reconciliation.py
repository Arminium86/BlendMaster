"""A combined run derives its OPF views inside one scenario."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from GUI.InitialiseGUI import UserInputs
from classes.CombinedOPFReconciliation import build_profiles, evidence_signature, opf_field_mappings
from classes.FieldDefinitions import default_field_definitions
from setup.DataStreamReconciliation import DataStreamReconciliation

OPFS = ['CC OPF01', 'CC OPF02']


def source_state():
    factors = {}
    for opf, blend, regression in zip(OPFS, [1.1, .9], [1.2, .8]):
        factors[opf] = DataStreamReconciliation.default_factors(opf, ['FB'], 'fixture')[0]
        for kind, value in [('blend', blend), ('regression', regression)]:
            factors[opf]['FB'][kind]['fe']['effective'] = value
    raw = dict(balance=100, build='B1', FE_ROM=50, FE_PROD1=60, FE_PROD3=55, WMT_PROD1=90, WMT_PROD3=80)
    mappings = []
    for family in ('inventory', 'amt', 'aps'):
        for target, source in [('modelled_rom_fe', 'FE_ROM'), ('modelled_product_fe', 'FE_PROD1'), ('modelled_rom_wmt', 'balance'), ('modelled_product_dmt', 'WMT_PROD1')]:
            mappings.append(dict(source_family=family, target_field=target, source_field=source))
    state = dict(active_scenario_id='only-scenario', mine_input_choice='CC', opf_input_choice=OPFS[0],
        start_time_choice=datetime(2026, 9, 1, 5), product_brand_labels_choice=['FB'],
        stockpile_data={'SP': raw}, updated_stockpile_data={'SP': deepcopy(raw)}, AMT_stockpile_data={},
        hex_sequence_table=[], field_definitions=default_field_definitions(), field_mappings=mappings,
        reconciliation_settings={'method': 'standard'}, historical_recon_factors=factors[OPFS[0]],
        opf_reconciliation_inputs={opf: dict(factors=factors[opf], reconciliation_inputs={}, warnings=[]) for opf in OPFS})
    return state


class CombinedOPFReconciliationTests(unittest.TestCase):
    def test_auto_uses_each_opfs_own_shift_history_for_the_same_source(self):
        from tests.test_reconciliation_factor_resolver import AS_OF, GB, composition, period
        state = source_state()
        state['start_time_choice'] = AS_OF
        state['reconciliation_settings'] = {'method': 'auto_max_confidence'}
        for opf, blend, regression in zip(OPFS, [1.05, .95], [.7, .6]):
            state['opf_reconciliation_inputs'][opf]['reconciliation_inputs'] = dict(
                samples=period(opf=opf, brand='FB', blend=blend, regression=regression),
                inventory_lineage={'B1': dict(inventory_wmt=100, contributing_blocks=composition((GB, 100)))})
        profiles = build_profiles(state, OPFS, UserInputs)
        for opf, rom, product in zip(OPFS, [52.5, 47.5], [42, 33]):
            row = profiles[opf]['inventory']['SP']
            self.assertAlmostEqual(row['grade_streams']['adjusted_rom']['FB']['fe'], rom)
            self.assertAlmostEqual(row['grade_streams']['adjusted_product']['FB']['fe'], product)
            self.assertTrue(row['reconciliation'])

    def test_single_scenario_independent_factors_and_inventory_product_slots(self):
        state = source_state()
        before = deepcopy(state)
        profiles = build_profiles(state, OPFS, UserInputs)
        for opf, rom, product in zip(OPFS, [55, 45], [72, 44]):
            profile = profiles[opf]
            self.assertEqual(profile['scenario_id'], 'only-scenario')
            row = profile['inventory']['SP']
            self.assertEqual(row['balance'], 100)
            self.assertAlmostEqual(row['grade_streams']['adjusted_rom']['FB']['fe'], rom)
            self.assertAlmostEqual(row['grade_streams']['adjusted_product']['FB']['fe'], product)
        self.assertEqual(state, before)

    def test_changed_scenario_start_invalidates_saved_evidence(self):
        from classes.CombinedOPFReconciliation import SourceContext
        from datetime import timedelta
        state = source_state()
        for opf in OPFS:
            state['opf_reconciliation_inputs'][opf]['signature'] = evidence_signature(state, opf, [])
        context = SourceContext(**state, _ui_class=UserInputs)
        context.current_multi_feed_configuration = lambda: dict(mode='combined_opf', tipping_points=[{'opf': opf} for opf in OPFS])
        self.assertEqual(set(context.current_opf_profiles()), set(OPFS))
        context.start_time_choice += timedelta(days=1)
        self.assertEqual(context.current_opf_profiles(), {})

    def test_amt_chunks_reuse_membership_and_physical_mass_with_opf_product_slots(self):
        state = source_state()
        rows = [dict(HEX='a', FINAL_WMT=40, balance=40, FE_ROM=50, FE_PROD1=60, FE_PROD2=55, WMT_PROD1=36, WMT_PROD2=32),
                dict(HEX='b', FINAL_WMT=60, balance=60, FE_ROM=50, FE_PROD1=60, FE_PROD2=55, WMT_PROD1=54, WMT_PROD2=48)]
        state['AMT_stockpile_data'] = {'SP': rows}
        state['hex_sequence_table'] = [dict(hex='original-id', footprint='SP', sequence=3, balance=100, member_hexes=['a', 'b'], average_reclaim_rate=777)]
        profiles = build_profiles(state, OPFS, UserInputs)
        for opf, expected in zip(OPFS, [72, 44]):
            row = profiles[opf]['chunks']['original-id']
            self.assertEqual((row['balance'], row['sequence'], row['average_reclaim_rate']), (100, 3, 777))
            self.assertAlmostEqual(row['grade_streams']['adjusted_product']['FB']['fe'], expected)

    def test_missing_chunk_members_cannot_borrow_other_opf_product(self):
        state = source_state()
        state['hex_sequence_table'] = [dict(hex='chunk', footprint='SP', balance=100)]
        with self.assertRaisesRegex(ValueError, 'original member hexes'):
            build_profiles(state, [OPFS[1]], UserInputs)

    def test_mapping_switch_preserves_custom_fields_and_uses_correct_family_slots(self):
        mappings = source_state()['field_mappings'] + [dict(source_family='inventory', target_field='insitu_fe', source_field='custom_prod1')]
        changed = opf_field_mappings(mappings, *OPFS)
        self.assertIn('FE_PROD3', [r['source_field'] for r in changed if r['source_family'] == 'inventory'])
        self.assertIn('FE_PROD2', [r['source_field'] for r in changed if r['source_family'] == 'amt'])
        self.assertIn('custom_prod1', [r['source_field'] for r in changed])

    def test_worker_fetches_both_opfs_from_frozen_single_scenario_context(self):
        state = source_state()
        service = Mock()
        service.fetch.side_effect = lambda start, opf, brands: (deepcopy(state['opf_reconciliation_inputs'][opf]['factors']), [])
        context = SimpleNamespace(**state)
        context.data_stream_reconciliation = service
        context.multi_feed_configuration = dict(mode='combined_opf', tipping_points=[dict(name='A', opf=OPFS[0]), dict(name='B', opf=OPFS[1])])
        context.selected_site_crushers = ['A', 'B']
        context.crusher_contribution_ratio_choice = 1
        context.auto_load_2wp_targets_choice = False
        context.group_2wp_build_targets_by_brand_choice = False
        context.planning_plan_targets = Mock()
        context.reconciliation_inventory_builds = lambda: ['B1']
        context.planning_period_count = lambda: 1
        context.selected_planning_category = lambda: 'ROM'
        snapshot = UserInputs.data_stream_fetch_snapshot(context)
        context.multi_feed_configuration['tipping_points'].pop()
        result = snapshot()
        self.assertEqual([c.args[1] for c in service.fetch.call_args_list], OPFS)
        self.assertEqual(set(result['opf_reconciliation_inputs']), set(OPFS))
        for opf in OPFS:
            self.assertEqual(result['opf_reconciliation_inputs'][opf]['signature'], evidence_signature(state, opf, ['B1']))
        # An unavailable OPF gets explicit defaults, never the other OPF's factors.
        service.default_factors.side_effect = DataStreamReconciliation.default_factors
        service.fetch.side_effect = [
            (deepcopy(state['opf_reconciliation_inputs'][OPFS[0]]['factors']), []),
            RuntimeError('warehouse unavailable')]
        failed = snapshot()
        self.assertEqual(failed['opf_reconciliation_inputs'][OPFS[0]]['factors']['FB']['regression']['fe']['effective'], 1.2)
        self.assertEqual(failed['opf_reconciliation_inputs'][OPFS[1]]['factors']['FB']['regression']['fe']['effective'], 1)
        self.assertTrue(any('warehouse unavailable' in w and OPFS[1] in w for w in failed['warnings']))


if __name__ == '__main__':
    unittest.main()
