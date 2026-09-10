"""Read-only review of saved destination assignments; no plan recalculation."""

import json

import pandas as pd
from PyQt5 import sip
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                            QLineEdit, QPushButton, QProgressBar, QTabWidget, QTableView,
                            QAbstractItemView, QHeaderView, QPlainTextEdit, QFileDialog)

from classes.DestinationPlanReport import read_publication
from GUI.DestinationProgressSetup import EvidenceModel


ASSIGNMENT_COLUMNS = [("grade_block", "Grade block"), ("status", "Status"),
    ("assigned_destination", "Assigned destination"), ("reported_wmt", "ROM WMT"),
    ("primary_destination", "Primary"), ("fallback_1_destination", "Fallback 1"),
    ("fallback_2_destination", "Fallback 2"), ("order_position", "2WP order"),
    ("build_instance", "Build instance"), ("consumed_capacity_wmt", "Capacity consumed WMT"),
    ("overrun_wmt", "Overrun WMT")]
VIEWS = [
    ("Assignments", "material_destination_plan", ASSIGNMENT_COLUMNS),
    ("Payload audit", "material_destination_plan_payloads", [("payload_id", "Payload"), ("delivered_datetime", "Delivered (AWST)")] + ASSIGNMENT_COLUMNS),
    ("Capacity balances", "destination_capacity_balances", [("destination", "Destination"), ("build_instance", "Build instance"), ("rom_area", "ROM area"),
        ("starting_wmt", "Starting WMT"), ("consumed_wmt", "Consumed WMT"), ("remaining_wmt", "Remaining WMT"), ("overrun_wmt", "Overrun WMT"), ("capacity_basis", "Capacity basis")]),
    ("Transitions", "destination_capacity_ledger", [("event_number", "Event"), ("payload_id", "Payload"), ("delivered_datetime", "Delivered (AWST)"),
        ("destination", "From destination"), ("build_instance", "Build instance"), ("next_destination", "Next destination"), ("reason", "Reason")]),
    ("Actual movements", "material_destination_plan_activity", [("observed_at", "Observed (AWST)"), ("source_block", "Grade block"),
        ("destination", "Destination"), ("rom_area", "ROM area"), ("material_type", "Material type"), ("wmt", "ROM WMT")]),
]


def display(value):
    return "—" if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)) else str(value)


class PlanModel(EvidenceModel):
    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and role in (Qt.DisplayRole, Qt.ToolTipRole):
            key = self.columns[index.column()][0]
            value = self.records[index.row()].get(key)
            if role == Qt.DisplayRole and value is not None and not pd.isna(value):
                if key.endswith("_wmt") or key == "wmt":
                    return f"{float(value):,.1f}"
                if key in {"build_instance", "order_position", "event_number"}:
                    return str(int(value))
            return display(value)
        return super().data(index, role)


class MaterialDestinationPlanView(QWidget):
    def __init__(self, parent=None, *, run_async, loader=read_publication):
        super().__init__(parent)
        self.run_async, self.loader = run_async, loader
        self.context = None
        self.generation = 0
        self.pending = False
        self.snapshot = {}
        layout = QVBoxLayout(self)
        title = QLabel("Material Destination Plan")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #172033;")
        layout.addWidget(title)
        intro = QLabel("Primary is the ROM destination for non-direct-tipped material. Fallbacks are alternatives and add no assigned tonnes. Recalculate the plan to apply changed reconciliation inputs.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        controls = QHBoxLayout()
        self.plan = QComboBox()
        self.plan.setMinimumWidth(235)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter grade blocks, destinations or status…")
        self.refresh = QPushButton("Reload saved results")
        self.export = QPushButton("Export current CSV…")
        for widget in (QLabel("Plan"), self.plan, self.search, self.refresh, self.export):
            controls.addWidget(widget)
        layout.addLayout(controls)
        self.status = QLabel("No saved destination plan loaded.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.tabs = QTabWidget()
        self.tables = []
        for label, _, _ in VIEWS:
            table = QTableView()
            table.setModel(PlanModel(table))
            table.setAlternatingRowColors(True)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.setSelectionMode(QAbstractItemView.SingleSelection)
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.verticalHeader().hide()
            table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
            table.horizontalHeader().setStretchLastSection(True)
            table.selectionModel().selectionChanged.connect(self.show_details)
            self.tables.append(table)
            self.tabs.addTab(table, label)
        layout.addWidget(self.tabs, 3)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(225)
        self.details.setMaximumHeight(300)
        self.details.setPlaceholderText("Select an assignment to inspect its capacity, actual-movement evidence and fallback rules.")
        layout.addWidget(self.details, 1)
        self.plan.currentIndexChanged.connect(self.render)
        self.search.textChanged.connect(self.render)
        self.tabs.currentChanged.connect(self.show_details)
        self.refresh.clicked.connect(self.request_refresh)
        self.export.clicked.connect(self.export_csv)

    def set_context(self, database_path, scenario_id):
        key = (str(database_path), str(scenario_id))
        if key != self.context:
            self.context = key
            self.generation += 1
            self.pending = False
            self.progress.hide()
            self.refresh.setEnabled(True)
            self.snapshot = {}
            self.plan.clear()
            self.search.clear()
            self.render()

    def request_refresh(self):
        if self.pending or not self.context:
            return
        self.pending = True
        self.progress.show()
        self.status.setText("Loading saved destination plan…")
        self.refresh.setEnabled(False)
        self.export.setEnabled(False)
        generation = self.generation
        path = self.context[0]
        def finished(snapshot=None, error=None):
            if sip.isdeleted(self) or generation != self.generation:
                return
            self.pending = False
            self.progress.hide()
            self.refresh.setEnabled(True)
            if error:
                self.status.setText(f"Unable to reload: {error}. " + ("Previously loaded saved results remain visible." if self.snapshot else "No results available."))
                self.export.setEnabled(bool(self.snapshot))
                return
            self.snapshot = snapshot or {}
            selected = self.plan.currentData()
            owners = {(r.get("plan_type"), r.get("plan_id")) for name in ("destination_allocation_runs", "material_destination_plan") for r in self.snapshot.get(name, [])}
            self.plan.blockSignals(True)
            self.plan.clear()
            for kind, plan_id in sorted(owners, key=lambda p: (str(p[0]), str(p[1]))):
                self.plan.addItem(f"{str(kind).title()} · {plan_id}", (kind, plan_id))
            index = next((i for i in range(self.plan.count()) if self.plan.itemData(i) == selected), -1)
            if index >= 0:
                self.plan.setCurrentIndex(index)
            self.plan.blockSignals(False)
            self.render()
        self.run_async(lambda: self.loader(path), lambda result: finished(result), lambda error: finished(error=error))

    def rows(self, table_name):
        owner = self.plan.currentData()
        return [r for r in self.snapshot.get(table_name, []) if (r.get("plan_type"), r.get("plan_id")) == owner]

    def render(self):
        query = self.search.text().casefold()
        for table, (_, name, columns) in zip(self.tables, VIEWS):
            rows = self.rows(name)
            if name == "destination_capacity_ledger":
                rows = [r for r in rows if r.get("event") == "Advance"]
            if query:
                rows = [r for r in rows if query in " ".join(display(r.get(k)) for k, _ in columns).casefold()]
            table.model().replace(rows, columns)
            for index, (key, _) in enumerate(columns):
                table.setColumnWidth(index, 290 if key in {"grade_block", "source_block"} else 200 if "destination" in key or "datetime" in key or key == "observed_at" else 145)
        assignments = self.rows("material_destination_plan")
        runs = self.rows("destination_allocation_runs")
        run = runs[0] if runs else {}
        if not assignments:
            self.status.setText("No published destination rows for this plan. " + display(run.get("reason", "Run a plan to create its destination report.")))
        else:
            tonnes = lambda key: sum(float(r.get(key) or 0) for r in assignments)
            self.status.setText(f"Saved result · {run.get('status') or assignments[0].get('status') or 'Legacy snapshot — recalculate'} · "
                f"Assigned {tonnes('assigned_tonnes'):,.1f} WMT · Unresolved {tonnes('unresolved_wmt'):,.1f} WMT · "
                f"Outside finalised window {tonnes('outside_window_wmt'):,.1f} WMT · Overrun {tonnes('overrun_wmt'):,.1f} WMT\n"
                f"Actual movements: {display(run.get('activity_status'))} · fetched {display(run.get('activity_fetched_at'))} · "
                f"Window {display(run.get('activity_window_start'))} → {display(run.get('activity_window_end'))} (AWST)")
        self.export.setEnabled(bool(assignments) and not self.pending)
        self.details.clear()

    def show_details(self, *_):
        table = self.tables[self.tabs.currentIndex()]
        indexes = table.selectionModel().selectedRows()
        if not indexes:
            self.details.clear()
            return
        if indexes[0].row() >= len(table.model().records):
            return
        row = table.model().records[indexes[0].row()]
        if self.tabs.currentIndex() >= 2:
            self.details.setPlainText("\n".join(f"{label}: {display(row.get(key))}" for key, label in VIEWS[self.tabs.currentIndex()][2]))
            return
        lines = [f"{display(row.get('grade_block'))} · {display(row.get('status'))}", display(row.get("reason")),
                 f"Build instance {display(row.get('build_instance'))} · 2WP order {display(row.get('order_position'))} · {display(row.get('selection_basis'))}",
                 f"Capacity WMT — starting: {display(row.get('starting_capacity_wmt'))}; before first payload: {display(row.get('capacity_before_wmt'))}; consumed by this row: {display(row.get('consumed_capacity_wmt'))}; after last payload: {display(row.get('capacity_after_wmt'))}. Shared capacity also changes with other sources.",
                 f"Actual movements — detected {display(row.get('detected_destination'))}; latest {display(row.get('latest_inbound'))}; {display(row.get('activity_rows'))} movements, {display(row.get('activity_wmt'))} WMT.",
                 f"Fallback 1: {display(row.get('fallback_1_destination'))} — {display(row.get('fallback_1_rule'))}",
                 f"Fallback 2: {display(row.get('fallback_2_destination'))} — {display(row.get('fallback_2_rule'))}"]
        try:
            trace = json.loads(row.get("destination_rule_trace") or "{}")
            for candidate in trace.get("candidates", []):
                if "cycle_time_minutes" in candidate:
                    lines.append(f"Haul route: {candidate.get('source_node')} → {candidate.get('destination_node')} · {candidate['cycle_time_minutes']:.3f} minutes")
            if trace.get("reason"):
                lines.append(trace["reason"])
        except (ValueError, TypeError):
            pass
        self.details.setPlainText("\n".join(lines))

    def export_csv(self):
        table = self.tables[self.tabs.currentIndex()]
        if not table.model().records:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Material Destination Plan", "material_destination_plan.csv", "CSV (*.csv)")
        if path:
            try:
                pd.DataFrame(table.model().records).to_csv(path if path.lower().endswith(".csv") else path + ".csv", index=False)
            except (OSError, ValueError) as error:
                self.status.setText(f"Unable to export: {error}")
