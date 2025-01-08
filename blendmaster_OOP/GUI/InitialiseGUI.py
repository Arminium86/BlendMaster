import sys, threading, requests, time
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QTabWidget,
    QFormLayout, QLineEdit, QPushButton, QComboBox, QHBoxLayout, QLabel, QMessageBox, QDateTimeEdit, QFileDialog, QTextEdit, QFrame, QAbstractItemView, QCheckBox
)

from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineDownloadItem
from PyQt5.QtGui import QColor, QBrush, QFont
from PyQt5.QtCore import Qt, QUrl, QTimer, QDateTime
from setup.OpeningStockpileInventories import OpeningStockpileInventories
from execute.Run import Run
from datetime import datetime, timedelta
from GUI.DrawCharts import DrawGanttChart, DrawStockProfiles
import pandas as pd, sqlite3
from classes.PeriodManager import PeriodManager

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
        
        # Create a frame for manual_gantt_view
        self.manual_gantt_view_frame = QFrame()
        self.manual_gantt_view_frame.setFrameShape(QFrame.Box)  # Set the frame shape (Box, Panel, etc.)
        self.manual_gantt_view_frame.setLineWidth(1)  # Set the thickness of the frame

        # Create a layout for the frame
        self.manual_gantt_view_layout = QVBoxLayout(self.manual_gantt_view_frame)
        self.manual_gantt_view_layout.setContentsMargins(0, 0, 0, 0)  # Remove margins inside the frame
        self.manual_gantt_view_layout.addWidget(self.manual_gantt_view)  # Add the WebEngineView to the frame
        
        # Add it to the tab
        self.blend_sequence_tab_layout.addWidget(self.manual_gantt_view_frame)
        
        # Add the horizontal layout for the two tables at the bottom
        self.blend_sequence_table_layout = QHBoxLayout()

        # Left Table: Blend Results Table (View Only) - Create a new instance
        self.blend_results_table_view = CustomTableWidget()
        self.blend_results_table_view.setEditTriggers(QTableWidget.NoEditTriggers)  # Make uneditable

        # Add the new table and label to the layout
        self.blend_results_table_view_external_layout = QVBoxLayout()
        blend_results_label = QLabel("Blend Results")
        blend_results_label.setAlignment(Qt.AlignLeft)  # Center-align the caption
        blend_results_label.setStyleSheet("font-weight: bold; font-size: 22px;")  # Optional: Styling for the label
      
        self.blend_results_table_view_external_layout.addWidget(blend_results_label)
        self.blend_results_table_view_external_layout.addWidget(self.blend_results_table_view)

        self.blend_sequence_table_layout.addLayout(self.blend_results_table_view_external_layout)

        # Right Table: table for real-time updates
        self.blend_sequence_table_external_layout = QVBoxLayout()
        self.blend_sequence_table_layout.addLayout(self.blend_sequence_table_external_layout)
        self.blend_sequence_table = CustomTableWidget()

        # Add the horizontal layout to the main layout of the tab
        self.blend_sequence_tab_layout.addLayout(self.blend_sequence_table_layout)

        # Workflow controls
        self.load_profiles_first_call = True
        self.setup_blends_tab_first_call = True
        self.setup_blend_sequence_table_first_call = True


        # Disable tabs initially
        self.tabs.setTabEnabled(1, False)  # Disable Stockpile tab
        self.tabs.setTabEnabled(2, False)  # Disable Calendar tab
        self.tabs.setTabEnabled(3, False)  # Disable Decision tab
        self.tabs.setTabEnabled(4, False)  # Disable Results tab
        self.tabs.setTabEnabled(5, False)  # Disable Profiles tab
        self.tabs.setTabEnabled(6, False)  # Disable Setup Blends tab
        self.tabs.setTabEnabled(7, False)  # Disable Blend Sequence tab


        # Initialise main optimisation program
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
                
                # Conditionally format the "Available" column
                if key == "Available":
                    item = QTableWidgetItem(str(value))
                    item.setTextAlignment(Qt.AlignCenter)
                    if str(value) == "Now":
                        item.setForeground(QColor("green"))
                    else:
                        item.setForeground(QColor("purple"))
                    self.blend_results_table_view.setItem(row_idx, col_idx, item)

                # Handle other columns as standard table items
                else:
                    item = QTableWidgetItem(str(value))
                    item.setTextAlignment(Qt.AlignCenter)
                    self.blend_results_table_view.setItem(row_idx, col_idx, item)

        # Resize columns to fit contents
        self.blend_results_table_view.resizeColumnsToContents()

        self.setup_blend_sequence_table()

        # Additional Setup: Load the Dash app into the CustomWebEngineView
        #self.start_dash_optimised_charts_thread()

    def set_start_and_end_datetime(self, periods):
        # Set start and end datetime
        self.default_start_datetime = periods.get_periods()["preplan_start"]
        self.default_end_datetime = periods.get_periods()["period_2_end"]
        self.default_start_datetime_str = periods.get_periods()["preplan_start"].strftime("%Y-%m-%d %H:%M")
        self.default_end_datetime_str = periods.get_periods()["period_2_end"].strftime("%Y-%m-%d %H:%M")
    
    def setup_blend_sequence_table(self):
        
        self.blend_sequence_table.clearContents()  

        # Add new column for Early Start Flag
        headers = ["Blend ID", "Origin", "Start Datetime", "Duration (hrs)", "End Datetime", "Early Start Flag", "Remaining Hrs"]
        self.blend_sequence_table.setColumnCount(len(headers))
        self.blend_sequence_table.setHorizontalHeaderLabels(headers)

        # Prepopulate the table
        blend_ids = sorted([blend["Blend ID"] for blend in self.saved_blends_for_schedule])
        row_data = []
        current_start = self.default_start_datetime

        # Populate rows for Blend IDs in self.saved_blends_for_schedule
        for blend_id in blend_ids:
            blend_data = next(item for item in self.saved_blends_for_schedule if item["Blend ID"] == blend_id)
            duration = float(blend_data["Max Duration (hrs)"])
            end_datetime = current_start + timedelta(hours=duration)
            end_datetime_str = end_datetime.strftime("%Y-%m-%d %H:%M")
            origin = "User Defined"

            # Conditional formatting logic for Early Start Flag
            available_time_str = blend_data["Available"]
            try:
                if available_time_str == "Now":
                    available_time = self.default_start_datetime
                else:
                    available_time = datetime.strptime(available_time_str, "%Y-%m-%d %H:%M:%S")
            except Exception as e:
                QMessageBox.critical(None, "Error", f"Error parsing Available column: {e}")
                available_time = None

            if available_time and current_start >= available_time:
                early_start_flag = "On Time"
                flag_color = QColor("green")
            else:
                early_start_flag = "Early"
                flag_color = QColor("red")

            current_start_str = current_start.strftime("%Y-%m-%d %H:%M")

            row_data.append({
                "Blend ID": blend_id,
                "Origin": origin,
                "Start Datetime": current_start_str,
                "Duration (hrs)": duration,
                "End Datetime": end_datetime_str,
                "Early Start Flag": early_start_flag,
                "Flag Color": flag_color,
                "Max Duration": duration
            })
            current_start = end_datetime

        # Add rows for missing Blend IDs
        all_blend_ids = set(range(1, 6))  # Default blend IDs 1-5
        missing_blend_ids = [blend_id for blend_id in all_blend_ids if str(blend_id) not in blend_ids]

        for blend_id in sorted(missing_blend_ids):
            duration = (self.default_end_datetime - self.default_start_datetime).total_seconds() / 3600
            row_data.append({
                "Blend ID": blend_id,
                "Origin": "Default",
                "Start Datetime": self.default_start_datetime.strftime("%Y-%m-%d %H:%M"),
                "Duration (hrs)": duration,
                "End Datetime": self.default_end_datetime.strftime("%Y-%m-%d %H:%M"),
                "Early Start Flag": "N/A",
                "Flag Color": None,
                "Max Duration": duration
            })

        self.blend_sequence_table.setRowCount(len(row_data))
        for row, data in enumerate(row_data):
            for col, key in enumerate(headers):

                if key == "Blend ID":
                    blend_dropdown = QComboBox()
                    blend_dropdown.addItems([str(i) for i in range(1, 6)])  # Blend IDs 1 to 5
                    blend_dropdown.currentIndexChanged.connect(lambda _, row=row: self.update_blend_id(row))
                    self.blend_sequence_table.setCellWidget(row, col, blend_dropdown)
                    blend_dropdown.setCurrentText(str(data[key]))  # Set initial value
                    self.blend_sequence_table.setCellWidget(row, col, blend_dropdown)

                # Add editable field for Duration
                elif key == "Duration (hrs)":
                    duration_item = QTableWidgetItem(str(data[key]))
                    duration_item.setFlags(duration_item.flags() | Qt.ItemIsEditable)  # Make editable
                    duration_item.setTextAlignment(Qt.AlignCenter)
                    self.blend_sequence_table.setItem(row, col, duration_item)

                # Add a date-time picker for Start Datetime
                elif key == "Start Datetime":
                    date_edit = QDateTimeEdit()
                    date_edit.setCalendarPopup(True)
                    date_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
                    date_edit.setAlignment(Qt.AlignCenter)
                    date_edit.dateTimeChanged.connect(lambda dt, row=row: self.update_row_datetime(row, dt))
                    self.blend_sequence_table.setCellWidget(row, col, date_edit)
                    
                    # Set the value to the date picker from data[key]
                    date_value = data[key]
                    if date_value:  # Ensure the value exists
                        date_edit.setDateTime(datetime.strptime(date_value, "%Y-%m-%d %H:%M"))

                    # Place the date picker in the table cell
                    self.blend_sequence_table.setCellWidget(row, col, date_edit)

                # Add a read-only field for Early Start Flag with color formatting
                elif key == "Early Start Flag":
                    flag_item = QTableWidgetItem(data[key])
                    flag_item.setTextAlignment(Qt.AlignCenter)
                    if data["Flag Color"] is not None:
                        flag_item.setBackground(data["Flag Color"])
                    flag_item.setFlags(flag_item.flags() & ~Qt.ItemIsEditable)  # Make read-only
                    self.blend_sequence_table.setItem(row, col, flag_item)

                # Remaining hours
                elif key == "Remaining Hrs":
                    remaining_hrs_item = QTableWidgetItem()
                    remaining_hrs_item.setFlags(remaining_hrs_item.flags() | ~Qt.ItemIsEditable) 
                    remaining_hrs_item.setTextAlignment(Qt.AlignCenter)
                    self.blend_sequence_table.setItem(row, col, remaining_hrs_item)
                    
                # Handle other fields (like Origin and End Datetime)
                else:
                    item = QTableWidgetItem(str(data[key]))
                    item.setTextAlignment(Qt.AlignCenter)
                    if key in ["Origin", "End Datetime"]:  # Make these read-only
                        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    self.blend_sequence_table.setItem(row, col, item)

        # Adjust column widths to contents
        self.blend_sequence_table.resizeColumnsToContents()

        # Set up signal for detecting edits
        self.blend_sequence_table.cellChanged.connect(self.on_cell_changed)

        # Add button to add rows
        add_row_button = QPushButton("Add Row")
        add_row_button.setFixedWidth(120)
        add_row_button.clicked.connect(self.add_blank_row)
    
        # Blend sequence table
        blend_sequence_label = QLabel("Sequence")
        blend_sequence_label.setAlignment(Qt.AlignLeft)  # Center-align the caption
        blend_sequence_label.setStyleSheet("font-weight: bold; font-size: 22px;")  # Optional: Styling for the label
        
        if self.setup_blend_sequence_table_first_call:
            
            self.blend_sequence_table_external_layout.addWidget(blend_sequence_label)
    
            # Layout to contain the table and button
            self.blend_sequence_table_external_layout.addWidget(self.blend_sequence_table)
            self.blend_sequence_table_external_layout.addWidget(add_row_button)

        store_button = QPushButton("Submit Table")
        store_button.setFixedWidth(120)
        store_button.clicked.connect(self.store_blend_sequence_table)
        
        if self.setup_blend_sequence_table_first_call:
            
            # Add the button to the layout
            self.blend_sequence_table_external_layout.addWidget(store_button)
            
        self.setup_blend_sequence_table_first_call = False
        
        self.update_remaining_hrs()

        self.blend_sequence_table.viewport().update()
        self.blend_sequence_table_external_layout.update()

    def update_row_datetime(self, row, new_datetime):
        """Update all cells in a row when Start Datetime changes."""
        try:
            # Retrieve and parse duration
            duration_item = self.blend_sequence_table.item(row, 3)
            
            duration = float(duration_item.text()) if duration_item and duration_item.text().strip() else 0.0

            # Calculate new start and end datetimes
            start_datetime = new_datetime.toPyDateTime()
            end_datetime = start_datetime + timedelta(hours=duration)

            # Update End Datetime cell
            end_item = self.blend_sequence_table.item(row, 4)
            if end_item:
                end_item.setText(end_datetime.strftime("%Y-%m-%d %H:%M"))
                
            # Update Early Start Flag based on new Start Datetime
            blend_id_widget = self.blend_sequence_table.cellWidget(row, 0)
            blend_id = int(blend_id_widget.currentText()) if blend_id_widget else None

            blend_data = next((b for b in self.saved_blends_for_schedule if b["Blend ID"] == blend_id), None)
            if blend_data:
                available_time_str = blend_data["Available"]
                available_time = (self.default_start_datetime if available_time_str == "Now" else datetime.strptime(available_time_str, "%Y-%m-%d %H:%M:%S"))
                
                early_start_flag = "On Time" if start_datetime >= available_time else "Early"
                flag_color = QColor("green") if start_datetime >= available_time else QColor("red")

                flag_item = self.blend_sequence_table.item(row, 5)
                if flag_item:
                    flag_item.setText(early_start_flag)
                    flag_item.setBackground(flag_color)

        except Exception as e:
            QMessageBox.warning(None, "Warning", f"Could not update row {row}: {e}")

    def add_blank_row(self):
        current_row_count = self.blend_sequence_table.rowCount()
        self.blend_sequence_table.insertRow(current_row_count)

        # Add default values for the new row
        default_start_datetime = self.default_start_datetime.strftime("%Y-%m-%d %H:%M")
        default_duration = 1.0  # Default duration in hours
        default_end_datetime = (self.default_start_datetime + timedelta(hours=default_duration)).strftime("%Y-%m-%d %H:%M")

        # Populate each column
        headers = ["Blend ID", "Origin", "Start Datetime", "Duration (hrs)", "End Datetime", "Early Start Flag", "Remaining Hrs"]
        for col, key in enumerate(headers):
            
            if key == "Blend ID":
                blend_dropdown = QComboBox()
                blend_dropdown.addItems([str(i) for i in range(1, 6)])  # Blend IDs 1 to 5
                blend_dropdown.currentIndexChanged.connect(lambda _, row=current_row_count: self.update_blend_id(row))
                self.blend_sequence_table.setCellWidget(current_row_count, col, blend_dropdown)
                blend_dropdown.setCurrentText(str(1))  # Set initial default value
                self.blend_sequence_table.setCellWidget(current_row_count, col, blend_dropdown)
                self.update_blend_id(current_row_count)
                self.update_early_start_conditional_format()
            
            if key == "Start Datetime":
                # Add a calendar picker
                date_edit = QDateTimeEdit()
                date_edit.setCalendarPopup(True)
                date_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
                date_edit.setDateTime(self.default_start_datetime)
                date_edit.setAlignment(Qt.AlignCenter)
                date_edit.dateTimeChanged.connect(lambda dt, row=current_row_count: self.update_row_datetime(row, dt))
                self.blend_sequence_table.setCellWidget(current_row_count, col, date_edit)

            elif key == "Duration (hrs)":
                # Add default duration
                item = QTableWidgetItem(str(default_duration))
                item.setTextAlignment(Qt.AlignCenter)
                self.blend_sequence_table.setItem(current_row_count, col, item)

            elif key == "End Datetime":
                # Add default end datetime
                item = QTableWidgetItem(default_end_datetime)
                item.setTextAlignment(Qt.AlignCenter)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)  # Make it non-editable
                self.blend_sequence_table.setItem(current_row_count, col, item)

            elif key == "Early Start Flag":
                # Default flag for new rows (e.g., "N/A")
                item = QTableWidgetItem("N/A")
                item.setTextAlignment(Qt.AlignCenter)
                item.setBackground(QColor("white"))  # Neutral background color for new rows
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)  # Make it non-editable
                self.blend_sequence_table.setItem(current_row_count, col, item)

            elif key == "Remaining Hrs":
                # Remaining hours
                item = QTableWidgetItem()
                item.setFlags(item.flags() | ~Qt.ItemIsEditable) 
                item.setTextAlignment(Qt.AlignCenter)
                self.blend_sequence_table.setItem(current_row_count, col, item)

            elif key == "Origin":
                item = QTableWidgetItem("Select Blend ID")
                item.setTextAlignment(Qt.AlignCenter)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.blend_sequence_table.setItem(current_row_count, col, item)

            else:
                # Other columns, like Blend ID and Origin, can be left blank or with default values
                item = QTableWidgetItem()
                item.setTextAlignment(Qt.AlignCenter)
                if key in ["Origin", "End Datetime"]:  # Make these read-only
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.blend_sequence_table.setItem(current_row_count, col, item)
        
        self.update_remaining_hrs()

    def update_blend_id(self, row):
        blend_dropdown = self.blend_sequence_table.cellWidget(row, 0)
        if blend_dropdown:
            blend_id = int(blend_dropdown.currentText())

        # Update the Origin based on the new Blend ID
        origin_item = self.blend_sequence_table.item(row, 1)

        if origin_item is None:
            # Create a new QTableWidgetItem if it doesn't exist
            origin_item = QTableWidgetItem()
            self.blend_sequence_table.setItem(row, 1, origin_item)

        origin_item.setText("User Defined" if blend_id in [int(b["Blend ID"]) for b in self.saved_blends_for_schedule] else "Default")

    def on_cell_changed(self, row, column):
        if column == 3:  # Assuming "Duration (hrs)" is the 3rd column
            self.on_duration_changed(row)
        
        self.update_early_start_conditional_format()
        self.update_remaining_hrs()

    def on_duration_changed(self, row):
        try:
            # Retrieve updated duration
            duration_item = self.blend_sequence_table.item(row, 3)
            if duration_item:
                duration = float(duration_item.text())
                
            # Update End Datetime
            start_datetime_widget = self.blend_sequence_table.cellWidget(row, 2)  # Access the widget in the cell
            if isinstance(start_datetime_widget, QDateTimeEdit):  # Check if it's a QDateTimeEdit
                start_datetime = start_datetime_widget.dateTime().toPyDateTime()  # Convert to Python datetime
            else:
                start_datetime = None  # Handle case where the widget is not set

            # Ensure both duration and start_datetime are valid before calculating
            if duration_item and start_datetime:
                end_datetime = start_datetime + timedelta(hours=duration)

            end_datetime_item = self.blend_sequence_table.item(row, 4)
            if end_datetime_item:
                end_datetime_item.setText(end_datetime.strftime("%Y-%m-%d %H:%M"))
        except Exception as e:
            QMessageBox.warning(None, "Invalid Duration", f"Error updating row {row}: {e}")

    def store_blend_sequence_table(self):
        headers = ["Blend ID", "Origin", "Start Datetime", "Duration (hrs)", "End Datetime", "Early Start Flag"]

        self.stored_blend_sequence_table_for_gantt = []  # Reset stored table
        for row in range(self.blend_sequence_table.rowCount()):
            row_data = {}
            for col, header in enumerate(headers):
                item = self.blend_sequence_table.item(row, col) or self.blend_sequence_table.cellWidget(row, col)
                if isinstance(item, QTableWidgetItem):
                    row_data[header] = item.text() if item else None
                elif isinstance(item, QComboBox):
                    row_data[header] = item.currentText() if item else None
                elif isinstance(item, QDateTimeEdit):
                    row_data[header] = item.dateTime().toString("yyyy-MM-dd HH:mm") if item else None
            self.stored_blend_sequence_table_for_gantt.append(row_data)

        QMessageBox.information(None, "Success", "Table data has been stored!")

    def update_early_start_conditional_format(self):
        """
        Updates the 6th column of `self.blend_sequence_table` based on conditional formatting logic.
        """
        row_count = self.blend_sequence_table.rowCount()

        for row in range(row_count):
            # Retrieve the combo box for Blend ID (first column)
            blend_id_widget = self.blend_sequence_table.cellWidget(row, 0)
            blend_id = blend_id_widget.currentText() if blend_id_widget else None

            # Retrieve the second column (User Defined text)
            user_defined_item = self.blend_sequence_table.item(row, 1)
            user_defined_text = user_defined_item.text() if user_defined_item else None

            # Retrieve the calendar picker for Start Time (third column)
            start_time_widget = self.blend_sequence_table.cellWidget(row, 2)
            start_time = start_time_widget.dateTime().toString("yyyy-MM-dd HH:mm:ss") if start_time_widget else None

            # Retrieve the 6th column item (Early Start Flag)
            early_start_item = self.blend_sequence_table.item(row, 5)

            # Ensure required fields are valid and "User Defined" condition is met
            if not blend_id or not start_time or user_defined_text != "User Defined":
                early_start_flag = "N/A"
                flag_color = QColor("white")
                foreground_color = QColor("black")
                if early_start_item is None:
                    early_start_item = QTableWidgetItem()
                early_start_item.setText(early_start_flag)
                early_start_item.setBackground(flag_color)
                early_start_item.setForeground(foreground_color)
                early_start_item.setTextAlignment(Qt.AlignCenter)
                continue

            try:
                current_start = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
            except Exception as e:
                QMessageBox.critical(None, "Error", f"Error parsing Start Time: {e}")
                continue

            # Find the corresponding blend data in `self.saved_blends_for_schedule`
            try:
                blend_data = next(item for item in self.saved_blends_for_schedule if item["Blend ID"] == blend_id)
            except StopIteration:
                QMessageBox.warning(None, "Warning", f"Blend ID {blend_id} not found in saved blends.")
                continue

            available_time_str = blend_data.get("Available", "")
            try:
                if available_time_str == "Now":
                    available_time = self.default_start_datetime
                else:
                    available_time = datetime.strptime(available_time_str, "%Y-%m-%d %H:%M:%S")
            except Exception as e:
                QMessageBox.critical(None, "Error", f"Error parsing Available column: {e}")
                available_time = None

            # Determine the Early Start Flag and apply conditional formatting
            if available_time and current_start >= available_time:
                early_start_flag = "On Time"
                flag_color = QColor("green")
            else:
                early_start_flag = "Early"
                flag_color = QColor("red")

            # Update the 6th column with the Early Start Flag and apply background color
            if early_start_item is None:
                early_start_item = QTableWidgetItem()
                self.blend_sequence_table.setItem(row, 5, early_start_item)

            early_start_item.setText(early_start_flag)
            early_start_item.setBackground(flag_color)
            early_start_item.setForeground(QColor("white"))
            early_start_item.setTextAlignment(Qt.AlignCenter)

    def update_remaining_hrs(self):
        """
        Updates the "Remaining Hrs" column in self.blend_sequence_table.
        If a Blend ID is present in more than one row, the aggregate duration of its rows is considered.
        """
        row_count = self.blend_sequence_table.rowCount()
        blend_remaining_map = {}  # Tracks remaining hours for each Blend ID

        for row in range(row_count):
            # Get the "Duration (Hrs)" value
            duration_item = self.blend_sequence_table.item(row, 3) 
            scheduled_duration = float(duration_item.text()) if duration_item and duration_item.text() else 0

            # Get the Blend ID value
            blend_id_widget = self.blend_sequence_table.cellWidget(row, 0) 
            blend_id = blend_id_widget.currentText() if blend_id_widget else None

            if not blend_id:
                continue

            # Find the max duration for the Blend ID in self.saved_blends_for_schedule
            try:
                blend_data = next(b for b in self.saved_blends_for_schedule if str(b["Blend ID"]) == blend_id)
                max_duration = float(blend_data["Max Duration (hrs)"])
            except StopIteration:
                # If Blend ID not found, set max_duration to scheduled_duration
                max_duration = scheduled_duration

            # Calculate remaining hours based on whether Blend ID has been encountered before
            if blend_id not in blend_remaining_map:
                # First occurrence of Blend ID
                remaining_hrs = max_duration - scheduled_duration
            else:
                # Subsequent occurrence: subtract from the remaining hrs of the row above
                previous_remaining = blend_remaining_map[blend_id]
                remaining_hrs = previous_remaining - scheduled_duration

            # Update the map with the current remaining hours for this Blend ID
            blend_remaining_map[blend_id] = remaining_hrs

            # Update the "Remaining Hrs" column
            remaining_hrs_item = self.blend_sequence_table.item(row, 6)  
            if remaining_hrs_item is None:
                # Create a new item if it doesn't exist
                remaining_hrs_item = QTableWidgetItem()
                self.blend_sequence_table.setItem(row, 6, remaining_hrs_item)

            # Set the value of "Remaining Hrs" and conditionally format
            remaining_hrs_item.setText(f"{remaining_hrs:.2f}")
            if remaining_hrs >= 0:
                remaining_hrs_item.setBackground(QColor("green"))
                remaining_hrs_item.setForeground(QColor("white"))
            else:
                remaining_hrs_item.setBackground(QColor("red"))
                remaining_hrs_item.setForeground(QColor("white"))

            # Center align the text
            remaining_hrs_item.setTextAlignment(Qt.AlignCenter)
            remaining_hrs_item.setFlags(remaining_hrs_item.flags() | ~Qt.ItemIsEditable) 

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

