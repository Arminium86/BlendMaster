"""Prepare every enabled site, then publish one atomic shared project."""
from datetime import datetime, timezone
from pathlib import Path
import time
import uuid

from PyQt5.QtCore import QObject, QTimer
from classes.SiteWorkflow import validate_contract
from classes.SharedProjects import settings, filename
from classes.WorkflowCheckpoint import write_checkpoint


def enabled_contracts(scenarios):
    result = {}
    for site, state in scenarios.items():
        raw = state.get('site_workflow_contract') or {}
        if raw.get('enabled'):
            value = validate_contract(raw)
            if value['site_id'] != site:
                raise ValueError('A scheduled site contract belongs to another site model.')
            result[site] = value
    return result


class ScheduledSiteBatch(QObject):
    def __init__(self, controller, contracts):
        super().__init__(controller)
        self.controller, self.host, self.contracts = controller, controller.host, contracts
        self.original = self.host.active_scenario_id
        self.queue, self.completed = list(contracts), {}
        self.retries = {}
        self.stage, self.cancelled, self.started = 'next', False, time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.lock()

    def lock(self):
        h = self.host
        h.tabs.setEnabled(False)
        h.scenario_selector.setEnabled(False)
        h.prepare_inputs_button.setEnabled(False)
        h.cancel_preparation_button.setVisible(True)

    def poll(self):
        h, c = self.host, self.controller
        if h.background_tasks or c.active or vars(h).get('project_load_restore_in_progress'):
            return
        try:
            if self.stage == 'next':
                if self.cancelled or not self.queue:
                    self.finish_sites()
                    return
                self.site = self.queue.pop(0)
                if h.active_scenario_id != self.site:
                    h.active_scenario_id = self.site
                    h.restore_site_scenario(h.site_scenarios[self.site])
                    h.refresh_scenario_selector()
                self.stage = 'restored'
                self.lock()
            elif self.stage == 'restored':
                if self.cancelled:
                    self.stage = 'next'
                    return
                self.stage = 'running'
                if not c.start(self.contracts[self.site]['handoff'], scheduled=True):
                    self.completed[self.site] = 'failed'
                    c.last_attempt[self.site] = time.monotonic()
                    self.stage = 'next'
            elif self.stage == 'return':
                self.stage = 'closed'
                self.timer.stop()
                c.batch = None
                c.unlock()
                self.deleteLater()
        except Exception as exc:
            c.status('Scheduled update failed: ' + str(exc))
            self.cancelled = True
            self.finish_sites()

    def site_finished(self):
        if self.stage == 'running':
            self.completed[self.site] = self.controller.run.status
            self.cancelled = self.cancelled or self.controller.run.status == 'cancelled'
            retries = self.retries.get(self.site, 0)
            if self.controller.run.status == 'failed' and not self.cancelled and retries < self.contracts[self.site]['retries']:
                self.retries[self.site] = retries + 1
                self.queue.insert(0, self.site)
            self.stage = 'next'
        self.lock()

    def finish_sites(self):
        h, c = self.host, self.controller
        h.save_active_scenario_state()
        if self.stage == 'publishing':
            return
        if self.cancelled or set(self.completed) != set(self.contracts) or any(
                value not in {'inputs_ready', 'plan_prepared'} for value in self.completed.values()):
            c.status('Shared project retained. The site update batch did not complete successfully.')
            self.restore_original()
            return
        shared = settings(vars(h).get('shared_project_settings'))
        name = shared['model_name'] or h.scenario_display_name(h.site_scenarios[self.original], fallback='BlendMaster')
        if not shared['model_name']:
            shared['model_name'] = name
            h.shared_project_settings = shared
        destination = Path(shared['folder']) / filename(name)
        scenarios = dict(h.site_scenarios)
        metadata = dict(shared_project_settings=shared, shared_project_publication={
            'id': str(uuid.uuid4()), 'published_at': datetime.now(timezone.utc).isoformat(),
            'site_outcomes': dict(self.completed), 'role': h.access_role})
        self.stage = 'publishing'
        def done(path):
            h.last_shared_project_save_path = path
            c.status(f'{len(self.completed)} sites updated. Shared project saved: {path}')
            self.restore_original()
        def failed(error):
            c.status('Shared project was not replaced: ' + str(error.get('message', error)))
            self.restore_original()
        h.run_background_task('Publishing updated shared project…',
            lambda: write_checkpoint(scenarios, self.original, destination, h.snapshot_database, metadata=metadata), done, failed)

    def restore_original(self):
        self.stage = 'return'
        h = self.host
        if h.active_scenario_id != self.original:
            h.active_scenario_id = self.original
            h.restore_site_scenario(h.site_scenarios[self.original])
            h.refresh_scenario_selector()
        self.lock()


def start_due_batch(controller):
    h = controller.host
    # Timer checks only need the small scheduling contract, not a full model
    # capture (which prepares Calendar context and scans source evidence).
    scenarios = dict(h.site_scenarios)
    current = vars(h)
    site = current.get('active_scenario_id')
    if site and 'site_workflow_contract' in current:
        scenarios[site] = {**scenarios.get(site, {}),
                           'site_workflow_contract': current['site_workflow_contract']}
    contracts = enabled_contracts(scenarios)
    now = time.monotonic()
    if contracts and any(now - controller.last_attempt.get(site, 0) >= value['refresh_minutes'] * 60
                         for site, value in contracts.items()):
        h.save_active_scenario_state()
        controller.batch = ScheduledSiteBatch(controller, contracts)
        controller.status(f'Updating {len(contracts)} enabled site models…')
