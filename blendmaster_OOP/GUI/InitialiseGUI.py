import sys, threading, requests, os, pickle, copy, traceback
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QTabWidget,
    QFormLayout, QLineEdit, QPushButton, QComboBox, QHBoxLayout, QLabel, QMessageBox, QDateTimeEdit, QFileDialog, QTextEdit, QFrame, QCheckBox, QProgressDialog, QAbstractItemView, QSizePolicy, QListWidget
)
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineDownloadItem
from PyQt5.QtGui import QColor, QBrush, QFont, QIcon, QDoubleValidator, QIntValidator, QPixmap
from PyQt5.QtCore import Qt, QUrl, QDateTime, QDir, QObject, pyqtSignal, pyqtSlot, QThread, QTimer
from setup.OpeningStockpileInventories import OpeningStockpileInventories
from execute.Run import Run
from classes.ExpitDataHandler import ExpitDataHandler
from classes.Optimizer import Optimizer
from classes.PeriodManager import PeriodManager
from datetime import datetime, timedelta
from GUI.DrawCharts import DrawGanttChart, DrawStockProfiles, DrawAMTStockpile
from GUI.ManualBlendDash import ManualBlendDash, DrawGradeProfiles, DrawOptimisedGradeProfiles
from database.SQLiteDatabase import DatabaseManager
import pandas as pd, sqlite3
from numbers import Real, Integral
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

class UserInputs(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BlendMaster PoC v0.1.0 - 2025 Fortescue - MOPP")
        self.setWindowIcon(QIcon("C:/BlendMaster/blendmaster_OOP/resources/icon_2.ico")) 
        self.setGeometry(100, 100, 800, 600)

        self.initialise_all_variables()
        self.clear_sqlite_session_data()

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
        self.stockpile_tab_index = self.tabs.addTab(self.stockpile_tab, "Stockpile Inventories")
        self.stockpile_tab_layout = QVBoxLayout(self.stockpile_tab)

        # Stockpile Table
        self.stockpile_table = CustomTableWidget()
        self.stockpile_tab_layout.addWidget(self.stockpile_table)

        # Add AMT Stockpile Tab
        self.AMT_stockpile_tab = QWidget()
        self.AMT_stockpile_tab_index = self.tabs.addTab(self.AMT_stockpile_tab, "AMT Stockpiles")
        self.AMT_stockpile_tab_layout = QHBoxLayout(self.AMT_stockpile_tab)

        # Stockpile AMT Table
        self.AMT_stockpile_table = CustomTableWidget()
        self.AMT_stockpile_table.setMinimumWidth(560)
        self.AMT_stockpile_tab_layout.addWidget(self.AMT_stockpile_table, stretch=0)

        # Add Solver Configuration Tab
        self.setup_solver_configuration_tab()

        # Add calendar Tab
        self.main_tab = QWidget()
        self.calendar_tab_index = self.tabs.addTab(self.main_tab, "Calendar")
        self.main_tab_layout = QVBoxLayout(self.main_tab)

        # Calendar table
        self.main_table = CustomTableWidget()
        self.main_tab_layout.addWidget(self.main_table)

        # Add Decision Point Tab
        self.decision_point_tab = QWidget()
        self.decision_point_tab_index = self.tabs.addTab(self.decision_point_tab, "Decision Point")
        self.decision_point_tab_layout = QVBoxLayout(self.decision_point_tab)

        self.decision_status_label = QLabel("Run the optimiser to review feasible blend options.")
        self.decision_status_label.setStyleSheet("font-weight: bold;")
        self.decision_point_tab_layout.addWidget(self.decision_status_label)

        # Create the table widget for the DataFrame
        self.decision_table = CustomTableWidget()
        self.decision_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.decision_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.decision_table.cellDoubleClicked.connect(self.select_decision_blend_from_row)
        self.decision_point_tab_layout.addWidget(self.decision_table)  # Add table at the top

        # Text output area
        self.decision_output = QTextEdit()
        self.decision_output.setReadOnly(True)
        self.decision_point_tab_layout.addWidget(self.decision_output)  # Add text in the middle

        # Input layout (field + button)
        input_layout = QHBoxLayout()
        self.decision_input = QLineEdit()
        self.decision_input.setPlaceholderText("Enter your input here...")
        self.decision_input.setFixedWidth(300)
        self.decision_input.returnPressed.connect(self.handle_decision_input)
        input_layout.addWidget(self.decision_input, alignment=Qt.AlignLeft)
        self.decision_input.setEnabled(False)

        self.enter_button = QPushButton("Submit")
        self.enter_button.setFixedWidth(200)
        self.enter_button.clicked.connect(self.handle_decision_input)
        input_layout.addWidget(self.enter_button, alignment=Qt.AlignLeft)

        self.decision_select_button = QPushButton("Select Highlighted Blend")
        self.decision_select_button.setFixedWidth(220)
        self.decision_select_button.clicked.connect(self.select_decision_blend_from_selected_row)
        self.decision_select_button.setEnabled(False)
        input_layout.addWidget(self.decision_select_button, alignment=Qt.AlignLeft)

        input_layout.setAlignment(Qt.AlignLeft)
        self.enter_button.setEnabled(False)

        self.decision_point_tab_layout.addLayout(input_layout)  # Add input field and button at the bottom

        # Add Results and Profiles Tab
        self.setup_results_tab()

        self.setup_profiles_tab()

        self.setup_sqlite_reports_tab()

        self.setup_optimised_grade_profile_tab()

        # Add Setup Blends tab
        self.blend_config_tab = QWidget()
        self.blend_config_tab_index = self.tabs.addTab(self.blend_config_tab, "Setup Blends (Manual)")
        self.setup_blends_tab_layout = QVBoxLayout(self.blend_config_tab)

        # Add Sequence tab
        self.blend_sequence_tab = QWidget()
        self.blend_sequence_tab_index = self.tabs.addTab(self.blend_sequence_tab, "Blend Sequence (Manual Gantt)")
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
        self.blend_sequence_tab_layout.addWidget(self.manual_gantt_view_frame, stretch=1)
        
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

        self.blend_results_table = None

        self.blend_sequence_table_layout.addLayout(self.blend_results_table_view_external_layout)

        # Right Table: table for real-time updates
        self.blend_sequence_table_external_layout = QVBoxLayout()
        self.blend_sequence_table_layout.addLayout(self.blend_sequence_table_external_layout)
        self.blend_sequence_table = CustomTableWidget()

        # Add the horizontal layout to the main layout of the tab
        self.blend_sequence_tab_layout.addLayout(self.blend_sequence_table_layout, stretch=1)

        self.setup_grade_profile_tab()

        # Workflow controls
        self.load_profiles_first_call = True
        self.load_optimised_grade_profiles_first_call = True
        self.load_AMT_map_first_call = True
        self.setup_blends_tab_first_call = True
        self.setup_blend_sequence_table_first_call = True
        self.setup_stockpile_table_first_call = True
        self.setup_AMT_stockpile_table_first_call = True
        self.setup_calendar_first_call = True
        self.stockpile_data_use_column = {}
        self.stockpile_data_AMT_column = {}
        self.AMT_chunk_settings = {}
        self.submit_calendar_first_call = True
        self.is_project_loaded = False
        self.start_dash_AMT_map_thread_first_call = True
        self.start_dash_optimised_grade_profile_first_call = True
        self.manual_gantt_poll_timer = QTimer(self)
        self.manual_gantt_poll_timer.setInterval(500)
        self.manual_gantt_poll_timer.timeout.connect(self.poll_manual_gantt_updates)
        self.manual_gantt_poll_timer.start()


        # Disable tabs initially
        self.tabs.setTabEnabled(self.stockpile_tab_index, False)
        self.tabs.setTabEnabled(self.AMT_stockpile_tab_index, False)
        self.tabs.setTabEnabled(self.solver_config_tab_index, False)
        self.tabs.setTabEnabled(self.calendar_tab_index, False)
        self.tabs.setTabEnabled(self.decision_point_tab_index, False)
        self.tabs.setTabEnabled(self.results_tab_index, False)
        self.tabs.setTabEnabled(self.profiles_tab_index, False)
        self.tabs.setTabEnabled(self.sqlite_reports_tab_index, False)
        self.tabs.setTabEnabled(self.optimised_grade_profile_tab_index, False)
        self.tabs.setTabEnabled(self.blend_config_tab_index, False)
        self.tabs.setTabEnabled(self.blend_sequence_tab_index, False)
        self.tabs.setTabEnabled(self.grade_profile_tab_index, False)

        # Initialise main optimisation program
        self.run_program = Run(self)

    def setup_solver_configuration_tab(self):
        self.solver_config_tab = QWidget()
        self.solver_config_tab_index = self.tabs.addTab(self.solver_config_tab, "Solver Configuration")
        self.solver_config_layout = QVBoxLayout(self.solver_config_tab)

        contribution_ratio_validator = QDoubleValidator(0.01, 1.0, 4, self)
        contribution_ratio_validator.setNotation(QDoubleValidator.StandardNotation)
        threshold_validator = QDoubleValidator(0.0, 1000.0, 4, self)
        threshold_validator.setNotation(QDoubleValidator.StandardNotation)
        positive_integer_validator = QIntValidator(1, 1000, self)

        limits_label = QLabel("Blend Settings")
        limits_label.setStyleSheet("font-weight: bold;")
        self.solver_config_layout.addWidget(limits_label)

        stockpile_limit_layout = QHBoxLayout()
        self.min_stockpiles_input = QLineEdit()
        self.min_stockpiles_input.setPlaceholderText("Min Stockpiles")
        self.min_stockpiles_input.setFixedWidth(100)
        self.max_stockpiles_input = QLineEdit()
        self.max_stockpiles_input.setPlaceholderText("Max Stockpiles")
        self.max_stockpiles_input.setFixedWidth(100)
        stockpile_limit_layout.addWidget(QLabel("Min Stockpiles:"))
        stockpile_limit_layout.addWidget(self.min_stockpiles_input)
        stockpile_limit_layout.addWidget(QLabel("Max Stockpiles:"))
        stockpile_limit_layout.addWidget(self.max_stockpiles_input)
        stockpile_limit_layout.addStretch()
        self.solver_config_layout.addLayout(stockpile_limit_layout)

        stockpile_contribution_layout = QHBoxLayout()
        self.min_stockpile_contribution_ratio_input = QLineEdit()
        self.min_stockpile_contribution_ratio_input.setPlaceholderText("0.01 - 1")
        self.min_stockpile_contribution_ratio_input.setText(str(Optimizer.MIN_SELECTED_STOCKPILE_BLEND_RATIO))
        self.min_stockpile_contribution_ratio_input.setFixedWidth(100)
        self.min_stockpile_contribution_ratio_input.setValidator(contribution_ratio_validator)
        stockpile_contribution_layout.addWidget(QLabel("Min Stockpile Contribution Ratio:"))
        stockpile_contribution_layout.addWidget(self.min_stockpile_contribution_ratio_input)
        stockpile_contribution_layout.addStretch()
        self.solver_config_layout.addLayout(stockpile_contribution_layout)

        min_feed_duration_layout = QHBoxLayout()
        self.min_feed_duration_input = QLineEdit()
        self.min_feed_duration_input.setPlaceholderText("Optional")
        self.min_feed_duration_input.setFixedWidth(100)
        self.min_feed_duration_input.setValidator(threshold_validator)
        min_feed_duration_layout.addWidget(QLabel("Min Stockpile Feed Duration:"))
        min_feed_duration_layout.addWidget(self.min_feed_duration_input)
        min_feed_duration_layout.addWidget(QLabel("hrs"))
        min_feed_duration_layout.addStretch()
        self.solver_config_layout.addLayout(min_feed_duration_layout)

        blend_option_timeout_layout = QHBoxLayout()
        self.blend_option_timeout_input = self.create_solver_threshold_input("30.0", threshold_validator)
        blend_option_timeout_layout.addWidget(QLabel("Blend Option Timeout:"))
        blend_option_timeout_layout.addWidget(self.blend_option_timeout_input)
        blend_option_timeout_layout.addWidget(QLabel("sec (0 = off)"))
        self.max_blend_options_input = QLineEdit()
        self.max_blend_options_input.setText("12")
        self.max_blend_options_input.setValidator(positive_integer_validator)
        self.max_blend_options_input.setFixedWidth(70)
        blend_option_timeout_layout.addWidget(QLabel("Max Blend Options per Steady State:"))
        blend_option_timeout_layout.addWidget(self.max_blend_options_input)
        blend_option_timeout_layout.addStretch()
        self.solver_config_layout.addLayout(blend_option_timeout_layout)

        feasibility_layout = QHBoxLayout()
        feasibility_layout.addWidget(QLabel("Stockpile Blend Feasibility:"))
        self.stockpile_feasibility_combo = QComboBox()
        self.stockpile_feasibility_combo.addItems([
            "Stockpile blend must be feasible",
            "Stockpile blend can rely on grade blocks"
        ])
        self.stockpile_feasibility_combo.setFixedWidth(260)
        feasibility_layout.addWidget(self.stockpile_feasibility_combo)
        feasibility_layout.addStretch()
        self.solver_config_layout.addLayout(feasibility_layout)

        direct_tip_layout = QHBoxLayout()
        self.direct_tip_enabled_checkbox = QCheckBox("Enable Direct Tip")
        self.direct_tip_enabled_checkbox.setChecked(True)
        self.direct_tip_enabled_checkbox.toggled.connect(self.update_direct_tip_input_state)
        self.direct_tip_cash_incentive_input = self.create_solver_threshold_input("10.0", threshold_validator)
        direct_tip_layout.addWidget(self.direct_tip_enabled_checkbox)
        direct_tip_layout.addWidget(QLabel("Direct Tip Incentive:"))
        direct_tip_layout.addWidget(self.direct_tip_cash_incentive_input)
        direct_tip_layout.addWidget(QLabel("$/t"))
        direct_tip_layout.addStretch()
        self.solver_config_layout.addLayout(direct_tip_layout)

        same_blend_layout = QHBoxLayout()
        self.stay_on_same_blend_incentive_input = self.create_solver_threshold_input("0.0", threshold_validator)
        same_blend_layout.addWidget(QLabel("Stay on Same Blend Incentive:"))
        same_blend_layout.addWidget(self.stay_on_same_blend_incentive_input)
        same_blend_layout.addWidget(QLabel("$/t"))
        same_blend_layout.addStretch()
        self.solver_config_layout.addLayout(same_blend_layout)

        direct_tip_commitment_label = QLabel("Direct Tip Commitment")
        direct_tip_commitment_label.setStyleSheet("font-weight: bold; margin-top: 12px;")
        self.solver_config_layout.addWidget(direct_tip_commitment_label)

        grade_block_pair_duration_layout = QHBoxLayout()
        self.min_grade_block_pair_duration_input = self.create_solver_threshold_input("0.0", threshold_validator)
        grade_block_pair_duration_layout.addWidget(
            QLabel("Min Grade Block Pair Duration (only applies to longer steady state durations):")
        )
        grade_block_pair_duration_layout.addWidget(self.min_grade_block_pair_duration_input)
        grade_block_pair_duration_layout.addWidget(QLabel("hrs"))
        grade_block_pair_duration_layout.addStretch()
        self.solver_config_layout.addLayout(grade_block_pair_duration_layout)

        same_grade_block_pair_layout = QHBoxLayout()
        self.stay_on_same_grade_block_pair_incentive_input = self.create_solver_threshold_input("0.0", threshold_validator)
        same_grade_block_pair_layout.addWidget(QLabel("Stay With Same Grade Block Pair Incentive:"))
        same_grade_block_pair_layout.addWidget(self.stay_on_same_grade_block_pair_incentive_input)
        same_grade_block_pair_layout.addWidget(QLabel("$/t"))
        same_grade_block_pair_layout.addStretch()
        self.solver_config_layout.addLayout(same_grade_block_pair_layout)

        self.grade_block_lock_checkbox = QCheckBox("Lock grade block to selected stockpile mix")
        self.solver_config_layout.addWidget(self.grade_block_lock_checkbox)

        preference_label = QLabel("Tie-Break Preferences")
        preference_label.setStyleSheet("font-weight: bold; margin-top: 12px;")
        self.solver_config_layout.addWidget(preference_label)

        self.prefer_fewer_stockpiles_checkbox = QCheckBox("Prefer using fewer stockpiles")
        self.solver_config_layout.addWidget(self.prefer_fewer_stockpiles_checkbox)

        balance_layout = QHBoxLayout()
        balance_layout.addWidget(QLabel("Balance Preference:"))
        self.balance_preference_combo = QComboBox()
        self.balance_preference_combo.addItems([
            "No balance preference",
            "Lower balance first",
            "Higher balance first"
        ])
        self.balance_preference_combo.setFixedWidth(180)
        balance_layout.addWidget(self.balance_preference_combo)
        balance_layout.addStretch()
        self.solver_config_layout.addLayout(balance_layout)

        self.prefer_amt_stockpiles_checkbox = QCheckBox(
            "Prefer AMT stockpiles before weighted average inventory stockpiles"
        )
        self.solver_config_layout.addWidget(self.prefer_amt_stockpiles_checkbox)

        self.prefer_contaminated_stockpiles_checkbox = QCheckBox(
            "Try blending contaminated stockpiles/chunks first"
        )
        self.solver_config_layout.addWidget(self.prefer_contaminated_stockpiles_checkbox)

        contaminant_layout = QHBoxLayout()
        self.contaminant_si_threshold_input = self.create_solver_threshold_input("5.0", threshold_validator)
        self.contaminant_al_threshold_input = self.create_solver_threshold_input("3.0", threshold_validator)
        self.contaminant_p_threshold_input = self.create_solver_threshold_input("0.1", threshold_validator)
        self.contaminant_mn_threshold_input = self.create_solver_threshold_input("0.1", threshold_validator)
        for label, widget in [
            ("Si threshold:", self.contaminant_si_threshold_input),
            ("Al threshold:", self.contaminant_al_threshold_input),
            ("P threshold:", self.contaminant_p_threshold_input),
            ("Mn threshold:", self.contaminant_mn_threshold_input),
        ]:
            contaminant_layout.addWidget(QLabel(label))
            contaminant_layout.addWidget(widget)
        contaminant_layout.addStretch()
        self.solver_config_layout.addLayout(contaminant_layout)

        low_fe_layout = QHBoxLayout()
        self.prefer_low_fe_stockpiles_checkbox = QCheckBox("Try blending low grade stockpiles/chunks first")
        self.low_fe_threshold_input = self.create_solver_threshold_input("58.0", threshold_validator)
        low_fe_layout.addWidget(self.prefer_low_fe_stockpiles_checkbox)
        low_fe_layout.addWidget(QLabel("Fe threshold:"))
        low_fe_layout.addWidget(self.low_fe_threshold_input)
        low_fe_layout.addStretch()
        self.solver_config_layout.addLayout(low_fe_layout)

        submit_layout = QHBoxLayout()
        self.solver_config_submit_button = QPushButton("Submit")
        self.solver_config_submit_button.clicked.connect(self.handle_solver_configuration_submit)
        submit_layout.addWidget(self.solver_config_submit_button)
        submit_layout.addStretch()
        self.solver_config_layout.addLayout(submit_layout)
        self.solver_config_layout.addStretch()

    def create_solver_threshold_input(self, default_value, validator):
        input_field = QLineEdit()
        input_field.setText(default_value)
        input_field.setValidator(validator)
        input_field.setFixedWidth(70)
        return input_field

    def update_direct_tip_input_state(self, checked=None):
        direct_tip_enabled = self.direct_tip_enabled_checkbox.isChecked()
        self.direct_tip_cash_incentive_input.setEnabled(direct_tip_enabled)
        if hasattr(self, "min_grade_block_pair_duration_input"):
            self.min_grade_block_pair_duration_input.setEnabled(direct_tip_enabled)
        if hasattr(self, "stay_on_same_grade_block_pair_incentive_input"):
            self.stay_on_same_grade_block_pair_incentive_input.setEnabled(direct_tip_enabled)
        if hasattr(self, "grade_block_lock_checkbox"):
            self.grade_block_lock_checkbox.setEnabled(direct_tip_enabled)

    def is_direct_tip_enabled(self):
        solver_config = self.normalized_solver_config()
        return bool(solver_config.get("direct_tip_enabled", True))

    def normalized_solver_config(self, solver_config=None):
        defaults = {
            "stockpile_feasibility_mode": "stockpile_must_be_feasible",
            "min_feed_duration_hours": None,
            "direct_tip_enabled": True,
            "direct_tip_cash_incentive": 10.0,
            "stay_on_same_blend_incentive": 0.0,
            "blend_option_timeout_seconds": 30.0,
            "max_blend_options_per_steady_state": 12,
            "min_grade_block_pair_duration_hours": 0.0,
            "stay_on_same_grade_block_pair_incentive": 0.0,
            "grade_block_lock_enabled": False,
            "prefer_fewer_stockpiles": False,
            "balance_preference": "none",
            "prefer_amt_stockpiles": False,
            "prefer_contaminated_stockpiles": False,
            "contaminant_thresholds": {
                "si": 5.0,
                "al": 3.0,
                "p": 0.1,
                "mn": 0.1,
            },
            "prefer_low_fe_stockpiles": False,
            "low_fe_threshold": 58.0,
        }
        incoming = solver_config if solver_config is not None else self.solver_config
        if not incoming:
            return defaults

        merged = copy.deepcopy(defaults)
        incoming = copy.deepcopy(incoming)
        contaminant_thresholds = incoming.pop("contaminant_thresholds", None)
        merged.update(incoming)
        if isinstance(contaminant_thresholds, dict):
            merged["contaminant_thresholds"].update(contaminant_thresholds)
        return merged

    def setup_site_configuration(self):
        """Setup for the Site Configuration Form."""
        self.site_config_tab = QWidget()
        self.site_config_tab_index = self.tabs.addTab(self.site_config_tab, "Site Configuration")
        self.site_config_tab.setObjectName("siteConfigTab")  # Set an object name for the stylesheet

        # Get base directory (handles running as a script OR an EXE)
        if getattr(sys, 'frozen', False):  # Running as a PyInstaller EXE
            base_dir = sys._MEIPASS
        else:  # Running as a normal Python script
            base_dir = os.path.dirname(os.path.abspath(__file__))

        # Remove "GUI" if it's part of the base directory
        if "GUI" in base_dir:
            base_dir = base_dir.split("GUI")[0]  # Get the part before "GUI"

        # Construct path to the background image
        background_path = os.path.join(base_dir, "resources", "background.PNG").replace("\\", "/")


        self.site_config_tab.setStyleSheet(f"""
            #siteConfigTab {{
                background-color: #ffffff;
            }}
            #siteConfigCard {{
                background-color: rgba(255, 255, 255, 242);
                border: 1px solid #d9e2ec;
                border-radius: 8px;
            }}
            #siteConfigLogoPanel {{
                background-color: #ffffff;
                border: 0;
            }}
            #siteConfigTitle {{
                color: #1f2933;
                font-size: 22px;
                font-weight: 700;
                padding-bottom: 2px;
            }}
            #siteConfigSubtitle {{
                color: #607080;
                font-size: 12px;
                padding-bottom: 14px;
            }}
            #siteConfigTab QLabel {{
                background: transparent;
            }}
            #siteConfigTab QLineEdit,
            #siteConfigTab QComboBox,
            #siteConfigTab QListWidget,
            #siteConfigTab QDateTimeEdit {{
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                padding: 4px 6px;
                min-height: 20px;
            }}
            #siteConfigTab QLineEdit:disabled,
            #siteConfigTab QComboBox:disabled,
            #siteConfigTab QListWidget:disabled,
            #siteConfigTab QDateTimeEdit:disabled {{
                color: #7b8794;
                background-color: #f5f7fa;
            }}
            #siteConfigTab QPushButton {{
                background-color: #ffffff;
                border: 1px solid #b8c4d2;
                border-radius: 4px;
                padding: 5px 12px;
            }}
            #siteConfigTab QPushButton:hover {{
                background-color: #eef7f0;
                border-color: #98d4a6;
            }}
            #siteConfigTab QPushButton:pressed {{
                background-color: #dff1e3;
            }}
        """)

        outer_layout = QHBoxLayout(self.site_config_tab)
        outer_layout.setContentsMargins(20, 18, 20, 20)
        outer_layout.setSpacing(28)

        form_card = QFrame()
        form_card.setObjectName("siteConfigCard")
        form_card.setMinimumWidth(620)
        form_card.setMaximumWidth(820)
        form_card.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        layout = QFormLayout(form_card)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setHorizontalSpacing(16)
        layout.setVerticalSpacing(10)
        layout.setLabelAlignment(Qt.AlignLeft)
        layout.setFormAlignment(Qt.AlignTop)

        title_label = QLabel("BlendMaster")
        title_label.setObjectName("siteConfigTitle")
        subtitle_label = QLabel("Site configuration and run setup")
        subtitle_label.setObjectName("siteConfigSubtitle")
        layout.addRow(title_label)
        layout.addRow(subtitle_label)

        logo_panel = QFrame()
        logo_panel.setObjectName("siteConfigLogoPanel")
        logo_layout = QVBoxLayout(logo_panel)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_layout.setSpacing(0)
        self.site_config_logo = ScaledPixmapLabel(background_path)
        self.site_config_logo.setAlignment(Qt.AlignCenter)
        self.site_config_logo.setMinimumSize(520, 420)
        self.site_config_logo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        logo_layout.addWidget(self.site_config_logo)

        outer_layout.addWidget(form_card, 0, Qt.AlignTop)
        outer_layout.addWidget(logo_panel, 1)

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
        expit_label = QLabel("Expit Transactions (optional):")
        expit_label.setStyleSheet("font-weight: bold;")
        self.expit_mode = QComboBox()
        self.expit_mode.addItems(["Use Original Expit Transactions", "Update Transactions on Current Time"])
        self.expit_mode.setFixedWidth(300)

        # Expit options are active only when time starts at "Now"
        self.expit_mode.setEnabled(False)
        self.time_mode.currentIndexChanged.connect(
            lambda: self.expit_mode.setEnabled(self.time_mode.currentIndex() == 0)
        )

        layout.addRow(expit_label, self.expit_mode)

        # --- Input 3: Select File ---
        file_label = QLabel("Select APS Mining.csv (optional):")
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

        self.reevaluate_aps_direct_tip_checkbox = QCheckBox("Re-evaluate APS direct tip tonnes")
        self.reevaluate_aps_direct_tip_checkbox.setChecked(False)
        self.aps_crusher_button = QPushButton("Get Crusher Names")
        self.aps_crusher_button.setFixedWidth(140)
        self.aps_crusher_button.setEnabled(False)
        self.aps_crusher_button.clicked.connect(self.load_aps_crusher_names)
        self.aps_crusher_input = QListWidget()
        self.aps_crusher_input.setSelectionMode(QAbstractItemView.MultiSelection)
        self.aps_crusher_input.setMinimumWidth(420)
        self.aps_crusher_input.setFixedHeight(74)
        self.aps_crusher_input.setEnabled(False)

        aps_direct_tip_layout = QVBoxLayout()
        aps_direct_tip_layout.setSpacing(6)
        aps_direct_tip_header_layout = QHBoxLayout()
        aps_direct_tip_header_layout.addWidget(self.reevaluate_aps_direct_tip_checkbox)
        aps_direct_tip_header_layout.addWidget(self.aps_crusher_button)
        aps_direct_tip_header_layout.addStretch()
        aps_direct_tip_layout.addLayout(aps_direct_tip_header_layout)
        aps_direct_tip_layout.addWidget(self.aps_crusher_input)
        layout.addRow(QLabel("APS Direct Tip:"), aps_direct_tip_layout)

        # --- Input 4: Optimised Blend Choices ---
        blend_label = QLabel("Optimised Blend Choices:")
        blend_label.setStyleSheet("font-weight: bold;")
        self.blend_mode = QComboBox()
        self.blend_mode.addItems(["Select Best Result Automatically", "Prompt User at Decision Point"])
        self.blend_mode.setFixedWidth(300)

        layout.addRow(blend_label, self.blend_mode)

        # Save and load button
        self.save_button = QPushButton("Save Project")
        self.save_button.setFixedWidth(100)
        self.save_button.clicked.connect(self.save_state)
        self.save_button.setEnabled(False)

        self.load_button = QPushButton("Load Project")
        self.load_button.setFixedWidth(100)
        self.load_button.clicked.connect(self.load_state)

        save_load_button_layout = QHBoxLayout()
        save_load_button_layout.addWidget(self.save_button)
        save_load_button_layout.addWidget(self.load_button)
        save_load_button_layout.addStretch()
        layout.addRow(save_load_button_layout)
        
        # Submit Button
        self.submit_button = QPushButton("Submit")
        self.submit_button.setFixedWidth(100)
        self.submit_button.setEnabled(True)  # Initially disabled

        self.submit_button.clicked.connect(self.handle_site_config_submit)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.submit_button)
        button_layout.addStretch()

        layout.addRow(button_layout)

        self.update_mine_dropdown()
        self.validate_form()

        # Connect input field changes to form validation
        self.hub_input.currentIndexChanged.connect(self.validate_form)
        self.mine_input.currentIndexChanged.connect(self.validate_form)
        self.time_mode.currentIndexChanged.connect(self.validate_form)
        self.start_time.dateTimeChanged.connect(self.validate_form)
        self.file_path.textChanged.connect(self.validate_form)
        self.blend_mode.currentIndexChanged.connect(self.validate_form)
        self.reevaluate_aps_direct_tip_checkbox.toggled.connect(self.toggle_aps_direct_tip_controls)
        self.reevaluate_aps_direct_tip_checkbox.toggled.connect(self.validate_form)
        self.aps_crusher_input.itemSelectionChanged.connect(self.validate_form)

    def validate_form(self):
        """Enable or disable the submit button based on form completion."""
        aps_direct_tip_ready = True
        if (
            hasattr(self, "reevaluate_aps_direct_tip_checkbox")
            and self.reevaluate_aps_direct_tip_checkbox.isChecked()
        ):
            aps_direct_tip_ready = (
                bool(self.file_path.text().strip())
                and hasattr(self, "aps_crusher_input")
                and bool(self.selected_aps_crusher_names())
            )
        all_fields_populated = (
            self.hub_input.currentIndex() != -1
            and self.mine_input.currentIndex() != -1
            and (self.time_mode.currentIndex() == 0 or self.start_time.dateTime().isValid())
            and self.blend_mode.currentIndex() != -1
            and aps_direct_tip_ready
        )
        self.submit_button.setEnabled(all_fields_populated)
        self.save_button.setEnabled(all_fields_populated)

    def show_progress_dialog(self, message, cancel_callback=None):
        if self.progress_dialog:
            self.progress_dialog.close()

        self.current_cancel_callback = cancel_callback
        cancel_text = "Abort" if cancel_callback else None
        self.progress_dialog = QProgressDialog(message, cancel_text, 0, 0, self)
        self.progress_dialog.setWindowTitle("BlendMaster")
        self.progress_dialog.setAutoClose(False)
        self.progress_dialog.setAutoReset(False)
        if cancel_callback:
            self.progress_dialog.canceled.connect(self.cancel_current_background_task)
        else:
            self.progress_dialog.setCancelButton(None)
        self.progress_dialog.setModal(False)
        self.progress_dialog.setWindowModality(Qt.NonModal)
        self.resize_progress_dialog_for_message(message)
        self.progress_dialog.setWindowIcon(QIcon(r"C:\BlendMaster\blendmaster_OOP\resources\icon.png"))
        self.progress_dialog.show()

    def cancel_current_background_task(self):
        cancel_callback = getattr(self, "current_cancel_callback", None)
        if cancel_callback is None:
            return
        try:
            cancel_callback()
        except Exception:
            self.show_error_popup(traceback.format_exc())
            return
        if self.progress_dialog:
            self.progress_dialog.setLabelText("Abort requested. Finishing the current solver step...")
            self.progress_dialog.setCancelButton(None)
            self.resize_progress_dialog_for_message("Abort requested. Finishing the current solver step...")

    def close_progress_dialog(self):
        self.current_cancel_callback = None
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None

    def update_progress_message(self, message):
        if self.progress_dialog:
            message = str(message)
            self.progress_dialog.setLabelText(message)
            self.resize_progress_dialog_for_message(message)

    def resize_progress_dialog_for_message(self, message):
        if not self.progress_dialog:
            return
        text_width = self.progress_dialog.fontMetrics().horizontalAdvance(str(message))
        dialog_width = min(max(text_width + 110, 360), 900)
        self.progress_dialog.setFixedWidth(dialog_width)

    def run_background_task(self, message, work_fn, on_success, on_error=None, cancel_callback=None):
        self.show_progress_dialog(message, cancel_callback)

        thread = QThread(self)
        worker = BackgroundWorker(work_fn)
        worker.moveToThread(thread)
        task = (thread, worker)
        self.background_tasks.append(task)

        def cleanup():
            try:
                self.background_tasks.remove(task)
            except ValueError:
                pass

        def handle_success(result):
            self.close_progress_dialog()
            try:
                on_success(result)
            except Exception:
                self.show_error_popup(traceback.format_exc())

        def handle_error(error_message):
            self.close_progress_dialog()
            if on_error:
                on_error(error_message)
            else:
                self.show_error_popup(error_message)

        thread.started.connect(worker.run)
        worker.finished.connect(handle_success)
        worker.failed.connect(handle_error)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(cleanup)
        thread.start()

    def browse_file(self):
        """Browse to select a file."""
        file_path, _ = QFileDialog.getOpenFileName(self, "Select File", "", "CSV Files (*.csv);;All Files (*)")
        if file_path:
            self.file_path.setText(file_path)
            if hasattr(self, "aps_crusher_input"):
                self.aps_crusher_input.clear()

    def toggle_aps_direct_tip_controls(self, enabled):
        if hasattr(self, "aps_crusher_button"):
            self.aps_crusher_button.setEnabled(bool(enabled))
        if hasattr(self, "aps_crusher_input"):
            self.aps_crusher_input.setEnabled(bool(enabled))
            if not enabled:
                self.aps_crusher_input.clear()

    @staticmethod
    def normalized_aps_crusher_choice(choice):
        if choice is None:
            return []
        if isinstance(choice, (list, tuple, set)):
            return [str(value).strip() for value in choice if str(value).strip()]
        choice = str(choice).strip()
        return [choice] if choice else []

    def selected_aps_crusher_names(self):
        if not hasattr(self, "aps_crusher_input"):
            return []
        if isinstance(self.aps_crusher_input, QListWidget):
            return [
                item.text().strip()
                for item in self.aps_crusher_input.selectedItems()
                if item.text().strip()
            ]
        if isinstance(self.aps_crusher_input, QComboBox):
            text = self.aps_crusher_input.currentText().strip()
            return [text] if text else []
        return []

    def set_aps_crusher_items(self, crusher_names, selected_crushers=None):
        if not hasattr(self, "aps_crusher_input"):
            return
        selected_crushers = set(self.normalized_aps_crusher_choice(selected_crushers))
        self.aps_crusher_input.clear()
        self.aps_crusher_input.addItems(crusher_names)
        if isinstance(self.aps_crusher_input, QListWidget):
            for row in range(self.aps_crusher_input.count()):
                item = self.aps_crusher_input.item(row)
                item.setSelected(item.text().strip() in selected_crushers)

    def load_aps_crusher_names(self):
        file_path = self.file_path.text().strip() if hasattr(self, "file_path") else ""
        if not file_path:
            QMessageBox.information(
                self,
                "BlendMaster",
                "Select an APS Mining.csv file before loading crusher names.",
            )
            return

        try:
            crusher_names = ExpitDataHandler.get_distinct_crusher_destinations(file_path)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "BlendMaster",
                f"Unable to read crusher names from APS Mining.csv: {exc}",
            )
            return

        previous_selection = self.selected_aps_crusher_names()
        self.set_aps_crusher_items(crusher_names, previous_selection)
        if crusher_names:
            QMessageBox.information(
                self,
                "BlendMaster",
                f"Found {len(crusher_names)} crusher destination(s). Select one or more crushers to re-evaluate.",
            )
        else:
            QMessageBox.information(
                self,
                "BlendMaster",
                "No crusher destinations were found in the selected APS Mining.csv file.",
            )
        self.validate_form()

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
        if not self.is_project_loaded:
        
            self.time_mode_choice = self.time_mode.currentIndex() + 1  # Translate to 1 or 2

            if self.time_mode_choice == 2:  # If "Set Time" is selected
                self.start_time_choice = self.start_time.dateTime().toPyDateTime()

            else: self.start_time_choice = datetime.now()

            self.expit_mode_choice = self.expit_mode.currentIndex() + 1 if self.expit_mode.isEnabled() else 1
            self.file_path_choice = self.file_path.text()
            self.reevaluate_aps_direct_tip_choice = self.reevaluate_aps_direct_tip_checkbox.isChecked()
            self.aps_direct_tip_crusher_choice = (
                self.selected_aps_crusher_names()
                if self.reevaluate_aps_direct_tip_choice
                else []
            )
            self.blend_mode_choice = self.blend_mode.currentIndex() + 1  # Translate to 1 or 2
            
            self.hub_input_choice = self.hub_input.currentText().strip()
            self.mine_input_choice = self.mine_input.currentText().strip()

            if self.reevaluate_aps_direct_tip_choice and not self.aps_direct_tip_crusher_choice:
                QMessageBox.warning(
                    self,
                    "Missing Information",
                    "Load and select a crusher destination before re-evaluating APS direct tip tonnes.",
                )
                return

            if self.hub_input_choice and self.mine_input_choice and self.time_mode_choice and self.start_time_choice and self.expit_mode_choice and self.blend_mode_choice: 
                self.submit_button.setEnabled(False)
                self.run_background_task(
                    "Fetching stockpile inventories from Snowflake...",
                    self.fetch_stockpile_data,
                    self.finish_site_config_submit,
                    self.handle_site_config_error,
                )

            else:
                QMessageBox.warning(self, "Missing Information", "Please fill in all fields.")
            
        else:
            if self.project_load_restore_in_progress:
                self.time_mode.setCurrentIndex(self.time_mode_choice - 1)
                self.start_time.setDateTime(QDateTime(
                    self.start_time_choice.year,
                    self.start_time_choice.month,
                    self.start_time_choice.day,
                    self.start_time_choice.hour,
                    self.start_time_choice.minute,
                    self.start_time_choice.second
                ))
                self.expit_mode.setCurrentText(str(self.expit_mode_choice))
                self.file_path.setText(str(self.file_path_choice))
                if hasattr(self, "reevaluate_aps_direct_tip_checkbox"):
                    self.reevaluate_aps_direct_tip_checkbox.setChecked(
                        bool(getattr(self, "reevaluate_aps_direct_tip_choice", False))
                    )
                if hasattr(self, "aps_crusher_input"):
                    saved_crushers = self.normalized_aps_crusher_choice(
                        getattr(self, "aps_direct_tip_crusher_choice", [])
                    )
                    self.set_aps_crusher_items(saved_crushers, saved_crushers)
                self.blend_mode.setCurrentText(str(self.blend_mode_choice))
                self.hub_input.setCurrentText(str(self.hub_input_choice))
                self.mine_input.setCurrentText(str(self.mine_input_choice))
            else:
                self.time_mode_choice = self.time_mode.currentIndex() + 1
                if self.time_mode_choice == 2:
                    self.start_time_choice = self.start_time.dateTime().toPyDateTime()
                else:
                    self.start_time_choice = datetime.now()

                self.expit_mode_choice = self.expit_mode.currentIndex() + 1 if self.expit_mode.isEnabled() else 1
                self.file_path_choice = self.file_path.text()
                self.reevaluate_aps_direct_tip_choice = self.reevaluate_aps_direct_tip_checkbox.isChecked()
                self.aps_direct_tip_crusher_choice = (
                    self.selected_aps_crusher_names()
                    if self.reevaluate_aps_direct_tip_choice
                    else []
                )
                self.blend_mode_choice = self.blend_mode.currentIndex() + 1
                self.hub_input_choice = self.hub_input.currentText().strip()
                self.mine_input_choice = self.mine_input.currentText().strip()

                if self.reevaluate_aps_direct_tip_choice and not self.aps_direct_tip_crusher_choice:
                    QMessageBox.warning(
                        self,
                        "Missing Information",
                        "Load and select a crusher destination before re-evaluating APS direct tip tonnes.",
                    )
                    return

            if self.hub_input_choice and self.mine_input_choice and self.time_mode_choice and self.start_time_choice and self.expit_mode_choice and self.blend_mode_choice: 
                if not self.stockpile_data:
                    QMessageBox.warning(self, "Missing Project Data", "The loaded project does not contain stockpile inventory data.")
                    return
                self.finish_site_config_submit(self.stockpile_data)

            else:
                QMessageBox.warning(self, "Missing Information", "Please fill in all fields.")   

    def fetch_stockpile_data(self):
        """Fetch stockpile data from OpeningStockpileInventories."""
        hub_input = self.hub_input_choice
        mine_input = self.mine_input_choice
        return self.opening_stockpile_inventories.call_opening_stockpile_inventories(hub_input, mine_input, self.start_time_choice)

    def finish_site_config_submit(self, stockpile_data):
        self.submit_button.setEnabled(True)
        self.save_button.setEnabled(True)
        self.stockpile_data = stockpile_data
        QMessageBox.information(self, "BlendMaster", f"Configuration successfully submitted for Hub: {self.hub_input_choice}, Mine: {self.mine_input_choice}.")

        self.setup_stockpile_table()
        self.tabs.setTabEnabled(self.stockpile_tab_index, True)
        self.tabs.setCurrentIndex(self.stockpile_tab_index)  # Switch to the next tab

    def handle_site_config_error(self, error_message):
        self.submit_button.setEnabled(True)
        self.show_error_popup(error_message)
    
    def setup_stockpile_table(self):
        """Setup for the stockpile table in the new Stockpiles tab with live conditional formatting."""
        # Define Headers (Add "Use" Column)
        headers = [
            "Use",
            "AMT",
            "Stockpile Name",
            "Build",
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

        # Choose data source
        data_source = self.stockpile_data

        # Set Table Dimensions
        self.stockpile_table.setRowCount(len(self.stockpile_data))

        # Populate Stockpile Data
        for row_idx, (stockpile_name, attributes) in enumerate(data_source.items()):
           
            # "Use" Column (Checkbox)
            use_checkbox = QCheckBox()

            AMT_checkbox = QCheckBox()

            
            if self.stockpile_data_use_column:
                # Set the checkbox state based on the value in self.stockpile_data_use_column
                use_checkbox.setChecked(self.stockpile_data_use_column.get(stockpile_name, True))  # Default to checked if not found
                AMT_checkbox.setChecked(self.stockpile_data_AMT_column.get(stockpile_name, False))  # Default to checked if not found

            else:
                use_checkbox.setChecked(True)  # Default to checked
                AMT_checkbox.setChecked(False)

            # Center the checkbox using a QWidget and layout
            checkbox_widget = QWidget()
            layout = QHBoxLayout(checkbox_widget)
            layout.addWidget(use_checkbox)
            layout.setAlignment(Qt.AlignCenter)  # Center the checkbox
            layout.setContentsMargins(0, 0, 0, 0)  # Remove any extra padding
            self.stockpile_table.setCellWidget(row_idx, 0, checkbox_widget)

            # Center the checkbox using a QWidget and layout (AMT)
            AMT_checkbox_widget = QWidget()
            AMT_layout = QHBoxLayout(AMT_checkbox_widget)
            AMT_layout.addWidget(AMT_checkbox)
            AMT_layout.setAlignment(Qt.AlignCenter)  # Center the checkbox
            AMT_layout.setContentsMargins(0, 0, 0, 0)  # Remove any extra padding
            self.stockpile_table.setCellWidget(row_idx, 1, AMT_checkbox_widget)

            # Stockpile Name (Center-align)
            stockpile_item = QTableWidgetItem(str(stockpile_name))
            stockpile_item.setFlags(Qt.ItemIsEnabled)  # Non-editable
            stockpile_item.setTextAlignment(Qt.AlignCenter)  # Center-align the stockpile name
            self.stockpile_table.setItem(row_idx, 2, stockpile_item)

            # Attributes (Balance and Grades, Center-aligned)
            keys = ["BUILD", "BALANCE", "GRADE_FE", "GRADE_SI", "GRADE_AL", "GRADE_P", "GRADE_MN"]
       
            for col_idx, key in enumerate(keys, start=3):  # Start after "Use", "AMT", "Stockpile Name"

                try:
                    value = attributes[key]
                except KeyError:
                    # Transform keys and retry
                    keys = [k.lower() for k in keys]  # Transform all keys to lowercase
                    if key.lower() in attributes:
                        value = attributes[key.lower()]  # Try accessing with the transformed key
                    else:
                        # Show error message if the key is still not found
                        QMessageBox.warning(self, "Error", f"Unable to find value for key: {key}")
                        value = 0  # Or handle the absence of value appropriately

                if key == "BALANCE" or key == 'balance':
                    # Round balance and apply conditional formatting
                    value = round(float(value)) if value else 0
                    balance_item = QTableWidgetItem(str(value))
                    balance_item.setFlags(Qt.ItemIsEnabled)  # Non-editable
                    balance_item.setTextAlignment(Qt.AlignCenter)  # Center-align value
                    if value < 0:
                        balance_item.setForeground(QColor("red"))
                        font = balance_item.font()
                        font.setBold(True)
                        balance_item.setFont(font)
                    self.stockpile_table.setItem(row_idx, col_idx, balance_item)

                elif key == "BUILD" or key == 'build':
                    build_item = QTableWidgetItem(value)
                    build_item.setFlags(Qt.ItemIsEnabled)  # Non-editable
                    build_item.setTextAlignment(Qt.AlignCenter)  # Center-align value
                    self.stockpile_table.setItem(row_idx, col_idx, build_item)
                
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
        self.stockpile_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.stockpile_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.stockpile_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.stockpile_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.stockpile_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.stockpile_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.stockpile_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.stockpile_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.stockpile_table.horizontalHeader().setSectionResizeMode(8, QHeaderView.Stretch)
        self.stockpile_table.horizontalHeader().setSectionResizeMode(9, QHeaderView.Stretch)
        self.stockpile_table.horizontalHeader().setSectionResizeMode(10, QHeaderView.ResizeToContents)

        if self.setup_stockpile_table_first_call:
            # Connect cellChanged signal to a slot for live formatting
            self.stockpile_table.cellChanged.connect(self.handle_cell_change)

            # Add Submit Button at the Bottom
            submit_button = QPushButton("Submit")
            submit_button.clicked.connect(self.store_stockpile_table)

            # Align button to the bottom-left using layout
            button_layout = QHBoxLayout()
            
            # Don't add the button if returning from the calendar
            if self.submit_calendar_first_call:
                select_all_use_button = QPushButton("Select All Stockpiles")
                select_all_use_button.clicked.connect(lambda: self.set_all_stockpile_checkboxes(0, True))

                clear_use_button = QPushButton("Clear All Stockpiles")
                clear_use_button.clicked.connect(lambda: self.set_all_stockpile_checkboxes(0, False))

                select_all_AMT_button = QPushButton("Select All Stockpiles as AMT")
                select_all_AMT_button.clicked.connect(lambda: self.set_all_stockpile_checkboxes(1, True))

                clear_AMT_button = QPushButton("Clear All Stockpiles as AMT")
                clear_AMT_button.clicked.connect(lambda: self.set_all_stockpile_checkboxes(1, False))

                button_layout.addWidget(select_all_use_button)
                button_layout.addWidget(clear_use_button)
                button_layout.addWidget(select_all_AMT_button)
                button_layout.addWidget(clear_AMT_button)
                button_layout.addWidget(submit_button)  
                button_layout.addStretch()  # Push the button to the left

            self.stockpile_tab_layout.addLayout(button_layout)
            
            self.setup_stockpile_table_first_call = False

    def set_all_stockpile_checkboxes(self, column, checked):
        """Set all checkbox widgets in a Stockpile Inventories checkbox column."""
        state_by_column = {
            0: self.stockpile_data_use_column,
            1: self.stockpile_data_AMT_column
        }

        for row in range(self.stockpile_table.rowCount()):
            checkbox_widget = self.stockpile_table.cellWidget(row, column)
            stockpile_item = self.stockpile_table.item(row, 2)

            if not checkbox_widget or not stockpile_item:
                continue

            checkbox = checkbox_widget.layout().itemAt(0).widget()
            checkbox.setChecked(checked)
            state_by_column[column][stockpile_item.text()] = checked

    def handle_cell_change(self, row, column):
        """Handle live formatting for the Reclaim Threshold column."""
        headers = [
            "Use",
            "AMT",
            "Stockpile Name",
            "Build",
            "Balance (WMT)",
            "Grade Fe (%)",
            "Grade Si (%)",
            "Grade Al (%)",
            "Grade P (%)",
            "Grade Mn (%)",
            "Reclaim Threshold (WMT)"
        ]

        if column == headers.index("Reclaim Threshold (WMT)"):  # Check if the changed cell is in the Reclaim Threshold column
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
            AMT_checkbox_widget = self.stockpile_table.cellWidget(row, 1)  # Get the widget in the "AMT" column

            if checkbox_widget:
                checkbox = checkbox_widget.layout().itemAt(0).widget()  # Extract the QCheckBox
                
                if checkbox.isChecked():
                    # Get the stockpile name from the relevant column (assuming column 2 for name)
                    stockpile_name = self.stockpile_table.item(row, 2).text()
                    # Store the stockpile name and its "Use" status (True for checked, False otherwise)
                    self.stockpile_data_use_column[stockpile_name] = True
                    
                    if AMT_checkbox_widget:
                        AMT_checkbox = AMT_checkbox_widget.layout().itemAt(0).widget()  # Extract the AMT QCheckBox

                        if AMT_checkbox.isChecked():
                            # Store the stockpile name and its "AMT" status (True for checked, False otherwise)
                            self.stockpile_data_AMT_column[stockpile_name] = True

                        else:
                            self.stockpile_data_AMT_column[stockpile_name] = False

                    # Retrieve Reclaim Threshold
                    reclaim_item = self.stockpile_table.item(row, self.stockpile_table.columnCount() - 1)
                    reclaim_threshold = float(reclaim_item.text()) if reclaim_item else 0

                    # Update stockpile data
                    updated_stockpile_data[stockpile_name] = self.stockpile_data.get(stockpile_name, {})
                    updated_stockpile_data[stockpile_name]["reclaim_threshold"] = reclaim_threshold
                    updated_stockpile_data[stockpile_name]["AMT"] = self.stockpile_data_AMT_column[stockpile_name]

                else:
                    # Optional: Store unchecked stockpiles
                    stockpile_name = self.stockpile_table.item(row, 2).text()
                    self.stockpile_data_use_column[stockpile_name] = False

                
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

        if self.updated_stockpile_data:
            # Enable the next tab (Calendar Tab)
            self.setup_calendar()
            has_AMT_stockpiles = any(value.get("amt", False) for value in self.updated_stockpile_data.values())

            if has_AMT_stockpiles:
                self.setup_AMT_stockpile_table()
                self.tabs.setTabEnabled(self.AMT_stockpile_tab_index, True)
                self.tabs.setCurrentIndex(self.AMT_stockpile_tab_index)  # Switch to AMT tab
            else:
                self.hex_sequence_table = []
                self.hex_sequence_table_argument = []
                self.activate_manual_setup_tab()
                self.navigate_to_solver_configuration()
        else:
            QMessageBox.information(self, "BlendMaster", "No stockpiles selected!\nPlease select stockpiles to proceed.")

    def set_default_manual_schedule_periods(self):
        if self.default_start_datetime and self.default_end_datetime:
            return

        periods = PeriodManager()
        periods.calculate_periods(self.start_time_choice or datetime.now())
        self.set_start_and_end_datetime(periods)

    def activate_manual_setup_tab(self):
        self.set_default_manual_schedule_periods()
        self.setup_blends_tab()
        self.tabs.setTabEnabled(self.blend_config_tab_index, True)

    def navigate_to_solver_configuration(self):
        self.load_solver_config_inputs()
        self.tabs.setTabEnabled(self.solver_config_tab_index, True)
        self.tabs.setCurrentIndex(self.solver_config_tab_index)

    def handle_solver_configuration_submit(self):
        if not self.store_solver_config_inputs():
            return

        self.setup_calendar()

        self.tabs.setTabEnabled(self.calendar_tab_index, True)
        self.tabs.setCurrentIndex(self.calendar_tab_index)

    def parse_float_from_table_item(self, item, default=0.0):
        if not item or not item.text().strip():
            return default
        try:
            return float(item.text().strip())
        except ValueError:
            return default

    def get_AMT_chunk_setting(self, stockpile_name):
        return self.AMT_chunk_settings.get(stockpile_name, {
            "average_reclaim_rate": 1000.0,
            "chunk_reclaim_hours": 1.0,
            "chunk_size": 1000.0
        })

    def calculate_AMT_chunk_size(self, average_reclaim_rate, chunk_reclaim_hours):
        return max(float(average_reclaim_rate), 0.0) * max(float(chunk_reclaim_hours), 0.0)

    def handle_AMT_chunk_cell_change(self, row, column):
        if column not in (1, 2):
            return

        stockpile_item = self.AMT_stockpile_table.item(row, 0)
        if not stockpile_item:
            return

        average_reclaim_rate = self.parse_float_from_table_item(self.AMT_stockpile_table.item(row, 1))
        chunk_reclaim_hours = self.parse_float_from_table_item(self.AMT_stockpile_table.item(row, 2))
        chunk_size = self.calculate_AMT_chunk_size(average_reclaim_rate, chunk_reclaim_hours)

        self.AMT_stockpile_table.blockSignals(True)
        chunk_size_item = QTableWidgetItem(f"{chunk_size:.0f}")
        chunk_size_item.setFlags(Qt.ItemIsEnabled)
        chunk_size_item.setTextAlignment(Qt.AlignCenter)
        self.AMT_stockpile_table.setItem(row, 3, chunk_size_item)
        self.AMT_stockpile_table.blockSignals(False)

        stockpile_name = stockpile_item.text()
        self.AMT_chunk_settings[stockpile_name] = {
            "average_reclaim_rate": average_reclaim_rate,
            "chunk_reclaim_hours": chunk_reclaim_hours,
            "chunk_size": chunk_size
        }

        if hasattr(self, "draw_AMT_map"):
            self.draw_AMT_map.update_chunk_settings(copy.deepcopy(self.AMT_chunk_settings))

    def store_AMT_chunk_settings(self):
        settings = {}
        for row in range(self.AMT_stockpile_table.rowCount()):
            stockpile_item = self.AMT_stockpile_table.item(row, 0)
            if not stockpile_item:
                continue

            stockpile_name = stockpile_item.text()
            average_reclaim_rate = self.parse_float_from_table_item(self.AMT_stockpile_table.item(row, 1), 1000.0)
            chunk_reclaim_hours = self.parse_float_from_table_item(self.AMT_stockpile_table.item(row, 2), 1.0)
            chunk_size = self.calculate_AMT_chunk_size(average_reclaim_rate, chunk_reclaim_hours)

            if average_reclaim_rate <= 0 or chunk_reclaim_hours <= 0:
                QMessageBox.warning(
                    self,
                    "BlendMaster",
                    f"Average Reclaim Rate and Chunk Reclaim Hours must be positive for {stockpile_name}."
                )
                return False

            settings[stockpile_name] = {
                "average_reclaim_rate": average_reclaim_rate,
                "chunk_reclaim_hours": chunk_reclaim_hours,
                "chunk_size": chunk_size
            }

            self.AMT_stockpile_table.blockSignals(True)
            chunk_size_item = QTableWidgetItem(f"{chunk_size:.0f}")
            chunk_size_item.setFlags(Qt.ItemIsEnabled)
            chunk_size_item.setTextAlignment(Qt.AlignCenter)
            self.AMT_stockpile_table.setItem(row, 3, chunk_size_item)
            self.AMT_stockpile_table.blockSignals(False)

        self.AMT_chunk_settings = settings

        if hasattr(self, "draw_AMT_map"):
            self.draw_AMT_map.update_chunk_settings(copy.deepcopy(self.AMT_chunk_settings))

        return True

    def setup_AMT_stockpile_table(self):
        """Setup for the stockpile table in the new Stockpiles tab with live conditional formatting."""
        
        # Define Headers (Add "Use" Column)
        headers = [
            "AMT Stockpiles",
            "Average Reclaim Rate (t/h)",
            "Chunk Reclaim Hours",
            "Chunk Size (WMT)"
        ]
        self.AMT_stockpile_table.setColumnCount(len(headers))
        self.AMT_stockpile_table.setHorizontalHeaderLabels(headers)
        self.AMT_stockpile_table.verticalHeader().setVisible(False)

        # Bold headers
        header_font = self.AMT_stockpile_table.horizontalHeader().font()
        header_font.setBold(True)
        self.AMT_stockpile_table.horizontalHeader().setFont(header_font)

        # Choose data source
        data_source = {
            key: value for key, value in self.updated_stockpile_data.items() if value.get("amt", False)
        }

        builds = [value["build"] for value in self.updated_stockpile_data.values() if value.get("amt", False)]

        if data_source:
            if (
                getattr(self, "project_load_restore_in_progress", False)
                and self.restore_loaded_AMT_data_to_database(data_source)
            ):
                self.finish_AMT_stockpile_table(data_source, getattr(self, "AMT_stockpile_data", {}))
                return

            if getattr(self, "project_load_restore_in_progress", False):
                self.project_load_waiting_for_AMT = True

            self.run_background_task(
                "Fetching AMT stockpile data from Snowflake...",
                lambda: self.opening_stockpile_inventories.call_opening_AMT_stockpile_inventories(
                    builds,
                    self.start_time_choice,
                ),
                lambda AMT_stockpile_data: self.finish_AMT_stockpile_table_from_fetch(data_source, AMT_stockpile_data),
                self.handle_AMT_stockpile_fetch_error,
            )
            return

        QMessageBox.information(self, "BlendMaster", f"No AMT Stockpile Selected.")
        self.opening_stockpile_inventories.clear_AMT_stockpile_database()
        self.finish_AMT_stockpile_table(data_source, {})

    def restore_loaded_AMT_data_to_database(self, data_source):
        AMT_stockpile_data = getattr(self, "AMT_stockpile_data", {}) or {}
        if not isinstance(AMT_stockpile_data, dict) or not AMT_stockpile_data:
            return False

        selected_footprints = {str(stockpile_name).upper() for stockpile_name in data_source}
        saved_footprints = {
            str(footprint).upper()
            for footprint, rows in AMT_stockpile_data.items()
            if rows
        }
        if not selected_footprints.issubset(saved_footprints):
            return False

        self.opening_stockpile_inventories.save_AMT_to_database(AMT_stockpile_data)
        return True

    def finish_AMT_stockpile_table_from_fetch(self, data_source, AMT_stockpile_data):
        self.finish_AMT_stockpile_table(data_source, AMT_stockpile_data)

        if getattr(self, "project_load_waiting_for_AMT", False):
            self.project_load_waiting_for_AMT = False
            self.continue_project_load_after_stockpile_setup()

    def handle_AMT_stockpile_fetch_error(self, error_message):
        self.project_load_waiting_for_AMT = False
        self.project_load_restore_in_progress = False
        self.show_error_popup(error_message)

    def finish_AMT_stockpile_table(self, data_source, AMT_stockpile_data):
        self.AMT_stockpile_data = AMT_stockpile_data or {}
        self.start_dash_AMT_map_thread()
        self.refresh_AMT_map_data_from_database()

        # Set Table Dimensions
        self.AMT_stockpile_table.setRowCount(len(data_source))

        # Populate Stockpile Data
        for row_idx, (stockpile_name, attributes) in enumerate(data_source.items()):
           
            if attributes["amt"]:

                # Stockpile Name (Center-align)
                stockpile_item = QTableWidgetItem(str(stockpile_name))
                stockpile_item.setFlags(Qt.ItemIsEnabled)  # Non-editable
                stockpile_item.setTextAlignment(Qt.AlignCenter)  # Center-align the stockpile name
                self.AMT_stockpile_table.setItem(row_idx, 0, stockpile_item)

                chunk_setting = self.get_AMT_chunk_setting(stockpile_name)
                average_reclaim_rate = chunk_setting.get("average_reclaim_rate", 1000.0)
                chunk_reclaim_hours = chunk_setting.get("chunk_reclaim_hours", 1.0)
                chunk_size = self.calculate_AMT_chunk_size(average_reclaim_rate, chunk_reclaim_hours)

                average_rate_item = QTableWidgetItem(f"{average_reclaim_rate:.0f}")
                average_rate_item.setTextAlignment(Qt.AlignCenter)
                self.AMT_stockpile_table.setItem(row_idx, 1, average_rate_item)

                chunk_hours_item = QTableWidgetItem(f"{chunk_reclaim_hours:g}")
                chunk_hours_item.setTextAlignment(Qt.AlignCenter)
                self.AMT_stockpile_table.setItem(row_idx, 2, chunk_hours_item)

                chunk_size_item = QTableWidgetItem(f"{chunk_size:.0f}")
                chunk_size_item.setFlags(Qt.ItemIsEnabled)
                chunk_size_item.setTextAlignment(Qt.AlignCenter)
                self.AMT_stockpile_table.setItem(row_idx, 3, chunk_size_item)

        # Resize Columns
        self.AMT_stockpile_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.AMT_stockpile_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.AMT_stockpile_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.AMT_stockpile_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.store_AMT_chunk_settings()

        if self.setup_AMT_stockpile_table_first_call:
            self.AMT_stockpile_table.cellChanged.connect(self.handle_AMT_chunk_cell_change)

            # AMT map view inside a QFrame
            self.AMT_map_view = CustomWebEngineView()

            # Frame to surround the map view
            self.AMT_map_frame = QFrame()
            self.AMT_map_frame.setFrameShape(QFrame.Box)
            self.AMT_map_frame.setFrameShadow(QFrame.Sunken)
            self.AMT_map_frame.setLineWidth(1)
            self.AMT_map_frame.setStyleSheet("border: 0.5px solid black;")
            frame_layout = QVBoxLayout()
            frame_layout.addWidget(self.AMT_map_view)
            self.AMT_map_frame.setLayout(frame_layout)

            # Horizontal layout for map view and button
            self.AMT_stockpile_tab_vertical_layout = QVBoxLayout()
            self.AMT_stockpile_tab_vertical_layout.addWidget(self.AMT_map_frame)

            # Add a button to load the chart
            self.load_AMT_button = QPushButton("Load or Update AMT Map")
            self.load_AMT_button.setFixedWidth(200)
            self.load_AMT_button.setStyleSheet("font-size: 16px; padding: 8px;")  # Optional styling
            self.load_AMT_button.clicked.connect(self.load_AMT_map)  # Connect button to function
            self.AMT_stockpile_tab_vertical_layout.addWidget(self.load_AMT_button)

            # Add Submit Button at the Bottom
            submit_button = QPushButton("Submit")
            submit_button.setStyleSheet("font-size: 16px; padding: 8px;")  # Optional styling
            submit_button.clicked.connect(self.store_hex_sequence_table)

            # Align button to the bottom-left using layout
            button_layout = QHBoxLayout()
            button_layout.addWidget(submit_button)
            button_layout.addStretch()  # Push the button to the left
            self.AMT_stockpile_tab_vertical_layout.addLayout(button_layout)

            # Add the vertical layout to the main layout
            self.AMT_stockpile_tab_layout.addLayout(self.AMT_stockpile_tab_vertical_layout, stretch=1)
            
            self.setup_AMT_stockpile_table_first_call = False  

    def refresh_AMT_map_data_from_database(self):
        draw_AMT_map = getattr(self, "draw_AMT_map", None)
        if draw_AMT_map is None:
            return

        draw_AMT_map.update_chunk_settings(copy.deepcopy(self.AMT_chunk_settings))
        draw_AMT_map.selected_points = copy.deepcopy(self.hex_sequence_table or [])
        draw_AMT_map.data = draw_AMT_map.fetch_data()
        draw_AMT_map.unique_footprints = draw_AMT_map.get_unique_footprints()
        draw_AMT_map.clean_up_hex_sequence_table()
        draw_AMT_map.update_sequence_counter()

    def get_AMT_stockpile_data(self, builds):
        if any(self.stockpile_data_AMT_column.values()):
            QMessageBox.information(self, "BlendMaster", f"Calling Snowflake Query..")
            self.AMT_stockpile_data = self.opening_stockpile_inventories.call_opening_AMT_stockpile_inventories(
                builds,
                self.start_time_choice,
            )
        else:    
            QMessageBox.information(self, "BlendMaster", f"No AMT Stockpile Selected.")
            self.opening_stockpile_inventories.clear_AMT_stockpile_database()

    def store_hex_sequence_table(self):
        if not self.store_AMT_chunk_settings():
            return

        self.hex_sequence_table = self.draw_AMT_map.return_hex_sequence()

        if not any(not isinstance(item, dict) for item in self.hex_sequence_table):
            self.hex_sequence_table_argument = copy.deepcopy(self.hex_sequence_table)
            self.total_AMT_stockpile_balances = {}
            self.populate_total_AMT_stockpile_balances()
            self.activate_manual_setup_tab()
            self.navigate_to_solver_configuration()
        else:
            QMessageBox.warning(self, "BlendMaster", "Invalid entries detected!\nPlease regenerate chunks for the selected AMT stockpiles.")
    
    def populate_total_AMT_stockpile_balances(self):
        # Extract unique footprints from the hex sequence table
        unique_footprints = set(hex_entry.get('footprint') for hex_entry in self.hex_sequence_table if 'footprint' in hex_entry)

        # Calculate the total balance for each unique footprint
        for footprint in unique_footprints:
            filtered_hexes = [
                hex_entry for hex_entry in self.hex_sequence_table
                if hex_entry.get('footprint') == footprint
            ]
            total_balance = sum(max(float(hex_entry.get('balance', 0) or 0), 0) for hex_entry in filtered_hexes)
            self.total_AMT_stockpile_balances[footprint] = total_balance
        
    def setup_calendar(self):
        """Setup for the main table with a Submit button."""
            
        self.calendar_headers = ["", "Preplan", "Period_1", "Period_2"]  # Column headers
        self.calendar_rows = []

        # Static Rows with default values of 0
        direct_tip_enabled = self.is_direct_tip_enabled()
        direct_tip_editables = [direct_tip_enabled, direct_tip_enabled, direct_tip_enabled]
        direct_tip_max_defaults = ["1", "1", "1"] if direct_tip_enabled else ["0", "0", "0"]

        self.calendar_rows.extend([
            ("Reclaim Equipment", [False, False, False], "green", ["", "", ""]),
            {"reclaim_equipment_max_reclaim_rate": ("  Max Reclaim Rate", [True, True, True], "green", ["1000", "1000", "1000"])},

            ("Crusher", [False, False, False], "blue", ["", "", ""]),
            {"crusher_rate": ("  Rate", [True, True, True], "blue", ["1000", "1000", "1000"])},
            ("  Direct Tip Ratio", [False, False, False], "blue", ["", "", ""]),
            {"crusher_direct_tip_ratio_min": ("    Min", direct_tip_editables, "blue", ["0", "0", "0"])},
            {"crusher_direct_tip_ratio_max": ("    Max", direct_tip_editables, "blue", direct_tip_max_defaults)},

            ("  Target", [False, False, False], "blue", ["", "", ""]),
            ("    Fe", [False, False, False], "blue", ["", "", ""]),
            {"crusher_target_fe_min": ("      Min", [True, True, True], "blue", ["0", "0", "0"])},
            {"crusher_target_fe_max": ("      Max", [True, True, True], "blue", ["100", "100", "100"])},

            ("    Si", [False, False, False], "blue", ["", "", ""]),
            {"crusher_target_si_min": ("      Min", [True, True, True], "blue", ["0", "0", "0"])},
            {"crusher_target_si_max": ("      Max", [True, True, True], "blue", ["100", "100", "100"])},

            ("    Al", [False, False, False], "blue", ["", "", ""]),
            {"crusher_target_al_min": ("      Min", [True, True, True], "blue", ["0", "0", "0"])},
            {"crusher_target_al_max": ("      Max", [True, True, True], "blue", ["100", "100", "100"])},

            ("    P", [False, False, False], "blue", ["", "", ""]),
            {"crusher_target_p_min": ("      Min", [True, True, True], "blue", ["0", "0", "0"])},
            {"crusher_target_p_max": ("      Max", [True, True, True], "blue", ["100", "100", "100"])},

            ("    Mn", [False, False, False], "blue", ["", "", ""]),
            {"crusher_target_mn_min": ("      Min", [True, True, True], "blue", ["0", "0", "0"])},
            {"crusher_target_mn_max": ("      Max", [True, True, True], "blue", ["100", "100", "100"])},
        ])

        # Dynamically Add Stockpile Rows with default values
        self.calendar_rows.append(("Stockpiles", [False, False, False], "red", ["", "", ""]))

        for stockpile in self.updated_stockpile_data_keys:
            self.calendar_rows.append({f"stockpiles_{stockpile.lower()}" : (f"  {stockpile}", [False, False, False], "red", ["", "", ""])})
            self.calendar_rows.append({f"stockpiles_{stockpile.lower()}_state" : (f"    State", [True, True, True], "red", ["Auto", "Auto", "Auto"])})
            self.calendar_rows.append({f"stockpiles_{stockpile.lower()}_maximum_quantity": (f"    Maximum Quantity", [True, True, True], "red", ["100000", "100000", "100000"])})
            self.calendar_rows.append({f"stockpiles_{stockpile.lower()}_cost": (f"    Cost", [True, True, True], "red", ["0", "0", "0"])})
            self.calendar_rows.append({f"stockpiles_{stockpile.lower()}_cash": (f"    Cash", [True, True, True], "red", ["10", "10", "10"])})

        self.populate_calendar()
       
        if self.setup_calendar_first_call:
            # Add Submit Button at bottom-Right
            submit_button = QPushButton("Submit")
            submit_button.clicked.connect(self.store_calendar_inputs)  # Connect to store_calendar_inputs method

            # Align button to bottom-right
            button_layout = QHBoxLayout()
            
            button_layout.addWidget(submit_button) 
            button_layout.addStretch()  # Push any other content (if any) to the right

            # Add table and button layout to the main tab layout
            self.main_tab_layout.addLayout(button_layout)
            self.setup_calendar_first_call = False
        
        self.load_calendar_inputs()
    
    def populate_calendar(self):
        # Define Parent Colors
        parent_colors = {
            "green": QColor(200, 255, 200),
            "blue": QColor(200, 200, 255),
            "red": QColor(255, 200, 200),
        }

        # Configure the main table
        self.main_table.setColumnCount(len(self.calendar_headers))
        self.main_table.setRowCount(len(self.calendar_rows))
        self.main_table.setHorizontalHeaderLabels(self.calendar_headers)
        self.main_table.verticalHeader().setVisible(False)

        # Bold Font for Captions
        bold_font = QFont()
        bold_font.setBold(True)

        # Populate Table
        for row_idx, row in enumerate(self.calendar_rows):
            row_key = None
            if isinstance(row, tuple):  # Unpack tuples
                caption, editables, color_group, default_values = row
                # Process the tuple as needed
            elif isinstance(row, dict):  # Handle dictionaries
                for key, value in row.items():
                    row_key = key
                    caption, editables, color_group, default_values = value
                    # Process the dictionary value (tuple) as needed
            
            # Caption Column
            item_caption = QTableWidgetItem(caption)
            item_caption.setFlags(Qt.ItemIsEnabled)  # Non-editable
            item_caption.setFont(bold_font)
            item_caption.setBackground(QBrush(parent_colors[color_group]))  # Parent group color
            self.main_table.setItem(row_idx, 0, item_caption)

            # Editable and Non-Editable Cells with Default Values
            for col_idx, (is_editable, default_value) in enumerate(zip(editables, default_values), start=1):
                if is_editable and row_key and row_key.endswith("_state"):
                    state_combo = QComboBox()
                    state_options = ["Auto", "Build", "Reclaim"]
                    state_value = str(default_value)
                    if state_value not in state_options:
                        state_options.append(state_value)
                    state_combo.addItems(state_options)
                    state_combo.setCurrentText(state_value)
                    state_combo.setStyleSheet("QComboBox { qproperty-alignment: AlignCenter; }")
                    self.main_table.setCellWidget(row_idx, col_idx, state_combo)
                    continue

                item = QTableWidgetItem(str(default_value))
                item.setTextAlignment(Qt.AlignCenter)  # Center align all values
                if not is_editable:
                    item.setFlags(Qt.ItemIsEnabled)  # Non-editable
                    item.setBackground(QBrush(QColor(200, 200, 200)))  # Grey background
                self.main_table.setItem(row_idx, col_idx, item)

        # Resize Columns
        self.main_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

    def load_calendar_inputs(self):
        if self.calendar_inputs:
            if self.calendar_inputs.get("min_stockpiles") is not None:
                self.min_stockpiles = self.calendar_inputs["min_stockpiles"]
            if self.calendar_inputs.get("max_stockpiles") is not None:
                self.max_stockpiles = self.calendar_inputs["max_stockpiles"]
            if self.calendar_inputs.get("min_stockpile_contribution_ratio") is not None:
                self.min_stockpile_contribution_ratio = self.calendar_inputs["min_stockpile_contribution_ratio"]
            self.solver_config = self.normalized_solver_config(
                self.calendar_inputs.get("solver_config", self.solver_config)
            )
            self.load_solver_config_inputs()

        if self.is_project_loaded or not self.submit_calendar_first_call:
            def calendar_values(key, defaults, fallback_key=None):
                saved_values = self.calendar_inputs.get(key, {}) if self.calendar_inputs else {}
                if not saved_values and fallback_key:
                    saved_values = self.calendar_inputs.get(fallback_key, {}) if self.calendar_inputs else {}
                return [
                    saved_values.get(header, defaults.get(header))
                    for header in self.calendar_headers[1:]
                ]
            
            zero_defaults = {"Preplan": 0, "Period_1": 0, "Period_2": 0}
            one_defaults = {"Preplan": 1, "Period_1": 1, "Period_2": 1}
            hundred_defaults = {"Preplan": 100, "Period_1": 100, "Period_2": 100}
            thousand_defaults = {"Preplan": 1000, "Period_1": 1000, "Period_2": 1000}
            direct_tip_enabled = self.is_direct_tip_enabled()
            direct_tip_editables = [direct_tip_enabled, direct_tip_enabled, direct_tip_enabled]
            direct_tip_min_values = (
                calendar_values("crusher_direct_tip_ratio_min", zero_defaults, "crusher_direct_feed_ratio_min")
                if direct_tip_enabled
                else [0, 0, 0]
            )
            direct_tip_max_values = (
                calendar_values("crusher_direct_tip_ratio_max", one_defaults, "crusher_direct_feed_ratio_max")
                if direct_tip_enabled
                else [0, 0, 0]
            )

            self.calendar_rows[1]["reclaim_equipment_max_reclaim_rate"] = ("  Max Reclaim Rate", [True, True, True], "green", calendar_values("reclaim_equipment_max_reclaim_rate", thousand_defaults))
            self.calendar_rows[3]["crusher_rate"] = ("  Rate", [True, True, True], "blue", calendar_values("crusher_rate", thousand_defaults))
            self.calendar_rows[5]["crusher_direct_tip_ratio_min"] = ("    Min", direct_tip_editables, "blue", direct_tip_min_values)
            self.calendar_rows[6]["crusher_direct_tip_ratio_max"] = ("    Max", direct_tip_editables, "blue", direct_tip_max_values)
            self.calendar_rows[9]["crusher_target_fe_min"] = ("      Min", [True, True, True], "blue", calendar_values("crusher_target_fe_min", zero_defaults))
            self.calendar_rows[10]["crusher_target_fe_max"] = ("      Max", [True, True, True], "blue", calendar_values("crusher_target_fe_max", hundred_defaults))
            self.calendar_rows[12]["crusher_target_si_min"] = ("      Min", [True, True, True], "blue", calendar_values("crusher_target_si_min", zero_defaults))
            self.calendar_rows[13]["crusher_target_si_max"] = ("      Max", [True, True, True], "blue", calendar_values("crusher_target_si_max", hundred_defaults))
            self.calendar_rows[15]["crusher_target_al_min"] = ("      Min", [True, True, True], "blue", calendar_values("crusher_target_al_min", zero_defaults))
            self.calendar_rows[16]["crusher_target_al_max"] = ("      Max", [True, True, True], "blue", calendar_values("crusher_target_al_max", hundred_defaults))
            self.calendar_rows[18]["crusher_target_p_min"] = ("      Min", [True, True, True], "blue", calendar_values("crusher_target_p_min", zero_defaults))
            self.calendar_rows[19]["crusher_target_p_max"] = ("      Max", [True, True, True], "blue", calendar_values("crusher_target_p_max", hundred_defaults))
            self.calendar_rows[21]["crusher_target_mn_min"] = ("      Min", [True, True, True], "blue", calendar_values("crusher_target_mn_min", zero_defaults))
            self.calendar_rows[22]["crusher_target_mn_max"] = ("      Max", [True, True, True], "blue", calendar_values("crusher_target_mn_max", hundred_defaults))

            
            start_index = 24
            calendar_index = start_index  # Start populating calendar_rows after the static crusher rows

            for stockpile in self.updated_stockpile_data_keys:
                # Populate the rows using calendar_index
                self.calendar_rows[calendar_index][f"stockpiles_{stockpile.lower()}"] = (
                    f"  {stockpile}", [False, False, False], "red", ["", "", ""]
                )
                try:
                    self.calendar_rows[calendar_index + 1][f"stockpiles_{stockpile.lower()}_state"] = (
                        f"    State", [True, True, True], "red",
                        list(self.calendar_inputs[f"stockpiles_{stockpile.lower()}_state"].values())
                    )
                    self.calendar_rows[calendar_index + 2][f"stockpiles_{stockpile.lower()}_maximum_quantity"] = (
                        f"    Maximum Quantity", [True, True, True], "red",
                        list(self.calendar_inputs[f"stockpiles_{stockpile.lower()}_maximum_quantity"].values())
                    )
                    self.calendar_rows[calendar_index + 3][f"stockpiles_{stockpile.lower()}_cost"] = (
                        f"    Cost", [True, True, True], "red",
                        list(self.calendar_inputs[f"stockpiles_{stockpile.lower()}_cost"].values())
                    )
                    self.calendar_rows[calendar_index + 4][f"stockpiles_{stockpile.lower()}_cash"] = (
                        f"    Cash", [True, True, True], "red",
                        list(self.calendar_inputs[f"stockpiles_{stockpile.lower()}_cash"].values())
                    )

                    # Increment calendar_index by 5 for the next stockpile
                    calendar_index += 5

                except: 

                    self.calendar_rows.append({f"stockpiles_{stockpile.lower()}" : (f"  {stockpile}", [False, False, False], "red", ["", "", ""])})
                    self.calendar_rows.append({f"stockpiles_{stockpile.lower()}_state" : (f"    State", [True, True, True], "red", ["Auto", "Auto", "Auto"])})
                    self.calendar_rows.append({f"stockpiles_{stockpile.lower()}_maximum_quantity": (f"    Maximum Quantity", [True, True, True], "red", ["100000", "100000", "100000"])})
                    self.calendar_rows.append({f"stockpiles_{stockpile.lower()}_cost": (f"    Cost", [True, True, True], "red", ["0", "0", "0"])})
                    self.calendar_rows.append({f"stockpiles_{stockpile.lower()}_cash": (f"    Cash", [True, True, True], "red", ["10", "10", "10"])})

                    QMessageBox.information(self, "BlendMaster", f"{stockpile} added to calendar")
                    
            self.populate_calendar()
            self.store_calendar_inputs_no_run()

    def get_main_table_cell_text(self, row_idx, col_idx):
        widget = self.main_table.cellWidget(row_idx, col_idx)
        if isinstance(widget, QComboBox):
            return widget.currentText().strip()

        item = self.main_table.item(row_idx, col_idx)
        return item.text().strip() if item and item.text().strip() else None

    def store_calendar_inputs_no_run(self):
        """Extract and store user entries from the table into a structured format with concatenated keys and modified types. Also calls the main optimised run"""

        self.submit_calendar_first_call = False
        
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
                value = self.get_main_table_cell_text(row_idx, col_idx)
                row_data[header] = value

            # Store the data in the dictionary
            self.calendar_inputs[full_key] = row_data

        # Modify types
        self.calendar_inputs = {
            outer_key: {
                inner_key: (
                    float(inner_value) if inner_value is not None and "state" not in outer_key.lower() else inner_value
                )
                for inner_key, inner_value in outer_value.items()
            }
            for outer_key, outer_value in self.calendar_inputs.items()
        }

        self.store_stockpile_constraint_inputs()

    def load_solver_config_inputs(self):
        if not hasattr(self, "min_stockpiles_input"):
            return

        if self.calendar_inputs:
            self.min_stockpiles = self.calendar_inputs.get("min_stockpiles", self.min_stockpiles)
            self.max_stockpiles = self.calendar_inputs.get("max_stockpiles", self.max_stockpiles)
            self.min_stockpile_contribution_ratio = self.calendar_inputs.get(
                "min_stockpile_contribution_ratio",
                self.min_stockpile_contribution_ratio
            )
            self.solver_config = self.normalized_solver_config(
                self.calendar_inputs.get("solver_config", self.solver_config)
            )

        self.min_stockpiles_input.setText("" if self.min_stockpiles is None else str(self.min_stockpiles))
        self.max_stockpiles_input.setText("" if self.max_stockpiles is None else str(self.max_stockpiles))
        self.min_stockpile_contribution_ratio_input.setText(str(self.min_stockpile_contribution_ratio))

        solver_config = self.normalized_solver_config()
        self.solver_config = copy.deepcopy(solver_config)
        feasibility_label = {
            "stockpile_must_be_feasible": "Stockpile blend must be feasible",
            "stockpile_can_rely_on_grade_blocks": "Stockpile blend can rely on grade blocks"
        }.get(
            solver_config.get("stockpile_feasibility_mode", "stockpile_must_be_feasible"),
            "Stockpile blend must be feasible"
        )
        self.stockpile_feasibility_combo.setCurrentText(feasibility_label)
        min_feed_duration = solver_config.get("min_feed_duration_hours")
        self.min_feed_duration_input.setText(
            "" if min_feed_duration in (None, "") else str(min_feed_duration)
        )
        self.direct_tip_enabled_checkbox.setChecked(bool(solver_config.get("direct_tip_enabled", True)))
        self.direct_tip_cash_incentive_input.setText(str(solver_config.get("direct_tip_cash_incentive", 10.0)))
        self.stay_on_same_blend_incentive_input.setText(str(solver_config.get("stay_on_same_blend_incentive", 0.0)))
        self.blend_option_timeout_input.setText(str(solver_config.get("blend_option_timeout_seconds", 30.0)))
        self.max_blend_options_input.setText(str(solver_config.get("max_blend_options_per_steady_state", 12)))
        self.min_grade_block_pair_duration_input.setText(str(solver_config.get("min_grade_block_pair_duration_hours", 0.0)))
        self.stay_on_same_grade_block_pair_incentive_input.setText(str(solver_config.get("stay_on_same_grade_block_pair_incentive", 0.0)))
        self.grade_block_lock_checkbox.setChecked(bool(solver_config.get("grade_block_lock_enabled", False)))
        self.update_direct_tip_input_state()
        self.prefer_fewer_stockpiles_checkbox.setChecked(bool(solver_config.get("prefer_fewer_stockpiles", False)))
        balance_preference = solver_config.get("balance_preference", "none")
        balance_label = {
            "none": "No balance preference",
            "lower": "Lower balance first",
            "higher": "Higher balance first"
        }.get(balance_preference, "No balance preference")
        self.balance_preference_combo.setCurrentText(balance_label)
        self.prefer_amt_stockpiles_checkbox.setChecked(bool(solver_config.get("prefer_amt_stockpiles", False)))
        self.prefer_contaminated_stockpiles_checkbox.setChecked(bool(solver_config.get("prefer_contaminated_stockpiles", False)))
        contaminant_thresholds = solver_config.get("contaminant_thresholds", {})
        self.contaminant_si_threshold_input.setText(str(contaminant_thresholds.get("si", 5.0)))
        self.contaminant_al_threshold_input.setText(str(contaminant_thresholds.get("al", 3.0)))
        self.contaminant_p_threshold_input.setText(str(contaminant_thresholds.get("p", 0.1)))
        self.contaminant_mn_threshold_input.setText(str(contaminant_thresholds.get("mn", 0.1)))
        self.prefer_low_fe_stockpiles_checkbox.setChecked(bool(solver_config.get("prefer_low_fe_stockpiles", False)))
        self.low_fe_threshold_input.setText(str(solver_config.get("low_fe_threshold", 58.0)))

    def store_solver_config_inputs(self, show_errors=True):
        if self.calendar_inputs is None:
            self.calendar_inputs = {}

        min_text = self.min_stockpiles_input.text().strip()
        max_text = self.max_stockpiles_input.text().strip()
        ratio_text = self.min_stockpile_contribution_ratio_input.text().strip()
        min_feed_duration_text = self.min_feed_duration_input.text().strip()

        try:
            self.min_stockpiles = int(min_text) if min_text else None
        except ValueError:
            if show_errors:
                QMessageBox.warning(self, "Invalid Input", "Min Stockpiles must be an integer.")
                return False
            self.min_stockpiles = None

        try:
            self.max_stockpiles = int(max_text) if max_text else None
        except ValueError:
            if show_errors:
                QMessageBox.warning(self, "Invalid Input", "Max Stockpiles must be an integer.")
                return False
            self.max_stockpiles = None

        if (
            self.min_stockpiles is not None
            and self.max_stockpiles is not None
            and self.min_stockpiles > self.max_stockpiles
        ):
            if show_errors:
                QMessageBox.warning(self, "Invalid Input", "Min Stockpiles cannot be greater than Max Stockpiles.")
            return False

        try:
            self.min_stockpile_contribution_ratio = (
                float(ratio_text) if ratio_text else Optimizer.MIN_SELECTED_STOCKPILE_BLEND_RATIO
            )
        except ValueError:
            if show_errors:
                QMessageBox.warning(
                    self,
                    "Invalid Input",
                    "Min Stockpile Contribution Ratio must be a number from 0.01 to 1.",
                )
            return False

        if not 0.01 <= self.min_stockpile_contribution_ratio <= 1:
            if show_errors:
                QMessageBox.warning(
                    self,
                    "Invalid Input",
                    "Min Stockpile Contribution Ratio must be between 0.01 and 1.",
                )
            return False

        def parse_threshold(input_widget, label):
            try:
                return float(input_widget.text().strip())
            except ValueError:
                if show_errors:
                    QMessageBox.warning(self, "Invalid Input", f"{label} must be a number.")
                return None

        def parse_non_negative_input(input_widget, label, default=0.0):
            text = input_widget.text().strip()
            if not text:
                return default
            try:
                value = float(text)
            except ValueError:
                if show_errors:
                    QMessageBox.warning(self, "Invalid Input", f"{label} must be a number.")
                return None
            if value < 0:
                if show_errors:
                    QMessageBox.warning(self, "Invalid Input", f"{label} cannot be negative.")
                return None
            return value

        def parse_optional_non_negative(text, label):
            if not text:
                return None
            try:
                value = float(text)
            except ValueError:
                if show_errors:
                    QMessageBox.warning(self, "Invalid Input", f"{label} must be a number.")
                return None
            if value < 0:
                if show_errors:
                    QMessageBox.warning(self, "Invalid Input", f"{label} cannot be negative.")
                return None
            return value

        def parse_positive_int_input(input_widget, label, default):
            text = input_widget.text().strip()
            if not text:
                return default
            try:
                value = int(text)
            except ValueError:
                if show_errors:
                    QMessageBox.warning(self, "Invalid Input", f"{label} must be a whole number.")
                return None
            if value < 1:
                if show_errors:
                    QMessageBox.warning(self, "Invalid Input", f"{label} must be at least 1.")
                return None
            return value

        contaminant_thresholds = {
            "si": parse_threshold(self.contaminant_si_threshold_input, "Si threshold"),
            "al": parse_threshold(self.contaminant_al_threshold_input, "Al threshold"),
            "p": parse_threshold(self.contaminant_p_threshold_input, "P threshold"),
            "mn": parse_threshold(self.contaminant_mn_threshold_input, "Mn threshold"),
        }
        low_fe_threshold = parse_threshold(self.low_fe_threshold_input, "Fe threshold")
        direct_tip_cash_incentive = parse_non_negative_input(
            self.direct_tip_cash_incentive_input,
            "Direct Tip Incentive",
            10.0,
        )
        stay_on_same_blend_incentive = parse_non_negative_input(
            self.stay_on_same_blend_incentive_input,
            "Stay on Same Blend Incentive",
            0.0,
        )
        blend_option_timeout_seconds = parse_non_negative_input(
            self.blend_option_timeout_input,
            "Blend Option Timeout",
            30.0,
        )
        max_blend_options_per_steady_state = parse_positive_int_input(
            self.max_blend_options_input,
            "Max Blend Options per Steady State",
            12,
        )
        min_grade_block_pair_duration_hours = parse_non_negative_input(
            self.min_grade_block_pair_duration_input,
            "Min Grade Block Pair Duration (only applies to longer steady state durations)",
            0.0,
        )
        stay_on_same_grade_block_pair_incentive = parse_non_negative_input(
            self.stay_on_same_grade_block_pair_incentive_input,
            "Stay With Same Grade Block Pair Incentive",
            0.0,
        )
        min_feed_duration_hours = parse_optional_non_negative(
            min_feed_duration_text,
            "Min Stockpile Feed Duration",
        )

        if (
            any(value is None for value in contaminant_thresholds.values())
            or low_fe_threshold is None
            or direct_tip_cash_incentive is None
            or stay_on_same_blend_incentive is None
            or blend_option_timeout_seconds is None
            or max_blend_options_per_steady_state is None
            or min_grade_block_pair_duration_hours is None
            or stay_on_same_grade_block_pair_incentive is None
            or (min_feed_duration_text and min_feed_duration_hours is None)
        ):
            return False

        balance_preference = {
            "No balance preference": "none",
            "Lower balance first": "lower",
            "Higher balance first": "higher"
        }.get(self.balance_preference_combo.currentText(), "none")
        stockpile_feasibility_mode = {
            "Stockpile blend must be feasible": "stockpile_must_be_feasible",
            "Stockpile blend can rely on grade blocks": "stockpile_can_rely_on_grade_blocks"
        }.get(self.stockpile_feasibility_combo.currentText(), "stockpile_must_be_feasible")

        self.solver_config = {
            "stockpile_feasibility_mode": stockpile_feasibility_mode,
            "min_feed_duration_hours": min_feed_duration_hours,
            "direct_tip_enabled": self.direct_tip_enabled_checkbox.isChecked(),
            "direct_tip_cash_incentive": direct_tip_cash_incentive,
            "stay_on_same_blend_incentive": stay_on_same_blend_incentive,
            "blend_option_timeout_seconds": blend_option_timeout_seconds,
            "max_blend_options_per_steady_state": max_blend_options_per_steady_state,
            "min_grade_block_pair_duration_hours": min_grade_block_pair_duration_hours,
            "stay_on_same_grade_block_pair_incentive": stay_on_same_grade_block_pair_incentive,
            "grade_block_lock_enabled": self.grade_block_lock_checkbox.isChecked(),
            "prefer_fewer_stockpiles": self.prefer_fewer_stockpiles_checkbox.isChecked(),
            "balance_preference": balance_preference,
            "prefer_amt_stockpiles": self.prefer_amt_stockpiles_checkbox.isChecked(),
            "prefer_contaminated_stockpiles": self.prefer_contaminated_stockpiles_checkbox.isChecked(),
            "contaminant_thresholds": contaminant_thresholds,
            "prefer_low_fe_stockpiles": self.prefer_low_fe_stockpiles_checkbox.isChecked(),
            "low_fe_threshold": low_fe_threshold,
        }
        self.solver_config = self.normalized_solver_config(self.solver_config)

        self.min_stockpile_contribution_ratio_input.setText(str(self.min_stockpile_contribution_ratio))
        self.calendar_inputs["min_stockpiles"] = self.min_stockpiles
        self.calendar_inputs["max_stockpiles"] = self.max_stockpiles
        self.calendar_inputs["min_stockpile_contribution_ratio"] = self.min_stockpile_contribution_ratio
        self.calendar_inputs["solver_config"] = copy.deepcopy(self.solver_config)
        return True

    def store_stockpile_constraint_inputs(self):
        return self.store_solver_config_inputs()

    def store_calendar_inputs(self):
        """Extract and store user entries from the table into a structured format with concatenated keys and modified types. Also calls the main optimised run"""

        self.submit_calendar_first_call = False
        self.clear_decision_point_output()
        
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
                value = self.get_main_table_cell_text(row_idx, col_idx)
                row_data[header] = value

            # Store the data in the dictionary
            self.calendar_inputs[full_key] = row_data

        # Modify types
        self.calendar_inputs = {
    outer_key: {
        inner_key: (
            float(inner_value) if inner_value is not None and "state" not in outer_key.lower() else inner_value
        )
        for inner_key, inner_value in outer_value.items()
    }
    for outer_key, outer_value in self.calendar_inputs.items()
}

        if not self.store_stockpile_constraint_inputs():
            return
        self.solver_config = self.normalized_solver_config(
            self.calendar_inputs.get("solver_config", self.solver_config)
        )
        self.calendar_inputs["solver_config"] = copy.deepcopy(self.solver_config)

        self.update_decision_point_tab_state()
        self.run_background_task(
            "Optimising blends...",
            self.execute_run_program,
            self.finish_run_program,
            self.handle_run_program_error,
            cancel_callback=self.run_program.request_abort,
        )

    def clear_decision_point_output(self):
        if hasattr(self, "decision_output"):
            self.decision_output.clear()
        if hasattr(self, "decision_table"):
            self.decision_table.clearContents()
            self.decision_table.setRowCount(0)
            self.decision_table.setColumnCount(0)
        if hasattr(self, "decision_status_label"):
            self.decision_status_label.setText("Optimising blends...")
        self.valid_decision_blend_options = []
        self.decision_blend_option_column = None
        self.decision_run_dataframe = pd.DataFrame()
        self.current_decision_dataframe = pd.DataFrame()
        self.decision_display_dataframe = pd.DataFrame()
        self.decision_current_steady_state = None

    def execute_run_program(self):
        active_solver_config = self.normalized_solver_config(
            (self.calendar_inputs or {}).get("solver_config", self.solver_config)
        )
        self.solver_config = copy.deepcopy(active_solver_config)
        if self.calendar_inputs is not None:
            self.calendar_inputs["solver_config"] = copy.deepcopy(active_solver_config)

        return self.run_program.execute(
            self.start_time_choice,
            self.expit_mode_choice,
            self.file_path_choice,
            self.blend_mode_choice,
            self.updated_stockpile_data,
            self.calendar_inputs,
            getattr(self, "hex_sequence_table_argument", []),
            self.min_stockpiles,
            self.max_stockpiles,
            self.min_stockpile_contribution_ratio,
            active_solver_config,
            getattr(self, "reevaluate_aps_direct_tip_choice", False),
            getattr(self, "aps_direct_tip_crusher_choice", []),
        )

    def finish_run_program(self, periods):
        self.set_start_and_end_datetime(periods=periods)
        self.update_decision_point_tab_state()
        self.activate_manual_setup_tab()
        self.refresh_sqlite_reports()
        self.start_dash_optimised_charts_thread()

        if self.project_load_continuation_pending:
            self.project_load_continuation_pending = False
            self.on_blend_data_change()
            self.store_blend_results()
            QMessageBox.information(self, "BlendMaster", "Project loaded successfully!")

    def handle_run_program_error(self, error_message):
        self.project_load_continuation_pending = False

        error_title = "Error"
        error_text = str(error_message)
        if isinstance(error_message, dict):
            error_title = error_message.get("title") or error_title
            error_text = str(error_message.get("message", ""))

        if hasattr(self, "decision_status_label"):
            self.decision_status_label.setText(f"{error_title}. Review diagnostics below.")
        if hasattr(self, "decision_output"):
            self.display_decision_output(f"\n--- {error_title} ---\n{error_text}")

        self.tabs.setTabEnabled(self.calendar_tab_index, True)
        self.tabs.setTabEnabled(self.decision_point_tab_index, True)
        for tab_index in [
            self.results_tab_index,
            self.profiles_tab_index,
            self.sqlite_reports_tab_index,
            self.optimised_grade_profile_tab_index,
        ]:
            self.tabs.setTabEnabled(tab_index, False)
        self.decision_input.setEnabled(False)
        self.enter_button.setEnabled(False)
        self.decision_select_button.setEnabled(False)
        self.tabs.setCurrentIndex(self.decision_point_tab_index)
        self.show_error_popup(error_message)

    def setup_results_tab(self):
        self.results_tab = QWidget()
        self.results_tab_index = self.tabs.addTab(self.results_tab, "Results (Optimised)")
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

    def load_AMT_map(self):
        if not self.store_AMT_chunk_settings():
            return

        try:
            requests.post("http://localhost:8054/trigger-refresh", timeout=5)

        except requests.exceptions.RequestException:
            print("Refresh timed out.")

        self.AMT_map_view.setUrl(QUrl("http://localhost:8054"))

        self.load_AMT_map_first_call = False

    def load_gantt_chart(self):
        self.resize_results_chart_area()
        # Load the Dash app into the QWebEngineView
        self.gantt_chart_view.setUrl(QUrl("http://localhost:8050"))

    def resize_results_chart_area(self):
        desired_height = 520

        conn = None
        try:
            conn = sqlite3.connect("blendmaster.db")
            data = pd.read_sql(
                """
                SELECT blend_ID, steady_state_number
                FROM optimised_blend_report
                """,
                conn
            )

            if not data.empty:
                blend_count = max(data["blend_ID"].nunique(), 1)
                row_count = len(data.drop_duplicates())
                graph_height = min(max(280, 190 + (blend_count * 55)), 720)
                table_height = min(360, 120 + (min(row_count, 8) * 34))
                desired_height = graph_height + table_height + 90
        except Exception:
            desired_height = 520
        finally:
            if conn is not None:
                conn.close()

        available_height = self.results_tab.height() - self.load_chart_button.sizeHint().height() - 28
        available_height = max(420, available_height)
        desired_height = max(420, min(desired_height, available_height))

        self.top_frame.setMinimumHeight(desired_height)
        self.top_frame.setMaximumHeight(desired_height)
    
    def load_manual_gantt_chart(self):
        # Load the Dash app into the QWebEngineView
        self.manual_gantt_view.setUrl(QUrl("http://localhost:8052"))
        
    def setup_profiles_tab(self):
        self.profiles_tab = QWidget()
        self.profiles_tab_index = self.tabs.addTab(self.profiles_tab, "Stockpile Profiles (Optimised)")
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

    def setup_sqlite_reports_tab(self):
        self.sqlite_reports_tab = QWidget()
        self.sqlite_reports_tab_index = self.tabs.addTab(self.sqlite_reports_tab, "SQLite Reports")
        self.sqlite_reports_layout = QVBoxLayout(self.sqlite_reports_tab)

        controls_layout = QHBoxLayout()
        controls_layout.addWidget(QLabel("Report Table:"))

        self.sqlite_report_selector = QComboBox()
        self.sqlite_report_selector.currentIndexChanged.connect(self.load_selected_sqlite_report)
        controls_layout.addWidget(self.sqlite_report_selector)

        refresh_button = QPushButton("Refresh Reports")
        refresh_button.clicked.connect(self.refresh_sqlite_reports)
        controls_layout.addWidget(refresh_button)
        controls_layout.addStretch()

        self.sqlite_reports_layout.addLayout(controls_layout)

        self.sqlite_report_status = QLabel("")
        self.sqlite_reports_layout.addWidget(self.sqlite_report_status)

        self.sqlite_report_table = CustomTableWidget()
        self.sqlite_report_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.sqlite_reports_layout.addWidget(self.sqlite_report_table)

    def get_sqlite_report_tables(self):
        try:
            conn = sqlite3.connect("blendmaster.db")
            query = """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY name
            """
            tables = pd.read_sql(query, conn)["name"].tolist()
            conn.close()
            return tables
        except Exception as e:
            QMessageBox.warning(self, "SQLite Reports", f"Unable to list SQLite tables: {e}")
            return []

    def refresh_sqlite_reports(self):
        current_table = self.sqlite_report_selector.currentText()
        tables = self.get_sqlite_report_tables()

        self.sqlite_report_selector.blockSignals(True)
        self.sqlite_report_selector.clear()
        self.sqlite_report_selector.addItems(tables)
        if current_table in tables:
            self.sqlite_report_selector.setCurrentText(current_table)
        self.sqlite_report_selector.blockSignals(False)

        self.load_selected_sqlite_report()

    def load_selected_sqlite_report(self):
        table_name = self.sqlite_report_selector.currentText()
        if not table_name:
            self.sqlite_report_table.clearContents()
            self.sqlite_report_table.setRowCount(0)
            self.sqlite_report_table.setColumnCount(0)
            self.sqlite_report_status.setText("")
            return

        try:
            conn = sqlite3.connect("blendmaster.db")
            row_count = pd.read_sql(f'SELECT COUNT(*) AS row_count FROM "{table_name}"', conn)["row_count"].iloc[0]
            preview_limit = 10000
            df = pd.read_sql(f'SELECT * FROM "{table_name}" LIMIT {preview_limit}', conn)
            conn.close()
        except Exception as e:
            QMessageBox.warning(self, "SQLite Reports", f"Unable to load '{table_name}': {e}")
            return

        if row_count > len(df):
            self.sqlite_report_status.setText(
                f"{table_name}: showing first {len(df):,} of {row_count:,} rows."
            )
        else:
            self.sqlite_report_status.setText(f"{table_name}: {row_count:,} rows.")

        self.populate_dataframe_table(self.sqlite_report_table, df)

    def format_table_display_value(self, value):
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass

        if isinstance(value, bool):
            return str(value)

        if hasattr(value, "strftime"):
            return value.strftime("%Y-%m-%d %H:%M:%S")

        if isinstance(value, Real) and not isinstance(value, Integral):
            return f"{float(value):.2f}"

        if isinstance(value, str):
            stripped_value = value.strip()
            if len(stripped_value) >= 10 and stripped_value[4:5] == "-" and stripped_value[7:8] == "-":
                parsed_datetime = pd.to_datetime(stripped_value, errors="coerce")
                if not pd.isna(parsed_datetime):
                    return parsed_datetime.strftime("%Y-%m-%d %H:%M:%S")
            if stripped_value and ("." in stripped_value or "e" in stripped_value.lower()):
                try:
                    return f"{float(stripped_value):.2f}"
                except ValueError:
                    pass

        return str(value)

    def populate_dataframe_table(self, table_widget, df):
        table_widget.clearContents()
        table_widget.setRowCount(len(df))
        table_widget.setColumnCount(len(df.columns))
        table_widget.setHorizontalHeaderLabels([str(column) for column in df.columns])
        table_widget.verticalHeader().setVisible(False)

        header_font = table_widget.horizontalHeader().font()
        header_font.setBold(True)
        table_widget.horizontalHeader().setFont(header_font)

        for row_idx, row in enumerate(df.itertuples(index=False)):
            for col_idx, value in enumerate(row):
                item = QTableWidgetItem(self.format_table_display_value(value))
                item.setTextAlignment(Qt.AlignCenter)
                table_widget.setItem(row_idx, col_idx, item)

        table_widget.resizeColumnsToContents()

    def setup_optimised_grade_profile_tab(self):
        self.optimised_grade_profile_tab = QWidget()
        self.optimised_grade_profile_tab_index = self.tabs.addTab(self.optimised_grade_profile_tab, "Grade Profiles (Optimised)")
        self.optimised_grade_profile_layout = QVBoxLayout(self.optimised_grade_profile_tab)

        self.optimised_grade_profile_frame = QFrame()
        self.optimised_grade_profile_frame.setFrameShape(QFrame.Box)
        self.optimised_grade_profile_frame.setLineWidth(1)

        frame_layout = QVBoxLayout(self.optimised_grade_profile_frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)

        self.optimised_grade_profile_chart_view = CustomWebEngineView()
        self.optimised_grade_profile_chart_view.setStyleSheet("border: 1px solid black;")
        frame_layout.addWidget(self.optimised_grade_profile_chart_view)

        self.optimised_grade_profile_layout.addWidget(self.optimised_grade_profile_frame)

        self.load_optimised_grade_profile_chart_button = QPushButton("Load or Update Chart")
        self.load_optimised_grade_profile_chart_button.setFixedWidth(200)
        self.load_optimised_grade_profile_chart_button.setStyleSheet("font-size: 16px; padding: 8px;")
        self.load_optimised_grade_profile_chart_button.clicked.connect(self.load_optimised_grade_profiles)
        self.optimised_grade_profile_layout.addWidget(self.load_optimised_grade_profile_chart_button)

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
    
    def load_grade_profiles(self):
        
        self.start_or_update_dash_manual_grade_profile_thread()

        # Load the Dash app into the QWebEngineView
        self.blend_grade_profile_chart_view.setUrl(QUrl("http://localhost:8053"))

    def load_optimised_grade_profiles(self):
        self.start_or_update_dash_optimised_grade_profile_thread()

        if not self.load_optimised_grade_profiles_first_call:
            try:
                requests.post("http://localhost:8055/trigger-refresh", timeout=5)
            except requests.exceptions.Timeout:
                QMessageBox.critical(None, "Timeout", "The server did not respond in time.")
            except requests.exceptions.RequestException:
                pass

        self.optimised_grade_profile_chart_view.setUrl(QUrl("http://localhost:8055"))
        self.load_optimised_grade_profiles_first_call = False

    def start_or_update_dash_optimised_grade_profile_thread(self):
        if self.start_dash_optimised_grade_profile_first_call:
            self.draw_optimised_grade_profile_chart = DrawOptimisedGradeProfiles("blendmaster.db", 8055)
            self.dash_thread_optimised_grade_profile = threading.Thread(
                target=self.draw_optimised_grade_profile_chart.run_app,
                daemon=True
            )
            self.dash_thread_optimised_grade_profile.start()
            self.start_dash_optimised_grade_profile_first_call = False

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

    def start_dash_AMT_map_thread(self):
        """Start the Dash app in a separate thread."""
        
        db_path = "blendmaster.db"

        if self.start_dash_AMT_map_thread_first_call:

            self.draw_AMT_map = DrawAMTStockpile(
                db_path,
                port=8054,
                hex_sequence_table=self.hex_sequence_table,
                chunk_settings=copy.deepcopy(self.AMT_chunk_settings)
            )

            # Use a thread to run the Dash app server
            self.dash_thread_AMT_map = threading.Thread(target=self.draw_AMT_map.run_app, daemon=True)
            self.dash_thread_AMT_map.start()

        self.start_dash_AMT_map_thread_first_call = False
    
    def update_decision_point_tab_state(self):
        """Enable or disable the Decision Point tab based on blend_mode."""
        if self.blend_mode_choice == 2:
            self.tabs.setTabEnabled(self.decision_point_tab_index, True)
            self.tabs.setCurrentIndex(self.decision_point_tab_index)
            self.decision_input.setEnabled(True) # Enable the input
            self.enter_button.setEnabled(True) # Enable the button
            self.decision_select_button.setEnabled(True)
            self.tabs.setTabEnabled(self.results_tab_index, True)
            self.tabs.setTabEnabled(self.profiles_tab_index, True)
            self.tabs.setTabEnabled(self.sqlite_reports_tab_index, True)
            self.tabs.setTabEnabled(self.optimised_grade_profile_tab_index, True)

        else:
            self.tabs.setTabEnabled(self.decision_point_tab_index, True)
            self.decision_input.setEnabled(False) # Disable the input
            self.enter_button.setEnabled(False) # Disable the button
            self.decision_select_button.setEnabled(False)
            self.tabs.setTabEnabled(self.results_tab_index, True)
            self.tabs.setTabEnabled(self.profiles_tab_index, True)
            self.tabs.setTabEnabled(self.sqlite_reports_tab_index, True)
            self.tabs.setTabEnabled(self.optimised_grade_profile_tab_index, True)
            self.tabs.setCurrentIndex(self.results_tab_index)  # Switch to Results (optimised) tab

    def handle_decision_input(self):
        """Send input from the Decision Point tab to the CaseModellerBridge."""
        user_input = self.decision_input.text()

        # Validate the input
        try:
            user_input_int = int(user_input)  # Check if input is an integer
            valid_options = getattr(self, "valid_decision_blend_options", [])
            if user_input_int in valid_options:
                # Input is valid, send it to CaseModellerBridge
                self.decision_status_label.setText(f"Selected Blend Option {user_input_int}.")
                self.display_decision_output(f"Selected Blend Option {user_input_int}.")
                self.run_program.case_bridge.send_input(user_input)
                self.decision_input.clear()
            else:
                option_text = ", ".join(str(option) for option in valid_options) or "none"
                raise ValueError(f"Input must be one of: {option_text}")
        except ValueError as e:
            # Display an error message in the decision_output text area
            self.display_decision_output(f"Invalid input: {e}")

    def select_decision_blend_from_selected_row(self):
        self.select_decision_blend_from_row(self.decision_table.currentRow(), self.decision_table.currentColumn())

    def select_decision_blend_from_row(self, row, column):
        if self.blend_mode_choice != 2 or not self.decision_input.isEnabled():
            return
        if row < 0:
            self.display_decision_output("Select a candidate row first.")
            return
        display_df = getattr(self, "decision_display_dataframe", pd.DataFrame())
        current_steady_state = getattr(self, "decision_current_steady_state", None)
        if (
            current_steady_state is not None
            and not display_df.empty
            and "steady_state_number" in display_df.columns
            and row < len(display_df)
        ):
            selected_steady_state = display_df.iloc[row]["steady_state_number"]
            if str(selected_steady_state) != str(current_steady_state):
                self.display_decision_output("Select a blend row from the current steady state.")
                return
        blend_option_column = getattr(self, "decision_blend_option_column", None)
        if blend_option_column is None:
            self.display_decision_output("No blend options are available yet.")
            return
        item = self.decision_table.item(row, blend_option_column)
        if item is None:
            self.display_decision_output("Selected row does not contain a blend option.")
            return
        self.decision_input.setText(item.text())
        self.handle_decision_input()

    def display_decision_output(self, message):
        """Display messages from CaseModeller in the Decision Point tab."""
        self.decision_output.append(message)
        plain_message = str(message).strip()
        if plain_message.startswith("Choose") or "Manual mode" in plain_message or "Auto select mode" in plain_message:
            self.decision_status_label.setText(plain_message)

    def display_decision_dataframe(self, df):
        """Display a DataFrame in a QTableWidget with custom styles."""
        if df is None:
            return

        current_df = df.copy()
        self.current_decision_dataframe = current_df
        if not hasattr(self, "decision_run_dataframe") or self.decision_run_dataframe is None:
            self.decision_run_dataframe = pd.DataFrame()

        self.decision_run_dataframe = pd.concat(
            [self.decision_run_dataframe, current_df],
            ignore_index=True,
            sort=False,
        )
        display_df = self.decision_run_dataframe.copy()
        self.decision_display_dataframe = display_df

        # Clear the table instead of removing/recreating it
        self.decision_table.clearContents()
        self.decision_table.setRowCount(0)
        self.decision_table.setColumnCount(0)

        # Populate the table with new data
        self.decision_table.setRowCount(len(display_df))
        self.decision_table.setColumnCount(len(display_df.columns))
        decision_column_aliases = {
            "steady_state_number": "Steady State",
            "start_datetime": "Steady State Start",
            "end_datetime": "Steady State End",
            "steady_state_duration": "Duration (hrs)",
            "blend_option": "Blend Option",
            "solver_score": "Solver Score",
            "source": "Source",
            "estimated_delivery_datetime": "Estimated Payload Delivery Time",
            "source_blend_ratio": "Blend Ratio",
            "source_actual_tonnes": "Source Tonnes",
            "crusher_rate_output": "Crusher Rate",
            "crusher_actual_grade_fe": "Grade Fe (%)",
            "crusher_actual_grade_si": "Grade Si (%)",
            "crusher_actual_grade_al": "Grade Al (%)",
            "crusher_actual_grade_p": "Grade P (%)",
            "crusher_actual_grade_mn": "Grade Mn (%)",
        }
        self.decision_table.setHorizontalHeaderLabels([
            decision_column_aliases.get(column, str(column))
            for column in display_df.columns
        ])
        
        self.decision_blend_option_column = (
            list(display_df.columns).index("blend_option") if "blend_option" in display_df.columns else None
        )
        if "steady_state_number" in current_df.columns and not current_df.empty:
            self.decision_current_steady_state = current_df["steady_state_number"].iloc[-1]
        else:
            self.decision_current_steady_state = None

        if "blend_option" in current_df.columns:
            numeric_options = pd.to_numeric(current_df["blend_option"], errors="coerce").dropna()
            self.valid_decision_blend_options = sorted(set(numeric_options.astype(int).tolist()))
            self.max_blend_option = max(self.valid_decision_blend_options) if self.valid_decision_blend_options else 0
        else:
            self.valid_decision_blend_options = []
            self.max_blend_option = 0

        if self.valid_decision_blend_options:
            steady_state_text = (
                f"Steady state {self.decision_current_steady_state}: "
                if self.decision_current_steady_state is not None
                else ""
            )
            self.decision_status_label.setText(
                f"{steady_state_text}{len(self.valid_decision_blend_options)} feasible blend option(s). "
                "Select a row or type a Blend Option."
            )

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
        for row_idx, row in enumerate(display_df.itertuples(index=False)):
            blend_option_value = None
            if self.decision_blend_option_column is not None:
                blend_option_value = row[self.decision_blend_option_column]
            row_color = QColor(238, 246, 255) if row_idx % 2 == 0 else QColor(255, 255, 255)
            if (
                self.decision_current_steady_state is not None
                and "steady_state_number" in display_df.columns
            ):
                row_steady_state = display_df.iloc[row_idx]["steady_state_number"]
                if str(row_steady_state) != str(self.decision_current_steady_state):
                    row_color = QColor(245, 245, 245)
            try:
                if blend_option_value is not None and int(blend_option_value) % 2 == 0:
                    row_color = QColor(230, 242, 255) if str(display_df.iloc[row_idx].get("steady_state_number", "")) == str(self.decision_current_steady_state) else QColor(238, 238, 238)
            except (TypeError, ValueError):
                pass
            for col_idx, value in enumerate(row):
                item = QTableWidgetItem(self.format_table_display_value(value))
                item.setTextAlignment(Qt.AlignCenter)  # Center-align cell content
                item.setBackground(row_color)
                self.decision_table.setItem(row_idx, col_idx, item)

        # Resize columns to fit content 
        self.decision_table.resizeColumnsToContents()
        self.decision_table.scrollToBottom()
        QTimer.singleShot(0, self.decision_table.scrollToBottom)
    
    def show_error_popup(self, error_message, title=None):
        """Display an error message in a popup."""
        if isinstance(error_message, dict):
            title = error_message.get("title", title)
            error_message = error_message.get("message", "")
        if (title or "").lower() in {"infeasible run", "run aborted", "stockpile selection required"}:
            QMessageBox.information(self, title, str(error_message))
            return
        QMessageBox.critical(self, title or "Error", str(error_message))
    
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

            self.blend_results_table
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

        if hasattr(self, "blend_config_table"):
            self.refresh_blend_config_projected_columns()

        # Populate weights and Blend IDs if project is loaded
        self.populate_blend_config_weights_and_ids()

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
                
                # Conventional vs AMT stockpile
                is_amt =  attributes.get("amt", False)
                
                if not is_amt:
                    
                    # Safely get the value, default to 0 if None
                    value = attributes.get(key, 0) or 0

                    if key == "balance":
                        col_idx = 1  # Balance column
                        item = QTableWidgetItem(f"{float(value):.0f}")  # Format as integer (no decimals)
                    else:
                        col_idx = keys.index(key) + 4  # Offset for additional columns
                        item = QTableWidgetItem(f"{float(value):.2f}")  # Format as float (2 decimals)
                else:
                    
                    # Safely get the value, default to 0 if None
                    balance = self.total_AMT_stockpile_balances.get(stockpile_name, 0) or 0

                    if key == "balance":
                        col_idx = 1  # Balance column
                        item = QTableWidgetItem(f"{float(balance):.0f}")  # Format as integer (no decimals)
                    else:
                        col_idx = keys.index(key) + 4  # Offset for additional columns
                        item = QTableWidgetItem("AMT")


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

            latest_record = self.latest_build_record_for_stockpile(build_report_df, stockpile_name)
            if latest_record is not None:
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
            self.blend_id_combo = QComboBox()
            self.blend_id_combo.addItems(["None"] + [str(i) for i in range(1, 6)])  # Add "None" option
            self.blend_id_combo.setCurrentText("None")  # Set default to "None"
            self.blend_id_combo.currentIndexChanged.connect(self.on_blend_data_change)
            self.blend_config_table.setCellWidget(row_idx, len(keys) + 4, self.blend_id_combo)  
            
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

    def latest_build_record_for_stockpile(self, build_report_df, stockpile_name):
        if (
            build_report_df is None
            or build_report_df.empty
            or "stockpile" not in build_report_df.columns
        ):
            return None

        stockpile_records = build_report_df[
            build_report_df["stockpile"].astype(str) == str(stockpile_name)
        ].copy()
        if stockpile_records.empty:
            return None

        stockpile_records["_delivered_datetime_sort"] = pd.to_datetime(
            stockpile_records["delivered_datetime"],
            errors="coerce",
        )
        stockpile_records = stockpile_records.sort_values(
            ["_delivered_datetime_sort", "delivered_datetime"],
            ascending=False,
            na_position="last",
        )
        return stockpile_records.iloc[0]

    def refresh_blend_config_projected_columns(self):
        if not hasattr(self, "blend_config_table"):
            return

        build_report_df = self.fetch_build_report()
        was_blocked = self.blend_config_table.blockSignals(True)
        try:
            for row_idx in range(self.blend_config_table.rowCount()):
                stockpile_item = self.blend_config_table.item(row_idx, 0)
                if stockpile_item is None:
                    continue

                latest_record = self.latest_build_record_for_stockpile(
                    build_report_df,
                    stockpile_item.text(),
                )
                projected_text = ""
                delivered_text = ""
                if latest_record is not None:
                    try:
                        projected_text = f"{float(latest_record['closing_balance']):.0f}"
                    except (TypeError, ValueError):
                        projected_text = str(latest_record["closing_balance"])
                    delivered_text = str(latest_record["delivered_datetime"])

                projected_item = self.blend_config_table.item(row_idx, 2)
                if projected_item is None:
                    projected_item = QTableWidgetItem("")
                    projected_item.setFlags(Qt.ItemIsEnabled)
                    projected_item.setTextAlignment(Qt.AlignCenter)
                    self.blend_config_table.setItem(row_idx, 2, projected_item)
                projected_item.setText(projected_text)

                last_payload_item = self.blend_config_table.item(row_idx, 3)
                if last_payload_item is None:
                    last_payload_item = QTableWidgetItem("")
                    last_payload_item.setFlags(Qt.ItemIsEnabled)
                    last_payload_item.setTextAlignment(Qt.AlignCenter)
                    self.blend_config_table.setItem(row_idx, 3, last_payload_item)
                last_payload_item.setText(delivered_text)

                checkbox_widget = self.blend_config_table.cellWidget(row_idx, 4)
                if checkbox_widget:
                    checkbox = checkbox_widget.findChild(QCheckBox)
                    if checkbox:
                        checkbox.setEnabled(bool(projected_text))
                        if not projected_text:
                            checkbox.setChecked(False)
        finally:
            self.blend_config_table.blockSignals(was_blocked)

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
            "Balance (WMT)", "Max Duration (hrs)", "Available", "Sources", "Source Ratios"
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

    def populate_blend_config_weights_and_ids(self):

        self.blend_id_combo.currentIndexChanged.disconnect(self.on_blend_data_change)
        self.blend_config_table.itemChanged.disconnect(self.on_blend_data_change)     
        self.crusher_rate_input.textChanged.disconnect(self.on_blend_data_change)

        if self.is_project_loaded and self.blend_config_table_inputs:
            # Loop through the 5 keys in self.blend_config_table_inputs
            for row_idx, key in enumerate(self.blend_config_table_inputs.keys()):
                # Get the row data from the dictionary
                row_data = self.blend_config_table_inputs[key]

                # Process columns 10 (QComboBox) and 11 (weights) together
                sources_list = row_data['sources'] if isinstance(row_data['sources'], list) else row_data['sources'].split(",")
                weights_list = row_data['weights'] if isinstance(row_data['weights'], list) else row_data['weights'].split(",")

                # Loop through sources and weights simultaneously
                for source, weight in zip(sources_list, weights_list):
                    # Find the matching row in the first column
                    for table_row in range(self.blend_config_table.rowCount()):
                        table_item = self.blend_config_table.item(table_row, 0)  # Get the first column
                        if table_item and table_item.text() == source.strip():  # Match the source
                            # Set the value in the combo box (Column 10)
                            combo_box = self.blend_config_table.cellWidget(table_row, 10)
                            if isinstance(combo_box, QComboBox):
                                combo_box.setCurrentText(key)
                                combo_box.setStyleSheet("QComboBox { text-align: center; }")  # Center align text

                            # Set the value in column 11 (weights)
                            weights_item = QTableWidgetItem(str(round(weight)).strip())
                            weights_item.setTextAlignment(Qt.AlignCenter)
                            self.blend_config_table.setItem(table_row, 11, weights_item)

                            break

        if self.is_project_loaded and self.crusher_rate_input_value:
            self.crusher_rate_input.setText(self.crusher_rate_input_value)
        
        self.blend_id_combo.currentIndexChanged.connect(self.on_blend_data_change)
        self.blend_config_table.itemChanged.connect(self.on_blend_data_change)     
        self.crusher_rate_input.textChanged.connect(self.on_blend_data_change)

    def update_blend_results(self):
        """
        Recalculate and update the Blend Results Table, ensuring unused Blend IDs are cleared.
        """
        # Initialize a dictionary to aggregate data for each blend ID
        self.blend_data_from_config_table_inputs = {str(i): {"weights": [], "grades": [], "balances": [], "available": [], "sources": [], "source_ratios": []} for i in range(1, 6)}

        # Aggregate data from blend configuration table
        for row_idx in range(self.blend_config_table.rowCount()):
            try:
                blend_id_combo = self.blend_config_table.cellWidget(row_idx, 10)  # Column index for Blend ID
                blend_id = blend_id_combo.currentText()

                # Skip calculations if Blend ID is "None"
                if blend_id == "None":
                    continue

                weight = float(self.blend_config_table.item(row_idx, 11).text())  # Column index for Weight
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

                grades = [float(self.blend_config_table.item(row_idx, col).text()) if self.blend_config_table.item(row_idx, col).text() != "AMT" else "AMT" for col in range(5, 10)]

                sources = self.blend_config_table.item(row_idx, 0).text()
                source_ratios = self.blend_config_table.item(row_idx, 13).text()

                self.blend_data_from_config_table_inputs[blend_id]["weights"].append(weight)
                self.blend_data_from_config_table_inputs[blend_id]["grades"].append([grade * weight if grade != "AMT" else "AMT" for grade in grades])
                self.blend_data_from_config_table_inputs[blend_id]["balances"].append(balance)
                self.blend_data_from_config_table_inputs[blend_id]["available"].append(available)
                self.blend_data_from_config_table_inputs[blend_id]["sources"].append(sources)
                self.blend_data_from_config_table_inputs[blend_id]["source_ratios"].append(source_ratios)

            except (ValueError, AttributeError):
                continue

        # Update the Blend Results Table
        for blend_id, data in self.blend_data_from_config_table_inputs.items():
            row_idx = int(blend_id) - 1
            total_weight = sum(data["weights"])

            if total_weight > 0:
                # Calculate weighted averages
                if not any("AMT" in grades for grades in data["grades"]):
                    avg_grades = [sum(grades) / total_weight for grades in zip(*data["grades"])]
                else:
                    avg_grades = ["AMT" for grades in zip(*data["grades"])]

                balance = min(data["balances"])
                max_duration = balance / self.crusher_rate if self.crusher_rate > 0 else 0
                available_status = "Now"
                if any(avail != "Now" for avail in data["available"]):
                    datetime_values = [avail for avail in data["available"] if avail != "Now"]
                    available_status = min(datetime_values)
                else:
                    available_status = "Now"

                # Create comma-separated strings for sources and source ratios
                sources_combined = ", ".join(data["sources"])
                source_ratios_combined = ", ".join(data["source_ratios"])

                # Update blend results table
                self.blend_results_table.setItem(row_idx, 0, self.create_centered_item(blend_id))
                for col_idx, avg_grade in enumerate(avg_grades, start=1):
                    if avg_grade == "AMT":
                        self.blend_results_table.setItem(row_idx, col_idx, self.create_centered_item("AMT"))
                    else:
                        self.blend_results_table.setItem(row_idx, col_idx, self.create_centered_item(f"{avg_grade:.2f}"))
                self.blend_results_table.setItem(row_idx, 6, self.create_centered_item(f"{balance:.1f}"))
                self.blend_results_table.setItem(row_idx, 7, self.create_centered_item(f"{max_duration:.1f}"))
                self.blend_results_table.setItem(row_idx, 9, self.create_centered_item(sources_combined))
                self.blend_results_table.setItem(row_idx, 10, self.create_centered_item(source_ratios_combined))

                item = QTableWidgetItem(available_status)
                item.setTextAlignment(Qt.AlignCenter)

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

        self.setup_sequence_tab()
        self.tabs.setTabEnabled(self.blend_sequence_tab_index, True)
        self.tabs.setCurrentIndex(self.blend_sequence_tab_index)
        self.save_button.setEnabled(True)
        QMessageBox.information(self, "BlendMaster", "Blend results successfully saved.")

    def fetch_build_report(self):
        """
        Fetch the build report from the database.
        """
        conn = sqlite3.connect("blendmaster.db")
        try:
            df = pd.read_sql("SELECT * FROM build_report", conn)
        except (sqlite3.Error, pd.errors.DatabaseError) as e:
            print(f"Warning: {e}")
            df = pd.DataFrame()
        finally:
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
                if item and item.text() and item.text() != "AMT":
                    item.setText(f"{float(item.text()):.2f}")  # Two decimal places
                    item.setTextAlignment(Qt.AlignCenter)
    
    def setup_sequence_tab(self):
       
        if self.saved_blends_for_schedule is None:
            self.saved_blends_for_schedule = []
        if not isinstance(getattr(self, "stored_blend_sequence_table_for_gantt", []), list):
            self.stored_blend_sequence_table_for_gantt = []

        default_duration = str((self.default_end_datetime - self.default_start_datetime).total_seconds() / 3600)
        self.stored_blend_sequence_table_for_gantt_default = [
        {"Blend ID": "1", "Origin": "Default", "Start Datetime": self.default_start_datetime_str, "Duration (hrs)": default_duration, "End Datetime": self.default_end_datetime_str, "Early Start Flag": ""},
        {"Blend ID": "2", "Origin": "Default", "Start Datetime": self.default_start_datetime_str, "Duration (hrs)": default_duration, "End Datetime": self.default_end_datetime_str, "Early Start Flag": ""},
        {"Blend ID": "3", "Origin": "Default", "Start Datetime": self.default_start_datetime_str, "Duration (hrs)": default_duration, "End Datetime": self.default_end_datetime_str, "Early Start Flag": ""},
        {"Blend ID": "4", "Origin": "Default", "Start Datetime": self.default_start_datetime_str, "Duration (hrs)": default_duration, "End Datetime": self.default_end_datetime_str, "Early Start Flag": ""},
        {"Blend ID": "5", "Origin": "Default", "Start Datetime": self.default_start_datetime_str, "Duration (hrs)": default_duration, "End Datetime": self.default_end_datetime_str, "Early Start Flag": ""}
        ]
        
        if not self.is_project_loaded:
            self.stored_blend_sequence_table_for_gantt = []

        self.manual_gantt_legend_and_tooltip = self.saved_blends_for_schedule
        
        self.start_or_update_dash_manual_chart_thread()
        self.load_manual_gantt_chart()     

        # Populate data from saved_blends_for_schedule into blend_results_table_view
        self.blend_results_table_view.setRowCount(len(self.saved_blends_for_schedule))  # Set row count
        if not self.saved_blends_for_schedule:
            self.blend_results_table_view.clearContents()
            self.blend_results_table_view.setColumnCount(0)
            self.setup_blend_sequence_table()
            return

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

        store_button = QPushButton("Submit")
        store_button.setFixedWidth(120)
        store_button.clicked.connect(self.submit_blend_sequence_table_to_gantt)
        
        if self.setup_blend_sequence_table_first_call:
            
            # Add the button to the layout
            self.blend_sequence_table_external_layout.addWidget(store_button)
            
        if self.is_project_loaded and self.setup_blend_sequence_table_first_call:
            self.populate_blend_sequence_table_if_project_is_loaded()
       
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
            QMessageBox.warning(self, "Warning", f"Could not update row {row}: {e}")

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
            QMessageBox.warning(self, "Invalid Duration", f"Error updating row {row}: {e}")

    def collect_blend_sequence_table_rows(self):
        headers = ["Blend ID", "Origin", "Start Datetime", "Duration (hrs)", "End Datetime", "Early Start Flag", "Remaining Hrs"]
        rows = []
        for row in range(self.blend_sequence_table.rowCount()):
            row_data = {}
            for col, header in enumerate(headers):
                item = self.blend_sequence_table.cellWidget(row, col) or self.blend_sequence_table.item(row, col)
                if isinstance(item, QTableWidgetItem):
                    row_data[header] = item.text() if item else None
                elif isinstance(item, QComboBox):
                    row_data[header] = item.currentText() if item else None
                elif isinstance(item, QDateTimeEdit):
                    row_data[header] = item.dateTime().toString("yyyy-MM-dd HH:mm") if item else None
                else:
                    row_data[header] = None
            rows.append(row_data)
        return rows

    def poll_manual_gantt_updates(self):
        manual_gantt = getattr(self, "draw_manual_gantt_chart", None)
        if manual_gantt is None or not hasattr(manual_gantt, "consume_pending_table_update"):
            return

        updated_rows = manual_gantt.consume_pending_table_update()
        if updated_rows:
            self.apply_manual_gantt_rows_to_table(updated_rows)

    def apply_manual_gantt_rows_to_table(self, updated_rows):
        if not updated_rows or not hasattr(self, "blend_sequence_table"):
            return

        headers = ["Blend ID", "Origin", "Start Datetime", "Duration (hrs)", "End Datetime", "Early Start Flag", "Remaining Hrs"]
        start_col = headers.index("Start Datetime")
        duration_col = headers.index("Duration (hrs)")
        end_col = headers.index("End Datetime")
        blend_col = headers.index("Blend ID")
        origin_col = headers.index("Origin")

        self.blend_sequence_table.blockSignals(True)
        try:
            for row_index, row_data in enumerate(updated_rows[:self.blend_sequence_table.rowCount()]):
                blend_widget = self.blend_sequence_table.cellWidget(row_index, blend_col)
                if isinstance(blend_widget, QComboBox) and row_data.get("Blend ID") is not None:
                    blend_widget.setCurrentText(str(row_data.get("Blend ID")))

                origin_item = self.blend_sequence_table.item(row_index, origin_col)
                if origin_item is None:
                    origin_item = QTableWidgetItem()
                    self.blend_sequence_table.setItem(row_index, origin_col, origin_item)
                origin_item.setText(str(row_data.get("Origin", "")))
                origin_item.setTextAlignment(Qt.AlignCenter)

                start_value = str(row_data.get("Start Datetime", ""))
                start_widget = self.blend_sequence_table.cellWidget(row_index, start_col)
                if isinstance(start_widget, QDateTimeEdit) and start_value:
                    parsed_start = QDateTime.fromString(start_value, "yyyy-MM-dd HH:mm")
                    if parsed_start.isValid():
                        start_widget.setDateTime(parsed_start)

                duration_item = self.blend_sequence_table.item(row_index, duration_col)
                if duration_item is None:
                    duration_item = QTableWidgetItem()
                    self.blend_sequence_table.setItem(row_index, duration_col, duration_item)
                duration_item.setText(str(row_data.get("Duration (hrs)", "")))
                duration_item.setTextAlignment(Qt.AlignCenter)

                end_item = self.blend_sequence_table.item(row_index, end_col)
                if end_item is None:
                    end_item = QTableWidgetItem()
                    self.blend_sequence_table.setItem(row_index, end_col, end_item)
                end_item.setText(str(row_data.get("End Datetime", "")))
                end_item.setTextAlignment(Qt.AlignCenter)

                self.update_blend_id(row_index)
        finally:
            self.blend_sequence_table.blockSignals(False)

        self.update_early_start_conditional_format()
        self.update_remaining_hrs()
        self.stored_blend_sequence_table_for_gantt = self.collect_blend_sequence_table_rows()

    def submit_blend_sequence_table_to_gantt(self):
                
        headers = ["Blend ID", "Origin", "Start Datetime", "Duration (hrs)", "End Datetime", "Early Start Flag", "Remaining Hrs"]

        self.stored_blend_sequence_table_for_gantt = []
                
        # Check for negative values in "Remaining Hrs"
        for row in range(self.blend_sequence_table.rowCount()):
            item = self.blend_sequence_table.item(row, headers.index("Remaining Hrs"))
            if item:
                try:
                    remaining_hrs = float(item.text())
                    if remaining_hrs < 0:
                        QMessageBox.warning(
                            None,
                            "Warning",
                            "One or more rows have negative values in 'Remaining Hrs'. Data has not been stored."
                        )
                        return  # Exit the method without storing any data
                except ValueError:
                    QMessageBox.warning(
                        None,
                        "Warning",
                        "Invalid value detected in 'Remaining Hrs'. Data has not been stored."
                    )
                    return

        # If all rows are valid, store data
        self.stored_blend_sequence_table_for_gantt = self.collect_blend_sequence_table_rows()
        
        self.start_or_update_dash_manual_chart_thread()

        self.load_manual_gantt_chart()  

        self.tabs.setTabEnabled(self.grade_profile_tab_index, True)  # Enable Grade Profile tab

        QMessageBox.information(self, "BlendMaster", "Blend sequence successfully submitted.")

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
                QMessageBox.warning(self, "Warning", f"Blend ID {blend_id} not found in saved blends.")
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

    def populate_blend_sequence_table_if_project_is_loaded(self):
        stored_sequence = getattr(self, "stored_blend_sequence_table_for_gantt", None)
        if not isinstance(stored_sequence, list) or not stored_sequence:
            self.stored_blend_sequence_table_for_gantt = []
            return

        # Ensure the table has enough rows to match the stored data
        while self.blend_sequence_table.rowCount() < len(stored_sequence):
            self.add_blank_row()  # Use the method to insert rows with the correct format

        # Iterate through the stored data and update the table
        for row_index, row_data in enumerate(stored_sequence):
            # Update "Blend ID" (QComboBox)
            blend_id = row_data.get("Blend ID")
            combo_box = self.blend_sequence_table.cellWidget(row_index, 0)
            if isinstance(combo_box, QComboBox) and blend_id is not None:
                combo_box.setCurrentText(blend_id)

            # Update "Start Datetime" (QDateTimeEdit)
            start_datetime = row_data.get("Start Datetime")
            datetime_widget = self.blend_sequence_table.cellWidget(row_index, 2)
            if isinstance(datetime_widget, QDateTimeEdit) and start_datetime is not None:
                datetime_widget.setDateTime(QDateTime.fromString(start_datetime, "yyyy-MM-dd HH:mm"))

            # Update "Duration (hrs)" (QTableWidgetItem)
            duration = row_data.get("Duration (hrs)")
            duration_item = self.blend_sequence_table.item(row_index, 3)
            if isinstance(duration_item, QTableWidgetItem):
                duration_item.setText(duration if duration is not None else "")
            else:  # Create a new QTableWidgetItem if not already set
                self.blend_sequence_table.setItem(row_index, 3, QTableWidgetItem(duration if duration is not None else ""))
            
            self.on_cell_changed(row_index,3)
            self.update_blend_id(row_index)

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

    def start_or_update_dash_manual_chart_thread(self):
        """Update or start the Dash app."""
        gantt_data = self.stored_blend_sequence_table_for_gantt or self.stored_blend_sequence_table_for_gantt_default

        if hasattr(self, 'draw_manual_gantt_chart') and self.dash_thread_manual_gantt.is_alive():
            # Update data in the running Dash app
            self.draw_manual_gantt_chart.update_data(gantt_data, self.manual_gantt_legend_and_tooltip)  
        else:
            # Start the Dash app if not already running
            self.draw_manual_gantt_chart = ManualBlendDash(gantt_data, self.manual_gantt_legend_and_tooltip, port=8052, crusher_rate=self.crusher_rate)
            self.dash_thread_manual_gantt = threading.Thread(target=self.draw_manual_gantt_chart.run_app, daemon=True)
            self.dash_thread_manual_gantt.start()

    def setup_grade_profile_tab(self):
        # Create a tab for Grade Profiles (Manual)
        self.grade_profile_tab = QWidget()
        self.grade_profile_tab_index = self.tabs.addTab(self.grade_profile_tab, "Grade Profiles (Manual)")

        # Create the main layout for the tab
        self.grade_profile_layout = QVBoxLayout(self.grade_profile_tab)

        # Create the bottom frame
        self.grade_profile_frame = QFrame()
        self.grade_profile_frame.setFrameStyle(QFrame.Box | QFrame.Plain)  # Set a plain box-style frame
        self.grade_profile_frame.setLineWidth(2)  # Set the frame's border width
        self.grade_profile_frame.setStyleSheet("border-color: black;")  # Optional: Set border color

        # Add the frame to the layout
        self.grade_profile_layout.addWidget(self.grade_profile_frame)

        # Create a layout for the frame
        self.grade_profile_frame_layout = QVBoxLayout(self.grade_profile_frame)

        # Add the custom chart view to the frame layout
        self.blend_grade_profile_chart_view = CustomWebEngineView()  # Embed the Dash app
        self.blend_grade_profile_chart_view.setStyleSheet("border: 1px solid black;")
        self.grade_profile_frame_layout.addWidget(self.blend_grade_profile_chart_view)

        # Add a button to load the chart
        self.load_grade_profile_chart_button = QPushButton("Load or Update Chart")
        self.load_grade_profile_chart_button.setFixedWidth(200)
        self.load_grade_profile_chart_button.setStyleSheet("font-size: 16px; padding: 8px;")  # Smaller button
        self.load_grade_profile_chart_button.clicked.connect(self.load_grade_profiles)  # Connect button to function

        # Add the button to the layout at the bottom-left
        self.grade_profile_layout.addWidget(self.load_grade_profile_chart_button)

    def start_or_update_dash_manual_grade_profile_thread(self):
        """Update or start the Dash app."""
        hex_sequence_table = copy.deepcopy(self.hex_sequence_table)
        updated_stockpile_data = copy.deepcopy(self.updated_stockpile_data)
        grade_profile_data = self.draw_manual_gantt_chart.return_grade_profile_data()

        if hasattr(self, 'draw_grade_profile_chart') and self.dash_thread_grade_profile.is_alive():
            # Update data in the running Dash app
            self.draw_grade_profile_chart.update_data(grade_profile_data)  
        else:
            # Start the Dash app if not already running
            self.draw_grade_profile_chart = DrawGradeProfiles(grade_profile_data, hex_sequence_table, updated_stockpile_data, 8053)
            self.dash_thread_grade_profile = threading.Thread(target=self.draw_grade_profile_chart.run_app, daemon=True)
            self.dash_thread_grade_profile.start()
            self.draw_grade_profile_chart.update_data(grade_profile_data)  

    def save_state(self):
        """Save the application state to a file using pickle."""

        if hasattr(self, "hub_input"):
            self.hub_input_choice = self.hub_input.currentText().strip()
        if hasattr(self, "mine_input"):
            self.mine_input_choice = self.mine_input.currentText().strip()
        if hasattr(self, "time_mode"):
            self.time_mode_choice = self.time_mode.currentIndex() + 1
        if hasattr(self, "start_time") and self.time_mode_choice == 2:
            self.start_time_choice = self.start_time.dateTime().toPyDateTime()
        elif self.start_time_choice is None:
            self.start_time_choice = datetime.now()
        if hasattr(self, "expit_mode"):
            self.expit_mode_choice = self.expit_mode.currentIndex() + 1 if self.expit_mode.isEnabled() else 1
        if hasattr(self, "file_path"):
            self.file_path_choice = self.file_path.text()
        if hasattr(self, "reevaluate_aps_direct_tip_checkbox"):
            self.reevaluate_aps_direct_tip_choice = self.reevaluate_aps_direct_tip_checkbox.isChecked()
        if hasattr(self, "aps_crusher_input"):
            self.aps_direct_tip_crusher_choice = (
                self.selected_aps_crusher_names()
                if self.reevaluate_aps_direct_tip_choice
                else []
            )
        if hasattr(self, "blend_mode"):
            self.blend_mode_choice = self.blend_mode.currentIndex() + 1

        if hasattr(self, "blend_data_from_config_table_inputs"):
            self.blend_config_table_inputs = self.blend_data_from_config_table_inputs
        elif self.blend_config_table_inputs is None:
            self.blend_config_table_inputs = {}

        if hasattr(self, "crusher_rate_input"):
            self.crusher_rate_input_value = self.crusher_rate_input.text()

        self.store_solver_config_inputs(show_errors=False)
        
        try:
            
            # Save the enabled/disabled state of tabs
            tab_states = {index: self.tabs.isTabEnabled(index) for index in range(self.tabs.count())}

            # Combine all class variables into a dictionary
            state_to_save = {
                "tab_states": tab_states,
                "blend_mode_choice": self.blend_mode_choice,
                "calendar_inputs": self.calendar_inputs,
                "crusher_rate": self.crusher_rate,
                "default_end_datetime": self.default_end_datetime,
                "default_end_datetime_str": self.default_end_datetime_str,
                "default_start_datetime": self.default_start_datetime,
                "default_start_datetime_str": self.default_start_datetime_str,
                "expit_mode_choice": self.expit_mode_choice,
                "file_path_choice": self.file_path_choice,
                "reevaluate_aps_direct_tip_choice": self.reevaluate_aps_direct_tip_choice,
                "aps_direct_tip_crusher_choice": self.aps_direct_tip_crusher_choice,
                "mine_input_choice": self.mine_input_choice,
                "hub_input_choice": self.hub_input_choice,
                "opening_stockpile_inventories": self.opening_stockpile_inventories,
                "saved_blends_for_schedule": self.saved_blends_for_schedule,
                "start_time_choice": self.start_time_choice,
                "stockpile_data": self.stockpile_data,
                "stockpile_data_use_column": self.stockpile_data_use_column,
                "stored_blend_sequence_table_for_gantt": self.stored_blend_sequence_table_for_gantt,
                "stored_blend_sequence_table_for_gantt_default": self.stored_blend_sequence_table_for_gantt_default,
                "time_mode_choice": self.time_mode_choice,
                "updated_stockpile_data": self.updated_stockpile_data,
                "blend_config_table_inputs":  self.blend_config_table_inputs,
                "crusher_rate_input_value": self.crusher_rate_input_value,
                'hex_sequence_table': self.hex_sequence_table,
                'stockpile_data_AMT_column': self.stockpile_data_AMT_column,
                'AMT_stockpile_data': getattr(self, "AMT_stockpile_data", {}),
                'AMT_chunk_settings': self.AMT_chunk_settings,
                "solver_config": self.solver_config,
            }
            # Generate a timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M')

            # Use the timestamp in the filename
            filename = f'blendmaster_{timestamp}.prj'
            
            # Serialize the dictionary to a file
            with open(filename, 'wb') as file:
                pickle.dump(state_to_save, file)

            QMessageBox.information(self, "BlendMaster", "Project saved successfully!")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save project: {str(e)}")

    def load_state(self):
        """Load the application state from a user-selected file."""
        self.is_project_loaded = True
        try:
            # Open a file dialog for the user to select the file
            file_path, _ = QFileDialog.getOpenFileName(
                self, 
                "Select Project File to Load", 
                "",  # Starting directory (empty string means current directory)
                "Project Files (*.prj);;All Files (*)"  # File filters
            )
            
            if not file_path:  # If no file was selected, return early
                self.is_project_loaded = False
                return

            # Load the selected file
            with open(file_path, 'rb') as file:
                loaded_state = pickle.load(file)

            # Unpack loaded state into variables
            self.blend_mode_choice = loaded_state.get("blend_mode_choice", None)
            self.crusher_rate = loaded_state.get("crusher_rate", None)
            self.calendar_inputs = loaded_state.get("calendar_inputs", None)
            self.default_end_datetime = loaded_state.get("default_end_datetime", None)
            self.default_end_datetime_str = loaded_state.get("default_end_datetime_str", "")
            self.default_start_datetime = loaded_state.get("default_start_datetime", None)
            self.default_start_datetime_str = loaded_state.get("default_start_datetime_str", "")
            self.expit_mode_choice = loaded_state.get("expit_mode_choice", None)
            self.file_path_choice = loaded_state.get("file_path_choice", "")
            self.reevaluate_aps_direct_tip_choice = loaded_state.get(
                "reevaluate_aps_direct_tip_choice", False
            )
            self.aps_direct_tip_crusher_choice = loaded_state.get(
                "aps_direct_tip_crusher_choice", []
            )
            self.aps_direct_tip_crusher_choice = self.normalized_aps_crusher_choice(
                self.aps_direct_tip_crusher_choice
            )
            self.mine_input_choice = loaded_state.get("mine_input_choice", None)
            self.hub_input_choice = loaded_state.get("hub_input_choice", None)
            self.opening_stockpile_inventories = loaded_state.get("opening_stockpile_inventories", None)
            self.saved_blends_for_schedule = loaded_state.get("saved_blends_for_schedule") or []
            self.start_time_choice = loaded_state.get("start_time_choice", None)
            self.stockpile_data = loaded_state.get("stockpile_data", None)
            self.stockpile_data_use_column = loaded_state.get("stockpile_data_use_column", None)
            self.stored_blend_sequence_table_for_gantt = (
                loaded_state.get("stored_blend_sequence_table_for_gantt") or []
            )
            self.stored_blend_sequence_table_for_gantt_default = (
                loaded_state.get("stored_blend_sequence_table_for_gantt_default") or []
            )
            self.time_mode_choice = loaded_state.get("time_mode_choice", None)
            self.updated_stockpile_data = loaded_state.get("updated_stockpile_data", None)
            self.blend_config_table_inputs =  loaded_state.get("blend_config_table_inputs", None)
            self.crusher_rate_input_value = loaded_state.get("crusher_rate_input_value", None)
            self.hex_sequence_table = loaded_state.get("hex_sequence_table", None)
            self.hex_sequence_table_argument = copy.deepcopy(self.hex_sequence_table or [])
            self.stockpile_data_AMT_column = loaded_state.get("stockpile_data_AMT_column", None)
            self.AMT_stockpile_data = loaded_state.get("AMT_stockpile_data", {}) or {}
            self.AMT_chunk_settings = loaded_state.get("AMT_chunk_settings", {})
            self.solver_config = self.normalized_solver_config(
                loaded_state.get("solver_config", {})
            )
            if self.calendar_inputs is not None:
                self.calendar_inputs["solver_config"] = copy.deepcopy(self.solver_config)

            tab_states = loaded_state.get("tab_states", {})
            for index, enabled in tab_states.items():
                self.tabs.setTabEnabled(index, enabled)

        except FileNotFoundError:
            QMessageBox.warning(self, "Error", "No saved projects found!")
            self.is_project_loaded = False
            return
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load project: {str(e)}")
            self.is_project_loaded = False
            return
        
        self.project_load_restore_in_progress = True
        self.handle_site_config_submit()
        self.store_stockpile_table()
        if getattr(self, "project_load_waiting_for_AMT", False):
            return
        self.continue_project_load_after_stockpile_setup()

    def continue_project_load_after_stockpile_setup(self):
        self.project_load_restore_in_progress = False
        if any((self.stockpile_data_AMT_column or {}).values()):
            self.store_hex_sequence_table()
        self.project_load_continuation_pending = True
        self.store_calendar_inputs()
        
    def initialise_all_variables(self):
        self.blend_mode_choice = None
        self.calendar_inputs = None
        self.crusher_rate = None
        self.default_end_datetime = None
        self.default_end_datetime_str = None
        self.default_start_datetime = None
        self.default_start_datetime_str = None
        self.expit_mode_choice = None
        self.file_path_choice = None
        self.reevaluate_aps_direct_tip_choice = False
        self.aps_direct_tip_crusher_choice = []
        self.mine_input_choice = None
        self.hub_input_choice = None
        self.opening_stockpile_inventories = None
        self.saved_blends_for_schedule = None
        self.start_time_choice = None
        self.stockpile_data = None
        self.stockpile_data_use_column = {}
        self.stored_blend_sequence_table_for_gantt = None
        self.stored_blend_sequence_table_for_gantt_default = None
        self.time_mode_choice = None
        self.updated_stockpile_data = None
        self.blend_config_table_inputs = None
        self.crusher_rate_input_value = None
        self.hex_sequence_table = []
        self.hex_sequence_table_argument = []
        self.stockpile_data_AMT_column = {}
        self.AMT_stockpile_data = {}
        self.AMT_chunk_settings = {}
        self.solver_config = {}
        self.min_stockpiles = None
        self.max_stockpiles = None
        self.min_stockpile_contribution_ratio = Optimizer.MIN_SELECTED_STOCKPILE_BLEND_RATIO
        self.progress_dialog = None
        self.current_cancel_callback = None
        self.background_tasks = []
        self.project_load_continuation_pending = False
        self.project_load_restore_in_progress = False
        self.project_load_waiting_for_AMT = False

    def clear_sqlite_session_data(self):
        try:
            DatabaseManager.clear_all_tables()
        except sqlite3.Error as e:
            print(f"Warning: failed to clear SQLite session data: {e}")
    
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

class ScaledPixmapLabel(QLabel):
    def __init__(self, image_path):
        super().__init__()
        self.original_pixmap = QPixmap(image_path)
        self.setMinimumSize(1, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_scaled_pixmap()

    def update_scaled_pixmap(self):
        if self.original_pixmap.isNull():
            return
        scaled_pixmap = self.original_pixmap.scaled(
            self.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.setPixmap(scaled_pixmap)

class BackgroundWorker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, work_fn):
        super().__init__()
        self.work_fn = work_fn

    @pyqtSlot()
    def run(self):
        try:
            self.finished.emit(self.work_fn())
        except Exception as exc:
            if hasattr(exc, "user_message"):
                self.failed.emit({
                    "title": getattr(exc, "title", "Infeasible Run"),
                    "message": exc.user_message,
                })
            else:
                self.failed.emit({
                    "title": "Error",
                    "message": traceback.format_exc(),
                })

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Set the global font to Segoe UI, size 12
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    
    window = UserInputs()  # Create an instance of the imported class
    window.show()              # Show the GUI
    sys.exit(app.exec_())      # Run the event loop

