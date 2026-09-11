import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pandas as pd

from classes.CaseModeller import CaseModeller, SteadyStateInfeasible
from classes.EquipmentData import EquipmentData
from classes.PeriodManager import PeriodManager
from classes.StockpileData import StockpileData


class MinimumStockpileFeedDurationTests(unittest.TestCase):
    @staticmethod
    def stockpile(balance=139, name="SP1", **overrides):
        values = dict(name=name, balance=balance, equipment=["RC1"],
                      reclaim_threshold=0, grade_fe=60, grade_si=4,
                      grade_al=2, grade_p=0.08, grade_mn=0.1,
                      auto_turnover_datetime=None, is_ready=True, is_AMT=False)
        for period in ("preplan", "period_1", "period_2"):
            values.update({f"state_{period}": "Reclaim", f"max_quantity_{period}": 10000,
                           f"cost_{period}": 0, f"cash_{period}": 0})
        values.update(overrides)
        return StockpileData(**values)

    def modeller(self, start=None, balance=139, minimum=3, builds=None, other_stockpiles=None):
        periods = PeriodManager()
        periods.calculate_periods(start or datetime(2026, 9, 11))
        target = dict(crusher_rate=100, direct_feed_ratio_min=0, direct_feed_ratio_max=0)
        for grade in ("fe", "si", "al", "p", "mn"):
            target.update({f"target_{grade}_min": 0, f"target_{grade}_max": 100})
        with patch("classes.CaseModeller.DatabaseManager"):
            modeller = CaseModeller(
                [self.stockpile(balance), *(other_stockpiles or [])], [],
                [EquipmentData("RC1", 0, 0, 0, 100, 100, 100)],
                {period: dict(target) for period in periods.period_keys()}, pd.DataFrame(),
                periods, 1, [], solver_config={
                    "min_feed_duration_hours": minimum, "max_blend_options_per_steady_state": 1,
                    "blend_option_timeout_seconds": 5,
                }, product_build_settings=builds,
            )
        # Exercise the real solver, decision guardrail, balance update and clock.
        modeller.publish_decision_options = Mock()
        return modeller

    def run_step(self, modeller):
        output = io.StringIO()
        with redirect_stdout(output):
            modeller.run_optimization_step()
        self.assertNotIn("Rejected blend option", output.getvalue())
        return output.getvalue()

    def test_short_calendar_state_accepts_blend_that_can_feed_1_39_hours(self):
        start = datetime(2026, 9, 11, 5)
        modeller = self.modeller(start=start)
        output = self.run_step(modeller)
        self.assertIn("capped at 1.00 hours", output)
        self.assertIn("configured 3.00 hours", output)
        self.assertEqual(modeller.current_time, datetime(2026, 9, 11, 6))
        self.assertEqual(modeller.period_tracker, "period_1")
        self.assertAlmostEqual(modeller.results.iloc[0]["steady_state_duration"], 1)
        self.assertAlmostEqual(modeller.balance_tracker.balance_copy["SP1"], 39)
        self.assertEqual(modeller.solver_config["min_feed_duration_hours"], 3)
        self.assertEqual(modeller.required_min_feed_duration(12), 3)

    def test_dynamic_stockpile_depletion_caps_minimum_after_initial_six_hour_window(self):
        modeller = self.modeller()
        self.assertEqual(modeller.calculate_initial_steady_state_duration(), 6)
        self.run_step(modeller)
        self.assertAlmostEqual(modeller.results.iloc[0]["steady_state_duration"], 1.39)
        self.assertEqual(modeller.current_time, modeller.start_time + timedelta(hours=1.39))
        self.assertAlmostEqual(modeller.balance_tracker.balance_copy["SP1"], 0)
        self.assertEqual(modeller.solver_config["min_feed_duration_hours"], 3)

    def test_product_completion_caps_minimum_to_shortened_state(self):
        build = dict(build_id=1, build_name="Build 1", brand="", target_tonnes=100)
        for grade in ("fe", "si", "al", "p", "mn"):
            build.update({f"target_{grade}_min": 0, f"target_{grade}_max": 100})
        modeller = self.modeller(builds=[build])
        self.run_step(modeller)
        self.assertAlmostEqual(modeller.results.iloc[0]["steady_state_duration"], 1)
        self.assertAlmostEqual(modeller.product_build_runtime_states[0]["tonnes"], 100)
        self.assertAlmostEqual(modeller.balance_tracker.balance_copy["SP1"], 39)

    def test_turnover_caps_minimum_to_shortened_state(self):
        turnover = datetime(2026, 9, 11, 1)
        other = self.stockpile(0, "BUILDING", state_preplan="Auto",
                              equipment=[], auto_turnover_datetime=turnover)
        modeller = self.modeller(other_stockpiles=[other])
        self.run_step(modeller)
        self.assertEqual(modeller.current_time, turnover)
        self.assertAlmostEqual(modeller.results.iloc[0]["steady_state_duration"], 1)

    def test_long_window_keeps_configured_minimum(self):
        modeller = self.modeller(balance=1000)
        output = self.run_step(modeller)
        self.assertNotIn("capped", output)
        self.assertAlmostEqual(modeller.results.iloc[0]["steady_state_duration"], 6)
        self.assertEqual(modeller.required_min_feed_duration(6), 3)

    def test_insufficient_feed_is_rejected_against_effective_minimum(self):
        for duration, expected_minimum in ((2, 2), (4, 3)):
            with self.subTest(duration=duration):
                modeller = self.modeller()
                solve = modeller.optimizer.run_with_dynamic_steady_state

                def longer_window_candidate(*args, **kwargs):
                    candidate = solve(*args, **kwargs)
                    # Isolate the post-solve guardrail: a returned window
                    # must meet its effective minimum, even when below 3 h.
                    candidate["steady_state_duration"] = duration
                    return candidate

                modeller.optimizer.run_with_dynamic_steady_state = longer_window_candidate
                output = io.StringIO()
                with redirect_stdout(output), self.assertRaises(SteadyStateInfeasible):
                    modeller.run_optimization_step()
                self.assertIn(
                    f"potentially feed for 1.39 hours; minimum feed duration is {expected_minimum:.2f} hours",
                    output.getvalue(),
                )
                self.assertEqual(modeller.current_time, modeller.start_time)

    def test_disabled_minimum_remains_disabled(self):
        for minimum in (None, 0):
            with self.subTest(minimum=minimum):
                modeller = self.modeller(start=datetime(2026, 9, 11, 5), minimum=minimum)
                output = self.run_step(modeller)
                self.assertNotIn("capped", output)
                self.assertIsNone(modeller.required_min_feed_duration(1))


if __name__ == "__main__":
    unittest.main()
