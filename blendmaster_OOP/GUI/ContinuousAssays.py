"""Support-configured automatic updates; no modal prompts during polling."""
from copy import deepcopy
from datetime import datetime
import json
import time
from zoneinfo import ZoneInfo
from PyQt5.QtCore import QObject, QTimer
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QPlainTextEdit, QPushButton, QHBoxLayout
from classes.ContinuousAssays import settings, write_audit, live_enabled, request_basis
from classes.ContinuousAssayScope import current_sources
from classes.SiteWorkflow import require_action
from setup.ContinuousAssayHistory import ContinuousAssayHistory, request_snapshot


class ContinuousAssayController(QObject):
    def __init__(self, host):
        super().__init__(host)
        self.host, self.running, self.last = host, False, {}
        self.service = ContinuousAssayHistory()
        self.timer = QTimer(self)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(self.refresh_due)
        self.timer.start()

    def prepare_current(self):
        """Include current assay evidence before a prepared site is published."""
        h = self.host
        if not live_enabled(vars(h)):
            h.continuous_assay_status = 'Continuous assays are inactive for Set Time or a disabled policy.'
            h.continuous_assay_panel.show_status()
            return
        database = h.scenario_database_path(h.active_scenario_id)
        site = h.active_scenario_id
        scope = current_sources(vars(h), database, datetime.now(ZoneInfo('Australia/Perth')))
        if scope:
            from GUI.OPFProfileLoading import ensure
            if ensure(h, self.prepare_current):
                return
        state = request_snapshot(vars(h), h.current_opf_profiles() if scope else {})
        basis = request_basis(state)
        self.last[h.active_scenario_id] = time.monotonic()
        def work():
            try:
                return self.service.refresh(state,datetime.now(ZoneInfo('Australia/Perth')),database), None
            except Exception as exc:
                return None, str(exc)
        def done(value):
            bundle, error = value
            if h.active_scenario_id != site or basis != request_basis(vars(h)) or scope != current_sources(vars(h), database, datetime.now(ZoneInfo('Australia/Perth'))):
                h.continuous_assay_status = 'Inputs or active blend changed while checking assays; results discarded.'
                h.continuous_assay_panel.show_status()
                return
            if error:
                h.continuous_assay_status = error
            else:
                write_audit(database,bundle)
                h.continuous_assay_state = bundle
                h.continuous_assay_status = f"Assays checked {bundle.get('checked_at','')}. {bundle.get('status', '')}"
            h.continuous_assay_panel.show_status()
        h.run_background_task('Checking current product assays…',work,done)

    def refresh_due(self, force=False):
        h = self.host
        if self.running or h.background_tasks or vars(h).get('project_load_restore_in_progress') or h.site_workflow_controller.active or h.site_workflow_controller.batch:
            return
        if not vars(h).get('mine_input_choice'):
            return
        states = {**h.site_scenarios, h.active_scenario_id: vars(h)}
        due = {site for site, state in states.items() if live_enabled(state) and
               (force or site not in self.last or time.monotonic()-self.last[site] >= settings(state.get('continuous_assay_settings'))['interval_minutes']*60)}
        if not due:
            return
        # Read the active site's authoritative attributes directly. Capturing
        # the whole model here refreshes Calendar context and can synchronously
        # rebuild historical profiles before the background preparation gate.
        scopes = {site: current_sources(states[site], h.scenario_database_path(site), datetime.now(ZoneInfo('Australia/Perth')))
                  for site in due}
        active_profiles = {}
        if h.active_scenario_id in due and scopes[h.active_scenario_id]:
            from GUI.OPFProfileLoading import ensure
            if ensure(h, lambda: self.refresh_due(force)):
                return
            active_profiles = h.current_opf_profiles()
        requests = []
        for site, state in states.items():
            if site in due and live_enabled(state):
                self.last[site] = time.monotonic()
                profiles = active_profiles if site == h.active_scenario_id else (state.get('calendar_inputs') or {}).get('site_context', {}).get('opf_profiles', {})
                requests.append((site, request_snapshot(state, profiles), h.scenario_database_path(site)))
        if not requests:
            return
        self.running = True
        def work():
            results = []
            for site, state, database in requests:
                try:
                    result = self.service.refresh(state, datetime.now(ZoneInfo('Australia/Perth')), database)
                    results.append((site, request_basis(state), result, None))
                except Exception as exc:
                    results.append((site, '', None, str(exc)))
            return results
        def done(results):
            self.running = False
            for site, basis, result, error in results:
                state = h.site_scenarios.get(site)
                if state is None:
                    continue
                current = vars(h) if site == h.active_scenario_id else state
                if error:
                    state['continuous_assay_status'] = error
                elif (request_basis(current) == basis and live_enabled(current) and
                      scopes[site] == current_sources(current, h.scenario_database_path(site), datetime.now(ZoneInfo('Australia/Perth')))):
                    write_audit(h.scenario_database_path(site), result)
                    state['continuous_assay_state'] = result
                    applied = sum(a.get('status') == 'applied' for a in result.get('audit', []))
                    state['continuous_assay_status'] = f'{applied} validated analyte updates; {len(result.get("audit", []))-applied} withheld. Checked {result.get("checked_at", "")}. {result.get("status", "")}'
                else:
                    continue
                if site == h.active_scenario_id:
                    h.continuous_assay_state = deepcopy(state.get('continuous_assay_state') or {})
                    h.continuous_assay_status = state['continuous_assay_status']
            h.continuous_assay_panel.show_status()
            from GUI.WorkflowWorkspace import refresh_context
            refresh_context(h)
        def failed(message):
            self.running = False
            h.continuous_assay_status = str(message)
            h.continuous_assay_panel.show_status()
        h.run_background_task('Checking current product assays…', work, done, failed, show_progress=bool(force))


class ContinuousAssayPanel(QWidget):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        layout = QVBoxLayout(self)
        note = QLabel('In Now mode, validated assays reconcile adjusted product grades for inventory stockpiles and AMT chunks '
            'feeding the active blend in the selected saved plan. Set Time uses historical reconciliation only. '
            'Bounds and standard deviations are grade percentage points. Actual feed must identify the exact stockpile build; '
            'AMT also requires a single active saved-plan chunk. Configure a measured feed-to-assay lag per OPF when transport is enabled. '
            'Unattributable observations are retained in the audit without changing estimates.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.editor = QPlainTextEdit()
        layout.addWidget(self.editor)
        buttons = QHBoxLayout()
        save = QPushButton('Save assay policy')
        save.clicked.connect(self.save)
        refresh = QPushButton('Check assays now')
        refresh.clicked.connect(lambda: host.continuous_assay_controller.refresh_due(True))
        buttons.addWidget(save)
        buttons.addWidget(refresh)
        layout.addLayout(buttons)
        self.status = QPlainTextEdit()
        self.status.setReadOnly(True)
        layout.addWidget(self.status)
        self.refresh()

    def refresh(self):
        self.editor.setPlainText(json.dumps(settings(vars(self.host).get('continuous_assay_settings')), indent=2))
        self.show_status()

    def show_status(self):
        h = self.host
        summary = vars(h).get('continuous_assay_summary')
        if summary is not None:
            bundle = vars(h).get('continuous_assay_state') or {}
            count = sum(a.get('status') == 'applied' for a in bundle.get('audit', []))
            summary.setText(f'Continuous assays: {count} validated analyte updates recorded.' if live_enabled(vars(h))
                            else 'Continuous assays inactive: requires Now mode and an enabled policy.')
        self.status.setPlainText(str(vars(h).get('continuous_assay_status') or 'Awaiting current source and assay evidence.') + '\n' +
            json.dumps((vars(h).get('continuous_assay_state') or {}).get('audit', [])[-100:], indent=2))

    def save(self):
        require_action(self.host.access_role, 'solver_configuration')
        try:
            self.host.continuous_assay_settings = settings(json.loads(self.editor.toPlainText()))
            self.host.save_active_scenario_state()
            self.status.setPlainText('Policy saved. Accepted updates apply automatically within these bounds.')
        except (ValueError, KeyError, TypeError) as exc:
            self.status.setPlainText(str(exc))


def install(host):
    host.continuous_assay_controller = ContinuousAssayController(host)
    host.continuous_assay_panel = ContinuousAssayPanel(host)
    host.register_page('continuous_assays', host.setup_tabs, host.continuous_assay_panel, 'Continuous Assays')
    host.continuous_assay_summary = QLabel('Assay corrections apply when current feed and laboratory evidence are available.')
    host.continuous_assay_summary.setWordWrap(True)
    host.page_widgets['grade_reconciliation'].widget().layout().insertWidget(0,host.continuous_assay_summary)
