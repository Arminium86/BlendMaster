import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
from PyQt5.QtWidgets import QApplication, QMainWindow, QTabWidget, QWidget
from GUI.InitialiseGUI import UserInputs
from GUI.ManualBlendDash import DrawOptimisedGradeProfiles
from GUI.WorkflowNavigation import WORKSPACE, VIEWS, SUPPORT
from GUI.WorkflowViews import WorkflowViews, input_readiness, result_presence


class WorkflowViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def host(self):
        h = UserInputs.__new__(UserInputs)
        QMainWindow.__init__(h)
        h.access_role = 'support'
        h.active_scenario_id = 'first'
        h.tabs = QTabWidget(h)
        h.handle_main_tab_changed = Mock()
        h.setup_navigation()
        for key in (*WORKSPACE, *VIEWS, *SUPPORT, 'optimised_grade_profiles', 'manual_grade_profiles'):
            if key not in h.page_widgets:
                tabs = h.grade_profiles_tabs if key.endswith('_grade_profiles') else h.workspace_tabs
                h.register_page(key, tabs, QWidget(), key)
        h.selected_optimisation_plan_id = lambda: 'Primary'
        for name in ('load_gantt_chart', 'load_profiles', 'load_optimised_grade_profiles', 'load_grade_profiles'):
            setattr(h, name, Mock())
        pending = []
        h.run_background_task = lambda message, work, done, *args, **kw: pending.append((work, done))
        h._workflow_views = WorkflowViews(h)
        self.addCleanup(h.deleteLater)
        return h, h._workflow_views, pending

    def test_expit_belongs_to_views(self):
        h, _, _ = self.host()
        self.assertNotIn('expit_sequence', WORKSPACE)
        self.assertIs(h.page_locations['expit_sequence'][0], h.results_tabs)

    def test_input_tabs_require_available_files_and_usable_configuration(self):
        h = SimpleNamespace()
        self.assertFalse(any(ready for ready, _ in input_readiness(h).values()))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'Mining.csv'
            source.write_text('schedule')
            h.mine_input_choice, h.start_time_choice = 'CC', datetime(2026, 9, 13)
            h.file_path_choice = h.file_path_24hr_choice = str(source)
            h.stockpile_data = {'SP': {'nearest_crusher': ''}}
            h.expit_mode_choice = 2
            h.selected_24hr_expit_agents = ['EX1']
            ready = input_readiness(h)
            self.assertTrue(ready['expit_sequence'][0])
            self.assertFalse(ready['destination_progress'][0])
            h.stockpile_data['SP']['nearest_crusher'] = 'OPF02_PC'
            self.assertTrue(input_readiness(h)['destination_progress'][0])
            h.selected_24hr_expit_agents = []
            self.assertFalse(input_readiness(h)['expit_sequence'][0])
            h.selected_24hr_expit_agents = ['EX1']
            h.expit_mode_choice = 1
            self.assertFalse(input_readiness(h)['expit_sequence'][0])
            source.unlink()
            self.assertFalse(any(ready for ready, _ in input_readiness(h).values()))

    def test_only_actual_report_rows_enable_grade_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reports.db'
            self.assertEqual(result_presence(str(path)), {'optimised': False, 'manual': False})
            self.assertFalse(path.exists())
            connection = sqlite3.connect(path)
            self.addCleanup(connection.close)
            connection.executescript('CREATE TABLE optimised_blend_report (grade REAL);'
                                     'CREATE TABLE manual_blend_report (grade REAL);'
                                     'CREATE TABLE optimisation_plan_status (status TEXT);'
                                     "INSERT INTO optimisation_plan_status VALUES ('complete');")
            connection.commit()
            self.assertFalse(any(result_presence(str(path)).values()))
            connection.execute('INSERT INTO manual_blend_report VALUES (58)')
            connection.commit()
            self.assertEqual(result_presence(str(path)), {'optimised': False, 'manual': True})
            connection.execute('INSERT INTO optimised_blend_report VALUES (59)')
            connection.commit()
            self.assertTrue(all(result_presence(str(path)).values()))
            connection.close()

    def test_parent_and_children_follow_current_results_not_saved_flags(self):
        h, views, pending = self.host()
        h.restore_page_states({'grade_profiles': True, 'optimised_grade_profiles': True, 'manual_grade_profiles': True})
        views.timer.stop()
        views.refresh()
        self.assertFalse(h.is_page_enabled('grade_profiles'))
        pending.pop()[1]({'optimised': False, 'manual': True})
        self.assertTrue(h.is_page_enabled('grade_profiles'))
        self.assertTrue(h.is_page_enabled('manual_grade_profiles'))
        self.assertFalse(h.is_page_enabled('optimised_grade_profiles'))
        views.results = {'optimised': False, 'manual': False}
        views.apply_results()
        self.assertFalse(h.is_page_enabled('grade_profiles'))

    def test_disabled_views_cannot_be_opened_even_with_force(self):
        h, views, _ = self.host()
        views.results_dirty = False
        views.refresh()
        h.show_page('calendar')
        for page in ('expit_sequence', 'destination_progress', 'grade_profiles', 'manual_grade_profiles'):
            h.show_page(page, force=True)
            self.assertIs(h.workspace_tabs.currentWidget(), h.page_widgets['calendar'])
            self.assertIs(h.tabs.currentWidget(), h.workspace_navigation_page)

    def test_old_site_probe_does_not_enable_new_site_views(self):
        h, views, pending = self.host()
        views.refresh()
        old_done = pending.pop()[1]
        h.active_scenario_id = 'second'
        views.refresh()
        old_done({'optimised': True, 'manual': True})
        self.assertFalse(h.is_page_enabled('grade_profiles'))
        pending.pop()[1]({'optimised': True, 'manual': False})
        self.assertTrue(h.is_page_enabled('optimised_grade_profiles'))
        self.assertFalse(h.is_page_enabled('manual_grade_profiles'))

    def test_expit_evidence_is_reused_until_its_inputs_change(self):
        h, views, _ = self.host()
        h.start_time_choice = datetime(2026, 9, 13)
        views.refresh()
        h.expit_sequence_snapshot = {'summary': {'agent': 'EX1'}}
        views.refresh()
        self.assertTrue(h.expit_sequence_snapshot)
        h.start_time_choice = datetime(2026, 9, 14)
        views.refresh()
        self.assertFalse(h.expit_sequence_snapshot)

    def test_charts_load_on_entry_and_after_results_change_without_refresh_loop(self):
        h, views, pending = self.host()
        views.refresh()
        pending.pop()[1]({'optimised': True, 'manual': False})
        h.show_page('optimised_grade_profiles')
        views.ensure_current_chart()
        h.load_optimised_grade_profiles.assert_called_once()
        views.ensure_current_chart()
        h.load_optimised_grade_profiles.assert_called_once()
        views.schedule(charts=True)
        views.timer.stop()
        views.refresh()
        self.assertEqual(h.load_optimised_grade_profiles.call_count, 2)
        h.show_page('calendar')
        views.schedule(charts=True)
        views.timer.stop()
        views.refresh()
        self.assertEqual(h.load_optimised_grade_profiles.call_count, 2)
        h.show_page('optimised_grade_profiles')
        views.ensure_current_chart()
        self.assertEqual(h.load_optimised_grade_profiles.call_count, 3)

    def test_optimised_dash_layout_reads_new_results_and_site_without_post(self):
        chart = DrawOptimisedGradeProfiles.__new__(DrawOptimisedGradeProfiles)
        chart.fetch_data = Mock(side_effect=[pd.DataFrame({'grade': [58]}), pd.DataFrame({'grade': [59]})])
        self.assertEqual(chart.create_layout().children[0].data, [{'grade': 58}])
        self.assertEqual(chart.create_layout().children[0].data, [{'grade': 59}])

    def test_dash_http_load_and_callbacks_see_current_committed_results(self):
        from contextlib import closing
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'first.db'
            def write(database, grade):
                row = dict(steady_state_number=1, start_datetime='2026-09-13 00:00:00',
                           end_datetime='2026-09-13 01:00:00', crusher_actual_tonnes=100,
                           **{f'crusher_actual_grade_{key}': grade if key == 'fe' else 1
                              for key in ('fe', 'si', 'al', 'p', 'mn')})
                with closing(sqlite3.connect(database)) as connection:
                    pd.DataFrame([row]).to_sql('optimised_blend_report', connection, index=False, if_exists='replace')
                    pd.DataFrame({'build': []}).to_sql('product_build_report', connection, index=False, if_exists='replace')
            write(path, 58)
            chart = DrawOptimisedGradeProfiles(str(path), 0)
            client = chart.app.server.test_client()
            def check(expected):
                response = client.get('/_dash-layout')
                self.assertEqual(response.status_code, 200)
                rows = response.json['props']['children'][0]['props']['data']
                self.assertEqual(rows[0]['crusher_actual_grade_fe'], expected)
                rendered = client.post('/_dash-update-component', json={
                    'output': 'charts-container.children',
                    'outputs': {'id': 'charts-container', 'property': 'children'},
                    'changedPropIds': ['df-store.data'],
                    'inputs': [{'id': 'df-store', 'property': 'data', 'value': rows}], 'state': []})
                self.assertEqual(rendered.status_code, 200)
                charts = rendered.json['response']['charts-container']['children']
                figure = charts[0]['props']['children']['props']['figure']
                self.assertEqual(figure['data'][0]['y'], [expected, expected])
            check(58)
            write(path, 59)
            check(59)
            other = Path(directory) / 'second.db'
            write(other, 61)
            chart.db_path = str(other)
            check(61)

    def test_connection_timeout_does_not_load_a_dead_service_and_can_retry(self):
        from GUI.ChartReadiness import connect_view
        host = SimpleNamespace(active_scenario_id='first')
        pending = []
        host.run_background_task = lambda message, work, done, **kw: pending.append(done)
        view = QWidget()
        self.addCleanup(view.deleteLater)
        view.setUrl, view.setHtml = Mock(), Mock()
        connect_view(host, view, 'http://localhost:8050/?plan=Primary')
        pending.pop()(False)
        view.setUrl.assert_not_called()
        view.setHtml.assert_called_once()
        connect_view(host, view, 'http://localhost:8050/?plan=Primary')
        pending.pop()(True)
        loaded = view.setUrl.call_args.args[0].toString()
        self.assertIn('plan=Primary', loaded)
        self.assertIn('refresh=', loaded)
