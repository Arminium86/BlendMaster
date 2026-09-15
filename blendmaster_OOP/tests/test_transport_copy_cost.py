"""Transport previews isolate physical state without recopying accepted audits."""
from copy import deepcopy
from datetime import timedelta
import unittest
from unittest.mock import patch

from classes.ConveyorCOS import ConveyorCOS, material_event
from tests.test_conveyor_cos import configuration, mat, START


class Evidence(dict):
    def __deepcopy__(self, memo):
        raise AssertionError('Accepted source evidence must not be recopied per payload or trial')


class TransportCopyCostTests(unittest.TestCase):
    def test_splitting_preview_and_checkpoint_reuse_evidence_but_detach_chemistry(self):
        source = mat()
        source['reconciliation'] = Evidence(by_brand={'FB': {'records': [{'score': 90}]}})
        flow = ConveyorCOS(configuration(100, 200, 2), START, {'A': 100},
            [dict(time=START-timedelta(hours=1.5), wmt=100, material=source)])
        flow.add_feed('A', source, 100, START, START+timedelta(hours=1), 100)
        before = flow.balance('A')
        for trial in (flow.fork(), deepcopy(flow)):
            self.assertIs(trial.opening_reconciliation_audits[0], source['reconciliation'])
            queued = trial.points['A']['conveyor'][0]
            self.assertIs(queued['material']['reconciliation'], source['reconciliation'])
            queued['material']['event'].grade_fe = 12
            self.assertEqual(flow.points['A']['conveyor'][0]['material']['event'].grade_fe, 60)
            arrivals = trial.advance(START+timedelta(hours=2), {'A': 100})
            self.assertTrue(arrivals)
            self.assertEqual(flow.balance('A'), before)
            trial.assert_balance()
            for row in arrivals:
                self.assertIs(row['material']['reconciliation'], source['reconciliation'])
                delivered = material_event(row['material']['event'], row['wmt'])
                delivered.grade_fe = 9
                self.assertNotEqual(row['material']['event'].grade_fe, 9)
        self.assertTrue(flow.preview(START+timedelta(hours=2), {'A': 100}))
        self.assertEqual(flow.balance('A'), before)

    def test_timing_preview_matches_rate_change_without_reading_any_material(self):
        flow = ConveyorCOS(configuration(100, 100, 1), START, {'A': 100})
        flow.add_feed('A', mat(), 100, START, START+timedelta(hours=1), 100)
        original = deepcopy(flow.points)
        for rate in (0, 50, 100, 200):
            expected = flow.fork()
            expected.prepare_rates({'A': rate})
            with patch('classes.ConveyorCOS.deepcopy', side_effect=AssertionError('Timing must not copy the graph')):
                actual = flow.conveyor_schedule('A', rate)
            self.assertEqual(actual, [{key: row[key] for key in ('start', 'end', 'rate')}
                for row in expected.points['A']['conveyor']])
        for key in ('start', 'end', 'remaining', 'rate'):
            self.assertEqual(flow.points['A']['conveyor'][0][key], original['A']['conveyor'][0][key])

    def test_multi_lane_capacity_check_does_not_fork_again_per_lane(self):
        from classes.MultiLaneOptimizer import MultiLaneOptimizer
        from classes.PeriodManager import PeriodManager
        from tests.test_multi_lane_optimizer import settings
        from tests import test_decision_levers as fixtures
        events = fixtures.DecisionLeverOptimizerTests
        flow = ConveyorCOS(configuration(100), START, {'A': 100})
        flow.add_feed('A', mat(), 50, START, START+timedelta(hours=.5), 100)
        periods = PeriodManager()
        periods.calculate_periods(START)
        with patch.object(flow, 'fork', wraps=flow.fork) as forks, \
             patch.object(flow, 'conveyor_schedule', wraps=flow.conveyor_schedule) as schedules:
            MultiLaneOptimizer(settings()).run_blending_optimization(
                [events.event('SP1'), events.event('SP2')], events.target(), .5, None, None,
                periods, 'preplan', solver_config=dict(_transport_engine=flow, current_steady_state_datetime=START))
        forks.assert_called_once_with(audit=False)  # Only the physical arrival preview.
        schedules.assert_called_once_with('A', 100)
