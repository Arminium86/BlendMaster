"""Configurable site preparation using the same operations as the planner UI."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import json
import time
from zoneinfo import ZoneInfo
from PyQt5.QtCore import QObject, QTimer, QDateTime
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                             QPlainTextEdit, QFileDialog, QMessageBox)
from classes.SiteWorkflow import (default_contract, validate_contract, require_action,
                                  WorkflowRun, fingerprint, source_arrival_status)
from classes.GuidanceImport import file_revision, current_import_revisions
from GUI.GuidanceImports import GuidanceImports
from database.DatabaseContext import get_database_path

SOURCE_WIDGETS = dict(two_wp='file_path', day_plan='file_path_24hr',
                      haul_cycles='haul_cycle_file_path', closing_balance='two_wp_closing_stocks_path')


class SiteAutomationPanel(QWidget):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        layout = QVBoxLayout(self)
        note = QLabel('Site contract: configure input locations, timezone, arrival windows and refresh interval. '
            'The desktop scheduler runs while a Support, Owner or Agent session is open. '
            'Prepare hands off validated inputs; Plan also runs optimisation. '
            'Previous accepted files remain usable while replacements are prepared.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.editor = QPlainTextEdit()
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.editor)
        buttons = QHBoxLayout()
        for caption, fn in [('Save Contract', self.save), ('Import Contract', self.import_contract),
                            ('Export Contract', self.export_contract),
                            ('Prepare Inputs', lambda: self.run('prepare')),
                            ('Prepare Blend Plan', lambda: self.run('plan')),
                            ('Cancel Run', lambda: host.site_workflow_controller.cancel())]:
            button = QPushButton(caption)
            button.clicked.connect(fn)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.status = QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setMaximumHeight(200)
        layout.addWidget(self.status)

    def refresh(self):
        controller = self.host.site_workflow_controller
        self.editor.setPlainText(json.dumps(controller.contract(), indent=2))
        runs = getattr(self.host, 'site_workflow_runs', None) or []
        arrivals = controller.arrival_status()
        self.status.setPlainText('\n'.join(
            [f"{name}: {row['status']}; expected by {row['expected_by']}" for name, row in arrivals.items()] +
            [f"{r['start_time']} · {r['status']} · {r.get('error', '')}" for r in runs[-10:]]))

    def save(self):
        require_action(self.host.access_role, 'configure_automation')
        try:
            value = validate_contract(json.loads(self.editor.toPlainText()))
            if value['site_id'] != self.host.active_scenario_id:
                raise ValueError('The contract site ID must match the selected site model.')
            self.host.site_workflow_contract = value
            self.host.save_active_scenario_state()
            self.status.setPlainText('Contract saved in this project session. Save Project to retain it between sessions.')
            return True
        except (ValueError, TypeError, KeyError) as exc:
            self.status.setPlainText(str(exc))
            return False

    def run(self, endpoint):
        if self.save():
            self.host.site_workflow_controller.start(endpoint)

    def import_contract(self):
        require_action(self.host.access_role, 'configure_automation')
        path, _ = QFileDialog.getOpenFileName(self, 'Import site contract', '', 'JSON (*.json)')
        if path:
            try:
                value = validate_contract(json.loads(Path(path).read_text(encoding='utf-8')))
                if value['site_id'] != self.host.active_scenario_id:
                    raise ValueError('This contract belongs to another site model.')
                self.editor.setPlainText(json.dumps(value, indent=2))
            except (ValueError, OSError) as exc:
                self.status.setPlainText(str(exc))

    def export_contract(self):
        if not self.save():
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Export site contract', 'site_contract.json', 'JSON (*.json)')
        if path:
            Path(path).write_text(json.dumps(self.host.site_workflow_contract, indent=2), encoding='utf-8')


class SiteWorkflowController(QObject):
    """Nonblocking stage coordinator; completion follows actual task outcomes."""
    def __init__(self, host):
        super().__init__(host)
        self.host, self.active, self.run = host, False, None
        self.waiting, self.after_wait, self.stage_started = None, None, None
        self.last_attempt, self.retry_count = {}, {}
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.poll)
        self.scheduler = QTimer(self)
        self.scheduler.setInterval(60_000)
        self.scheduler.timeout.connect(self.tick)
        self.scheduler.start()

    def contract(self):
        h = self.host
        value = getattr(h, 'site_workflow_contract', None)
        if not value or value.get('site_id') != h.active_scenario_id:
            paths = {key: getattr(h, attr).text() for key, attr in SOURCE_WIDGETS.items()}
            for record in getattr(h, 'guidance_import_audit', None) or []:
                paths[record['kind']] = record.get('delivery_path', record['path'])
            value = default_contract(h.active_scenario_id, paths)
        return validate_contract(value)

    def arrival_status(self):
        contract = self.contract()
        now = datetime.now(ZoneInfo(contract['timezone']))
        audits = getattr(self.host, 'guidance_import_audit', None) or []
        result = {}
        for name, source in contract['sources'].items():
            previous = next((row for row in reversed(audits) if row['kind'] == name), None)
            effective = {**source, 'path': source['path'] or getattr(self.host, SOURCE_WIDGETS[name]).text()}
            result[name] = source_arrival_status(effective, now, timezone=contract['timezone'],
                imported_at=(previous.get('source_modified_at') or previous.get('imported_at')) if previous else None)
        return result

    def status(self, text):
        self.host.preparation_status_label.setText(text)
        panel = vars(self.host).get('site_automation_panel')
        if panel:
            panel.status.appendPlainText(text)

    def start(self, endpoint='prepare', *, scheduled=False):
        require_action(self.host.access_role, 'prepare' if endpoint == 'prepare' else 'optimise')
        if endpoint not in ('prepare', 'plan'):
            raise ValueError('Unknown workflow endpoint.')
        h = self.host
        if self.active or h.background_tasks or getattr(h, 'project_load_restore_in_progress', False) or vars(h).get('_closing_requested'):
            self.status('Wait for the current operation before starting preparation.')
            return False
        try:
            contract = self.contract()
            if not getattr(h, 'mine_input_choice', None) or not getattr(h, 'crusher_input_choice', None):
                raise ValueError('Select a configured site model first.')
            if not any((getattr(h, 'stockpile_data_use_column', None) or {}).values()):
                raise ValueError('Select the stockpile inputs for this site model before preparation.')
            if not getattr(h, 'field_mappings', None):
                raise ValueError('Support must submit the site field mappings before preparation.')
            paths = {key: source['path'] or getattr(h, SOURCE_WIDGETS[key]).text()
                     for key, source in contract['sources'].items()}
            for key, source in contract['sources'].items():
                if source['required'] and not paths[key]:
                    raise ValueError(f'Configure the {key} input location first.')
            now = datetime.now(ZoneInfo(contract['timezone']))
            start = now.replace(tzinfo=None) if h.time_mode.currentIndex() == 0 else h.current_site_start_time()
            self.run = WorkflowRun(h.active_scenario_id, start, fingerprint(paths), endpoint)
            self.context = (h.active_scenario_id, get_database_path())
            self.paths, self.active_contract, self.scheduled = paths, contract, scheduled
        except (ValueError, KeyError) as exc:
            self.status(str(exc))
            return False
        self.active, self.run.status = True, 'running'
        self.run.outputs['model'] = dict(mode=(h.multi_feed_configuration or {}).get('mode', 'single'),
            site=h.scenario_display_name(vars(h)), opf=h.opf_input_choice,
            crushers=list(h.selected_site_crushers), product_brands=list(h.product_brand_labels_choice),
            planning_periods=h.planning_period_count())
        self.run.outputs['arrivals'] = self.arrival_status()
        self.cancel_requested, self.stage_index = False, -1
        self.last_attempt[h.active_scenario_id] = time.monotonic()
        h._workflow_run_start = start
        h.start_time_choice = start
        h.start_time.setDateTime(QDateTime(start))
        h.tabs.setEnabled(False)
        h.scenario_selector.setEnabled(False)
        h.prepare_inputs_button.setEnabled(False)
        h.cancel_preparation_button.setVisible(True)
        self.timer.start()
        self.next_stage()
        return True

    def next_stage(self):
        if not self.active:
            return
        if self.cancel_requested:
            self.finish('cancelled')
            return
        if self.stage_index >= 0:
            self.run.record(self.run.steps[self.stage_index], self.stage_started, 'validated')
        self.stage_index += 1
        if self.stage_index == len(self.run.steps):
            self.finish('inputs_ready' if self.run.endpoint == 'prepare' else 'plan_prepared')
            return
        stage = self.run.steps[self.stage_index]
        self.stage_started = datetime.now()
        self.deadline = time.monotonic() + 1800
        self.status('Preparing ' + stage.replace('_', ' ') + '…')
        try:
            getattr(self, 'stage_' + stage)()
        except Exception as exc:
            self.fail(str(exc))

    def await_ready(self, predicate=lambda: True, then=None):
        if self.active:
            self.waiting, self.after_wait = predicate, then or self.next_stage

    def poll(self):
        if not self.active:
            # Keep inputs locked until an in-flight operation has stopped.
            if not self.host.background_tasks:
                self.unlock()
            return
        if self.context != (self.host.active_scenario_id, get_database_path()):
            self.fail('The site changed during preparation; results were discarded.')
        elif time.monotonic() > self.deadline:
            self.fail('The preparation stage did not become ready within 30 minutes.')
        elif self.cancel_requested and not self.host.background_tasks:
            self.finish('cancelled')
        elif not self.host.background_tasks and self.waiting:
            try:
                if self.waiting():
                    after = self.after_wait
                    self.waiting = self.after_wait = None
                    after()
            except Exception as exc:
                self.fail(str(exc))

    def stage_imports(self):
        self.import_queue = list(self.paths)
        self.import_next()

    def import_next(self):
        if not self.active:
            return
        if not self.import_queue:
            self.run.outputs['accepted_inputs'] = current_import_revisions(getattr(self.host, 'guidance_import_audit', None))
            self.run.input_revision = fingerprint(self.run.outputs['accepted_inputs'])
            self.await_ready()
            return
        kind = self.import_queue.pop(0)
        path = self.paths[kind]
        if not path:
            self.import_next()
            return
        previous = next((r for r in reversed(getattr(self.host, 'guidance_import_audit', None) or []) if r['kind'] == kind), None)
        try:
            revision = file_revision(path)
            if previous and tuple(previous.get('revision', ())) == revision:
                self.import_next()
                return
        except OSError as exc:
            self.import_failed(kind, previous, str(exc))
            return
        controller = vars(self.host).setdefault('guidance_import_controller', GuidanceImports(self.host))
        controller.accept(kind, path, lambda _: self.import_next(), lambda error: self.import_failed(kind, previous, error))

    def import_failed(self, kind, previous, error):
        source = self.active_contract['sources'][kind]
        if source['retain_previous'] and previous and Path(previous['path']).is_file():
            self.status(f'{kind}: replacement unavailable; retaining accepted input. {error}')
            self.import_next()
        else:
            self.fail(f'{kind}: {error}')

    def stage_inventory(self):
        h = self.host
        h.handle_site_config_submit()
        self.await_ready(lambda: bool(h.stockpile_data) and h.inventory_data_request_signature == h.inventory_opening_request_signature())

    def stage_guidance(self):
        h = self.host
        h.handle_guidance_schedules_submit()
        if not self.active:
            return
        h.store_stockpile_table()
        self.await_ready(lambda: bool(h.updated_stockpile_data))

    def stage_reconciliation(self):
        h = self.host
        h.prepare_data_streams()
        def applied():
            if vars(h).get('data_stream_refresh_errors'):
                raise ValueError('Reconciliation refresh failed: ' + '; '.join(h.data_stream_refresh_errors))
            h.handle_data_streams_submit()
            from GUI.WorkflowDependencies import reconciliation_input_revision
            self.await_ready(lambda: vars(h).get('reconciliation_applied_revision') == reconciliation_input_revision(h))
        self.await_ready(lambda: not vars(h).get('_reconciliation_review_pending') and
            vars(h).get('_reconciliation_review_signature') == h.reconciliation_review_signature(), applied)

    def stage_amt(self):
        h = self.host
        if not h.selected_AMT_data_source():
            self.await_ready()
            return
        h.setup_AMT_stockpile_table()
        def submit():
            h.validate_AMT_participation()
            draw = vars(h).get('draw_AMT_map')
            if draw is None:
                raise ValueError('AMT model preparation is unavailable.')
            footprints = sorted(h.selected_amt_footprints() - h.excluded_amt_footprints())
            settings = deepcopy(h.AMT_chunk_settings)
            for footprint in footprints:
                if footprint not in settings:
                    raise ValueError(f'Support must configure AMT chunk sizes for {footprint}.')
            def work():
                from copy import copy
                shadow = copy(draw)
                for key in ('data', 'selected_points', 'reclaim_directions', 'cut_directions', 'dig_paths',
                            'sequence_counter', 'excluded_hexes', 'geometry_outliers', 'direction_clicks'):
                    if hasattr(draw, key):
                        setattr(shadow, key, deepcopy(getattr(draw, key)))
                shadow.update_chunk_settings(settings)
                rows = []
                for footprint in footprints:
                    if shadow.reclaim_directions.get(footprint) and shadow.cut_directions.get(footprint):
                        rows, message = shadow.generate_chunks_from_directions(footprint, rows)
                    else:
                        rows, message = shadow.auto_generate_chunks_for_footprint(footprint, rows)
                    if not any(str(row.get('footprint', '')).upper() == footprint.upper() for row in rows):
                        raise ValueError(f'{footprint}: {message}')
                return shadow.return_hex_sequence()
            def done(rows):
                draw.selected_points = deepcopy(rows)
                h.hex_sequence_table = rows
                h.hex_sequence_table_argument = deepcopy(rows)
                h.reconcile_saved_AMT_chunk_grade_streams(force=True)
                if not h.store_hex_sequence_table(navigate=False):
                    raise ValueError('AMT chunk submission did not complete. Review AMT Stockpiles.')
                self.await_ready()
            h.run_background_task('Preparing AMT chunks…', work, done, self.fail)
        self.await_ready(lambda: bool(h.AMT_stockpile_data), submit)

    def stage_expit(self):
        h = self.host
        if int(h.expit_mode_choice or 1) == 2 and h.file_path_24hr_choice:
            h.refresh_expit_sequence_live()
            self.await_ready(lambda: not h.expit_sequence_refresh_in_progress and bool(h.expit_sequence_snapshot))
        else:
            self.await_ready()

    def stage_destination(self):
        h = self.host
        if not h.file_path_choice:
            self.await_ready()
            return
        h.sync_destination_progress_context()
        h.destination_progress.request_refresh()
        self.await_ready(lambda: not h.destination_progress._pending and h.destination_allocation_context() is not None)

    def stage_database(self):
        self.host.open_database_view(navigate=False)
        self.await_ready(lambda: not self.host.database_view_refresh_in_progress and
            self.host.database_view_snapshot_signature == self.host.database_view_input_signature())

    def stage_validate(self):
        h = self.host
        h.validate_AMT_participation()
        if h.file_path_choice and h.destination_allocation_context() is None:
            raise ValueError('Refresh Destination Reconciliation for the current inputs.')
        if not h.included_stockpile_data():
            raise ValueError('No selected inventory is available.')
        if h.data_stream_target_errors:
            raise ValueError('Product target refresh failed: ' + str(h.data_stream_target_errors))
        from GUI.WorkflowDependencies import preparation_issues
        issues = preparation_issues(h)
        if issues:
            raise ValueError('; '.join(issues))
        self.await_ready()

    def stage_optimise(self):
        h = self.host
        h.setup_calendar()
        h.store_calendar_inputs()
        self.await_ready(lambda: vars(h).get('_workflow_optimisation_finished', False))

    def stage_reports(self):
        self.host.refresh_sqlite_reports()
        self.await_ready()

    def cancel(self):
        if self.active:
            self.cancel_requested = True
            self.host.run_program.request_abort()
            self.status('Cancellation requested; waiting for the current operation to stop.')

    def fail(self, error):
        if not self.active:
            return
        self.run.error = str(error.get('message', error) if isinstance(error, dict) else error)
        self.finish('failed')

    def finish(self, status):
        if status == 'plan_prepared' and (getattr(self.host, 'plan_readiness', {}) or {}).get('status') != 'ready':
            status = 'plan_requires_review'
        self.run.outputs['arrivals'] = self.arrival_status()
        if self.run.endpoint == 'plan':
            self.run.outputs['plan_readiness'] = deepcopy(getattr(self.host, 'plan_readiness', None) or {})
        self.active, self.run.status = False, status
        self.host.site_workflow_runs = [*(getattr(self.host, 'site_workflow_runs', None) or []),
                                        asdict(self.run)][-30:]
        directory = Path(self.host.scenario_session_directory) / 'workflow_runs'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (self.run.run_id + '.json')).write_text(json.dumps(asdict(self.run), default=str, indent=2), encoding='utf-8')
        self.status(status.replace('_', ' ').capitalize() + (': ' + self.run.error if self.run.error else '.'))
        self.retry_count[self.run.site_id] = self.retry_count.get(self.run.site_id, 0) + 1 if status == 'failed' else 0
        self.host.save_active_scenario_state()
        if not self.host.background_tasks:
            self.unlock()

    def unlock(self):
        self.timer.stop()
        self.waiting = self.after_wait = None
        self.host._workflow_run_start = None
        self.host.tabs.setEnabled(True)
        self.host.scenario_selector.setEnabled(True)
        self.host.scenario_toolbar.setEnabled(True)
        self.host.prepare_inputs_button.setEnabled(True)
        self.host.cancel_preparation_button.setVisible(False)

    def tick(self):
        h = self.host
        if self.active or h.access_role == 'planner' or h.background_tasks or vars(h).get('_closing_requested'):
            return
        try:
            contract = self.contract()
            if not contract['enabled'] or self.retry_count.get(h.active_scenario_id, 0) > contract['retries']:
                return
            if time.monotonic() - self.last_attempt.get(h.active_scenario_id, 0) >= contract['refresh_minutes'] * 60:
                self.start(contract['handoff'], scheduled=True)
        except ValueError as exc:
            self.status('Invalid site contract: ' + str(exc))


def install(host):
    host.site_workflow_controller = SiteWorkflowController(host)
    host.site_automation_panel = SiteAutomationPanel(host)
    host.register_page('site_automation', host.setup_tabs, host.site_automation_panel, 'Site Automation')
    host.prepare_inputs_button = QPushButton('Prepare Inputs')
    host.prepare_inputs_button.clicked.connect(lambda: host.site_workflow_controller.start('prepare'))
    host.preparation_status_label = QLabel('Select a configured site, then prepare its inputs.')
    host.preparation_status_label.setWordWrap(True)
    host.cancel_preparation_button = QPushButton('Cancel Preparation')
    host.cancel_preparation_button.clicked.connect(host.site_workflow_controller.cancel)
    host.cancel_preparation_button.setVisible(False)
    status_bar = QHBoxLayout()
    status_bar.addWidget(host.preparation_status_label, 1)
    status_bar.addWidget(host.cancel_preparation_button)
    host.scenario_toolbar.layout().addWidget(host.prepare_inputs_button)
    host.layout.insertLayout(1, status_bar)
    host.site_automation_panel.refresh()
