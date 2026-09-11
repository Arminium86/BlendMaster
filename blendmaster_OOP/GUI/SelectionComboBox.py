"""A dropdown with single or checked multiple selection and stable item values."""
from PyQt5.QtCore import Qt, QEvent, pyqtSignal
from PyQt5.QtWidgets import QComboBox


class SelectionComboBox(QComboBox):
    selectionChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.multiple = False
        self.view().viewport().installEventFilter(self)
        self.view().installEventFilter(self)
        self.currentIndexChanged.connect(self.single_changed)

    def selectedTexts(self):
        if not self.multiple:
            value = super().currentText()
            return [value] if value else []
        return [self.itemText(i) for i in range(self.count()) if self.model().item(i).checkState() == Qt.Checked]

    def currentText(self):
        # Legacy readers still receive the primary OPF; routing uses selectedTexts.
        values = self.selectedTexts()
        return values[0] if values else ''

    def setCurrentText(self, text):
        if self.multiple:
            self.setSelectedTexts([text])
        else:
            super().setCurrentText(text)

    def setMultiple(self, multiple):
        selected = self.selectedTexts()
        self.multiple = bool(multiple)
        self.setEditable(self.multiple)
        if self.multiple:
            self.lineEdit().setReadOnly(True)
        self.setSelectedTexts(selected)

    def setSelectedTexts(self, selected):
        blocked = self.blockSignals(True)
        try:
            if self.multiple:
                for i in range(self.count()):
                    item = self.model().item(i)
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Checked if self.itemText(i) in selected else Qt.Unchecked)
                self.lineEdit().setText(', '.join(self.selectedTexts()) or 'Select…')
                self.lineEdit().setCursorPosition(0)
                self.setToolTip(', '.join(self.selectedTexts()))
            else:
                for i in range(self.count()):
                    self.model().item(i).setData(None, Qt.CheckStateRole)
                    self.model().item(i).setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.setCurrentIndex(self.findText(selected[0]) if selected and self.findText(selected[0]) >= 0 else 0 if self.count() else -1)
        finally:
            self.blockSignals(blocked)

    def single_changed(self, *_):
        if not self.multiple:
            self.selectionChanged.emit()

    def toggle_selection(self, index):
        if self.multiple:
            item = self.model().item(index.row())
            item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
            self.lineEdit().setText(', '.join(self.selectedTexts()) or 'Select…')
            self.lineEdit().setCursorPosition(0)
            self.setToolTip(', '.join(self.selectedTexts()))
            self.selectionChanged.emit()

    def eventFilter(self, obj, event):
        if self.multiple:
            if obj is self.view().viewport() and event.type() == QEvent.MouseButtonRelease:
                index = self.view().indexAt(event.pos())
                if index.isValid():
                    self.toggle_selection(index)
                return True
            if obj is self.view() and event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
                self.toggle_selection(self.view().currentIndex())
                return True
        return super().eventFilter(obj, event)

    def hidePopup(self):
        if not self.multiple or not self.view().underMouse():
            super().hidePopup()
