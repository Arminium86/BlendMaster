"""Destination setup and evidence review; no payload allocation takes place here."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path

from PyQt5 import sip
from PyQt5.QtCore import Qt, pyqtSignal, QAbstractTableModel, QModelIndex, QSize
from PyQt5.QtGui import QDoubleValidator, QFontMetrics
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                            QDoubleSpinBox, QProgressBar, QTabWidget, QTableWidget,
                            QTableWidgetItem, QHeaderView, QAbstractItemView, QComboBox,
                            QLineEdit, QPlainTextEdit, QTableView)

from classes.DestinationBuildOrder import extract_build_order, inventory_areas, digest
from classes.DestinationSupplementalLanes import add_24hr_lanes, SUPPLEMENTAL_ORIGIN
from classes.DestinationProgress import progress_settings, resolve_progress, remaining_2wp_estimates, ESTIMATED_CAPACITY_BASIS
from setup.RecentDestinationActivity import RecentDestinationActivity
from setup.ProductAssayHistory import awst


def instance_label(row):
    if row and row.get("origin") == SUPPLEMENTAL_ORIGIN:
        return row["destination"] + " · 24-hour plan allowance"
    return f"{row['order_position']}. {row['destination']} · build {row['build_instance']}" if row else "—"


def context_key(scenario_id, site, start, path, areas, supplemental_path="", selected_agents=None):
    try:
        stat = Path(path).stat() if path else None
    except OSError:
        stat = None
    key = [scenario_id, site, start, str(path), (stat.st_size, stat.st_mtime_ns) if stat else None, areas]
    if supplemental_path:
        try:
            extra = Path(supplemental_path).stat()
        except OSError:
            extra = None
        key.append([str(supplemental_path), (extra.st_size, extra.st_mtime_ns) if extra else None, sorted(selected_agents or [])])
    return digest(key)


class EvidenceModel(QAbstractTableModel):
    """Keep large warehouse/2WP audits as records; render only requested cells."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.records, self.columns = [], []

    def replace(self, records, columns):
        self.beginResetModel()
        self.records, self.columns = records, columns
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and role in (Qt.DisplayRole, Qt.ToolTipRole):
            key = self.columns[index.column()][0]
            value = self.records[index.row()].get(key)
            if role == Qt.DisplayRole and key in ("wmt", "planned_wmt") and isinstance(value, (int, float)):
                return f"{value:,.1f}"
            return ", ".join(map(str, value)) if isinstance(value, list) else "—" if value is None else str(value)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.columns[section][1]
        if role == Qt.SizeHintRole and orientation == Qt.Horizontal:
            font = self.parent().horizontalHeader().font()
            font.setBold(True)
            metrics = QFontMetrics(font)
            return QSize(metrics.horizontalAdvance(self.columns[section][1]) + 32, metrics.height() + 18)
        return None


class DestinationProgressSetup(QWidget):
    settingsChanged = pyqtSignal(dict)
    auditReady = pyqtSignal(dict)

    def __init__(self, parent=None, *, service=None, run_async):
        super().__init__(parent)
        self.service = service or RecentDestinationActivity()
        self.run_async = run_async
        self._generation, self._pending, self._context_key = 0, False, None
        self._settings = progress_settings()
        self.snapshot, self.rows, self.capacity_fields = None, [], {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        title = QLabel("Destination Reconciliation")
        title.setStyleSheet("font-size: 20px; font-weight: 750; color: #172033;")
        layout.addWidget(title)
        note = QLabel("For 2WP rows, detection uses matching actual inbound movements; a successful lookup with no matches assumes the first build. Materials found only in the 24-hour plan require an explicit destination and allowance. ROM area uses Nearest Crusher from Stockpile Inventories.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.context_label = QLabel("Set the scenario start, import 2WP Mining.csv and load Stockpile Inventories.")
        self.context_label.setWordWrap(True)
        layout.addWidget(self.context_label)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Activity lookback (hours)"))
        self.lookback = QDoubleSpinBox()
        self.lookback.setRange(.01, 744)
        self.lookback.setDecimals(2)
        self.lookback.setValue(12)
        controls.addWidget(self.lookback)
        controls.addWidget(QLabel("Immediately before scenario start · AWST"))
        controls.addStretch()
        self.refresh = QPushButton("Refresh")
        controls.addWidget(self.refresh)
        layout.addLayout(controls)
        self.activity_window = QLabel("")
        self.activity_window.setWordWrap(True)
        self.activity_window.setToolTip("The window ends at scenario start, not the current clock time. Only undeleted PrimaryMovement / ExPit / Expit Ore / Expit Ore rows qualify. Destination FMS must match a stockpile with Nearest Crusher; source grade block and positive ROM WMT are required. Direct feed, waste and rehandle are excluded.")
        layout.addWidget(self.activity_window)
        self.status = QLabel("Ready — open this tab after setting the scenario.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("padding: 8px; background: #edf6ff; color: #1e4f8a; border-radius: 4px;")
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(5)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        overview = QWidget()
        overview_layout = QVBoxLayout(overview)
        self.table = self.new_table()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        overview_layout.addWidget(self.table, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(210)
        overview_layout.addWidget(self.details)
        self.tabs.addTab(overview, "Progress")
        self.order_table, self.activity_table, self.audit_table = [self.new_table(records=True) for _ in range(3)]
        self.tabs.addTab(self.order_table, "2WP Build order")
        self.tabs.addTab(self.activity_table, "Actual movements")
        audit = QWidget()
        audit_layout = QVBoxLayout(audit)
        audit_controls = QHBoxLayout()
        audit_controls.addWidget(QLabel("Show"))
        self.audit_filter = QComboBox()
        for label, key in (("ROM inbound", "included"), ("Excluded ROM inbound", "excluded_inbound"),
                           ("Reclaim evidence", "reclaim"), ("All rows", "all")):
            self.audit_filter.addItem(label, key)
        self.audit_filter.setToolTip("Reclaim evidence identifies build → reclaim → build transitions. Other CSV rows are retained in All rows for traceability.")
        audit_controls.addWidget(self.audit_filter)
        self.audit_count = QLabel("")
        audit_controls.addWidget(self.audit_count)
        audit_controls.addStretch()
        audit_layout.addLayout(audit_controls)
        audit_layout.addWidget(self.audit_table)
        self.tabs.addTab(audit, "2WP row audit")
        self.validation = QLabel("")
        self.validation.setWordWrap(True)
        self.validation.setStyleSheet("color: #a33b16;")
        layout.addWidget(self.validation)
        footer = QLabel("For 2WP builds, blank Remaining (ROM WMT) uses the remaining-delivery estimate, shared across the physical build's material types. For 24-hour-only rows, blank remains unresolved. Zero means no remaining capacity or allowance. Edits apply immediately; save the project to retain them and recalculate the plan to update assignments.")
        footer.setWordWrap(True)
        footer.setStyleSheet("color: #526474;")
        layout.addWidget(footer)
        self.refresh.clicked.connect(lambda: self.request_refresh(force=True))
        self.lookback.valueChanged.connect(self.lookback_changed)
        self.table.itemSelectionChanged.connect(self.show_details)
        self.audit_filter.currentIndexChanged.connect(self.render_audit)

    @staticmethod
    def new_table(records=False):
        table = QTableView() if records else QTableWidget()
        if records:
            table.setModel(EvidenceModel(table))
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().hide()
        header_font = table.horizontalHeader().font()
        header_font.setPixelSize(14)
        table.horizontalHeader().setFont(header_font)
        table.horizontalHeader().setStyleSheet("QHeaderView::section { font-size: 14px; font-weight: 600; padding: 6px 10px; }")
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setResizeContentsPrecision(100)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    @staticmethod
    def fill(table, rows, columns):
        if isinstance(table.model(), EvidenceModel):
            table.model().replace(rows, columns)
            return
        table.setRowCount(0)
        table.setColumnCount(len(columns))
        table.setHorizontalHeaderLabels([label for key, label in columns])
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, (key, _) in enumerate(columns):
                value = row.get(key)
                text = ", ".join(map(str, value)) if isinstance(value, list) else "—" if value is None else str(value)
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                table.setItem(i, j, item)

    def settings(self):
        return deepcopy(self._settings)

    def emit_settings(self):
        self.settingsChanged.emit(self.settings())

    def reset_context(self):
        self._context_key = None
        self.invalidate("Scenario changed — open this tab to load destination reconciliation.")

    def invalidate(self, message):
        self._generation += 1
        self._pending = False
        self.snapshot, self.rows, self.capacity_fields = None, [], {}
        self.progress.hide()
        self.refresh.setEnabled(True)
        self.table.setRowCount(0)
        for table in (self.order_table, self.activity_table, self.audit_table):
            table.model().replace([], [])
        self.details.clear()
        self.audit_count.clear()
        self.status.setText(message)
        self.validation.clear()

    def set_context(self, *, scenario_id, site, scenario_start, path, inventories, state=None, supplemental_path="", selected_agents=None):
        areas = inventory_areas(inventories)
        start = awst(scenario_start).isoformat() if scenario_start is not None else None
        key = context_key(scenario_id, site, start, path, areas, supplemental_path, selected_agents)
        if key == self._context_key:
            return
        self.invalidate("Ready — Refresh to load destination reconciliation.")
        self._context_key = key
        self._context = dict(scenario_id=scenario_id, site=site, start=start, path=str(path or ""), inventories=deepcopy(inventories or {}), supplemental_path=str(supplemental_path or ""), selected_agents=list(selected_agents or []))
        self._settings = progress_settings(state)
        self.lookback.blockSignals(True)
        self.lookback.setValue(self._settings["lookback_hours"])
        self.lookback.blockSignals(False)
        self.context_label.setText(f"Site: {site or 'not set'} · Scenario start: {start or 'not set'} AWST · 2WP: {Path(path).name if path else 'not selected'}")
        if supplemental_path:
            self.context_label.setText(self.context_label.text() + f" · 24-hour plan: {Path(supplemental_path).name}")
        self.update_activity_window()

    def allocation_context(self, *, scenario_id, site, scenario_start, path, inventories, supplemental_path="", selected_agents=None):
        """Freeze reviewed inputs for a plan; never return an old scenario's data."""
        if not self.snapshot or self._pending:
            return None
        start = awst(scenario_start).isoformat() if scenario_start is not None else None
        if context_key(scenario_id, site, start, path, inventory_areas(inventories), supplemental_path, selected_agents) != self._context_key:
            return None
        if self._settings["context_signature"] != self.snapshot["context_signature"]:
            return None
        order = self.snapshot["order"]
        return deepcopy(dict(order={k: order[k] for k in ("schema_version", "signature", "source_file", "orders", "areas", "supplemental_lanes") if k in order},
                             activity=self.snapshot["activity"], settings=self._settings,
                             context_signature=self.snapshot["context_signature"], scenario_id=scenario_id, site=site, start=start))

    def update_activity_window(self):
        context = getattr(self, "_context", {})
        if not context.get("start"):
            self.activity_window.clear()
            return
        end = awst(context["start"])
        start = end - timedelta(hours=self.lookback.value())
        operation = RecentDestinationActivity.warehouse_operation(context["site"]).title()
        self.activity_window.setText(f"Activity window (AWST): {start:%d %b %Y %H:%M:%S} inclusive → {end:%d %b %Y %H:%M:%S} exclusive · {operation}")

    def lookback_changed(self):
        self.update_activity_window()
        self._settings["lookback_hours"] = self.lookback.value()
        self._settings["selected_instances"] = {}
        self.emit_settings()
        self.invalidate("Activity lookback changed — refreshing destination reconciliation.")
        self.request_refresh()

    def request_refresh(self, force=False):
        if not self._context_key or self._pending:
            return
        context = deepcopy(self._context)
        if not context["site"] or not context["start"] or not context["path"]:
            self.invalidate("Set the scenario site and start, and select 2WP Mining.csv in Guidance Schedules.")
            return
        self.invalidate("Loading destination reconciliation…")
        self._pending = True
        generation = self._generation
        hours = self.lookback.value()
        self.progress.show()
        self.refresh.setEnabled(False)

        def work():
            order = extract_build_order(context["path"], context["inventories"])
            order = add_24hr_lanes(order, context["supplemental_path"], context["start"], context["selected_agents"])
            try:
                activity = self.service.fetch(context["site"], context["start"], hours, order["areas"], order["signature"], force_refresh=force) if order["orders"] else None
                error = None
            except Exception as exc:
                activity, error = None, str(exc)
            return dict(order=order, activity=activity, error=error,
                        context_signature=digest([context["scenario_id"], context["site"], context["start"], order["signature"]]))

        def success(snapshot):
            if sip.isdeleted(self) or generation != self._generation:
                return
            self._pending = False
            self.progress.hide()
            self.refresh.setEnabled(True)
            self.snapshot = snapshot
            if self._settings["context_signature"] != snapshot["context_signature"]:
                had_values = bool(self._settings["remaining_wmt"] or self._settings["selected_instances"])
                self._settings.update(context_signature=snapshot["context_signature"], remaining_wmt={}, selected_instances={})
                if had_values:
                    self.validation.setText("Scenario time or source data changed. Review current instances and re-enter remaining ROM WMT.")
            self.emit_settings()
            self.render()
            self.auditReady.emit(deepcopy(snapshot["order"]))

        def failure(error):
            if sip.isdeleted(self) or generation != self._generation:
                return
            self.invalidate(f"Unable to load destination reconciliation: {error}")

        self.run_async(work, success, failure)

    def render(self, refresh_evidence=True):
        order, activity = self.snapshot["order"], self.snapshot["activity"] or {}
        self.rows = resolve_progress(order, activity, self._settings["selected_instances"])
        if not order["orders"] and not order.get("supplemental_lanes"):
            status = "No planned ROM destinations. Review the 2WP row audit and Nearest Crusher values."
        elif not order["orders"]:
            status = "No 2WP build order. Review the 24-hour plan only rows and enter explicit destinations and allowances."
        elif self.snapshot["error"]:
            status = "Activity unavailable — " + self.snapshot["error"]
        else:
            source = {"fresh": "Fresh activity", "cached": "Cached activity", "offline_cached": "Offline — using cached activity"}.get(activity.get("status"), "Activity")
            status = f"{source} · Fetched {activity.get('fetched_at', '—')} AWST · {len(activity.get('records', []))} qualifying movements"
            if not activity.get("records"):
                status += " · No qualifying inbound activity in this window."
            if any(r["ambiguous"] for r in self.rows):
                status += " · Ambiguous activity — review current build instances."
        self.status.setText(status)
        summaries = [dict(rom_area=r["rom_area"], material_type=r["material_type"], detected=r["detected_destination"] or ("Ambiguous" if r["ambiguous"] else "Not detected"),
                          current="", previous=instance_label(r["previous"]), next=instance_label(r["next"]), remaining="", basis=r["selection_basis"]) for r in self.rows]
        self.fill(self.table, summaries, [("rom_area", "ROM area"), ("material_type", "Material type"), ("detected", "Detected destination"), ("current", "Current build instance / destination"), ("previous", "Previous"), ("next", "Next"), ("remaining", "Remaining (ROM WMT)"), ("basis", "Selection basis")])
        self.capacity_fields = {}
        self.capacity_estimates = remaining_2wp_estimates(order, self._context.get("start"))
        for i, row in enumerate(self.rows):
            combo = QComboBox()
            combo.setToolTip("Select the current build instance from the 2WP Build order. A manual selection overrides automatic detection or the assumed first build for this ROM area/material type. The planned destinations and their sequence remain as defined in 2WP.")
            combo.setMinimumWidth(combo.fontMetrics().horizontalAdvance("Automatic / review required") + 45)
            combo.addItem("Select destination" if row.get("supplemental") else "Automatic / review required", "")
            if row.get("supplemental"):
                combo.setToolTip(SUPPLEMENTAL_ORIGIN + ". Choose a stockpile in the same ROM area. These are destination choices, not a build order; the allowance never advances to another choice.")
            for entry in row["sequence"]:
                combo.addItem(instance_label(entry), entry["instance_id"])
            current = row["current"]
            combo.setCurrentIndex(combo.findData(current["instance_id"]) if current else 0)
            combo.currentIndexChanged.connect(lambda _, key=row["lane_key"], field=combo: self.choose_instance(key, field.currentData()))
            self.table.setCellWidget(i, 3, combo)
            field = QLineEdit()
            field.setPlaceholderText("Not set")
            field.setMinimumWidth(130)
            field.setValidator(QDoubleValidator(0, 1e15, 10, field))
            field.setEnabled(current is not None)
            if current:
                key = current["instance_id"]
                value = self._settings["remaining_wmt"].get(key)
                estimate = self.capacity_estimates.get(key)
                if estimate is not None:
                    field.setPlaceholderText(f"Estimated: {estimate:,.1f}")
                field.setToolTip(f"{ESTIMATED_CAPACITY_BASIS}: {estimate:,.1f} ROM WMT. Used when blank; an entered value, including zero, overrides it. Shared across material types in this physical build. Intervals crossing scenario start are prorated by time." if estimate is not None else "No remaining 2WP delivery estimate is available. Enter remaining ROM WMT or refresh the 2WP inputs.")
                if row.get("supplemental"):
                    field.setToolTip("Enter an explicit ROM WMT allowance for this material type and destination. Blank remains unresolved; zero leaves no allowance. This does not change an existing 2WP build's capacity.")
                field.setText(str(value) if value is not None else "")
                field.textEdited.connect(lambda text, key=key, field=field: self.edit_capacity(key, field, text))
                field.editingFinished.connect(lambda key=key, field=field: self.finish_capacity(key, field))
                self.capacity_fields.setdefault(key, []).append(field)
            self.table.setCellWidget(i, 6, field)
        self.table.resizeRowsToContents()
        if refresh_evidence:
            self.fill(self.order_table, order["orders"], [("rom_area", "ROM area"), ("material_type", "Material type"), ("order_position", "Order"), ("destination", "Destination"), ("build_instance", "Build instance"), ("first_inbound", "First planned inbound (AWST)"), ("last_inbound", "Last planned inbound (AWST)"), ("planned_wmt", "Planned ROM WMT"), ("csv_records", "CSV records")])
            self.fill(self.activity_table, activity.get("records", []), [("rom_area", "ROM area"), ("material_type", "Material type"), ("destination", "Destination"), ("observed_at", "Inbound time (AWST)"), ("wmt", "ROM WMT"), ("source_block", "Source grade block"), ("destination_build", "Actual destination build"), ("movement_id", "Movement ID")])
            self.render_audit()
        if self.rows:
            self.table.selectRow(0)
        self.show_details()

    def render_audit(self):
        if not self.snapshot:
            return
        records = self.snapshot["order"]["audit"]
        mode = self.audit_filter.currentData()
        if mode == "included":
            rows = [r for r in records if r["outcome"] == "included"]
        elif mode == "excluded_inbound":
            rows = [r for r in records if r.get("row_type") == "rom_inbound" and r["outcome"] == "excluded"]
        elif mode == "reclaim":
            rows = [r for r in records if r.get("row_type") == "reclaim"]
        else:
            rows = records
        columns = [("csv_record", "CSV record")]
        if mode != "included":
            columns.append(("outcome", "Outcome"))
        columns += [("reason", "Reason"), ("rom_area", "ROM area")]
        if mode != "reclaim":
            columns.append(("material_type", "Material type"))
        columns.append(("destination", "Destination"))
        if mode in ("reclaim", "all"):
            columns.append(("reclaimed_stockpile", "Reclaimed stockpile"))
        if mode != "reclaim":
            columns += [("build_instance", "Build instance"), ("order_position", "Order")]
        columns += [("planned_wmt", "ROM WMT"), ("start", "Start (AWST)"), ("source", "2WP source")]
        self.fill(self.audit_table, rows, columns)
        self.audit_count.setText(f"{len(rows):,} of {len(records):,} CSV records")

    def choose_instance(self, key, value):
        if value:
            self._settings["selected_instances"][key] = value
        else:
            self._settings["selected_instances"].pop(key, None)
        self.emit_settings()
        self.render(refresh_evidence=False)

    def edit_capacity(self, key, field, text):
        if text and not field.hasAcceptableInput():
            return
        if text:
            number, valid = field.validator().locale().toDouble(text)
            if not valid:
                return
            self._settings["remaining_wmt"][key] = number
        else:
            self._settings["remaining_wmt"].pop(key, None)
        for sibling in self.capacity_fields.get(key, []):
            if sibling is not field:
                sibling.setText(text)
        self.validation.clear()
        self.emit_settings()
        self.show_details()

    def finish_capacity(self, key, field):
        if field.text() and not field.hasAcceptableInput():
            value = self._settings["remaining_wmt"].get(key)
            field.setText(str(value) if value is not None else "")
            self.validation.setText("Remaining assignable tonnes must be a non-negative ROM WMT value. The previous value was retained.")
        else:
            self.edit_capacity(key, field, field.text())

    def show_details(self):
        if not self.snapshot:
            return
        index = self.table.currentRow()
        row = self.rows[index] if 0 <= index < len(self.rows) else None
        notes = list(self.snapshot["order"]["warnings"]) + list((self.snapshot["activity"] or {}).get("warnings", []))
        lines = []
        if row:
            lines = [f"{row['rom_area']} / {row['material_type']} — extracted order:",
                     "  →  ".join(instance_label(r) for r in row["sequence"]),
                     f"Latest inbound: {row['latest_inbound'] or 'none'} AWST · Activity: {row['activity_wmt']:,.1f} ROM WMT · {row['selection_basis']}"]
            notes = row["warnings"] + notes
            if row.get("supplemental"):
                lines = [f"{row['rom_area']} / {row['material_type']} — {SUPPLEMENTAL_ORIGIN}",
                         "24-hour plan sources: " + ", ".join(row["source_blocks"]),
                         "Destination: " + (row["current"]["destination"] if row["current"] else "Not selected")]
            if row["current"]:
                key = row["current"]["instance_id"]
                value = self._settings["remaining_wmt"].get(key)
                estimate = self.capacity_estimates.get(key)
                lines.append(f"Remaining (ROM WMT): {value:,.1f} · User entered" if value is not None else f"Remaining (ROM WMT): {estimate:,.1f} · {ESTIMATED_CAPACITY_BASIS}" if estimate is not None else "Remaining (ROM WMT): Not set — no 2WP estimate available.")
        lines.extend(notes)
        self.details.setPlainText("\n\n".join(lines))
