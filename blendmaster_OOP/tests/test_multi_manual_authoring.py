import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd
from PyQt5.QtWidgets import QApplication, QWidget, QMessageBox
from PyQt5.QtCore import Qt
if QApplication.instance() is None:
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
from classes.MultiManualPlan import MultiManualPlanner, from_report, definitions
from classes.MultiFeedCalendar import calendar_key
from GUI.MultiManualAuthoring import MultiManualAuthoring


START = datetime(2026, 1, 1, 6)


def inputs():
    stock = {s: dict(balance=1000, grade_fe=g, grade_si=4, grade_al=2, grade_p=.04, grade_mn=.1, subset='ROM')
             for s, g in [('S1', 60), ('S2', 55), ('S3', 58)]}
    settings = dict(mode='multi_tipping_point', tipping_points=[dict(name=p, opf='OPF01', rom_area='ROM') for p in ('A', 'B')],
                    source_subsets={s: 'ROM' for s in stock})
    calendar = {calendar_key(p, f): {'Preplan': 1000} for p in ('A', 'B') for f in ('crusher_rate', 'max_reclaim_rate')}
    calendar['site_context'] = dict(multi_feed_settings=settings, opf='OPF01')
    drafts = {p: dict(recipes=[dict(id='Recipe 1', sources=[dict(source=s, weight=1, reclaim_rate=200)])],
        sequence=[{'Blend ID': 'Recipe 1', 'Start Datetime': START, 'End Datetime': START+timedelta(hours=1)}],
        rates={'Preplan': 100}, allocations={}) for p, s in [('A', 'S1'), ('B', 'S2')]}
    return drafts, stock, [], pd.DataFrame(), {'preplan_start': START, 'preplan_end': START+timedelta(hours=12)}, [], calendar, {}


class MultiManualCalculationTests(unittest.TestCase):
    def test_new_recipes_work_without_any_optimisation_result(self):
        planner = MultiManualPlanner(*inputs())
        states = planner.build_steady_states(); report = planner.build_report(states)
        self.assertEqual(set(report.tipping_point), {'A', 'B'})
        self.assertAlmostEqual(report.source_actual_tonnes.sum(), 200)
        self.assertEqual(set(report.blend_ID), {'Recipe 1'})

    def test_weight_changes_and_new_source_change_the_physical_report(self):
        args = list(inputs())
        args[0]['A']['recipes'][0]['sources'].append(dict(source='S3', weight=3))
        planner = MultiManualPlanner(*args)
        report = planner.build_report(planner.build_steady_states())
        rows = report[report.tipping_point.eq('A')].set_index('source')
        self.assertAlmostEqual(rows.loc['S1', 'source_actual_tonnes'], 25)
        self.assertAlmostEqual(rows.loc['S3', 'source_actual_tonnes'], 75)
        self.assertAlmostEqual(rows.loc['S3', 'source_blend_ratio'], .75)

    def test_shared_inventory_advances_when_source_moves_between_points(self):
        args = list(inputs())
        args[0]['B']['recipes'][0]['sources'][0]['source'] = 'S1'
        args[0]['B']['sequence'][0].update({'Start Datetime': START+timedelta(hours=1), 'End Datetime': START+timedelta(hours=2)})
        planner = MultiManualPlanner(*args)
        report = planner.build_report(planner.build_steady_states())
        self.assertEqual(report.source_opening_balance.tolist(), [1000, 900])
        self.assertEqual(report.source_closing_balance.tolist(), [900, 800])
        args[0]['B']['sequence'][0]['Start Datetime'] = START
        with self.assertRaisesRegex(ValueError, 'overlapping reclaim'):
            MultiManualPlanner(*args).build_steady_states()

    def test_recipe_reclaim_rates_and_calendar_rates_are_hard_limits(self):
        args = list(inputs()); args[0]['A']['rates']['Preplan'] = 1001
        with self.assertRaisesRegex(ValueError, 'Calendar limit'): MultiManualPlanner(*args)
        args = list(inputs()); args[0]['A']['recipes'][0]['sources'][0]['reclaim_rate'] = 50
        planner = MultiManualPlanner(*args)
        with self.assertRaisesRegex(ValueError, 'reclaim rate exceeded'): planner.build_report(planner.build_steady_states())

    def test_combined_opf_uses_its_own_source_chemistry(self):
        args = list(inputs()); settings = args[6]['site_context']['multi_feed_settings']
        settings['mode'] = 'combined_opf'; settings['tipping_points'][1]['opf'] = 'OPF02'
        other = deepcopy(args[1]); other['S2']['grade_fe'] = 62
        args[6]['site_context']['opf_profiles'] = {
            'OPF01': dict(inventory=args[1], chunks={}), 'OPF02': dict(inventory=other, chunks={})}
        planner = MultiManualPlanner(*args); report = planner.build_report(planner.build_steady_states())
        self.assertEqual(report.loc[report.tipping_point.eq('B'), 'source_grade_fe'].iloc[0], 62)

    def test_amt_depletion_is_shared_across_points_with_different_opf_grades(self):
        args = list(inputs())
        args[2] = [dict(footprint='S1', hex='CH1', sequence=1, balance=50, grade_fe=50),
                   dict(footprint='S1', hex='CH2', sequence=2, balance=150, grade_fe=65)]
        args[0]['B']['recipes'][0]['sources'][0]['source'] = 'S1'
        args[0]['B']['sequence'][0].update({'Start Datetime': START+timedelta(hours=1), 'End Datetime': START+timedelta(hours=2)})
        planner = MultiManualPlanner(*args); report = planner.build_report(planner.build_steady_states())
        self.assertEqual(report.loc[report.tipping_point.eq('B'), 'source_grade_fe'].iloc[0], 65)
        self.assertEqual(report.source_actual_tonnes.sum(), 200)

    def test_direct_tip_review_can_satisfy_a_hard_minimum(self):
        args = list(inputs())
        args[6][calendar_key('A', 'direct_feed_ratio_min')] = {'Preplan': .1}
        args[3] = pd.DataFrame([dict(source='GB', direct_tip_id='payload-1', direct_tip_eligible=True,
            delivered_datetime=START+timedelta(minutes=5), payload=20, crusher='A', source_grade_fe=60)])
        planner = MultiManualPlanner(*args); states = planner.build_steady_states()
        with self.assertRaisesRegex(ValueError, 'direct.tip|Direct.tip'): planner.build_report(states)
        state = next(s for s in states if s['tipping_point'] == 'A')
        self.assertEqual(len(state['direct_tip_candidates']), 1)
        report = planner.build_report(states, {state['state_key']: {'GB': 20}})
        self.assertEqual(report.loc[report.source_type.eq('grade_block'), 'source_actual_tonnes'].sum(), 20)
        self.assertEqual(report.source_actual_tonnes.sum(), 200)

    def test_imported_direct_tip_does_not_reserve_stockpile_tonnes_twice(self):
        args = list(inputs())
        args[3] = pd.DataFrame([dict(source='GB', direct_tip_id='truck', direct_tip_eligible=True,
            delivered_datetime=START+timedelta(minutes=5), payload=50, destination='A', source_grade_fe=60)])
        planner = MultiManualPlanner(*args); states = planner.build_steady_states()
        selected = {next(s['state_key'] for s in states if s['tipping_point'] == 'A'): {'GB': 50}}
        original = planner.build_report(states, selected)
        args[0] = from_report(original, args[1]); args[1]['S1']['balance'] = 50
        copied = MultiManualPlanner(*args); states = copied.build_steady_states()
        replay = copied.build_report(states, copied.imported_allocations(states))
        self.assertEqual(replay.source_actual_tonnes.sum(), 200)
        self.assertEqual(replay.loc[replay.source.eq('S1'), 'source_closing_balance'].iloc[-1], 0)

    def test_projected_inventory_cannot_be_used_before_its_receipts(self):
        args = list(inputs()); args[0].pop('B')
        source = args[0]['A']['recipes'][0]['sources'][0]
        source.update(projected=True, projection=dict(balance=1200, available=START+timedelta(hours=1),
            material=dict(grade_fe=62)))
        with self.assertRaisesRegex(ValueError, 'only available after'):
            MultiManualPlanner(*args).build_steady_states()
        args[0]['A']['sequence'][0].update({'Start Datetime': START+timedelta(hours=1), 'End Datetime': START+timedelta(hours=2)})
        planner = MultiManualPlanner(*args); report = planner.build_report(planner.build_steady_states())
        self.assertEqual(report.source_opening_balance.iloc[0], 1200)
        self.assertEqual(report.source_grade_fe.iloc[0], 62)

    def test_three_points_across_two_opfs_produce_one_shared_manual_plan(self):
        args = list(inputs()); settings = args[6]['site_context']['multi_feed_settings']
        settings['mode'] = 'combined_opf'
        settings['tipping_points'].append(dict(name='C', opf='OPF02', rom_area='ROM'))
        args[0]['C'] = deepcopy(args[0]['A']); args[0]['C']['recipes'][0]['sources'][0]['source'] = 'S3'
        for field in ('crusher_rate', 'max_reclaim_rate'):
            args[6][calendar_key('C', field)] = {'Preplan': 1000}
        second = deepcopy(args[1]); second['S3']['grade_fe'] = 63
        args[6]['site_context']['opf_profiles'] = {
            'OPF01': dict(inventory=args[1], chunks={}), 'OPF02': dict(inventory=second, chunks={})}
        planner = MultiManualPlanner(*args); report = planner.build_report(planner.build_steady_states())
        self.assertEqual(set(report.tipping_point), {'A', 'B', 'C'})
        self.assertEqual(report.source_actual_tonnes.sum(), 300)
        self.assertEqual(report.loc[report.tipping_point.eq('C'), 'source_grade_fe'].iloc[0], 63)

    def test_direct_tip_payload_cannot_be_spent_at_two_tipping_points(self):
        args = list(inputs())
        args[3] = pd.DataFrame([dict(source='GB', direct_tip_id='same-truck', direct_tip_eligible=True,
            delivered_datetime=START+timedelta(minutes=5), payload=100, destination='A', source_grade_fe=60)])
        args[6]['site_context']['direct_tip_movement_rules'] = [dict(grade_block_source='GB', crusher_destination=p) for p in ('A', 'B')]
        planner = MultiManualPlanner(*args); states = planner.build_steady_states()
        with self.assertRaisesRegex(ValueError, 'more than once'):
            planner.build_report(states, {s['state_key']: {'GB': 60} for s in states})

    def test_points_switch_product_brands_together_at_shared_build_completion(self):
        args = list(inputs())
        for source in args[1].values():
            source['grade_streams'] = {'adjusted_product': {'First': {'fe': 60, 'si': 4, 'al': 2, 'p': .04, 'mn': .1},
                                                          'Second': {'fe': 65, 'si': 4, 'al': 2, 'p': .04, 'mn': .1}}}
        args[5] = [dict(build_name=name, brand=name, target_tonnes=100, target_mode='soft', target_fe_target=grade)
                   for name, grade in [('First', 60), ('Second', 65)]]
        planner = MultiManualPlanner(*args); states = planner.build_steady_states(); report = planner.build_report(states)
        self.assertEqual(len(states), 4)
        first = report.loc[pd.to_datetime(report.start_datetime).eq(START)]
        second = report.loc[pd.to_datetime(report.start_datetime).eq(START+timedelta(minutes=30))]
        self.assertEqual(set(first.source_grade_fe), {60})
        self.assertEqual(set(second.source_grade_fe), {65})


class MultiManualWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def host(self):
        args = inputs(); host = QWidget(); self.addCleanup(host.deleteLater)
        host.active_manual_plan_id = 'New plan'; host.manual_point_drafts = args[0]
        host.updated_stockpile_data = args[1]; host.multi_feed_configuration = args[6]['site_context']['multi_feed_settings']
        host.start_time_choice = START; host.calendar_inputs = args[6]
        host.planning_period_count = lambda: 3
        host.included_stockpile_data = lambda: host.updated_stockpile_data
        return host

    def test_recipe_weights_are_editable_without_saved_allocations(self):
        widget = MultiManualAuthoring(self.host()); widget.refresh()
        self.assertEqual(widget.points.count(), 2)
        self.assertEqual(widget.recipes.currentText(), 'Recipe 1')
        widget.sources.item(0, 1).setText('3'); widget.sources.item(2, 1).setText('1')
        self.assertEqual(widget.sources.item(0, 2).text(), '75.000')
        self.assertEqual(widget.sources.item(2, 2).text(), '25.000')
        self.assertEqual(definitions(widget.draft())[0]['Source Ratios'], [.75, .25])
        widget.points.setCurrentIndex(1); widget.change_point(); widget.points.setCurrentIndex(0); widget.change_point()
        self.assertEqual(widget.sources.item(0, 1).text(), '3')
        widget.refresh(); self.assertEqual(widget.sources.item(2, 1).text(), '1')

    def test_sequence_add_remove_reorder_and_time_edits_persist(self):
        widget = MultiManualAuthoring(self.host(), sequence=True); widget.refresh()
        widget.add_row(); self.assertEqual(widget.rows.rowCount(), 2)
        widget.rows.cellWidget(1, 3).setValue(2)
        self.assertEqual(widget.draft()['sequence'][1]['Duration (hrs)'], 2)
        widget.rows.selectRow(1); widget.move_row(-1)
        self.assertEqual(widget.draft()['sequence'][0]['Duration (hrs)'], 2)
        widget.join_times()
        self.assertEqual(widget.draft()['sequence'][1]['Start Datetime'], widget.draft()['sequence'][0]['End Datetime'])
        widget.rows.selectRow(1); widget.remove_rows(); self.assertEqual(widget.rows.rowCount(), 1)

    def test_imported_subsecond_timing_is_unchanged_until_a_control_is_edited(self):
        host = self.host(); planner = MultiManualPlanner(*inputs()); report = planner.build_report(planner.build_steady_states())
        report['start_datetime'] = pd.to_datetime(report.start_datetime)+pd.Timedelta(microseconds=123456)
        report['end_datetime'] = pd.to_datetime(report.end_datetime)+pd.Timedelta(microseconds=234567)
        report['steady_state_duration'] = (report.end_datetime-report.start_datetime).dt.total_seconds()/3600
        host.manual_point_drafts = from_report(report)
        widget = MultiManualAuthoring(host, sequence=True); widget.refresh()
        original = deepcopy(widget.draft()['sequence'])
        self.assertEqual(widget.sequence_values(), original)
        widget.rows.cellWidget(0, 3).setValue(2)
        self.assertNotIn('_fixed_steady_state', widget.draft()['sequence'][0])
        self.assertEqual(widget.draft()['sequence'][0]['Duration (hrs)'], 2)

    def test_hidden_legacy_recipe_controls_do_not_invalidate_multi_point_plans(self):
        from GUI.WorkflowDependencies import manual_revision
        host = self.host(); revision = manual_revision(host)
        host.blend_config_table = QWidget(); self.addCleanup(host.blend_config_table.deleteLater)
        host.blend_data_from_config_table_inputs = {'old hidden recipe': 'unchanged inputs'}
        self.assertEqual(revision, manual_revision(host))

    def test_named_recipe_summary_keeps_its_grades(self):
        from classes.OperationalBlendPlans import split_blend_plans
        planner = MultiManualPlanner(*inputs()); report = planner.build_report(planner.build_steady_states())
        plan = split_blend_plans(report)['A']
        self.assertEqual(plan['summary'].iloc[0]['Blend ID'], 'Recipe 1')
        self.assertEqual(plan['summary'].iloc[0]['Grade Fe'], 60)

    def test_old_saved_allocations_are_migrated_to_editable_recipes_once(self):
        from GUI.WorkflowDependencies import manual_revision
        host = self.host(); host.manual_point_drafts = {}
        host.manual_input_revision = manual_revision(host)
        planner = MultiManualPlanner(*inputs()); report = planner.build_report(planner.build_steady_states())
        widget = MultiManualAuthoring(host); widget.refresh(); widget.hydrate_report(report)
        self.assertEqual(widget.recipes.count(), 1)
        self.assertEqual(host.manual_input_revision, manual_revision(host))
        with patch('GUI.MultiManualAuthoring.QMessageBox.question', return_value=QMessageBox.Yes):
            widget.clear_point()
        widget.hydrate_report(report)
        self.assertEqual(widget.recipes.count(), 0)

    def test_submission_runs_the_recipe_worker_and_publishes_a_fresh_manual_plan(self):
        from GUI.InitialiseGUI import UserInputs
        from database.DatabaseContext import get_database_path, set_database_path
        from classes.SavedPlanStore import write_manual_snapshot
        from classes.SavedResultViews import read_report
        from GUI.WorkflowDependencies import manual_revision
        class Host(QWidget):
            manual_expit_payload_transactions = UserInputs.manual_expit_payload_transactions
        host = Host(); self.addCleanup(host.deleteLater)
        original = get_database_path(); self.addCleanup(set_database_path, original)
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        set_database_path(os.path.join(folder.name, 'test.db'))
        args = inputs(); host.active_manual_plan_id = 'Authored plan'; host.manual_point_drafts = args[0]
        for draft in host.manual_point_drafts.values(): draft['rates'].update(Period_1=100, Period_2=100)
        host.updated_stockpile_data = args[1]; host.hex_sequence_table = []; host.product_targets = []
        host.calendar_inputs = args[6]; host.multi_feed_configuration = args[6]['site_context']['multi_feed_settings']
        host.start_time_choice = START; host.planning_period_count = lambda: 3
        host.included_stockpile_data = lambda: host.updated_stockpile_data
        host.current_opf_profiles = lambda: {}
        host.active_site_context = lambda: deepcopy(host.calendar_inputs['site_context'])
        host.selected_optimisation_plan_id = lambda: 'Primary'
        host.capture_active_manual_plan_state = lambda: None
        host.save_active_scenario_state = lambda: None
        host.set_page_enabled = lambda *args: None
        host.write_active_manual_plan_reports = lambda report: write_manual_snapshot(get_database_path(), host.active_manual_plan_id, report)
        def run(title, work, done, failed):
            try: done(work())
            except Exception as exc: failed({'message': str(exc)})
        host.run_background_task = run
        widget = MultiManualAuthoring(host, sequence=True); widget.refresh()
        with patch('GUI.WorkflowSubmissions.actuals_required', return_value=False), patch('classes.ApprovedReconciliation.missing_sources', return_value=[]), \
             patch('GUI.OPFProfileLoading.ensure', return_value=False), patch('GUI.MaterialFlowIntegration.topology_for_gui', return_value={}):
            widget.calculate()
        report = read_report(get_database_path(), 'manual', host.active_manual_plan_id)
        self.assertFalse(report.empty, widget.status.text())
        self.assertAlmostEqual(report.source_actual_tonnes.sum(), 200)
        self.assertEqual(host.manual_input_revision, manual_revision(host))
        self.assertIn('calculated', widget.status.text())


if __name__ == '__main__': unittest.main()
