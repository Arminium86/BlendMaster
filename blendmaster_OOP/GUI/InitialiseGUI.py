import sys, threading, requests, time
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QTabWidget,
    QFormLayout, QLineEdit, QPushButton, QComboBox, QHBoxLayout, QLabel, QMessageBox, QDateTimeEdit, QFileDialog, QTextEdit, QFrame, QAbstractItemView, QCheckBox
)

from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineDownloadItem
from PyQt5.QtGui import QColor, QBrush, QFont
from PyQt5.QtCore import Qt, QUrl, QTimer
from setup.OpeningStockpileInventories import OpeningStockpileInventories
from execute.Run import Run
from datetime import datetime
from GUI.DrawCharts import DrawGanttChart, DrawStockProfiles
import pandas as pd, sqlite3

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
        self.tabs.addTab(self.stockpile_tab, "Stockpile Inventories")
        self.stockpile_tab_layout = QVBoxLayout(self.stockpile_tab)

        # Stockpile Table
        self.stockpile_table = CustomTableWidget()
        self.stockpile_tab_layout.addWidget(self.stockpile_table)

        # Add calendar Tab
        self.main_tab = QWidget()
        self.tabs.addTab(self.main_tab, "Calendar")
        self.main_tab_layout = QVBoxLayout(self.main_tab)
        self.calendar_inputs = {}

        # Calendar table
        self.main_table = CustomTableWidget()
        self.main_tab_layout.addWidget(self.main_table)

        # Add Decision Point Tab
        self.decision_point_tab = QWidget()
        self.tabs.addTab(self.decision_point_tab, "Decision Point")
        self.decision_point_tab_layout = QVBoxLayout(self.decision_point_tab)

        # Create the table widget for the DataFrame
        self.decision_table = CustomTableWidget()
        self.decision_point_tab_layout.addWidget(self.decision_table)  # Add table at the top

        # Text output area
        self.decision_output = QTextEdit()
        self.decision_output.setReadOnly(True)
        self.decision_point_tab_layout.addWidget(self.decision_output)  # Add text in the middle

        # Input layout (field + button)
        input_layout = QHBoxLayout()
        self.decision_input = QLineEdit()
        self.decision_input.setPlaceholderText("Enter your input here...")
        self.decision_input.returnPressed.connect(self.handle_decision_input)
        input_layout.addWidget(self.decision_input)

        self.enter_button = QPushButton("Enter")
        self.enter_button.clicked.connect(self.handle_decision_input)
        input_layout.addWidget(self.enter_button)

        self.decision_point_tab_layout.addLayout(input_layout)  # Add input field and button at the bottom

        # Add Results and Profiles Tab
        self.setup_results_tab()

        self.setup_profiles_tab()

        # Add Setup Blends tab
        self.blend_config_tab = QWidget()
        self.tabs.addTab(self.blend_config_tab, "Setup Blends (Manual)")
        self.setup_blends_tab_layout = QVBoxLayout(self.blend_config_tab)

        # Add Sequence tab
        self.blend_sequence_tab = QWidget()
        self.tabs.addTab(self.blend_sequence_tab, "Blend Sequence (Manual Gantt)")
        self.blend_sequence_tab_layout = QVBoxLayout(self.blend_sequence_tab)

         # Add the CustomWebEngineView at the top to display the Dash app
        self.manual_gantt_view = CustomWebEngineView()
        self.blend_sequence_tab_layout.addWidget(self.manual_gantt_view)
        
        # Add the horizontal layout for the two tables at the bottom
        self.blend_sequence_table_layout = QHBoxLayout()

        # Left Table: Blend Results Table (View Only) - Create a new instance
        self.blend_results_table_view = CustomTableWidget()
        self.blend_results_table_view.setEditTriggers(QTableWidget.NoEditTriggers)  # Make uneditable

        # Add the new table to the layout
        self.blend_sequence_table_layout.addWidget(self.blend_results_table_view)

        # Right Table: Placeholder Table for real-time updates
        self.blend_sequence_table = CustomTableWidget()
        self.blend_sequence_table_layout.addWidget(self.blend_sequence_table)

        # Add the horizontal layout to the main layout of the tab
        self.blend_sequence_tab_layout.addLayout(self.blend_sequence_table_layout)

        # Workflow controls
        self.load_profiles_first_call = True
        self.setup_blends_tab_first_call = True

        # Disable tabs initially
        self.tabs.setTabEnabled(1, False)  # Disable Stockpile tab
        self.tabs.setTabEnabled(2, False)  # Disable Calendar tab
        self.tabs.setTabEnabled(3, False)  # Disable Decision tab
        self.tabs.setTabEnabled(4, False)  # Disable Results tab
        self.tabs.setTabEnabled(5, False)  # Disable Profiles tab
        self.tabs.setTabEnabled(6, False)  # Disable Setup Blends tab
        self.tabs.setTabEnabled(7, False)  # Disable Blend Sequence tab


        # Initialise main program
        self.run_program = Run(self)

    def setup_site_configuration(self):
        """Setup for the Site Configuration Form."""
        self.site_config_tab = QWidget()
        self.tabs.addTab(self.site_config_tab, "Site Configuration")
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
        file_label = QLabel("Select APS Mining.csv:")
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

        # --- Input 4: Optimised Blend Choices ---
        blend_label = QLabel("Optimised Blend Choices:")
        blend_label.setStyleSheet("font-weight: bold;")
        self.blend_mode = QComboBox()
        self.blend_mode.addItems(["Select Best Result Automatically", "Prompt User at Decision Point"])
        self.blend_mode.setFixedWidth(300)

        layout.addRow(blend_label, self.blend_mode)

         # Submit Button
        self.submit_button = QPushButton("Submit")
        self.submit_button.setFixedWidth(100)
        self.submit_button.setEnabled(False)  # Initially disabled

        self.submit_button.clicked.connect(self.handle_site_config_submit)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.submit_button)
        button_layout.addStretch()

        layout.addRow(button_layout)

        # Connect input field changes to form validation
        self.hub_input.currentIndexChanged.connect(self.validate_form)
        self.mine_input.currentIndexChanged.connect(self.validate_form)
        self.time_mode.currentIndexChanged.connect(self.validate_form)
        self.start_time.dateTimeChanged.connect(self.validate_form)
        self.file_path.textChanged.connect(self.validate_form)
        self.blend_mode.currentIndexChanged.connect(self.validate_form)

    def validate_form(self):
        """Enable or disable the submit button based on form completion."""
        all_fields_populated = (
            self.hub_input.currentIndex() != -1
            and self.mine_input.currentIndex() != -1
            and (self.time_mode.currentIndex() == 0 or self.start_time.dateTime().isValid())
            and bool(self.file_path.text())
            and self.blend_mode.currentIndex() != -1
        )
        self.submit_button.setEnabled(all_fields_populated)

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
        
        self.hub_input = self.hub_input.currentText().strip()
        self.mine_input = self.mine_input.currentText().strip()

        if self.hub_input and self.mine_input and self.time_mode and self.start_time and self.expit_mode and self.file_path and self.blend_mode: 
            QMessageBox.information(self, "Site Configuration Form", f"Configuration successfully submitted for Hub: {self.hub_input}, Mine: {self.mine_input}.")
            
            # Fetch stockpile data and create setup task
            self.fetch_stockpile_data()
            self.setup_stockpile_table()
            self.tabs.setTabEnabled(1, True)
            self.tabs.setCurrentIndex(1)  # Switch to the next tab

        else:
            QMessageBox.warning(self, "Missing Information", "Please fill in all fields.")

    def fetch_stockpile_data(self):
        """Fetch stockpile data from OpeningStockpileInventories."""
        hub_input = self.hub_input
        mine_input = self.mine_input
        self.stockpile_data = self.opening_stockpile_inventories.call_opening_stockpile_inventories(hub_input, mine_input, self.start_time)
    
    def setup_stockpile_table(self):
        """Setup for the stockpile table in the new Stockpiles tab with live conditional formatting."""
        # Define Headers (Add "Use" Column)
        headers = [
            "Use",
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

        # Choose data source based on calendar_inputs
        data_source = self.updated_stockpile_data if self.calendar_inputs else self.stockpile_data

        # Set Table Dimensions
        self.stockpile_table.setRowCount(len(self.stockpile_data))

        # Populate Stockpile Data
        for row_idx, (stockpile_name, attributes) in enumerate(data_source.items()):
            # "Use" Column (Checkbox)
            use_checkbox = QCheckBox()
            use_checkbox.setChecked(True)  # Default to checked

            # Center the checkbox using a QWidget and layout
            checkbox_widget = QWidget()
            layout = QHBoxLayout(checkbox_widget)
            layout.addWidget(use_checkbox)
            layout.setAlignment(Qt.AlignCenter)  # Center the checkbox
            layout.setContentsMargins(0, 0, 0, 0)  # Remove any extra padding
            self.stockpile_table.setCellWidget(row_idx, 0, checkbox_widget)

            # Stockpile Name (Center-align)
            stockpile_item = QTableWidgetItem(str(stockpile_name))
            stockpile_item.setFlags(Qt.ItemIsEnabled)  # Non-editable
            stockpile_item.setTextAlignment(Qt.AlignCenter)  # Center-align the stockpile name
            self.stockpile_table.setItem(row_idx, 1, stockpile_item)

            # Attributes (Balance and Grades, Center-aligned)
            keys = ["BALANCE", "GRADE_FE", "GRADE_SI", "GRADE_AL", "GRADE_P", "GRADE_MN"]
            for col_idx, key in enumerate(keys, start=2):  # Start after "Use" and "Stockpile Name"
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

        # Connect cellChanged signal to a slot for live formatting
        self.stockpile_table.cellChanged.connect(self.handle_cell_change)

        # Add Submit Button at the Bottom
        submit_button = QPushButton("Submit")
        submit_button.clicked.connect(self.store_stockpile_table)

        # Align button to the bottom-left using layout
        button_layout = QHBoxLayout()
        
        # Don't add the button if returning from the calendar
        if not self.calendar_inputs:
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

    def store_stockpile_table(self):
        """Store stockpile details entered by the user, filtering by the 'Use' column."""
        updated_stockpile_data = {}

        for row in range(self.stockpile_table.rowCount()):
            # Check if "Use" column checkbox is checked
            checkbox_widget = self.stockpile_table.cellWidget(row, 0)  # Get the widget in the "Use" column
            if checkbox_widget:
                checkbox = checkbox_widget.layout().itemAt(0).widget()  # Extract the QCheckBox
                if checkbox.isChecked():  # Check if the checkbox is checked
                    stockpile_name = self.stockpile_table.item(row, 1).text()

                    # Retrieve Reclaim Threshold
                    reclaim_item = self.stockpile_table.item(row, self.stockpile_table.columnCount() - 1)
                    reclaim_threshold = float(reclaim_item.text()) if reclaim_item else 0

                    # Update stockpile data
                    updated_stockpile_data[stockpile_name] = self.stockpile_data.get(stockpile_name, {})
                    updated_stockpile_data[stockpile_name]["reclaim_threshold"] = reclaim_threshold

                
        # Update stockpile_data with filtered data
        self.updated_stockpile_data = updated_stockpile_data
        self.updated_stockpile_data_keys = updated_stockpile_data.keys()

        self.updated_stockpile_data = {
            key.upper(): {
                nested_key.lower(): (nested_value.lower() if nested_key.lower() == "name" and isinstance(nested_value, str) else nested_value)
                for nested_key, nested_value in value.items()
            }
            for key, value in self.updated_stockpile_data.items()
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

            ("    Al", [False, False, False], "blue", ["", "", ""]),
            ("      Min", [True, True, True], "blue", ["0", "0", "0"]),
            ("      Max", [True, True, True], "blue", ["100", "100", "100"]),

            ("    P", [False, False, False], "blue", ["", "", ""]),
            ("      Min", [True, True, True], "blue", ["0", "0", "0"]),
            ("      Max", [True, True, True], "blue", ["100", "100", "100"]),

            ("    Mn", [False, False, False], "blue", ["", "", ""]),
            ("      Min", [True, True, True], "blue", ["0", "0", "0"]),
            ("      Max", [True, True, True], "blue", ["100", "100", "100"]),
        ])

        # Dynamically Add Stockpile Rows with default values
        rows.append(("Stockpiles", [False, False, False], "red", ["", "", ""]))

        for stockpile in self.updated_stockpile_data_keys:
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

        # Add Submit Button at bottom-Right
        submit_button = QPushButton("Submit")
        submit_button.clicked.connect(self.store_calendar_inputs)  # Connect to store_calendar_inputs method

        # Align button to bottom-right
        button_layout = QHBoxLayout()
        
        # Check if the button already exists in the layout
        if not self.calendar_inputs:
          button_layout.addWidget(submit_button) 
          button_layout.addStretch()  # Push any other content (if any) to the right

        # Add table and button layout to the main tab layout
        self.main_tab_layout.addLayout(button_layout)

    def store_calendar_inputs(self):
        """Extract and store user entries from the table into a structured format with concatenated keys and modified types. Also calls the main optimised run"""
        self.calendar_inputs = {}

        # Connect the error signal to a popup display method
        self.run_program.case_bridge.error_signal.connect(self.show_error_popup)
        
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
        
        # Initialise the shared object and stop event
        status = {'success': False}
        stop_event = threading.Event()

        # Create and start the thread
        execution_thread = threading.Thread(
            target=self.execute_run_program_in_thread, args=(status, stop_event)
        )
        execution_thread.start()

        # Wait for the thread to complete with a 3-minute timeout
        execution_thread.join(timeout=3 * 60)  # 3 minutes in seconds

        # Check if the thread is still alive after the timeout
        if execution_thread.is_alive():
            QMessageBox.critical(None, "Timeout", "Optimisation thread timed out. Signaling to stop.")
            stop_event.set()  # Signal the thread to stop (for graceful handling)

            # Optionally, perform any forced cleanup actions if needed
            execution_thread.join()  # Wait for thread to exit gracefully

        # Conditional logic based on whether the execution was successful
        if status['success']:
            self.update_decision_point_tab_state()
            self.setup_blends_tab()
            self.tabs.setTabEnabled(6, True)
            self.start_dash_optimised_charts_thread()
        else:
            # Handle alternative flow if an exception occurred or timeout
            self.tabs.setCurrentIndex(1)
            self.setup_stockpile_table()

    def execute_run_program_in_thread(self, status, stop_event):
        try:
            # Call main optimised run
            self.run_program.execute(
                self.start_time,
                self.expit_mode,
                self.file_path,
                self.blend_mode,
                self.updated_stockpile_data,
                self.calendar_inputs
            )
            if not stop_event.is_set():  # If not stopped, mark as success
                status['success'] = True
        except Exception as e:
            # Handle or log the error
            self.run_program.case_bridge.error_signal.emit(str(e))  # Emit the error to show in the popup
            status['success'] = False

    def setup_results_tab(self):
        self.results_tab = QWidget()
        self.tabs.addTab(self.results_tab, "Results (Optimised)")
        self.results_layout = QVBoxLayout(self.results_tab)

        # Create a QFrame
        self.top_frame = QFrame()
        self.top_frame.setFrameStyle(QFrame.Box | QFrame.Plain)  # Set a plain box-style frame
        self.top_frame.setLineWidth(2)  # Set the frame's border width
        self.top_frame.setStyleSheet("border-color: black;")  # Optional: Set border color

        # Add layout to the frame
        self.top_layout = QHBoxLayout()
        self.top_frame.setLayout(self.top_layout)

        # Add the frame to the parent layout
        self.results_layout.addWidget(self.top_frame)

        # Top (Gantt Chart with CustomWebEngineView)
        self.gantt_chart_view = CustomWebEngineView()
        self.gantt_chart_view.setStyleSheet("border: 1px solid black;")
        self.top_layout.addWidget(self.gantt_chart_view)

        # Add a button to load the chart
        self.load_chart_button = QPushButton("Load or Update Chart")
        self.load_chart_button.setFixedWidth(200)
        self.load_chart_button.setStyleSheet("font-size: 16px; padding: 8px;")  # Optional: Style the button
        self.load_chart_button.clicked.connect(self.load_gantt_chart)  # Connect button to function

        # Add the button to the layout (you can position it as needed)
        self.results_layout.addWidget(self.load_chart_button)

    def load_gantt_chart(self):
        # Load the Dash app into the QWebEngineView
        self.gantt_chart_view.setUrl(QUrl("http://localhost:8050"))
        
    def setup_profiles_tab(self):
        self.profiles_tab = QWidget()
        self.tabs.addTab(self.profiles_tab, "Stockpile Profiles (Optimised)")
        self.profiles_layout = QVBoxLayout(self.profiles_tab)

        # Create the bottom frame
        self.bottom_frame = QFrame()
        self.bottom_frame.setFrameStyle(QFrame.Box | QFrame.Plain)  # Set a plain box-style frame
        self.bottom_frame.setLineWidth(2)  # Set the frame's border width
        self.bottom_frame.setStyleSheet("border-color: black;")  # Optional: Set border color

        # Add layout to the frame
        self.bottom_layout = QHBoxLayout()
        self.bottom_frame.setLayout(self.bottom_layout)

        # Add the frame to the parent layout
        self.profiles_layout.addWidget(self.bottom_frame)

        # Bottom Section (Stockpile Profiles Chart Placeholder)
        self.stockpile_profile_chart_view = CustomWebEngineView()  # Embed the Dash app
        self.stockpile_profile_chart_view.setStyleSheet("border: 1px solid black;")
        self.bottom_layout.addWidget(self.stockpile_profile_chart_view)

        # Add a button to load the chart
        self.load_profile_chart_button = QPushButton("Load or Update Chart")
        self.load_profile_chart_button.setFixedWidth(200)
        self.load_profile_chart_button.setStyleSheet("font-size: 16px; padding: 8px;")  # Smaller button
        self.load_profile_chart_button.clicked.connect(self.load_profiles)  # Connect button to function

        # Add the button to the layout at the bottom-left
        self.profiles_layout.addWidget(self.load_profile_chart_button)

    def load_profiles(self):

        if not self.load_profiles_first_call:
       
            # Send a request to trigger the refresh
            try:
                requests.post("http://localhost:8051/trigger-refresh", timeout=5)  # Timeout after 5 seconds
            except requests.exceptions.Timeout:
                QMessageBox.critical(None, "Timeout", "The server did not respond in time.")
            except requests.exceptions.RequestException as e:
                QMessageBox.critical(None, "Error", f"Failed to trigger refresh: {e}")

        # Load the Dash app into the QWebEngineView
        self.stockpile_profile_chart_view.setUrl(QUrl("http://localhost:8051"))

        self.load_profiles_first_call = False

    def start_dash_optimised_charts_thread(self):
        """Start the Dash app in a separate thread."""
        db_path = "blendmaster.db"
        self.draw_gantt_chart = DrawGanttChart(db_path, port=8050)
        self.draw_stockpile_profile_chart = DrawStockProfiles(db_path, port=8051)
        
        # Use a thread to run the Dash app server
        self.dash_thread_gantt = threading.Thread(target=self.draw_gantt_chart.run_app, daemon=True)
        self.dash_thread_gantt.start()

        self.dash_thread_stockpile_profile = threading.Thread(target=self.draw_stockpile_profile_chart.run_app, daemon=True)
        self.dash_thread_stockpile_profile.start()
    
    def update_decision_point_tab_state(self):
        """Enable or disable the Decision Point tab based on blend_mode."""
        if self.blend_mode == 2:
            self.tabs.setTabEnabled(3, True)
            self.tabs.setCurrentIndex(3)
            self.tabs.setTabEnabled(4, True)  # Enable Results (optimised) tab
            self.tabs.setTabEnabled(5, True)  # Enable profiles tab

        else:
            self.tabs.setTabEnabled(3, True)
            self.tabs.setTabEnabled(4, True)  # Enable Results (optimised) tab
            self.tabs.setTabEnabled(5, True)  # Enable profiles tab
            self.tabs.setCurrentIndex(4)  # Switch to Results (optimised) tab

    def handle_decision_input(self):
        """Send input from the Decision Point tab to the CaseModellerBridge."""
        user_input = self.decision_input.text()

        # Validate the input
        try:
            user_input_int = int(user_input)  # Check if input is an integer
            max_blend_option = self.max_blend_option  # Max blend_ID from the DataFrame
            if 1 <= user_input_int <= max_blend_option:
                # Input is valid, send it to CaseModellerBridge
                self.run_program.case_bridge.send_input(user_input)
                self.decision_input.clear()
            else:
                raise ValueError(f"Input must be between 1 and {max_blend_option}")
        except ValueError as e:
            # Display an error message in the decision_output text area
            self.display_decision_output(f"Invalid input: {e}")

    def display_decision_output(self, message):
        """Display messages from CaseModeller in the Decision Point tab."""
        self.decision_output.append(message)

    def display_decision_dataframe(self, df):
        """Display a DataFrame in a QTableWidget with custom styles."""
        # Clear the table instead of removing/recreating it
        self.decision_table.clearContents()
        self.decision_table.setRowCount(0)
        self.decision_table.setColumnCount(0)

        # Populate the table with new data
        self.decision_table.setRowCount(len(df))
        self.decision_table.setColumnCount(len(df.columns))
        self.decision_table.setHorizontalHeaderLabels(df.columns)
        
        # Store number of rows for data validation in handle_decision_input
        self.max_blend_option = df['blend_option'].max()

        # Set font for the table
        font = QFont("Segoe UI", 10)  # Set font name and size
        self.decision_table.setFont(font)

        # Customize the headers
        header_font = QFont("Segoe UI", 10, QFont.Bold)  # Bold font for headers
        self.decision_table.horizontalHeader().setFont(header_font)

        # Increase column width
        self.decision_table.horizontalHeader().setDefaultSectionSize(150)  # Set default column width
        self.decision_table.horizontalHeader().setStretchLastSection(False)  # Optional: Stretch the last column

        # Populate the table with DataFrame content
        for row_idx, row in enumerate(df.itertuples(index=False)):
            for col_idx, value in enumerate(row):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignCenter)  # Center-align cell content
                self.decision_table.setItem(row_idx, col_idx, item)

        # Resize columns to fit content 
        self.decision_table.resizeColumnsToContents()
    
    def show_error_popup(self, error_message):
        """Display an error message in a popup."""
        QMessageBox.critical(self, "Error", error_message)
    
    def setup_blends_tab(self):
        
        if self.setup_blends_tab_first_call:
        
            self.crusher_rate = 1000  # Default crusher rate

            # Crusher rate input
            self.crusher_rate_input = QLineEdit()
            self.crusher_rate_input.setText("1000")
            self.crusher_rate_input.textChanged.connect(self.on_blend_data_change)

            # Set a fixed width for the input field
            self.crusher_rate_input.setFixedWidth(100)  # Adjust the width as needed

            crusher_label = QLabel("Crusher Rate:")
            crusher_font = crusher_label.font()
            crusher_font.setBold(True)
            crusher_label.setFont(crusher_font)
            
            crusher_layout = QHBoxLayout()
            crusher_layout.addWidget(crusher_label)
            crusher_layout.addWidget(self.crusher_rate_input)
            crusher_layout.addStretch()  # Add stretch to align inputs neatly

            self.setup_blends_tab_layout.addLayout(crusher_layout)

            # Blend configuration table
            blend_config_label = QLabel("Blend Configuration")
            blend_config_label.setAlignment(Qt.AlignLeft)  # Center-align the caption
            blend_config_label.setStyleSheet("font-weight: bold; font-size: 22px;")  # Optional: Styling for the label
            self.setup_blends_tab_layout.addWidget(blend_config_label)

            self.blend_config_table = CustomTableWidget()
            self.setup_blend_config_table()
            self.setup_blends_tab_layout.addWidget(self.blend_config_table)

            # Blend results table
            blend_results_label = QLabel("Blend Results")
            blend_results_label.setAlignment(Qt.AlignLeft)  # Center-align the caption
            blend_results_label.setStyleSheet("font-weight: bold; font-size: 22px;")  # Optional: Styling for the label
            self.setup_blends_tab_layout.addWidget(blend_results_label)

            self.blend_results_table = CustomTableWidget()
            self.setup_blend_results_table()
            self.setup_blends_tab_layout.addWidget(self.blend_results_table)

            # Create Submit Button
            submit_button = QPushButton("Submit")
            submit_button.clicked.connect(self.store_blend_results)

            # Align button to the bottom-left using layout
            button_layout = QHBoxLayout()
            button_layout.addWidget(submit_button)
            button_layout.addStretch()  # Push the button to the left

            self.setup_blends_tab_layout.addLayout(button_layout)

            self.setup_blends_tab_first_call = False

    def setup_blend_config_table(self):
        headers = [
            "Stockpile Name", "Balance (WMT)", "Projected Balance (WMT)", "Projected Last Payload Delivered",
            "Use Projected Balance", "Grade Fe", "Grade Si", "Grade Al", "Grade P", "Grade Mn",
            "Blend ID", "Weight", "Reclaim Rate", "Source Ratio"
        ]
        self.blend_config_table.setColumnCount(len(headers))
        self.blend_config_table.setHorizontalHeaderLabels(headers)

        # Remove row indices
        self.blend_config_table.verticalHeader().setVisible(False)

        # Bold headers
        header_font = self.blend_config_table.horizontalHeader().font()
        header_font.setBold(True)
        self.blend_config_table.horizontalHeader().setFont(header_font)

        # Populate blend config table
        self.populate_blend_config_table()

        # Format columns
        self.format_blend_config_table()

        # Connect cell changes to trigger updates
        self.blend_config_table.cellChanged.connect(self.update_reclaim_rate)
        
        # Connect edits to update results
        self.blend_config_table.itemChanged.connect(self.on_blend_data_change)     

    def populate_blend_config_table(self):
        # Fetch build report data
        build_report_df = self.fetch_build_report()

        self.blend_config_table.setRowCount(len(self.updated_stockpile_data))

        for row_idx, (stockpile_name, attributes) in enumerate(self.updated_stockpile_data.items()):
            # Stockpile Name (Bold Content)
            stockpile_item = QTableWidgetItem(stockpile_name)
            stockpile_item.setFlags(Qt.ItemIsEnabled)
            stockpile_item.setTextAlignment(Qt.AlignCenter)

            font = stockpile_item.font()
            font.setBold(True)
            stockpile_item.setFont(font)

            self.blend_config_table.setItem(row_idx, 0, stockpile_item)

            # Attributes
            keys = [key.lower() for key in ["BALANCE", "GRADE_FE", "GRADE_SI", "GRADE_AL", "GRADE_P", "GRADE_MN"]]
            for key in keys:
                # Safely get the value, default to 0 if None
                value = attributes.get(key, 0) or 0

                if key == "balance":
                    col_idx = 1  # Balance column
                    item = QTableWidgetItem(f"{float(value):.0f}")  # Format as integer (no decimals)
                else:
                    col_idx = keys.index(key) + 4  # Offset for additional columns
                    item = QTableWidgetItem(f"{float(value):.2f}")  # Format as float (2 decimals)

                item.setFlags(Qt.ItemIsEnabled)
                item.setTextAlignment(Qt.AlignCenter)
                self.blend_config_table.setItem(row_idx, col_idx, item)

            # Projected Balance and Last Payload Delivered
            projected_balance_item = QTableWidgetItem("")
            last_payload_item = QTableWidgetItem("")
            projected_balance_item.setFlags(Qt.ItemIsEnabled)
            last_payload_item.setFlags(Qt.ItemIsEnabled)
            projected_balance_item.setTextAlignment(Qt.AlignCenter)
            last_payload_item.setTextAlignment(Qt.AlignCenter)

            # Check if the stockpile is in the build report
            stockpile_records = build_report_df[build_report_df["stockpile"] == stockpile_name]
            if not stockpile_records.empty:
                latest_record = stockpile_records.sort_values("delivered_datetime", ascending=False).iloc[0]
                projected_balance_item.setText(str(latest_record["closing_balance"]))
                last_payload_item.setText(str(latest_record["delivered_datetime"]))

            self.blend_config_table.setItem(row_idx, 2, projected_balance_item)  # Projected Balance
            self.blend_config_table.setItem(row_idx, 3, last_payload_item)  # Last Payload Delivered

            # Use Projected Balance (Checkbox)
            use_projected_checkbox = QCheckBox()
            use_projected_checkbox.setEnabled(projected_balance_item.text() != "")  # Enable only if projected balance exists
            use_projected_checkbox.stateChanged.connect(self.update_blend_results)  # Update results on state change

            checkbox_widget = QWidget()
            checkbox_layout = QHBoxLayout(checkbox_widget)
            checkbox_layout.addWidget(use_projected_checkbox)
            checkbox_layout.setAlignment(Qt.AlignCenter)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)
            self.blend_config_table.setCellWidget(row_idx, 4, checkbox_widget)

            # Blend ID (Dropdown with "None" default)
            blend_id_combo = QComboBox()
            blend_id_combo.addItems(["None"] + [str(i) for i in range(1, 6)])  # Add "None" option
            blend_id_combo.setCurrentText("None")  # Set default to "None"
            blend_id_combo.currentIndexChanged.connect(self.on_blend_data_change)
            self.blend_config_table.setCellWidget(row_idx, len(keys) + 4, blend_id_combo)  

            # Weight (Editable)
            weight_item = QTableWidgetItem("0")
            weight_item.setTextAlignment(Qt.AlignCenter)
            weight_item.setForeground(QColor("green"))
            self.blend_config_table.setItem(row_idx, len(keys) + 5, weight_item)  

            # Reclaim Rate (Auto-calculated)
            reclaim_item = QTableWidgetItem("0")
            reclaim_item.setFlags(Qt.ItemIsEnabled)  # Make it uneditable
            reclaim_item.setTextAlignment(Qt.AlignCenter)
            self.blend_config_table.setItem(row_idx, len(keys) + 6, reclaim_item)  

            # Source Ratio (Auto-calculated)
            ratio_item = QTableWidgetItem("0")
            ratio_item.setFlags(Qt.ItemIsEnabled)  # Make it uneditable
            ratio_item.setTextAlignment(Qt.AlignCenter)
            self.blend_config_table.setItem(row_idx, len(keys) + 7, ratio_item)  


        # Resize headers to fit content
        self.blend_config_table.resizeColumnsToContents()

    def on_blend_data_change(self):
        """Recalculate and update blend results whenever blend data changes."""
        self.update_reclaim_rate()  # Update reclaim rates
        self.update_blend_results()  # Refresh blend results table

    def update_reclaim_rate(self):
        """
        Calculate and update the Reclaim Rate for each stockpile based on its Blend ID and Weight.
        """
        try:
            self.crusher_rate = float(self.crusher_rate_input.text())
        except ValueError:
            self.crusher_rate = 1000  # Default crusher rate

        # Temporarily disconnect the signal to prevent recursion
        self.blend_config_table.blockSignals(True)

        try:
            # Initialize a dictionary to store total weights for each blend ID
            blend_weights = {str(i): 0 for i in range(1, 6)}

            # Step 1: Calculate total weights for each blend ID
            for row_idx in range(self.blend_config_table.rowCount()):
                try:
                    blend_id_combo = self.blend_config_table.cellWidget(row_idx, 10)  # Adjusted column index
                    weight_item = self.blend_config_table.item(row_idx, 11)  # Adjusted column index
                    blend_id = blend_id_combo.currentText()
                    if blend_id == "None":
                        continue
                    weight = float(weight_item.text())
                    blend_weights[blend_id] += weight
                except (ValueError, AttributeError):
                    continue

            # Step 2: Calculate and update Reclaim Rate for each stockpile
            for row_idx in range(self.blend_config_table.rowCount()):
                try:
                    blend_id_combo = self.blend_config_table.cellWidget(row_idx, 10)  
                    weight_item = self.blend_config_table.item(row_idx, 11)  
                    reclaim_item = self.blend_config_table.item(row_idx, 12)  
                    ratio_item = self.blend_config_table.item(row_idx, 13)  

                    blend_id = blend_id_combo.currentText()
                    if blend_id == "None":
                        if reclaim_item:
                            reclaim_item.setText("0")
                            ratio_item.setText("0.00")
                        continue

                    weight = float(weight_item.text())
                    total_weight = blend_weights.get(blend_id, 0)

                    # Calculate ratio and reclaim rate
                    if total_weight > 0:
                        ratio = weight / total_weight
                    else:
                        ratio = 0

                    reclaim_rate = ratio * self.crusher_rate

                    # Update the reclaim rate cell
                    if not reclaim_item:
                        reclaim_item = QTableWidgetItem()
                        self.blend_config_table.setItem(row_idx, 12, reclaim_item)
                        self.blend_config_table.setItem(row_idx, 13, ratio_item)


                    reclaim_item.setText(f"{reclaim_rate:.0f}")
                    reclaim_item.setFlags(Qt.ItemIsEnabled)  # Ensure non-editable
                    reclaim_item.setTextAlignment(Qt.AlignCenter)  # Center-align the text

                    ratio_item.setText(f"{ratio:.2f}")
                    ratio_item.setFlags(Qt.ItemIsEnabled)  # Ensure non-editable
                    ratio_item.setTextAlignment(Qt.AlignCenter)  # Center-align the text

                except (ValueError, AttributeError):
                    continue

        finally:
            # Reconnect the signal after updates are complete
            self.blend_config_table.blockSignals(False)

        # Optional: Resize columns to fit updated content
        self.blend_config_table.resizeColumnsToContents()

    def setup_blend_results_table(self):
        headers = [
            "Blend ID", "Grade Fe", "Grade Si", "Grade Al", "Grade P", "Grade Mn",
            "Balance (WMT)", "Max Duration (hrs)", "Available"
        ]
        self.blend_results_table.setColumnCount(len(headers))
        self.blend_results_table.setHorizontalHeaderLabels(headers)

        # Remove row indices
        self.blend_results_table.verticalHeader().setVisible(False)

        # Bold headers
        header_font = self.blend_results_table.horizontalHeader().font()
        header_font.setBold(True)
        self.blend_results_table.horizontalHeader().setFont(header_font)

        # Set up rows for each blend ID
        self.blend_results_table.setRowCount(5)
        self.update_blend_results()

        # Connect edits to update results
        self.blend_config_table.itemChanged.connect(self.on_blend_data_change)     

    def update_blend_results(self):
        """
        Recalculate and update the Blend Results Table, ensuring unused Blend IDs are cleared.
        """
        # Initialize a dictionary to aggregate data for each blend ID
        blend_data = {str(i): {"weights": [], "grades": [], "balances": [], "available": []} for i in range(1, 6)}

        # Aggregate data from blend configuration table
        for row_idx in range(self.blend_config_table.rowCount()):
            try:
                blend_id_combo = self.blend_config_table.cellWidget(row_idx, 10) # Column index for Blend ID
                blend_id = blend_id_combo.currentText()

                # Skip calculations if Blend ID is "None"
                if blend_id == "None":
                    continue

                weight = float(self.blend_config_table.item(row_idx, 11).text()) # Column index for Weight
                use_projected = (
                    self.blend_config_table.cellWidget(row_idx, 4)
                    .layout()
                    .itemAt(0)
                    .widget()
                    .isChecked()
                )
                balance = (
                    float(self.blend_config_table.item(row_idx, 2).text())  # Projected Balance
                    if use_projected
                    else float(self.blend_config_table.item(row_idx, 1).text())  # Actual Balance
                )
                available = (
                    self.blend_config_table.item(row_idx, 3).text()  # Last Payload Delivered
                    if use_projected
                    else "Now"
                )

                grades = [float(self.blend_config_table.item(row_idx, col).text()) for col in range(5, 10)]
                blend_data[blend_id]["weights"].append(weight)
                blend_data[blend_id]["grades"].append([grade * weight for grade in grades])
                blend_data[blend_id]["balances"].append(balance * weight)
                blend_data[blend_id]["available"].append(available)
            except (ValueError, AttributeError):
                continue

        # Update the Blend Results Table
        for blend_id, data in blend_data.items():
            row_idx = int(blend_id) - 1
            total_weight = sum(data["weights"])

            if total_weight > 0:
                # Calculate weighted averages
                avg_grades = [sum(grades) / total_weight for grades in zip(*data["grades"])]
                balance = min(data["balances"])
                max_duration = balance / self.crusher_rate if self.crusher_rate > 0 else 0
                available_status = "Now"
                if any(avail != "Now" for avail in data["available"]):
                    # Extract all datetimes from "available" (excluding "Now")
                    datetime_values = [avail for avail in data["available"] if avail != "Now"]
                    # Get the minimum datetime
                    available_status = min(datetime_values)
                else:
                    available_status = "Now"

                # Update blend results table
                self.blend_results_table.setItem(row_idx, 0, self.create_centered_item(blend_id))
                for col_idx, avg_grade in enumerate(avg_grades, start=1):
                    self.blend_results_table.setItem(row_idx, col_idx, self.create_centered_item(f"{avg_grade:.2f}"))
                self.blend_results_table.setItem(row_idx, 6, self.create_centered_item(f"{balance:.1f}"))
                self.blend_results_table.setItem(row_idx, 7, self.create_centered_item(f"{max_duration:.1f}"))
                item = QTableWidgetItem(available_status)
                item.setTextAlignment(Qt.AlignCenter)

                # Apply green color for "Now", purple otherwise
                if available_status == "Now":
                    item.setForeground(QBrush(QColor("green")))
                else:
                    item.setForeground(QBrush(QColor("purple")))

                self.blend_results_table.setItem(row_idx, 8, item)
            
            else:
                # Clear unused blend ID rows
                for col_idx in range(self.blend_results_table.columnCount()):
                    self.blend_results_table.setItem(row_idx, col_idx, self.create_centered_item(""))

        # Ensure headers resize to fit updated content
        self.blend_results_table.resizeColumnsToContents()

    def create_centered_item(self, text):
        """
        Helper method to create a centered QTableWidgetItem with the given text.
        """
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        return item
    
    def store_blend_results(self):
        """
        Store the blend results table data into self.saved_blends_for_schedule.
        """
        self.saved_blends_for_schedule = []

        # Iterate through rows of the blend results table
        for row_idx in range(self.blend_results_table.rowCount()):
            blend_data = {}
            for col_idx in range(self.blend_results_table.columnCount()):
                header = self.blend_results_table.horizontalHeaderItem(col_idx).text()
                cell_item = self.blend_results_table.item(row_idx, col_idx)

                if cell_item:
                    blend_data[header] = cell_item.text()
                else:
                    blend_data[header] = None  # Handle empty cells

            # Skip rows that are entirely empty
            if any(value is not None and value != "" for value in blend_data.values()):
                self.saved_blends_for_schedule.append(blend_data)

        QMessageBox.information(self, "Blend Setup", "Blend results successfully saved.")
        self.update_sequence_tab()
        self.tabs.setTabEnabled(7, True)
        self.tabs.setCurrentIndex(7)  
    
    def fetch_build_report(self):
        """
        Fetch the build report from the database.
        """
        conn = sqlite3.connect("blendmaster.db")
        df = pd.read_sql("SELECT * FROM build_report", conn)
        conn.close()
        return df

    def format_blend_config_table(self):
        """
        Apply column formatting for the blend configuration table.
        """
        # Format Balance and Projected Balance columns (no decimal places)
        for row_idx in range(self.blend_config_table.rowCount()):
            for col_idx in [1, 2]:  # Balance and Projected Balance columns
                item = self.blend_config_table.item(row_idx, col_idx)
                if item and item.text():
                    item.setText(f"{float(item.text()):.0f}")  # No decimal places
                    item.setTextAlignment(Qt.AlignCenter)

        # Format Grade columns (two decimal places)
        for row_idx in range(self.blend_config_table.rowCount()):
            for col_idx in range(5, 10):  # Grade columns
                item = self.blend_config_table.item(row_idx, col_idx)
                if item and item.text():
                    item.setText(f"{float(item.text()):.2f}")  # Two decimal places
                    item.setTextAlignment(Qt.AlignCenter)
    
    def update_sequence_tab(self):

        self.manual_gantt_view.setUrl(QUrl("http://localhost:8050"))

        # Populate data from saved_blends_for_schedule into blend_results_table_view
        self.blend_results_table_view.setRowCount(len(self.saved_blends_for_schedule))  # Set row count
        self.blend_results_table_view.setColumnCount(len(self.saved_blends_for_schedule[0].keys()))  # Set column count

        # Set column headers from the dictionary keys
        self.blend_results_table_view.setHorizontalHeaderLabels(self.saved_blends_for_schedule[0].keys())

        # Make headers bold
        header_font = self.blend_results_table_view.horizontalHeader().font()
        header_font.setBold(True)
        self.blend_results_table_view.horizontalHeader().setFont(header_font)

        # Remove row index (vertical header)
        self.blend_results_table_view.verticalHeader().setVisible(False)

        # Iterate over the saved_blends_for_schedule to populate rows
        for row_idx, row_data in enumerate(self.saved_blends_for_schedule):
            for col_idx, (key, value) in enumerate(row_data.items()):
                # Create the table item
                item = QTableWidgetItem(str(value))
                
                # Center-align the text
                item.setTextAlignment(Qt.AlignCenter)
                
                # Conditionally format the "Available" column
                if key == "Available":
                    if str(value) == "Now":
                        item.setForeground(QColor("green"))
                    else:
                        item.setForeground(QColor("purple"))

                # Set the table item
                self.blend_results_table_view.setItem(row_idx, col_idx, item)

        # Resize columns to fit contents
        self.blend_results_table_view.resizeColumnsToContents()

        # Additional Setup: Load the Dash app into the CustomWebEngineView
        #self.start_dash_optimised_charts_thread()

class CustomTableWidget(QTableWidget):
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):  # Check for Enter key
            current_row = self.currentRow()
            current_column = self.currentColumn()

            # Move one row down, wrap around if at the last row
            next_row = (current_row + 1) % self.rowCount()
            self.setCurrentCell(next_row, current_column)

        else:
            # Default behavior for other keys
            super().keyPressEvent(event)

class CustomWebEngineView(QWebEngineView):
    def __init__(self):
        super().__init__()
        # Connect download handling to the QWebEngineView
        self.page().profile().downloadRequested.connect(self.handle_download)

    def handle_download(self, download_item: QWebEngineDownloadItem):
        # Default the file name to JPG
        save_path = QFileDialog.getSaveFileName(
            self,
            "Save File",
            download_item.path().rsplit(".", 1)[0] + ".jpg",  # Replace the file extension with .jpg
            "JPEG (*.jpg)"  # Only allow JPG files
        )[0]

        if save_path:  # Ensure a file path is selected
            # Force .jpg extension in case the user deletes it
            if not save_path.endswith(".jpg"):
                save_path += ".jpg"

            # Set the file path and accept the download
            download_item.setPath(save_path)
            download_item.accept()
        else:
            # Cancel the download if no path is chosen
            download_item.cancel()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Set the global font to Segoe UI, size 12
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    
    window = UserInputs()  # Create an instance of the imported class
    window.show()              # Show the GUI
    sys.exit(app.exec_())      # Run the event loop

