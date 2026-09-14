"""Historical searches are explicit; approved physical-source factors persist."""
from copy import deepcopy
from datetime import timedelta
import pickle
import unittest
from unittest.mock import Mock, patch

from classes.ApprovedReconciliation import (ReconciliationRequired, missing_sources, require_approved,
    record_active_sources, continuous_members, accept_continuous_update)
from classes.CombinedOPFReconciliation import SourceContext, build_profiles
from classes.ReconciliationApplication import ReconciliationApplication
from classes.ReconciliationFactorResolver import ReconciliationFactorResolver
from GUI.InitialiseGUI import UserInputs
from GUI.ManualGradeReconciliation import calculate, saved_review
from tests.test_combined_opf_reconciliation import source_state, OPFS
from tests.test_reconciliation_factor_resolver import AS_OF, GB, REMOTE, composition, period, standard


def state():
    result = source_state()
    result.update(start_time_choice=AS_OF, historical_recon_warnings=[],
                  reconciliation_settings={'method': 'auto_max_confidence'},
                  grade_reconciliation_registry={},
                  multi_feed_configuration={'mode': 'combined_opf', 'tipping_points': [{'name': 'Crusher '+opf, 'opf': opf} for opf in OPFS]})
    result['updated_stockpile_data']['INV'] = deepcopy(result['updated_stockpile_data']['SP'])
    result['updated_stockpile_data']['SP']['amt'] = True
    result['AMT_stockpile_data'] = {'SP': [dict(HEX=h, FINAL_WMT=wmt, balance=wmt,
        FE_ROM=50, FE_PROD1=60, FE_PROD2=55, WMT_PROD1=wmt*.9, WMT_PROD2=wmt*.8,
        GRADE_BLOCK_LINEAGE_JSON=[dict(grade_block_name=GB, remaining_wmt=wmt)]) for h, wmt in [('a',40),('b',60)]]}
    for opf in OPFS:
        result['opf_reconciliation_inputs'][opf]['reconciliation_inputs'] = dict(
            samples=period(opf=opf, brand='FB', blend=1.1, regression=.9),
            inventory_lineage={'B1': dict(inventory_wmt=100, contributing_blocks=composition((GB,100)))})
    result['reconciliation_inputs'] = deepcopy(result['opf_reconciliation_inputs'][OPFS[0]]['reconciliation_inputs'])
    return result


def manual(values, refresh=None):
    context = SourceContext(**deepcopy(values), _ui_class=UserInputs)
    context._grade_reconciliation_refresh_sources = refresh or []
    result = calculate(context, UserInputs)
    values['grade_reconciliation_registry'] = result[2]
    return result


class ManualGradeReconciliationTests(unittest.TestCase):
    def test_library_defaults_to_no_search_and_gui_application_marks_new_sources_pending(self):
        engine = ReconciliationApplication(samples=period(), standard_factors={'SF': standard()},
            opf='CB OPF', brands=['SF'], scenario_start=AS_OF, settings={'method': 'auto_max_confidence'})
        with patch.object(ReconciliationFactorResolver, 'resolve_source', side_effect=AssertionError('automatic search')):
            with self.assertRaises(ReconciliationRequired):
                engine.apply({}, source_id='new', source_kind='inventory', source_wmt=100, contributing_blocks=[])
            values = state()
            context = SourceContext(**values, _ui_class=UserInputs)
            context.apply_grade_streams_to_inventory()
            self.assertEqual(context.updated_stockpile_data['INV']['reconciliation']['status'], 'pending')
            self.assertEqual(context.updated_stockpile_data['INV']['grade_streams']['adjusted_product'], {})
            self.assertTrue(missing_sources(values))
            with self.assertRaises(ReconciliationRequired):
                require_approved(values)

    def test_manual_update_searches_only_missing_sources_for_both_opfs(self):
        values = state()
        original, calls = ReconciliationFactorResolver.resolve_source, []
        def search(engine, *args, **kwargs):
            calls.append((engine.opf, args[0], kwargs.get('hex_id')))
            return original(engine, *args, **kwargs)
        with patch.object(ReconciliationFactorResolver, 'resolve_source', search):
            manual(values)
            self.assertEqual(len(calls), 6)
            self.assertFalse(missing_sources(values))
            calls.clear()
            manual(values)
            self.assertEqual(calls, [])
            values['AMT_stockpile_data']['SP'].append({**deepcopy(values['AMT_stockpile_data']['SP'][0]), 'HEX': 'c'})
            self.assertEqual(len(missing_sources(values)), 2)
            manual(values)
            self.assertEqual(calls, [('CC_OPF01', 'SP', 'c'), ('CC_OPF02', 'SP', 'c')])

    def test_date_history_tonnes_and_lineage_do_not_revoke_inactive_approval(self):
        values = state()
        manual(values)
        approved = deepcopy(values['grade_reconciliation_registry'])
        values['start_time_choice'] += timedelta(days=20)
        values['reconciliation_inputs']['samples'] = []
        values['AMT_stockpile_data']['SP'][0].update(FINAL_WMT=20, balance=20,
            GRADE_BLOCK_LINEAGE_JSON=[dict(grade_block_name=REMOTE, remaining_wmt=20)])
        with patch.object(ReconciliationFactorResolver, 'resolve_source', side_effect=AssertionError('repeat')):
            self.assertFalse(missing_sources(values))
            profiles = build_profiles(values, OPFS, UserInputs)
            manual(values)
        self.assertEqual(values['grade_reconciliation_registry'], approved)
        self.assertAlmostEqual(profiles[OPFS[0]]['inventory']['INV']['grade_streams']['adjusted_rom']['FB']['fe'], 55)

    def test_policy_or_build_change_blocks_planning_without_automatic_search(self):
        values = state()
        manual(values)
        for change in ('settings', 'build'):
            updated = deepcopy(values)
            if change == 'settings':
                updated['reconciliation_settings']['min_production_days'] = 2
            else:
                updated['updated_stockpile_data']['INV']['build'] = 'NEW_BUILD'
            with patch.object(ReconciliationFactorResolver, 'resolve_source', side_effect=AssertionError('repeat')):
                with self.assertRaises(ReconciliationRequired):
                    require_approved(updated)
                saved_review(updated)
            manual(updated)
            self.assertFalse(missing_sources(updated))

    def test_approval_survives_project_serialization_and_selected_refresh_leaves_other_dates(self):
        values = state()
        manual(values)
        values = pickle.loads(pickle.dumps(values))
        before = deepcopy(values['grade_reconciliation_registry']['sources'])
        identity = next(r['detail']['source_identity'] for r in before.values() if r['detail']['source_id'] == 'INV')
        manual(values, [identity])
        after = values['grade_reconciliation_registry']['sources']
        for key, row in before.items():
            if row['detail']['source_identity'] != identity:
                self.assertEqual(after[key], row)
            else:
                self.assertNotEqual(after[key]['detail']['calculated_at'], row['detail']['calculated_at'])

    def test_valid_legacy_snapshot_is_adopted_without_search_or_invented_date(self):
        from classes.PlanningPersistence import migrate_project_state
        from GUI.WorkflowDependencies import reconciliation_input_revision
        from types import SimpleNamespace
        values = state()
        manual(values)
        profiles = build_profiles(values, OPFS, UserInputs)
        for profile in profiles.values():
            audits = [r['reconciliation'] for r in profile['inventory'].values()] + profile['reconciliation_audits']
            for audit in audits:
                audit.pop('last_adjusted', None)
                for detail in audit.get('by_brand', {}).values():
                    for key in ('calculated_at', 'approval_policy', 'source_identity'):
                        detail.pop(key, None)
        values.pop('grade_reconciliation_registry')
        values['_combined_opf_profile_cache'] = ('legacy', profiles)
        values['reconciliation_applied_revision'] = reconciliation_input_revision(SimpleNamespace(**values))
        with patch.object(ReconciliationFactorResolver, 'resolve_source', side_effect=AssertionError('migration search')):
            migrated = migrate_project_state(values)
            self.assertFalse(missing_sources(migrated))
            self.assertTrue(all(not r['detail'].get('calculated_at') for r in migrated['grade_reconciliation_registry']['sources'].values()))
            migrated['start_time_choice'] += timedelta(days=30)
            self.assertFalse(missing_sources(migrate_project_state(migrated)))
            values['reconciliation_settings']['min_production_days'] = 3
            self.assertTrue(missing_sources(migrate_project_state(values)))

    def test_manual_approval_invalidates_pending_amt_cache_and_zero_mass_is_not_approved(self):
        values = state()
        view = SourceContext(**values, _ui_class=UserInputs)
        before = view.AMT_enrichment_request_signature()
        manual(values)
        view.grade_reconciliation_registry = values['grade_reconciliation_registry']
        self.assertNotEqual(view.AMT_enrichment_request_signature(), before)
        values = state()
        values['updated_stockpile_data']['INV']['balance'] = 0
        manual(values)
        values['updated_stockpile_data']['INV']['balance'] = 100
        self.assertTrue(any(r['source'] == 'INV' for r in missing_sources(values)))

    def test_closed_opening_build_can_be_manually_approved_and_applied_without_search(self):
        from setup.TransportOpeningHistory import opening_history_events
        values = state()
        movement = dict(INTERNAL_ID='m1', SOURCE='CLOSED_BUILD', SOURCE_FMS='CLOSED_BUILD', opf=OPFS[0],
                        wmt=10, tipping_point='CRUSHER', time=AS_OF.isoformat(),
                        OPENING_INVENTORY_FIELDS={'BASIS_WMT': 100, 'balance': 100, 'FE_ROM': 50, 'FE_PROD1': 60, 'WMT_PROD1': 90})
        values['transport_opening_history'] = dict(request={'end': AS_OF.isoformat()}, records=[movement])
        for analyte in ('si', 'al', 'p', 'mn'):
            for stream, slot in [('modelled_rom', 'ROM'), ('modelled_product', 'PROD1')]:
                field = analyte.upper()+'_'+slot
                movement['OPENING_INVENTORY_FIELDS'][field] = .1
                values['field_mappings'].append(dict(source_family='inventory', target_field=stream+'_'+analyte, source_field=field))
        manual(values)
        self.assertFalse(missing_sources(values))
        self.assertIn('CLOSED_BUILD', SourceContext(**values, _ui_class=UserInputs).reconciliation_inventory_builds())
        context = dict(mine=values.get('mine_input_choice'), opf=OPFS[0], product_brands=['FB'],
            grade_reconciliation_registry=values['grade_reconciliation_registry'], reconciliation_settings=values['reconciliation_settings'],
            field_definitions=values.get('field_definitions'), field_mappings=values.get('field_mappings'))
        with patch.object(ReconciliationFactorResolver, 'resolve_source', side_effect=AssertionError('opening search')):
            result = opening_history_events(values['transport_opening_history'], context, {})
            with self.assertRaises(ReconciliationRequired):
                opening_history_events(values['transport_opening_history'], {**context, 'mine': 'OTHER'}, {})
        self.assertEqual(len(result), 1)

    def test_active_chunk_excludes_member_hexes_and_keeps_chunk_prior_after_settings_edit(self):
        values = state()
        manual(values)
        values['hex_sequence_table'] = [dict(hex='chunk', footprint='SP', sequence=1, balance=100, member_hexes=['a','b'])]
        profiles = build_profiles(values, OPFS, UserInputs)
        record_active_sources(values, {'CC_OPF01': ['CHUNK']}, profiles)
        self.assertEqual(continuous_members(values, OPFS[0]), {('SP','a'),('SP','b')})
        values['reconciliation_settings']['min_production_days'] = 2
        missing = missing_sources(values)
        self.assertEqual(len(missing), 4)  # Primary inventory + the other OPF's inventory and hexes.
        manual(values)
        values['AMT_stockpile_data']['SP'][0]['FE_PROD1'] = 30
        rebuilt = build_profiles(values, OPFS, UserInputs)
        self.assertEqual(rebuilt[OPFS[0]]['chunks']['chunk']['grade_streams'], profiles[OPFS[0]]['chunks']['chunk']['grade_streams'])
        values['hex_sequence_table'][0]['member_hexes'] = ['a']
        self.assertEqual(continuous_members(values, OPFS[0]), set())

    def test_last_adjusted_uses_accepted_assay_time_not_poll_time(self):
        values = state()
        manual(values)
        values['hex_sequence_table'] = [dict(hex='chunk', footprint='SP', sequence=1, balance=100, member_hexes=['a','b'])]
        profiles = build_profiles(values, OPFS, UserInputs)
        accepted = '2026-09-14T15:35:00'
        bundle = dict(profiles={OPFS[0]: dict(activity_sources=['CHUNK'], timeline=[dict(
            available_at=accepted, offsets={'fe': {'CHUNK': .1}})], checked_at='2026-09-14T16:00:00')})
        accept_continuous_update(values, bundle, profiles)
        audits, _, _ = saved_review(values)
        row = next(a for a in audits if a.get('prediction_scope') == 'amt_chunk')
        self.assertEqual(row['last_adjusted'], accepted)
        bundle['profiles'][OPFS[0]]['timeline'] = []
        accept_continuous_update(values, bundle, profiles)
        self.assertEqual(next(a for a in saved_review(values)[0] if a.get('prediction_scope') == 'amt_chunk')['last_adjusted'], accepted)

    def test_active_inventory_keeps_approved_prior_but_new_brands_require_manual_review(self):
        values = state()
        manual(values)
        profiles = build_profiles(values, OPFS, UserInputs)
        record_active_sources(values, {OPFS[0]: ['INV']}, profiles)
        values['reconciliation_settings']['min_production_days'] = 2
        self.assertFalse(any(r['source'] == 'INV' and r['opf'] == OPFS[0] for r in missing_sources(values)))
        with patch.object(ReconciliationFactorResolver, 'resolve_source', side_effect=AssertionError('active source search')):
            rebuilt = build_profiles(values, OPFS, UserInputs)
        self.assertEqual(rebuilt[OPFS[0]]['inventory']['INV']['grade_streams']['adjusted_product'],
                         profiles[OPFS[0]]['inventory']['INV']['grade_streams']['adjusted_product'])
        values['product_brand_labels_choice'] = ['FB', 'NEW']
        self.assertTrue(any(r['source'] == 'INV' and 'NEW' in r['brands'] for r in missing_sources(values)))

    def test_unrelated_assay_does_not_advance_adjustment_time(self):
        from classes.ContinuousAssays import estimate
        catalog = {s: {'grades': {'FB': {'fe': 60}}} for s in ('A', 'B')}
        evidence = [dict(id=s, available_at=f'2026-09-14T{hour}:00:00', opf=OPFS[0], brand='FB',
                         weights={s: 1}, grades={'fe': 60.1}) for s, hour in [('A', '10'), ('B', '11')]]
        result = estimate(catalog, evidence)
        self.assertEqual(result['timeline'][0]['adjusted_sources'], ['A'])
        self.assertEqual(result['timeline'][1]['adjusted_sources'], ['B'])


if __name__ == '__main__':
    unittest.main()


class ManualReviewUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_last_adjusted_column_and_selected_source_refresh_signal(self):
        from GUI.ReconciliationReview import ReconciliationReview
        from PyQt5.QtTest import QSignalSpy
        values = state()
        manual(values)
        panel = ReconciliationReview()
        panel.set_context(values['reconciliation_settings'], OPFS[0], ['FB'])
        panel.set_review(*saved_review(values))
        self.assertEqual(panel.sources.headerItem().text(9), 'Last adjusted')
        item = panel.sources.topLevelItem(0)
        self.assertRegex(item.text(9), r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$')
        panel.sources.setCurrentItem(item)
        requested = QSignalSpy(panel.refreshSourcesRequested)
        panel.refresh_selected_button.click()
        self.assertEqual(len(requested), 1)
        self.assertEqual(requested[0][0][0][2], 'inventory')
        panel.deleteLater()

    def test_automatic_page_preparation_displays_saved_rows_without_fetch_or_search(self):
        from GUI.ReconciliationReview import ReconciliationReview
        from PyQt5.QtWidgets import QPushButton
        values = state()
        manual(values)
        context = SourceContext(**values, _ui_class=UserInputs)
        context.reconciliation_review = ReconciliationReview()
        context.data_streams_submit_button = QPushButton()
        context.run_background_task = Mock()
        with patch.object(ReconciliationFactorResolver, 'resolve_source', side_effect=AssertionError('automatic search')):
            context.prepare_data_streams()
        context.run_background_task.assert_not_called()
        self.assertTrue(context.data_streams_submit_button.isEnabled())
        self.assertGreater(context.reconciliation_review.sources.topLevelItemCount(), 0)
        context.reconciliation_review.deleteLater()
        context.data_streams_submit_button.deleteLater()
