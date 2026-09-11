import copy
import io
import json
import unittest
from contextlib import redirect_stdout

import pandas as pd

from classes.CaseModeller import CaseModeller
from classes.DataLoader import DataLoader
from classes.MaterialFlowTopology import planning_topology
from tests.test_material_flow_topology import planning_inputs, SITE
from tests.test_multi_lane_optimizer import settings
from classes.OPFSourceProfiles import register_profiles, prepare_inventory_profiles
from classes.GradeStreams import legacy_grade_streams
from classes.MultiFeedSettings import multi_feed_settings


def make_multi_case(configuration=None, builds=None):
    start, periods, calendar, stockpiles, _ = planning_inputs()
    configuration = configuration or settings()
    for point in configuration['tipping_points']:
        point['targets_by_period'] = {key: copy.deepcopy(point['targets_by_period']['preplan']) for key in periods.period_keys()}
    calendar['solver_config']['multi_feed_settings'] = configuration
    calendar['solver_config']['product_builds_configured'] = bool(builds)
    if configuration['mode'] == 'combined_opf':
        profiles = {}
        for point in configuration['tipping_points']:
            profile_rows = copy.deepcopy(stockpiles)
            for row in profile_rows.values():
                row['grade_streams'] = legacy_grade_streams(row)
            profiles[point['opf']] = dict(scenario_id=point['opf'], inventory=profile_rows, chunks={}, fields=[], brands=['*'])
        register_profiles(calendar['solver_config'], profiles)
        calendar['solver_config']['multi_feed_settings'] = multi_feed_settings(configuration)
        prepare_inventory_profiles(stockpiles, [], calendar['solver_config'])
    piles, blocks, equipment, targets = DataLoader(stockpiles, calendar, pd.DataFrame(), [], periods).load_data()
    return CaseModeller(piles, blocks, equipment, targets, pd.DataFrame(), periods, 1, [],
                        solver_config=calendar['solver_config'], product_build_settings=builds or [], site_context=SITE)


class MultiFeedIntegrationTests(unittest.TestCase):
    def test_combined_opf_separate_builds_advance_independently(self):
        cfg = settings()
        cfg['mode'] = 'combined_opf'
        cfg['tipping_points'][1]['opf'] = 'OPF2'
        builds = [dict(build_name='One', opf='OPF1', target_tonnes=150),
                  dict(build_name='Two', opf='OPF2', target_tonnes=1000)]
        with redirect_stdout(io.StringIO()):
            case = make_multi_case(cfg, builds)
            case.run()
        self.assertAlmostEqual(case.product_build_runtime_states[0]['tonnes'], 150)
        self.assertAlmostEqual(case.product_build_runtime_states[1]['tonnes'], 300)
        feed = case.results[case.results.source_actual_tonnes > 0]
        self.assertEqual(feed.groupby('opf').source_actual_tonnes.sum().to_dict(), {'OPF1': 150, 'OPF2': 300})
        report = case.build_product_build_report()
        self.assertTrue((report.opf == report.contributing_opf).all())
        self.assertEqual(report.groupby('opf').source_actual_tonnes_to_build.sum().to_dict(), {'OPF1': 150, 'OPF2': 300})

    def test_combined_shared_build_receives_both_opfs(self):
        cfg = settings(allow_opf_compensation=True)
        cfg['mode'] = 'combined_opf'
        cfg['tipping_points'][1]['opf'] = 'OPF2'
        with redirect_stdout(io.StringIO()):
            case = make_multi_case(cfg, [dict(build_name='Shared', opf='OPF1, OPF2', target_tonnes=1000)])
            case.run()
        self.assertAlmostEqual(case.product_build_runtime_states[0]['tonnes'], 600)
        report = case.build_product_build_report()
        self.assertEqual(set(report.contributing_opf), {'OPF1', 'OPF2'})
        self.assertAlmostEqual(report.build_grade_fe.iloc[-1], 58)

    def test_three_period_shared_inventory_and_identity(self):
        with redirect_stdout(io.StringIO()):
            case = make_multi_case()
            case.run()
        feed = case.results[case.results.source_actual_tonnes > 0]
        self.assertAlmostEqual(feed.source_actual_tonnes.sum(), 600)
        self.assertEqual(set(feed.tipping_point), {'A', 'B'})
        self.assertEqual(feed.groupby('source').source_actual_tonnes.sum().to_dict(), {'SP1': 300, 'SP2': 300})
        self.assertTrue((feed.source_closing_balance >= 0).all())
        self.assertTrue((feed.crusher_rate_output == 100).all())

    def test_graph_shares_physical_sources_and_opf(self):
        cfg = settings(rehandle_rules=[dict(subset='A', tipping_point='B', allowed=True)])
        graph = planning_topology(multi_feed=cfg, site_context=SITE, sources=[dict(source='SP1', source_type='stockpile')])
        self.assertEqual(sum(n['node_type'] == 'source' for n in graph['nodes']), 1)
        self.assertEqual(sum(n['node_type'] == 'tipping_point' for n in graph['nodes']), 2)
        self.assertEqual(sum(n['node_type'] == 'opf' for n in graph['nodes']), 1)
        self.assertEqual(graph, json.loads(json.dumps(graph)))


if __name__ == '__main__':
    unittest.main()
