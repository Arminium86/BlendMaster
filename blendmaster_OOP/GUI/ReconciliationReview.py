"""Native Data Streams controls and evidence review, with no database I/O."""

from copy import deepcopy
import csv

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QDoubleValidator
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout, QLabel,
    QComboBox, QSpinBox, QPushButton, QLineEdit, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QTableWidget, QTableWidgetItem, QHeaderView,
    QPlainTextEdit, QCheckBox, QAbstractItemView, QFileDialog,
)

from classes.GradeStreams import ANALYTES, is_dry_plant, normalise_opf
from classes.ReconciliationControls import (
    normalise_reconciliation_settings, spatial_cell, resolution_levels, reconciliation_report_rows, confidence_search_labels,
)

METHODS = (("Standard · global factors", "standard"),
           ("Advanced · lookback window", "lookback"),
           ("Advanced · spatial and compositional", "spatial_compositional"),
           ("Auto · maximise confidence", "auto_max_confidence"))
WINDOWS = (("Trailing calendar days", "calendar_days"),
           ("Last N production days", "production_days"),
           ("Last N days of latest campaign", "latest_campaign"))
ANALYTE_LABELS = ("Fe", "SiO₂", "Al₂O₃", "P", "Mn")
LEVEL_LABELS = {"pit_stage_bench_blast_flitch_material_type": "Flitch + material",
                "pit_stage_bench_blast_material_type": "Blast + material",
                "pit_stage_bench_material_type": "Bench + material",
                "pit_stage_material_type": "Stage + material", "pit_material_type": "Pit + material",
                "global": "Global"}


def percent(value):
    if value is None:
        return "Unscored"
    if 0 < value < .1:
        return "<0.1%"
    if 99.9 < value < 100:
        return ">99.9%"
    return f"{value:.1f}%"


def factor(value):
    return "—" if value is None else f"{value:.6g}"


def quantity(value, decimals=1):
    return "—" if value is None else f"{value:,.{decimals}f}"


def levels(detail):
    return ", ".join(LEVEL_LABELS.get(level, level) for level in resolution_levels(detail)) or "No positive WMT"


class ReconciliationReview(QWidget):
    settingsChanged = pyqtSignal(dict)
    reviewRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._settings = normalise_reconciliation_settings()
        self._opf, self._brands, self._records, self._cells = "", [], {}, []
        self._evidence_rows = []
        self._record_options = {}
        self._report_rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel("Reconciliation method and evidence")
        heading.setStyleSheet("font-size: 17px; font-weight: 700; color: #1f2933;")
        layout.addWidget(heading)
        defaults = QGridLayout()
        self.method = self.combo(METHODS)
        defaults.addWidget(QLabel("Method"), 0, 0)
        defaults.addWidget(self.method, 0, 1, 1, 3)
        self.window = self.combo(WINDOWS)
        self.days, self.minimum, self.maximum = self.spin(7), self.spin(1), self.spin(30)
        defaults.addWidget(QLabel("Default window"), 1, 0)
        defaults.addWidget(self.window, 1, 1)
        defaults.addWidget(QLabel("N days"), 1, 2)
        defaults.addWidget(self.days, 1, 3)
        defaults.addWidget(QLabel("Minimum production days"), 2, 0)
        defaults.addWidget(self.minimum, 2, 1)
        defaults.addWidget(QLabel("Maximum lookback (calendar days)"), 2, 2)
        defaults.addWidget(self.maximum, 2, 3)
        defaults.setColumnStretch(1, 1)
        layout.addLayout(defaults)
        self.help = QLabel()
        self.help.setWordWrap(True)
        self.help.setStyleSheet("color: #526474;")
        layout.addWidget(self.help)
        review_row = QHBoxLayout()
        self.review_button = QPushButton("Calculate review")
        self.review_button.clicked.connect(self.reviewRequested)
        review_row.addWidget(self.review_button)
        self.export_button = QPushButton("Export review CSV…")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_review)
        review_row.addWidget(self.export_button)
        self.status = QLabel("Standard global factors are active.")
        self.status.setWordWrap(True)
        review_row.addWidget(self.status, 1)
        layout.addLayout(review_row)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("background: #edf5fa; color: #183b56; padding: 10px;")
        layout.addWidget(self.summary)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.build_source_tab()
        self.build_local_tab()
        self.method.currentIndexChanged.connect(self.defaults_changed)
        self.window.currentIndexChanged.connect(self.defaults_changed)
        for widget in (self.days, self.minimum, self.maximum):
            widget.valueChanged.connect(self.defaults_changed)
        self.set_context({}, "", [])

    @staticmethod
    def combo(items):
        widget = QComboBox()
        for label, value in items:
            widget.addItem(label, value)
        return widget

    @staticmethod
    def spin(value):
        widget = QSpinBox()
        widget.setRange(1, 36500)
        widget.setValue(value)
        return widget

    def build_source_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.source_filter = QLineEdit()
        self.source_filter.setPlaceholderText("Filter sources, hexes, grade blocks, brands or fallback levels…")
        self.source_filter.textChanged.connect(self.filter_sources)
        layout.addWidget(self.source_filter)
        self.sources = QTreeWidget()
        self.sources.setHeaderLabels(["Source / component", "Brand", "WMT / share", "Confidence",
                                      "Uncertainty", "Global evidence", "Lineage", "Fallback level", "Selected window"])
        self.sources.setMinimumHeight(240)
        self.sources.setColumnWidth(0, 280)
        for column in range(1, 8):
            self.sources.setColumnWidth(column, 100 if column != 7 else 160)
        self.sources.setColumnWidth(8, 190)
        self.sources.currentItemChanged.connect(self.show_evidence)
        layout.addWidget(self.sources)
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
        self.evidence.setPlaceholderText("Select a source or component to review factors, supporting history and fallback reasons.")
        self.evidence.setMinimumHeight(150)
        self.evidence.setMaximumHeight(185)
        layout.addWidget(self.evidence)
        self.edit_component = QPushButton("Open selected component in local settings")
        self.edit_component.setEnabled(False)
        self.edit_component.clicked.connect(self.open_component)
        layout.addWidget(self.edit_component)
        self.tabs.addTab(page, "Sources and confidence")

    def build_local_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("Local settings apply to the selected OPF, brand, pit, stage, bench, blast, flitch, material type and analyte. "
                      "Both factor kinds share the accepted fallback level. Blank factors use the automatic result.")
        note.setWordWrap(True)
        layout.addWidget(note)
        selectors = QFormLayout()
        self.scope = QLabel()
        selectors.addRow("OPF", self.scope)
        self.brand = QComboBox()
        selectors.addRow("Brand", self.brand)
        self.cell_filter = QLineEdit()
        self.cell_filter.setPlaceholderText("Filter pit, stage, bench, blast, flitch or material…")
        selectors.addRow("Find spatial cell", self.cell_filter)
        self.cell = QComboBox()
        self.cell.setMinimumContentsLength(25)
        self.cell.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        selectors.addRow("Pit | Stage | Bench | Blast | Flitch | Material", self.cell)
        self.source_context = QComboBox()
        self.source_context.setMinimumContentsLength(28)
        self.source_context.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.source_context.setToolTip("Automatic factors can differ by source composition in Auto mode. Local edits apply to this cell across all sources.")
        selectors.addRow("Review source / hex", self.source_context)
        self.source_context.currentIndexChanged.connect(self.populate_matrix_values)
        layout.addLayout(selectors)
        self.matrix = QTableWidget(5, 9)
        self.matrix.setHorizontalHeaderLabels(["Analyte", "Auto blend", "Local blend", "Auto regression",
                                               "Local regression", "Window", "N", "Min days", "Max days"])
        self.matrix.verticalHeader().hide()
        self.matrix.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.matrix.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.matrix.setSelectionMode(QAbstractItemView.SingleSelection)
        self.matrix.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.matrix.setFixedHeight(185)
        layout.addWidget(self.matrix)
        editor = QGridLayout()
        self.analyte = self.combo(tuple(zip(ANALYTE_LABELS, ANALYTES)))
        self.blend, self.regression = QLineEdit(), QLineEdit()
        for field in (self.blend, self.regression):
            field.setValidator(QDoubleValidator(field))
            field.setPlaceholderText("Automatic")
        editor.addWidget(QLabel("Edit analyte"), 0, 0)
        editor.addWidget(self.analyte, 0, 1)
        editor.addWidget(QLabel("Local blend factor"), 0, 2)
        editor.addWidget(self.blend, 0, 3)
        editor.addWidget(QLabel("Local regression factor"), 0, 4)
        editor.addWidget(self.regression, 0, 5)
        self.custom_window = QCheckBox("Set a local window for this analyte")
        editor.addWidget(self.custom_window, 1, 0, 1, 6)
        self.local_window = self.combo(WINDOWS)
        self.local_days, self.local_minimum, self.local_maximum = self.spin(7), self.spin(1), self.spin(30)
        editor.addWidget(self.local_window, 2, 0, 1, 3)
        for column, label, widget in ((3, "N days", self.local_days), (4, "Min production days", self.local_minimum),
                                      (5, "Max calendar days", self.local_maximum)):
            block = QVBoxLayout()
            block.addWidget(QLabel(label))
            block.addWidget(widget)
            editor.addLayout(block, 2, column)
        layout.addLayout(editor)
        actions = QHBoxLayout()
        self.save_cell = QPushButton("Save local settings")
        self.reset_cell = QPushButton("Use inherited settings")
        actions.addWidget(self.save_cell)
        actions.addWidget(self.reset_cell)
        actions.addStretch()
        layout.addLayout(actions)
        self.local_status = QLabel()
        self.local_status.setWordWrap(True)
        self.local_status.setStyleSheet("color: #92400e;")
        layout.addWidget(self.local_status)
        self.tabs.addTab(page, "Local factors and windows")
        self.brand.currentIndexChanged.connect(self.populate_cells)
        self.cell_filter.textChanged.connect(self.populate_cells)
        self.cell.currentIndexChanged.connect(self.populate_matrix)
        self.analyte.currentIndexChanged.connect(self.load_editor)
        self.matrix.cellClicked.connect(lambda row, _col: self.analyte.setCurrentIndex(row))
        self.custom_window.toggled.connect(self.update_local_window)
        self.save_cell.clicked.connect(self.save_local)
        self.reset_cell.clicked.connect(lambda: self.save_local(reset=True))

    def settings(self):
        return deepcopy(self._settings)

    def set_context(self, settings, opf, brands):
        self._loading = True
        self._settings = normalise_reconciliation_settings(settings)
        self._opf, self._brands = normalise_opf(opf), list(brands)
        self.scope.setText(self._opf.replace("_", " ") or "Select an OPF in Site Configuration")
        self.method.setCurrentIndex(self.method.findData(self._settings["method"]))
        self.window.setCurrentIndex(self.window.findData(self._settings["window_mode"]))
        for widget, name in ((self.days, "lookback_days"), (self.minimum, "min_production_days"),
                              (self.maximum, "max_lookback_days")):
            widget.setMaximum(max(36500, self._settings[name]))
            widget.setValue(self._settings[name])
        current_brand = self.brand.currentText()
        self.brand.clear()
        self.brand.addItems(self._brands)
        if current_brand in self._brands:
            self.brand.setCurrentText(current_brand)
        self._records = {}
        self._record_options = {}
        self._cells = []
        self.sources.clear()
        self._evidence_rows = []
        self.evidence.clear()
        self.populate_cells()
        self._loading = False
        self.update_mode()
        self.mark_stale()

    def defaults_changed(self, *_):
        if self._loading:
            return
        self._settings.update(method=self.method.currentData(), window_mode=self.window.currentData(),
                              lookback_days=self.days.value(), min_production_days=self.minimum.value(),
                              max_lookback_days=self.maximum.value())
        self.update_mode()
        self.populate_matrix()
        self.mark_stale()
        self.settingsChanged.emit(self.settings())

    def update_mode(self):
        advanced = self._settings["method"] != "standard"
        lookback = self._settings["method"] == "lookback"
        self.window.setEnabled(lookback)
        self.days.setEnabled(lookback)
        self.minimum.setEnabled(advanced)
        self.maximum.setEnabled(advanced)
        self.tabs.setVisible(advanced)
        self.export_button.setVisible(advanced)
        self.summary.setVisible(advanced)
        self.sources.setColumnHidden(8, self._settings["method"] != "auto_max_confidence")
        self.help.setText(
            "Standard uses the existing global 7/14/21/28/30-day search. Local settings are retained for advanced mode."
            if not advanced else
            "Defaults can be refined by cell and analyte in Local factors and windows. All windows end before scenario start. "
            "Latest campaign means consecutive production dates; a date gap ends the campaign. "
            "Confidence measures composition and spatial-address overlap; uncertainty is its complement, not a statistical interval.")
        if self._settings["method"] == "auto_max_confidence":
            self.help.setText("Auto compares spatial horizons and all three lookback options within the minimum production days and maximum lookback. "
                              "It chooses one policy per inventory stockpile or AMT hex and brand. Local minimum/maximum guardrails and manual factors are retained; N is chosen automatically. "
                              "Confidence measures evidence similarity, not a statistical probability.")
        self.custom_window.setText("Set local guardrails for this analyte" if self._settings["method"] == "auto_max_confidence"
                                   else "Set a local window for this analyte")
        self.update_local_window()

    def mark_stale(self, message=None):
        advanced = self._settings["method"] != "standard"
        self.status.setText(message or ("Settings changed. Calculate review before submitting." if advanced
                                      else "Standard global factors are active."))
        self._report_rows = []
        self.export_button.setEnabled(False)
        self.summary.setText("Review pending for the current settings.")
        self.sources.clear()
        self._evidence_rows = []
        self.evidence.clear()
        self._records = {}
        self._record_options = {}
        self.populate_matrix()

    def set_busy(self, busy):
        self.review_button.setEnabled(not busy)
        if busy:
            self.mark_stale("Loading history and calculating evidence…")

    def set_review(self, audits, overall, warnings=()):
        self.sources.clear()
        self._evidence_rows = []
        self._records, self._cells = {}, []
        self._record_options = {}
        self._report_rows = reconciliation_report_rows(audits, overall)
        for row in self._report_rows:
            row["method"] = self._settings["method"]
        if self._report_rows:
            self._report_rows[0]["warnings"] = "; ".join(dict.fromkeys([*overall.get("warnings", []), *warnings]))
        self.export_button.setEnabled(bool(self._report_rows))
        def attach(node, audit, brand, record):
            # Store small indices in Qt; copying nested audit dictionaries into
            # every QVariant would multiply the history held by large sources.
            node.setData(0, Qt.UserRole, len(self._evidence_rows))
            self._evidence_rows.append((audit, brand, record))
        def add(parent, audit):
            for brand, detail in audit.get("by_brand", {}).items():
                name = str(audit.get("review_label") or audit.get("hex_id") or audit.get("source_id") or "Source")
                node = QTreeWidgetItem([name, brand, f"{audit.get('source_wmt', 0):,.1f}",
                    percent(detail.get("confidence_percent")), percent(detail.get("uncertainty_percent")),
                    percent(100 * detail.get("global_fraction", 0)), percent(100 * detail.get("lineage_coverage", 0)), levels(detail),
                    "; ".join(confidence_search_labels(detail))])
                (parent.addChild if parent else self.sources.addTopLevelItem)(node)
                attach(node, audit, brand, None)
                for record in detail.get("records", []):
                    child = QTreeWidgetItem([record.get("grade_block_key") or "Unknown lineage · global fallback", brand,
                        percent(100 * record.get("lineage_fraction", 0)), percent(record.get("confidence_percent")),
                        percent(record.get("uncertainty_percent")), "", "",
                        LEVEL_LABELS.get(record.get("resolution_level"), record.get("resolution_level", "")) +
                        (" · manual edit" if record.get("manual_override") else ""), "; ".join(confidence_search_labels(detail))])
                    attach(child, audit, brand, record)
                    node.addChild(child)
                    cell = spatial_cell(record.get("grade_block_key"))
                    if cell:
                        self._records[(brand, cell)] = record
                        self._record_options.setdefault((brand, cell), []).append((
                            f"{audit.get('source_id', '')} / {audit.get('hex_id') or 'inventory'} / {record.get('grade_block_key')}", record))
                        self._cells.append((brand, cell))
                for member in audit.get("review_children", []):
                    # Child audit has the same brand set as its parent.
                    add(node, {**member, "by_brand": {brand: member["by_brand"][brand]}})
        for audit in audits:
            add(None, audit)
        summaries = []
        for brand, detail in overall.get("by_brand", {}).items():
            summaries.append(f"{brand}: confidence {percent(detail.get('confidence_percent'))} · "
                             f"uncertainty {percent(detail.get('uncertainty_percent'))} · "
                             f"global evidence {percent(100 * detail.get('global_fraction', 0))} · "
                             f"lineage {percent(100 * detail.get('lineage_coverage', 0))} · "
                             f"manual edits {percent(100 * detail.get('manual_override_fraction', 0))}")
            search = detail.get("auto_selection")
            if search:
                gain = search.get("improvement_percent")
                summaries.append(f"Auto: full-window baseline {percent(search.get('baseline_confidence_percent'))} · "
                                 f"improvement {quantity(gain, 2)} percentage points")
        self.summary.setText("Overall · physical WMT weighted\n" + "\n".join(summaries) if summaries else
                             "No positive adjusted sources are available for confidence review.")
        self.status.setText(("Standard global factors are ready. Submit applies these settings." if self._settings["method"] == "standard"
                            else "Review calculated. Submit applies these settings to the grade streams.") +
                            (" History or lineage warnings are shown below." if warnings else ""))
        self.populate_cells()
        self.filter_sources()

    def export_review(self):
        if not self._report_rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export reconciliation review", "reconciliation_review.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            fields = list(dict.fromkeys(key for row in self._report_rows for key in row))
            with open(path, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(self._report_rows)
        except OSError as exc:
            self.status.setText(f"Review export failed: {exc}")
            return
        self.status.setText(f"Review exported to {path}")

    def filter_sources(self, *_):
        query = self.source_filter.text().strip().lower()
        def visit(node, parent_match=False):
            match = parent_match or not query or query in " ".join(node.text(i) for i in range(self.sources.columnCount())).lower()
            child_matches = [visit(node.child(i), match) for i in range(node.childCount())]
            visible = match or any(child_matches)
            node.setHidden(not visible)
            if query and any(child_matches):
                node.setExpanded(True)
            return visible
        for i in range(self.sources.topLevelItemCount()):
            visit(self.sources.topLevelItem(i))

    def show_evidence(self, current, _previous=None):
        self.edit_component.setEnabled(False)
        if current is None:
            self.evidence.clear()
            return
        audit, brand, record = self._evidence_rows[current.data(0, Qt.UserRole)]
        detail = audit["by_brand"][brand]
        lines = [f"{audit.get('source_kind', '')} · {audit.get('source_id', '')} · {brand}"]
        search = detail.get("auto_selection")
        if search:
            lines += ["Auto selection: " + "; ".join(confidence_search_labels(detail)),
                      f"Full-window baseline: {percent(search.get('baseline_confidence_percent'))} · improvement: {quantity(search.get('improvement_percent'), 2)} percentage points"]
            if search.get("candidate_count") is not None:
                lines.append(f"Compared {search['candidate_count']} windows / {search['unique_evidence_count']} distinct period selections for this whole source.")
            for family, candidate in search.get("best_by_family", {}).items():
                label = dict((value, title) for title, value in WINDOWS).get(family, "Spatial and compositional")
                n = candidate["window_days"]
                lines.append(f"Best {label}: {n} {'day' if n == 1 else 'days'} · confidence {percent(candidate['confidence_percent'])}")
        if record:
            lines += [f"Component: {record.get('grade_block_key') or 'Unknown lineage'}",
                      f"Selected fallback: {LEVEL_LABELS.get(record['resolution_level'], record['resolution_level'])}",
                      "Matched address: " + (" | ".join(record.get("matched_spatial_key") or []) or "Global OPF / brand"),
                      f"Reason: {record.get('fallback_reason') or 'Most specific level has sufficient history.'}"]
            local = record.get("provenance", {}).get("local_override", {})
            for kind in ("blend", "regression"):
                lines.append(kind.title() + ": " + ", ".join(
                    f"{label} {factor(record[kind + '_factors'].get(a))}" for a, label in zip(ANALYTES, ANALYTE_LABELS)))
            if local:
                lines.append("Manual local edits: " + "; ".join(
                    f"{kind}/{a} {factor(v['automatic'])} → {factor(v['effective'])}"
                    for kind, values in local["factors"].items() for a, v in values.items()))
                lines.append(local["confidence_note"])
            elif record.get("manual_override"):
                lines.append("Includes edits to the supplied standard global factors.")
            lines.append(f"History: {quantity(record.get('source_rows'), 0)} rows · "
                         f"{quantity(record.get('source_feed_wmt'))} period feed WMT")
            for kind, values in record.get("provenance", {}).get("factor_history", {}).items():
                for a, value in values.items():
                    lines.append(f"{kind}/{a}: {value['production_days']} production dates · {value['period_count']} periods · "
                                 f"{quantity(value.get('feed_wmt'))} feed WMT · confidence {percent(value.get('confidence_percent'))}")
            for period in record.get("source_history", []):
                if period.get("period_start"):
                    lines.append(f"{period['period_start']} — {period['period_end']} AWST · {quantity(period.get('feed_wmt'))} feed WMT")
                elif period.get("source") == "standard_global":
                    lines.append(f"Global source brand: {period.get('source_brand')} · windows: {period.get('lookback_days')}")
            lines.extend(record.get("provenance", {}).get("history_warnings", []))
            self.edit_component.setEnabled(bool(spatial_cell(record.get("grade_block_key"))))
        else:
            lines += [f"Fallback levels: {levels(detail)}", "Expand this row for component evidence."]
            for kind, values in detail.get("applied_factors", {}).items():
                lines.append("Applied " + kind + ": " + ", ".join(f"{a} {factor(v)}" for a, v in values.items()))
        lines += audit.get("warnings", [])
        self.evidence.setPlainText("\n".join(lines))

    def populate_cells(self, *_):
        current = self.cell.currentData()
        query, brand = self.cell_filter.text().strip().lower(), self.brand.currentText()
        cells = {cell for b, cell in self._cells if b == brand}
        cells.update(r["cell"] for r in self._settings.get("cells", []) if r["opf"] == self._opf and r["brand"] == brand)
        self.cell.blockSignals(True)
        self.cell.clear()
        for cell in sorted(cells):
            if not query or query in cell.lower():
                self.cell.addItem(" | ".join(cell.split("|")), cell)
        if current in cells and self.cell.findData(current) >= 0:
            self.cell.setCurrentIndex(self.cell.findData(current))
        self.cell.blockSignals(False)
        self.populate_matrix()

    def local_record(self, analyte):
        return next((r for r in self._settings.get("cells", []) if
                     (r["opf"], r["brand"], r["cell"], r["analyte"]) ==
                     (self._opf, self.brand.currentText(), self.cell.currentData(), analyte)), {})

    def populate_matrix(self, *_):
        previous = self.source_context.currentText()
        self.source_context.blockSignals(True)
        self.source_context.clear()
        for label, _record in self._record_options.get((self.brand.currentText(), self.cell.currentData()), []):
            self.source_context.addItem(label)
        index = self.source_context.findText(previous)
        if index >= 0:
            self.source_context.setCurrentIndex(index)
        self.source_context.blockSignals(False)
        self.populate_matrix_values()

    def populate_matrix_values(self, *_):
        options = self._record_options.get((self.brand.currentText(), self.cell.currentData()), [])
        index = self.source_context.currentIndex()
        record = options[index][1] if 0 <= index < len(options) else {}
        overrides = record.get("provenance", {}).get("local_override", {}).get("factors", {})
        for row, (analyte, label) in enumerate(zip(ANALYTES, ANALYTE_LABELS)):
            local = self.local_record(analyte)
            config = {**self._settings, **local.get("window", {})}
            automatic = {kind: overrides.get(kind, {}).get(analyte, {}).get("automatic",
                          record.get(kind + "_factors", {}).get(analyte)) for kind in ("blend", "regression")}
            lookback = self._settings["method"] == "lookback"
            window_label = "Auto within guardrails" if self._settings["method"] == "auto_max_confidence" else "Spatial within max window"
            values = [label, factor(automatic["blend"]), factor(local.get("blend")), factor(automatic["regression"]),
                      factor(local.get("regression")), dict((v, k) for k, v in WINDOWS)[config["window_mode"]] if lookback else window_label,
                      config["lookback_days"] if lookback else "—", config["min_production_days"], config["max_lookback_days"]]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col >= 5:
                    item.setToolTip("Local window" if local.get("window") else "Inherited default window")
                self.matrix.setItem(row, col, item)
        self.load_editor()

    def load_editor(self, *_):
        local = self.local_record(self.analyte.currentData())
        config = {**self._settings, **local.get("window", {})}
        for widget, kind in ((self.blend, "blend"), (self.regression, "regression")):
            widget.setText(str(local[kind]) if kind in local else "")
        self.regression.setEnabled(not is_dry_plant(self._opf))
        self.regression.setToolTip("Dry-plant regression is fixed." if is_dry_plant(self._opf) else "")
        self.custom_window.setChecked(bool(local.get("window")))
        self.local_window.setCurrentIndex(self.local_window.findData(config["window_mode"]))
        for widget, name in ((self.local_days, "lookback_days"), (self.local_minimum, "min_production_days"),
                              (self.local_maximum, "max_lookback_days")):
            widget.setMaximum(max(36500, config[name]))
            widget.setValue(config[name])
        self.save_cell.setEnabled(bool(self.cell.currentData()))
        self.reset_cell.setEnabled(bool(local))
        self.local_status.setText("Save changes here, then Calculate review. Inherited settings removes this analyte's local factors and window."
                                  if self.cell.currentData() else "Calculate a review to discover source cells. Saved local settings remain searchable.")
        self.update_local_window()

    def update_local_window(self, *_):
        enabled = self.custom_window.isChecked()
        lookback = self._settings["method"] == "lookback"
        self.local_window.setEnabled(enabled and lookback)
        self.local_days.setEnabled(enabled and lookback)
        self.local_minimum.setEnabled(enabled)
        self.local_maximum.setEnabled(enabled)

    def save_local(self, _checked=False, *, reset=False):
        cell = self.cell.currentData()
        if not cell:
            return
        key = dict(opf=self._opf, brand=self.brand.currentText(), cell=cell, analyte=self.analyte.currentData())
        rows = [r for r in self._settings.get("cells", []) if not all(r.get(k) == v for k, v in key.items())]
        if not reset:
            row = {**key, "blend": self.blend.text().strip(), "regression": self.regression.text().strip()}
            if self.custom_window.isChecked():
                row["window"] = dict(window_mode=self.local_window.currentData(), lookback_days=self.local_days.value(),
                                     min_production_days=self.local_minimum.value(), max_lookback_days=self.local_maximum.value())
            rows.append(row)
        try:
            updated = normalise_reconciliation_settings({**self._settings, "cells": rows})
        except ValueError as exc:
            self.local_status.setText(str(exc))
            return
        self._settings = updated
        self.mark_stale()
        self.settingsChanged.emit(self.settings())
        self.local_status.setText("Local settings saved. Calculate review to see the resulting factors and confidence.")

    def open_component(self):
        current = self.sources.currentItem()
        if current is None:
            return
        audit, brand, record = self._evidence_rows[current.data(0, Qt.UserRole)]
        if not record:
            return
        self.brand.setCurrentText(brand)
        self.cell_filter.clear()
        self.cell.setCurrentIndex(self.cell.findData(spatial_cell(record.get("grade_block_key"))))
        label = f"{audit.get('source_id', '')} / {audit.get('hex_id') or 'inventory'} / {record.get('grade_block_key')}"
        self.source_context.setCurrentIndex(self.source_context.findText(label))
        self.tabs.setCurrentIndex(1)
