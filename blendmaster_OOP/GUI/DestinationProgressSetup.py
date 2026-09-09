"""Destination setup and evidence review; no payload allocation takes place here."""

from copy import deepcopy
from pathlib import Path

from PyQt5 import sip
from PyQt5.QtCore import Qt, pyqtSignal, QAbstractTableModel, QModelIndex, QSize
from PyQt5.QtGui import QDoubleValidator, QFontMetrics
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                            QDoubleSpinBox, QProgressBar, QTabWidget, QTableWidget,
                            QTableWidgetItem, QHeaderView, QAbstractItemView, QComboBox,
                            QLineEdit, QPlainTextEdit, QTableView)

from classes.DestinationBuildOrder import extract_build_order, inventory_areas, digest
from classes.DestinationProgress import progress_settings, resolve_progress
from setup.RecentDestinationActivity import RecentDestinationActivity
from setup.ProductAssayHistory import awst


def instance_label(row):
    return f"{row['order_position']}. {row['destination']} · build {row['build_instance']}" if row else "—"


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
            value = self.records[index.row()].get(self.columns[index.column()][0])
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
        title = QLabel("Destination Progress")
        title.setStyleSheet("font-size: 20px; font-weight: 750; color: #172033;")
        layout.addWidget(title)
        note = QLabel("Review the 2WP ROM build order against actual inbound activity, then enter remaining assignable tonnes for the current build instance. ROM area uses Nearest Crusher from Stockpile Inventories.")
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
        self.tabs.addTab(self.order_table, "Build order")
        self.tabs.addTab(self.activity_table, "Activity evidence")
        self.tabs.addTab(self.audit_table, "2WP row audit")
        self.validation = QLabel("")
        self.validation.setWordWrap(True)
        self.validation.setStyleSheet("color: #a33b16;")
        layout.addWidget(self.validation)
        footer = QLabel("Remaining assignable tonnes are entered in ROM WMT; blank means not set and 0 means no remaining capacity. Settings save with the site scenario. This setup does not yet change Material Destination Plan assignments.")
        footer.setWordWrap(True)
        footer.setStyleSheet("color: #526474;")
        layout.addWidget(footer)
        self.refresh.clicked.connect(lambda: self.request_refresh(force=True))
        self.lookback.valueChanged.connect(self.lookback_changed)
        self.table.itemSelectionChanged.connect(self.show_details)

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
        self.invalidate("Scenario changed — open this tab to load destination progress.")

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
        self.status.setText(message)
        self.validation.clear()

    def set_context(self, *, scenario_id, site, scenario_start, path, inventories, state=None):
        areas = inventory_areas(inventories)
        try:
            stat = Path(path).stat() if path else None
        except OSError:
            stat = None
        start = awst(scenario_start).isoformat() if scenario_start is not None else None
        key = digest([scenario_id, site, start, str(path), (stat.st_size, stat.st_mtime_ns) if stat else None, areas])
        if key == self._context_key:
            return
        self.invalidate("Ready — Refresh to load destination progress.")
        self._context_key = key
        self._context = dict(scenario_id=scenario_id, site=site, start=start, path=str(path or ""), inventories=deepcopy(inventories or {}))
        self._settings = progress_settings(state)
        self.lookback.blockSignals(True)
        self.lookback.setValue(self._settings["lookback_hours"])
        self.lookback.blockSignals(False)
        self.context_label.setText(f"Site: {site or 'not set'} · Scenario start: {start or 'not set'} AWST · 2WP: {Path(path).name if path else 'not selected'}")

    def lookback_changed(self):
        self._settings["lookback_hours"] = self.lookback.value()
        self._settings["selected_instances"] = {}
        self.emit_settings()
        self.invalidate("Activity lookback changed — refreshing destination progress.")
        self.request_refresh()

    def request_refresh(self, force=False):
        if not self._context_key or self._pending:
            return
        context = deepcopy(self._context)
        if not context["site"] or not context["start"] or not context["path"]:
            self.invalidate("Set the scenario site and start, and select 2WP Mining.csv in Guidance Schedules.")
            return
        self.invalidate("Loading destination progress…")
        self._pending = True
        generation = self._generation
        hours = self.lookback.value()
        self.progress.show()
        self.refresh.setEnabled(False)

        def work():
            order = extract_build_order(context["path"], context["inventories"])
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
            self.invalidate(f"Unable to load destination progress: {error}")

        self.run_async(work, success, failure)

    def render(self):
        order, activity = self.snapshot["order"], self.snapshot["activity"] or {}
        self.rows = resolve_progress(order, activity, self._settings["selected_instances"])
        if not order["orders"]:
            status = "No planned ROM destinations. Review the 2WP row audit and Nearest Crusher values."
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
        self.fill(self.table, summaries, [("rom_area", "ROM area"), ("material_type", "Material type"), ("detected", "Detected destination"), ("current", "Current build instance"), ("previous", "Previous"), ("next", "Next"), ("remaining", "Remaining (ROM WMT)"), ("basis", "Selection basis")])
        self.capacity_fields = {}
        for i, row in enumerate(self.rows):
            combo = QComboBox()
            combo.setMinimumWidth(combo.fontMetrics().horizontalAdvance("Automatic / review required") + 45)
            combo.addItem("Automatic / review required", "")
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
                field.setText(str(value) if value is not None else "")
                field.textEdited.connect(lambda text, key=key, field=field: self.edit_capacity(key, field, text))
                field.editingFinished.connect(lambda key=key, field=field: self.finish_capacity(key, field))
                self.capacity_fields.setdefault(key, []).append(field)
            self.table.setCellWidget(i, 6, field)
        self.table.resizeRowsToContents()
        self.fill(self.order_table, order["orders"], [("rom_area", "ROM area"), ("material_type", "Material type"), ("order_position", "Order"), ("destination", "Destination"), ("build_instance", "Build instance"), ("first_inbound", "First planned inbound (AWST)"), ("last_inbound", "Last planned inbound (AWST)"), ("planned_wmt", "Planned ROM WMT"), ("csv_records", "CSV records")])
        self.fill(self.activity_table, activity.get("records", []), [("rom_area", "ROM area"), ("material_type", "Material type"), ("destination", "Destination"), ("observed_at", "Inbound time (AWST)"), ("wmt", "ROM WMT"), ("source_block", "Source grade block"), ("destination_build", "Actual destination build"), ("movement_id", "Movement ID")])
        self.fill(self.audit_table, order["audit"], [("csv_record", "CSV record"), ("outcome", "Outcome"), ("reason", "Reason"), ("rom_area", "ROM area"), ("material_type", "Material type"), ("destination", "Destination"), ("build_instance", "Build instance"), ("order_position", "Order"), ("planned_wmt", "ROM WMT"), ("start", "Start (AWST)"), ("source", "2WP source")])
        if self.rows:
            self.table.selectRow(0)
        self.show_details()

    def choose_instance(self, key, value):
        if value:
            self._settings["selected_instances"][key] = value
        else:
            self._settings["selected_instances"].pop(key, None)
        self.emit_settings()
        self.render()

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
        lines.extend(notes)
        self.details.setPlainText("\n\n".join(lines))
