"""Small physical boundary intervals must not be mistaken for zero time."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta
import io
from types import SimpleNamespace
import unittest

import pandas as pd

from classes.CaseModeller import CaseModeller
from classes.TransportPlanning import initialise_transport
from tests import test_min_stockpile_feed_duration as feed_fixture
from tests.test_multi_feed_integration import make_multi_case
from tests.test_conveyor_cos import configuration, mat


class SteadyStateClockTests(unittest.TestCase):
    def test_positive_intervals_below_solver_tolerance_advance_exactly(self):
        for microseconds in (1, 1000, 3600):
            for start in (datetime(2026, 9, 14), pd.Timestamp('2026-09-14')):
                with self.subTest(microseconds=microseconds, start_type=type(start)):
                    case = SimpleNamespace(current_time=start, period_tracker='preplan',
                        latest_result_duration=lambda: microseconds / 3_600_000_000,
                        period_for_time=lambda when: 'period_1')
                    CaseModeller.advance_time(case)
                    self.assertEqual(case.current_time, start + timedelta(microseconds=microseconds))
                    self.assertEqual(case.period_tracker, 'period_1')

    def test_zero_invalid_or_unrepresentable_duration_cannot_advance(self):
        for hours in (0, -1, float('nan'), float('inf'), 0.1 / 3_600_000_000):
            with self.subTest(hours=hours):
                start = datetime(2026, 9, 14)
                case = SimpleNamespace(current_time=start, period_tracker='preplan',
                    latest_result_duration=lambda: hours, period_for_time=lambda when: 'period_1')
                with self.assertRaises(ValueError):
                    CaseModeller.advance_time(case)
                self.assertEqual(case.current_time, start)
                self.assertEqual(case.period_tracker, 'preplan')

    def test_one_millisecond_turnover_solves_and_next_step_moves_on(self):
        fixture = feed_fixture.MinimumStockpileFeedDurationTests()
        turnover = datetime(2026, 9, 11, 1)
        start = turnover - timedelta(milliseconds=1)
        other = fixture.stockpile(0, 'BUILDING', state_preplan='Auto', equipment=[], auto_turnover_datetime=turnover)
        case = fixture.modeller(start=start, balance=1000, minimum=0, other_stockpiles=[other])
        with redirect_stdout(io.StringIO()):
            case.run_optimization_step()
            self.assertEqual(case.current_time, turnover)
            self.assertAlmostEqual(case.results.steady_state_duration.iloc[-1] * 3600, .001)
            case.steady_state_tracker += 1
            case.run_optimization_step()
        self.assertEqual(case.current_time, datetime(2026, 9, 11, 6))
        expected = (case.current_time - start).total_seconds() / 3600 * 100
        self.assertAlmostEqual(case.results.source_actual_tonnes.sum(), expected, places=5)
        self.assertAlmostEqual(case.balance_tracker.balance_copy['SP1'], 1000 - expected, places=5)

    def test_tiny_cos_opening_tail_advances_and_preserves_mass(self):
        with redirect_stdout(io.StringIO()):
            case = make_multi_case()
            case.solver_config['transport_settings'] = configuration(0, 100, 1)
            quantity = 100 * .001 / 3600
            case.solver_config['transport_history'] = [dict(
                time=case.start_time - timedelta(minutes=1), wmt=quantity, material=mat())]
            initialise_transport(case)
            case.run_optimization_step()
            self.assertEqual(case.current_time, case.start_time + timedelta(milliseconds=1))
            self.assertEqual(case.transport.now, case.current_time)
            case.transport.assert_balance()
            case.steady_state_tracker += 1
            case.run_optimization_step()
        self.assertGreater(case.current_time, case.start_time + timedelta(milliseconds=1))
        self.assertEqual(case.transport.now, case.current_time)
        self.assertTrue((case.results.steady_state_duration > 0).all())
        case.transport.assert_balance()


if __name__ == '__main__':
    unittest.main()
