"""Copy budgets at publication boundaries, with mutable-state isolation checks."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from GUI.InitialiseGUI import UserInputs
from GUI.OPFProfilePublication import publish
from classes.CombinedOPFReconciliation import SourceContext, build_profiles
from tests import test_reconciliation_application as application
from tests import test_opf_profile_loading as loading
from tests import test_combined_opf_reconciliation as combined


class CopyProbe(dict):
    copies = 0

    def __deepcopy__(self, memo):
        type(self).copies += 1
        result = type(self)()
        memo[id(self)] = result
        result.update(deepcopy(dict(self), memo))
        return result


class ReconciliationCopyTests(unittest.TestCase):
    setUpClass = classmethod(loading.OPFProfileLoadingTests.setUpClass.__func__)

    def test_repeated_applications_reuse_history_but_own_factors_and_coverage(self):
        for approved in (False, True):
            with self.subTest(approved=approved):
                engine = application.application(registry={} if approved else None)
                expected, initial = application.apply(engine)
                stored = (next(iter(engine.registry['sources'].values()))['detail'] if approved
                          else next(iter(engine._resolved_sources.values())))
                record = stored['records'][0]
                record['source_history'] = CopyProbe(periods=record['source_history'])
                record['provenance'] = CopyProbe(record['provenance'])
                CopyProbe.copies = 0
                engine.allow_search = False
                for _ in range(3):
                    actual, audit = application.apply(engine)
                    self.assertEqual(actual, expected)
                    detail = audit['by_brand']['SF']
                    self.assertIs(detail['records'][0]['source_history'], record['source_history'])
                    detail['records'][0]['blend_factors']['fe'] = 99
                    detail['grade_coverage']['adjusted_rom']['fe'] = 0
                    detail['source_wmt'] = -1
                self.assertEqual(CopyProbe.copies, 0)
                self.assertNotEqual(record['blend_factors']['fe'], 99)
                self.assertEqual(stored['source_wmt'], 100)

    def test_opening_movements_reuse_approval_for_each_movement_tonnage(self):
        engine = application.application(registry={})
        expected, _ = application.apply(engine)
        stored = next(iter(engine.registry['sources'].values()))['detail']
        history = CopyProbe(periods=stored['records'][0]['source_history'])
        stored['records'][0]['source_history'] = history
        engine.allow_search, engine.accepted_movement = False, True
        CopyProbe.copies = 0
        for amount in (2, 5, 20):
            actual, audit = application.apply(engine, total=amount)
            self.assertEqual(actual, expected)
            self.assertEqual(audit['by_brand']['SF']['source_wmt'], amount)
            self.assertIs(audit['by_brand']['SF']['records'][0]['source_history'], history)
        self.assertEqual(CopyProbe.copies, 0)
        self.assertEqual(stored['source_wmt'], 100)

    def test_profile_worker_reuses_registry_without_sharing_editable_sources(self):
        from GUI.OPFProfileLoading import ensure
        host = loading.OPFProfileLoadingTests().host()
        self.addCleanup(host.deleteLater)
        host.run_background_task = Mock()
        registry = host.grade_reconciliation_registry
        inventory = deepcopy(host.updated_stockpile_data)
        def build(state, *args):
            self.assertIs(state['grade_reconciliation_registry'], registry)
            state['updated_stockpile_data']['SP']['balance'] = 1
            return {}
        with patch('GUI.OPFProfileLoading.build_profiles', side_effect=build):
            self.assertTrue(ensure(host, lambda: None))
            host.run_background_task.call_args.args[1]()
        self.assertEqual(host.updated_stockpile_data, inventory)

    def test_inventory_worker_failure_leaves_live_sources_and_registry_intact(self):
        from GUI.InventoryStreamApplication import apply
        class Host(SimpleNamespace):
            def apply_canonical_field_mappings(self):
                self.stockpile_data['SP']['grades']['fe'] = 1
                observed.append(self.grade_reconciliation_registry)
                raise ValueError('stop after editing worker-owned grades')
        observed = []
        host = Host(stockpile_data={'SP': {'grades': {'fe': 55}}},
                    grade_reconciliation_registry={'sources': {'old': {'detail': {}}}},
                    run_background_task=Mock(), show_error_popup=Mock())
        complete = Mock()
        apply(host, complete)
        _, work, done, failed = host.run_background_task.call_args.args
        with self.assertRaisesRegex(ValueError, 'worker-owned'):
            work()
        failed('failed')
        self.assertIs(observed[0], host.grade_reconciliation_registry)
        self.assertEqual(host.stockpile_data['SP']['grades']['fe'], 55)
        self.assertFalse(host._data_stream_application_pending)
        complete.assert_not_called()

    def test_manual_worker_forks_approvals_before_replacing_records(self):
        from GUI.ManualGradeReconciliation import calculate
        host = loading.OPFProfileLoadingTests().host()
        self.addCleanup(host.deleteLater)
        host.run_background_task = Mock()
        host.reconciliation_review_signature = Mock(return_value='current')
        host.reconciliation_review = Mock()
        host.data_streams_submit_button = Mock()
        registry = host.grade_reconciliation_registry
        snapshot = deepcopy(registry)
        captured = []
        def review(context, implementation):
            self.assertIs(context.grade_reconciliation_registry, registry)
            result = calculate(context, implementation)
            captured.append(context.grade_reconciliation_registry)
            context.grade_reconciliation_registry['sources']['new'] = {'detail': {}}
            return result
        with patch('GUI.ManualGradeReconciliation.calculate', side_effect=review):
            UserInputs.start_confidence_search_review(host)
            host.run_background_task.call_args.args[1]()
        self.assertIsNot(captured[0], registry)
        self.assertEqual(registry, snapshot)

    def test_profile_publication_skips_unchanged_rows_and_hydrates_missing_grades(self):
        audit = CopyProbe(by_brand={'FB': {'confidence_percent': 90}})
        inventory = {'balance': 100, 'grade_streams': {'adjusted_rom': {'FB': {'fe': 55}}},
                     'reconciliation': audit}
        chunk = {**inventory, 'hex': 'chunk', 'member_hexes': ['a', 'b']}
        profiles = {'OPF': {'inventory': {'SP': inventory}, 'chunks': {'chunk': chunk}}}
        host = SimpleNamespace(opf_input_choice='OPF', stockpile_data={'SP': {}},
            updated_stockpile_data={'SP': {}}, hex_sequence_table=[{'hex': 'chunk'}],
            hex_sequence_table_argument=[{'hex': 'chunk'}])
        CopyProbe.copies = 0
        publish(host, profiles)
        def rows():
            return (host.stockpile_data['SP'], host.updated_stockpile_data['SP'],
                    host.hex_sequence_table[0], host.hex_sequence_table_argument[0])
        first = rows()
        with patch('GUI.OPFProfilePublication.copy_prepared_source',
                   side_effect=AssertionError('unchanged publication copied a row')):
            publish(host, profiles)
        self.assertTrue(all(a is b for a, b in zip(first, rows())))
        self.assertTrue(all(row['reconciliation'] is audit for row in rows()))
        self.assertEqual(CopyProbe.copies, 0)
        host.hex_sequence_table[0]['grade_streams']['adjusted_rom']['FB']['fe'] = 1
        host.hex_sequence_table[0]['member_hexes'].append('edit')
        self.assertEqual(chunk['grade_streams']['adjusted_rom']['FB']['fe'], 55)
        self.assertEqual(host.hex_sequence_table_argument[0]['member_hexes'], ['a', 'b'])
        host.updated_stockpile_data['SP'].pop('grade_streams')
        publish(host, profiles)
        self.assertEqual(host.updated_stockpile_data['SP']['grade_streams'], inventory['grade_streams'])

    def test_chunk_build_transfers_member_evidence_without_copying_it_again(self):
        state = combined.CombinedOPFReconciliationTests().auto_amt_state()
        state['hex_sequence_table'] = [dict(hex='chunk', footprint='SP', sequence=1,
            balance=100, member_hexes=['a', 'b'], grade_streams={})]
        combined.approve_sources(state)
        original = deepcopy(state)
        enriched, prepared = [], []
        from GUI.DrawCharts import DrawAMTStockpile
        enrich = UserInputs.enrich_AMT_grade_streams
        build = DrawAMTStockpile.build_chunk_row
        def track_members(context, *args, **kwargs):
            result = enrich(context, *args, **kwargs)
            for rows in result.values():
                for row in rows:
                    row['reconciliation'] = CopyProbe(row['reconciliation'])
                    enriched.append(row)
            return result
        def track_build(builder, footprint, sequence, rows, size):
            prepared.extend(rows)
            return build(builder, footprint, sequence, rows, size)
        CopyProbe.copies = 0
        with patch.object(UserInputs, 'enrich_AMT_grade_streams', track_members), \
             patch.object(DrawAMTStockpile, 'build_chunk_row', track_build):
            profiles = build_profiles(state, combined.OPFS, UserInputs)
        self.assertTrue(prepared)
        self.assertEqual(CopyProbe.copies, 0)
        self.assertEqual(state, original)
        audits = [audit for profile in profiles.values() for audit in profile['reconciliation_audits']]
        self.assertTrue(all(any(audit is row['reconciliation'] for row in enriched) for audit in audits))
        self.assertTrue(all(any(row is not source and row['reconciliation'] is source['reconciliation']
                                for source in enriched) for row in prepared))

    def test_history_delivery_copies_cache_and_editable_fields_once_each(self):
        inputs = CopyProbe(samples=[{'feed_wmt': 100}])
        result = {'reconciliation_inputs': inputs,
                  'factors': {'FB': {'blend': {'fe': {'effective': 1.1}}}}}
        host = SimpleNamespace(data_stream_input_request_inflight='current',
            data_stream_input_request_signature=lambda: 'current',
            data_stream_effective_overrides={('FB', 'blend', 'fe'): 1.2},
            populate_recon_factor_table=Mock(), update_reconciliation_review=lambda **kw: [],
            aps_grade_mapping_warnings=lambda: [], data_stream_warning_label=Mock(),
            data_streams_submit_button=Mock(), advance_agent_after_reconciliation=Mock())
        host.finish_data_stream_inputs = lambda value: UserInputs.finish_data_stream_inputs(host, value)
        CopyProbe.copies = 0
        UserInputs.finish_cached_data_stream_inputs(host, 'current', result)
        self.assertEqual(CopyProbe.copies, 2)
        self.assertEqual(host.historical_recon_factors['FB']['blend']['fe']['effective'], 1.2)
        self.assertEqual(host.data_stream_input_cache_result['factors']['FB']['blend']['fe']['effective'], 1.1)
        host.reconciliation_inputs['samples'][0]['feed_wmt'] = 1
        self.assertEqual(inputs['samples'][0]['feed_wmt'], 100)
        self.assertEqual(host.data_stream_input_cache_result['reconciliation_inputs']['samples'][0]['feed_wmt'], 100)
        host.finish_data_stream_inputs(host.data_stream_input_cache_result)
        self.assertEqual(CopyProbe.copies, 3)
        self.assertEqual(host.reconciliation_inputs['samples'][0]['feed_wmt'], 100)

    def test_scenario_restore_detaches_fields_once_and_preserves_saved_state(self):
        from classes.PlanningPersistence import migrate_project_state
        class Hydrated(Exception):
            pass
        stock = {'SP': {'balance': 100, 'grades': CopyProbe(fe=55)}}
        chunk = {'hex': 'chunk', 'footprint': 'SP', 'balance': 100, 'member_hexes': ['a']}
        state = migrate_project_state(dict(database_path='fixture.db', stockpile_data=stock, updated_stockpile_data=stock,
            hex_sequence_table=[chunk], selected_site_crushers=['PC'], opf_input_choice='CB OPF',
            min_stockpiles={'Preplan': 1}, max_stockpiles={'Preplan': 4},
            min_stockpile_contribution_ratio={'Preplan': .1},
            inventory_source_cache={'SP': {'raw': [1]}},
            _combined_opf_profile_cache=('current', {'OPF': {'inventory': {'SP': stock['SP']}}})))
        snapshot = deepcopy(state)
        host = SourceContext(_ui_class=UserInputs, active_scenario_id='fixture',
            reset_workflow_tabs_for_scenario=Mock(), refresh_scenario_selector=Mock(),
            plan_mode_input=Mock(), inventory_opening_request_signature=lambda: 'saved',
            normalized_solver_config=lambda value: {}, decision_table=None,
            restore_table_snapshot=Mock(side_effect=Hydrated))
        CopyProbe.copies = 0
        with patch('GUI.InitialiseGUI.set_database_path'), self.assertRaises(Hydrated):
            host.restore_site_scenario(state)
        # The same saved grade object needs one isolated copy in each editable
        # inventory collection and one in the restored profile cache, not six.
        self.assertEqual(CopyProbe.copies, 3)
        host.stockpile_data['SP']['grades']['fe'] = 1
        host.inventory_source_cache['SP']['raw'].append(2)
        host._combined_opf_profile_cache[1]['OPF']['inventory']['SP']['grades']['fe'] = 2
        host.selected_site_crushers.append('another')
        host.hex_sequence_table[0]['member_hexes'].append('edit')
        host.min_stockpiles['Preplan'] = 2
        host.max_stockpiles['Preplan'] = 3
        host.min_stockpile_contribution_ratio['Preplan'] = .2
        self.assertEqual(host.updated_stockpile_data['SP']['grades']['fe'], 55)
        self.assertEqual(host.hex_sequence_table_argument[0]['member_hexes'], ['a'])
        self.assertEqual(state, snapshot)
        self.assertFalse(host.scenario_switch_in_progress)


if __name__ == '__main__':
    unittest.main()
