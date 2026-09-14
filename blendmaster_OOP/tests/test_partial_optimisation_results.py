"""Only fully committed steady states survive a late optimisation failure."""
from collections import deque
from contextlib import closing, redirect_stdout
from copy import deepcopy
from datetime import timedelta
import io
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pandas as pd

from classes.CaseModeller import CaseModeller, SolverRunAborted
from classes.EventData import EventData
from classes.TransportPlanning import initialise_transport
from database.DatabaseContext import database_scope
from execute.Run import Run
from tests.test_conveyor_cos import configuration
from tests.test_material_flow_topology import planning_inputs, SITE
from tests.test_multi_feed_integration import make_multi_case
from tests.test_multi_lane_optimizer import settings


def transport_values(value):
    if isinstance(value, EventData):
        return transport_values(vars(value))
    if isinstance(value, dict):
        return {key: transport_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, deque)):
        return [transport_values(item) for item in value]
    return value


class PartialOptimisationResultsTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.database = Path(folder) / 'partial.db'
        self.enterContext(database_scope(self.database))
        self.enterContext(redirect_stdout(io.StringIO()))

    def case(self, repair=False):
        case = make_multi_case(builds=[dict(build_name='Product', target_tonnes=2000)])
        case.solver_config.update(transport_settings=configuration(100),
            enable_product_build_repair_loop=repair,
            allow_offspec_steady_states_for_product_build=repair)
        initialise_transport(case)
        return case

    def assert_same_completed_state(self, case, expected):
        self.assertEqual(case.current_time, expected.current_time)
        self.assertEqual(case.period_tracker, expected.period_tracker)
        self.assertEqual(case.steady_state_tracker, expected.steady_state_tracker)
        for attribute in ('results', 'build_report', 'product_arrival_results'):
            pd.testing.assert_frame_equal(getattr(case, attribute), getattr(expected, attribute))
        pd.testing.assert_frame_equal(case.build_product_build_report(), expected.build_product_build_report())
        self.assertEqual(case.product_build_runtime_states, expected.product_build_runtime_states)
        self.assertEqual(case.balance_tracker.balance_copy, expected.balance_tracker.balance_copy)
        self.assertEqual(case.balance_tracker.get_physical_balance_history(),
                         expected.balance_tracker.get_physical_balance_history())
        self.assertEqual(case.transport.now, expected.transport.now)
        self.assertEqual(transport_values(case.transport.points), transport_values(expected.transport.points))
        self.assertEqual(case.transport.movements, expected.transport.movements)
        self.assertEqual(case.transport.snapshots, expected.transport.snapshots)
        case.transport.assert_balance()

    def test_late_error_rolls_back_results_inventory_product_and_fifo(self):
        for repair in (False, True):
            with self.subTest(repair=repair):
                expected = self.case(repair)
                expected.run_optimization_step()
                expected.steady_state_tracker += 1
                case = self.case(repair)
                advance = case.advance_time

                def fail_second_clock():
                    if case.steady_state_tracker == 1:
                        # The failing state has already altered all three
                        # accounts before advance_time raises, as in the crash.
                        self.assertGreater(len(case.results), len(expected.results))
                        self.assertGreater(case.transport.now, expected.current_time)
                        self.assertGreater(case.product_build_runtime_states[0]['tonnes'],
                                           expected.product_build_runtime_states[0]['tonnes'])
                        raise ValueError('injected clock failure')
                    advance()

                with patch.object(case, 'advance_time', side_effect=fail_second_clock):
                    with self.assertRaisesRegex(ValueError, 'injected clock failure'):
                        case.run()
                self.assertTrue(case.partial_plan_restored_after_error)
                self.assertFalse(case.partial_plan_restored_after_repair)
                self.assert_same_completed_state(case, expected)

    def test_first_state_failure_has_no_reportable_partial_plan(self):
        case, expected = self.case(), self.case()
        with patch.object(case, 'advance_time', side_effect=ValueError('first state failure')):
            with self.assertRaisesRegex(ValueError, 'first state failure'):
                case.run()
        self.assertFalse(case.partial_plan_restored_after_error)
        self.assert_same_completed_state(case, expected)

    def test_abort_during_commit_also_discards_unfinished_state(self):
        case, expected = self.case(), self.case()
        expected.run_optimization_step()
        expected.steady_state_tracker += 1
        advance = case.advance_time

        def abort_second_clock():
            if case.steady_state_tracker == 1:
                raise SolverRunAborted()
            advance()

        with patch.object(case, 'advance_time', side_effect=abort_second_clock):
            with self.assertRaises(SolverRunAborted):
                case.run()
        self.assert_same_completed_state(case, expected)

    def test_reusing_checkpoint_after_repair_does_not_mutate_saved_prefix(self):
        case, expected = self.case(), self.case()
        for item in (case, expected):
            item.run_optimization_step()
            item.steady_state_tracker += 1
        checkpoint = case.capture_product_build_repair_checkpoint()
        case.restore_product_build_repair_checkpoint(checkpoint)
        case.run_optimization_step()
        case.restore_product_build_repair_checkpoint(checkpoint)
        self.assert_same_completed_state(case, expected)

    def execute_with_clock_failure(self, failing_state):
        start, periods, calendar, stockpiles, _ = planning_inputs()
        calendar['product_targets'] = [dict(build_name='Product', target_tonnes=2000)]
        feed_settings = settings()
        for point in feed_settings['tipping_points']:
            point['targets_by_period'] = {key: deepcopy(point['targets_by_period']['preplan'])
                                          for key in periods.period_keys()}
        runner = Run.__new__(Run)
        runner.case_bridge = Mock()
        # Use a real modeller, solver and report database. Only the remote
        # opening-history fetch and the failing clock operation are replaced.
        advance = CaseModeller.advance_time

        def fail_clock(case):
            if case.steady_state_tracker == failing_state:
                raise ValueError('injected clock failure')
            advance(case)

        with patch('execute.Run.PeriodManager', return_value=periods), \
             patch.object(periods, 'calculate_periods'), \
             patch('setup.TransportOpeningHistory.TransportOpeningHistory.fetch', return_value={'records': []}), \
             patch.object(CaseModeller, 'advance_time', fail_clock):
            result = runner.execute(start, 1, None, 1, stockpiles, calendar, [],
                solver_config={**calendar['solver_config'], 'contingency_plan_count': 1},
                site_context={**SITE, 'multi_feed_settings': feed_settings,
                              'transport_settings': configuration(100)})
        return runner, result

    def test_worker_publishes_completed_prefix_as_partial_and_gui_enables_results(self):
        runner, periods = self.execute_with_clock_failure(1)
        outcome = periods.run_outcome
        self.assertEqual(outcome['status'], 'partial')
        self.assertTrue(outcome['partial_plan_restored_after_error'])
        self.assertFalse(outcome['partial_plan_restored'])
        self.assertIn('injected clock failure', outcome['message'])
        self.assertEqual(outcome['solved_through'], str(runner.case_modeller.start_time + timedelta(hours=1)))
        log = '\n'.join(str(call.args[0]) for call in runner.case_bridge.print.call_args_list)
        self.assertIn('Traceback (most recent call last)', log)
        self.assertIn('ValueError: injected clock failure', log)
        with closing(sqlite3.connect(self.database)) as connection:
            for table in ('optimised_blend_report', 'optimisation_plan_blend_report'):
                saved = pd.read_sql_query(f'SELECT * FROM {table}', connection)
                self.assertEqual(set(saved.steady_state_number), {0})
                self.assertEqual(len(saved), outcome['result_row_count'])
                self.assertEqual(pd.to_datetime(saved.end_datetime).max(), runner.case_modeller.current_time)
            status = pd.read_sql_query('SELECT * FROM optimisation_plan_status', connection)
            self.assertEqual(status[['plan_id', 'status']].to_dict('records'),
                             [dict(plan_id='Primary', status='partial')])
            movements = pd.read_sql_query('SELECT * FROM transport_movements', connection)
            self.assertAlmostEqual(movements.query("movement == 'tip'").physical_rom_wmt.sum(), 100)
        self.assert_gui_results_available(periods)

    def assert_gui_results_available(self, periods):
        from GUI.InitialiseGUI import UserInputs
        from GUI.WorkflowViews import result_presence
        host = SimpleNamespace(_workflow_optimisation_finished=False,
            blend_mode_choice=1, project_load_continuation_pending=False,
            calendar_tab_index='calendar', decision_point_tab_index='decision_point',
            results_tab_index='optimised_blend_sequence', profiles_tab_index='profiles',
            closing_rom_stocks_tab_index='closing_rom', sqlite_reports_tab_index='reports',
            optimised_grade_profile_tab_index='optimised_grades',
            decision_input=Mock(), enter_button=Mock(), decision_select_button=Mock())
        pages = {}
        host.set_page_enabled = lambda page, enabled: pages.update({page: enabled})
        host.update_decision_point_tab_state = lambda: UserInputs.update_decision_point_tab_state(host)
        for name in ('set_start_and_end_datetime', 'activate_manual_setup_tab',
                     'prepopulate_manual_from_optimised_result', 'refresh_sqlite_reports',
                     'refresh_optimisation_plan_selectors', 'start_dash_optimised_charts_thread',
                     'save_active_scenario_state', 'show_page', 'advance_workspace'):
            setattr(host, name, Mock())
        with patch('GUI.InitialiseGUI.QMessageBox.information') as notice:
            UserInputs.finish_run_program(host, periods)
        self.assertTrue(host._workflow_optimisation_finished)
        host.advance_workspace.assert_called_once_with('calendar')
        for page in ('optimised_blend_sequence', 'reports', 'optimised_grades'):
            self.assertTrue(pages[page])
        self.assertTrue(result_presence(self.database)['optimised'])
        self.assertIn('Partial Plan', notice.call_args.args[1])
        self.assertIn('available in Results and Reports', notice.call_args.args[2])
        self.assertNotIn('unsuccessful repair', notice.call_args.args[2])

    def test_worker_reraises_when_no_state_was_completed(self):
        with self.assertRaisesRegex(ValueError, 'injected clock failure'):
            self.execute_with_clock_failure(0)
        with closing(sqlite3.connect(self.database)) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'optimised_blend_report' in tables:
                self.assertEqual(connection.execute('SELECT count(*) FROM optimised_blend_report').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
