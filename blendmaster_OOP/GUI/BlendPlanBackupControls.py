from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QComboBox, QAbstractItemView


class BlendPlanBackupControls(QWidget):
    changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        note = QLabel("Backup destinations — choose one per tipping point for the whole plan. Applies to all trucks, including direct tip. Choices come from resolved fallbacks in the same ROM area.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Tipping point", "Backup destination"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setMaximumHeight(130)
        self.table.setMinimumHeight(70)
        layout.addWidget(self.table)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.fields = {}

    def set_context(self, choices, selections, has_plan=True, unavailable_reason=""):
        # A selection can synchronously refresh this table while its previous
        # combo is still delivering a signal. Hide it before Qt's deferred delete.
        for field in self.fields.values():
            field.blockSignals(True)
            field.hide()
        self.table.clearContents()
        self.fields = {}
        self.table.setRowCount(len(choices))
        for index, (point, names) in enumerate(choices.items()):
            self.table.setItem(index, 0, QTableWidgetItem(point))
            combo = QComboBox()
            combo.addItem("Select backup…" if names else "No eligible fallback", "")
            for name in names:
                combo.addItem(name, name)
            selected = selections.get(point, "")
            if selected and selected not in names:
                combo.addItem(f"Unavailable selection: {selected}", selected)
            combo.setCurrentIndex(max(0, combo.findData(selected)))
            combo.setEnabled(has_plan and bool(names or selected))
            self.fields[point] = combo
            self.table.setCellWidget(index, 1, combo)
            combo.currentIndexChanged.connect(self.emit_changed)
        self.table.setColumnWidth(0, 200)
        self.table.setColumnWidth(1, 380)
        if not has_plan:
            message = "Submit a manual plan or run simultaneous optimisation to choose backups."
        elif not any(choices.values()):
            message = "No eligible backup destinations were resolved for this plan. " + (
                unavailable_reason or "Refresh Destination Reconciliation and recalculate the plan; backups must resolve to the same ROM area."
            )
        else:
            message = "Saved choices are checked again when exporting. Recalculate the plan if fallback evidence needs refreshing. Simultaneous results include backups in database XLSX exports."
        self.status.setText(message)

    def emit_changed(self, *_):
        self.changed.emit({point: field.currentData() for point, field in self.fields.items()})
