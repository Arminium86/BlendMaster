from datetime import timedelta
import unittest
from classes.ConveyorCOS import ConveyorCOS
from classes.MultiLaneOptimizer import MultiLaneOptimizer
from classes.PeriodManager import PeriodManager
from tests.test_conveyor_cos import START, mat, configuration, fixture_module
from tests.test_multi_lane_optimizer import settings


class ContinuousCOSOpeningTests(unittest.TestCase):
    def test_opening_chunk_grades_weight_boundary_movements_by_tonnes(self):
        history = [dict(time=START-timedelta(hours=2),wmt=75,material=mat(50,'old')),
                   dict(time=START-timedelta(hours=1),wmt=25,material=mat(70,'new'))]
        flow = ConveyorCOS(configuration(0,100,2),START,{'A':100},history)
        chunks = flow.points['A']['chunks']
        self.assertAlmostEqual(flow.component_grade(chunks[0]['components'],'fe'),50)
        self.assertAlmostEqual(flow.component_grade(chunks[1]['components'],'fe'),60)

    def test_opening_material_is_ordered_by_tonnes_and_new_tipping_starts_immediately(self):
        history = [dict(time=START-timedelta(hours=30),wmt=20000,material=mat(50,'old')),
                   dict(time=START-timedelta(minutes=2),wmt=35000,material=mat(60,'middle')),
                   dict(time=START-timedelta(seconds=1),wmt=5000,material=mat(65,'belt'))]
        flow = ConveyorCOS(configuration(5000,50000,10),START,{'A':6000},history)
        state = flow.points['A']
        self.assertEqual(len(state['chunks']),10)
        self.assertTrue(all(c['sealed'] and c['wmt']==5000 for c in state['chunks']))
        self.assertAlmostEqual(flow.balance('A'),55000)
        self.assertEqual(state['conveyor'][0]['start'],START)
        flow.add_feed('A',mat(70,'planned'),6000,START,START+timedelta(hours=1),6000)
        output = flow.advance(START+timedelta(hours=1),{'A':6000})
        self.assertAlmostEqual(sum(r['wmt'] for r in output),6000)
        self.assertEqual({r['material']['event'].grade_fe for r in output},{50})
        self.assertAlmostEqual(flow.balance('A'),55000)
        fills = [r for r in flow.movements if r['movement']=='cos_inflow']
        self.assertEqual(min(r['start_datetime'] for r in fills if r['source']=='belt'),START)
        self.assertEqual(min(r['start_datetime'] for r in fills if r['source']=='planned'),START+timedelta(minutes=50))
        flow.assert_balance()

    def test_cos_adds_chunks_beyond_opening_tonnes_without_capacity_failure(self):
        flow = ConveyorCOS(configuration(0,50000,10),START,{'A':5000},
            [dict(time=START-timedelta(hours=1),wmt=50000,material=mat(50,'opening'))])
        # Independent incoming intervals can accumulate beyond the opening
        # target. The solver owns crusher throughput, not the COS store.
        flow.add_feed('A',mat(60,'new'),10000,START,START+timedelta(hours=1),10000)
        output = flow.advance(START+timedelta(hours=1),{'A':5000})
        self.assertAlmostEqual(sum(r['wmt'] for r in output),5000)
        self.assertAlmostEqual(flow.balance('A'),55000)
        self.assertEqual(len(flow.points['A']['chunks']),11)
        flow.assert_balance()

    def test_joint_solver_can_tip_at_start_with_full_opening_belt_and_cos(self):
        periods = PeriodManager(); periods.calculate_periods(START)
        history = [dict(time=START-timedelta(minutes=1),wmt=300,material=mat(56))]
        flow = ConveyorCOS(configuration(100,200),START,{'A':100},history)
        result = MultiLaneOptimizer(settings()).run_blending_optimization(
            [fixture_module.DecisionLeverOptimizerTests.event('SP1')],
            fixture_module.DecisionLeverOptimizerTests.target(),1,None,None,periods,'preplan',
            solver_config=dict(_transport_engine=flow,current_steady_state_datetime=START))
        self.assertTrue(result['Linprog_result_object'].success)
        self.assertAlmostEqual(sum(t['wmt'] for t in result['transport_tips']),100)
        self.assertTrue(all(t['start']==START for t in result['transport_tips']))
        self.assertAlmostEqual(sum(r['actual_tonnes'] for r in result['transport_arrivals']),100)
