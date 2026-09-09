"""Selected-build settings for the Product Targets table."""

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QComboBox

from classes.ProductTargetModes import EVALUATION_LABELS, LIMIT_MODE_LABELS, target_mode_fields


class ProductTargetModeControls(QWidget):
    changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        self.title = QLabel("Select a build to view its soft target settings.")
        self.title.setStyleSheet("font-weight: 650; color: #334155;")
        layout.addWidget(self.title)
        top = QHBoxLayout()
        top.addWidget(QLabel("Evaluate targets against"))
        self.evaluation = QComboBox()
        for value, label in EVALUATION_LABELS.items():
            self.evaluation.addItem(label, value)
        self.evaluation.setMinimumWidth(max(self.evaluation.fontMetrics().horizontalAdvance(label) for label in EVALUATION_LABELS.values()) + 55)
        self.evaluation.setToolTip("Applies to this build, including a partial build at the planning horizon.")
        top.addWidget(self.evaluation)
        top.addStretch()
        layout.addLayout(top)
        limits = QGridLayout()
        self.limits = {}
        for column, (a, label) in enumerate((("fe", "Fe"), ("si", "Si"), ("al", "Al"), ("p", "P"), ("mn", "Mn"))):
            field = QComboBox()
            for value, text in LIMIT_MODE_LABELS.items():
                field.addItem(text, value)
            field.setToolTip("Hard: LQL/HQL cannot be breached. Soft: breaches receive a higher penalty than Target deviation. Blank limits stay unspecified.")
            field.setMinimumWidth(max(field.fontMetrics().horizontalAdvance(text) for text in LIMIT_MODE_LABELS.values()) + 55)
            limits.addWidget(QLabel(label + " LQL/HQL"), 0, column)
            limits.addWidget(field, 1, column)
            self.limits[a] = field
            field.currentIndexChanged.connect(self.emit_changed)
        layout.addLayout(limits)
        self.evaluation.currentIndexChanged.connect(self.emit_changed)
        self.set_row(None)

    def set_row(self, row, name=""):
        self._loading = True
        modes = target_mode_fields(row or {})
        soft = bool(row) and modes["target_mode"] == "soft"
        self.title.setText(f"Selected build: {name} · {'Soft target settings' if soft else 'Hard Min / Max (soft settings retained)'}"
                           if row else "Select a build to view its soft target settings.")
        self.evaluation.setCurrentIndex(self.evaluation.findData(modes["target_evaluation_basis"]))
        self.evaluation.setEnabled(soft)
        for a, field in self.limits.items():
            field.setCurrentIndex(field.findData(modes[f"target_{a}_limit_mode"]))
            field.setEnabled(soft)
        self._loading = False

    def emit_changed(self, *_):
        if not self._loading:
            self.changed.emit({"target_evaluation_basis": self.evaluation.currentData(),
                               **{f"target_{a}_limit_mode": field.currentData() for a, field in self.limits.items()}})
