"""Readiness and event-driven loading for the workspace's read-only views."""
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from PyQt5 import sip
from PyQt5.QtCore import QObject, QTimer
from classes.DestinationBuildOrder import inventory_areas
from database.DatabaseContext import get_database_path


READY_PAGES = {'expit_sequence', 'destination_progress', 'grade_profiles',
               'optimised_grade_profiles', 'manual_grade_profiles'}
CHART_LOADERS = {
    'optimised_blend_sequence': 'load_gantt_chart',
    'build_depletion_profiles': 'load_profiles',
    'optimised_grade_profiles': 'load_optimised_grade_profiles',
    'manual_grade_profiles': 'load_grade_profiles',
    'blend_sequence': 'load_manual_gantt_chart',
    'amt_stockpiles': 'load_AMT_map',
}


def result_presence(database):
    """Read a single row per report, without creating or loading a database."""
    result = {'optimised': False, 'manual': False}
    if not os.path.isfile(database):
        return result
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True, timeout=.2)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for key, table in (('optimised', 'optimised_blend_report'), ('manual', 'manual_blend_report')):
            if table in tables:
                result[key] = connection.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() is not None
    return result


def input_readiness(host):
    state = vars(host)
    configured = bool(state.get('mine_input_choice') and state.get('start_time_choice'))
    two_wp = os.path.isfile(str(state.get('file_path_choice') or ''))
    day_plan = os.path.isfile(str(state.get('file_path_24hr_choice') or ''))
    expit = (configured and day_plan and state.get('expit_mode_choice') == 2
             and bool(state.get('selected_24hr_expit_agents')))
    destination = configured and two_wp and any(inventory_areas(state.get('stockpile_data') or {}).values())
    return {
        'expit_sequence': (expit, 'Submit the site, import a 24HR schedule, select its dig circuits and enable transaction reconciliation in Guidance Settings.'),
        'destination_progress': (destination, 'Submit the site, import a 2WP schedule and load Stockpile Inventories with Nearest Crusher assignments.'),
    }


def expit_context(host):
    """Identify prepared map inputs without fetching warehouse evidence."""
    state = vars(host)
    files = []
    for key in ('file_path_choice', 'file_path_24hr_choice'):
        path = str(state.get(key) or '')
        try:
            stat = os.stat(path)
            files.append((path, stat.st_size, stat.st_mtime_ns))
        except OSError:
            files.append((path, None, None))
    return (tuple(files), *(str(state.get(key)) for key in (
        'active_scenario_id', 'start_time_choice', 'mine_input_choice', 'opf_input_choice',
        'crusher_input_choice', 'expit_mode_choice', 'reevaluate_aps_direct_tip_choice',
        'expit_completion_tolerance_pct', 'expit_refresh_tolerance_minutes')),
        tuple(state.get('selected_24hr_expit_agents') or []),
        tuple(state.get('aps_direct_tip_crusher_choice') or []))


def schedule(host, *, results=False, charts=False):
    controller = vars(host).get('_workflow_views')
    if controller is not None:
        controller.schedule(results=results, charts=charts)


class WorkflowViews(QObject):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.context = None
        self.results = {'optimised': False, 'manual': False}
        self.results_dirty = True
        self.generation = 0
        self.revision = 0
        self.loaded = {}
        self.expit_inputs = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.refresh)

    def schedule(self, *, results=False, charts=False):
        self.results_dirty |= results
        if charts:
            self.revision += 1
        self.timer.start(0)

    def available(self, page, enabled, reason=''):
        location = self.host.page_locations.get(page)
        if location is None:
            return
        tabs, index = location
        tabs.setTabToolTip(index, '' if enabled else reason)
        # These states are derived from this site's inputs/results, including
        # after restoring legacy projects with stale saved tab flags.
        self.host._page_enabled_state[page] = bool(enabled)
        if tabs.isTabEnabled(index) != bool(enabled):
            tabs.setTabEnabled(index, bool(enabled))

    def refresh(self):
        host = self.host
        if sip.isdeleted(host) or vars(host).get('scenario_switch_in_progress') or vars(host).get('project_load_restore_in_progress'):
            return
        context = (get_database_path(), vars(host).get('active_scenario_id'))
        if context != self.context:
            self.context = context
            self.results = {'optimised': False, 'manual': False}
            self.results_dirty = True
            self.loaded.clear()
        for page, (enabled, reason) in input_readiness(host).items():
            self.available(page, enabled, reason)
        self.sync_expit_inputs()
        self.apply_results()
        if self.results_dirty:
            self.results_dirty = False
            self.generation += 1
            generation = self.generation

            def done(results):
                if generation != self.generation or context != (get_database_path(), vars(host).get('active_scenario_id')):
                    return
                self.results = results
                self.apply_results()
                self.ensure_current_chart()

            host.run_background_task('Checking available views…', lambda: result_presence(context[0]), done,
                                     lambda error: done({'optimised': False, 'manual': False}), show_progress=False)
        self.ensure_current_chart()

    def sync_expit_inputs(self):
        inputs = expit_context(self.host)
        if self.expit_inputs is not None and inputs != self.expit_inputs:
            self.host.expit_sequence_snapshot = {}
        self.expit_inputs = inputs

    def apply_results(self):
        self.available('optimised_grade_profiles', self.results['optimised'], 'Run optimisation to generate grade results for this site.')
        self.available('manual_grade_profiles', self.results['manual'], 'Submit Manual Blend Sequence to generate grade results for this site.')
        self.available('grade_profiles', any(self.results.values()), 'Generate an optimised or manual plan to view its grade profiles.')

    def current_page(self):
        tabs = self.host.tabs
        while True:
            page = tabs.currentWidget()
            child = next((child for child, (parent, container) in self.host.navigation_parents.items()
                          if parent is tabs and container is page), None)
            if child is None:
                return next((key for key, widget in self.host.page_widgets.items() if widget is page), None)
            tabs = child

    def ensure_current_chart(self):
        host = self.host
        workflow = vars(host).get('site_workflow_controller')
        if (vars(host).get('project_load_restore_in_progress') or vars(host).get('scenario_switch_in_progress')
                or (workflow and workflow.active)):
            return
        page = self.current_page()
        if page not in CHART_LOADERS or not host.is_page_enabled(page):
            return
        if page in ('optimised_blend_sequence', 'build_depletion_profiles', 'optimised_grade_profiles') and not self.results['optimised']:
            return
        if page == 'amt_stockpiles' and (vars(host).get('_amt_map_pending') or not vars(host).get('draw_AMT_map')):
            return
        token = (get_database_path(), vars(host).get('active_scenario_id'), self.revision,
                 host.selected_optimisation_plan_id(), vars(host).get('active_manual_plan_id'))
        if self.loaded.get(page) == token:
            return
        self.loaded[page] = token
        getattr(host, CHART_LOADERS[page])()

    def enter(self):
        # Re-entering a page also allows a failed local connection to retry.
        self.loaded.pop(self.current_page(), None)
        self.schedule()
