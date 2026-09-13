from datetime import datetime, timedelta
from copy import deepcopy
import io
from contextlib import redirect_stdout
import unittest

from classes.ConveyorCOS import ConveyorCOS, material, material_event
from classes.TransportSettings import transport_settings
from classes.MultiLaneOptimizer import MultiLaneOptimizer
from classes.PeriodManager import PeriodManager
from classes.TransportPlanning import initialise_transport
from tests import test_decision_levers as fixture_module
from tests.test_multi_lane_optimizer import settings
from tests.test_multi_feed_integration import make_multi_case

START = datetime(2026, 1, 1)


def configuration(belt=100, cos=0, chunks=2):
    return dict(tipping_points={'A': dict(enabled=True, conveyor_capacity_wmt=belt,
                cos_capacity_wmt=cos, cos_chunks=chunks)})


def mat(fe=60, source='SP1', point='A'):
    return material(fixture_module.DecisionLeverOptimizerTests.event(source, grade_fe=fe), point=point, opf='OPF1')


class ConveyorCOSTests(unittest.TestCase):
    def test_settings_are_optional_and_reject_future_versions_and_invalid_values(self):
        self.assertEqual(transport_settings()['tipping_points'], {})
        for data in [dict(schema_version=999), configuration(-1), configuration(0, 0), configuration(1, 10, 1.2)]:
            with self.assertRaises(ValueError):
                transport_settings(data)

    def test_additive_properties_scale_up_as_well_as_down(self):
        event = fixture_module.DecisionLeverOptimizerTests.event('SP')
        event.source_properties = {'modelled_rom_wmt': event.balance, 'modelled_product_wmt': event.balance*.8}
        one = material_event(event, 1)
        hundred = material_event(one, 100)
        self.assertEqual(hundred.source_properties['modelled_product_wmt'], 80)

    def test_conveyor_delay_preserves_closing_mass_and_stops_at_horizon(self):
        flow = ConveyorCOS(configuration(), START, {'A': 100})
        flow.add_feed('A', mat(), 100, START, START+timedelta(hours=1), 100)
        self.assertEqual(flow.advance(START+timedelta(hours=1), {'A':100}), [])
        self.assertAlmostEqual(flow.balance('A'), 100)
        rows = flow.advance(START+timedelta(hours=1.5), {'A':100})
        self.assertAlmostEqual(sum(r['wmt'] for r in rows), 50)
        self.assertAlmostEqual(flow.balance('A'), 50)
        flow.assert_balance()

    def test_cos_opening_fifo_and_parallel_fill_depletion(self):
        history = [dict(time=START-timedelta(minutes=90), wmt=100, material=mat(50)),
                   dict(time=START-timedelta(minutes=30), wmt=100, material=mat(60,'SP2'))]
        flow = ConveyorCOS(configuration(0,200), START, {'A':100}, history)
        flow.add_feed('A', mat(70,'SP3'), 100, START, START+timedelta(hours=1), 100)
        rows = flow.advance(START+timedelta(hours=1), {'A':100})
        self.assertAlmostEqual(sum(r['wmt'] for r in rows), 100)
        self.assertEqual({r['material']['event'].grade_fe for r in rows}, {50})
        self.assertAlmostEqual(flow.balance('A'), 200)
        rows = flow.advance(START+timedelta(hours=2), {'A':100})
        self.assertEqual({r['material']['event'].grade_fe for r in rows}, {60})

    def test_no_history_does_not_invent_opening_tonnes_or_grades(self):
        flow = ConveyorCOS(configuration(0,200), START, {'A':100})
        self.assertTrue(flow.warnings)
        flow.add_feed('A', mat(), 50, START, START+timedelta(hours=.5), 100)
        self.assertEqual(flow.advance(START+timedelta(hours=1), {'A':100}), [])
        self.assertEqual(flow.balance('A'), 50)

    def test_preview_is_detached_and_cos_off_does_not_drain(self):
        history = [dict(time=START-timedelta(minutes=30), wmt=100, material=mat())]
        flow = ConveyorCOS(configuration(0,100,1), START, {'A':100}, history)
        self.assertEqual(sum(r['wmt'] for r in flow.preview(START+timedelta(hours=1), {'A':100})), 100)
        self.assertEqual(flow.balance('A'), 100)
        self.assertEqual(flow.advance(START+timedelta(hours=1), {'A':0}), [])

    def test_solver_combines_actual_arrival_with_zero_delay_point(self):
        periods = PeriodManager()
        periods.calculate_periods(START)
        history = [dict(time=START-timedelta(minutes=30), wmt=100, material=mat(56))]
        flow = ConveyorCOS(configuration(0,100,1), START, {'A':100}, history)
        build = dict(build_id='b', target_tonnes=1000, target_fe_min=58, target_fe_max=58)
        result = MultiLaneOptimizer(settings()).run_blending_optimization(
            [fixture_module.DecisionLeverOptimizerTests.event('SP1',grade_fe=50), fixture_module.DecisionLeverOptimizerTests.event('SP2',grade_fe=60)], fixture_module.DecisionLeverOptimizerTests.target(), 1,
            None,None,periods,'preplan',solver_config=dict(_transport_engine=flow,current_steady_state_datetime=START,
            target_product_build=build))
        self.assertTrue(result['Linprog_result_object'].success)
        self.assertAlmostEqual(result['product_build_actual_tonnes'],200)
        self.assertAlmostEqual(sum(t['actual_tonnes'] for t in result['transactions']),200)
        self.assertEqual(len(result['transport_tips']),1)
        self.assertEqual({r['grade_fe'] for r in result['transport_arrivals']},{56,60})

    def test_case_tracks_product_at_arrival_and_sources_at_tip(self):
        with redirect_stdout(io.StringIO()):
            case = make_multi_case(builds=[dict(build_name='Product', target_tonnes=2000)])
            case.solver_config['transport_settings'] = configuration(100)
            initialise_transport(case)
            case.run()
        self.assertAlmostEqual(case.results.source_actual_tonnes.sum(),600)
        self.assertAlmostEqual(case.product_build_runtime_states[0]['tonnes'],500)
        self.assertAlmostEqual(case.transport.balance('A'),100)
        self.assertAlmostEqual(case.product_arrival_results.source_arrival_wmt.sum(),500)
        state = case.product_build_runtime_states[0]
        self.assertAlmostEqual(state['grade_fe_metal']/state['grade_fe_weight'],57.6)
        report = case.build_product_build_report()
        self.assertAlmostEqual(report.source_actual_tonnes_to_build.sum(),500)
        self.assertAlmostEqual(report.build_grade_fe.iloc[-1],57.6)

    def test_opening_queue_can_idle_one_crusher_without_violating_minimum_sources(self):
        periods = PeriodManager()
        periods.calculate_periods(START)
        history = [dict(time=START-timedelta(minutes=1), wmt=100, material=mat(56))]
        flow = ConveyorCOS(configuration(100), START, {'A':100}, history)
        cfg = settings()
        cfg['tipping_points'][0]['min_stockpiles'] = 1
        cfg['tipping_points'][0]['max_stockpiles'] = 4
        for point in cfg['tipping_points']:
            point['targets_by_period']['preplan']['brand'] = 'SS'
        result = MultiLaneOptimizer(cfg).run_blending_optimization(
            [fixture_module.DecisionLeverOptimizerTests.event('SP1'),
             fixture_module.DecisionLeverOptimizerTests.event('SP2')],
            fixture_module.DecisionLeverOptimizerTests.target(), .1, None, None,
            periods, 'preplan', solver_config=dict(
                _transport_engine=flow, current_steady_state_datetime=START,
                active_blend_guidance_enabled=True, active_blend_guidance_incentive=10,
                active_blend_guidance=[dict(product_brand='SS', start_datetime=START,
                    end_datetime=START+timedelta(hours=1), stockpiles=['SP1'])]))
        self.assertTrue(result['Linprog_result_object'].success)
        self.assertAlmostEqual(result['tipping_point_results']['A']['crusher_actual_tonnes'], 0)
        self.assertAlmostEqual(result['tipping_point_results']['B']['crusher_actual_tonnes'], 10)
        self.assertEqual(result['transport_tips'], [])
        self.assertAlmostEqual(flow.balance('A'), 100)  # A solve only previews the queue.

    def test_feeding_transport_lane_still_requires_minimum_stockpile_contributions(self):
        periods = PeriodManager()
        periods.calculate_periods(START)
        flow = ConveyorCOS(configuration(100), START, {'A':100})
        cfg = settings()
        cfg['tipping_points'][0].update(min_stockpiles=2, max_stockpiles=2)
        cfg['source_subsets']['SP3'] = 'A'
        result = MultiLaneOptimizer(cfg).run_blending_optimization(
            [fixture_module.DecisionLeverOptimizerTests.event(name) for name in ('SP1','SP2','SP3')],
            fixture_module.DecisionLeverOptimizerTests.target(), 1, None, None,
            periods, 'preplan', min_stockpile_contribution_ratio=.2,
            solver_config=dict(_transport_engine=flow, current_steady_state_datetime=START))
        self.assertTrue(result['Linprog_result_object'].success)
        lane = result['tipping_point_results']['A']
        self.assertAlmostEqual(lane['crusher_actual_tonnes'], 100)
        positive = [r['actual_tonnes'] for r in lane['transactions'] if r['actual_tonnes'] > 0]
        self.assertEqual(len(positive), 2)
        self.assertTrue(all(tonnes >= 20-1e-6 for tonnes in positive))

    def test_nearly_full_chunk_does_not_create_a_rounding_only_solve(self):
        flow = ConveyorCOS(configuration(0,100,1), START, {'A':100})
        quantity = 100-1.4e-6
        flow.add_feed('A', mat(), quantity, START, START+timedelta(hours=1), 100)
        flow.advance(START+timedelta(hours=1), {'A':100})
        self.assertGreater(flow.next_chunk_boundary_hours({'A':100}, 1), .99)
        rows = flow.advance(START+timedelta(hours=2), {'A':100})
        self.assertAlmostEqual(sum(row['wmt'] for row in rows), quantity, places=9)
        self.assertAlmostEqual(flow.balance('A'), 0, places=9)
        flow.assert_balance()

    def test_small_delivery_tail_starts_a_new_chunk_without_losing_mass(self):
        flow = ConveyorCOS(configuration(0,200,2), START, {'A':100})
        quantity = 100+1e-7
        flow.add_feed('A', mat(), quantity, START, START+timedelta(hours=1), 100)
        arrivals = flow.advance(START+timedelta(hours=1), {'A':100})
        self.assertEqual(len(flow.points['A']['chunks']), 2)
        self.assertAlmostEqual(flow.balance('A')+sum(row['wmt'] for row in arrivals), quantity, places=9)
        flow.assert_balance()

    def test_final_reclaim_rounding_is_delivered_without_a_tiny_following_step(self):
        flow = ConveyorCOS(configuration(0,100,1), START, {'A':100})
        flow.add_feed('A', mat(), 100, START, START+timedelta(hours=1), 100)
        flow.advance(START+timedelta(hours=1), {'A':100})
        end = START+timedelta(hours=2, microseconds=-65)
        arrivals = flow.advance(end, {'A':100})
        self.assertAlmostEqual(sum(row['wmt'] for row in arrivals), 100, places=9)
        self.assertAlmostEqual(flow.balance('A'), 0, places=9)
        self.assertEqual(flow.next_chunk_boundary_hours({'A':100}, 1), 1)
        flow.assert_balance()


if __name__ == '__main__':
    unittest.main()
