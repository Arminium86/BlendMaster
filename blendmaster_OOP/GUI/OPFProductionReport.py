"""Native read-only production report; warehouse work uses the host task runner."""

from copy import deepcopy
from datetime import timedelta

from PyQt5.QtCore import QDateTime, QTimer, pyqtSignal
from PyQt5 import sip
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox, QDateTimeEdit,
    QPushButton, QSpinBox, QCheckBox, QProgressBar, QSizePolicy, QPlainTextEdit,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
import matplotlib.dates as mdates

from classes.GradeStreams import normalise_opf
from classes.ProductAssayReport import target_overlays
from setup.ProductAssayHistory import ProductAssayHistory, ProductAssayUnavailable, GRAINS, awst


LABELS = dict(fe="Fe", si="SiO₂", al="Al₂O₃", p="P", mn="Mn")
COLORS = ("#1769aa", "#008577", "#8752a3", "#cf6b27", "#ac3f64", "#4f667a")


class OPFProductionReport(QWidget):
    settingsChanged = pyqtSignal(dict)

    def __init__(self, parent=None, *, service=None, run_async):
        super().__init__(parent)
        self.service = service or ProductAssayHistory()
        self.run_async = run_async
        self._context_key = None
        self._generation = 0
        self._pending = False
        self._loading_settings = False
        self.snapshot = None
        self.points, self.overlays = [], []
        self.targets, self.opfs, self.brands = [], [], []
        self.scenario_start = None
        self._artists = []
        self._boundaries = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        title = QLabel("OPF Production Report")
        title.setStyleSheet("font-size: 20px; font-weight: 750; color: #172033;")
        layout.addWidget(title)
        self.context_label = QLabel("Set an OPF and scenario start in Site Configuration to view production history.")
        self.context_label.setWordWrap(True)
        layout.addWidget(self.context_label)
        filters = QGridLayout()
        self.opf = QComboBox()
        self.brand = QComboBox()
        self.brand.setEditable(False)
        self.grain = QComboBox()
        for key, label in GRAINS.items():
            self.grain.addItem(label, key)
        self.grain.setCurrentIndex(self.grain.findData("shift"))
        self.combined = QCheckBox("Show combined series")
        self.combined.setChecked(True)
        for col, (label, field) in enumerate((("OPF", self.opf), ("Product brand", self.brand), ("Aggregation", self.grain))):
            filters.addWidget(QLabel(label), 0, 2 * col)
            filters.addWidget(field, 0, 2 * col + 1)
        filters.addWidget(self.combined, 0, 6)
        self.start, self.end = QDateTimeEdit(), QDateTimeEdit()
        for field in (self.start, self.end):
            field.setCalendarPopup(True)
            field.setDisplayFormat("dd MMM yyyy HH:mm")
        filters.addWidget(QLabel("From (AWST)"), 1, 0)
        filters.addWidget(self.start, 1, 1)
        filters.addWidget(QLabel("To (AWST)"), 1, 2)
        filters.addWidget(self.end, 1, 3)
        self.last_day = QPushButton("Last 24 hours")
        self.last_day.setToolTip("Reset to the 24 hours before the BlendMaster scenario start.")
        filters.addWidget(self.last_day, 1, 4, 1, 2)
        self.refresh = QPushButton("Refresh")
        filters.addWidget(self.refresh, 1, 6)
        layout.addLayout(filters)
        refresh_row = QHBoxLayout()
        refresh_row.addWidget(QLabel("Auto-refresh (minutes)"))
        self.interval = QSpinBox()
        self.interval.setRange(0, 1440)
        self.interval.setSpecialValueText("Off")
        refresh_row.addWidget(self.interval)
        refresh_row.addWidget(QLabel("Refresh keeps the selected time window. All times are AWST."))
        refresh_row.addStretch()
        layout.addLayout(refresh_row)
        self.status = QLabel("Ready — open this tab after setting the scenario.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("padding: 8px; background: #edf6ff; color: #1e4f8a; border-radius: 4px;")
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(5)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.timestamps = QLabel("")
        self.timestamps.setWordWrap(True)
        self.timestamps.setStyleSheet("color: #526474;")
        layout.addWidget(self.timestamps)
        self.figure = Figure(figsize=(11, 7), layout="constrained", facecolor="#f8fafc")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.canvas.setMinimumSize(600, 460)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)
        self.notes = QLabel("Production records use transaction time; assay sample time is retained separately. Weighted averages use valid assay DMT for each analyte.")
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet("color: #526474; font-size: 11px;")
        layout.addWidget(self.notes)
        self.details_button = QPushButton("Target build details")
        self.details_button.setCheckable(True)
        self.details_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        layout.addWidget(self.details_button)
        self.target_details = QPlainTextEdit()
        self.target_details.setReadOnly(True)
        self.target_details.setMaximumHeight(135)
        self.target_details.hide()
        self.details_button.toggled.connect(self.target_details.setVisible)
        layout.addWidget(self.target_details)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._auto_refresh)
        self.refresh.clicked.connect(lambda: self.request_refresh(force=True))
        self.last_day.clicked.connect(self.reset_window)
        self.interval.valueChanged.connect(self.update_timer)
        for field in (self.opf,):
            field.currentIndexChanged.connect(self.filters_changed)
        for field in (self.start, self.end):
            field.dateTimeChanged.connect(self.filters_changed)
        self.brand.currentIndexChanged.connect(self.redraw)
        self.grain.currentIndexChanged.connect(self.redraw)
        self.combined.toggled.connect(self.redraw)
        self.canvas.mpl_connect("motion_notify_event", self.hover)
        self.draw_empty("No report loaded")

    def settings(self):
        return dict(opf=self.opf.currentData(), brand=self.brand.currentText(), grain=self.grain.currentData(),
                    start=self.start.dateTime().toPyDateTime().isoformat(), end=self.end.dateTime().toPyDateTime().isoformat(),
                    combined=self.combined.isChecked(), refresh_minutes=self.interval.value(),
                    scenario_start=self.scenario_start.isoformat() if self.scenario_start else None)

    def emit_settings(self):
        if self._context_key and not self._loading_settings:
            self.settingsChanged.emit(self.settings())

    def reset_context(self):
        self._context_key = None
        self.scenario_start = None
        self.invalidate("Scenario changed — open this tab to load its production history.")
        self.context_label.setText("Set an OPF and scenario start in Site Configuration to view production history.")
        self.timer.stop()

    def set_context(self, *, scenario_id, scenario_start, opfs, active_opf, brands, targets, state=None):
        if scenario_start is None or not active_opf:
            self.reset_context()
            self.scenario_start = None
            return
        scenario_start = awst(scenario_start)
        opfs = sorted({normalise_opf(o) for o in opfs if o})
        active_opf = normalise_opf(active_opf)
        if active_opf not in opfs:
            opfs.append(active_opf)
        key = (scenario_id, scenario_start.isoformat(), active_opf, tuple(opfs))
        self.targets = deepcopy(targets)
        self.brands = list(dict.fromkeys(str(b).strip().upper() for b in brands if str(b).strip()))
        self.scenario_start = scenario_start
        if key != self._context_key:
            self.invalidate("Ready — Refresh to load product assay history.")
            self._context_key = key
            self.opfs = opfs
            self._loading_settings = True
            state = dict(state or {})
            if state.get("scenario_start") != scenario_start.isoformat():
                state.pop("start", None)
                state.pop("end", None)
            self.opf.clear()
            for opf in opfs:
                self.opf.addItem(opf.replace("_", " "), opf)
            if len(opfs) > 1:
                self.opf.addItem("All configured OPFs", "all")
            index = self.opf.findData(state.get("opf", active_opf))
            self.opf.setCurrentIndex(index if index >= 0 else self.opf.findData(active_opf))
            self.brand.clear()
            self.brand.addItems(list(dict.fromkeys([state.get("brand", ""), *self.brands])))
            if self.brand.findText("") >= 0:
                self.brand.removeItem(self.brand.findText(""))
            try:
                first, last = awst(state.get("start")), awst(state.get("end"))
            except (ValueError, TypeError):
                first, last = scenario_start - timedelta(days=1), scenario_start
            self.start.setDateTime(QDateTime(first))
            self.end.setDateTime(QDateTime(last))
            index = self.grain.findData(state.get("grain", "shift"))
            self.grain.setCurrentIndex(max(index, 0))
            self.combined.setChecked(bool(state.get("combined", True)))
            try:
                self.interval.setValue(int(state.get("refresh_minutes", 0)))
            except (TypeError, ValueError):
                self.interval.setValue(0)
            self._loading_settings = False
        self.context_label.setText(f"Scenario start: {scenario_start:%d %b %Y %H:%M} AWST · Default window: the preceding 24 hours")
        self.combined.setEnabled(self.opf.currentData() == "all")
        self.redraw()
        self.update_timer()

    def selected_opfs(self):
        return self.opfs if self.opf.currentData() == "all" else [self.opf.currentData()] if self.opf.currentData() else []

    def invalidate(self, message):
        self._generation += 1
        self._pending = False
        self.snapshot = None
        self.progress.hide()
        self.refresh.setEnabled(True)
        self.timestamps.clear()
        self.notes.clear()
        self.status.setStyleSheet("padding: 8px; background: #edf6ff; color: #1e4f8a; border-radius: 4px;")
        self.status.setText(message)
        self.draw_empty("Refresh to load this selection")

    def filters_changed(self, *_):
        if self._loading_settings:
            return
        self.invalidate("Selection changed — Refresh to load this window and OPF selection.")
        self.combined.setEnabled(self.opf.currentData() == "all")
        self.emit_settings()

    def reset_window(self):
        if self.scenario_start is None:
            return
        self._loading_settings = True
        self.start.setDateTime(QDateTime(self.scenario_start - timedelta(days=1)))
        self.end.setDateTime(QDateTime(self.scenario_start))
        self._loading_settings = False
        self.filters_changed()
        self.request_refresh(force=False)

    def update_timer(self, *_):
        self.timer.stop()
        if self._loading_settings:
            return
        if self.interval.value() and self.isVisible() and self._context_key:
            self.timer.start(self.interval.value() * 60000)
        self.emit_settings()

    def _auto_refresh(self):
        if self.isVisible() and not self._pending:
            self.request_refresh(force=True)

    def showEvent(self, event):
        super().showEvent(event)
        self.update_timer()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def request_refresh(self, *, force=False):
        if self._pending or not self._context_key:
            return
        start, end, opfs = self.start.dateTime().toPyDateTime(), self.end.dateTime().toPyDateTime(), self.selected_opfs()
        try:
            self.service.request(start, end, opfs)
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self._generation += 1
        generation = self._generation
        self._pending = True
        if self.snapshot is None:
            self.draw_empty("Loading product assay history…")
        self.refresh.setEnabled(False)
        self.progress.show()
        self.status.setText("Loading product assay history from Snowflake…" + (" Previous data remains visible until refresh completes." if self.snapshot else ""))
        def fetch():
            # The host worker normally wraps exceptions in a traceback dictionary.
            # Keep expected service errors intact for this report's inline status.
            try:
                return self.service.fetch(start, end, opfs, force_refresh=force)
            except (ProductAssayUnavailable, ValueError) as exc:
                return exc
        self.run_async(fetch,
                       lambda payload: self.receive(payload, generation), lambda error: self.failed(error, generation))

    def receive(self, payload, generation):
        if sip.isdeleted(self) or generation != self._generation:
            return
        if isinstance(payload, Exception):
            self.failed(payload, generation)
            return
        self._pending = False
        self.refresh.setEnabled(True)
        self.progress.hide()
        self.snapshot = deepcopy(payload)
        previous = self.brand.currentText()
        available = sorted({r["brand"] for r in payload["records"]})
        self._loading_settings = True
        self.brand.clear()
        self.brand.addItems(list(dict.fromkeys([b for b in [previous, *self.brands, *available] if b])))
        if previous:
            self.brand.setCurrentText(previous)
        elif available:
            self.brand.setCurrentText(available[0])
        self._loading_settings = False
        self.redraw()

    def failed(self, error, generation):
        if sip.isdeleted(self) or generation != self._generation:
            return
        self._pending = False
        self.refresh.setEnabled(True)
        self.progress.hide()
        self.snapshot = None
        self.draw_empty("No report data available")
        self.status.setText(str(error) if isinstance(error, (ProductAssayUnavailable, ValueError)) else "Report could not load. Try refreshing the selection.")
        self.status.setStyleSheet("padding: 8px; background: #fff3d9; color: #805200; border-radius: 4px;")
        self.timestamps.clear()
        self.notes.clear()

    def draw_empty(self, text):
        self.figure.clear()
        self.points, self.overlays, self._artists, self._boundaries = [], [], [], []
        self.target_details.clear()
        self.details_button.setText("Target build details")
        for i, name in enumerate(LABELS.values(), 1):
            ax = self.figure.add_subplot(3, 2, i)
            ax.set_title(name + " (%)", loc="left", fontweight="bold", fontsize=11)
            ax.text(.5, .5, text, ha="center", va="center", transform=ax.transAxes, color="#637487", fontsize=10)
            ax.set_xticks([])
            ax.set_yticks([])
        self.canvas.draw_idle()

    def redraw(self, *_):
        if self._loading_settings:
            return
        self.emit_settings()
        if not self.snapshot:
            return
        selected, warnings = self.service.select_brand(self.snapshot["records"], self.brand.currentText())
        opfs = self.selected_opfs()
        selected = [r for r in selected if r["opf"] in opfs]
        self.points = self.service.aggregate(selected, self.grain.currentData(), combined=self.combined.isChecked(), expected_opfs=opfs)
        start, end = self.start.dateTime().toPyDateTime(), self.end.dateTime().toPyDateTime()
        self.overlays, overlay_warnings = target_overlays(self.targets, opfs, self.brand.currentText(), start, end)
        warnings = [*self.snapshot.get("warnings", []), *warnings, *overlay_warnings]
        if any(p["opf"] == "Combined" and set(p["contributing_opfs"]) != set(opfs) for p in self.points):
            warnings.append("Combined values use available OPFs only; some time buckets lack one or more selected OPFs.")
        self.render_charts(start, end)
        valid = sum(any(v is not None for v in r["grades"].values()) for r in selected)
        status = self.snapshot["status"]
        source = {"fresh": "Snowflake data loaded", "cached": "Cached data", "offline_cached": "Snowflake is unavailable — showing cached data"}[status]
        detail = (f"{len(selected):,} product records · {valid:,} with assay values" if selected else "No product records for this brand in the selected window")
        if selected and not valid:
            detail += " · No valid assay values available"
        self.status.setText(("Loading product assay history… Previous data: " if self._pending else "") + source + " · " + detail)
        self.status.setStyleSheet("padding: 8px; border-radius: 4px; background: " + ("#fff3d9; color: #805200;" if status == "offline_cached" else "#edf6ff; color: #1e4f8a;"))
        latest = max((r["observed_at"] for r in selected), default=None)
        def stamp(value):
            return awst(value).strftime("%d %b %Y %H:%M AWST") if value else "Not available"
        self.timestamps.setText(f"Fetched: {stamp(self.snapshot['fetched_at'])} · Latest production record: {stamp(latest)} · Warehouse updated: {stamp(self.snapshot.get('data_last_updated'))}")
        note = "Production time is the transaction time (AWST). Weighted averages use valid assay DMT for each analyte. Edge periods may be partial."
        self.notes.setText(note + ("\n" + " ".join(dict.fromkeys(warnings)) if warnings else ""))

    def render_charts(self, start, end):
        self.figure.clear()
        self._artists = []
        self._boundaries = []
        colors = {opf: COLORS[i % len(COLORS)] for i, opf in enumerate(self.selected_opfs())}
        colors["Combined"] = "#172033"
        legend = []
        for i, (analyte, label) in enumerate(LABELS.items(), 1):
            ax = self.figure.add_subplot(3, 2, i)
            ax.set_facecolor("#ffffff")
            ax.set_title(label + " (%)", loc="left", fontweight="bold", fontsize=11, color="#172033")
            plotted = False
            for opf in [*self.selected_opfs(), "Combined"]:
                rows = [p for p in self.points if p["opf"] == opf]
                if not rows:
                    continue
                x = [max(start, awst(p["timestamp"])) for p in rows]
                y = [p["grades"][analyte] if p["grades"][analyte] is not None else float("nan") for p in rows]
                line, = ax.plot(x, y, color=colors[opf], marker="o", markersize=3.5,
                                linestyle="None" if self.grain.currentData() == "observations" else "-",
                                linewidth=2.2 if opf == "Combined" else 1.4, label=opf.replace("_", " "), picker=5)
                self._artists.append((line, rows, analyte))
                plotted |= any(p["grades"][analyte] is not None for p in rows)
                if i == 1:
                    legend.append(Line2D([], [], color=colors[opf], marker="o", label=opf.replace("_", " ")))
            for overlay in self.overlays:
                for part, style in (("lql", "--"), ("target", "-"), ("hql", ":")):
                    value = overlay["values"].get(f"target_{analyte}_{part}")
                    if value is not None:
                        ax.plot([awst(overlay["start"]), awst(overlay["end"])], [value, value],
                                color=colors[overlay["opf"]], linestyle=style, alpha=.55, linewidth=1.2)
                for key, label_text in (("start", "starts"), ("end", "ends")):
                    boundary = awst(overlay[key])
                    if start < boundary < end and not overlay["reference_only"]:
                        marker = ax.axvline(boundary, color="#7c8794", linewidth=.8, linestyle="--", alpha=.65)
                        self._boundaries.append((marker, f"{overlay['opf']} · {overlay['name']} {label_text}\n{boundary:%d %b %Y %H:%M} AWST"))
            if not plotted:
                ax.text(.5, .5, "No assay data", ha="center", va="center", transform=ax.transAxes, color="#637487")
            ax.set_xlim(start, end)
            locator = mdates.AutoDateLocator(minticks=3, maxticks=5)
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
            ax.tick_params(labelsize=9)
            ax.grid(axis="y", color="#e7edf3", linewidth=.7)
            for spine in ("top", "right"):
                ax.spines[spine].set_visible(False)
            ax.margins(y=.2)
        key = self.figure.add_subplot(3, 2, 6)
        key.axis("off")
        legend += [Line2D([], [], color="#526474", linestyle=s, label=l) for s, l in (("--", "LQL"), ("-", "Target"), (":", "HQL"))]
        if self._boundaries:
            legend.append(Line2D([], [], color="#7c8794", linestyle="--", label="Target build change (vertical)"))
        key.legend(handles=legend, loc="upper left", frameon=False, fontsize=9,
                   ncol=3 if len(legend) > 8 else 2 if len(legend) > 4 else 1)
        details = []
        for o in self.overlays:
            period = "Undated current reference" if o["reference_only"] else f"{awst(o['start']):%d %b %Y %H:%M} – {awst(o['end']):%d %b %Y %H:%M} AWST (visible interval)"
            values = []
            for analyte, label in LABELS.items():
                specs = " / ".join(str(o["values"].get(f"target_{analyte}_{part}")) if o["values"].get(f"target_{analyte}_{part}") is not None else "—" for part in ("lql", "target", "hql"))
                values.append(f"{label}: {specs}")
            details.append(f"{o['opf']} · {o['name']} · {period}\nLQL / Target / HQL (%): " + " · ".join(values))
        self.target_details.setPlainText("\n\n".join(details) or "No matching target specifications for this selection.")
        self.details_button.setText(f"Target build details ({len(self.overlays)})")
        self.canvas.draw_idle()

    def hover(self, event):
        for line, rows, analyte in self._artists:
            if event.inaxes is not line.axes:
                continue
            hit, detail = line.contains(event)
            if hit and len(detail.get("ind", [])):
                row = rows[detail["ind"][0]]
                value = row["grades"][analyte]
                self.canvas.setToolTip(f"{row['opf'].replace('_', ' ')} · {row['brand']}\n"
                                       f"{awst(row['timestamp']):%d %b %Y %H:%M} AWST\n"
                                       f"{LABELS[analyte]}: {value:.6g}% · {row['dmt']:,.1f} DMT\n"
                                       f"Assayed DMT coverage: {row['coverage'][analyte]:.1%}\n"
                                       f"Assay sampled: {row.get('sampled_at') or 'Not available'}\n"
                                       f"OPFs contributing: {', '.join(row['contributing_opfs'])}")
                return
        for marker, description in self._boundaries:
            if event.inaxes is marker.axes and marker.contains(event)[0]:
                self.canvas.setToolTip(description)
                return
        self.canvas.setToolTip("")
