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
from classes.GradeBlockIdentity import parent_grade_block_name


class ManualSteadyStateDialog(QDialog):
    """Edit direct-tip tonnes and review the resulting manual feed."""

    GRADES = ("fe", "si", "al", "p", "mn")
    HEADERS = [
        "Steady State", "Blend ID", "Start", "End", "Duration (h)",
        "Boundary Event", "Direct Tip Parent Grade Block", "Available (t)",
        "Acceptance Ratio (0–1)", "DT Fe", "DT Si", "DT Al", "DT P", "DT Mn",
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
            "Each row is a parent grade block delivered within that steady "
            "state; operational slices are combined. Enter the ratio of its "
            "total available tonnes to direct tip, from 0 (none) to 1 (all). "
            "The ratio is applied to every underlying slice. The remaining "
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

    @classmethod
    def aggregate_parent_candidates(cls, candidates):
        """Combine steady-state slice candidates at parent-block level."""
        grouped = {}
        for candidate in candidates or []:
            source = str(candidate.get("source") or "").strip()
            parent = parent_grade_block_name(source)
            if not parent:
                continue
            available = max(
                float(candidate.get("available_tonnes") or 0), 0.0
            )
            aggregate = grouped.setdefault(parent, {
                "source": parent,
                "available_tonnes": 0.0,
                "_slice_candidates": [],
                **{
                    f"_grade_mass_{grade}": 0.0
                    for grade in cls.GRADES
                },
                **{
                    f"_grade_weight_{grade}": 0.0
                    for grade in cls.GRADES
                },
            })
            aggregate["available_tonnes"] += available
            aggregate["_slice_candidates"].append({
                "source": source,
                "available_tonnes": available,
            })
            for grade in cls.GRADES:
                value = candidate.get(f"grade_{grade}")
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    continue
                aggregate.setdefault(f"_grade_mass_{grade}", 0.0)
                aggregate.setdefault(f"_grade_weight_{grade}", 0.0)
                aggregate[f"_grade_mass_{grade}"] += numeric * available
                aggregate[f"_grade_weight_{grade}"] += available

        result = []
        for aggregate in grouped.values():
            for grade in cls.GRADES:
                weight = aggregate.pop(f"_grade_weight_{grade}", 0.0)
                mass = aggregate.pop(f"_grade_mass_{grade}", 0.0)
                aggregate[f"grade_{grade}"] = (
                    mass / weight if weight > 0 else None
                )
            result.append(aggregate)
        return result

    @staticmethod
    def selected_parent_tonnes(allocations, state_key, candidate):
        selected = (allocations or {}).get(state_key, {}) or {}
        return sum(
            float(selected.get(slice_row["source"], 0) or 0)
            for slice_row in candidate.get("_slice_candidates", [])
        )

    @staticmethod
    def apply_parent_ratio(
        allocations, state_key, candidate, acceptance_ratio
    ):
        state_allocations = allocations.setdefault(state_key, {})
        for slice_row in candidate.get("_slice_candidates", []):
            state_allocations[slice_row["source"]] = (
                acceptance_ratio * slice_row["available_tonnes"]
            )

    def populate(self):
        rows = []
        for state in self.states:
            candidates = self.aggregate_parent_candidates(
                state.get("direct_tip_candidates", [])
            )
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
                    state["state_key"],
                    source,
                    candidate["available_tonnes"] if candidate else 0,
                    candidate,
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
                        2,
                    ),
                ]
                for column, value in enumerate(values):
                    self.table.setItem(
                        row_number, column, self._item(value)
                    )

                selected_tonnes = (
                    self.selected_parent_tonnes(
                        self.allocations,
                        state["state_key"],
                        candidate,
                    )
                    if candidate else 0
                )
                acceptance_ratio = (
                    selected_tonnes / candidate["available_tonnes"]
                    if candidate
                    and candidate["available_tonnes"] > 0
                    else 0
                )
                self.table.setItem(
                    row_number,
                    self.SELECTED_COLUMN,
                    self._item(
                        self._format(acceptance_ratio, 3),
                        editable=candidate is not None,
                    ),
                )
                if candidate is not None:
                    self.table.item(
                        row_number, self.SELECTED_COLUMN
                    ).setToolTip(
                        "Enter a value from 0 to 1. Accepted direct-tip "
                        "tonnes are this ratio multiplied by Available (t)."
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
        (
            state_key, source, available_tonnes, candidate
        ) = self._row_context[row]
        if not source:
            return
        item = self.table.item(row, column)
        text = (item.text() if item else "").replace(",", "").strip()
        try:
            acceptance_ratio = float(text or 0)
            if not 0 <= acceptance_ratio <= 1:
                raise ValueError
        except ValueError:
            self.status_label.setText(
                "The direct-tip acceptance ratio must be a number from 0 to 1."
            )
            self.status_label.setStyleSheet(
                "color: #b91c1c; font-weight: 600;"
            )
            self.apply_button.setEnabled(False)
            return
        self.apply_parent_ratio(
            self.allocations,
            state_key,
            candidate,
            acceptance_ratio,
        )
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
            for row_number, (
                state_key, _source, _available_tonnes, _candidate
            ) in self._row_context.items():
                summary = summaries[state_key]
                output_values = [
                    self._format(
                        summary["stockpile_feed_tonnes"], 2
                    ),
                    self._format(
                        summary["direct_tip_ratio"] * 100, 1
                    ),
                    self._format(
                        summary["feed_capacity_tonnes"], 2
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
