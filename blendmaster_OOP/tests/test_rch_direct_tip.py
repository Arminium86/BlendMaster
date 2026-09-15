import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pandas as pd
from classes.ExpitDataHandler import ExpitDataHandler
from classes.DataLoader import DataLoader
from classes.DirectTipLimits import validate_direct_tip_sources
from classes.PlanningPrerequisites import PlanningPrerequisiteError
from classes.PeriodManager import PeriodManager
from execute.Run import Run
from tests.test_dual_schedule_ingestion import reserve_row
from tests.test_decision_levers import DecisionLeverOptimizerTests as Fixtures


class RCHDirectTipTests(unittest.TestCase):
    def points(self):
        return [dict(name='OPF02_PC', opf='CC OPF02', direct_tip_enabled=True,
                     targets_by_period={'preplan': {**Fixtures.target(), 'direct_feed_ratio_min': .1}}),
                dict(name='HAL_PC', opf='CC OPF01', direct_tip_enabled=True,
                     targets_by_period={'preplan': Fixtures.target()})]

    def rules(self):
        return [dict(grade_block_source='PIT_A', crusher_destination='RCH')]

    def test_rch_rule_survives_combined_preparation_loader_and_routing(self):
        start = pd.Timestamp('2026-01-01').to_pydatetime()
        cfg = dict(mode='combined_opf', tipping_points=self.points())
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'schedule.csv'
            pd.DataFrame([reserve_row('Reserves/CC/PIT_A/BLOCK_1', 'PIT_A', 'Stockpiles/SP_A', 100,
                                     '01/01/2026 00:00', '01/01/2026 01:00')]).to_csv(path, index=False)
            payloads = Run.__new__(Run).prepare_expit_payload_transactions(start, 1, path,
                site_context=dict(mine='CC', opf='CC OPF01', multi_feed_settings=cfg,
                                  direct_tip_movement_rules=self.rules()), two_wp_file_path=path)
        self.assertGreater(len(payloads), 0)
        self.assertTrue(payloads.direct_tip_eligible.all())
        routes = ExpitDataHandler.direct_tip_routes(payloads.to_dict('records'), self.points(), 'CC', self.rules())
        self.assertTrue(all(points == ['OPF02_PC'] for points in routes.values()))
        periods = PeriodManager(); periods.calculate_periods(start)
        solver = dict(direct_tip_enabled=True, direct_tip_point_by_payload=routes, multi_feed_settings=cfg)
        loader = DataLoader({}, {'solver_config': solver, 'site_context': {'mine': 'CC'}}, payloads, [], periods)
        blocks = loader.create_grade_block_data_objects(payloads)
        self.assertEqual(len(blocks), len(payloads))
        validate_direct_tip_sources(solver, blocks, {'preplan': Fixtures.target()}, payload_count=len(payloads))

    def test_alias_does_not_grant_other_sources_or_other_opfs_permission(self):
        handler = ExpitDataHandler.__new__(ExpitDataHandler)
        handler.operational_mine, handler.operational_opf = 'CC', 'CC OPF01'
        handler.selected_crusher_names = ['OPF02_PC']
        handler.direct_tip_movement_rules = self.rules()
        self.assertTrue(handler._direct_tip_rule_matches('Reserves/CC/PIT_A/1'))
        self.assertFalse(handler._direct_tip_rule_matches('Reserves/CC/PIT_B/1'))
        handler.selected_crusher_names = ['OPF01_PC', 'HAL_PC']
        self.assertFalse(handler._direct_tip_rule_matches('Reserves/CC/PIT_A/1'))
        self.assertFalse(ExpitDataHandler.crusher_destination_matches_point('RCH', 'CB', 'OPF02_PC'))
        self.assertFalse(ExpitDataHandler.crusher_destination_matches_point('RCH', None, 'OPF02_PC'))

    def test_positive_minimum_without_eligible_routes_is_actionable(self):
        config = dict(multi_feed_settings=dict(mode='combined_opf', tipping_points=self.points()))
        with self.assertRaises(PlanningPrerequisiteError) as caught:
            validate_direct_tip_sources(config, [], {'preplan': Fixtures.target()}, payload_count=1345)
        self.assertEqual(caught.exception.workflow_page, 'guidance_schedules')
        self.assertIn('OPF02_PC', str(caught.exception))
        self.assertIn('10.0%', str(caught.exception))
        self.assertIn('1345 payloads', str(caught.exception))
        config['direct_tip_point_by_payload'] = {'GB': ['HAL_PC']}
        with self.assertRaises(PlanningPrerequisiteError):
            validate_direct_tip_sources(config, [SimpleNamespace(name='GB', balance=100)], {'preplan': Fixtures.target()})

    def test_operational_alias_uses_rch_travel_evidence(self):
        payload = dict(direct_tip_id='GB', start_datetime='2026-01-01 00:00', haulage=dict(
            loading_hours=.1, routes={'RCH': dict(LoadedTravel=10, SpotAtDump=1, Dumping=1, basis='RCH route')},
            fallback=dict(LoadedTravel=99, SpotAtDump=1, Dumping=1)))
        host = SimpleNamespace(solver_config={'direct_tip_point_by_payload': {'GB': ['OPF02_PC']}},
                               calendar_inputs={'site_context': {'mine': 'CC'}})
        result = DataLoader.direct_tip_timing(host, payload)
        self.assertEqual(result['direct_tip_arrivals']['OPF02_PC'], pd.Timestamp('2026-01-01 00:18'))

    def test_preflight_does_not_require_direct_tip_for_an_inactive_opf_or_future_period(self):
        config = dict(product_builds_configured=True,
                      multi_feed_settings=dict(mode='combined_opf', tipping_points=self.points()))
        validate_direct_tip_sources(config, [], {'preplan': Fixtures.target()},
            calendar={'product_targets': [{'opf': 'CC OPF01', 'target_tonnes': 100}]})
        config['product_builds_configured'] = False
        target = config['multi_feed_settings']['tipping_points'][0]['targets_by_period']
        target['period_1'] = target['preplan']
        target['preplan'] = {**Fixtures.target(), 'direct_feed_ratio_min': 0}
        validate_direct_tip_sources(config, [], {'preplan': Fixtures.target(), 'period_1': Fixtures.target()})
