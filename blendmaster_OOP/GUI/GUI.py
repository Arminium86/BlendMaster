import sys
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView
)
from PyQt5.QtGui import QColor, QBrush, QFont
from PyQt5.QtCore import Qt


class DataEntryTable(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Data Entry Table Form")
        self.setGeometry(100, 100, 800, 600)

        # Main Widget and Layout
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)

        # Table Widget
        self.table = QTableWidget()
        self.layout.addWidget(self.table)

        # Table Setup
        self.setup_table()

    def setup_table(self):
        """Set up the table structure with captions, inputs, and colors."""
        headers = ["", "Preplan", "Period_1", "Period_2"]  # Column headers

        # Rows: (Caption, Editable for Columns 1-3, Parent Color Group)
        rows = [
            ("Reclaim Equipment", [False, False, False], "green"),
            ("  Max Reclaim Rate", [True, True, True], "green"),

            ("Crusher", [False, False, False], "blue"),
            ("  Rate", [True, True, True], "blue"),
            ("  Target", [False, False, False], "blue"),
            ("    Fe", [False, False, False], "blue"),
            ("      Min", [True, True, True], "blue"),
            ("      Max", [True, True, True], "blue"),
            ("    Si", [False, False, False], "blue"),
            ("      Min", [True, True, True], "blue"),
            ("      Max", [True, True, True], "blue"),
            ("    Al", [False, False, False], "blue"),
            ("      Min", [True, True, True], "blue"),
            ("      Max", [True, True, True], "blue"),
            ("    P", [False, False, False], "blue"),
            ("      Min", [True, True, True], "blue"),
            ("      Max", [True, True, True], "blue"),
            ("    Mn", [False, False, False], "blue"),
            ("      Min", [True, True, True], "blue"),
            ("      Max", [True, True, True], "blue"),

            ("  Direct Feed Ratio", [False, False, False], "blue"),
            ("    Min", [True, True, True], "blue"),
            ("    Max", [True, True, True], "blue"),

            ("Stockpiles", [False, False, False], "red"),
            ("  Stockpile 1", [False, False, False], "red"),
            ("    State", [True, True, True], "red"),
            ("    Maximum Quantity", [True, True, True], "red"),
            ("    Cost", [True, True, True], "red"),
            ("    Cash", [True, True, True], "red"),
            ("  Stockpile 2", [False, False, False], "red"),
            ("    State", [True, True, True], "red"),
            ("    Maximum Quantity", [True, True, True], "red"),
            ("    Cost", [True, True, True], "red"),
            ("    Cash", [True, True, True], "red"),
            ("  Stockpile 3", [False, False, False], "red"),
            ("    State", [True, True, True], "red"),
            ("    Maximum Quantity", [True, True, True], "red"),
            ("    Cost", [True, True, True], "red"),
            ("    Cash", [True, True, True], "red"),
        ]

        # Define Parent Colors
        parent_colors = {
            "green": QColor(200, 255, 200),
            "blue": QColor(200, 200, 255),
            "red": QColor(255, 200, 200),
        }

        # Set Table Dimensions
        self.table.setColumnCount(len(headers))
        self.table.setRowCount(len(rows))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)

        # Bold Font for Captions
        bold_font = QFont()
        bold_font.setBold(True)

        # Populate Table
        for row_idx, (caption, editables, color_group) in enumerate(rows):
            # Caption Column
            item_caption = QTableWidgetItem(caption)
            item_caption.setFlags(Qt.ItemIsEnabled)  # Non-editable
            item_caption.setFont(bold_font)
            item_caption.setBackground(QBrush(parent_colors[color_group]))  # Parent group color
            self.table.setItem(row_idx, 0, item_caption)

            # Editable and Non-Editable Cells
            for col_idx, is_editable in enumerate(editables, start=1):
                if is_editable:
                    item = QTableWidgetItem()
                    self.table.setItem(row_idx, col_idx, item)
                else:
                    item = QTableWidgetItem("")
                    item.setFlags(Qt.ItemIsEnabled)  # Non-editable
                    item.setBackground(QBrush(QColor(200, 200, 200)))  # Grey background
                    self.table.setItem(row_idx, col_idx, item)

        # Resize Columns
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(self.table.AllEditTriggers)

    def save_data(self):
        """Collect table data."""
        print("Saving Table Data:")
        for row in range(self.table.rowCount()):
            row_caption = self.table.item(row, 0).text()
            for col in range(1, self.table.columnCount()):
                item = self.table.item(row, col)
                if item and item.text():
                    print(f"{row_caption} - {self.table.horizontalHeaderItem(col).text()}: {item.text()}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DataEntryTable()
    window.show()
    sys.exit(app.exec_())
