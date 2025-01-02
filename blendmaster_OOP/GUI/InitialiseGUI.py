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

        # Disable tabs initially
        self.tabs.setTabEnabled(1, False)  # Disable Stockpile tab
        self.tabs.setTabEnabled(2, False)  # Disable Calendar tab
        self.tabs.setTabEnabled(3, False)  # Disable Decision tab
        self.tabs.setTabEnabled(4, False)  # Disable Results tab
        self.tabs.setTabEnabled(5, False)  # Disable Profiles tab

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
            use_checkbox.setChecked(False)  # Default to unchecked

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
        
        success = False  # Flag to track whether execution was successful

        try:
            # Call main optimised run
            self.run_program.execute(self.start_time, self.expit_mode, self.file_path, self.blend_mode, self.updated_stockpile_data, self.calendar_inputs)
            success = True  # Set flag to True if no exception occurs

        except Exception as e:
            # Handle or log the error
            self.run_program.case_bridge.error_signal.emit(str(e))  # Emit the error to show in the popup

        # Conditional logic based oÜn whether the execution was successful
        if success:
            self.update_decision_point_tab_state()
            self.start_dash_thread()
        else:
            # Handle alternative flow if an exception occurred
            self.tabs.setCurrentIndex(1)  
            self.setup_stockpile_table()

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
        # Load the Dash app into the QWebEngineView
        self.stockpile_profile_chart_view.setUrl(QUrl("http://localhost:8051"))
   
    def start_dash_thread(self):
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
            self.tabs.setTabEnabled(3, False)
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

