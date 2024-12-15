import sys
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QTabWidget,
    QFormLayout, QLineEdit, QPushButton
)
from PyQt5.QtGui import QColor, QBrush, QFont
from PyQt5.QtCore import Qt
from setup.OpeningStockpileInventories import OpeningStockpileInventories


class UserInputs(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BlendMaster PoC v0.1.0 © 2024 Fortescue - MOPP")
        self.setGeometry(100, 100, 800, 600)

        # Placeholder for OpeningStockpileInventories
        self.opening_stockpile_inventories = OpeningStockpileInventories()

        # Main Widget and Layout
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)

        # Tabs
        self.tabs = QTabWidget()
        self.layout.addWidget(self.tabs)

        # Add Site Configuration Tab
        self.setup_site_configuration()

        # Add Stockpile Tab
        self.stockpile_tab = QWidget()
        self.tabs.addTab(self.stockpile_tab, "Stockpile Inventories and Reclaim Thresholds")
        self.stockpile_tab_layout = QVBoxLayout(self.stockpile_tab)

        # Stockpile Table
        self.stockpile_table = QTableWidget()
        self.stockpile_tab_layout.addWidget(self.stockpile_table)

        # Add Main Tab
        self.main_tab = QWidget()
        self.tabs.addTab(self.main_tab, "Calendar")
        self.main_tab_layout = QVBoxLayout(self.main_tab)

        # Main Table
        self.main_table = QTableWidget()
        self.main_tab_layout.addWidget(self.main_table)

    
        
        # Disable tabs initially
        self.tabs.setTabEnabled(1, False)  # Disable Stockpile tab
        self.tabs.setTabEnabled(2, False)  # Disable Calendar tab


    def setup_site_configuration(self):
        """Setup for the Site Configuration Form."""
        self.site_config_tab = QWidget()
        self.tabs.addTab(self.site_config_tab, "Site Configuration")
        layout = QFormLayout(self.site_config_tab)

        # Input fields for Hub and Mine
        self.hub_input = QLineEdit()
        self.mine_input = QLineEdit()

        layout.addRow("Hub:", self.hub_input)
        layout.addRow("Mine:", self.mine_input)

        # Submit Button
        submit_button = QPushButton("Submit")
        submit_button.clicked.connect(self.handle_site_config_submit)
        layout.addWidget(submit_button)

    def handle_site_config_submit(self):
        """Handle the submission of site configuration."""
        self.hub_input = self.hub_input.text().strip()
        self.mine_input = self.mine_input.text().strip()

        if self.hub_input and self.mine_input:
            print(f"Site Configuration - Hub: {self.hub_input}, Mine: {self.mine_input}")
            
            # Fetch stockpile data and create setup task
            self.fetch_stockpile_data()
            self.setup_stockpile_table()
            self.tabs.setTabEnabled(1, True)
            self.tabs.setCurrentIndex(1)  # Switch to the next tab
        else:
            print("Error: Please fill in both Hub and Mine.")

    def setup_calendar(self):
        """Setup for the main table (existing functionality)."""
        headers = ["", "Preplan", "Period_1", "Period_2"]  # Column headers
        rows = []

        # Static Rows
        rows.extend([
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
        ])
        
        # Dynamically Add Stockpile Rows
        rows.append(("Stockpiles", [False, False, False], "red"))

        for stockpile in self.stockpile_data_keys:
            rows.append((f"  {stockpile}", [False, False, False], "red"))
            rows.append((f"    State", [True, True, True], "red"))
            rows.append((f"    Maximum Quantity", [True, True, True], "red"))
            rows.append((f"    Cost", [True, True, True], "red"))
            rows.append((f"    Cash", [True, True, True], "red"))
        
        # Define Parent Colors
        parent_colors = {
            "green": QColor(200, 255, 200),
            "blue": QColor(200, 200, 255),
            "red": QColor(255, 200, 200),
        }

        # Set Table Dimensions
        self.main_table.setColumnCount(len(headers))
        self.main_table.setRowCount(len(rows))
        self.main_table.setHorizontalHeaderLabels(headers)
        self.main_table.verticalHeader().setVisible(False)

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
            self.main_table.setItem(row_idx, 0, item_caption)

            # Editable and Non-Editable Cells
            for col_idx, is_editable in enumerate(editables, start=1):
                if is_editable:
                    item = QTableWidgetItem()
                    self.main_table.setItem(row_idx, col_idx, item)
                else:
                    item = QTableWidgetItem("")
                    item.setFlags(Qt.ItemIsEnabled)  # Non-editable
                    item.setBackground(QBrush(QColor(200, 200, 200)))  # Grey background
                    self.main_table.setItem(row_idx, col_idx, item)

        # Resize Columns
        self.main_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.main_table.setEditTriggers(self.main_table.AllEditTriggers)

    def fetch_stockpile_data(self):
        """Fetch stockpile data from OpeningStockpileInventories."""
        hub_input = self.hub_input
        mine_input = self.mine_input
        self.stockpile_data = self.opening_stockpile_inventories.call_opening_stockpile_inventories(hub_input, mine_input)
        self.stockpile_data_keys = self.stockpile_data.keys()
    
    def setup_stockpile_table(self):
        """Setup for the stockpile table in the new Stockpiles tab."""
        # Define Headers
        headers = ["Stockpile Name"] + list(next(iter(self.stockpile_data.values())).keys()) + ["Reclaim Threshold"]
        self.stockpile_table.setColumnCount(len(headers))
        self.stockpile_table.setHorizontalHeaderLabels(headers)
        self.stockpile_table.verticalHeader().setVisible(False)

        # Set Table Dimensions
        self.stockpile_table.setRowCount(len(self.stockpile_data))

        # Populate Stockpile Data
        for row_idx, (stockpile_name, attributes) in enumerate(self.stockpile_data.items()):
            # Stockpile Name
            stockpile_item = QTableWidgetItem(stockpile_name)
            stockpile_item.setFlags(Qt.ItemIsEnabled)  # Non-editable
            self.stockpile_table.setItem(row_idx, 0, stockpile_item)

            # Attributes
            for col_idx, (key, value) in enumerate(attributes.items(), start=1):
                attr_item = QTableWidgetItem(str(value))
                attr_item.setFlags(Qt.ItemIsEnabled)  # Non-editable
                self.stockpile_table.setItem(row_idx, col_idx, attr_item)

            # Reclaim Threshold (Editable)
            reclaim_item = QTableWidgetItem("")
            self.stockpile_table.setItem(row_idx, len(headers) - 1, reclaim_item)

        # Resize Columns
        self.stockpile_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.stockpile_table.setEditTriggers(self.stockpile_table.AllEditTriggers)

        # Add Submit Button
        submit_button = QPushButton("Submit Reclaim Thresholds")
        submit_button.clicked.connect(self.store_reclaim_thresholds)
        self.stockpile_tab_layout.addWidget(submit_button)

    def store_reclaim_thresholds(self):
        """Retrieve and store reclaim threshold values."""
        thresholds = {}
        for row_idx in range(self.stockpile_table.rowCount()):
            stockpile_name = self.stockpile_table.item(row_idx, 0).text()
            reclaim_threshold_item = self.stockpile_table.item(row_idx, self.stockpile_table.columnCount() - 1)
            reclaim_threshold = reclaim_threshold_item.text() if reclaim_threshold_item else ""
            
            # Validate and store reclaim threshold
            if reclaim_threshold.strip():  # Ensure it's not empty
                try:
                    thresholds[stockpile_name] = float(reclaim_threshold)  # Convert to float for numerical use
                except ValueError:
                    print(f"Invalid reclaim threshold for {stockpile_name}: '{reclaim_threshold}' (not a number)")
            else:
                print(f"No reclaim threshold entered for {stockpile_name}")

        print("Reclaim Thresholds Stored:", thresholds)

        # Enable the next tab (Calendar Tab)
        self.setup_calendar()
        self.tabs.setTabEnabled(2, True)
        self.tabs.setCurrentIndex(2)  # Switch to Calendar tab