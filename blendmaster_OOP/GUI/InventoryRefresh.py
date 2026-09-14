"""Prepare opening inputs privately, preserving the last published plan on failure."""
from contextlib import closing
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event
import math
import hashlib
import sqlite3
import tempfile
import uuid

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton
from database.DatabaseContext import database_scope, get_database_path
from GUI.InventoryStreamApplication import InventoryContext, FIELDS
from GUI.InputPreparationLocks import acquire, release
from classes.CombinedOPFReconciliation import SOURCE_FIELDS


TABLES = ('opening_stockpile_inventories', 'opening_AMT_stockpile_inventories')
EXTRA_FIELDS = ('AMT_last_refresh_datetime', 'AMT_refresh_tolerance_minutes', 'time_mode_choice',
                'AMT_chunk_settings', 'hex_sequence_table', 'hex_sequence_table_argument',
                '_combined_opf_profile_cache', *SOURCE_FIELDS)
RESULT_FIELDS = ('stockpile_data', 'updated_stockpile_data', 'stockpile_data_use_column',
    'stockpile_data_AMT_column', 'AMT_stockpile_data', 'AMT_footprint_exclusions',
    'AMT_chunk_settings', 'hex_sequence_table', 'hex_sequence_table_argument',
    'AMT_data_request_signature', 'AMT_enrichment_signature', 'AMT_chunk_reconciliation_signature',
    'AMT_last_refresh_datetime', 'historical_recon_warnings', '_combined_opf_profile_cache',
    'opening_inputs_revision')


def issue(host, *, include_workflow=False):
    request = vars(host).get('_inventory_refresh_request') or {}
    if request.get('status') in ('running', 'publishing', 'cancelling'):
        return 'Opening stockpiles and AMT are being prepared. The previous plan is available for viewing.'
    if request.get('status') == 'failed':
        return 'Opening-input refresh did not complete. Retry or keep the previous inputs before calculating.'
    workflow = vars(host).get('site_workflow_controller')
    if include_workflow and workflow and workflow.active:
        return 'Inputs are being prepared. The previous plan is available for viewing; calculations must wait.'
    return ''


def collect(host):
    """Read editable cells without changing accepted selections or evidence."""
    stockpiles = {name: dict(row) for name, row in host.stockpile_data.items()}
    selected, use, amt = {}, {}, {}
    rate_column = host.stockpile_table_column_index('Max Reclaim Rate (t/h)')
    threshold_column = host.stockpile_table_column_index('Reclaim Threshold (WMT)')
    subset_column = host.stockpile_table_column_index('Subset')
    for index in range(host.stockpile_table.rowCount()):
        name_item = host.stockpile_table.item(index, 2)
        if name_item is None:
            continue
        name = name_item.text()
        row = stockpiles.get(name)
        if row is None:
            raise ValueError(f'{name}: the opening inventory row is unavailable. Refresh the inventory first.')
        def checked(column):
            wrapper = host.stockpile_table.cellWidget(index, column)
            return bool(wrapper and wrapper.layout().itemAt(0).widget().isChecked())
        use[name], amt[name] = checked(0), checked(0) and checked(1)
        if rate_column is not None:
            item = host.stockpile_table.item(index, rate_column)
            try:
                rate = float(item.text())
            except (AttributeError, TypeError, ValueError):
                rate = math.nan
            if not math.isfinite(rate) or rate <= 0:
                raise ValueError(f'{name}: Max Reclaim Rate must be greater than 0 t/h.')
            row['max_reclaim_rate'] = rate
        if subset_column is not None:
            item = host.stockpile_table.item(index, subset_column)
            choice = host.stockpile_table.cellWidget(index, subset_column)
            row['subset'] = choice.currentText().strip() if choice is not None else item.text().strip() if item else ''
        if use[name]:
            item = host.stockpile_table.item(index, threshold_column) if threshold_column is not None else None
            row['reclaim_threshold'] = host.parse_formatted_number(item.text(), 0.0) if item else 0.0
            row['AMT'] = amt[name]
            selected[name.upper()] = {key.lower(): value.lower() if key.lower() == 'name' and isinstance(value, str)
                                      else value for key, value in row.items()}
    if not selected:
        raise ValueError('Select at least one stockpile before submitting.')
    penalty = vars(host).get('rehandle_cycle_time_penalty_checkbox')
    if penalty is not None and penalty.isChecked():
        missing = [name for name, row in selected.items() if not row.get('rehandle_cycle_time_minutes')]
        if missing:
            raise ValueError('No selected-crusher haul cycle was found for: ' + ', '.join(sorted(missing)))
    return dict(stockpile_data=stockpiles, updated_stockpile_data=selected,
                stockpile_data_use_column=use, stockpile_data_AMT_column=amt)


@dataclass
class Prepared:
    directory: object
    values: dict
    totals: dict
    message: str

    @property
    def database(self):
        return str(Path(self.directory.name) / 'opening.db')

    def close(self):
        self.directory.cleanup()


def check_cancel(cancel):
    if cancel.is_set():
        raise InterruptedError('Inventory refresh cancelled; previous inputs retained.')


def prepare(implementation, values, selection, service, cancel, *, force=False):
    """No Qt access or active-database writes are allowed inside this worker."""
    stage = Prepared(tempfile.TemporaryDirectory(prefix='blendmaster-opening-'), {}, {}, '')
    try:
        context = InventoryContext(implementation, deepcopy({**values, **selection}))
        context.opening_stockpile_inventories = service
        footprints = {name for name, row in context.updated_stockpile_data.items() if row.get('amt')}
        context.prune_unselected_amt_state(footprints)
        source = context.selected_AMT_data_source()
        signature = context.AMT_opening_request_signature(source)
        cached_signature = vars(context).get('AMT_data_request_signature', '')
        retained, needed = {}, {}
        for name, row in source.items():
            subset = {name: row}
            reusable, _ = context.AMT_cached_snapshot_is_reusable(
                cached_signature, context.AMT_opening_request_signature(subset))
            if not force and reusable and not context.AMT_data_compatibility_issue(subset):
                retained[name] = context.AMT_stockpile_data[name]
            else:
                needed[name] = row
        check_cancel(cancel)
        with database_scope(stage.database):
            if needed:
                builds = [row.get('build') for row in needed.values()]
                if any(not value for value in builds):
                    raise ValueError('Every selected AMT footprint needs an opening inventory build.')
                fetched = service.call_opening_AMT_stockpile_inventories(builds, context.start_time_choice)
                context.AMT_stockpile_data = {**retained, **context.AMT_snapshot_for_selected_footprints(fetched, needed)}
                if not retained: context.AMT_last_refresh_datetime = datetime.now()
                context.AMT_enrichment_signature = ''
                stage.message = f'Opening inputs prepared: {len(needed)} AMT footprints refreshed, {len(retained)} reused.'
            else:
                context.AMT_stockpile_data = context.AMT_snapshot_for_selected_footprints(
                    vars(context).get('AMT_stockpile_data') or {}, source)
                stage.message = 'Opening stockpiles prepared; compatible AMT data reused.'
            check_cancel(cancel)
            missing = [name for name, row in source.items()
                       if not context.AMT_stockpile_data.get(name) and float(row.get('balance') or 0) > 0]
            if missing:
                raise ValueError('AMT opening data is missing for: ' + ', '.join(missing))
            context.AMT_data_request_signature = signature if source else ''
            context.apply_canonical_field_mappings()
            context.apply_grade_streams_to_inventory(allow_pending=True)
            context.refresh_AMT_enrichment_if_needed(source, persist=False, refresh_map=False, allow_pending=True)
            context.prune_zeroed_amt_chunks()
            stage.totals = {name: (*context.AMT_footprint_totals(name, row), context.AMT_raw_signed_footprint_total(name))
                            for name, row in context.updated_stockpile_data.items() if row.get('amt')}
            for name in source:
                setting = context.get_AMT_chunk_setting(name)
                total, inventory, raw = stage.totals[name]
                plan = context.calculate_AMT_chunk_plan(total, setting['average_reclaim_rate'], setting['chunk_reclaim_hours'])
                context.AMT_chunk_settings[name] = {**setting, **plan, 'amt_total_wmt':total,
                    'inventory_total_wmt':inventory, 'raw_signed_amt_wmt':raw}
            from classes.CombinedOPFReconciliation import reusable_cache
            # Opening refresh may reuse a valid profile; preparation waits for
            # AMT/Calendar submission after the user has approved new sources.
            context._combined_opf_profile_cache = reusable_cache(vars(context))
            check_cancel(cancel)
            service.save_to_database(context.included_AMT_snapshot(context.stockpile_data))
            service.save_AMT_to_database(context.AMT_stockpile_data)
            stage.values = {name: vars(context).get(name) for name in RESULT_FIELDS}
            with closing(sqlite3.connect(stage.database)) as connection:
                if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Prepared opening data failed its database validation.')
            with open(stage.database, 'rb') as snapshot:
                stage.values['opening_inputs_revision'] = hashlib.file_digest(snapshot, 'sha256').hexdigest()
            check_cancel(cancel)
        return stage
    except BaseException:
        stage.close()
        raise


def publish(stage, database, cancel):
    """One transaction replaces only the opening tables; saved plans stay intact."""
    try:
        check_cancel(cancel)
        with closing(sqlite3.connect(database, timeout=30)) as connection:
            # Saved-result readers can continue using their committed snapshot
            # while a large AMT table is copied into the new input version.
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('ATTACH DATABASE ? AS prepared', (stage.database,))
            schemas = {table: connection.execute(
                'SELECT sql FROM prepared.sqlite_master WHERE type=\'table\' AND name=?', (table,)).fetchone()
                for table in TABLES}
            if any(not row for row in schemas.values()):
                raise ValueError('The prepared opening snapshot is incomplete.')
            connection.execute('BEGIN IMMEDIATE')
            try:
                for table in TABLES:
                    connection.execute(f'DROP TABLE IF EXISTS main."{table}"')
                    connection.execute(schemas[table][0])
                    connection.execute(f'INSERT INTO main."{table}" SELECT * FROM prepared."{table}"')
                    check_cancel(cancel)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return stage.values, stage.totals, stage.message
    finally:
        stage.close()


class InventoryRefresh:
    def __init__(self, host):
        self.host, self.cancelled, self.held = host, Event(), []
        self.bar = QFrame(host)
        layout = QHBoxLayout(self.bar); layout.setContentsMargins(8, 4, 8, 4)
        self.label = QLabel(); self.label.setWordWrap(True); self.label.setTextFormat(Qt.PlainText)
        layout.addWidget(self.label, 1)
        self.force = False
        self.retry = QPushButton('Retry refresh'); self.retry.clicked.connect(lambda: self.start(force=self.force))
        self.keep = QPushButton('Keep previous inputs'); self.keep.clicked.connect(self.discard)
        self.cancel = QPushButton('Cancel refresh'); self.cancel.clicked.connect(self.request_cancel)
        for button in (self.retry, self.keep, self.cancel): layout.addWidget(button)
        host.layout.insertWidget(1, self.bar)
        self.bar.hide()

    def status(self, message, state):
        request = vars(self.host).get('_inventory_refresh_request') or {}
        self.host._inventory_refresh_request = {**request, 'status': state}
        self.label.setText(message); self.label.setToolTip(''); self.bar.show()
        self.retry.setVisible(state == 'failed'); self.keep.setVisible(state == 'failed')
        self.cancel.setVisible(state in ('running', 'cancelling'))
        self.cancel.setEnabled(state == 'running')

    def unlock(self):
        release(self.host, self.held); self.held = []

    def request_cancel(self):
        self.cancelled.set()
        self.status('Cancellation requested. The current data fetch must finish; the previous plan remains available.', 'cancelling')

    def discard(self):
        if (vars(self.host).get('_inventory_refresh_request') or {}).get('status') != 'failed':
            return
        self.host._inventory_refresh_request = {}
        accepted = vars(self.host).get('updated_stockpile_data') or {}
        self.host.stockpile_data_use_column = {name: name.upper() in accepted for name in self.host.stockpile_data}
        self.host.stockpile_data_AMT_column = {name: bool(accepted.get(name.upper(), {}).get('amt')) for name in self.host.stockpile_data}
        for row in range(self.host.stockpile_table.rowCount()):
            item = self.host.stockpile_table.item(row, 2)
            if item:
                data = accepted.get(item.text().upper())
                self.host.set_stockpile_checkbox(row, 0, data is not None)
                self.host.set_stockpile_checkbox(row, 1, bool(data and data.get('amt')))
                original = data or self.host.stockpile_data.get(item.text(), {})
                for caption, key, default in (('Subset', 'subset', ''),
                        ('Max Reclaim Rate (t/h)', 'max_reclaim_rate', 1000),
                        ('Reclaim Threshold (WMT)', 'reclaim_threshold', 0)):
                    column = self.host.stockpile_table_column_index(caption)
                    cell = self.host.stockpile_table.item(row, column) if column is not None else None
                    if cell is not None:
                        cell.setText(str(original.get(key, default)))
                    if key == 'subset' and column is not None:
                        value = str(original.get(key, default))
                        picker = self.host.stockpile_table.cellWidget(row, column)
                        if picker is not None:
                            blocked = picker.blockSignals(True)
                            if picker.findText(value) < 0:
                                picker.addItem(value)
                            picker.setCurrentText(value)
                            picker.blockSignals(blocked)
                        self.host.stockpile_data[item.text()]['subset'] = value
        self.label.setText('Previous opening inputs retained.'); self.retry.hide(); self.keep.hide()

    def start(self, *, force=False):
        h = self.host
        if (self.held or vars(h).get('_data_stream_application_pending') or vars(h).get('_project_save_pending')
                or vars(h).get('_background_input_locks')):
            return False
        try:
            selection = collect(h)
        except ValueError as exc:
            h._inventory_refresh_request = {'token': uuid.uuid4().hex}
            self.status('Selection was not applied. ' + str(exc), 'failed')
            return False
        from GUI.WorkflowDependencies import input_revision
        self.cancelled = Event()
        self.force = force
        h._inventory_refresh_request = {'token': uuid.uuid4().hex, 'status': 'running'}
        revision, database = input_revision(h), get_database_path()
        values = {name: vars(h)[name] for name in (*FIELDS, *EXTRA_FIELDS) if name in vars(h)}
        self.held = acquire(h, readable_results=True)
        self.status('Preparing opening stockpiles and AMT. You can continue viewing the previous plan.', 'running')
        def failed(error):
            self.unlock()
            message = error.get('message', str(error)) if isinstance(error, dict) else str(error)
            detail = message.strip().splitlines()[-1] if 'Traceback (most recent call last)' in message else message
            self.status('Opening refresh was not applied. Previous inputs and plan retained. ' + detail, 'failed')
            self.label.setToolTip(message)
        def prepared(stage):
            if self.cancelled.is_set() or database != get_database_path() or input_revision(h) != revision:
                stage.close(); failed('The request was cancelled or its inputs changed. Submit the current selection again.')
                return
            self.status('Applying validated opening inputs. The previous plan remains available.', 'publishing')
            h.run_background_task('Applying opening inputs…', lambda: publish(stage, database, self.cancelled),
                                  completed, failed, show_progress=False)
        def completed(result):
            values, totals, message = result
            for name, value in values.items(): setattr(h, name, value)
            h.updated_stockpile_data_keys = h.updated_stockpile_data.keys()
            h.database_view_rows = []; h.database_view_snapshot_signature = None; h.database_view_refresh_pending = True
            h.total_AMT_stockpile_balances = {}
            self.status(message + ' Saved results remain available; prepare/review reconciliation before recalculating.', 'ready')
            self.unlock()
            deferred = vars(h).get('_defer_opf_profile_preparation', False)
            h._defer_opf_profile_preparation = True
            try:
                h.save_active_scenario_state()
                h.setup_calendar()
                h.ensure_AMT_map_panel()
                h.finish_AMT_stockpile_table(h.selected_AMT_data_source(), h.AMT_stockpile_data,
                    reuse_prepared=True, refresh_prepared_map=True, prepared=True, prepared_totals=totals)
                h.populate_define_fields_table()
                h.set_page_enabled(h.define_fields_tab_index, True)
                h.advance_workspace('stockpile_inventories')
                from GUI.WorkflowViews import schedule
                schedule(h, results=True, charts=True)
            except Exception as exc:
                self.status(message + ' Opening data was applied, but a view needs refreshing: ' + str(exc), 'ready')
            finally:
                h._defer_opf_profile_preparation = deferred
        h.run_background_task('Preparing opening inputs…',
            lambda: prepare(type(h), values, selection, h.opening_stockpile_inventories, self.cancelled, force=force),
            prepared, failed, show_progress=False)
        return True


def start(host, *, force=False):
    controller = vars(host).get('_inventory_refresh_controller')
    if controller is None:
        controller = host._inventory_refresh_controller = InventoryRefresh(host)
    return controller.start(force=force)
