import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
import pandas as pd
from classes.SavedResultViews import read_report, plan_names, product_from_feed
from classes.SolverPresets import make_preset, save_preset, matching_preset
from classes.PlannerPresentation import visible_columns, chunk_display
from GUI.SavedGradeCharts import grade_figures


class SavedResultViewTests(unittest.TestCase):
    def test_plan_and_result_types_never_fall_back_to_another_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'site.db'
            with closing(sqlite3.connect(path)) as connection:
                pd.DataFrame([dict(plan_id='Primary', source='O')]).to_sql('optimisation_plan_blend_report', connection, index=False)
                pd.DataFrame([dict(plan_id='Primary', source='M')]).to_sql('manual_plan_blend_report', connection, index=False)
            self.assertEqual(['Primary'], plan_names(path, 'manual'))
            self.assertEqual(['M'], read_report(path, 'manual').source.tolist())
            self.assertTrue(read_report(path, 'manual', 'Missing').empty)
            self.assertEqual(['O'], read_report(path).source.tolist())

    def test_manual_product_projection_does_not_multiply_cumulative_builds(self):
        row = dict(start_datetime='2026-09-13 06:00', end_datetime='2026-09-13 07:00',
                   steady_state_number=0, product_build_id='B1', product_build_name='Build 1',
                   product_build_opf='OPF1', product_build_closing_tonnes=300,
                   product_build_grade_fe=61, product_build_target_fe_target=62)
        product = product_from_feed(pd.DataFrame([{**row, 'source': 'SP1'}, {**row, 'source': 'SP2'}]))
        self.assertEqual(1, len(product))
        self.assertEqual(300, product.iloc[0].build_closing_tonnes)
        self.assertEqual(62, product.iloc[0].target_fe_target)

    def test_all_points_and_quality_lines_are_drawn_without_clipping(self):
        feed = pd.DataFrame([dict(opf='OPF1', tipping_point=p, steady_state_number=0,
            start_datetime='2026-09-13 06:00', end_datetime='2026-09-13 07:00',
            crusher_actual_grade_fe=g) for p, g in [('PC1', 60), ('PC2', 62)]])
        product = pd.DataFrame([dict(opf='OPF1', product_build_lane='product@OPF1', product_build_id='B1',
            product_build_name='Build 1', steady_state_start_datetime='2026-09-13 06:00',
            steady_state_end_datetime='2026-09-13 07:00', build_grade_fe=61,
            target_fe_lql=58, target_fe_target=61, target_fe_hql=64)])
        fig = grade_figures(feed, product)[0]
        self.assertEqual(6, len(fig.data))
        self.assertTrue(any('PC2' in trace.name for trace in fig.data))
        self.assertTrue(any(trace.name == 'Build: OPF1 / Build 1' for trace in fig.data))
        self.assertTrue(all('@' not in trace.name for trace in fig.data))
        self.assertLess(fig.layout.yaxis.range[0], 58)
        self.assertGreater(fig.layout.yaxis.range[1], 64)

    def test_presets_copy_preferences_but_not_site_fields(self):
        config = dict(prefer_amt_stockpiles=True, transport_settings={'enabled': True},
                      custom_constraints=[dict(name='a')])
        composition = dict(min_stockpiles=1, max_stockpiles=3, min_stockpile_contribution_ratio=.1)
        preset = make_preset('Wet plan', config, composition)
        config['custom_constraints'][0]['name'] = 'changed'
        self.assertNotIn('transport_settings', preset['solver_config'])
        self.assertEqual('a', preset['solver_config']['custom_constraints'][0]['name'])
        library = save_preset([], preset)
        self.assertEqual('Wet plan', matching_preset(library, preset['solver_config'], composition))
        self.assertEqual(1, len(save_preset(library, make_preset('WET PLAN', {}, composition))))

    def test_operational_projection_keeps_selected_stream_and_exact_mapped_quantities(self):
        columns = ['Source', 'Modelled ROM Grades', 'Adjusted Product Grades (SS)', 'source_grade_adjusted_rom_fe', 'grade_adjusted_product_ss_fe']
        self.assertEqual(['Source', 'Adjusted Product Grades (SS)', 'grade_adjusted_product_ss_fe'], visible_columns(columns, 'adjusted_product'))
        row = dict(footprint='SP1', hex='Chunk 1', sequence=1, balance=1000,
                   source_properties={'rom_selected': 900, 'product_selected': 650},
                   grade_streams={'adjusted_product': {'SS': dict(fe=61, p=.0522)}})
        shown = chunk_display(row, 'adjusted_product', 'rom_selected', 'product_selected')
        self.assertEqual(900, shown['rom_tonnes'])
        self.assertEqual(650, shown['product_tonnes'])
        self.assertIn('0.0522', shown['selected_grades'])
        self.assertEqual(1000, row['balance'])


if __name__ == '__main__':
    unittest.main()
