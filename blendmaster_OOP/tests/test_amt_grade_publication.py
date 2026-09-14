"""Grades must reach both scheduling chunks and their independent AMT chart copy."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from GUI.InitialiseGUI import UserInputs
from GUI.OPFProfileLoading import ensure
from GUI.OPFProfilePublication import publish, sync_map
from classes.AMTReconciliation import submission_signature
from classes.CombinedOPFReconciliation import build_profiles, profile_signature
from classes.PlannerPresentation import chunk_display, hex_grade_display
from tests.test_combined_opf_reconciliation import approve_sources, OPFS
from tests import test_combined_opf_reconciliation as combined, test_opf_profile_loading as loading


class AMTGradePublicationTests(unittest.TestCase):
    setUpClass = classmethod(loading.OPFProfileLoadingTests.setUpClass.__func__)
    drain = loading.OPFProfileLoadingTests.drain

    def state(self):
        state = combined.CombinedOPFReconciliationTests().auto_amt_state()
        for field in ('stockpile_data', 'updated_stockpile_data'):
            state[field]['SP'].update(amt=True, subset='Secondary')
        state['multi_feed_configuration']['tipping_points'][0]['rom_area'] = 'Primary'
        state['multi_feed_configuration']['tipping_points'][1]['rom_area'] = 'Secondary'
        state['hex_sequence_table'] = [dict(hex='SP_CHUNK_001', footprint='SP', balance=100,
            member_hexes=['a', 'b'], sequence=1, grade_streams={}, average_reclaim_rate=100)]
        state['hex_sequence_table_argument'] = deepcopy(state['hex_sequence_table'])
        state['multi_feed_configuration']['amt_submission_signature'] = submission_signature(state)
        approve_sources(state)
        return state

    def test_secondary_opf_grades_reach_inventory_chunks_chart_and_survive_resubmission(self):
        host = loading.OPFProfileLoadingTests().host()
        self.addCleanup(host.deleteLater)
        host.__dict__.update(self.state())
        host.draw_AMT_map = SimpleNamespace(selected_points=deepcopy(host.hex_sequence_table), init_layout=Mock())
        completed = Mock()
        registry = deepcopy(host.grade_reconciliation_registry)
        with patch('classes.ReconciliationFactorResolver.ReconciliationFactorResolver.resolve_source',
                   side_effect=AssertionError('Factor search outside manual reconciliation')), \
             patch('GUI.OPFProfileLoading.build_profiles',
                   side_effect=lambda state, opfs, implementation: build_profiles(state, opfs, UserInputs)) as build, \
             patch('GUI.WorkflowViews.schedule'):
            self.assertTrue(ensure(host, completed))
            self.drain(host)
            self.assertEqual(host.errors, [])
            completed.assert_called_once()
            for row in (host.updated_stockpile_data['SP'], host.hex_sequence_table[0],
                        host.hex_sequence_table_argument[0], host.draw_AMT_map.selected_points[0]):
                self.assertEqual(row['prepared_grade_opf'], OPFS[1])
                self.assertAlmostEqual(row['grade_streams']['adjusted_rom']['FB']['fe'], 47.5)
            shown = chunk_display(host.draw_AMT_map.selected_points[0])['selected_grades']
            self.assertIn(OPFS[1], shown)
            self.assertNotIn('Pending', shown)
            # Submit reads the map's separate copy. It must retain the prepared
            # streams so the exact same cached profile can serve Calendar.
            host.hex_sequence_table = deepcopy(host.draw_AMT_map.selected_points)
            host.hex_sequence_table_argument = deepcopy(host.hex_sequence_table)
            self.assertFalse(ensure(host, completed))
            self.assertEqual(build.call_count, 1)
            self.assertEqual(host.grade_reconciliation_registry, registry)
            self.assertEqual(host._combined_opf_profile_cache[0], profile_signature(vars(host), OPFS))

    def test_chart_publication_preserves_new_geometry_and_rate_edits(self):
        state = self.state()
        profiles = build_profiles(state, OPFS, UserInputs)
        host = SimpleNamespace(**state)
        host.draw_AMT_map = SimpleNamespace(selected_points=deepcopy(host.hex_sequence_table), init_layout=Mock())
        host.draw_AMT_map.selected_points[0]['average_reclaim_rate'] = 500
        with patch('GUI.WorkflowViews.schedule'):
            publish(host, profiles)
        self.assertEqual(host.draw_AMT_map.selected_points[0]['average_reclaim_rate'], 500)
        self.assertTrue(host.draw_AMT_map.selected_points[0]['grade_streams']['adjusted_rom'])
        host.draw_AMT_map.selected_points[0].update(member_hexes=['a'], balance=40, grade_streams={})
        with patch('GUI.WorkflowViews.schedule'):
            sync_map(host)
        self.assertEqual(host.draw_AMT_map.selected_points[0]['grade_streams'], {})

    def test_baseline_mapping_runs_without_factors_and_does_not_apply_adjustments(self):
        from tests.test_reconciliation_application import window, raw_hex
        host = window()
        host._manual_grade_reconciliation = False
        host._apply_amt_component_factors = False
        host.historical_recon_factors = {}
        host.AMT_stockpile_data = {'SP1': [raw_hex()]}
        host.AMT_enrichment_signature = ''
        host.prune_excluded_AMT_state = Mock()
        host.prune_zeroed_amt_chunks = Mock()
        host.opening_stockpile_inventories = Mock()
        host.refresh_AMT_map_data_from_database = Mock()
        with patch.object(UserInputs, 'reconciliation_application', side_effect=AssertionError('No factors needed')):
            self.assertTrue(host.refresh_AMT_enrichment_if_needed({}, allow_pending=True))
            self.assertFalse(host.refresh_AMT_enrichment_if_needed({}, allow_pending=True))
        row = host.AMT_stockpile_data['SP1'][0]
        self.assertTrue(row['grade_streams']['modelled_rom'])
        self.assertTrue(row['grade_streams']['modelled_product'])
        self.assertEqual(row['grade_streams']['adjusted_product'], {})
        host.opening_stockpile_inventories.save_AMT_to_database.assert_called_once()

    def test_tooltips_show_baselines_and_missing_grades_never_become_zero(self):
        from tests.test_amt_geometry_outliers import AMTGeometryOutlierTests
        chart = AMTGeometryOutlierTests().chart()
        chart.selected_points = []
        chart.reclaim_directions = {}
        chart.cut_directions = {}
        chart.data['grade_fe'] = None
        chart.data['grade_streams'] = [dict(modelled_rom={'*': {'fe': 54.25, 'p': 0}}) for _ in chart.data.index]
        fig = chart.generate_scatter_plot('SP1', None, None)
        markers = [t for t in fig.data if t.name == 'Available Hexagons'][0]
        self.assertEqual(markers.customdata[0][2], '54.25%')
        self.assertEqual(markers.customdata[0][3], 'Unavailable')
        self.assertEqual(markers.customdata[0][5], '0.00%')
        self.assertEqual(markers.customdata[0][15], 'Hex modelled ROM')
        label, grades = hex_grade_display({'grade_fe': None})
        self.assertIsNone(grades['fe'])
        self.assertIn('Pending application', chunk_display({'grade_streams': {}})['selected_grades'])

    def test_map_delivery_cannot_overwrite_newly_published_chunk_grades(self):
        from GUI.AMTMapLoading import refresh
        import pandas as pd
        class Host(SimpleNamespace):
            def reconcile_saved_AMT_chunk_grade_streams(self, **kwargs):
                self.hex_sequence_table = deepcopy(self.hex_sequence_table)
        host = Host(hex_sequence_table=[dict(hex='chunk', grade_streams={})],
                    AMT_chunk_settings={}, excluded_amt_footprints=lambda:set())
        host.draw_AMT_map = SimpleNamespace(selected_points=[], fetch_data=lambda:pd.DataFrame(),
            update_chunk_settings=Mock(), get_unique_footprints=lambda:[], clean_up_hex_sequence_table=Mock(),
            update_sequence_counter=Mock(), init_layout=Mock())
        tasks = []
        host.run_background_task = lambda msg, work, done, failed, **kwargs: tasks.append((work, done))
        refresh(host)
        work, done = tasks.pop()
        result = work()
        newer = [dict(hex='chunk', grade_streams={'adjusted_rom': {'FB': {'fe': 53}}})]
        host.hex_sequence_table = deepcopy(newer)
        with patch('GUI.WorkflowViews.schedule'):
            done(result)
        self.assertEqual(host.hex_sequence_table, newer)
        self.assertEqual(host.draw_AMT_map.selected_points, newer)
