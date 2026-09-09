"""Native quality audit viewer for the current optimised or manual plan."""

from contextlib import closing
import csv
import sqlite3

import pandas as pd
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
                             QPushButton, QTableWidget, QTableWidgetItem, QAbstractItemView, QFileDialog, QMessageBox)
from classes.ProductQualityReport import quality_report_rows
from classes.ProductTargetModes import EVALUATION_LABELS


class ProductQualityReview(QDialog):
    FIELDS = (
        ("steady_state_number", "Steady\nstate"), ("build_name", "Build"), ("opf", "OPF"), ("brand", "Brand"),
        ("lane", "By-product"), ("analyte", "Analyte"), ("target_mode", "Target\nmode"),
        ("actual_grade", "Actual grade\n(%)"), ("target", "Target\n(%)"), ("lql", "LQL\n(%)"), ("hql", "HQL\n(%)"),
        ("target_deviation", "Target deviation\n(pp)"), ("below_lql", "Below LQL\n(pp)"), ("above_hql", "Above HQL\n(pp)"),
        ("limit_mode", "LQL/HQL\nmode"), ("quality_status", "Status"), ("grade_weight_tonnes", "Grade-weight\ntonnes"),
        ("target_penalty", "Target\npenalty"), ("limit_penalty", "Limit\npenalty"), ("opening_penalty", "Opening\npenalty"),
        ("applied_penalty", "Applied grade\npenalty"), ("source_closeness_score", "Source closeness\n(0–1)"),
        ("source_dispersion_score", "Source\ndispersion"), ("applied_similarity_penalty", "Applied similarity\npenalty"),
        ("lower_bound", "Active lower\nbound (%)"), ("upper_bound", "Active upper\nbound (%)"),
        ("hard_limits_satisfied", "Hard limits satisfied"), ("evaluation_basis", "Evaluation basis"), ("start_datetime", "Start"),
    )

    def __init__(self, database_path=None, parent=None):
        super().__init__(parent)
        self.database_path = database_path
        self.report = pd.DataFrame()
        self.setWindowTitle("Product Quality Results")
        self.resize(1440, 750)
        layout = QVBoxLayout(self)
        title = QLabel("Product Quality Results")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #172033;")
        layout.addWidget(title)
        description = QLabel("Review the saved plan at either grain. Applied penalties appear only at the build's selected evaluation basis. A negative applied penalty is an improvement or a reward. Penalties are objective units; they are not financial forecasts. Hard mode uses Min/Max; LQL/Target/HQL remain reference values.")
        description.setWordWrap(True)
        layout.addWidget(description)
        controls = QHBoxLayout()
        self.plan = QComboBox()
        self.plan.addItem("Current optimised plan", "optimised_blend_report")
        self.plan.addItem("Current manual plan", "manual_blend_report")
        self.grain = QComboBox()
        for value, label in EVALUATION_LABELS.items():
            self.grain.addItem(label, value)
        self.analyte = QComboBox()
        self.analyte.addItem("All analytes", None)
        for a in ("fe", "si", "al", "p", "mn"):
            self.analyte.addItem(a.title(), a)
        self.columns = QComboBox()
        self.columns.addItems(["Grades and limits", "Penalties and similarity", "All fields"])
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter build, OPF or brand")
        for widget in (self.plan, self.grain, self.analyte, self.columns, self.search):
            if isinstance(widget, QComboBox):
                widget.setMinimumWidth(max(widget.fontMetrics().horizontalAdvance(widget.itemText(i)) for i in range(widget.count())) + 45)
            controls.addWidget(widget)
        self.refresh = QPushButton("Refresh")
        self.export = QPushButton("Export CSV")
        controls.addWidget(self.refresh)
        controls.addWidget(self.export)
        layout.addLayout(controls)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, len(self.FIELDS))
        self.table.setHorizontalHeaderLabels([label for _, label in self.FIELDS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        layout.addWidget(self.table)
        self.grain.currentIndexChanged.connect(self.render)
        self.analyte.currentIndexChanged.connect(self.render)
        self.columns.currentIndexChanged.connect(self.render)
        self.search.textChanged.connect(self.render)
        self.plan.currentIndexChanged.connect(self.reload)
        self.refresh.clicked.connect(self.reload)
        self.export.clicked.connect(self.export_csv)
        self.reload()

    def reload(self, *_):
        if self.database_path is None:
            self.render()
            return
        path = self.database_path() if callable(self.database_path) else self.database_path
        try:
            table = self.plan.currentData()  # fixed allowlist from this widget
            with closing(sqlite3.connect(f"file:{str(path).replace(chr(92), '/')}?mode=ro", uri=True)) as connection:
                available = [r[1] for r in connection.execute(f'PRAGMA table_info("{table}")')]
                columns = [c for c in available if c.endswith("quality_audit") or c in {
                    "steady_state_number", "start_datetime", "blend_ID", "blend_option", "plan_id", "scenario_id"}]
                self.report = pd.read_sql_query('SELECT ' + ','.join('"' + c.replace('"', '""') + '"' for c in columns)
                                               + f' FROM "{table}"', connection) if columns else pd.DataFrame()
            self.render()
        except (sqlite3.Error, pd.errors.DatabaseError) as exc:
            self.report = pd.DataFrame()
            self.render()
            self.status.setText(f"Quality results could not be loaded: {exc}")

    def set_report(self, report):
        self.report = report.copy()
        self.render()

    def render(self, *_):
        rows = quality_report_rows(self.report, grain=self.grain.currentData(), analyte=self.analyte.currentData())
        term = self.search.text().strip().lower()
        self.rows = [r for r in rows if not term or term in " ".join(str(r.get(k, "")) for k in ("build_name", "opf", "brand")).lower()]
        self.table.setRowCount(len(self.rows))
        for i, row in enumerate(self.rows):
            for j, (key, _) in enumerate(self.FIELDS):
                value = row.get(key)
                if value is None:
                    text = ""
                elif isinstance(value, bool):
                    text = "Yes" if value else "No"
                elif isinstance(value, (int, float)):
                    text = f"{value:,.6g}"
                elif key == "evaluation_basis":
                    text = EVALUATION_LABELS.get(value, value)
                elif key in {"analyte", "target_mode", "limit_mode", "lane"}:
                    text = str(value).title()
                else:
                    text = str(value).replace("_", " ")
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, value)
                item.setToolTip("" if value is None else str(value))
                self.table.setItem(i, j, item)
        self.table.resizeColumnsToContents()
        overview = {"steady_state_number", "build_name", "opf", "brand", "lane", "analyte", "target_mode", "actual_grade",
                    "target", "lql", "hql", "target_deviation", "below_lql", "above_hql", "limit_mode", "quality_status"}
        penalties = {"steady_state_number", "build_name", "opf", "analyte", "target_mode", "grade_weight_tonnes", "target_penalty",
                     "limit_penalty", "opening_penalty", "applied_penalty", "source_closeness_score", "source_dispersion_score", "applied_similarity_penalty"}
        visible = overview if self.columns.currentIndex() == 0 else penalties if self.columns.currentIndex() == 1 else {k for k, _ in self.FIELDS}
        for column, (key, _) in enumerate(self.FIELDS):
            self.table.setColumnHidden(column, key not in visible)
        self.export.setEnabled(bool(self.rows))
        if not self.rows:
            self.status.setText("No quality audit rows for this selection. Run or evaluate a plan with Product Targets, then refresh. Older saved reports need a fresh run to include these diagnostics.")
        else:
            breaches = sum(r["quality_status"] in {"Soft limit breached", "Hard limit breached"} for r in self.rows)
            penalty = sum(r.get("applied_penalty") or 0 for r in self.rows)
            similarity = sum(r.get("applied_similarity_penalty") or 0 for r in self.rows)
            self.status.setText(f"{len(self.rows)} analyte rows · {breaches} limit breaches · Applied grade penalty {penalty:,.3f} · Applied similarity penalty {similarity:,.3f}")

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Product Quality Results", "product_quality_results.csv", "CSV (*.csv)")
        if path:
            try:
                with open(path, "w", newline="", encoding="utf-8-sig") as handle:
                    writer = csv.DictWriter(handle, fieldnames=[key for key, _ in self.FIELDS], extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(self.rows)
            except OSError as exc:
                QMessageBox.warning(self, "Export failed", str(exc))
