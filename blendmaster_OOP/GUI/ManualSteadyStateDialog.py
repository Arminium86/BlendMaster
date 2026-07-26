from copy import deepcopy

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from classes.ManualBlendPlanner import ManualBlendPlanningError


class ManualSteadyStateDialog(QDialog):
    """Edit direct-tip tonnes and review the resulting manual feed."""

    HEADERS = [
        "Steady State", "Blend ID", "Start", "End", "Duration (h)",
        "Boundary Event", "Direct Tip Source", "Available (t)",
        "Selected (t)", "DT Fe", "DT Si", "DT Al", "DT P", "DT Mn",
        "Stockpile Feed (t)", "Direct Tip (%)", "Total Feed (t)",
        "Output Fe", "Output Si", "Output Al", "Output P", "Output Mn",
    ]
    SELECTED_COLUMN = 8

    def __init__(
        self, planner, states, allocations=None, parent=None
    ):
        super().__init__(parent)
        self.planner = planner
        self.states = states
        self.allocations = deepcopy(allocations or {})
        self.report = None
        self._row_context = {}
        self._updating = False

        self.setWindowTitle("Manual Steady States & Direct Tip")
        self.resize(1500, 720)
        layout = QVBoxLayout(self)

        help_label = QLabel(
            "Each row is an aggregated grade-block source delivered within "
            "that steady state. Enter the tonnes to direct tip. The remaining "
            "crusher feed is supplied by the scheduled stockpile blend, so "
            "direct tip plus stockpile feed always equals 100%."
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.SelectedClicked
        )
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.apply_button = QPushButton(
            "Apply & Generate Manual Blend Report"
        )
        self.apply_button.setStyleSheet(
            "QPushButton { background: #15803d; color: white; "
            "font-weight: 600; padding: 7px 14px; border-radius: 4px; } "
            "QPushButton:disabled { background: #94a3b8; }"
        )
        button_layout.addWidget(self.cancel_button)
        button_layout.addWidget(self.apply_button)
        layout.addLayout(button_layout)

        self.cancel_button.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self.apply_plan)
        self.populate()
        self.table.cellChanged.connect(self.handle_cell_changed)
        self.recalculate()

    @staticmethod
    def _item(value="", editable=False):
        item = QTableWidgetItem(str(value))
        item.setTextAlignment(Qt.AlignCenter)
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if editable:
            flags |= Qt.ItemIsEditable
            item.setBackground(QColor("#fff7cc"))
        item.setFlags(flags)
        return item

    @staticmethod
    def _format(value, decimals=2):
        try:
            return f"{float(value):,.{decimals}f}"
        except (TypeError, ValueError):
            return ""

    def populate(self):
        rows = []
        for state in self.states:
            candidates = state.get("direct_tip_candidates", [])
            if not candidates:
                rows.append((state, None))
            else:
                rows.extend((state, candidate) for candidate in candidates)

        self._updating = True
        try:
            self.table.setRowCount(len(rows))
            for row_number, (state, candidate) in enumerate(rows):
                source = candidate["source"] if candidate else ""
                self._row_context[row_number] = (
                    state["state_key"], source
                )
                values = [
                    state["steady_state_number"],
                    state["blend_ID"],
                    state["start_datetime"].strftime("%Y-%m-%d %H:%M"),
                    state["end_datetime"].strftime("%Y-%m-%d %H:%M"),
                    self._format(state["steady_state_duration"]),
                    state["trigger"],
                    source if candidate else "No eligible direct tip",
                    self._format(
                        candidate["available_tonnes"] if candidate else 0,
                        1,
                    ),
                ]
                for column, value in enumerate(values):
                    self.table.setItem(
                        row_number, column, self._item(value)
                    )

                selected = (
                    self.allocations.get(
                        state["state_key"], {}
                    ).get(source, 0)
                    if candidate else 0
                )
                self.table.setItem(
                    row_number,
                    self.SELECTED_COLUMN,
                    self._item(
                        self._format(selected, 1),
                        editable=candidate is not None,
                    ),
                )

                for index, grade in enumerate(
                    self.planner.GRADES, start=9
                ):
                    value = (
                        candidate.get(f"grade_{grade}", 0)
                        if candidate else 0
                    )
                    self.table.setItem(
                        row_number, index,
                        self._item(self._format(value)),
                    )
                for column in range(14, len(self.HEADERS)):
                    self.table.setItem(
                        row_number, column, self._item()
                    )
            self.table.resizeColumnsToContents()
        finally:
            self._updating = False

    def handle_cell_changed(self, row, column):
        if self._updating or column != self.SELECTED_COLUMN:
            return
        state_key, source = self._row_context[row]
        if not source:
            return
        item = self.table.item(row, column)
        text = (item.text() if item else "").replace(",", "").strip()
        try:
            value = float(text or 0)
            if value < 0:
                raise ValueError
        except ValueError:
            self.status_label.setText(
                "Selected direct-tip tonnes must be a non-negative number."
            )
            self.status_label.setStyleSheet(
                "color: #b91c1c; font-weight: 600;"
            )
            self.apply_button.setEnabled(False)
            return
        self.allocations.setdefault(state_key, {})[source] = value
        self.recalculate()

    def recalculate(self):
        try:
            summaries = self.planner.state_summaries(
                self.states, self.allocations
            )
        except ManualBlendPlanningError as error:
            self.status_label.setText(str(error))
            self.status_label.setStyleSheet(
                "color: #b91c1c; font-weight: 600;"
            )
            self.apply_button.setEnabled(False)
            return

        self._updating = True
        try:
            for row_number, (state_key, _) in self._row_context.items():
                summary = summaries[state_key]
                output_values = [
                    self._format(
                        summary["stockpile_feed_tonnes"], 1
                    ),
                    self._format(
                        summary["direct_tip_ratio"] * 100, 1
                    ),
                    self._format(
                        summary["feed_capacity_tonnes"], 1
                    ),
                ] + [
                    self._format(summary[f"output_grade_{grade}"])
                    for grade in self.planner.GRADES
                ]
                for column, value in enumerate(
                    output_values, start=14
                ):
                    self.table.item(row_number, column).setText(value)
            self.status_label.setText(
                f"{len(self.states)} manual steady state(s) ready. "
                "Unselected payloads retain their planned 2WP or fallback "
                "stockpile destination."
            )
            self.status_label.setStyleSheet("color: #166534;")
            self.apply_button.setEnabled(True)
        finally:
            self._updating = False

    def apply_plan(self):
        try:
            self.report = self.planner.build_report(
                self.states, self.allocations
            )
        except ManualBlendPlanningError as error:
            self.status_label.setText(str(error))
            self.status_label.setStyleSheet(
                "color: #b91c1c; font-weight: 600;"
            )
            self.apply_button.setEnabled(False)
            return
        self.accept()
