import sys
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QTabWidget,
    QFormLayout, QLineEdit, QPushButton, QComboBox, QHBoxLayout, QLabel, QMessageBox, QDateTimeEdit, QFileDialog
)
from PyQt5.QtGui import QColor, QBrush, QFont
from PyQt5.QtCore import Qt
from setup.OpeningStockpileInventories import OpeningStockpileInventories
from execute.Run import Run
from datetime import datetime



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

        # Initialise main program
        self.run_program = Run()

    def setup_site_configuration(self):
        """Setup for the Site Configuration Form."""
        self.site_config_tab = QWidget()
        self.tabs.addTab(self.site_config_tab, "Site Selection")
        layout = QFormLayout(self.site_config_tab)

        # Dropdown lists for Hub and Mine
        self.hub_input = QComboBox()
        self.hub_input.addItems(["Chichester Hub", "Western Hub", "Solomon Hub", "Iron Bridge Hub"])
        self.mine_input = QComboBox()

        # Adjust size of dropdowns
        self.hub_input.setFixedWidth(150)
        self.mine_input.setFixedWidth(150)

        # Create bold labels for Hub and Mine
        hub_label = QLabel("Hub:")
        hub_label.setStyleSheet("font-weight: bold;")
        mine_label = QLabel("Mine:")
        mine_label.setStyleSheet("font-weight: bold;")

        layout.addRow(hub_label, self.hub_input)
        layout.addRow(mine_label, self.mine_input)

        # Connect hub dropdown change to update mine dropdown
        self.hub_input.currentIndexChanged.connect(self.update_mine_dropdown)

        # --- Input 1: Time Starts At ---
        time_label = QLabel("Time Starts At:")
        time_label.setStyleSheet("font-weight: bold;")
        self.time_mode = QComboBox()
        self.time_mode.addItems(["Now", "Set Time"])
        self.time_mode.setFixedWidth(150)

        # DateTime selector (initially disabled)
        self.start_time = QDateTimeEdit()
        self.start_time.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.start_time.setFixedWidth(200)
        self.start_time.setEnabled(False)

        # Enable datetime input only if "Set Time" is selected
        self.time_mode.currentIndexChanged.connect(
            lambda: self.start_time.setEnabled(self.time_mode.currentIndex() == 1)
        )

        layout.addRow(time_label, self.time_mode)
        layout.addRow(QLabel("Set Date & Time:"), self.start_time)

        # --- Input 2: Expit Transactions ---
        expit_label = QLabel("Expit Transactions:")
        expit_label.setStyleSheet("font-weight: bold;")
        self.expit_mode = QComboBox()
        self.expit_mode.addItems(["Execute Original Expit Transactions", "Update Transactions Based on Current Time"])
        self.expit_mode.setFixedWidth(300)

        # Expit options are active only when time starts at "Now"
        self.expit_mode.setEnabled(False)
        self.time_mode.currentIndexChanged.connect(
            lambda: self.expit_mode.setEnabled(self.time_mode.currentIndex() == 0)
        )

        layout.addRow(expit_label, self.expit_mode)

        # --- Input 3: Select File ---
        file_label = QLabel("Select File:")
        file_label.setStyleSheet("font-weight: bold;")
        self.file_path = QLineEdit()
        self.file_path.setReadOnly(True)
        self.file_path.setFixedWidth(400)

        # Browse button
        self.file_button = QPushButton("Browse")
        self.file_button.setFixedWidth(100)
        self.file_button.clicked.connect(self.browse_file)

        file_layout = QHBoxLayout()
        file_layout.addWidget(self.file_path)
        file_layout.addWidget(self.file_button)

        layout.addRow(file_label, file_layout)

        # --- Input 4: Optimized Blend Choices ---
        blend_label = QLabel("Optimized Blend Choices:")
        blend_label.setStyleSheet("font-weight: bold;")
        self.blend_mode = QComboBox()
        self.blend_mode.addItems(["Select the Best Result Automatically", "Prompt User at Every Decision Point"])
        self.blend_mode.setFixedWidth(300)

        layout.addRow(blend_label, self.blend_mode)

        # Submit Button
        submit_button = QPushButton("Submit")
        submit_button.setFixedWidth(100)
        submit_button.clicked.connect(self.handle_site_config_submit)

        # Create a horizontal layout for the button
        button_layout = QHBoxLayout()
        button_layout.addWidget(submit_button)
        button_layout.addStretch()  # Push button to the left

        # Add button layout to the main layout
        layout.addRow(button_layout)

    def browse_file(self):
        """Browse to select a file."""
        file_path, _ = QFileDialog.getOpenFileName(self, "Select File", "", "CSV Files (*.csv);;All Files (*)")
        if file_path:
            self.file_path.setText(file_path)

    def update_mine_dropdown(self):
        """Update the Mine dropdown based on the selected Hub."""
        hub_selection = self.hub_input.currentText()

        # Clear current items in the Mine dropdown
        self.mine_input.clear()

        # Populate Mine dropdown based on Hub selection
        if hub_selection == "Chichester Hub":
            self.mine_input.addItems(["CC", "CB"])
        elif hub_selection == "Iron Bridge Hub":
            self.mine_input.addItems(["IB"])
        elif hub_selection == "Western Hub":
            self.mine_input.addItems(["EW"])
        elif hub_selection == "Solomon Hub":
            self.mine_input.addItems(["KV", "FT"])
        else:
            # Default: No selection or unknown hub
            self.mine_input.addItems([])

    def handle_site_config_submit(self):
        """Handle the submission of site configuration."""
        self.time_mode = self.time_mode.currentIndex() + 1  # Translate to 1 or 2

        if self.time_mode == 2:  # If "Set Time" is selected
           self.start_time = self.start_time.dateTime().toPyDateTime()

        else: self.start_time = datetime.now()

        self.expit_mode = self.expit_mode.currentIndex() + 1 if self.expit_mode.isEnabled() else 1
        self.file_path = self.file_path.text()
        self.blend_mode = self.blend_mode.currentIndex() + 1  # Translate to 1 or 2

        print("Time Mode:", self.time_mode )
        print("Date/Time Value:", self.start_time)
        print("Expit Mode:", self.expit_mode)
        print("Selected File:", self.file_path)
        print("Blend Mode:", self.blend_mode)
        QMessageBox.information(self, "Configuration Submitted", "Your configuration has been saved!")
        
        self.hub_input = self.hub_input.currentText().strip()
        self.mine_input = self.mine_input.currentText().strip()

        if self.hub_input and self.mine_input:
            print(f"Site Configuration - Hub: {self.hub_input}, Mine: {self.mine_input}")
            
            # Fetch stockpile data and create setup task
            self.fetch_stockpile_data()
            self.setup_stockpile_table()
            self.tabs.setTabEnabled(1, True)
            self.tabs.setCurrentIndex(1)  # Switch to the next tab
        else:
            print("Error: Please fill in both Hub and Mine.")

    def fetch_stockpile_data(self):
        """Fetch stockpile data from OpeningStockpileInventories."""
        hub_input = self.hub_input
        mine_input = self.mine_input
        self.stockpile_data = self.opening_stockpile_inventories.call_opening_stockpile_inventories(hub_input, mine_input, self.start_time)

        self.stockpile_data_keys = self.stockpile_data.keys()
    
    def setup_stockpile_table(self):
        """Setup for the stockpile table in the new Stockpiles tab with live conditional formatting."""
        # Define Headers
        headers = [
            "Stockpile Name",
            "Balance (WMT)",
            "Grade Fe (%)",
            "Grade Si (%)",
            "Grade Al (%)",
            "Grade P (%)",
            "Grade Mn (%)",
            "Reclaim Threshold (WMT)"
        ]
        self.stockpile_table.setColumnCount(len(headers))
        self.stockpile_table.setHorizontalHeaderLabels(headers)
        self.stockpile_table.verticalHeader().setVisible(False)

        # Bold headers
        header_font = self.stockpile_table.horizontalHeader().font()
        header_font.setBold(True)
        self.stockpile_table.horizontalHeader().setFont(header_font)

        # Left-align the first header
        header_item = self.stockpile_table.horizontalHeaderItem(0)
        if header_item:
            header_item.setTextAlignment(Qt.AlignLeft)

        # Set Table Dimensions
        self.stockpile_table.setRowCount(len(self.stockpile_data))

        # Populate Stockpile Data
        for row_idx, (stockpile_name, attributes) in enumerate(self.stockpile_data.items()):
            # Stockpile Name (Left-aligned)
            stockpile_item = QTableWidgetItem(str(stockpile_name))
            stockpile_item.setFlags(Qt.ItemIsEnabled)  # Non-editable
            stockpile_item.setTextAlignment(Qt.AlignLeft)
            self.stockpile_table.setItem(row_idx, 0, stockpile_item)

            # Attributes (Balance and Grades, Center-aligned)
            keys = ["BALANCE", "GRADE_FE", "GRADE_SI", "GRADE_AL", "GRADE_P", "GRADE_MN"]
            for col_idx, key in enumerate(keys, start=1):
                value = attributes.get(key, 0)  # Default to 0 if key is missing

                if key == "BALANCE":
                    # Round balance and apply conditional formatting
                    value = round(float(value))
                    balance_item = QTableWidgetItem(str(value))
                    balance_item.setFlags(Qt.ItemIsEnabled)  # Non-editable
                    balance_item.setTextAlignment(Qt.AlignCenter)  # Center-align value
                    if value < 0:
                        balance_item.setForeground(QColor("red"))
                        font = balance_item.font()
                        font.setBold(True)
                        balance_item.setFont(font)
                    self.stockpile_table.setItem(row_idx, col_idx, balance_item)
                else:
                    # Round grade values to 2 decimal points
                    value = round(float(value), 2) if value else 0
                    grade_item = QTableWidgetItem(f"{value:.2f}")
                    grade_item.setFlags(Qt.ItemIsEnabled)  # Non-editable
                    grade_item.setTextAlignment(Qt.AlignCenter)  # Center-align value
                    self.stockpile_table.setItem(row_idx, col_idx, grade_item)

            # Reclaim Threshold (Editable, Center-aligned)
            reclaim_value = attributes.get("reclaim_threshold", 0)
            reclaim_value = round(float(reclaim_value))  # Ensure reclaim threshold is rounded
            reclaim_item = QTableWidgetItem(str(reclaim_value))
            reclaim_item.setTextAlignment(Qt.AlignCenter)
            self.stockpile_table.setItem(row_idx, len(headers) - 1, reclaim_item)

        # Resize Columns
        self.stockpile_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.stockpile_table.setEditTriggers(self.stockpile_table.AllEditTriggers)

        # Connect cellChanged signal to a slot for live formatting
        self.stockpile_table.cellChanged.connect(self.handle_cell_change)

        # Add Submit Button at the Bottom Left
        submit_button = QPushButton("Submit Reclaim Thresholds")
        submit_button.clicked.connect(self.store_reclaim_thresholds)

        # Align button to the bottom-left using layout
        button_layout = QHBoxLayout()
        button_layout.addWidget(submit_button)
        button_layout.addStretch()  # Push the button to the left

        self.stockpile_tab_layout.addLayout(button_layout)

    def handle_cell_change(self, row, column):
        """Handle live formatting for the Reclaim Threshold column."""
        headers = [
            "Stockpile Name",
            "Balance (WMT)",
            "Grade Fe",
            "Grade Si",
            "Grade Al",
            "Grade P",
            "Grade Mn",
            "Reclaim Threshold"
        ]

        if column == headers.index("Reclaim Threshold"):  # Check if the changed cell is in the Reclaim Threshold column
            reclaim_item = self.stockpile_table.item(row, column)
            balance_item = self.stockpile_table.item(row, headers.index("Balance (WMT)"))

            if reclaim_item and balance_item:
                try:
                    reclaim_value = float(reclaim_item.text())
                    balance_value = float(balance_item.text())

                    # Apply conditional formatting
                    if reclaim_value <= balance_value:
                        reclaim_item.setForeground(QColor("green"))
                    else:
                        reclaim_item.setForeground(QColor("red"))
                except ValueError:
                    # Ignore invalid inputs
                    reclaim_item.setForeground(QColor("black"))

    def store_reclaim_thresholds(self):
        """Store reclaim thresholds entered by the user and modifies types."""
        for row in range(self.stockpile_table.rowCount()):
            stockpile_name = self.stockpile_table.item(row, 0).text()
            reclaim_threshold = self.stockpile_table.item(row, 7) # Assuming last column index is 7
            
            if reclaim_threshold:
                self.stockpile_data[stockpile_name]["reclaim_threshold"] = reclaim_threshold.type()
            else:
                self.stockpile_data[stockpile_name]["reclaim_threshold"] = 0
            
        
        self.stockpile_data = {
    key.upper(): {
        nested_key.lower(): (nested_value.lower() if nested_key.lower() == "name" and isinstance(nested_value, str) else nested_value)
        for nested_key, nested_value in value.items()
    }
    for key, value in self.stockpile_data.items()
}

       
        # Enable the next tab (Calendar Tab)
        self.setup_calendar()
        self.tabs.setTabEnabled(2, True)
        self.tabs.setCurrentIndex(2)  # Switch to Calendar tab
    
    def setup_calendar(self):
        """Setup for the main table with a Submit button."""
        headers = ["", "Preplan", "Period_1", "Period_2"]  # Column headers
        rows = []

        # Static Rows with default values of 0

        rows.extend([
            ("Reclaim Equipment", [False, False, False], "green", ["", "", ""]),
            ("  Max Reclaim Rate", [True, True, True], "green", ["1000", "1000", "1000"]),

            ("Crusher", [False, False, False], "blue", ["", "", ""]),
            ("  Rate", [True, True, True], "blue", ["1000", "1000", "1000"]),

            ("  Target", [False, False, False], "blue", ["", "", ""]),
            ("    Fe", [False, False, False], "blue", ["", "", ""]),
            ("      Min", [True, True, True], "blue", ["0", "0", "0"]),
            ("      Max", [True, True, True], "blue", ["100", "100", "100"]),

            ("    Si", [False, False, False], "blue", ["", "", ""]),
            ("      Min", [True, True, True], "blue", ["0", "0", "0"]),
            ("      Max", [True, True, True], "blue", ["100", "100", "100"]),

            ("    Al", [False, False, False], "blue", ["0", "0", "0"]),
            ("      Min", [True, True, True], "blue", ["0", "0", "0"]),
            ("      Max", [True, True, True], "blue", ["100", "100", "100"]),

            ("    P", [False, False, False], "blue", ["0", "0", "0"]),
            ("      Min", [True, True, True], "blue", ["0", "0", "0"]),
            ("      Max", [True, True, True], "blue", ["100", "100", "100"]),

            ("    Mn", [False, False, False], "blue", ["0", "0", "0"]),
            ("      Min", [True, True, True], "blue", ["0", "0", "0"]),
            ("      Max", [True, True, True], "blue", ["100", "100", "100"]),
        ])

        # Dynamically Add Stockpile Rows with default values
        rows.append(("Stockpiles", [False, False, False], "red", ["", "", ""]))

        for stockpile in self.stockpile_data_keys:
            rows.append((f"  {stockpile}", [False, False, False], "red", ["", "", ""]))
            rows.append((f"    State", [True, True, True], "red", ["Auto", "Auto", "Auto"]))
            rows.append((f"    Maximum Quantity", [True, True, True], "red", ["100000", "100000", "100000"]))
            rows.append((f"    Cost", [True, True, True], "red", ["0", "0", "0"]))
            rows.append((f"    Cash", [True, True, True], "red", ["10", "10", "10"]))

        # Define Parent Colors
        parent_colors = {
            "green": QColor(200, 255, 200),
            "blue": QColor(200, 200, 255),
            "red": QColor(255, 200, 200),
        }

        # Configure the main table
        self.main_table.setColumnCount(len(headers))
        self.main_table.setRowCount(len(rows))
        self.main_table.setHorizontalHeaderLabels(headers)
        self.main_table.verticalHeader().setVisible(False)

        # Bold Font for Captions
        bold_font = QFont()
        bold_font.setBold(True)

        # Populate Table
        for row_idx, (caption, editables, color_group, default_values) in enumerate(rows):
            # Caption Column
            item_caption = QTableWidgetItem(caption)
            item_caption.setFlags(Qt.ItemIsEnabled)  # Non-editable
            item_caption.setFont(bold_font)
            item_caption.setBackground(QBrush(parent_colors[color_group]))  # Parent group color
            self.main_table.setItem(row_idx, 0, item_caption)

            # Editable and Non-Editable Cells with Default Values
            for col_idx, (is_editable, default_value) in enumerate(zip(editables, default_values), start=1):
                item = QTableWidgetItem(str(default_value))
                item.setTextAlignment(Qt.AlignCenter)  # Center align all values
                if not is_editable:
                    item.setFlags(Qt.ItemIsEnabled)  # Non-editable
                    item.setBackground(QBrush(QColor(200, 200, 200)))  # Grey background
                self.main_table.setItem(row_idx, col_idx, item)

        # Resize Columns
        self.main_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.main_table.setEditTriggers(self.main_table.AllEditTriggers)

        # Add Submit Button at Bottom-Right
        submit_button = QPushButton("Submit")
        submit_button.clicked.connect(self.store_calendar_inputs)  # Connect to store_calendar_inputs method

        # Align button to bottom-right
        button_layout = QHBoxLayout()
        button_layout.addWidget(submit_button)  # Add the button first to keep it aligned to the left
        button_layout.addStretch()  # Push any other content (if any) to the right

        # Add table and button layout to the main tab layout
        self.main_tab_layout.addLayout(button_layout)

    def store_calendar_inputs(self):
        """Extract and store user entries from the table into a structured format with concatenated keys and modified types. Also calls the main optimised run"""
        self.calendar_inputs = {}

        # Capture column headers for periods
        headers = [self.main_table.horizontalHeaderItem(col).text().strip() for col in range(1, self.main_table.columnCount())]

        # A stack to track the current hierarchy
        hierarchy = []

        for row_idx in range(self.main_table.rowCount()):
            # Get the caption for the row (e.g., "Crusher", "  Rate")
            caption_item = self.main_table.item(row_idx, 0)
            if not caption_item:
                continue  # Skip if no caption exists (shouldn't happen)

            caption = caption_item.text()

            # Adjust hierarchy based on indentation
            indent_level = (len(caption) - len(caption.lstrip()))/2
            while len(hierarchy) > indent_level:
                hierarchy.pop()

            # Add the current name to the hierarchy
            current_name = caption.strip().replace(" ", "_").lower()
            hierarchy.append(current_name)

            # Generate the full key by joining the hierarchy
            full_key = "_".join(hierarchy)

            # Retrieve the values for Preplan, Period_1, Period_2, etc.
            row_data = {}
            for col_idx, header in enumerate(headers, start=1):
                item = self.main_table.item(row_idx, col_idx)
                value = item.text().strip() if item and item.text().strip() else None  # Get the value
                row_data[header] = value

            # Store the data in the dictionary
            self.calendar_inputs[full_key] = row_data

        # Modify types
        self.calendar_inputs = {
    outer_key: {
        inner_key: (
            int(inner_value) if inner_value is not None and "state" not in outer_key.lower() else inner_value
        )
        for inner_key, inner_value in outer_value.items()
    }
    for outer_key, outer_value in self.calendar_inputs.items()
}

       
        # Call main optimised run
        self.run_program.execute(self.start_time, self.expit_mode, self.file_path, self.blend_mode, self.stockpile_data, self.calendar_inputs)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = UserInputs()  # Create an instance of the imported class
    window.show()              # Show the GUI
    sys.exit(app.exec_())      # Run the event loop


