"""Route scope, submitted-chunk gating and manual reconciliation at either grain."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from PyQt5.QtWidgets import QApplication, QTableWidget, QComboBox
from classes.AMTReconciliation import chunks_ready, submission_signature, chunk_lineage
from classes.ApprovedReconciliation import missing_sources, require_approved, ReconciliationRequired
from classes.MultiFeedSettings import source_opfs, route_allowed, source_routing
from classes.CombinedOPFReconciliation import build_profiles, SourceContext
from classes.ReconciliationFactorResolver import ReconciliationFactorResolver
from classes.SiteWorkflow import WorkflowRun
from GUI.InitialiseGUI import UserInputs
from GUI.ManualGradeReconciliation import saved_review
from tests.test_manual_grade_reconciliation import state, manual, OPFS


def fixture(post=False):
    value = state()
    value['multi_feed_configuration']['amt_reconcile_after_chunking'] = post
    value['multi_feed_configuration']['tipping_points'][1]['rom_area'] = 'Other'
    return value


def submit(value, members=('a', 'b')):
    rows = {r['HEX']: r for r in value['AMT_stockpile_data']['SP']}
    value['hex_sequence_table'] = [dict(footprint='SP', hex='SP_CHUNK_001', sequence=1,
        member_hexes=list(members), balance=sum(rows[h]['FINAL_WMT'] for h in members))]
    value['hex_sequence_table_argument'] = deepcopy(value['hex_sequence_table'])
    value['multi_feed_configuration']['amt_submission_signature'] = submission_signature(value)


class GrainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_routes_limit_manual_search_and_profile_grades_to_one_opf(self):
        value = fixture()
        value['stockpile_data']['UNSELECTED'] = dict(balance=100, subset='Other')
        value['opf_reconciliation_inputs'][OPFS[1]]['factors'] = {}
        calls = []
        original = ReconciliationFactorResolver.resolve_source
        def resolve(engine, *args, **kwargs):
            calls.append((engine.opf, args[1]))
            return original(engine, *args, **kwargs)
        with patch.object(ReconciliationFactorResolver, 'resolve_source', resolve):
            manual(value)
            self.assertEqual(calls, [('CC_OPF01', 'amt'), ('CC_OPF01', 'amt'), ('CC_OPF01', 'inventory')])
            submit(value)
            calls.clear()
            profiles = build_profiles(value, OPFS, UserInputs)
            self.assertFalse(calls)
        self.assertIn('INV', profiles[OPFS[0]]['inventory'])
        self.assertFalse(profiles[OPFS[1]]['inventory'])
        self.assertFalse(profiles[OPFS[1]]['chunks'])

    def test_post_chunk_search_uses_combined_lineage_and_applies_once(self):
        value = fixture(True)
        submit(value)
        self.assertEqual(sum(b['feed_wmt'] for b in chunk_lineage(value, value['hex_sequence_table'][0])[0]), 100)
        calls = []
        original = ReconciliationFactorResolver.resolve_source
        def resolve(engine, *args, **kwargs):
            calls.append((args[1], args[3], kwargs.get('hex_id')))
            return original(engine, *args, **kwargs)
        with patch.object(ReconciliationFactorResolver, 'resolve_source', resolve):
            manual(value)
            self.assertEqual(calls, [('amt_chunk', 100.0, 'SP_CHUNK_001'), ('inventory', 100.0, None)])
            calls.clear()
            profiles = build_profiles(value, OPFS, UserInputs)
            self.assertFalse(calls)
        chunk = profiles[OPFS[0]]['chunks']['SP_CHUNK_001']
        self.assertAlmostEqual(chunk['grade_streams']['adjusted_rom']['FB']['fe'], 55)
        self.assertAlmostEqual(chunk['grade_streams']['adjusted_product']['FB']['fe'], 54)
        self.assertFalse(missing_sources(value))
        self.assertEqual(len(saved_review(value)[0]), 2)
        self.assertEqual(chunk['reconciliation']['source_kind'], 'amt_chunk')

    def test_mode_and_membership_changes_require_only_new_chunk_approvals(self):
        value = fixture()
        manual(value)
        submit(value)
        value['multi_feed_configuration']['amt_reconcile_after_chunking'] = True
        self.assertEqual([r['kind'] for r in missing_sources(value)], ['amt_chunk'])
        manual(value)
        inventory = [deepcopy(r) for r in value['grade_reconciliation_registry']['sources'].values()
                     if r['detail']['source_kind'] == 'inventory']
        submit(value, ('a',))
        self.assertEqual([r['kind'] for r in missing_sources(value)], ['amt_chunk'])
        manual(value)
        self.assertEqual(inventory, [r for r in value['grade_reconciliation_registry']['sources'].values()
                                    if r['detail']['source_kind'] == 'inventory'])

    def test_chunk_gate_requires_all_footprints_and_explicit_submission(self):
        value = fixture()
        self.assertFalse(chunks_ready(value))
        submit(value)
        self.assertTrue(chunks_ready(value))
        value['updated_stockpile_data']['NEW'] = dict(balance=10, amt=True, subset='Shared')
        self.assertFalse(chunks_ready(value))
        value['updated_stockpile_data'].pop('NEW')
        value['hex_sequence_table'][0]['member_hexes'] = ['a']
        self.assertFalse(chunks_ready(value))

    def test_aliases_and_explicit_deny_and_blank_subset_are_authoritative(self):
        value = fixture()
        point = value['multi_feed_configuration']['tipping_points'][0]
        point['rom_area'] = 'RCH'
        value['updated_stockpile_data']['SP']['subset'] = 'OPF02_PC'
        self.assertEqual(source_opfs(value, 'SP'), {'CC_OPF01'})
        value['multi_feed_configuration']['rehandle_rules'] = [dict(subset='OPF02_PC', tipping_point=point['name'], allowed=False)]
        self.assertFalse(source_opfs(value, 'SP'))
        value['updated_stockpile_data']['SP']['subset'] = ''
        value['updated_stockpile_data']['SP']['nearest_crusher'] = 'RCH'
        self.assertFalse(source_opfs(value, 'SP'))
        with self.assertRaisesRegex(ReconciliationRequired, 'Subset'):
            require_approved(value, planning=False)

    def test_background_preparation_waits_for_chunk_submission_and_approvals(self):
        from GUI.OPFProfileLoading import ensure
        from tests.test_background_tasks import Host
        host = Host()
        self.addCleanup(host.deleteLater)
        host.__dict__.update(fixture(True))
        host.run_background_task = Mock()
        host.reconciliation_inventory_builds = lambda: ['B1']
        self.assertFalse(ensure(host, Mock()))
        submit(vars(host))
        self.assertFalse(ensure(host, Mock()))
        host.run_background_task.assert_not_called()
        from classes.CombinedOPFReconciliation import SOURCE_FIELDS
        values = {k: vars(host)[k] for k in SOURCE_FIELDS if k in vars(host)}
        manual(values)
        host.grade_reconciliation_registry = values['grade_reconciliation_registry']
        self.assertTrue(ensure(host, Mock()))
        host.run_background_task.assert_called_once()

    def test_navigation_and_automation_share_selected_grain(self):
        from tests.test_workflow_navigation import WorkflowNavigationTests
        case = WorkflowNavigationTests()
        for role in ('support', 'planner'):
            host = case.progression_host(role)
            self.addCleanup(host.deleteLater)
            host.multi_feed_configuration = {'amt_reconcile_after_chunking': True}
            host.sync_workspace_order()
            self.assertEqual(host.next_workspace_page('stockpile_inventories'), 'amt_stockpiles')
            self.assertEqual(host.next_workspace_page('amt_stockpiles'), 'grade_reconciliation')
            self.assertEqual(host.next_workspace_page('grade_reconciliation'), 'product_targets')
            self.assertLess(host.page_locations['amt_stockpiles'][1], host.page_locations['grade_reconciliation'][1])
        run = WorkflowRun('site', None, 'inputs', amt_reconcile_after_chunking=True)
        self.assertLess(run.steps.index('amt'), run.steps.index('reconciliation'))

    def test_submit_with_missing_sources_keeps_approved_rows_and_timestamps(self):
        from GUI.ReconciliationReview import ReconciliationReview
        value = fixture()
        manual(value)
        panel = ReconciliationReview()
        self.addCleanup(panel.deleteLater)
        panel.set_context(value['reconciliation_settings'], OPFS[0], ['FB'])
        panel.set_review(*saved_review(value))
        value['AMT_stockpile_data']['SP'].append({**deepcopy(value['AMT_stockpile_data']['SP'][0]), 'HEX': 'new'})
        host = SimpleNamespace(**value, reconciliation_review=panel, show_page=Mock(), data_streams_submit_button=Mock())
        host.update_reconciliation_review = lambda: UserInputs.update_reconciliation_review(host)
        UserInputs.handle_data_streams_submit(host)
        self.assertGreater(panel.sources.topLevelItemCount(), 0)
        self.assertTrue(any(r['detail'].get('calculated_at') for r in host.grade_reconciliation_registry['sources'].values()))

    def test_pre_chunk_factors_preserve_weighted_member_contributions(self):
        from tests.test_reconciliation_factor_resolver import GB, REMOTE, period
        value = fixture()
        value['reconciliation_settings'] = {'method': 'spatial_compositional'}
        rows = value['AMT_stockpile_data']['SP']
        rows[1].update(FE_ROM=40, FE_PROD1=45,
            GRADE_BLOCK_LINEAGE_JSON=[dict(grade_block_name=REMOTE, remaining_wmt=60)])
        value['reconciliation_inputs']['samples'] = (
            period(opf=OPFS[0], brand='FB', block=GB, blend=1.1, regression=.9) +
            period('2026-08-21 06:00', opf=OPFS[0], brand='FB', block=REMOTE, blend=1.5, regression=1.3))
        manual(value)
        submit(value)
        chunk = build_profiles(value, OPFS, UserInputs)[OPFS[0]]['chunks']['SP_CHUNK_001']
        self.assertAlmostEqual(chunk['grade_streams']['adjusted_rom']['FB']['fe'], (40*50*1.1 + 60*40*1.5)/100)
        self.assertAlmostEqual(chunk['grade_streams']['adjusted_product']['FB']['fe'], (40*60*.9 + 60*45*1.3)/100)

    def test_post_chunk_active_source_reuses_prior_and_does_not_search_members(self):
        from classes.ApprovedReconciliation import record_active_sources, continuous_members
        value = fixture(True)
        submit(value)
        manual(value)
        profiles = build_profiles(value, OPFS, UserInputs)
        record_active_sources(value, {OPFS[0]: ['SP_CHUNK_001']}, profiles)
        self.assertEqual(continuous_members(value, OPFS[0]), {('SP', 'a'), ('SP', 'b')})
        value['AMT_stockpile_data']['SP'][0]['FE_PROD1'] = 30
        with patch.object(ReconciliationFactorResolver, 'resolve_source', side_effect=AssertionError('repeat search')):
            manual(value)
            rebuilt = build_profiles(value, OPFS, UserInputs)
        self.assertEqual(rebuilt[OPFS[0]]['chunks']['SP_CHUNK_001']['grade_streams'],
                         profiles[OPFS[0]]['chunks']['SP_CHUNK_001']['grade_streams'])

    def test_grain_and_submission_survive_project_roundtrip(self):
        import pickle
        from classes.PlanningPersistence import migrate_project_state
        from classes.MultiFeedSettings import multi_feed_settings
        value = fixture(True)
        submit(value)
        manual(value)
        value = migrate_project_state(pickle.loads(pickle.dumps(value)))
        config = multi_feed_settings(value['multi_feed_configuration'])
        self.assertTrue(config['amt_reconcile_after_chunking'])
        self.assertTrue(config['amt_submission_signature'])
        self.assertTrue(chunks_ready(value))
        self.assertFalse(missing_sources(value))
        self.assertFalse(multi_feed_settings({})['amt_reconcile_after_chunking'])

    def test_subset_dropdown_failed_refresh_restores_accepted_value(self):
        from tests.test_inventory_refresh import Host
        from GUI.InventoryRefresh import InventoryRefresh, collect
        host = Host()
        self.addCleanup(host.deleteLater)
        host.updated_stockpile_data['SP1']['subset'] = 'Accepted'
        picker = QComboBox()
        picker.addItems(['Accepted', 'New'])
        host.stockpile_table.setCellWidget(0, 3, picker)
        picker.currentTextChanged.connect(lambda value: UserInputs.set_stockpile_subset(host, 'SP1', value))
        picker.setCurrentText('New')
        self.assertEqual(collect(host)['updated_stockpile_data']['SP1']['subset'], 'New')
        self.assertEqual(host.updated_stockpile_data['SP1']['subset'], 'Accepted')
        controller = InventoryRefresh(host)
        host._inventory_refresh_request = {'status': 'failed'}
        controller.discard()
        self.assertEqual(picker.currentText(), 'Accepted')
        self.assertEqual(host.stockpile_data['SP1']['subset'], 'Accepted')
        self.assertEqual(collect(host)['updated_stockpile_data']['SP1']['subset'], 'Accepted')

    def test_single_opf_prepares_and_publishes_submitted_chunks_once(self):
        from PyQt5.QtWidgets import QMainWindow
        from GUI.OPFProfileLoading import ensure
        for post in (False, True):
            with self.subTest(post=post):
                value = fixture(post)
                value['multi_feed_configuration']['mode'] = 'single'
                submit(value)
                manual(value)
                host = UserInputs.__new__(UserInputs)
                QMainWindow.__init__(host)
                self.addCleanup(host.deleteLater)
                host.__dict__.update(value)
                host.run_background_task = Mock()
                host.show_error_popup = Mock()
                done = Mock()
                with patch.object(ReconciliationFactorResolver, 'resolve_source', side_effect=AssertionError('automatic search')):
                    self.assertTrue(ensure(host, done))
                    _, work, finished, _ = host.run_background_task.call_args.args
                    finished(work())
                    self.assertFalse(ensure(host, done))
                done.assert_called_once()
                host.run_background_task.assert_called_once()
                host.show_error_popup.assert_not_called()
                for field in ('hex_sequence_table', 'hex_sequence_table_argument'):
                    self.assertAlmostEqual(vars(host)[field][0]['grade_streams']['adjusted_product']['FB']['fe'], 54)

    def test_route_editor_cannot_reset_grain_or_chunk_submission(self):
        host = SimpleNamespace(multi_feed_configuration=dict(amt_reconcile_after_chunking=True, amt_submission_signature='submitted'),
            multi_feed_setup=SimpleNamespace(settings=Mock(return_value=dict(amt_reconcile_after_chunking=False,
                amt_submission_signature='', rehandle_rules=[dict(subset='Pad', tipping_point='PC', allowed=True)])), validation=Mock()),
            save_active_scenario_state=Mock())
        UserInputs.submit_multi_feed_setup(host)
        self.assertTrue(host.multi_feed_configuration['amt_reconcile_after_chunking'])
        self.assertEqual(host.multi_feed_configuration['amt_submission_signature'], 'submitted')
        self.assertEqual(host.multi_feed_configuration['rehandle_rules'][0]['subset'], 'Pad')

    def test_raw_amt_preparation_needs_no_factor_resolver_or_approval(self):
        value = fixture()
        value['historical_recon_factors'] = {}
        context = SourceContext(**value, _ui_class=UserInputs)
        context.reconciliation_application = Mock(side_effect=AssertionError('premature factor application'))
        enriched = context.enrich_AMT_grade_streams({}, value['AMT_stockpile_data'])
        context.reconciliation_application.assert_not_called()
        self.assertTrue(enriched['SP'][0]['grade_streams']['modelled_product'])
        self.assertFalse(enriched['SP'][0]['grade_streams']['adjusted_product'])
