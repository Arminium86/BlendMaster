"""Conversion-only controls; editing these never changes an existing plan."""
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QWidget, QHBoxLayout, QCheckBox, QComboBox, QLabel
from classes.ManualRatioRounding import INCREMENTS, rounding_settings


class ManualRatioRoundingControls(QWidget):
    changed = pyqtSignal(dict)

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.enabled = QCheckBox("Round ratios when prepopulating")
        self.increment = QComboBox()
        for value in INCREMENTS:
            self.increment.addItem(f"{value}%", value)
        layout.addWidget(self.enabled)
        layout.addWidget(QLabel("Increment"))
        layout.addWidget(self.increment)
        note = QLabel("Includes direct tip. Tries shorter duration, then reclaim only with direct-tip share split evenly and rounded again. If both fail, keeps the successful sequence. Review the rounding audit and targets before publishing.")
        note.setWordWrap(True)
        layout.addWidget(note, 1)
        self.set_settings(settings)
        self.enabled.toggled.connect(self.emit_changed)
        self.increment.currentIndexChanged.connect(self.emit_changed)

    def set_settings(self, value):
        settings = rounding_settings(value)
        self.enabled.blockSignals(True)
        self.increment.blockSignals(True)
        self.enabled.setChecked(settings["enabled"])
        self.increment.setCurrentIndex(self.increment.findData(settings["increment"]))
        self.increment.setEnabled(settings["enabled"])
        self.enabled.blockSignals(False)
        self.increment.blockSignals(False)

    def settings(self):
        return {"enabled": self.enabled.isChecked(), "increment": self.increment.currentData()}

    def emit_changed(self, *_):
        self.increment.setEnabled(self.enabled.isChecked())
        self.changed.emit(self.settings())
