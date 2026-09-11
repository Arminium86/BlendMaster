from datetime import datetime
import unittest

from classes.MultiLaneOptimizer import MultiLaneOptimizer
from classes.PeriodManager import PeriodManager
from tests.test_decision_levers import DecisionLeverOptimizerTests as Fixtures
from tests.test_product_quality_limits import build


def settings(**changes):
    return dict(mode="multi_tipping_point", tipping_points=[
        dict(name="A", opf="OPF1", targets_by_period={"preplan": Fixtures.target()}),
        dict(name="B", opf="OPF1", targets_by_period={"preplan": Fixtures.target()})],
        source_subsets={"SP1": "A", "SP2": "B"}, **changes)


class MultiLaneOptimizerTests(unittest.TestCase):
    def solve(self, configuration, events=None, **config):
        periods = PeriodManager()
        periods.calculate_periods(datetime(2026, 1, 1))
        return MultiLaneOptimizer(configuration).run_blending_optimization(
            events or [Fixtures.event("SP1"), Fixtures.event("SP2")], Fixtures.target(), 1,
            None, None, periods, "preplan", solver_config=config)

    def test_simultaneous_points_keep_separate_rates_and_identity(self):
        result = self.solve(settings())
        self.assertTrue(result["Linprog_result_object"].success)
        self.assertAlmostEqual(result["crusher_actual_tonnes"], 200)
        self.assertEqual({(r["source"], r["tipping_point"], r["opf"]) for r in result["transactions"]},
                         {("SP1", "A", "OPF1"), ("SP2", "B", "OPF1")})
        self.assertEqual([r["crusher_rate_output"] for r in result["tipping_point_results"].values()], [100, 100])

    def test_cross_transfer_never_feeds_one_stockpile_to_two_points(self):
        cfg = settings(rehandle_rules=[dict(subset="A", tipping_point="B", allowed=True)])
        result = self.solve(cfg, [Fixtures.event("SP1", balance=500)])
        self.assertTrue(result["Linprog_result_object"].success)
        self.assertAlmostEqual(result["crusher_actual_tonnes"], 100)
        self.assertEqual(len({r["tipping_point"] for r in result["transactions"] if r["actual_tonnes"] > 0}), 1)

    def test_route_reclaim_rate_is_specific_to_source_and_point(self):
        cfg = settings(route_reclaim_rates={"SP1": {"A": 30}, "SP2": {"B": 70}})
        result = self.solve(cfg)
        self.assertEqual({r["source"]: r["actual_tonnes"] for r in result["transactions"]}, {"SP1": 30, "SP2": 70})

    def test_crusher_constraints_remain_independent(self):
        cfg = settings()
        cfg["tipping_points"][0]["targets_by_period"]["preplan"]["target_fe_min"] = 61
        result = self.solve(cfg, [Fixtures.event("SP1", grade_fe=60), Fixtures.event("SP2", grade_fe=62)])
        self.assertEqual({r["source"] for r in result["transactions"] if r["actual_tonnes"] > 0}, {"SP2"})

    def test_product_build_combines_both_point_contributions(self):
        result = self.solve(settings(), [Fixtures.event("SP1", grade_fe=56), Fixtures.event("SP2", grade_fe=60)],
                            target_product_build=build(target_mode="soft", target_fe_target=58))
        self.assertTrue(result["Linprog_result_object"].success)
        self.assertAlmostEqual(result["crusher_actual_grade_fe"], 58)
        self.assertAlmostEqual(result["product_build_actual_tonnes"], 200)
