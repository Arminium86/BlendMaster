import sys, threading, requests, os, pickle, copy, traceback, json, subprocess, tempfile, uuid, shutil, math
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QTabWidget, QTabBar,
    QFormLayout, QLineEdit, QPushButton, QComboBox, QHBoxLayout, QLabel, QMessageBox, QDateTimeEdit, QFileDialog, QTextEdit, QFrame, QCheckBox, QProgressDialog, QAbstractItemView, QSizePolicy, QListWidget, QSplashScreen, QScrollArea, QDialog
)
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineDownloadItem
from PyQt5.QtGui import QColor, QBrush, QFont, QIcon, QDoubleValidator, QIntValidator, QPixmap, QKeySequence, QPainter, QPen
from PyQt5.QtCore import Qt, QUrl, QDateTime, QDir, QObject, pyqtSignal, pyqtSlot, QThread, QTimer, QSize, QEvent
from setup.OpeningStockpileInventories import OpeningStockpileInventories
from execute.Run import Run
from classes.ExpitDataHandler import ExpitDataHandler
from classes.HaulCycleDataHandler import HaulCycleDataHandler
from classes.Optimizer import Optimizer
from classes.PeriodManager import PeriodManager
from classes.ManualBlendRules import ManualBlendRules
from classes.ManualBlendPlanner import (
    ManualBlendPlanner,
    ManualBlendPlanningError,
)
from classes.OptimisedToManualPlan import OptimisedToManualPlan
from datetime import datetime, timedelta
from GUI.DrawCharts import DrawGanttChart, DrawStockProfiles, DrawAMTStockpile
from GUI.ManualBlendDash import ManualBlendDash, DrawGradeProfiles, DrawOptimisedGradeProfiles
from GUI.ManualSteadyStateDialog import ManualSteadyStateDialog
from database.SQLiteDatabase import DatabaseManager
from database.DatabaseContext import get_database_path, set_database_path
from setup.PlanningPlanTargets import PlanningPlanTargets
import pandas as pd, sqlite3
from numbers import Real, Integral
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

APP_TITLE = "BlendMaster PoC v0.1.0 - 2025 Fortescue - MOPP"
APP_USER_MODEL_ID = "Fortescue.BlendMaster.PoC.v010"

SITE_OPF_OPTIONS = {
    "CC": ["CC OPF01", "CC OPF02"],
    "CB": ["CB OPF"],
    "EW": ["EW OPF"],
    "KV": ["KV OPF"],
    "FT": ["FT OPF"],
    "IB": ["IB OPF"],
}

SITE_CRUSHER_OPTIONS = {
    ("CC", "CC OPF01"): ["OPF01_PC", "HAL_PC", "Total_Feed_PC"],
    ("CC", "CC OPF02"): ["OPF02_PC"],
    ("CB", "CB OPF"): ["OPF01", "OPF02", "OPF03", "OPF04", "Total_Feed_PC"],
    ("EW", "EW OPF"): ["EW_PC"],
    ("KV", "KV OPF"): ["VK_PC", "VQ_PC", "Total_Feed_PC"],
    ("FT", "FT OPF"): ["FT_PC"],
    ("IB", "IB OPF"): ["Crusher"],
}


def opf_options_for_mine(mine):
    return SITE_OPF_OPTIONS.get(str(mine or "").strip().upper(), [])


def crusher_options_for_site(mine, opf=None):
    mine = str(mine or "").strip().upper()
    normalized_opf = " ".join(str(opf or "").strip().upper().split())
    if not normalized_opf:
        options = opf_options_for_mine(mine)
        normalized_opf = options[0] if options else ""
    return SITE_CRUSHER_OPTIONS.get((mine, normalized_opf), [])


def set_windows_app_user_model_id():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def app_bundle_root():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(*parts):
    return os.path.join(app_bundle_root(), "resources", *parts)


def preferred_resource_path(*names):
    for name in names:
        path = resource_path(name)
        if os.path.exists(path):
            return path
    return resource_path(names[0]) if names else resource_path()


def blendmaster_app_icon():
    icon = QIcon()
    for name in ("icon_2_v2.ico", "icon_1_v2.ico", "icon_v2.png", "icon_2.ico", "icon.png"):
        path = preferred_resource_path(name)
        if os.path.exists(path):
            icon.addFile(path)
    return icon


class FullCaptionTabBar(QTabBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDrawBase(False)
        self.setExpanding(False)
        self.setElideMode(Qt.ElideNone)
        self.setUsesScrollButtons(True)

    def tabSizeHint(self, index):
        caption_width = self.fontMetrics().horizontalAdvance(self.tabText(index))
        return QSize(max(caption_width + 44, 128), 36)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        border = QColor("#d8e0ea")

        for index in range(self.count()):
            rect = self.tabRect(index)
            if not rect.isValid():
                continue

            selected = index == self.currentIndex()
            painter.fillRect(rect, QColor("#ffffff" if selected else "#f1f5f9"))
            painter.setPen(QPen(border))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))

            if selected:
                painter.fillRect(rect.left(), rect.top(), rect.width(), 3, QColor("#0f766e"))

            font = painter.font()
            font.setWeight(QFont.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor("#0f172a" if selected else "#334155"))
            painter.drawText(rect.adjusted(16, 4, -16, -4), Qt.AlignCenter, self.tabText(index))


class MultiSelectBlendComboBox(QComboBox):
    """Compact checkable selector used for assigning several manual blends."""

    selectionChanged = pyqtSignal()

    def __init__(self, parent=None, blend_ids=None):
        super().__init__(parent)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setAlignment(Qt.AlignCenter)
        self.view().viewport().installEventFilter(self)
        for blend_id in (blend_ids or range(1, 6)):
            super().addItem(str(blend_id))
            item = self.model().item(self.count() - 1)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setData(Qt.Unchecked, Qt.CheckStateRole)
        self.update_display_text()

    def eventFilter(self, watched, event):
        if (
            watched is self.view().viewport()
            and event.type() == QEvent.MouseButtonRelease
        ):
            index = self.view().indexAt(event.pos())
            if index.isValid():
                item = self.model().item(index.row())
                item.setCheckState(
                    Qt.Unchecked
                    if item.checkState() == Qt.Checked
                    else Qt.Checked
                )
                self.update_display_text()
                self.selectionChanged.emit()
            return True
        return super().eventFilter(watched, event)

    def selected_blend_ids(self):
        return [
            self.itemText(index)
            for index in range(self.count())
            if self.model().item(index).checkState() == Qt.Checked
        ]

    def set_selected_blend_ids(self, blend_ids):
        available = {
            self.itemText(index) for index in range(self.count())
        }
        selected = {
            str(value).strip()
            for value in (blend_ids or [])
            if str(value).strip() in available
        }
        blocked = self.blockSignals(True)
        try:
            for index in range(self.count()):
                self.model().item(index).setCheckState(
                    Qt.Checked
                    if self.itemText(index) in selected
                    else Qt.Unchecked
                )
            self.update_display_text()
        finally:
            self.blockSignals(blocked)

    def setCurrentText(self, text):
        values = [
            value.strip()
            for value in str(text or "").replace(";", ",").split(",")
            if value.strip() and value.strip().lower() != "none"
        ]
        self.set_selected_blend_ids(values)
        self.selectionChanged.emit()

    def currentText(self):
        values = self.selected_blend_ids()
        return ", ".join(values) if values else "None"

    def update_display_text(self):
        self.lineEdit().setText(self.currentText())


def create_startup_splash():
    image_path = preferred_resource_path("splash_v2.png", "background_v2.PNG", "background.PNG")
    pixmap = QPixmap(image_path)
    if pixmap.isNull():
        pixmap = QPixmap(720, 480)
        pixmap.fill(QColor("#ffffff"))
    else:
        pixmap = pixmap.scaled(720, 520, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    splash = QSplashScreen(pixmap)
    splash.setWindowIcon(blendmaster_app_icon())
    if os.path.basename(image_path).lower() != "splash_v2.png":
        splash.showMessage(
            APP_TITLE,
            Qt.AlignBottom | Qt.AlignHCenter,
            QColor("#172033"),
        )
    return splash


def close_bootloader_splash():
    """Close the PyInstaller splash screen when running from the packaged exe."""
    try:
        import pyi_splash
        pyi_splash.close()
    except Exception:
        pass

class UserInputs(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(blendmaster_app_icon())
        self.setGeometry(100, 100, 800, 600)

        self.initialise_all_variables()
        self.clear_sqlite_session_data()

        # Placeholder for OpeningStockpileInventories
        self.opening_stockpile_inventories = OpeningStockpileInventories()

        # Main Widget and Layout
        self.central_widget = QWidget()
        self.central_widget.setObjectName("mainCentralWidget")
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)

        self.setup_scenario_toolbar()

        # Tabs
        self.tabs = QTabWidget()
        self.tabs.setTabBar(FullCaptionTabBar())
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.ElideNone)
        self.tabs.tabBar().setUsesScrollButtons(True)
        self.tabs.tabBar().setElideMode(Qt.ElideNone)
        self.tabs.tabBar().setExpanding(False)
        self.layout.addWidget(self.tabs)
        self.apply_app_theme()
        self.setup_navigation()

        # Add Site Configuration Tab
        self.setup_site_configuration()

        # Add Stockpile Tab
        self.stockpile_tab = QWidget()
        self.stockpile_tab_index = self.register_page(
            "stockpile_inventories",
            self.setup_tabs,
            self.stockpile_tab,
            "Stockpile Inventories",
        )
        self.stockpile_tab_layout = QVBoxLayout(self.stockpile_tab)

        # Stockpile Table
        self.stockpile_table = CustomTableWidget()
        self.stockpile_tab_layout.addWidget(self.stockpile_table)

        # Add AMT Stockpile Tab
        self.AMT_stockpile_tab = QWidget()
        self.AMT_stockpile_tab.setObjectName("amtStockpileTab")
        self.AMT_stockpile_tab_index = self.register_page(
            "amt_stockpiles",
            self.workspace_tabs,
            self.AMT_stockpile_tab,
            "AMT Stockpiles",
            position=0,
        )
        self.AMT_stockpile_tab_layout = QHBoxLayout(self.AMT_stockpile_tab)
        self.AMT_stockpile_tab_layout.setContentsMargins(12, 10, 12, 10)
        self.AMT_stockpile_tab_layout.setSpacing(10)
        self.AMT_stockpile_tab.setStyleSheet("""
            QWidget#amtStockpileTab {
                background-color: #f8fafc;
            }
            QTableWidget#amtStockpileTable {
                background-color: #ffffff;
                border: 1px solid #d8e0ea;
                border-radius: 6px;
                gridline-color: #e5e7eb;
                selection-background-color: #dbeafe;
                selection-color: #0f172a;
                alternate-background-color: #f8fbff;
            }
            QHeaderView::section {
                background-color: #f1f5f9;
                color: #0f172a;
                font-weight: 700;
                border: 0;
                border-right: 1px solid #dbe4ee;
                border-bottom: 1px solid #dbe4ee;
                padding: 7px 8px;
            }
            QFrame#amtSettingsFrame,
            QFrame#amtMapFrame {
                background-color: #ffffff;
                border: 1px solid #d8e0ea;
                border-radius: 6px;
            }
            QLabel#amtPanelTitle {
                color: #172033;
                font-size: 18px;
                font-weight: 750;
            }
            QLabel#amtPanelSubtitle {
                color: #64748b;
                font-size: 12px;
                padding-bottom: 4px;
            }
            QPushButton#loadAMTMapButton {
                background-color: #0f766e;
                color: white;
                border: 1px solid #0f766e;
                border-radius: 4px;
                font-size: 14px;
                font-weight: 650;
                padding: 8px 14px;
            }
            QPushButton#loadAMTMapButton:hover {
                background-color: #0d9488;
            }
            QPushButton#submitAMTChunksButton {
                background-color: #2563eb;
                color: white;
                border: 1px solid #2563eb;
                border-radius: 4px;
                font-size: 14px;
                font-weight: 650;
                padding: 8px 14px;
            }
            QPushButton#submitAMTChunksButton:hover {
                background-color: #1d4ed8;
            }
        """)

        # Stockpile AMT Table
        self.AMT_stockpile_table = CustomTableWidget()
        self.AMT_stockpile_table.setObjectName("amtStockpileTable")
        self.AMT_stockpile_table.setMinimumWidth(560)
        self.AMT_stockpile_table.setAlternatingRowColors(True)
        self.AMT_stockpile_table.setSelectionBehavior(QAbstractItemView.SelectRows)

        self.AMT_settings_frame = QFrame()
        self.AMT_settings_frame.setObjectName("amtSettingsFrame")
        self.AMT_settings_frame.setFrameShape(QFrame.NoFrame)
        self.AMT_settings_layout = QVBoxLayout(self.AMT_settings_frame)
        self.AMT_settings_layout.setContentsMargins(12, 12, 12, 12)
        self.AMT_settings_layout.setSpacing(8)

        AMT_settings_title = QLabel("Chunk Settings")
        AMT_settings_title.setObjectName("amtPanelTitle")
        self.AMT_settings_layout.addWidget(AMT_settings_title)

        AMT_settings_subtitle = QLabel("Set reclaim rate and target hours for each selected AMT stockpile.")
        AMT_settings_subtitle.setObjectName("amtPanelSubtitle")
        AMT_settings_subtitle.setWordWrap(True)
        self.AMT_settings_layout.addWidget(AMT_settings_subtitle)
        self.AMT_settings_layout.addWidget(self.AMT_stockpile_table)

        self.AMT_stockpile_tab_layout.addWidget(self.AMT_settings_frame, stretch=0)

        # Add Solver Configuration Tab
        self.setup_solver_configuration_tab()

        # Add Product Build Settings tab
        self.setup_product_build_settings_tab()

        # Decision Levers is the user-facing home for the simplified solver
        # controls introduced in the dedicated Decision Levers task.
        self.setup_decision_levers_tab()

        # Add calendar Tab
        self.main_tab = QWidget()
        self.calendar_tab_index = self.register_page(
            "calendar",
            self.auto_blend_tabs,
            self.main_tab,
            "Calendar",
        )
        self.main_tab_layout = QVBoxLayout(self.main_tab)
        self.main_tab_layout.setContentsMargins(14, 12, 14, 12)
        self.main_tab_layout.setSpacing(8)

        calendar_title = QLabel("Calendar")
        calendar_title.setStyleSheet("font-weight: 750; font-size: 20px; color: #172033;")
        self.main_tab_layout.addWidget(calendar_title)

        calendar_subtitle = QLabel("Set period rates, targets, direct-tip constraints, and source states.")
        calendar_subtitle.setStyleSheet("font-size: 12px; color: #64748b; padding-bottom: 4px;")
        self.main_tab_layout.addWidget(calendar_subtitle)

        # Calendar table
        self.main_table = CustomTableWidget()
        self.main_tab_layout.addWidget(self.main_table)

        # Add Decision Point Tab
        self.decision_point_tab = QWidget()
        self.decision_point_tab_index = self.register_page(
            "decision_point",
            self.auto_blend_tabs,
            self.decision_point_tab,
            "Decision Point",
        )
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

        self.setup_optimised_grade_profile_tab()

        self.setup_sqlite_reports_tab()

        # Add Setup Blends tab
        self.blend_config_tab = QWidget()
        self.blend_config_tab_index = self.register_page(
            "setup_blends",
            self.manual_blend_tabs,
            self.blend_config_tab,
            "Setup Blends",
        )
        self.setup_blends_tab_layout = QVBoxLayout(self.blend_config_tab)

        # Add Sequence tab
        self.blend_sequence_tab = QWidget()
        self.blend_sequence_tab_index = self.register_page(
            "blend_sequence",
            self.manual_blend_tabs,
            self.blend_sequence_tab,
            "Blend Sequence",
        )
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
        self.setup_agent_instructions_tab()
        self.update_tab_tooltips()

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
        for page_id in (
            self.stockpile_tab_index,
            self.AMT_stockpile_tab_index,
            self.solver_config_tab_index,
            self.product_build_tab_index,
            self.decision_levers_tab_index,
            self.calendar_tab_index,
            self.decision_point_tab_index,
            self.results_tab_index,
            self.profiles_tab_index,
            self.sqlite_reports_tab_index,
            self.optimised_grade_profile_tab_index,
            self.blend_config_tab_index,
            self.blend_sequence_tab_index,
            self.grade_profile_tab_index,
        ):
            self.set_page_enabled(page_id, False)
        self.set_page_enabled(
            self.agent_tab_index,
            bool(getattr(self, "agent_enabled_choice", False)),
        )

        # Initialise main optimisation program
        self.run_program = Run(self)

    def new_navigation_tabs(self):
        tabs = QTabWidget()
        tabs.setTabBar(FullCaptionTabBar())
        tabs.setUsesScrollButtons(True)
        tabs.setElideMode(Qt.ElideNone)
        tabs.tabBar().setUsesScrollButtons(True)
        tabs.tabBar().setElideMode(Qt.ElideNone)
        tabs.tabBar().setExpanding(False)
        tabs.currentChanged.connect(
            lambda index, tab_widget=tabs: self.handle_navigation_tab_changed(
                tab_widget, index
            )
        )
        self.navigation_tab_widgets.append(tabs)
        return tabs

    def navigation_container(self, child_tabs):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(child_tabs)
        return container

    def setup_navigation(self):
        """Create the task-oriented parent/sub-tab navigation hierarchy."""
        self.page_locations = {}
        self.page_widgets = {}
        self.navigation_parents = {}
        self.navigation_tab_widgets = [self.tabs]
        self.tabs.currentChanged.connect(
            lambda index: self.handle_navigation_tab_changed(self.tabs, index)
        )

        self.setup_tabs = self.new_navigation_tabs()
        self.setup_navigation_page = self.navigation_container(self.setup_tabs)
        self.tabs.addTab(self.setup_navigation_page, "Setup")
        self.navigation_parents[self.setup_tabs] = (
            self.tabs,
            self.setup_navigation_page,
        )

        self.workspace_tabs = self.new_navigation_tabs()
        self.workspace_navigation_page = self.navigation_container(self.workspace_tabs)
        self.tabs.addTab(self.workspace_navigation_page, "Workspace")
        self.navigation_parents[self.workspace_tabs] = (
            self.tabs,
            self.workspace_navigation_page,
        )

        self.auto_blend_tabs = self.new_navigation_tabs()
        self.auto_blend_navigation_page = self.navigation_container(self.auto_blend_tabs)
        self.workspace_tabs.addTab(
            self.auto_blend_navigation_page,
            "Auto Blending Dashboard",
        )
        self.navigation_parents[self.auto_blend_tabs] = (
            self.workspace_tabs,
            self.auto_blend_navigation_page,
        )

        self.manual_blend_tabs = self.new_navigation_tabs()
        self.manual_blend_navigation_page = self.navigation_container(self.manual_blend_tabs)
        self.workspace_tabs.addTab(
            self.manual_blend_navigation_page,
            "Manual Blending Dashboard",
        )
        self.navigation_parents[self.manual_blend_tabs] = (
            self.workspace_tabs,
            self.manual_blend_navigation_page,
        )

        self.results_tabs = self.new_navigation_tabs()
        self.results_navigation_page = self.navigation_container(self.results_tabs)
        self.tabs.addTab(self.results_navigation_page, "Results")
        self.navigation_parents[self.results_tabs] = (
            self.tabs,
            self.results_navigation_page,
        )

        self.grade_profiles_tabs = self.new_navigation_tabs()
        self.grade_profiles_navigation_page = self.navigation_container(
            self.grade_profiles_tabs
        )
        self.results_tabs.addTab(
            self.grade_profiles_navigation_page,
            "Grade Profiles",
        )
        self.navigation_parents[self.grade_profiles_tabs] = (
            self.results_tabs,
            self.grade_profiles_navigation_page,
        )

        # Old project files stored enabled state by the former flat-tab index.
        self.legacy_tab_page_ids = [
            "site_configuration",
            "stockpile_inventories",
            "amt_stockpiles",
            "solver_configuration",
            "product_build_settings",
            "calendar",
            "decision_point",
            "optimised_blend_sequence",
            "build_depletion_profiles",
            "optimised_grade_profiles",
            "reports",
            "setup_blends",
            "blend_sequence",
            "manual_grade_profiles",
            "agent",
        ]

    def register_page(self, page_id, tab_widget, page, caption, position=None):
        if position is None:
            tab_index = tab_widget.addTab(page, caption)
        else:
            tab_index = tab_widget.insertTab(position, page, caption)
            for existing_page_id, (existing_tabs, existing_index) in list(
                self.page_locations.items()
            ):
                if existing_tabs is tab_widget and existing_index >= tab_index:
                    self.page_locations[existing_page_id] = (
                        existing_tabs,
                        existing_index + 1,
                    )
        self.page_locations[page_id] = (tab_widget, tab_index)
        self.page_widgets[page_id] = page
        return page_id

    def set_page_enabled(self, page_id, enabled):
        location = self.page_locations.get(page_id)
        if location is None:
            return
        tab_widget, tab_index = location
        tab_widget.setTabEnabled(tab_index, bool(enabled))

    def is_page_enabled(self, page_id):
        location = self.page_locations.get(page_id)
        if location is None:
            return False
        tab_widget, tab_index = location
        return tab_widget.isTabEnabled(tab_index)

    def show_page(self, page_id, force=False):
        if (
            getattr(
                self,
                "project_load_keep_site_configuration_visible",
                False,
            )
            and page_id != "site_configuration"
            and not force
        ):
            return
        location = self.page_locations.get(page_id)
        if location is None:
            return
        page = self.page_widgets[page_id]
        tab_widget, tab_index = location
        current_tabs = tab_widget
        while current_tabs in self.navigation_parents:
            parent_tabs, parent_page = self.navigation_parents[current_tabs]
            parent_tabs.setCurrentWidget(parent_page)
            current_tabs = parent_tabs
        tab_widget.setCurrentIndex(tab_index)
        page.setFocus(Qt.OtherFocusReason)

    def capture_page_states(self):
        return {
            page_id: self.is_page_enabled(page_id)
            for page_id in self.page_locations
        }

    def normalized_page_states(self, raw_states):
        if not isinstance(raw_states, dict):
            return {}
        normalized = {}
        for key, enabled in raw_states.items():
            if key in self.page_locations:
                normalized[key] = bool(enabled)
                continue
            try:
                legacy_index = int(key)
            except (TypeError, ValueError):
                continue
            if 0 <= legacy_index < len(self.legacy_tab_page_ids):
                normalized[self.legacy_tab_page_ids[legacy_index]] = bool(enabled)
        return normalized

    def restore_page_states(self, raw_states):
        states = self.normalized_page_states(raw_states)
        for page_id, enabled in states.items():
            self.set_page_enabled(page_id, enabled)
        return states

    def handle_navigation_tab_changed(self, tab_widget, tab_index):
        page = tab_widget.widget(tab_index)
        for page_id, registered_page in self.page_widgets.items():
            if page is registered_page:
                self.handle_main_tab_changed(page_id)
                return

    def setup_scenario_toolbar(self):
        self.scenario_toolbar = QFrame()
        self.scenario_toolbar.setObjectName("scenarioToolbar")
        self.scenario_toolbar.setStyleSheet("""
            QFrame#scenarioToolbar {
                background-color: #eef4f8;
                border: 1px solid #d5e0e8;
                border-radius: 5px;
            }
            QLabel#scenarioToolbarTitle {
                color: #172033;
                font-weight: 700;
            }
        """)
        toolbar_layout = QHBoxLayout(self.scenario_toolbar)
        toolbar_layout.setContentsMargins(10, 5, 10, 5)
        toolbar_layout.setSpacing(8)

        title = QLabel("Active Site Scenario:")
        title.setObjectName("scenarioToolbarTitle")
        self.scenario_selector = QComboBox()
        self.scenario_selector.setMinimumWidth(260)
        self.scenario_selector.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.add_scenario_button = QPushButton("Add Site")
        self.remove_scenario_button = QPushButton("Remove Site")
        self.scenario_status_label = QLabel("")
        self.scenario_status_label.setStyleSheet("color: #526777;")

        toolbar_layout.addWidget(title)
        toolbar_layout.addWidget(self.scenario_selector)
        toolbar_layout.addWidget(self.add_scenario_button)
        toolbar_layout.addWidget(self.remove_scenario_button)
        toolbar_layout.addWidget(self.scenario_status_label)
        toolbar_layout.addStretch()
        self.layout.addWidget(self.scenario_toolbar)

        self.scenario_selector.currentIndexChanged.connect(self.handle_scenario_selection_changed)
        self.add_scenario_button.clicked.connect(self.add_blank_site_scenario)
        self.remove_scenario_button.clicked.connect(self.remove_active_site_scenario)
        self.refresh_scenario_selector()

    @staticmethod
    def scenario_display_name(state, fallback="New Site"):
        state = state or {}
        hub = str(state.get("hub_input_choice") or "").strip()
        mine = str(state.get("mine_input_choice") or "").strip()
        opf = str(state.get("opf_input_choice") or "").strip()
        crusher = str(state.get("crusher_input_choice") or "").strip()
        parts = [value for value in (hub, mine, opf, crusher) if value]
        return " / ".join(parts) if parts else fallback

    def scenario_database_path(self, scenario_id):
        return os.path.join(self.scenario_session_directory, f"{scenario_id}.db")

    @staticmethod
    def database_contains_table(database_path, table_name):
        if not database_path or not os.path.exists(database_path):
            return False
        connection = None
        try:
            connection = sqlite3.connect(database_path)
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
                (table_name,),
            ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False
        finally:
            if connection is not None:
                connection.close()

    def seed_active_scenario_database(self, force=False):
        """Recreate opening inventory tables when a scenario DB is new or restored."""
        database_path = get_database_path()
        if self.stockpile_data and (
            force or not self.database_contains_table(database_path, "opening_stockpile_inventories")
        ):
            self.opening_stockpile_inventories.save_to_database(self.stockpile_data)
        if self.AMT_stockpile_data and (
            force or not self.database_contains_table(database_path, "opening_AMT_stockpile_inventories")
        ):
            self.opening_stockpile_inventories.save_AMT_to_database(self.AMT_stockpile_data)

    @staticmethod
    def snapshot_database(database_path):
        if not database_path or not os.path.exists(database_path):
            return None
        snapshot_path = f"{database_path}.project_snapshot"
        source = destination = None
        try:
            try:
                os.remove(snapshot_path)
            except FileNotFoundError:
                pass
            source = sqlite3.connect(database_path)
            destination = sqlite3.connect(snapshot_path)
            source.backup(destination)
            destination.close()
            destination = None
            with open(snapshot_path, "rb") as snapshot_file:
                return snapshot_file.read()
        finally:
            if destination is not None:
                destination.close()
            if source is not None:
                source.close()
            try:
                os.remove(snapshot_path)
            except OSError:
                pass

    @staticmethod
    def restore_database_snapshot(database_path, database_bytes):
        if not database_bytes:
            return
        os.makedirs(os.path.dirname(database_path), exist_ok=True)
        with open(database_path, "wb") as database_file:
            database_file.write(database_bytes)

    def refresh_scenario_selector(self):
        if not hasattr(self, "scenario_selector"):
            return
        self.scenario_switch_in_progress = True
        try:
            self.scenario_selector.clear()
            for scenario_id, state in self.site_scenarios.items():
                self.scenario_selector.addItem(
                    self.scenario_display_name(state, f"Site {self.scenario_selector.count() + 1}"),
                    scenario_id,
                )
            index = self.scenario_selector.findData(self.active_scenario_id)
            if index >= 0:
                self.scenario_selector.setCurrentIndex(index)
            self.remove_scenario_button.setEnabled(len(self.site_scenarios) > 1)
            active = self.site_scenarios.get(self.active_scenario_id, {})
            self.scenario_status_label.setText(
                "Configured" if active.get("stockpile_data") else "Awaiting Site Configuration"
            )
        finally:
            self.scenario_switch_in_progress = False

    def new_scenario_id(self):
        return f"site_{uuid.uuid4().hex[:10]}"

    def add_blank_site_scenario(self):
        if getattr(self, "background_tasks", []):
            QMessageBox.information(self, "BlendMaster", "Wait for the current background task before changing sites.")
            return
        self.save_active_scenario_state()
        scenario_id = self.new_scenario_id()
        self.site_scenarios[scenario_id] = {
            "scenario_id": scenario_id,
            "database_path": self.scenario_database_path(scenario_id),
        }
        self.active_scenario_id = scenario_id
        self.refresh_scenario_selector()
        self.restore_site_scenario(self.site_scenarios[scenario_id])

    def remove_active_site_scenario(self):
        if len(self.site_scenarios) <= 1:
            return
        reply = QMessageBox.question(
            self,
            "Remove Site Scenario",
            "Remove the active site scenario from this model?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        removed_id = self.active_scenario_id
        remaining_ids = [key for key in self.site_scenarios if key != removed_id]
        self.site_scenarios.pop(removed_id, None)
        self.active_scenario_id = remaining_ids[0]
        self.refresh_scenario_selector()
        self.restore_site_scenario(self.site_scenarios[self.active_scenario_id])

    def handle_scenario_selection_changed(self, index):
        if self.scenario_switch_in_progress or index < 0:
            return
        scenario_id = self.scenario_selector.itemData(index)
        if not scenario_id or scenario_id == self.active_scenario_id:
            return
        if getattr(self, "background_tasks", []):
            QMessageBox.information(self, "BlendMaster", "Wait for the current background task before changing sites.")
            self.refresh_scenario_selector()
            return
        self.save_active_scenario_state()
        self.active_scenario_id = scenario_id
        self.restore_site_scenario(self.site_scenarios.get(scenario_id, {}))

    def capture_calendar_table_inputs(self):
        if not hasattr(self, "main_table") or not hasattr(self, "calendar_headers"):
            return copy.deepcopy(self.calendar_inputs or {})
        captured = copy.deepcopy(self.calendar_inputs or {})
        headers = self.calendar_headers[1:]
        hierarchy = []
        for row_idx in range(self.main_table.rowCount()):
            caption_item = self.main_table.item(row_idx, 0)
            if caption_item is None:
                continue
            caption = caption_item.text()
            indent_level = int((len(caption) - len(caption.lstrip())) / 2)
            while len(hierarchy) > indent_level:
                hierarchy.pop()
            hierarchy.append(caption.strip().replace(" ", "_").lower())
            full_key = "_".join(hierarchy)
            values = {}
            for column, header in enumerate(headers, start=1):
                value = self.get_main_table_cell_text(row_idx, column)
                if value not in (None, "") and "state" not in full_key:
                    try:
                        value = float(value)
                    except (TypeError, ValueError):
                        pass
                values[header] = value
            captured[full_key] = values
        captured["solver_config"] = copy.deepcopy(self.solver_config or {})
        captured["product_build_settings"] = copy.deepcopy(self.product_build_settings or [])
        captured["product_brand_labels"] = copy.deepcopy(self.product_brand_options())
        captured["site_context"] = self.active_site_context()
        return captured

    def capture_stockpile_table_choices(self):
        if not hasattr(self, "stockpile_table"):
            return
        for row in range(self.stockpile_table.rowCount()):
            name_item = self.stockpile_table.item(row, 2)
            if name_item is None:
                continue
            name = name_item.text()
            for column, target in (
                (0, self.stockpile_data_use_column),
                (1, self.stockpile_data_AMT_column),
            ):
                wrapper = self.stockpile_table.cellWidget(row, column)
                if wrapper and wrapper.layout() and wrapper.layout().count():
                    checkbox = wrapper.layout().itemAt(0).widget()
                    if isinstance(checkbox, QCheckBox):
                        target[name] = checkbox.isChecked()

    @staticmethod
    def capture_table_snapshot(table):
        if table is None:
            return None
        headers = []
        for column in range(table.columnCount()):
            item = table.horizontalHeaderItem(column)
            headers.append(item.text() if item is not None else "")
        rows = []
        for row in range(table.rowCount()):
            rows.append([
                table.item(row, column).text() if table.item(row, column) is not None else ""
                for column in range(table.columnCount())
            ])
        return {"headers": headers, "rows": rows}

    @staticmethod
    def restore_table_snapshot(table, snapshot):
        if table is None:
            return
        snapshot = snapshot or {}
        headers = snapshot.get("headers") or []
        rows = snapshot.get("rows") or []
        table.clearContents()
        table.setColumnCount(len(headers))
        table.setRowCount(len(rows))
        if headers:
            table.setHorizontalHeaderLabels(headers)
        for row_index, values in enumerate(rows):
            for column_index, value in enumerate(values[:len(headers)]):
                table.setItem(row_index, column_index, QTableWidgetItem(str(value)))
        if headers:
            table.resizeColumnsToContents()

    def active_site_context(self):
        return {
            "scenario_id": self.active_scenario_id,
            "hub": getattr(self, "hub_input_choice", None),
            "mine": getattr(self, "mine_input_choice", None),
            "opf": getattr(self, "opf_input_choice", None),
            "crusher": getattr(self, "crusher_input_choice", None),
            "crusher_contribution_ratio": getattr(
                self, "crusher_contribution_ratio_choice", 1.0
            ),
            "direct_tip_movement_rules": copy.deepcopy(getattr(
                self, "direct_tip_movement_rules", []
            )),
            "aps_destination_guidance": copy.deepcopy(getattr(
                self, "aps_destination_guidance", {}
            )),
            "aps_stockpile_timing_guidance": copy.deepcopy(getattr(
                self, "aps_stockpile_timing_guidance", {}
            )),
            "aps_active_blend_guidance": copy.deepcopy(getattr(
                self, "aps_active_blend_guidance", []
            )),
            "selected_24hr_expit_agents": copy.deepcopy(getattr(
                self, "selected_24hr_expit_agents", []
            )),
            "selected_haul_cycle_crushers": copy.deepcopy(getattr(
                self, "selected_haul_cycle_crushers", []
            )),
        }

    def capture_scenario_state(self):
        self.capture_stockpile_table_choices()
        if hasattr(self, "auto_load_2wp_targets_checkbox"):
            self.auto_load_2wp_targets_choice = (
                self.auto_load_2wp_targets_checkbox.isChecked()
            )
        if hasattr(self, "solver_config_tab"):
            self.store_solver_config_inputs(show_errors=False)
        if hasattr(self, "product_build_table"):
            self.store_product_build_settings(show_errors=False)
        calendar_inputs = self.capture_calendar_table_inputs()
        fields = [
            "hub_input_choice", "mine_input_choice", "opf_input_choice",
            "crusher_input_choice", "selected_site_crushers",
            "crusher_ratio_mode_choice", "crusher_contribution_ratio_choice",
            "crusher_ratio_configured", "aps_ratio_crusher_choices",
            "direct_tip_grade_block_sources", "direct_tip_crusher_destinations",
            "direct_tip_movement_rules", "time_mode_choice", "start_time_choice",
            "expit_mode_choice", "file_path_choice", "file_path_24hr_choice",
            "available_24hr_expit_agents", "selected_24hr_expit_agents",
            "haul_cycle_file_path_choice",
            "available_haul_cycle_crushers",
            "selected_haul_cycle_crushers", "haul_cycle_routes",
            "blend_mode_choice",
            "product_brand_labels_choice", "product_build_settings",
            "auto_load_2wp_targets_choice",
            "reevaluate_aps_direct_tip_choice", "aps_direct_tip_crusher_choice",
            "aps_stockpile_brand_map", "aps_stockpile_timing_guidance",
            "aps_active_blend_guidance", "aps_destination_guidance",
            "stockpile_data", "stockpile_data_use_column",
            "stockpile_data_AMT_column", "updated_stockpile_data", "AMT_stockpile_data",
            "AMT_chunk_settings", "hex_sequence_table", "hex_sequence_table_argument",
            "solver_config", "min_stockpiles", "max_stockpiles",
            "min_stockpile_contribution_ratio", "saved_blends_for_schedule",
            "stored_blend_sequence_table_for_gantt",
            "stored_blend_sequence_table_for_gantt_default",
            "manual_direct_tip_allocations", "manual_steady_states",
            "default_start_datetime",
            "default_end_datetime", "default_start_datetime_str", "default_end_datetime_str",
            "crusher_rate", "crusher_rate_input_value",
            "crusher_rate_input_values", "blend_config_table_inputs",
        ]
        state = {"scenario_id": self.active_scenario_id, "calendar_inputs": calendar_inputs}
        for field in fields:
            state[field] = copy.deepcopy(getattr(self, field, None))
        state["database_path"] = self.scenario_database_path(self.active_scenario_id)
        state["decision_table_snapshot"] = self.capture_table_snapshot(
            getattr(self, "decision_table", None)
        )
        state["decision_output_text"] = (
            self.decision_output.toPlainText() if hasattr(self, "decision_output") else ""
        )
        state["decision_status_text"] = (
            self.decision_status_label.text() if hasattr(self, "decision_status_label") else ""
        )
        if hasattr(self, "tabs"):
            state["tab_states"] = self.capture_page_states()
        return state

    def save_active_scenario_state(self):
        if not self.active_scenario_id:
            return
        self.site_scenarios[self.active_scenario_id] = self.capture_scenario_state()
        self.refresh_scenario_selector()

    def reset_workflow_tabs_for_scenario(self):
        for tab_index in [
            self.stockpile_tab_index, self.AMT_stockpile_tab_index,
            self.solver_config_tab_index, self.product_build_tab_index,
            self.decision_levers_tab_index,
            self.calendar_tab_index, self.decision_point_tab_index,
            self.results_tab_index, self.profiles_tab_index,
            self.sqlite_reports_tab_index, self.optimised_grade_profile_tab_index,
            self.blend_config_tab_index, self.blend_sequence_tab_index,
            self.grade_profile_tab_index,
        ]:
            self.set_page_enabled(tab_index, False)

    def update_chart_database_context(self, reload_views=True, refresh_amt_map=True):
        """Point chart services at the active scenario without needless reloads."""
        database_path = get_database_path()
        for attribute in (
            "draw_gantt_chart",
            "draw_stockpile_profile_chart",
            "draw_optimised_grade_profile_chart",
            "draw_AMT_map",
        ):
            chart = getattr(self, attribute, None)
            if chart is not None and hasattr(chart, "db_path"):
                chart.db_path = database_path

        amt_chart = getattr(self, "draw_AMT_map", None)
        if amt_chart is not None and refresh_amt_map:
            amt_chart.update_chunk_settings(copy.deepcopy(self.AMT_chunk_settings))
            amt_chart.selected_points = copy.deepcopy(self.hex_sequence_table or [])
            amt_chart.data = amt_chart.fetch_data()
            amt_chart.unique_footprints = amt_chart.get_unique_footprints()
            amt_chart.clean_up_hex_sequence_table()
            amt_chart.update_sequence_counter()
            try:
                requests.post("http://localhost:8054/trigger-refresh", timeout=2)
            except requests.exceptions.RequestException:
                pass

        if not reload_views:
            return

        for view_name in (
            "gantt_chart_view",
            "stockpile_profile_chart_view",
            "optimised_grade_profile_chart_view",
            "AMT_map_view",
        ):
            view = getattr(self, view_name, None)
            if view is not None and not view.url().isEmpty():
                view.reload()

    def handle_main_tab_changed(self, tab_index):
        """Defer heavyweight report/chart work until the user opens that tab."""
        if self.scenario_switch_in_progress:
            return

        if (
            tab_index == self.sqlite_reports_tab_index
            and getattr(self, "scenario_report_refresh_pending", False)
        ):
            self.scenario_report_refresh_pending = False
            QTimer.singleShot(0, self.refresh_sqlite_reports)

    def refresh_manual_scenario_views(self, tab_states):
        def is_enabled(index):
            return bool(self.normalized_page_states(tab_states).get(index, False))

        previous_project_loaded = self.is_project_loaded
        self.is_project_loaded = True
        try:
            blend_tab_enabled = is_enabled(self.blend_config_tab_index)
            if blend_tab_enabled and self.updated_stockpile_data:
                self.total_AMT_stockpile_balances = {}
                self.populate_total_AMT_stockpile_balances()
                if self.setup_blends_tab_first_call:
                    self.setup_blends_tab()
                else:
                    self.populate_blend_config_table()
                    self.setup_blends_tab()
                if hasattr(self, "crusher_rate_input"):
                    self.set_manual_crusher_rate_inputs(
                        self.crusher_rate_input_values
                        or self.calendar_crusher_rate_values()
                    )
                self.update_blend_results()

            sequence_tab_enabled = is_enabled(self.blend_sequence_tab_index)
            if (
                sequence_tab_enabled
                and self.default_start_datetime is not None
                and self.default_end_datetime is not None
            ):
                self.setup_sequence_tab()
        finally:
            self.is_project_loaded = previous_project_loaded

    def restore_site_scenario(self, state):
        state = copy.deepcopy(state or {})
        self.scenario_switch_in_progress = True
        try:
            set_database_path(state.get("database_path") or self.scenario_database_path(self.active_scenario_id))
            self.reset_workflow_tabs_for_scenario()
            self.is_project_loaded = False

            self.hub_input_choice = state.get("hub_input_choice")
            self.mine_input_choice = state.get("mine_input_choice")
            self.opf_input_choice = state.get("opf_input_choice") or self.default_opf_for_site(
                self.mine_input_choice,
                state.get("crusher_input_choice"),
            )
            self.crusher_input_choice = state.get("crusher_input_choice")
            self.selected_site_crushers = state.get("selected_site_crushers") or (
                [self.crusher_input_choice] if self.crusher_input_choice else []
            )
            self.time_mode_choice = state.get("time_mode_choice") or 1
            self.start_time_choice = state.get("start_time_choice") or datetime.now()
            self.expit_mode_choice = state.get("expit_mode_choice") or 1
            self.file_path_choice = state.get("file_path_choice") or ""
            self.file_path_24hr_choice = (
                state.get("file_path_24hr_choice")
                or state.get("twenty_four_hour_file_path_choice")
                or ""
            )
            self.available_24hr_expit_agents = self.normalized_expit_agent_names(
                state.get("available_24hr_expit_agents") or []
            )
            self.selected_24hr_expit_agents = self.normalized_expit_agent_names(
                state.get("selected_24hr_expit_agents") or []
            )
            self.haul_cycle_file_path_choice = str(
                state.get("haul_cycle_file_path_choice") or ""
            )
            self.available_haul_cycle_crushers = (
                self.normalized_expit_agent_names(
                    state.get("available_haul_cycle_crushers") or []
                )
            )
            self.selected_haul_cycle_crushers = (
                self.normalized_expit_agent_names(
                    state.get("selected_haul_cycle_crushers") or []
                )
            )
            self.haul_cycle_routes = copy.deepcopy(
                state.get("haul_cycle_routes") or {}
            )
            self.blend_mode_choice = state.get("blend_mode_choice") or 1
            self.product_brand_labels_choice = self.parse_product_brand_labels(
                state.get("product_brand_labels_choice") or self.default_product_brand_labels()
            )
            self.product_build_settings = copy.deepcopy(state.get("product_build_settings") or [])
            self.auto_load_2wp_targets_choice = bool(
                state.get("auto_load_2wp_targets_choice", True)
            )
            self.reevaluate_aps_direct_tip_choice = bool(state.get("reevaluate_aps_direct_tip_choice", False))
            self.aps_direct_tip_crusher_choice = self.normalized_aps_crusher_choice(
                state.get("aps_direct_tip_crusher_choice") or []
            )
            self.crusher_ratio_mode_choice = self.normalized_crusher_ratio_mode(
                state.get("crusher_ratio_mode_choice")
            )
            self.crusher_contribution_ratio_choice = self.normalized_crusher_ratio(
                state.get("crusher_contribution_ratio_choice", 1.0),
                default=1.0,
            )
            self.crusher_ratio_configured = bool(
                state.get("crusher_ratio_configured", False)
            )
            self.aps_ratio_crusher_choices = self.normalized_aps_crusher_choice(
                state.get("aps_ratio_crusher_choices") or []
            )
            self.direct_tip_grade_block_sources = list(
                state.get("direct_tip_grade_block_sources") or []
            )
            self.direct_tip_crusher_destinations = list(
                state.get("direct_tip_crusher_destinations") or []
            )
            self.direct_tip_movement_rules = ExpitDataHandler._normalize_movement_rules(
                state.get("direct_tip_movement_rules") or []
            )
            self.aps_stockpile_brand_map = copy.deepcopy(state.get("aps_stockpile_brand_map") or {})
            self.aps_stockpile_timing_guidance = copy.deepcopy(
                state.get("aps_stockpile_timing_guidance") or {}
            )
            self.aps_active_blend_guidance = copy.deepcopy(
                state.get("aps_active_blend_guidance") or []
            )
            self.aps_destination_guidance = copy.deepcopy(
                state.get("aps_destination_guidance") or {}
            )
            self.stockpile_data = copy.deepcopy(state.get("stockpile_data"))
            self.stockpile_data_use_column = copy.deepcopy(state.get("stockpile_data_use_column") or {})
            self.stockpile_data_AMT_column = copy.deepcopy(state.get("stockpile_data_AMT_column") or {})
            self.updated_stockpile_data = copy.deepcopy(state.get("updated_stockpile_data"))
            self.updated_stockpile_data_keys = (self.updated_stockpile_data or {}).keys()
            self.AMT_stockpile_data = copy.deepcopy(state.get("AMT_stockpile_data") or {})
            self.AMT_chunk_settings = copy.deepcopy(state.get("AMT_chunk_settings") or {})
            self.hex_sequence_table = copy.deepcopy(state.get("hex_sequence_table") or [])
            self.hex_sequence_table_argument = copy.deepcopy(
                state.get("hex_sequence_table_argument") or self.hex_sequence_table
            )
            self.calendar_inputs = copy.deepcopy(state.get("calendar_inputs") or {})
            self.solver_config = self.normalized_solver_config(state.get("solver_config") or {})
            self.min_stockpiles = state.get("min_stockpiles")
            self.max_stockpiles = state.get("max_stockpiles")
            self.min_stockpile_contribution_ratio = state.get(
                "min_stockpile_contribution_ratio",
                Optimizer.MIN_SELECTED_STOCKPILE_BLEND_RATIO,
            )
            self.saved_blends_for_schedule = copy.deepcopy(
                state.get("saved_blends_for_schedule") or []
            )
            self.stored_blend_sequence_table_for_gantt = copy.deepcopy(
                state.get("stored_blend_sequence_table_for_gantt") or []
            )
            self.stored_blend_sequence_table_for_gantt_default = copy.deepcopy(
                state.get("stored_blend_sequence_table_for_gantt_default") or []
            )
            self.manual_direct_tip_allocations = copy.deepcopy(
                state.get("manual_direct_tip_allocations") or {}
            )
            self.manual_steady_states = copy.deepcopy(
                state.get("manual_steady_states") or []
            )
            self.manual_blend_report = pd.DataFrame()
            self.default_start_datetime = state.get("default_start_datetime")
            self.default_end_datetime = state.get("default_end_datetime")
            self.default_start_datetime_str = state.get("default_start_datetime_str")
            self.default_end_datetime_str = state.get("default_end_datetime_str")
            self.crusher_rate = state.get("crusher_rate")
            self.crusher_rate_input_value = state.get("crusher_rate_input_value")
            self.crusher_rate_input_values = copy.deepcopy(
                state.get("crusher_rate_input_values") or {}
            )
            self.blend_config_table_inputs = copy.deepcopy(
                state.get("blend_config_table_inputs") or {}
            )
            self.restore_table_snapshot(
                self.decision_table,
                state.get("decision_table_snapshot"),
            )
            self.decision_output.setPlainText(state.get("decision_output_text") or "")
            self.decision_status_label.setText(
                state.get("decision_status_text")
                or "Run the optimiser to review feasible blend options."
            )
            self.seed_active_scenario_database()

            self.hub_input.setCurrentText(self.hub_input_choice or self.hub_input.itemText(0))
            self.update_mine_dropdown()
            if self.mine_input_choice:
                self.mine_input.setCurrentText(self.mine_input_choice)
            self.update_opf_dropdown(self.opf_input_choice)
            self.update_site_crusher_options(self.selected_site_crushers)
            self.file_path.setText(self.file_path_choice)
            self.file_path_24hr.setText(self.file_path_24hr_choice)
            self.set_24hr_expit_agent_items(
                self.available_24hr_expit_agents,
                self.selected_24hr_expit_agents,
            )
            self.haul_cycle_file_path.setText(
                self.haul_cycle_file_path_choice
            )
            self.set_haul_cycle_crusher_items(
                self.available_haul_cycle_crushers,
                self.selected_haul_cycle_crushers,
            )
            self.load_ratio_controls_from_state()
            self.set_direct_tip_movement_options(
                self.direct_tip_grade_block_sources,
                self.direct_tip_crusher_destinations,
            )
            self.refresh_direct_tip_rule_list()
            self.time_mode.setCurrentIndex(max(self.time_mode_choice - 1, 0))
            start_time = self.start_time_choice
            self.start_time.setDateTime(QDateTime(
                start_time.year,
                start_time.month,
                start_time.day,
                start_time.hour,
                start_time.minute,
                start_time.second,
            ))
            self.expit_mode.setCurrentIndex(max(self.expit_mode_choice - 1, 0))
            self.blend_mode.setCurrentIndex(max(self.blend_mode_choice - 1, 0))
            self.product_brand_labels_input.setText(", ".join(self.product_brand_labels_choice))
            self.auto_load_2wp_targets_checkbox.setChecked(
                self.auto_load_2wp_targets_choice
            )
            self.reevaluate_aps_direct_tip_checkbox.setChecked(self.reevaluate_aps_direct_tip_choice)
            self.set_aps_crusher_items(
                self.aps_direct_tip_crusher_choice,
                self.aps_direct_tip_crusher_choice,
            )
            self.populate_product_build_table()
            self.load_solver_config_inputs()

            if self.stockpile_data:
                # Brand guidance depends on the active scenario's CSV, mine,
                # OPF, crusher and configured product labels. Recalculate it
                # after restoring a scenario instead of relying on a stale or
                # legacy saved map.
                self.refresh_aps_stockpile_brand_map()
                self.apply_aps_brand_guidance_to_stockpile_data()
                self.setup_stockpile_table()
                self.set_page_enabled(self.stockpile_tab_index, True)
            if self.updated_stockpile_data:
                self.set_page_enabled(self.solver_config_tab_index, True)
                self.set_page_enabled(self.product_build_tab_index, True)
                self.set_page_enabled(self.decision_levers_tab_index, True)
                self.setup_calendar()
                self.set_page_enabled(self.calendar_tab_index, True)
            if any((self.stockpile_data_AMT_column or {}).values()) and self.AMT_stockpile_data:
                self.set_page_enabled(self.AMT_stockpile_tab_index, True)

            tab_states = state.get("tab_states") or {}
            self.restore_page_states(tab_states)
            self.refresh_manual_scenario_views(tab_states)
            self.set_page_enabled(self.site_config_tab_index, True)
            self.show_page(self.site_config_tab_index)
            self.validate_form()
            # Reports and Dash views can be expensive (large SQLite previews,
            # Dash reloads and AMT map data fetches). They refresh on demand
            # when their tab is next opened or its Load/Update button is used.
            self.scenario_report_refresh_pending = True
            self.update_chart_database_context(
                reload_views=False,
                # The AMT map needs its saved sequence applied immediately;
                # otherwise the table restores but the dig path is absent.
                refresh_amt_map=True,
            )
        finally:
            self.scenario_switch_in_progress = False
            self.refresh_scenario_selector()

    def update_tab_tooltips(self):
        for tab_widget in self.navigation_tab_widgets:
            for index in range(tab_widget.count()):
                tab_widget.setTabToolTip(index, tab_widget.tabText(index))

    def apply_windows_taskbar_icon(self):
        if sys.platform != "win32":
            return
        icon_path = preferred_resource_path("icon_2_v2.ico", "icon_1_v2.ico", "icon_2.ico")
        if not os.path.exists(icon_path):
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            image_icon = 1
            lr_load_from_file = 0x00000010
            wm_seticon = 0x0080
            icon_small = 0
            icon_big = 1
            user32 = ctypes.windll.user32
            hicon_big = user32.LoadImageW(None, icon_path, image_icon, 256, 256, lr_load_from_file)
            hicon_small = user32.LoadImageW(None, icon_path, image_icon, 32, 32, lr_load_from_file)
            if hicon_big:
                user32.SendMessageW(hwnd, wm_seticon, icon_big, hicon_big)
            if hicon_small:
                user32.SendMessageW(hwnd, wm_seticon, icon_small, hicon_small)
            self._windows_icon_handles = [handle for handle in (hicon_big, hicon_small) if handle]
        except Exception:
            pass

    def setup_solver_configuration_tab(self):
        self.solver_config_tab = QWidget()
        self.solver_config_tab_index = self.register_page(
            "solver_configuration",
            self.setup_tabs,
            self.solver_config_tab,
            "Solver Configuration",
        )
        solver_root_layout = QVBoxLayout(self.solver_config_tab)
        solver_root_layout.setContentsMargins(0, 0, 0, 0)
        solver_scroll = QScrollArea()
        solver_scroll.setWidgetResizable(True)
        solver_scroll.setFrameShape(QFrame.NoFrame)
        solver_content = QWidget()
        self.solver_config_layout = QVBoxLayout(solver_content)
        self.solver_config_layout.setContentsMargins(18, 16, 18, 16)
        self.solver_config_layout.setSpacing(8)
        solver_scroll.setWidget(solver_content)
        solver_root_layout.addWidget(solver_scroll)

        contribution_ratio_validator = QDoubleValidator(0.01, 1.0, 4, self)
        contribution_ratio_validator.setNotation(QDoubleValidator.StandardNotation)
        threshold_validator = QDoubleValidator(0.0, 1000.0, 4, self)
        threshold_validator.setNotation(QDoubleValidator.StandardNotation)
        hourly_cost_validator = QDoubleValidator(0.0, 100000.0, 4, self)
        hourly_cost_validator.setNotation(QDoubleValidator.StandardNotation)
        objective_value_validator = QDoubleValidator(
            0.0,
            1_000_000_000.0,
            4,
            self,
        )
        objective_value_validator.setNotation(
            QDoubleValidator.StandardNotation
        )
        signed_incentive_validator = QDoubleValidator(-1000.0, 1000.0, 4, self)
        signed_incentive_validator.setNotation(QDoubleValidator.StandardNotation)
        limits_label = QLabel("Blend Settings")
        limits_label.setStyleSheet("font-weight: bold;")
        self.solver_config_layout.addWidget(limits_label)

        throughput_incentive_layout = QHBoxLayout()
        self.throughput_incentive_input = (
            self.create_solver_threshold_input(
                "1000100.0",
                objective_value_validator,
            )
        )
        self.throughput_incentive_input.setFixedWidth(120)
        self.throughput_incentive_input.setToolTip(
            "Reward applied to every processed tonne. The high default "
            "preserves the existing throughput-first optimisation behavior; "
            "lower values allow source costs and other incentives to trade "
            "against throughput."
        )
        throughput_incentive_layout.addWidget(
            QLabel("Throughput Incentive:")
        )
        throughput_incentive_layout.addWidget(
            self.throughput_incentive_input
        )
        throughput_incentive_layout.addWidget(QLabel("$/t"))
        throughput_incentive_layout.addStretch()
        self.solver_config_layout.addLayout(
            throughput_incentive_layout
        )

        source_selection_penalty_layout = QHBoxLayout()
        self.source_selection_tie_break_penalty_input = (
            self.create_solver_threshold_input(
                "0.001",
                threshold_validator,
            )
        )
        self.source_selection_tie_break_penalty_input.setToolTip(
            "Small penalty per selected source while generating alternative "
            "blend options. It makes otherwise equal solutions deterministic."
        )
        source_selection_penalty_layout.addWidget(
            QLabel("Source Selection Tie-break Penalty:")
        )
        source_selection_penalty_layout.addWidget(
            self.source_selection_tie_break_penalty_input
        )
        source_selection_penalty_layout.addStretch()
        self.solver_config_layout.addLayout(
            source_selection_penalty_layout
        )

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
        blend_option_timeout_layout.addStretch()
        self.solver_config_layout.addLayout(blend_option_timeout_layout)

        self.allow_offspec_steady_states_checkbox = QCheckBox(
            "Off-spec steady states are allowed if ultimate build is on spec"
        )
        self.solver_config_layout.addWidget(self.allow_offspec_steady_states_checkbox)

        brand_guidance_layout = QHBoxLayout()
        brand_guidance_layout.addWidget(QLabel("2WP Product Guidance Reward/Penalty:"))
        self.brand_guidance_incentive_input = self.create_solver_threshold_input(
            "0.0", signed_incentive_validator
        )
        self.brand_guidance_incentive_input.setToolTip(
            "Positive values reward compliance; negative values penalise non-compliance."
        )
        brand_guidance_layout.addWidget(self.brand_guidance_incentive_input)
        brand_guidance_layout.addWidget(QLabel("$/t"))
        brand_guidance_layout.addStretch()
        self.solver_config_layout.addLayout(brand_guidance_layout)

        timing_guidance_layout = QHBoxLayout()
        timing_guidance_layout.addWidget(
            QLabel("2WP Source Timing Reward/Penalty:")
        )
        self.timing_guidance_incentive_input = self.create_solver_threshold_input(
            "0.0", signed_incentive_validator
        )
        self.timing_guidance_incentive_input.setToolTip(
            "Positive values reward compliance; negative values penalise non-compliance."
        )
        timing_guidance_layout.addWidget(self.timing_guidance_incentive_input)
        timing_guidance_layout.addWidget(QLabel("$/t"))
        timing_guidance_layout.addWidget(QLabel("Tolerance:"))
        self.timing_guidance_tolerance_input = self.create_solver_threshold_input(
            "0.0", threshold_validator
        )
        timing_guidance_layout.addWidget(self.timing_guidance_tolerance_input)
        timing_guidance_layout.addWidget(QLabel("hrs"))
        timing_guidance_layout.addStretch()
        self.solver_config_layout.addLayout(timing_guidance_layout)

        active_blend_guidance_layout = QHBoxLayout()
        active_blend_guidance_layout.addWidget(
            QLabel("2WP Active Blend Reward/Penalty:")
        )
        self.active_blend_guidance_incentive_input = (
            self.create_solver_threshold_input(
                "0.0", signed_incentive_validator
            )
        )
        self.active_blend_guidance_incentive_input.setToolTip(
            "Positive values reward an exact stockpile-set match; negative values penalise a mismatch."
        )
        active_blend_guidance_layout.addWidget(
            self.active_blend_guidance_incentive_input
        )
        active_blend_guidance_layout.addWidget(QLabel("$/t"))
        active_blend_guidance_layout.addStretch()
        self.solver_config_layout.addLayout(active_blend_guidance_layout)

        haulage_cost_layout = QHBoxLayout()
        haulage_cost_layout.addWidget(QLabel("Haulage Cost:"))
        self.haulage_cost_per_hour_input = (
            self.create_solver_threshold_input(
                "0.0",
                hourly_cost_validator,
            )
        )
        self.haulage_cost_per_hour_input.setToolTip(
            "Applied as $/t = $/hr × cycle minutes ÷ 6000, using a "
            "nominal 100 t payload."
        )
        haulage_cost_layout.addWidget(self.haulage_cost_per_hour_input)
        haulage_cost_layout.addWidget(QLabel("$/hr"))
        haulage_cost_layout.addStretch()
        self.solver_config_layout.addLayout(haulage_cost_layout)

        direct_tip_layout = QHBoxLayout()
        self.direct_tip_cash_incentive_input = self.create_solver_threshold_input("10.0", threshold_validator)
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
        grade_block_pair_duration_label = QLabel(
            "Min Grade Block Pair Duration:"
        )
        grade_block_pair_duration_label.setToolTip(
            "Only applies to longer steady-state durations."
        )
        grade_block_pair_duration_layout.addWidget(
            grade_block_pair_duration_label
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

        fewer_stockpiles_incentive_layout = QHBoxLayout()
        self.fewer_stockpiles_incentive_input = (
            self.create_solver_threshold_input(
                "10.0",
                threshold_validator,
            )
        )
        self.fewer_stockpiles_incentive_input.setToolTip(
            "Objective penalty applied for each selected stockpile when "
            "the Decision Lever is enabled."
        )
        fewer_stockpiles_incentive_layout.addWidget(
            QLabel("Fewer Stockpiles Incentive:")
        )
        fewer_stockpiles_incentive_layout.addWidget(
            self.fewer_stockpiles_incentive_input
        )
        fewer_stockpiles_incentive_layout.addStretch()
        self.solver_config_layout.addLayout(
            fewer_stockpiles_incentive_layout
        )

        balance_incentive_layout = QHBoxLayout()
        self.balance_preference_incentive_input = (
            self.create_solver_threshold_input(
                "1.0",
                threshold_validator,
            )
        )
        balance_incentive_layout.addWidget(
            QLabel("Balance Preference Incentive:")
        )
        balance_incentive_layout.addWidget(
            self.balance_preference_incentive_input
        )
        balance_incentive_layout.addWidget(QLabel("$/t"))
        balance_incentive_layout.addStretch()
        self.solver_config_layout.addLayout(balance_incentive_layout)

        amt_incentive_layout = QHBoxLayout()
        self.amt_preference_incentive_input = (
            self.create_solver_threshold_input(
                "1.0",
                threshold_validator,
            )
        )
        amt_incentive_layout.addWidget(QLabel("AMT Preference Incentive:"))
        amt_incentive_layout.addWidget(
            self.amt_preference_incentive_input
        )
        amt_incentive_layout.addWidget(QLabel("$/t"))
        amt_incentive_layout.addStretch()
        self.solver_config_layout.addLayout(amt_incentive_layout)

        contaminated_incentive_layout = QHBoxLayout()
        self.contaminated_preference_incentive_input = (
            self.create_solver_threshold_input(
                "1.0",
                threshold_validator,
            )
        )
        contaminated_incentive_layout.addWidget(
            QLabel("Contaminated Stockpile Incentive:")
        )
        contaminated_incentive_layout.addWidget(
            self.contaminated_preference_incentive_input
        )
        contaminated_incentive_layout.addWidget(QLabel("$/t"))
        contaminated_incentive_layout.addStretch()
        self.solver_config_layout.addLayout(
            contaminated_incentive_layout
        )

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
        self.low_fe_preference_incentive_input = (
            self.create_solver_threshold_input(
                "1.0",
                threshold_validator,
            )
        )
        self.low_fe_threshold_input = self.create_solver_threshold_input("58.0", threshold_validator)
        low_fe_layout.addWidget(QLabel("Low Grade Stockpile Incentive:"))
        low_fe_layout.addWidget(
            self.low_fe_preference_incentive_input
        )
        low_fe_layout.addWidget(QLabel("$/t"))
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

    def setup_decision_levers_tab(self):
        self.decision_levers_tab = QWidget()
        self.decision_levers_tab.setObjectName("decisionLeversTab")
        self.decision_levers_tab_index = self.register_page(
            "decision_levers",
            self.auto_blend_tabs,
            self.decision_levers_tab,
            "Decision Levers",
        )
        root_layout = QVBoxLayout(self.decision_levers_tab)
        root_layout.setContentsMargins(0, 0, 0, 0)
        decision_scroll = QScrollArea()
        decision_scroll.setWidgetResizable(True)
        decision_scroll.setFrameShape(QFrame.NoFrame)
        decision_content = QWidget()
        layout = QVBoxLayout(decision_content)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        decision_scroll.setWidget(decision_content)
        root_layout.addWidget(decision_scroll)

        title = QLabel("Decision Levers")
        title.setStyleSheet("font-weight: 750; font-size: 20px; color: #172033;")
        layout.addWidget(title)

        description = QLabel(
            "Choose the operational constraints and preferences that influence "
            "blend selection. Detailed thresholds and incentive values remain "
            "under Setup > Solver Configuration."
        )
        description.setWordWrap(True)
        description.setStyleSheet("font-size: 12px; color: #64748b;")
        layout.addWidget(description)

        blend_section = QLabel("Blend Composition")
        blend_section.setStyleSheet(
            "font-weight: bold; margin-top: 10px; color: #334155;"
        )
        layout.addWidget(blend_section)

        stockpile_count_validator = QIntValidator(1, 1000, self)
        stockpile_limit_layout = QHBoxLayout()
        self.min_stockpiles_input = QLineEdit()
        self.min_stockpiles_input.setPlaceholderText("Optional")
        self.min_stockpiles_input.setValidator(stockpile_count_validator)
        self.min_stockpiles_input.setFixedWidth(90)
        self.max_stockpiles_input = QLineEdit()
        self.max_stockpiles_input.setPlaceholderText("Optional")
        self.max_stockpiles_input.setValidator(stockpile_count_validator)
        self.max_stockpiles_input.setFixedWidth(90)
        stockpile_limit_layout.addWidget(QLabel("Min Stockpiles:"))
        stockpile_limit_layout.addWidget(self.min_stockpiles_input)
        stockpile_limit_layout.addWidget(QLabel("Max Stockpiles:"))
        stockpile_limit_layout.addWidget(self.max_stockpiles_input)
        stockpile_limit_layout.addStretch()
        layout.addLayout(stockpile_limit_layout)

        blend_options_layout = QHBoxLayout()
        self.max_blend_options_input = QLineEdit("12")
        self.max_blend_options_input.setValidator(
            QIntValidator(1, 1000, self)
        )
        self.max_blend_options_input.setFixedWidth(90)
        blend_options_layout.addWidget(
            QLabel("Max Blend Options per Steady State:")
        )
        blend_options_layout.addWidget(self.max_blend_options_input)
        blend_options_layout.addStretch()
        layout.addLayout(blend_options_layout)

        feasibility_layout = QHBoxLayout()
        feasibility_layout.addWidget(QLabel("Stockpile Blend Feasibility:"))
        self.stockpile_feasibility_combo = QComboBox()
        self.stockpile_feasibility_combo.addItems([
            "Stockpile blend must be feasible",
            "Stockpile blend can rely on grade blocks",
        ])
        self.stockpile_feasibility_combo.setFixedWidth(290)
        feasibility_layout.addWidget(self.stockpile_feasibility_combo)
        feasibility_layout.addStretch()
        layout.addLayout(feasibility_layout)

        guidance_section = QLabel("Planning Guidance")
        guidance_section.setStyleSheet(
            "font-weight: bold; margin-top: 10px; color: #334155;"
        )
        layout.addWidget(guidance_section)

        self.brand_guidance_enabled_checkbox = QCheckBox(
            "Use 2WP Product Guidance"
        )
        self.timing_guidance_enabled_checkbox = QCheckBox(
            "Use 2WP Source Stockpile Timing Compliance"
        )
        self.active_blend_guidance_enabled_checkbox = QCheckBox(
            "Use 2WP Active Blend Guidance"
        )
        self.rehandle_cycle_time_penalty_checkbox = QCheckBox(
            "Use Rehandle Cycle Time Penalty"
        )
        self.rehandle_cycle_time_penalty_checkbox.toggled.connect(
            self.update_rehandle_cycle_time_input_state
        )
        layout.addWidget(self.brand_guidance_enabled_checkbox)
        layout.addWidget(self.timing_guidance_enabled_checkbox)
        layout.addWidget(self.active_blend_guidance_enabled_checkbox)
        layout.addWidget(self.rehandle_cycle_time_penalty_checkbox)

        direct_tip_section = QLabel("Direct Tip")
        direct_tip_section.setStyleSheet(
            "font-weight: bold; margin-top: 10px; color: #334155;"
        )
        layout.addWidget(direct_tip_section)
        self.direct_tip_enabled_checkbox = QCheckBox("Enable Direct Tip")
        self.direct_tip_enabled_checkbox.setChecked(True)
        self.direct_tip_enabled_checkbox.toggled.connect(
            self.update_direct_tip_input_state
        )
        layout.addWidget(self.direct_tip_enabled_checkbox)

        preference_section = QLabel("Source Preferences")
        preference_section.setStyleSheet(
            "font-weight: bold; margin-top: 10px; color: #334155;"
        )
        layout.addWidget(preference_section)
        self.prefer_fewer_stockpiles_checkbox = QCheckBox(
            "Prefer using fewer stockpiles"
        )
        layout.addWidget(self.prefer_fewer_stockpiles_checkbox)

        balance_layout = QHBoxLayout()
        balance_layout.addWidget(QLabel("Balance Preference:"))
        self.balance_preference_combo = QComboBox()
        self.balance_preference_combo.addItems([
            "No balance preference",
            "Lower balance first",
            "Higher balance first",
        ])
        self.balance_preference_combo.setFixedWidth(200)
        balance_layout.addWidget(self.balance_preference_combo)
        balance_layout.addStretch()
        layout.addLayout(balance_layout)

        self.prefer_amt_stockpiles_checkbox = QCheckBox(
            "Prefer AMT stockpiles before weighted average inventory stockpiles"
        )
        self.prefer_contaminated_stockpiles_checkbox = QCheckBox(
            "Try blending contaminated stockpiles/chunks first"
        )
        self.prefer_low_fe_stockpiles_checkbox = QCheckBox(
            "Try blending low grade stockpiles/chunks first"
        )
        layout.addWidget(self.prefer_amt_stockpiles_checkbox)
        layout.addWidget(self.prefer_contaminated_stockpiles_checkbox)
        layout.addWidget(self.prefer_low_fe_stockpiles_checkbox)
        self.update_rehandle_cycle_time_input_state()
        self.update_direct_tip_input_state()

        decision_submit_layout = QHBoxLayout()
        self.decision_levers_submit_button = QPushButton("Submit")
        self.decision_levers_submit_button.clicked.connect(
            self.handle_decision_levers_submit
        )
        decision_submit_layout.addWidget(
            self.decision_levers_submit_button
        )
        decision_submit_layout.addStretch()
        layout.addLayout(decision_submit_layout)
        layout.addStretch()

    def setup_product_build_settings_tab(self):
        self.product_build_tab = QWidget()
        self.product_build_tab_index = self.register_page(
            "product_build_settings",
            self.workspace_tabs,
            self.product_build_tab,
            "Product Build Settings",
            position=1,
        )
        self.product_build_layout = QVBoxLayout(self.product_build_tab)
        self.product_build_layout.setContentsMargins(14, 12, 14, 12)
        self.product_build_layout.setSpacing(8)

        self.product_build_tab.setStyleSheet("""
            QWidget {
                background-color: #f8fafc;
            }
            QTableWidget {
                background-color: #ffffff;
                border: 1px solid #d8e0ea;
                border-radius: 6px;
                gridline-color: #e5e7eb;
                selection-background-color: #dbeafe;
                selection-color: #0f172a;
                alternate-background-color: #f8fbff;
            }
            QHeaderView::section {
                background-color: #f1f5f9;
                color: #0f172a;
                font-weight: 700;
                border: 0;
                border-right: 1px solid #dbe4ee;
                border-bottom: 1px solid #dbe4ee;
                padding: 7px 8px;
            }
        """)

        title_label = QLabel("Product Build Settings")
        title_label.setStyleSheet("font-weight: 750; font-size: 20px; color: #172033;")
        self.product_build_layout.addWidget(title_label)

        subtitle_label = QLabel(
            "Define post-crusher product builds. Crusher output is accumulated into each build until its target tonnes are reached."
        )
        subtitle_label.setStyleSheet("font-size: 12px; color: #64748b; padding-bottom: 4px;")
        subtitle_label.setWordWrap(True)
        self.product_build_layout.addWidget(subtitle_label)

        top_layout = QHBoxLayout()
        self.product_build_count_input = QLineEdit()
        self.product_build_count_input.setFixedWidth(80)
        self.product_build_count_input.setText(str(len(getattr(self, "product_build_settings", []) or [])))
        self.product_build_count_input.setValidator(QIntValidator(0, 50, self))
        self.product_build_count_button = QPushButton("Create")
        self.product_build_count_button.clicked.connect(self.set_product_build_count_from_input)
        self.product_build_2wp_button = QPushButton("Load 2WP Targets")
        self.product_build_2wp_button.setToolTip(
            "Load tonnes and grade targets from the latest Wednesday 2WP scenario for the active mine and crusher."
        )
        self.product_build_2wp_button.clicked.connect(self.load_2wp_product_build_targets)
        top_layout.addWidget(QLabel("Number of builds"))
        top_layout.addWidget(self.product_build_count_input)
        top_layout.addWidget(self.product_build_count_button)
        top_layout.addWidget(self.product_build_2wp_button)
        top_layout.addStretch()
        self.product_build_layout.addLayout(top_layout)

        self.product_build_table = CustomTableWidget()
        self.product_build_table.setAlternatingRowColors(True)
        self.product_build_table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.product_build_headers = [
            "Build",
            "Brand",
            "Target Tonnes",
            "Fe Min",
            "Fe Max",
            "Si Min",
            "Si Max",
            "Al Min",
            "Al Max",
            "P Min",
            "P Max",
            "Mn Min",
            "Mn Max",
        ]
        self.product_build_table.setColumnCount(len(self.product_build_headers))
        self.product_build_table.setHorizontalHeaderLabels(self.product_build_headers)
        self.product_build_table.verticalHeader().setVisible(False)
        self.product_build_layout.addWidget(self.product_build_table)

        button_layout = QHBoxLayout()
        self.product_build_submit_button = QPushButton("Submit")
        self.product_build_submit_button.clicked.connect(self.handle_product_build_settings_submit)
        self.product_build_delete_button = QPushButton("Delete Selected Build(s)")
        self.product_build_delete_button.clicked.connect(self.delete_selected_product_build_rows)
        button_layout.addWidget(self.product_build_submit_button)
        button_layout.addWidget(self.product_build_delete_button)
        button_layout.addStretch()
        self.product_build_layout.addLayout(button_layout)

        self.populate_product_build_table()

    def default_product_brand_labels(self):
        return ["FB", "SS", "SF", "FF", "KF"]

    def parse_product_brand_labels(self, value):
        if isinstance(value, (list, tuple, set)):
            raw_labels = value
        else:
            raw_labels = str(value or "").replace(";", ",").split(",")
        labels = []
        for label in raw_labels:
            cleaned = str(label or "").strip().upper()
            if cleaned and cleaned not in labels:
                labels.append(cleaned)
        return labels or self.default_product_brand_labels()

    def product_brand_options(self):
        return self.parse_product_brand_labels(
            getattr(self, "product_brand_labels_choice", self.default_product_brand_labels())
        )

    def product_build_name_for_row(self, row_idx, brand):
        brand = str(brand or "").strip().upper()
        if not brand:
            return f"Build {row_idx + 1}"

        brand_count = 0
        for index in range(row_idx + 1):
            if index == row_idx:
                row_brand = brand
            else:
                brand_widget = self.product_build_table.cellWidget(index, 1)
                row_brand = (
                    brand_widget.currentText().strip().upper()
                    if isinstance(brand_widget, QComboBox)
                    else ""
                )
            if row_brand == brand:
                brand_count += 1
        return f"{brand} Build {brand_count}"

    def set_product_build_count_from_input(self):
        try:
            count = int(self.product_build_count_input.text().strip() or 0)
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Product build count must be a whole number.")
            return
        self.set_product_build_table_row_count(count)

    def set_product_build_table_row_count(self, count):
        count = max(int(count or 0), 0)
        existing = self.read_product_build_settings_from_table(show_errors=False) or []
        self.product_build_table.setRowCount(count)
        for row_idx in range(count):
            source = existing[row_idx] if row_idx < len(existing) else {}
            self.populate_product_build_table_row(row_idx, source)
        self.renumber_product_build_rows()
        self.resize_product_build_table()

    def populate_product_build_table(self):
        settings = getattr(self, "product_build_settings", []) or []
        if hasattr(self, "product_build_count_input"):
            self.product_build_count_input.setText(str(len(settings)))
        self.product_build_table.setRowCount(len(settings))
        for row_idx, setting in enumerate(settings):
            self.populate_product_build_table_row(row_idx, setting)
        self.renumber_product_build_rows()
        self.resize_product_build_table()

    def populate_product_build_table_row(self, row_idx, setting=None):
        setting = setting or {}
        brand_combo = QComboBox()
        brand_combo.addItems(self.product_brand_options())
        selected_brand = str(setting.get("brand") or "").strip().upper()
        if selected_brand and brand_combo.findText(selected_brand) == -1:
            brand_combo.addItem(selected_brand)
        if selected_brand:
            brand_combo.setCurrentText(selected_brand)
        self.product_build_table.setCellWidget(row_idx, 1, brand_combo)

        brand = brand_combo.currentText().strip().upper()
        build_name = self.product_build_name_for_row(row_idx, brand)
        build_item = QTableWidgetItem(build_name)
        build_item.setFlags(Qt.ItemIsEnabled)
        build_item.setTextAlignment(Qt.AlignCenter)
        self.product_build_table.setItem(row_idx, 0, build_item)
        brand_combo.currentTextChanged.connect(
            lambda _text: self.renumber_product_build_rows()
        )

        defaults = {
            "target_tonnes": 0,
            "target_fe_min": 0,
            "target_fe_max": 100,
            "target_si_min": 0,
            "target_si_max": 100,
            "target_al_min": 0,
            "target_al_max": 100,
            "target_p_min": 0,
            "target_p_max": 100,
            "target_mn_min": 0,
            "target_mn_max": 100,
        }
        keys = [
            "target_tonnes",
            "target_fe_min",
            "target_fe_max",
            "target_si_min",
            "target_si_max",
            "target_al_min",
            "target_al_max",
            "target_p_min",
            "target_p_max",
            "target_mn_min",
            "target_mn_max",
        ]
        for col_idx, key in enumerate(keys, start=2):
            value = setting.get(key, defaults[key])
            if key == "target_tonnes":
                try:
                    value = math.floor(float(value))
                except (TypeError, ValueError, OverflowError):
                    value = 0
            item = QTableWidgetItem("" if value is None else str(value))
            item.setTextAlignment(Qt.AlignCenter)
            self.product_build_table.setItem(row_idx, col_idx, item)

    def resize_product_build_table(self):
        if not hasattr(self, "product_build_table"):
            return
        for col_idx in range(self.product_build_table.columnCount()):
            self.product_build_table.horizontalHeader().setSectionResizeMode(col_idx, QHeaderView.ResizeToContents)
        self.product_build_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)

    def read_product_build_settings_from_table(self, show_errors=True):
        if not hasattr(self, "product_build_table"):
            return []

        settings = []
        grade_keys = {
            "Fe": ("target_fe_min", "target_fe_max"),
            "Si": ("target_si_min", "target_si_max"),
            "Al": ("target_al_min", "target_al_max"),
            "P": ("target_p_min", "target_p_max"),
            "Mn": ("target_mn_min", "target_mn_max"),
        }

        def parse_number(row, col, label):
            item = self.product_build_table.item(row, col)
            text = item.text().strip() if item else ""
            try:
                return float(text or 0)
            except ValueError:
                if show_errors:
                    QMessageBox.warning(self, "Invalid Input", f"{label} in row {row + 1} must be a number.")
                return None

        brand_counts = {}
        for row_idx in range(self.product_build_table.rowCount()):
            brand_widget = self.product_build_table.cellWidget(row_idx, 1)
            brand = brand_widget.currentText().strip().upper() if isinstance(brand_widget, QComboBox) else ""
            if brand:
                brand_counts[brand] = brand_counts.get(brand, 0) + 1
                build_name = f"{brand} Build {brand_counts[brand]}"
            else:
                build_name = f"Build {row_idx + 1}"
            target_tonnes = parse_number(row_idx, 2, "Target Tonnes")
            if target_tonnes is None:
                return None
            if target_tonnes < 0:
                if show_errors:
                    QMessageBox.warning(self, "Invalid Input", f"Target Tonnes in row {row_idx + 1} cannot be negative.")
                return None
            target_tonnes = math.floor(target_tonnes)

            setting = {
                "build_id": row_idx + 1,
                "build_name": build_name,
                "brand": brand,
                "target_tonnes": target_tonnes,
            }
            for grade_label, (min_key, max_key) in grade_keys.items():
                min_col = self.product_build_headers.index(f"{grade_label} Min")
                max_col = self.product_build_headers.index(f"{grade_label} Max")
                min_value = parse_number(row_idx, min_col, f"{grade_label} Min")
                max_value = parse_number(row_idx, max_col, f"{grade_label} Max")
                if min_value is None or max_value is None:
                    return None
                if min_value > max_value:
                    if show_errors:
                        QMessageBox.warning(
                            self,
                            "Invalid Input",
                            f"{grade_label} Min cannot be greater than {grade_label} Max in row {row_idx + 1}.",
                        )
                    return None
                setting[min_key] = min_value
                setting[max_key] = max_value
            settings.append(setting)
        return settings

    def update_product_build_name_for_row(self, row_idx, brand):
        if not hasattr(self, "product_build_table") or row_idx >= self.product_build_table.rowCount():
            return
        brand = str(brand or "").strip().upper()
        build_name = self.product_build_name_for_row(row_idx, brand)
        item = self.product_build_table.item(row_idx, 0)
        if item is None:
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemIsEnabled)
            item.setTextAlignment(Qt.AlignCenter)
            self.product_build_table.setItem(row_idx, 0, item)
        item.setText(build_name)

    def delete_selected_product_build_rows(self):
        if not hasattr(self, "product_build_table"):
            return

        selected_rows = sorted(
            {index.row() for index in self.product_build_table.selectedIndexes()},
            reverse=True,
        )
        if not selected_rows:
            selected_rows = [self.product_build_table.currentRow()]
        selected_rows = [row for row in selected_rows if row >= 0]
        if not selected_rows:
            return

        for row in selected_rows:
            self.product_build_table.removeRow(row)

        self.renumber_product_build_rows()
        if hasattr(self, "product_build_count_input"):
            self.product_build_count_input.setText(str(self.product_build_table.rowCount()))
        self.store_product_build_settings(show_errors=False)
        self.resize_product_build_table()

    def renumber_product_build_rows(self):
        if not hasattr(self, "product_build_table"):
            return
        for row_idx in range(self.product_build_table.rowCount()):
            brand_widget = self.product_build_table.cellWidget(row_idx, 1)
            brand = brand_widget.currentText() if isinstance(brand_widget, QComboBox) else ""
            self.update_product_build_name_for_row(row_idx, brand)

    def store_product_build_settings(self, show_errors=True):
        settings = self.read_product_build_settings_from_table(show_errors=show_errors)
        if settings is None:
            return False
        previous_settings = getattr(self, "product_build_settings", []) or []
        for index, setting in enumerate(settings):
            if index >= len(previous_settings) or not isinstance(previous_settings[index], dict):
                continue
            for key, value in previous_settings[index].items():
                if key.startswith("planning_"):
                    setting[key] = copy.deepcopy(value)
        self.product_build_settings = settings
        if self.calendar_inputs is None:
            self.calendar_inputs = {}
        self.calendar_inputs["product_build_settings"] = copy.deepcopy(self.product_build_settings)
        self.calendar_inputs["product_brand_labels"] = copy.deepcopy(self.product_brand_options())
        return True

    def navigate_to_product_build_settings(self):
        self.populate_product_build_table()
        self.set_page_enabled(self.product_build_tab_index, True)
        self.set_page_enabled(self.decision_levers_tab_index, True)
        self.show_page(self.product_build_tab_index)

    def handle_product_build_settings_submit(self):
        if not self.store_product_build_settings():
            return
        self.save_active_scenario_state()
        self.navigate_to_decision_levers()

    def navigate_to_decision_levers(self):
        self.load_solver_config_inputs()
        self.set_page_enabled(self.decision_levers_tab_index, True)
        self.show_page(self.decision_levers_tab_index)

    def handle_decision_levers_submit(self):
        if not self.store_solver_config_inputs():
            return
        self.save_active_scenario_state()
        self.setup_calendar()
        self.set_page_enabled(self.calendar_tab_index, True)
        self.show_page(self.calendar_tab_index)

    def load_2wp_product_build_targets(self):
        mine = getattr(self, "mine_input_choice", None)
        opf = getattr(self, "opf_input_choice", None)
        crusher = getattr(self, "crusher_input_choice", None)
        start_time = getattr(self, "start_time_choice", None)
        if not mine or not crusher or not start_time:
            QMessageBox.information(
                self,
                "BlendMaster",
                "Submit Site Configuration before loading 2WP product-build targets.",
            )
            return

        def work():
            return self.planning_plan_targets.fetch(
                mine,
                crusher,
                start_time,
                self.product_brand_options(),
                opf=opf,
                crusher_contribution_ratio=getattr(
                    self, "crusher_contribution_ratio_choice", 1.0
                ),
            )

        def success(settings):
            if not settings:
                QMessageBox.information(
                    self,
                    "BlendMaster",
                f"No overlapping 2WP OPF Feed targets were found for {mine} / {opf} / {crusher}.",
                )
                return
            extra_brands = [setting.get("brand") for setting in settings if setting.get("brand")]
            self.product_brand_labels_choice = self.parse_product_brand_labels(
                self.product_brand_options() + extra_brands
            )
            self.product_brand_labels_input.setText(", ".join(self.product_brand_labels_choice))
            self.product_build_settings = settings
            self.populate_product_build_table()
            self.store_product_build_settings(show_errors=False)
            self.save_active_scenario_state()
            QMessageBox.information(
                self,
                "BlendMaster",
                f"Loaded {len(settings)} product build target(s) for {mine} / {opf} / {crusher}.",
            )

        self.run_background_task(
            f"Loading 2WP build targets for {mine} / {opf} / {crusher}...",
            work,
            success,
        )

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

    def update_rehandle_cycle_time_input_state(self, checked=None):
        if not hasattr(self, "haulage_cost_per_hour_input"):
            return
        enabled = bool(
            hasattr(self, "rehandle_cycle_time_penalty_checkbox")
            and self.rehandle_cycle_time_penalty_checkbox.isChecked()
        )
        self.haulage_cost_per_hour_input.setEnabled(enabled)

    def is_direct_tip_enabled(self):
        solver_config = self.normalized_solver_config()
        return bool(solver_config.get("direct_tip_enabled", True))

    def normalized_solver_config(self, solver_config=None):
        defaults = {
            "throughput_incentive_per_tonne": 1_000_100.0,
            "source_selection_tie_break_penalty": 0.001,
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
            "fewer_stockpiles_incentive": 10.0,
            "balance_preference": "none",
            "balance_preference_incentive": 1.0,
            "prefer_amt_stockpiles": False,
            "amt_preference_incentive": 1.0,
            "prefer_contaminated_stockpiles": False,
            "contaminated_preference_incentive": 1.0,
            "contaminant_thresholds": {
                "si": 5.0,
                "al": 3.0,
                "p": 0.1,
                "mn": 0.1,
            },
            "prefer_low_fe_stockpiles": False,
            "low_fe_preference_incentive": 1.0,
            "low_fe_threshold": 58.0,
            "allow_offspec_steady_states_for_product_build": False,
            "brand_guidance_mode": "ignore",
            "brand_guidance_enabled": False,
            "brand_guidance_incentive": 0.0,
            "timing_guidance_enabled": False,
            "timing_guidance_incentive": 0.0,
            "timing_guidance_tolerance_hours": 0.0,
            "active_blend_guidance_enabled": False,
            "active_blend_guidance_incentive": 0.0,
            "rehandle_cycle_time_penalty_enabled": False,
            "haulage_cost_per_hour": 0.0,
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
        if "brand_guidance_enabled" not in incoming:
            merged["brand_guidance_enabled"] = (
                merged.get("brand_guidance_mode", "ignore") != "ignore"
            )
        return merged

    def apply_app_theme(self):
        self.setStyleSheet(f"""
            QMainWindow,
            QWidget#mainCentralWidget {{
                background-color: #f8fafc;
                color: #0f172a;
            }}
            QWidget {{
                color: #0f172a;
            }}
            QLabel,
            QCheckBox,
            QRadioButton,
            QGroupBox {{
                color: #0f172a;
                background: transparent;
            }}
            QFrame {{
                color: #0f172a;
            }}
            QTabWidget::pane {{
                background-color: #ffffff;
                border: 1px solid #d8e0ea;
            }}
            QTabBar {{
                background-color: #f8fafc;
            }}
            QLineEdit,
            QTextEdit,
            QPlainTextEdit,
            QComboBox,
            QDateTimeEdit,
            QSpinBox,
            QDoubleSpinBox,
            QListWidget {{
                background-color: #ffffff;
                color: #0f172a;
                border: 1px solid #d8e0ea;
                border-radius: 4px;
                selection-background-color: #bfdbfe;
                selection-color: #0f172a;
            }}
            QLineEdit:disabled,
            QTextEdit:disabled,
            QPlainTextEdit:disabled,
            QComboBox:disabled,
            QDateTimeEdit:disabled {{
                background-color: #e5e7eb;
                color: #64748b;
            }}
            QPushButton {{
                background-color: #ffffff;
                color: #0f172a;
                border: 1px solid #d8e0ea;
                border-radius: 4px;
                padding: 5px 12px;
            }}
            QPushButton:hover {{
                background-color: #eef7f0;
                color: #0f172a;
                border-color: #98d4a6;
            }}
            QTableWidget {{
                background-color: #ffffff;
                color: #0f172a;
                gridline-color: #e5e7eb;
                selection-background-color: #bfdbfe;
                selection-color: #0f172a;
                alternate-background-color: #ffffff;
            }}
            QHeaderView::section {{
                background-color: #f1f5f9;
                color: #475569;
                border: 0;
                border-right: 1px solid #d8e0ea;
                border-bottom: 1px solid #d8e0ea;
                padding: 6px 8px;
                font-weight: 700;
            }}
            QScrollArea,
            QScrollBar {{
                background-color: #f8fafc;
            }}
            QToolTip {{
                background-color: #111827;
                color: #f8fafc;
                border: 1px solid #334155;
            }}
            QStatusBar {{
                color: #475569;
            }}
        """)

    def style_green_action_button(self, button, minimum_width=200):
        button.setMinimumWidth(minimum_width)
        button.setMaximumWidth(max(minimum_width + 80, minimum_width))
        button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        button.setStyleSheet("""
            QPushButton {
                background-color: #0f766e;
                color: white;
                border: 1px solid #0f766e;
                border-radius: 4px;
                font-size: 14px;
                font-weight: 650;
                padding: 8px 14px;
            }
            QPushButton:hover {
                background-color: #115e59;
                border-color: #115e59;
            }
            QPushButton:pressed {
                background-color: #134e4a;
                border-color: #134e4a;
            }
        """)

    def setup_site_configuration(self):
        """Setup for the Site Configuration Form."""
        self.site_config_tab = QWidget()
        self.site_config_tab_index = self.register_page(
            "site_configuration",
            self.setup_tabs,
            self.site_config_tab,
            "Site Configuration",
        )
        self.site_config_tab.setObjectName("siteConfigTab")  # Set an object name for the stylesheet

        # Construct path to the background image
        background_path = preferred_resource_path("background_v2.PNG", "background.PNG").replace("\\", "/")


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
        outer_layout.setSpacing(20)

        form_card = QFrame()
        form_card.setObjectName("siteConfigCard")
        form_card.setMinimumWidth(860)
        form_card.setMaximumWidth(980)
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
        self.site_config_logo.setMinimumSize(280, 260)
        self.site_config_logo.setMaximumWidth(760)
        self.site_config_logo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        logo_layout.addWidget(self.site_config_logo)

        outer_layout.addWidget(form_card, 0, Qt.AlignTop)
        outer_layout.addWidget(logo_panel, 1)

        self.guidance_schedules_tab = QWidget()
        self.guidance_schedules_tab.setObjectName("guidanceSchedulesTab")
        self.guidance_schedules_tab_index = self.register_page(
            "guidance_schedules",
            self.setup_tabs,
            self.guidance_schedules_tab,
            "Guidance Schedules",
        )
        self.guidance_schedules_tab.setStyleSheet("""
            QWidget#guidanceSchedulesTab {
                background-color: #f8fafc;
            }
            QWidget#guidanceSchedulesContent {
                background-color: #f8fafc;
            }
            QFrame#guidanceSchedulesCard {
                background-color: #ffffff;
                border: 1px solid #d9e2ec;
                border-radius: 8px;
            }
            QLabel#guidanceSchedulesTitle {
                color: #1f2933;
                font-size: 22px;
                font-weight: 700;
                padding-bottom: 2px;
            }
            QLabel#guidanceSchedulesSubtitle {
                color: #607080;
                font-size: 12px;
                padding-bottom: 14px;
            }
            QWidget#guidanceSchedulesTab QLineEdit,
            QWidget#guidanceSchedulesTab QComboBox,
            QWidget#guidanceSchedulesTab QListWidget {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                padding: 4px 6px;
                min-height: 20px;
            }
            QWidget#guidanceSchedulesTab QLineEdit:disabled,
            QWidget#guidanceSchedulesTab QComboBox:disabled,
            QWidget#guidanceSchedulesTab QListWidget:disabled {
                color: #7b8794;
                background-color: #f5f7fa;
            }
            QWidget#guidanceSchedulesTab QPushButton {
                background-color: #ffffff;
                border: 1px solid #b8c4d2;
                border-radius: 4px;
                padding: 5px 12px;
            }
            QWidget#guidanceSchedulesTab QPushButton:hover {
                background-color: #eef7f0;
                border-color: #98d4a6;
            }
            QWidget#guidanceSchedulesTab
            QPushButton#guidanceSchedulesSubmitButton:enabled {
                color: #ffffff;
                background-color: #16803c;
                border: 1px solid #126b32;
                border-radius: 4px;
                padding: 6px 14px;
                font-weight: 700;
            }
            QWidget#guidanceSchedulesTab
            QPushButton#guidanceSchedulesSubmitButton:enabled:hover {
                background-color: #116b31;
                border-color: #0e5929;
            }
            QWidget#guidanceSchedulesTab
            QPushButton#guidanceSchedulesSubmitButton:enabled:pressed {
                background-color: #0d5728;
                border-color: #0a4821;
            }
            QWidget#guidanceSchedulesTab
            QPushButton#guidanceSchedulesSubmitButton:disabled {
                color: #7b8794;
                background-color: #e5e7eb;
                border-color: #cbd5e1;
            }
        """)

        guidance_root_layout = QVBoxLayout(self.guidance_schedules_tab)
        guidance_root_layout.setContentsMargins(12, 10, 12, 12)
        guidance_scroll = QScrollArea()
        guidance_scroll.setWidgetResizable(True)
        guidance_scroll.setFrameShape(QFrame.NoFrame)
        guidance_content = QWidget()
        guidance_content.setObjectName("guidanceSchedulesContent")
        guidance_content_layout = QHBoxLayout(guidance_content)
        guidance_content_layout.setContentsMargins(8, 8, 8, 8)
        guidance_content_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        guidance_card = QFrame()
        guidance_card.setObjectName("guidanceSchedulesCard")
        guidance_card.setMinimumWidth(760)
        guidance_card.setMaximumWidth(1080)
        guidance_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        guidance_layout = QFormLayout(guidance_card)
        guidance_layout.setContentsMargins(18, 16, 18, 18)
        guidance_layout.setHorizontalSpacing(16)
        guidance_layout.setVerticalSpacing(10)
        guidance_layout.setLabelAlignment(Qt.AlignLeft)
        guidance_layout.setFormAlignment(Qt.AlignTop)

        guidance_title = QLabel("Guidance Schedules")
        guidance_title.setObjectName("guidanceSchedulesTitle")
        guidance_subtitle = QLabel(
            "Configure expit transaction handling and the 2WP/24HR schedule guidance."
        )
        guidance_subtitle.setObjectName("guidanceSchedulesSubtitle")
        guidance_layout.addRow(guidance_title)
        guidance_layout.addRow(guidance_subtitle)

        guidance_content_layout.addWidget(guidance_card, 1, Qt.AlignTop)
        guidance_content_layout.addStretch()
        guidance_scroll.setWidget(guidance_content)
        guidance_root_layout.addWidget(guidance_scroll)

        # Site hierarchy: one Hub / Mine / OPF / operating Crusher per scenario.
        self.hub_input = QComboBox()
        self.hub_input.addItems(["Chichester Hub", "Western Hub", "Solomon Hub", "Iron Bridge Hub"])
        self.mine_input = QComboBox()
        self.opf_input = QComboBox()
        self.site_crusher_input = QComboBox()

        # Adjust size of dropdowns
        self.hub_input.setFixedWidth(180)
        self.mine_input.setFixedWidth(180)
        self.opf_input.setFixedWidth(240)
        self.site_crusher_input.setFixedWidth(240)

        # Create bold labels for Hub and Mine
        hub_label = QLabel("Hub:")
        hub_label.setStyleSheet("font-weight: bold;")
        mine_label = QLabel("Mine:")
        mine_label.setStyleSheet("font-weight: bold;")
        opf_label = QLabel("OPF:")
        opf_label.setStyleSheet("font-weight: bold;")
        crusher_label = QLabel("Operating Crusher:")
        crusher_label.setStyleSheet("font-weight: bold;")

        layout.addRow(hub_label, self.hub_input)
        layout.addRow(mine_label, self.mine_input)
        layout.addRow(opf_label, self.opf_input)
        layout.addRow(crusher_label, self.site_crusher_input)

        # Connect hub dropdown change to update mine dropdown
        self.hub_input.currentIndexChanged.connect(self.update_mine_dropdown)
        self.mine_input.currentIndexChanged.connect(self.update_opf_dropdown)
        self.opf_input.currentIndexChanged.connect(self.update_site_crusher_options)

        self.crusher_ratio_mode_input = QComboBox()
        self.crusher_ratio_mode_input.addItem("Enter manually", "manual")
        self.crusher_ratio_mode_input.addItem("Derive from 2WP Mining.csv", "aps")
        self.crusher_ratio_mode_input.setMinimumWidth(220)
        self.crusher_ratio_input = QLineEdit("100.00")
        self.crusher_ratio_input.setValidator(QDoubleValidator(0.01, 100.0, 4))
        self.crusher_ratio_input.setFixedWidth(90)
        self.aps_ratio_crusher_button = QPushButton("Get APS Feed Crushers")
        self.aps_ratio_crusher_button.setMinimumWidth(185)
        self.aps_ratio_crusher_input = QListWidget()
        self.aps_ratio_crusher_input.setSelectionMode(QAbstractItemView.MultiSelection)
        self.aps_ratio_crusher_input.setMinimumWidth(420)
        self.aps_ratio_crusher_input.setFixedHeight(60)

        crusher_ratio_layout = QVBoxLayout()
        crusher_ratio_layout.setSpacing(5)
        crusher_ratio_header = QHBoxLayout()
        crusher_ratio_header.addWidget(self.crusher_ratio_mode_input)
        crusher_ratio_header.addWidget(self.crusher_ratio_input)
        crusher_ratio_header.addWidget(QLabel("%"))
        crusher_ratio_header.addWidget(self.aps_ratio_crusher_button)
        crusher_ratio_header.addStretch()
        crusher_ratio_layout.addLayout(crusher_ratio_header)
        crusher_ratio_layout.addWidget(self.aps_ratio_crusher_input)
        crusher_ratio_label = QLabel("Crusher Contribution:")
        crusher_ratio_label.setStyleSheet("font-weight: bold;")
        layout.addRow(crusher_ratio_label, crusher_ratio_layout)

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
        start_time_label = QLabel("Set Date & Time:")
        start_time_label.setStyleSheet("font-weight: bold;")
        layout.addRow(start_time_label, self.start_time)

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

        guidance_layout.addRow(expit_label, self.expit_mode)

        # --- Input 3: Select 2WP and 24HR schedules ---
        file_label = QLabel("Select 2WP Mining.csv (optional):")
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

        guidance_layout.addRow(file_label, file_layout)

        file_24hr_label = QLabel("Select 24HR Mining.csv (optional):")
        file_24hr_label.setStyleSheet("font-weight: bold;")
        self.file_path_24hr = QLineEdit()
        self.file_path_24hr.setReadOnly(True)
        self.file_path_24hr.setFixedWidth(400)

        self.file_24hr_button = QPushButton("Browse")
        self.file_24hr_button.setFixedWidth(100)
        self.file_24hr_button.clicked.connect(self.browse_24hr_file)

        file_24hr_layout = QHBoxLayout()
        file_24hr_layout.addWidget(self.file_path_24hr)
        file_24hr_layout.addWidget(self.file_24hr_button)
        guidance_layout.addRow(file_24hr_label, file_24hr_layout)

        self.expit_agent_button = QPushButton("Get Agent Names")
        self.expit_agent_button.setMinimumWidth(150)
        self.expit_agent_button.clicked.connect(self.load_24hr_expit_agent_names)
        self.expit_agent_input = QListWidget()
        self.expit_agent_input.setSelectionMode(QAbstractItemView.MultiSelection)
        self.expit_agent_input.setMinimumWidth(420)
        self.expit_agent_input.setMaximumHeight(105)

        expit_agent_layout = QVBoxLayout()
        expit_agent_layout.setSpacing(6)
        expit_agent_header = QHBoxLayout()
        expit_agent_header.addWidget(self.expit_agent_button)
        expit_agent_header.addWidget(QLabel(
            "Select the 24HR dig circuits to import as expit payload transactions."
        ))
        expit_agent_header.addStretch()
        expit_agent_layout.addLayout(expit_agent_header)
        expit_agent_layout.addWidget(self.expit_agent_input)
        expit_agent_label = QLabel("24HR Expit Dig Circuits:")
        expit_agent_label.setStyleSheet("font-weight: bold;")
        guidance_layout.addRow(expit_agent_label, expit_agent_layout)

        self.direct_tip_movement_button = QPushButton("Load Movement Values")
        self.direct_tip_movement_button.setMinimumWidth(170)
        self.direct_tip_grade_block_source_input = QListWidget()
        self.direct_tip_grade_block_source_input.setSelectionMode(QAbstractItemView.SingleSelection)
        self.direct_tip_crusher_destination_input = QListWidget()
        self.direct_tip_crusher_destination_input.setSelectionMode(QAbstractItemView.SingleSelection)
        self.direct_tip_rule_input = QListWidget()
        self.remove_direct_tip_rule_button = QPushButton("Remove Selected Rule")
        self.remove_direct_tip_rule_button.setMinimumWidth(165)
        for widget in (
            self.direct_tip_grade_block_source_input,
            self.direct_tip_crusher_destination_input,
            self.direct_tip_rule_input,
        ):
            widget.setMinimumWidth(170)
            widget.setFixedHeight(105)

        movement_rules_layout = QVBoxLayout()
        movement_rules_layout.setSpacing(5)
        movement_header = QHBoxLayout()
        movement_header.addWidget(self.direct_tip_movement_button)
        movement_header.addWidget(QLabel(
            "Select a grade-block source, then double-click a crusher destination."
        ))
        movement_header.addStretch()
        movement_rules_layout.addLayout(movement_header)
        movement_columns = QHBoxLayout()
        for caption, widget in (
            ("Grade Block Sources", self.direct_tip_grade_block_source_input),
            ("Crusher Destinations", self.direct_tip_crusher_destination_input),
            ("Movement Rules", self.direct_tip_rule_input),
        ):
            column = QVBoxLayout()
            column.addWidget(QLabel(caption))
            column.addWidget(widget)
            movement_columns.addLayout(column)
        movement_rules_layout.addLayout(movement_columns)
        movement_footer = QHBoxLayout()
        movement_footer.addStretch()
        movement_footer.addWidget(self.remove_direct_tip_rule_button)
        movement_rules_layout.addLayout(movement_footer)
        movement_rules_label = QLabel("Direct Tip Movement Rules:")
        movement_rules_label.setStyleSheet("font-weight: bold;")
        guidance_layout.addRow(movement_rules_label, movement_rules_layout)

        self.reevaluate_aps_direct_tip_checkbox = QCheckBox("Re-evaluate 2WP direct tip tonnes")
        self.reevaluate_aps_direct_tip_checkbox.setChecked(False)
        self.aps_crusher_button = QPushButton("Get Crusher Names")
        self.aps_crusher_button.setMinimumWidth(190)
        self.aps_crusher_button.setEnabled(False)
        self.aps_crusher_button.clicked.connect(self.load_aps_crusher_names)
        self.aps_crusher_input = QListWidget()
        self.aps_crusher_input.setSelectionMode(QAbstractItemView.MultiSelection)
        self.aps_crusher_input.setMinimumWidth(420)
        self.aps_crusher_input.setMaximumHeight(78)
        self.aps_crusher_input.setEnabled(False)

        aps_direct_tip_layout = QVBoxLayout()
        aps_direct_tip_layout.setSpacing(6)
        aps_direct_tip_header_layout = QHBoxLayout()
        aps_direct_tip_header_layout.addWidget(self.reevaluate_aps_direct_tip_checkbox)
        aps_direct_tip_header_layout.addWidget(self.aps_crusher_button)
        aps_direct_tip_header_layout.addStretch()
        aps_direct_tip_layout.addLayout(aps_direct_tip_header_layout)
        aps_direct_tip_layout.addWidget(self.aps_crusher_input)
        direct_tip_label = QLabel("2WP Direct Tip:")
        direct_tip_label.setStyleSheet("font-weight: bold;")
        guidance_layout.addRow(direct_tip_label, aps_direct_tip_layout)

        haul_cycle_file_label = QLabel(
            "Select 2WP Haul Infinity Cycles.csv (optional):"
        )
        haul_cycle_file_label.setStyleSheet("font-weight: bold;")
        self.haul_cycle_file_path = QLineEdit()
        self.haul_cycle_file_path.setReadOnly(True)
        self.haul_cycle_file_path.setFixedWidth(400)
        self.haul_cycle_file_button = QPushButton("Browse")
        self.haul_cycle_file_button.setFixedWidth(100)
        self.haul_cycle_file_button.clicked.connect(
            self.browse_haul_cycle_file
        )
        haul_cycle_file_layout = QHBoxLayout()
        haul_cycle_file_layout.addWidget(self.haul_cycle_file_path)
        haul_cycle_file_layout.addWidget(self.haul_cycle_file_button)
        guidance_layout.addRow(
            haul_cycle_file_label,
            haul_cycle_file_layout,
        )

        self.haul_cycle_crusher_button = QPushButton("Get Crusher Names")
        self.haul_cycle_crusher_button.setMinimumWidth(170)
        self.haul_cycle_crusher_button.clicked.connect(
            self.load_haul_cycle_crusher_names
        )
        self.haul_cycle_crusher_input = QListWidget()
        self.haul_cycle_crusher_input.setSelectionMode(
            QAbstractItemView.MultiSelection
        )
        self.haul_cycle_crusher_input.setMinimumWidth(420)
        self.haul_cycle_crusher_input.setMaximumHeight(105)
        haul_cycle_crusher_layout = QVBoxLayout()
        haul_cycle_crusher_header = QHBoxLayout()
        haul_cycle_crusher_header.addWidget(
            self.haul_cycle_crusher_button
        )
        haul_cycle_crusher_header.addWidget(QLabel(
            "Nearest Crusher is resolved from the shortest selected route."
        ))
        haul_cycle_crusher_header.addStretch()
        haul_cycle_crusher_layout.addLayout(haul_cycle_crusher_header)
        haul_cycle_crusher_layout.addWidget(
            self.haul_cycle_crusher_input
        )
        haul_cycle_crusher_label = QLabel("Haul Cycle Crushers:")
        haul_cycle_crusher_label.setStyleSheet("font-weight: bold;")
        guidance_layout.addRow(
            haul_cycle_crusher_label,
            haul_cycle_crusher_layout,
        )

        self.guidance_schedules_submit_button = QPushButton("Submit")
        self.guidance_schedules_submit_button.setObjectName(
            "guidanceSchedulesSubmitButton"
        )
        self.guidance_schedules_submit_button.setFixedWidth(100)
        self.guidance_schedules_submit_button.clicked.connect(
            self.handle_guidance_schedules_submit
        )
        guidance_submit_layout = QHBoxLayout()
        guidance_submit_layout.addWidget(
            self.guidance_schedules_submit_button
        )
        guidance_submit_layout.addStretch()
        guidance_layout.addRow(guidance_submit_layout)

        product_brand_label = QLabel("Product Brands:")
        product_brand_label.setStyleSheet("font-weight: bold;")
        self.product_brand_labels_input = QLineEdit()
        self.product_brand_labels_input.setFixedWidth(300)
        self.product_brand_labels_input.setText(", ".join(getattr(
            self,
            "product_brand_labels_choice",
            self.default_product_brand_labels(),
        )))
        layout.addRow(product_brand_label, self.product_brand_labels_input)

        product_build_targets_label = QLabel("Product Build Targets:")
        product_build_targets_label.setStyleSheet("font-weight: bold;")
        self.auto_load_2wp_targets_checkbox = QCheckBox(
            "Load automatically from the latest 2WP"
        )
        self.auto_load_2wp_targets_checkbox.setChecked(bool(getattr(
            self,
            "auto_load_2wp_targets_choice",
            True,
        )))
        self.auto_load_2wp_targets_checkbox.setToolTip(
            "Clear this option to skip the Snowflake 2WP target query and enter product builds manually."
        )
        layout.addRow(product_build_targets_label, self.auto_load_2wp_targets_checkbox)

        # --- Input 4: Optimised Blend Choices ---
        blend_label = QLabel("Optimised Blend Choices:")
        blend_label.setStyleSheet("font-weight: bold;")
        self.blend_mode = QComboBox()
        self.blend_mode.addItems(["Select Best Result Automatically", "Prompt User at Decision Point"])
        self.blend_mode.setFixedWidth(300)

        layout.addRow(blend_label, self.blend_mode)

        agent_label = QLabel("PoC Agent:")
        agent_label.setStyleSheet("font-weight: bold;")
        self.agent_enabled_checkbox = QCheckBox("Enable BlendMaster agent bridge")
        self.agent_enabled_checkbox.setChecked(bool(getattr(self, "agent_enabled_choice", False)))
        layout.addRow(agent_label, self.agent_enabled_checkbox)

        # Save and load button
        self.save_button = QPushButton("Save Project")
        self.save_button.setMinimumWidth(125)
        self.save_button.clicked.connect(self.save_state)
        self.save_button.setEnabled(False)

        self.load_button = QPushButton("Load Project")
        self.load_button.setMinimumWidth(125)
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
        self.toggle_crusher_ratio_controls()
        self.validate_form()

        # Connect input field changes to form validation
        self.hub_input.currentIndexChanged.connect(self.validate_form)
        self.mine_input.currentIndexChanged.connect(self.validate_form)
        self.opf_input.currentIndexChanged.connect(self.validate_form)
        self.site_crusher_input.currentIndexChanged.connect(self.handle_operating_crusher_changed)
        self.crusher_ratio_mode_input.currentIndexChanged.connect(self.toggle_crusher_ratio_controls)
        self.crusher_ratio_mode_input.currentIndexChanged.connect(self.validate_form)
        self.crusher_ratio_input.textChanged.connect(self.validate_form)
        self.aps_ratio_crusher_button.clicked.connect(self.load_aps_ratio_crusher_names)
        self.aps_ratio_crusher_input.itemSelectionChanged.connect(self.derive_active_crusher_ratio)
        self.aps_ratio_crusher_input.itemSelectionChanged.connect(self.validate_form)
        self.time_mode.currentIndexChanged.connect(self.validate_form)
        self.start_time.dateTimeChanged.connect(self.validate_form)
        self.file_path.textChanged.connect(self.validate_form)
        self.file_path_24hr.textChanged.connect(self.validate_form)
        self.expit_agent_input.itemSelectionChanged.connect(self.validate_form)
        self.haul_cycle_file_path.textChanged.connect(self.validate_form)
        self.haul_cycle_crusher_input.itemSelectionChanged.connect(
            self.validate_form
        )
        self.haul_cycle_crusher_input.itemSelectionChanged.connect(
            self.handle_haul_cycle_selection_changed
        )
        self.product_brand_labels_input.textChanged.connect(self.validate_form)
        self.blend_mode.currentIndexChanged.connect(self.validate_form)
        self.agent_enabled_checkbox.toggled.connect(self.toggle_agent_enabled)
        self.reevaluate_aps_direct_tip_checkbox.toggled.connect(self.toggle_aps_direct_tip_controls)
        self.reevaluate_aps_direct_tip_checkbox.toggled.connect(self.validate_form)
        self.aps_crusher_input.itemSelectionChanged.connect(self.validate_form)
        self.direct_tip_movement_button.clicked.connect(self.load_direct_tip_movement_options)
        self.direct_tip_crusher_destination_input.itemDoubleClicked.connect(
            self.add_direct_tip_movement_rule
        )
        self.remove_direct_tip_rule_button.clicked.connect(self.remove_direct_tip_movement_rule)

    def validate_form(self):
        """Enable each workflow submit button from the fields it owns."""
        ratio_ready = self.current_crusher_ratio() is not None
        if self.crusher_ratio_mode() == "aps" and len(self.current_site_crusher_options()) > 1:
            ratio_ready = (
                ratio_ready
                and bool(self.file_path.text().strip())
                and bool(self.selected_aps_ratio_crusher_names())
            )
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
        schedule_paths_ready = (
            not self.file_path_24hr.text().strip()
            or bool(self.file_path.text().strip())
        )
        expit_agents_ready = (
            not self.file_path_24hr.text().strip()
            or bool(self.selected_24hr_expit_agent_names())
        )
        haul_cycles_ready = (
            not self.haul_cycle_file_path.text().strip()
            or bool(self.selected_haul_cycle_crusher_names())
        )
        site_fields_populated = (
            self.hub_input.currentIndex() != -1
            and self.mine_input.currentIndex() != -1
            and self.opf_input.currentIndex() != -1
            and len(self.selected_site_crusher_names()) == 1
            and ratio_ready
            and (self.time_mode.currentIndex() == 0 or self.start_time.dateTime().isValid())
            and self.blend_mode.currentIndex() != -1
        )
        guidance_fields_populated = (
            aps_direct_tip_ready
            and schedule_paths_ready
            and expit_agents_ready
            and haul_cycles_ready
        )
        self.submit_button.setEnabled(site_fields_populated)
        self.guidance_schedules_submit_button.setEnabled(
            guidance_fields_populated
            and bool(getattr(self, "stockpile_data", None))
        )
        self.save_button.setEnabled(
            site_fields_populated and guidance_fields_populated
        )

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
        self.progress_dialog.setWindowIcon(blendmaster_app_icon())
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
        """Browse for the 2WP reference schedule."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select 2WP Mining.csv",
            "",
            "APS Mining.csv (*.csv);;All Files (*)",
        )
        if file_path:
            self.file_path.setText(file_path)
            if hasattr(self, "aps_crusher_input"):
                self.aps_crusher_input.clear()
            if hasattr(self, "aps_ratio_crusher_input"):
                self.aps_ratio_crusher_input.clear()
            self.direct_tip_grade_block_sources = []
            self.direct_tip_crusher_destinations = []
            self.direct_tip_movement_rules = []
            self.set_direct_tip_movement_options([], [])
            self.refresh_direct_tip_rule_list()

    def browse_24hr_file(self):
        """Browse for the 24HR movement schedule."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select 24HR Mining.csv",
            "",
            "APS Mining.csv (*.csv);;All Files (*)",
        )
        if file_path:
            self.file_path_24hr.setText(file_path)
            self.set_24hr_expit_agent_items([], [])

    def browse_haul_cycle_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select 2WP Haul Infinity Cycles.csv",
            "",
            "Haul Infinity Cycles.csv (*.csv);;All Files (*)",
        )
        if file_path:
            self.haul_cycle_file_path.setText(file_path)
            self.set_haul_cycle_crusher_items([], [])
            self.haul_cycle_routes = {}
            self.apply_haul_cycle_routes_to_stockpile_data()
            if getattr(self, "stockpile_data", None):
                self.setup_stockpile_table()

    def selected_haul_cycle_crusher_names(self):
        if not hasattr(self, "haul_cycle_crusher_input"):
            return []
        return sorted(
            item.text().strip()
            for item in self.haul_cycle_crusher_input.selectedItems()
            if item.text().strip()
        )

    def available_haul_cycle_crusher_names(self):
        if not hasattr(self, "haul_cycle_crusher_input"):
            return []
        return sorted(
            self.haul_cycle_crusher_input.item(row).text().strip()
            for row in range(self.haul_cycle_crusher_input.count())
            if self.haul_cycle_crusher_input.item(row).text().strip()
        )

    def set_haul_cycle_crusher_items(
        self,
        crusher_names,
        selected_crushers=None,
    ):
        if not hasattr(self, "haul_cycle_crusher_input"):
            return
        crusher_names = self.normalized_expit_agent_names(crusher_names)
        selected = set(
            self.normalized_expit_agent_names(selected_crushers)
        )
        self.haul_cycle_crusher_input.blockSignals(True)
        try:
            self.haul_cycle_crusher_input.clear()
            self.haul_cycle_crusher_input.addItems(crusher_names)
            for row in range(self.haul_cycle_crusher_input.count()):
                item = self.haul_cycle_crusher_input.item(row)
                item.setSelected(item.text().strip() in selected)
        finally:
            self.haul_cycle_crusher_input.blockSignals(False)

    def load_haul_cycle_crusher_names(self):
        file_path = self.haul_cycle_file_path.text().strip()
        if not file_path:
            QMessageBox.information(
                self,
                "BlendMaster",
                "Select a 2WP Haul Infinity Cycles.csv file first.",
            )
            return
        try:
            crusher_names = (
                HaulCycleDataHandler.get_distinct_crusher_names(file_path)
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "BlendMaster",
                f"Unable to read haul-cycle crusher names: {exc}",
            )
            return
        previous = self.selected_haul_cycle_crusher_names()
        retained = [name for name in previous if name in crusher_names]
        selected = retained or crusher_names
        self.set_haul_cycle_crusher_items(crusher_names, selected)
        self.refresh_haul_cycle_routes(show_errors=True)
        if getattr(self, "stockpile_data", None):
            self.setup_stockpile_table()
        if crusher_names:
            QMessageBox.information(
                self,
                "BlendMaster",
                f"Found {len(crusher_names)} crusher destination(s). "
                "All are selected by default.",
            )
        else:
            QMessageBox.information(
                self,
                "BlendMaster",
                "No crusher destinations were found in the cycle file.",
            )
        self.validate_form()

    def handle_haul_cycle_selection_changed(self):
        self.refresh_haul_cycle_routes(show_errors=False)
        if getattr(self, "stockpile_data", None):
            self.setup_stockpile_table()
        self.validate_form()

    def refresh_haul_cycle_routes(self, show_errors=False):
        file_path = (
            self.haul_cycle_file_path.text().strip()
            if hasattr(self, "haul_cycle_file_path")
            else str(getattr(self, "haul_cycle_file_path_choice", "") or "")
        )
        selected = self.selected_haul_cycle_crusher_names()
        self.available_haul_cycle_crushers = (
            self.available_haul_cycle_crusher_names()
        )
        self.selected_haul_cycle_crushers = selected
        if not file_path or not selected:
            self.haul_cycle_routes = {}
            self.apply_haul_cycle_routes_to_stockpile_data()
            return False
        try:
            self.haul_cycle_routes = (
                HaulCycleDataHandler.build_nearest_crusher_routes(
                    file_path,
                    selected,
                )
            )
        except Exception as exc:
            self.haul_cycle_routes = {}
            if show_errors:
                QMessageBox.warning(
                    self,
                    "BlendMaster",
                    f"Unable to resolve nearest-crusher routes: {exc}",
                )
            return False
        self.apply_haul_cycle_routes_to_stockpile_data()
        return True

    def apply_haul_cycle_routes_to_stockpile_data(self):
        routes = getattr(self, "haul_cycle_routes", {}) or {}
        for data in (
            getattr(self, "stockpile_data", None),
            getattr(self, "updated_stockpile_data", None),
        ):
            if not isinstance(data, dict):
                continue
            for key, attributes in data.items():
                if not isinstance(attributes, dict):
                    continue
                stockpile_name = str(
                    attributes.get("name")
                    or attributes.get("NAME")
                    or key
                ).strip()
                route = routes.get(stockpile_name.upper())
                attributes["nearest_crusher"] = (
                    route.get("nearest_crusher", "") if route else ""
                )
                attributes["rehandle_cycle_time_minutes"] = (
                    route.get("cycle_time_minutes") if route else None
                )

    @staticmethod
    def normalized_expit_agent_names(agent_names):
        if agent_names is None:
            return []
        if isinstance(agent_names, (list, tuple, set)):
            return sorted({
                str(name).strip()
                for name in agent_names
                if str(name).strip()
            })
        agent_name = str(agent_names).strip()
        return [agent_name] if agent_name else []

    def selected_24hr_expit_agent_names(self):
        if not hasattr(self, "expit_agent_input"):
            return []
        return sorted(
            item.text().strip()
            for item in self.expit_agent_input.selectedItems()
            if item.text().strip()
        )

    def available_24hr_expit_agent_names(self):
        if not hasattr(self, "expit_agent_input"):
            return []
        return sorted(
            self.expit_agent_input.item(row).text().strip()
            for row in range(self.expit_agent_input.count())
            if self.expit_agent_input.item(row).text().strip()
        )

    def set_24hr_expit_agent_items(self, agent_names, selected_agents=None):
        if not hasattr(self, "expit_agent_input"):
            return
        agent_names = self.normalized_expit_agent_names(agent_names)
        selected_agents = set(
            self.normalized_expit_agent_names(selected_agents)
        )
        self.expit_agent_input.clear()
        self.expit_agent_input.addItems(agent_names)
        for row in range(self.expit_agent_input.count()):
            item = self.expit_agent_input.item(row)
            item.setSelected(item.text().strip() in selected_agents)

    def load_24hr_expit_agent_names(self):
        file_path = (
            self.file_path_24hr.text().strip()
            if hasattr(self, "file_path_24hr")
            else ""
        )
        if not file_path:
            QMessageBox.information(
                self,
                "BlendMaster",
                "Select a 24HR Mining.csv file before loading agent names.",
            )
            return

        try:
            agent_names = ExpitDataHandler.get_distinct_expit_agent_names(
                file_path
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "BlendMaster",
                f"Unable to read agent names from 24HR Mining.csv: {exc}",
            )
            return

        previous_selection = self.selected_24hr_expit_agent_names()
        retained_selection = [
            name for name in previous_selection if name in agent_names
        ]
        selected_agents = retained_selection or agent_names
        self.set_24hr_expit_agent_items(agent_names, selected_agents)
        if agent_names:
            QMessageBox.information(
                self,
                "BlendMaster",
                f"Found {len(agent_names)} expit dig circuit(s). "
                "All circuits are selected by default; deselect any circuits to exclude.",
            )
        else:
            QMessageBox.information(
                self,
                "BlendMaster",
                "No reserve-movement agents were found in the selected 24HR Mining.csv file.",
            )
        self.validate_form()

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
        elif isinstance(self.aps_crusher_input, QComboBox):
            selected = next(iter(selected_crushers), "")
            selected_index = self.aps_crusher_input.findText(selected)
            self.aps_crusher_input.setCurrentIndex(selected_index)

    def selected_aps_crusher_matches_operating_crusher(self):
        selected = self.selected_aps_crusher_names()
        operating = self.selected_site_crusher_names()
        if not selected or len(operating) != 1:
            return False
        return all(
            ExpitDataHandler.crusher_destination_matches(
                destination,
                self.mine_input.currentText(),
                operating[0],
                self.opf_input.currentText() if hasattr(self, "opf_input") else None,
            )
            for destination in selected
        )

    def is_total_feed_operating_crusher(self):
        crusher = getattr(self, "crusher_input_choice", "")
        if hasattr(self, "site_crusher_input"):
            crusher = self.site_crusher_input.currentText()
        return str(crusher or "").strip().upper().replace("-", "_") == "TOTAL_FEED_PC"

    def current_site_start_time(self):
        if hasattr(self, "time_mode") and self.time_mode.currentIndex() == 1:
            return self.start_time.dateTime().toPyDateTime()
        return datetime.now()

    def load_aps_crusher_names(self):
        file_path = self.file_path.text().strip() if hasattr(self, "file_path") else ""
        if not file_path:
            QMessageBox.information(
                self,
                "BlendMaster",
                "Select a 2WP Mining.csv file before loading crusher names.",
            )
            return

        try:
            crusher_names = ExpitDataHandler.get_distinct_crusher_destinations(
                file_path,
                self.current_site_start_time(),
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "BlendMaster",
                f"Unable to read crusher names from 2WP Mining.csv: {exc}",
            )
            return

        previous_selection = self.selected_aps_crusher_names()
        operating_crusher = self.site_crusher_input.currentText().strip()
        mine = self.mine_input.currentText().strip()
        matching_names = [
            name for name in crusher_names
            if ExpitDataHandler.crusher_destination_matches(
                name, mine, operating_crusher,
                self.opf_input.currentText() if hasattr(self, "opf_input") else None,
            )
        ]
        retained_selection = [
            name for name in previous_selection if name in crusher_names
        ]
        if retained_selection:
            selected_names = retained_selection
        elif self.is_total_feed_operating_crusher():
            selected_names = matching_names
        else:
            selected_names = matching_names[:1]
        self.set_aps_crusher_items(crusher_names, selected_names)
        if crusher_names:
            selection_guidance = (
                "All matching child crusher destinations were selected for this Total Feed scenario."
                if self.is_total_feed_operating_crusher() and selected_names
                else "Select the corresponding 2WP crusher."
            )
            QMessageBox.information(
                self,
                "BlendMaster",
                f"Found {len(crusher_names)} crusher destination(s). {selection_guidance}",
            )
        else:
            QMessageBox.information(
                self,
                "BlendMaster",
                "No crusher destinations were found in the selected 2WP Mining.csv file.",
            )
        self.validate_form()

    @staticmethod
    def normalized_crusher_ratio(value, default=None):
        try:
            ratio = float(value)
        except (TypeError, ValueError):
            return default
        if ratio > 1:
            ratio /= 100.0
        return ratio if 0 < ratio <= 1 else default

    @staticmethod
    def normalized_crusher_ratio_mode(value):
        text = str(value or "manual").strip().lower()
        if text == "aps" or "derive" in text or "mining.csv" in text:
            return "aps"
        return "manual"

    def crusher_ratio_mode(self):
        if not hasattr(self, "crusher_ratio_mode_input"):
            return self.normalized_crusher_ratio_mode(
                getattr(self, "crusher_ratio_mode_choice", "manual")
            )
        return str(self.crusher_ratio_mode_input.currentData() or "manual")

    def current_crusher_ratio(self):
        if not hasattr(self, "crusher_ratio_input"):
            return self.normalized_crusher_ratio(
                getattr(self, "crusher_contribution_ratio_choice", 1.0),
                default=None,
            )
        return self.normalized_crusher_ratio(
            self.crusher_ratio_input.text(),
            default=None,
        )

    def current_site_crusher_options(self):
        return crusher_options_for_site(
            self.mine_input.currentText() if hasattr(self, "mine_input") else "",
            self.opf_input.currentText() if hasattr(self, "opf_input") else "",
        )

    def selected_aps_ratio_crusher_names(self):
        if not hasattr(self, "aps_ratio_crusher_input"):
            return []
        return [
            item.text().strip()
            for item in self.aps_ratio_crusher_input.selectedItems()
            if item.text().strip()
        ]

    def set_aps_ratio_crusher_items(self, names, selected=None):
        if not hasattr(self, "aps_ratio_crusher_input"):
            return
        selected = {
            str(value).strip() for value in selected or [] if str(value).strip()
        }
        self.aps_ratio_crusher_input.clear()
        self.aps_ratio_crusher_input.addItems(names or [])
        for row in range(self.aps_ratio_crusher_input.count()):
            item = self.aps_ratio_crusher_input.item(row)
            item.setSelected(item.text().strip() in selected)

    def toggle_crusher_ratio_controls(self, *_args):
        if not hasattr(self, "crusher_ratio_mode_input"):
            return
        multiple_crushers = len(self.current_site_crusher_options()) > 1
        derive_from_aps = self.crusher_ratio_mode() == "aps"
        self.crusher_ratio_mode_input.setEnabled(multiple_crushers)
        self.crusher_ratio_input.setEnabled(multiple_crushers and not derive_from_aps)
        self.aps_ratio_crusher_button.setEnabled(
            multiple_crushers and derive_from_aps and bool(self.file_path.text().strip())
        )
        self.aps_ratio_crusher_input.setEnabled(multiple_crushers and derive_from_aps)
        if not multiple_crushers:
            self.crusher_ratio_mode_input.setCurrentIndex(0)
            self.crusher_ratio_input.setText("100.00")
        elif derive_from_aps:
            self.derive_active_crusher_ratio()
        self.validate_form()

    def load_aps_ratio_crusher_names(self):
        file_path = self.file_path.text().strip()
        if not file_path:
            QMessageBox.information(
                self, "BlendMaster", "Select 2WP Mining.csv before deriving crusher ratios."
            )
            return
        try:
            names = ExpitDataHandler.get_distinct_crusher_destinations(
                file_path,
                self.current_site_start_time(),
            )
        except Exception as exc:
            QMessageBox.warning(
                self, "BlendMaster", f"Unable to read APS crusher feed destinations: {exc}"
            )
            return
        previous = self.selected_aps_ratio_crusher_names()
        self.set_aps_ratio_crusher_items(names, previous)
        self.derive_active_crusher_ratio()

    def derive_active_crusher_ratio(self):
        if self.crusher_ratio_mode() != "aps" or not hasattr(self, "crusher_ratio_input"):
            return
        file_path = self.file_path.text().strip()
        selected = self.selected_aps_ratio_crusher_names()
        if not file_path or not selected:
            self.crusher_ratio_input.clear()
            return
        try:
            ratios = ExpitDataHandler.calculate_crusher_feed_ratios(
                file_path,
                self.current_site_start_time(),
                selected,
            )
        except Exception as exc:
            self.crusher_ratio_input.clear()
            print(f"Warning: unable to derive APS crusher contribution ratio: {exc}")
            return
        mine = self.mine_input.currentText().strip()
        operating_crusher = self.site_crusher_input.currentText().strip()
        ratio = sum(
            value for destination, value in ratios.items()
            if ExpitDataHandler.crusher_destination_matches(
                destination, mine, operating_crusher,
                self.opf_input.currentText() if hasattr(self, "opf_input") else None,
            )
        )
        self.crusher_ratio_input.setText(f"{ratio * 100:.4f}" if ratio > 0 else "")

    def load_ratio_controls_from_state(self):
        if not hasattr(self, "crusher_ratio_mode_input"):
            return
        mode_index = self.crusher_ratio_mode_input.findData(
            self.normalized_crusher_ratio_mode(
                getattr(self, "crusher_ratio_mode_choice", "manual")
            )
        )
        self.crusher_ratio_mode_input.setCurrentIndex(max(mode_index, 0))
        ratio = self.normalized_crusher_ratio(
            getattr(self, "crusher_contribution_ratio_choice", 1.0),
            default=1.0,
        )
        self.crusher_ratio_input.setText(f"{ratio * 100:.4f}".rstrip("0").rstrip("."))
        choices = getattr(self, "aps_ratio_crusher_choices", []) or []
        self.set_aps_ratio_crusher_items(choices, choices)
        self.toggle_crusher_ratio_controls()

    def set_direct_tip_movement_options(self, sources, destinations):
        if not hasattr(self, "direct_tip_grade_block_source_input"):
            return
        self.direct_tip_grade_block_source_input.clear()
        self.direct_tip_grade_block_source_input.addItems(sorted(set(sources or [])))
        self.direct_tip_crusher_destination_input.clear()
        self.direct_tip_crusher_destination_input.addItems(sorted(set(destinations or [])))

    def load_direct_tip_movement_options(self):
        file_path = self.file_path.text().strip()
        if not file_path:
            QMessageBox.information(
                self, "BlendMaster", "Select 2WP Mining.csv before loading movement values."
            )
            return
        try:
            options = ExpitDataHandler.get_direct_tip_movement_options(
                file_path,
                self.current_site_start_time(),
            )
        except Exception as exc:
            QMessageBox.warning(
                self, "BlendMaster", f"Unable to read direct-tip movement values: {exc}"
            )
            return
        self.direct_tip_grade_block_sources = options["grade_block_sources"]
        self.direct_tip_crusher_destinations = options["crusher_destinations"]
        self.set_direct_tip_movement_options(
            self.direct_tip_grade_block_sources,
            self.direct_tip_crusher_destinations,
        )

    def add_direct_tip_movement_rule(self, destination_item):
        source_items = self.direct_tip_grade_block_source_input.selectedItems()
        if not source_items or destination_item is None:
            QMessageBox.information(
                self,
                "BlendMaster",
                "Select a grade-block source before double-clicking a crusher destination.",
            )
            return
        rule = {
            "grade_block_source": source_items[0].text().strip(),
            "crusher_destination": destination_item.text().strip(),
        }
        if rule not in self.direct_tip_movement_rules:
            self.direct_tip_movement_rules.append(rule)
            self.refresh_direct_tip_rule_list()

    def refresh_direct_tip_rule_list(self):
        if not hasattr(self, "direct_tip_rule_input"):
            return
        self.direct_tip_movement_rules = ExpitDataHandler._normalize_movement_rules(
            getattr(self, "direct_tip_movement_rules", [])
        )
        self.direct_tip_rule_input.clear()
        for rule in self.direct_tip_movement_rules:
            self.direct_tip_rule_input.addItem(
                f"{rule['grade_block_source']}  ->  {rule['crusher_destination']}"
            )

    def remove_direct_tip_movement_rule(self):
        rows = sorted(
            {index.row() for index in self.direct_tip_rule_input.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            if 0 <= row < len(self.direct_tip_movement_rules):
                self.direct_tip_movement_rules.pop(row)
        self.refresh_direct_tip_rule_list()

    def refresh_aps_stockpile_brand_map(self):
        self.aps_stockpile_brand_map = {}
        self.aps_stockpile_timing_guidance = {}
        self.aps_active_blend_guidance = []
        self.aps_destination_guidance = {}
        file_path = getattr(self, "file_path_choice", "") or ""
        if not file_path:
            return
        try:
            file_stamp = os.path.getmtime(file_path)
        except OSError:
            file_stamp = None
        cache_key = (
            os.path.normcase(os.path.abspath(file_path)),
            file_stamp,
            str(getattr(self, "mine_input_choice", "") or "").strip().upper(),
            tuple(self.product_brand_options()),
        )
        cached_map = getattr(self, "aps_brand_guidance_cache", {}).get(cache_key)
        if cached_map is not None:
            cached_guidance = copy.deepcopy(cached_map)
            if "brand_guidance" in cached_guidance:
                self.aps_stockpile_brand_map = cached_guidance.get(
                    "brand_guidance", {}
                )
                self.aps_stockpile_timing_guidance = cached_guidance.get(
                    "stockpile_timing_guidance", {}
                )
                self.aps_active_blend_guidance = cached_guidance.get(
                    "active_blend_guidance", []
                )
                self.aps_destination_guidance = cached_guidance.get(
                    "destination_guidance", {}
                )
            else:
                # Compatibility with caches created before dual ingestion.
                self.aps_stockpile_brand_map = cached_guidance
            return
        try:
            guidance = ExpitDataHandler.get_2wp_schedule_guidance(
                file_path,
                self.product_brand_options(),
            )
            guidance["destination_guidance"] = (
                ExpitDataHandler.build_2wp_destination_guidance(file_path)
            )
            self.aps_stockpile_brand_map = guidance["brand_guidance"]
            self.aps_stockpile_timing_guidance = guidance[
                "stockpile_timing_guidance"
            ]
            self.aps_active_blend_guidance = guidance["active_blend_guidance"]
            self.aps_destination_guidance = guidance["destination_guidance"]
            self.aps_brand_guidance_cache[cache_key] = copy.deepcopy(guidance)
        except Exception as exc:
            self.aps_stockpile_brand_map = {}
            self.aps_stockpile_timing_guidance = {}
            self.aps_active_blend_guidance = []
            self.aps_destination_guidance = {}
            print(f"Warning: unable to derive 2WP schedule guidance: {exc}")

    def aps_brand_info_for_stockpile(self, stockpile_name):
        brand_map = getattr(self, "aps_stockpile_brand_map", {}) or {}
        if not brand_map:
            return {}
        normalized_name = str(stockpile_name or "").strip().upper().replace("STOCKPILES/", "")
        lookup = {
            str(name or "").strip().upper().replace("STOCKPILES/", ""): value
            for name, value in brand_map.items()
        }
        return lookup.get(normalized_name, {})

    def aps_timing_info_for_stockpile(self, stockpile_name):
        timing_map = getattr(self, "aps_stockpile_timing_guidance", {}) or {}
        normalized_name = (
            str(stockpile_name or "").strip().upper().replace("STOCKPILES/", "")
        )
        lookup = {
            str(name or "").strip().upper().replace("STOCKPILES/", ""): value
            for name, value in timing_map.items()
        }
        return lookup.get(normalized_name, {})

    def format_aps_brand_summary(self, brand_info):
        proportions = (brand_info or {}).get("brand_proportions") or {}
        if not proportions:
            return ""
        ordered = sorted(proportions.items(), key=lambda item: item[1], reverse=True)
        if len(ordered) == 1:
            return ordered[0][0]
        return ", ".join(f"{brand} {proportion * 100:.0f}%" for brand, proportion in ordered)

    def apply_aps_brand_guidance_to_stockpile_data(self):
        if not isinstance(self.stockpile_data, dict):
            return
        for stockpile_name, attributes in self.stockpile_data.items():
            if not isinstance(attributes, dict):
                continue
            brand_info = self.aps_brand_info_for_stockpile(stockpile_name)
            attributes["aps_brand"] = brand_info.get("primary_brand", "")
            attributes["aps_brand_proportions"] = brand_info.get("brand_proportions", {})
            attributes["aps_brand_tonnes"] = brand_info.get("brand_tonnes", {})
            attributes["aps_brand_total_tonnes"] = brand_info.get("total_tonnes", 0)
            attributes["aps_brand_summary"] = self.format_aps_brand_summary(brand_info)
            attributes["aps_timing_guidance"] = copy.deepcopy(
                self.aps_timing_info_for_stockpile(stockpile_name)
            )

    def update_mine_dropdown(self):
        """Update the Mine dropdown based on the selected Hub."""
        hub_selection = self.hub_input.currentText()
        previous_mine = self.mine_input.currentText().strip()

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

        if previous_mine and self.mine_input.findText(previous_mine) >= 0:
            self.mine_input.setCurrentText(previous_mine)
        self.update_opf_dropdown()

    @staticmethod
    def default_opf_for_site(mine, crusher=None):
        mine = str(mine or "").strip().upper()
        crusher = str(crusher or "").strip().upper()
        options = opf_options_for_mine(mine)
        if crusher:
            for opf in options:
                if crusher in {
                    value.upper() for value in crusher_options_for_site(mine, opf)
                }:
                    return opf
        return options[0] if options else ""

    def update_opf_dropdown(self, selected_opf=None):
        if not hasattr(self, "opf_input"):
            return
        if isinstance(selected_opf, (int, bool)):
            selected_opf = None
        previous = str(selected_opf or self.opf_input.currentText() or "").strip()
        mine = self.mine_input.currentText().strip().upper()
        options = opf_options_for_mine(mine)
        self.opf_input.blockSignals(True)
        self.opf_input.clear()
        self.opf_input.addItems(options)
        if previous and self.opf_input.findText(previous) >= 0:
            self.opf_input.setCurrentText(previous)
        self.opf_input.blockSignals(False)
        self.update_site_crusher_options()

    def selected_site_crusher_names(self):
        if not hasattr(self, "site_crusher_input"):
            return []
        if isinstance(self.site_crusher_input, QComboBox):
            value = self.site_crusher_input.currentText().strip()
            return [value] if value else []
        return [
            item.text().strip()
            for item in self.site_crusher_input.selectedItems()
            if item.text().strip()
        ]

    def update_site_crusher_options(self, selected_crushers=None):
        if not hasattr(self, "site_crusher_input"):
            return
        if isinstance(selected_crushers, (int, bool)):
            selected_crushers = None
        if selected_crushers is None:
            selected_crushers = self.selected_site_crusher_names()
        selected = [str(value).strip() for value in selected_crushers or [] if str(value).strip()]
        mine = self.mine_input.currentText().strip().upper() if hasattr(self, "mine_input") else ""
        opf = self.opf_input.currentText().strip() if hasattr(self, "opf_input") else ""
        crushers = crusher_options_for_site(mine, opf)
        self.site_crusher_input.blockSignals(True)
        self.site_crusher_input.clear()
        self.site_crusher_input.addItems(crushers)
        if selected:
            index = self.site_crusher_input.findText(selected[0])
            self.site_crusher_input.setCurrentIndex(index if index >= 0 else 0)
        elif crushers:
            self.site_crusher_input.setCurrentIndex(0)
        self.site_crusher_input.blockSignals(False)
        self.toggle_crusher_ratio_controls()
        self.handle_operating_crusher_changed()

    def handle_operating_crusher_changed(self, *_args):
        self.toggle_crusher_ratio_controls()
        if self.crusher_ratio_mode() == "aps":
            self.derive_active_crusher_ratio()
        self.validate_form()

    def capture_site_ratio_and_movement_controls(self):
        self.opf_input_choice = self.opf_input.currentText().strip()
        self.selected_site_crushers = self.selected_site_crusher_names()
        self.crusher_input_choice = (
            self.selected_site_crushers[0] if self.selected_site_crushers else None
        )
        self.crusher_ratio_mode_choice = self.crusher_ratio_mode()
        self.crusher_contribution_ratio_choice = self.current_crusher_ratio()
        self.crusher_ratio_configured = True
        self.aps_ratio_crusher_choices = self.selected_aps_ratio_crusher_names()
        self.direct_tip_grade_block_sources = [
            self.direct_tip_grade_block_source_input.item(row).text().strip()
            for row in range(self.direct_tip_grade_block_source_input.count())
        ]
        self.direct_tip_crusher_destinations = [
            self.direct_tip_crusher_destination_input.item(row).text().strip()
            for row in range(self.direct_tip_crusher_destination_input.count())
        ]
        self.direct_tip_movement_rules = ExpitDataHandler._normalize_movement_rules(
            self.direct_tip_movement_rules
        )

    def site_ratio_group_states(self, include_live=True):
        hub = str(getattr(self, "hub_input_choice", "") or "").strip()
        mine = str(getattr(self, "mine_input_choice", "") or "").strip()
        opf = str(getattr(self, "opf_input_choice", "") or "").strip()
        states = []
        for scenario_id, state in self.site_scenarios.items():
            if include_live and scenario_id == self.active_scenario_id:
                continue
            if (
                str(state.get("hub_input_choice") or "").strip() == hub
                and str(state.get("mine_input_choice") or "").strip() == mine
                and str(state.get("opf_input_choice") or self.default_opf_for_site(
                    state.get("mine_input_choice"), state.get("crusher_input_choice")
                )).strip() == opf
                and state.get("crusher_ratio_configured", False)
            ):
                states.append(state)
        if include_live:
            states.append({
                "crusher_input_choice": self.crusher_input_choice,
                "crusher_contribution_ratio_choice": self.crusher_contribution_ratio_choice,
                "crusher_ratio_configured": self.crusher_ratio_configured,
            })
        return states

    def validate_site_configuration_constraints(self):
        if len(self.selected_site_crushers) != 1:
            return False, "Select exactly one operating crusher for this site scenario."
        ratio = self.crusher_contribution_ratio_choice
        if ratio is None or not 0 < ratio <= 1:
            return False, "Enter or derive a crusher contribution ratio greater than 0% and no more than 100%."

        group_states = self.site_ratio_group_states()
        crushers = [
            str(state.get("crusher_input_choice") or "").strip()
            for state in group_states
        ]
        if len(crushers) != len(set(crushers)):
            return False, (
                "This Hub / Mine / OPF already contains a scenario for the selected operating crusher. "
                "Update that scenario or choose another crusher."
            )
        crusher_options = crusher_options_for_site(
            self.mine_input_choice, self.opf_input_choice
        )
        if len(crusher_options) <= 1:
            self.crusher_contribution_ratio_choice = 1.0
            return True, ""

        ratio_total = sum(
            self.normalized_crusher_ratio(
                state.get("crusher_contribution_ratio_choice"), default=0.0
            )
            for state in group_states
        )
        if ratio_total > 1.000001:
            return False, (
                f"Crusher contribution ratios for {self.mine_input_choice} / {self.opf_input_choice} "
                f"total {ratio_total * 100:.2f}%. They cannot exceed 100%."
            )
        configured = set(crushers)
        if set(crusher_options).issubset(configured) and not math.isclose(
            ratio_total, 1.0, abs_tol=1e-6
        ):
            return False, (
                f"Crusher contribution ratios for {self.mine_input_choice} / {self.opf_input_choice} "
                f"must total 100%; they currently total {ratio_total * 100:.2f}%."
            )
        return True, ""

    def validate_guidance_schedule_constraints(self):
        if self.file_path_24hr_choice and not self.file_path_choice:
            return False, (
                "Select a 2WP Mining.csv reference before importing a "
                "24HR Mining.csv schedule."
            )
        if (
            self.file_path_24hr_choice
            and not self.selected_24hr_expit_agents
        ):
            return False, (
                "Load and select at least one 24HR expit dig circuit."
            )
        if (
            self.haul_cycle_file_path_choice
            and not self.selected_haul_cycle_crushers
        ):
            return False, (
                "Load and select at least one Haul Infinity crusher."
            )
        if self.reevaluate_aps_direct_tip_choice:
            selected_destinations = self.aps_direct_tip_crusher_choice
            if not selected_destinations:
                return False, (
                    "Select at least one 2WP direct-tip crusher destination."
                )
            if not self.selected_aps_crusher_matches_operating_crusher():
                return False, (
                    "Each selected 2WP direct-tip crusher destination must "
                    "map to the operating crusher for this scenario."
                )
            if (
                not self.is_total_feed_operating_crusher()
                and len(selected_destinations) != 1
            ):
                return False, (
                    "Select exactly one 2WP direct-tip crusher destination "
                    "for an individual crusher scenario."
                )
        return True, ""

    def validate_active_ratio_group_for_run(self):
        # Each operating-crusher scenario is independently optimisable.  The
        # contribution ratio scales this scenario's product-build target; it is
        # not a prerequisite that sibling crusher scenarios have been created.
        ratio = self.normalized_crusher_ratio(
            getattr(self, "crusher_contribution_ratio_choice", None), default=0.0
        )
        if not 0 < ratio <= 1:
            return False, "Enter a crusher contribution ratio greater than 0% and no more than 100%."
        return True, ""

    def capture_site_configuration_controls(self):
        """Capture the complete live Site Configuration form into scenario state."""
        self.time_mode_choice = self.time_mode.currentIndex() + 1
        self.start_time_choice = (
            self.start_time.dateTime().toPyDateTime()
            if self.time_mode_choice == 2
            else datetime.now()
        )
        self.expit_mode_choice = (
            self.expit_mode.currentIndex() + 1 if self.expit_mode.isEnabled() else 1
        )
        self.file_path_choice = self.file_path.text().strip()
        self.file_path_24hr_choice = self.file_path_24hr.text().strip()
        self.available_24hr_expit_agents = (
            self.available_24hr_expit_agent_names()
        )
        self.selected_24hr_expit_agents = (
            self.selected_24hr_expit_agent_names()
        )
        self.haul_cycle_file_path_choice = (
            self.haul_cycle_file_path.text().strip()
        )
        self.available_haul_cycle_crushers = (
            self.available_haul_cycle_crusher_names()
        )
        self.selected_haul_cycle_crushers = (
            self.selected_haul_cycle_crusher_names()
        )
        if self.haul_cycle_file_path_choice:
            self.refresh_haul_cycle_routes(show_errors=False)
        self.blend_mode_choice = self.blend_mode.currentIndex() + 1
        self.product_brand_labels_choice = self.parse_product_brand_labels(
            self.product_brand_labels_input.text()
        )
        self.auto_load_2wp_targets_choice = (
            self.auto_load_2wp_targets_checkbox.isChecked()
        )
        self.agent_enabled_choice = self.agent_enabled_checkbox.isChecked()
        self.hub_input_choice = self.hub_input.currentText().strip()
        self.mine_input_choice = self.mine_input.currentText().strip()
        self.capture_site_ratio_and_movement_controls()
        self.reevaluate_aps_direct_tip_choice = (
            self.reevaluate_aps_direct_tip_checkbox.isChecked()
        )
        self.aps_direct_tip_crusher_choice = (
            self.selected_aps_crusher_names()
            if self.reevaluate_aps_direct_tip_choice
            else []
        )

    def capture_guidance_schedule_controls(self):
        """Capture controls owned by the Guidance Schedules workflow step."""
        self.expit_mode_choice = (
            self.expit_mode.currentIndex() + 1
            if self.expit_mode.isEnabled()
            else 1
        )
        self.file_path_choice = self.file_path.text().strip()
        self.file_path_24hr_choice = self.file_path_24hr.text().strip()
        self.available_24hr_expit_agents = (
            self.available_24hr_expit_agent_names()
        )
        self.selected_24hr_expit_agents = (
            self.selected_24hr_expit_agent_names()
        )
        self.haul_cycle_file_path_choice = (
            self.haul_cycle_file_path.text().strip()
        )
        self.available_haul_cycle_crushers = (
            self.available_haul_cycle_crusher_names()
        )
        self.selected_haul_cycle_crushers = (
            self.selected_haul_cycle_crusher_names()
        )
        self.direct_tip_grade_block_sources = [
            self.direct_tip_grade_block_source_input.item(row).text().strip()
            for row in range(
                self.direct_tip_grade_block_source_input.count()
            )
        ]
        self.direct_tip_crusher_destinations = [
            self.direct_tip_crusher_destination_input.item(row).text().strip()
            for row in range(
                self.direct_tip_crusher_destination_input.count()
            )
        ]
        self.direct_tip_movement_rules = (
            ExpitDataHandler._normalize_movement_rules(
                self.direct_tip_movement_rules
            )
        )
        self.reevaluate_aps_direct_tip_choice = (
            self.reevaluate_aps_direct_tip_checkbox.isChecked()
        )
        self.aps_direct_tip_crusher_choice = (
            self.selected_aps_crusher_names()
            if self.reevaluate_aps_direct_tip_choice
            else []
        )
        self.refresh_haul_cycle_routes(show_errors=False)

    def restore_site_configuration_controls(self):
        """Restore Site Configuration widgets without discarding legacy project defaults."""
        self.hub_input.setCurrentText(str(self.hub_input_choice or ""))
        self.update_mine_dropdown()
        self.mine_input.setCurrentText(str(self.mine_input_choice or ""))
        self.update_opf_dropdown(getattr(self, "opf_input_choice", ""))
        self.update_site_crusher_options(
            getattr(self, "selected_site_crushers", None)
            or [getattr(self, "crusher_input_choice", "")]
        )
        self.file_path.setText(str(self.file_path_choice or ""))
        self.file_path_24hr.setText(str(self.file_path_24hr_choice or ""))
        self.set_24hr_expit_agent_items(
            getattr(self, "available_24hr_expit_agents", []),
            getattr(self, "selected_24hr_expit_agents", []),
        )
        self.haul_cycle_file_path.setText(str(
            getattr(self, "haul_cycle_file_path_choice", "") or ""
        ))
        self.set_haul_cycle_crusher_items(
            getattr(self, "available_haul_cycle_crushers", []),
            getattr(self, "selected_haul_cycle_crushers", []),
        )
        self.load_ratio_controls_from_state()
        self.set_direct_tip_movement_options(
            getattr(self, "direct_tip_grade_block_sources", []),
            getattr(self, "direct_tip_crusher_destinations", []),
        )
        self.refresh_direct_tip_rule_list()

        self.time_mode.setCurrentIndex(max(int(self.time_mode_choice or 1) - 1, 0))
        start_time = self.start_time_choice or datetime.now()
        self.start_time.setDateTime(QDateTime(
            start_time.year,
            start_time.month,
            start_time.day,
            start_time.hour,
            start_time.minute,
            start_time.second,
        ))
        self.expit_mode.setCurrentIndex(max(int(self.expit_mode_choice or 1) - 1, 0))
        self.reevaluate_aps_direct_tip_checkbox.setChecked(
            bool(getattr(self, "reevaluate_aps_direct_tip_choice", False))
        )
        saved_crushers = self.normalized_aps_crusher_choice(
            getattr(self, "aps_direct_tip_crusher_choice", [])
        )
        self.set_aps_crusher_items(saved_crushers, saved_crushers)
        self.toggle_aps_direct_tip_controls(self.reevaluate_aps_direct_tip_choice)
        self.blend_mode.setCurrentIndex(max(int(self.blend_mode_choice or 1) - 1, 0))
        self.agent_enabled_checkbox.setChecked(
            bool(getattr(self, "agent_enabled_choice", False))
        )
        self.product_brand_labels_input.setText(", ".join(
            self.parse_product_brand_labels(
                getattr(self, "product_brand_labels_choice", [])
            )
        ))
        self.auto_load_2wp_targets_checkbox.setChecked(
            bool(getattr(self, "auto_load_2wp_targets_choice", True))
        )

    def handle_site_config_submit(self):
        """Submit one explicit Hub / Mine / OPF / Crusher scenario."""
        restoring_project = bool(
            self.is_project_loaded and self.project_load_restore_in_progress
        )
        if restoring_project:
            self.restore_site_configuration_controls()
        else:
            self.capture_site_configuration_controls()
            valid, validation_message = self.validate_site_configuration_constraints()
            if not valid:
                QMessageBox.warning(
                    self,
                    "Site Configuration",
                    validation_message,
                )
                return

        required_values = (
            self.hub_input_choice,
            self.mine_input_choice,
            getattr(self, "opf_input_choice", None),
            self.crusher_input_choice,
            self.time_mode_choice,
            self.start_time_choice,
            self.expit_mode_choice,
            self.blend_mode_choice,
        )
        if not all(required_values):
            QMessageBox.warning(
                self,
                "Missing Information",
                "Please complete Hub, Mine, OPF, Operating Crusher, and run timing.",
            )
            return

        if self.is_project_loaded:
            if not self.stockpile_data:
                QMessageBox.warning(
                    self,
                    "Missing Project Data",
                    "The loaded project does not contain stockpile inventory data.",
                )
                return
            self.finish_site_config_submit(self.stockpile_data)
            return

        self.submit_button.setEnabled(False)
        progress_message = (
            "Fetching stockpile inventories and ratio-adjusted 2WP build targets from Snowflake..."
            if self.auto_load_2wp_targets_choice
            else "Fetching stockpile inventories from Snowflake..."
        )
        self.run_background_task(
            progress_message,
            self.fetch_site_configuration_data,
            self.finish_site_config_submit,
            self.handle_site_config_error,
        )

    def fetch_stockpile_data(self):
        """Fetch stockpile data from OpeningStockpileInventories."""
        hub_input = self.hub_input_choice
        mine_input = self.mine_input_choice
        return self.opening_stockpile_inventories.call_opening_stockpile_inventories(hub_input, mine_input, self.start_time_choice)

    def fetch_site_configuration_data(self):
        stockpile_data = self.fetch_stockpile_data()
        build_targets = {crusher: [] for crusher in self.selected_site_crushers}
        target_errors = {}
        if self.auto_load_2wp_targets_choice:
            for crusher in self.selected_site_crushers:
                try:
                    build_targets[crusher] = self.planning_plan_targets.fetch(
                        self.mine_input_choice,
                        crusher,
                        self.start_time_choice,
                        self.product_brand_labels_choice,
                        opf=self.opf_input_choice,
                        crusher_contribution_ratio=self.crusher_contribution_ratio_choice,
                    )
                except Exception as exc:
                    target_errors[crusher] = str(exc)
        return {
            "stockpile_data": stockpile_data,
            "build_targets": build_targets,
            "target_errors": target_errors,
            "automatic_2wp_targets": self.auto_load_2wp_targets_choice,
        }

    def reset_downstream_inputs_for_new_site_configuration(self):
        """Clear workflow state that is owned by the submitted site context."""
        self.stockpile_data_use_column = {}
        self.stockpile_data_AMT_column = {}
        self.updated_stockpile_data = None
        self.updated_stockpile_data_keys = {}.keys()
        self.AMT_stockpile_data = {}
        self.AMT_chunk_settings = {}
        self.hex_sequence_table = []
        self.hex_sequence_table_argument = []
        self.calendar_inputs = {}
        self.solver_config = self.normalized_solver_config({})
        self.min_stockpiles = None
        self.max_stockpiles = None
        self.min_stockpile_contribution_ratio = Optimizer.MIN_SELECTED_STOCKPILE_BLEND_RATIO
        self.saved_blends_for_schedule = []
        self.stored_blend_sequence_table_for_gantt = []
        self.stored_blend_sequence_table_for_gantt_default = []
        self.default_start_datetime = None
        self.default_end_datetime = None
        self.default_start_datetime_str = None
        self.default_end_datetime_str = None
        self.crusher_rate = None
        self.crusher_rate_input_value = None
        self.crusher_rate_input_values = {}
        self.blend_config_table_inputs = {}
        self.total_AMT_stockpile_balances = {}
        self.restore_table_snapshot(getattr(self, "decision_table", None), None)
        if hasattr(self, "decision_output"):
            self.decision_output.clear()
        if hasattr(self, "decision_status_label"):
            self.decision_status_label.setText(
                "Run the optimiser to review feasible blend options."
            )

    def register_submitted_site_scenarios(self, build_targets):
        selected_crushers = list(self.selected_site_crushers or [])
        if not selected_crushers:
            return

        current_crusher = selected_crushers[0]
        self.crusher_input_choice = current_crusher
        self.product_build_settings = copy.deepcopy(build_targets.get(current_crusher) or [])
        self.populate_product_build_table()
        self.saved_blends_for_schedule = []
        self.stored_blend_sequence_table_for_gantt = []
        self.stored_blend_sequence_table_for_gantt_default = []
        self.reset_workflow_tabs_for_scenario()
        DatabaseManager.clear_all_tables(get_database_path())
        self.seed_active_scenario_database(force=True)
        current_state = self.capture_scenario_state()
        current_state["crusher_input_choice"] = current_crusher
        current_state["selected_site_crushers"] = [current_crusher]
        current_state["opf_input_choice"] = self.opf_input_choice
        current_state["crusher_contribution_ratio_choice"] = (
            self.crusher_contribution_ratio_choice
        )
        current_state["direct_tip_movement_rules"] = copy.deepcopy(
            self.direct_tip_movement_rules
        )
        self.site_scenarios[self.active_scenario_id] = current_state
        self.selected_site_crushers = [current_crusher]
        self.update_site_crusher_options(self.selected_site_crushers)
        self.refresh_scenario_selector()

    def finish_site_config_submit(self, stockpile_data):
        restoring_project = bool(
            self.is_project_loaded
            and self.project_load_restore_in_progress
        )
        self.submit_button.setEnabled(True)
        self.save_button.setEnabled(True)
        build_targets = {}
        target_errors = {}
        automatic_2wp_targets = True
        fresh_site_configuration = (
            isinstance(stockpile_data, dict) and "stockpile_data" in stockpile_data
        )
        if fresh_site_configuration:
            build_targets = stockpile_data.get("build_targets") or {}
            target_errors = stockpile_data.get("target_errors") or {}
            automatic_2wp_targets = bool(
                stockpile_data.get("automatic_2wp_targets", True)
            )
            stockpile_data = stockpile_data.get("stockpile_data") or {}
            self.reset_downstream_inputs_for_new_site_configuration()
        self.stockpile_data = stockpile_data
        self.refresh_aps_stockpile_brand_map()
        self.apply_aps_brand_guidance_to_stockpile_data()
        if self.haul_cycle_file_path_choice:
            self.refresh_haul_cycle_routes(show_errors=True)
        else:
            self.apply_haul_cycle_routes_to_stockpile_data()
        if build_targets:
            self.register_submitted_site_scenarios(build_targets)
        else:
            self.save_active_scenario_state()
        message = (
            f"Configuration successfully submitted for Hub: {self.hub_input_choice}, "
            f"Mine: {self.mine_input_choice}, OPF: {self.opf_input_choice}, "
            f"Crusher: {self.crusher_input_choice} "
            f"({self.crusher_contribution_ratio_choice * 100:.2f}% contribution).\n\n"
            f"The model now contains {len(self.site_scenarios)} site scenario(s). "
            "Use Active Site Scenario above the tabs to switch inputs and outputs."
        )
        if target_errors:
            failed = ", ".join(sorted(target_errors))
            message += f"\n\n2WP build targets could not be loaded for: {failed}. Manual build entry remains available."
        elif not automatic_2wp_targets:
            message += (
                "\n\nAutomatic 2WP product-build targets were skipped. "
                "Use Product Build Settings for manual entry or load them there later."
            )
        if not restoring_project:
            QMessageBox.information(self, "BlendMaster", message)

        self.setup_stockpile_table()
        self.set_page_enabled(
            self.stockpile_tab_index,
            restoring_project,
        )
        if not restoring_project:
            self.show_page(self.guidance_schedules_tab_index)
        self.validate_form()

        if getattr(self, "agent_workflow_after_site_config", False):
            self.agent_workflow_after_site_config = False
            QTimer.singleShot(250, self.agent_workflow_apply_stockpiles)

    def handle_guidance_schedules_submit(self):
        """Apply optional schedule guidance before stockpile selection."""
        if not self.stockpile_data:
            QMessageBox.warning(
                self,
                "Guidance Schedules",
                "Submit Site Configuration before applying guidance schedules.",
            )
            return

        self.capture_guidance_schedule_controls()
        valid, validation_message = (
            self.validate_guidance_schedule_constraints()
        )
        if not valid:
            QMessageBox.warning(
                self,
                "Guidance Schedules",
                validation_message,
            )
            self.validate_form()
            return

        self.refresh_aps_stockpile_brand_map()
        self.apply_aps_brand_guidance_to_stockpile_data()
        if self.haul_cycle_file_path_choice:
            if not self.refresh_haul_cycle_routes(show_errors=True):
                self.validate_form()
                return
        else:
            self.haul_cycle_routes = {}
            self.apply_haul_cycle_routes_to_stockpile_data()

        self.setup_stockpile_table()
        self.set_page_enabled(self.stockpile_tab_index, True)
        self.save_active_scenario_state()
        self.show_page(self.stockpile_tab_index, force=True)
        self.validate_form()

    def handle_site_config_error(self, error_message):
        self.submit_button.setEnabled(True)
        if getattr(self, "agent_workflow_after_site_config", False):
            self.agent_workflow_after_site_config = False
            self.stop_agent_workflow_apply(f"Agent workflow stopped on Site Configuration fetch: {error_message}")
        self.show_error_popup(error_message)

    def setup_agent_instructions_tab(self):
        self.agent_tab = QWidget()
        self.agent_tab.setObjectName("agentInstructionsTab")
        self.agent_tab_index = self.register_page(
            "agent",
            self.tabs,
            self.agent_tab,
            "Agent",
        )
        self.agent_layout = QVBoxLayout(self.agent_tab)
        self.agent_layout.setContentsMargins(14, 12, 14, 12)
        self.agent_layout.setSpacing(10)
        self.agent_tab.setStyleSheet("""
            QWidget#agentInstructionsTab {
                background-color: #f8fafc;
            }
            QFrame#agentPanel {
                background-color: #ffffff;
                border: 1px solid #d8e0ea;
                border-radius: 6px;
            }
            QLabel#agentTitle {
                color: #172033;
                font-size: 20px;
                font-weight: 750;
            }
            QLabel#agentSubtitle,
            QLabel#agentMuted {
                color: #64748b;
                font-size: 12px;
            }
            QTextEdit,
            QTableWidget,
            QLineEdit {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
            }
            QPushButton {
                background-color: #ffffff;
                border: 1px solid #b8c4d2;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #eef7f0;
                border-color: #98d4a6;
            }
        """)

        title = QLabel("BlendMaster Agent Bridge")
        title.setObjectName("agentTitle")
        self.agent_layout.addWidget(title)

        subtitle = QLabel(
            "BlendMaster launches the local bridge. Connect Codex to the MCP endpoint, then submit a request from here."
        )
        subtitle.setObjectName("agentSubtitle")
        subtitle.setWordWrap(True)
        self.agent_layout.addWidget(subtitle)

        bridge_frame = QFrame()
        bridge_frame.setObjectName("agentPanel")
        bridge_layout = QVBoxLayout(bridge_frame)
        bridge_layout.setContentsMargins(12, 10, 12, 10)

        bridge_row = QHBoxLayout()
        self.agent_bridge_status_label = QLabel("Bridge stopped.")
        self.agent_bridge_status_label.setObjectName("agentMuted")
        self.agent_bridge_port_input = QLineEdit()
        self.agent_bridge_port_input.setText(str(getattr(self, "agent_bridge_port", 8765)))
        self.agent_bridge_port_input.setValidator(QIntValidator(1024, 65535, self))
        self.agent_bridge_port_input.setFixedWidth(80)
        self.agent_bridge_url_input = QLineEdit()
        self.agent_bridge_url_input.setReadOnly(True)
        self.agent_bridge_url_input.setText(self.agent_bridge_mcp_url())
        self.agent_bridge_url_input.setMinimumWidth(360)
        self.agent_bridge_port_input.textChanged.connect(
            lambda: self.agent_bridge_url_input.setText(self.agent_bridge_mcp_url())
        )

        self.agent_start_bridge_button = QPushButton("Start Bridge")
        self.agent_start_bridge_button.clicked.connect(self.start_agent_bridge)
        self.agent_stop_bridge_button = QPushButton("Stop Bridge")
        self.agent_stop_bridge_button.clicked.connect(self.stop_agent_bridge)

        bridge_row.addWidget(QLabel("Port:"))
        bridge_row.addWidget(self.agent_bridge_port_input)
        bridge_row.addWidget(QLabel("MCP URL:"))
        bridge_row.addWidget(self.agent_bridge_url_input, stretch=1)
        bridge_row.addWidget(self.agent_start_bridge_button)
        bridge_row.addWidget(self.agent_stop_bridge_button)
        bridge_layout.addLayout(bridge_row)
        bridge_layout.addWidget(self.agent_bridge_status_label)
        self.agent_layout.addWidget(bridge_frame)

        text_frame = QFrame()
        text_frame.setObjectName("agentPanel")
        text_layout = QHBoxLayout(text_frame)
        text_layout.setContentsMargins(12, 10, 12, 10)
        text_layout.setSpacing(10)

        story_layout = QVBoxLayout()
        story_label = QLabel("Blending Story")
        story_label.setStyleSheet("font-weight: 700;")
        self.agent_story_input = QTextEdit()
        self.agent_story_input.setPlaceholderText(
            "Describe the blending problem, practical constraints, operating habits, outputs, and pitfalls."
        )
        self.agent_story_input.setPlainText(getattr(self, "agent_story_text", ""))
        story_layout.addWidget(story_label)
        story_layout.addWidget(self.agent_story_input)

        instructions_layout = QVBoxLayout()
        instructions_label = QLabel("Run Instructions")
        instructions_label.setStyleSheet("font-weight: 700;")
        self.agent_run_instructions_input = QTextEdit()
        self.agent_run_instructions_input.setPlaceholderText(
            "Tell the agent what to try for this run and which outcomes or guardrails matter most."
        )
        self.agent_run_instructions_input.setPlainText(getattr(self, "agent_run_instructions_text", ""))
        instructions_layout.addWidget(instructions_label)
        instructions_layout.addWidget(self.agent_run_instructions_input)

        text_layout.addLayout(story_layout, stretch=1)
        text_layout.addLayout(instructions_layout, stretch=1)
        self.agent_layout.addWidget(text_frame, stretch=2)

        action_layout = QHBoxLayout()
        self.agent_submit_request_button = QPushButton("Submit Agent Request")
        self.agent_submit_request_button.clicked.connect(self.submit_agent_request)
        self.agent_apply_proposals_button = QPushButton("Apply Selected Proposals")
        self.agent_apply_proposals_button.clicked.connect(self.apply_selected_agent_proposals)
        self.agent_clear_button = QPushButton("Clear Agent Output")
        self.agent_clear_button.clicked.connect(self.clear_agent_output)
        action_layout.addWidget(self.agent_submit_request_button)
        action_layout.addWidget(self.agent_apply_proposals_button)
        action_layout.addWidget(self.agent_clear_button)
        action_layout.addStretch()
        self.agent_layout.addLayout(action_layout)

        bottom_layout = QHBoxLayout()

        proposal_layout = QVBoxLayout()
        proposal_label = QLabel("Proposed Constraints / Run Results")
        proposal_label.setStyleSheet("font-weight: 700;")
        self.agent_proposals_table = CustomTableWidget()
        self.agent_proposals_table.setColumnCount(5)
        self.agent_proposals_table.setHorizontalHeaderLabels(
            ["Apply", "Target", "Current Value", "Proposed Value", "Rationale"]
        )
        self.agent_proposals_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.agent_proposals_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        proposal_layout.addWidget(proposal_label)
        proposal_layout.addWidget(self.agent_proposals_table)

        console_layout = QVBoxLayout()
        console_label = QLabel("Agent Console")
        console_label.setStyleSheet("font-weight: 700;")
        self.agent_console_output = QTextEdit()
        self.agent_console_output.setReadOnly(True)
        console_layout.addWidget(console_label)
        console_layout.addWidget(self.agent_console_output)

        bottom_layout.addLayout(proposal_layout, stretch=2)
        bottom_layout.addLayout(console_layout, stretch=1)
        self.agent_layout.addLayout(bottom_layout, stretch=3)

        self.agent_poll_timer = QTimer(self)
        self.agent_poll_timer.setInterval(1500)
        self.agent_poll_timer.timeout.connect(self.poll_agent_request_result)
        self.update_agent_bridge_status()

    def toggle_agent_enabled(self, enabled):
        self.agent_enabled_choice = bool(enabled)
        if hasattr(self, "agent_tab_index"):
            self.set_page_enabled(self.agent_tab_index, self.agent_enabled_choice)
        if self.agent_enabled_choice:
            self.start_agent_bridge()
        else:
            self.stop_agent_bridge(silent=True)

    def agent_app_root_dir(self):
        if getattr(sys, "frozen", False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def agent_repo_dir(self):
        if getattr(sys, "frozen", False):
            return getattr(sys, "_MEIPASS", self.agent_app_root_dir())
        return self.agent_app_root_dir()

    def agent_bridge_dir(self):
        bridge_dir = os.path.join(self.agent_app_root_dir(), ".blendmaster_agent_bridge")
        os.makedirs(bridge_dir, exist_ok=True)
        return bridge_dir

    def agent_bridge_server_command(self):
        if getattr(sys, "frozen", False):
            return [sys.executable, "--agent-bridge-server"], sys.executable

        server_script = os.path.join(self.agent_repo_dir(), "GUI", "AgentBridgeServer.py")
        if not os.path.exists(server_script):
            return None, server_script
        return [sys.executable, server_script], server_script

    def agent_bridge_port_value(self):
        if hasattr(self, "agent_bridge_port_input"):
            text = self.agent_bridge_port_input.text().strip()
            if text:
                try:
                    return int(text)
                except ValueError:
                    pass
        return int(getattr(self, "agent_bridge_port", 8765) or 8765)

    def agent_bridge_base_url(self):
        return f"http://127.0.0.1:{self.agent_bridge_port_value()}"

    def agent_bridge_mcp_url(self):
        return f"{self.agent_bridge_base_url()}/mcp"

    def agent_bridge_store_file(self):
        return os.path.join(self.agent_bridge_dir(), "bridge_state.json")

    def update_agent_bridge_status(self, message=None):
        if hasattr(self, "agent_bridge_url_input"):
            self.agent_bridge_url_input.setText(self.agent_bridge_mcp_url())
        if not hasattr(self, "agent_bridge_status_label"):
            return
        if message:
            self.agent_bridge_status_label.setText(message)
            return
        if self.is_agent_bridge_healthy():
            self.agent_bridge_status_label.setText(
                f"Bridge running. In Codex custom MCP, use Streamable HTTP: {self.agent_bridge_mcp_url()}"
            )
        else:
            self.agent_bridge_status_label.setText("Bridge stopped.")

    def is_agent_bridge_healthy(self):
        try:
            response = requests.get(f"{self.agent_bridge_base_url()}/health", timeout=0.5)
            return response.ok and bool(response.json().get("ok"))
        except Exception:
            return False

    def start_agent_bridge(self):
        if not hasattr(self, "agent_bridge_port_input"):
            return
        self.agent_bridge_port = self.agent_bridge_port_value()
        if self.is_agent_bridge_healthy():
            self.update_agent_bridge_status("Bridge already running.")
            self.append_agent_console(f"Bridge already running at {self.agent_bridge_mcp_url()}.")
            return

        command_prefix, server_script = self.agent_bridge_server_command()
        if not command_prefix:
            self.append_agent_console(f"Bridge server script not found: {server_script}")
            self.update_agent_bridge_status("Bridge failed to start.")
            return

        log_path = os.path.join(self.agent_bridge_dir(), "bridge.log")
        command = command_prefix + [
            "--host",
            "127.0.0.1",
            "--port",
            str(self.agent_bridge_port),
            "--store-file",
            self.agent_bridge_store_file(),
        ]

        try:
            self.agent_bridge_log_handle = open(log_path, "a", encoding="utf-8")
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            self.agent_bridge_process = subprocess.Popen(
                command,
                cwd=self.agent_app_root_dir(),
                stdout=self.agent_bridge_log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except Exception as exc:
            self.append_agent_console(f"Failed to start bridge: {exc}")
            self.update_agent_bridge_status("Bridge failed to start.")
            return

        for _ in range(20):
            QApplication.processEvents()
            if self.is_agent_bridge_healthy():
                self.update_agent_bridge_status()
                self.append_agent_console(f"Started bridge at {self.agent_bridge_mcp_url()}.")
                self.append_agent_console("Codex custom MCP setup: select Streamable HTTP and use the MCP URL above.")
                return
            QThread.msleep(100)

        self.update_agent_bridge_status("Bridge process launched, but health check has not responded yet.")
        self.append_agent_console(f"Bridge launched. If it does not respond, check {log_path}.")

    def stop_agent_bridge(self, silent=False):
        if hasattr(self, "agent_poll_timer"):
            self.agent_poll_timer.stop()
        process = getattr(self, "agent_bridge_process", None)
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=3)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        self.agent_bridge_process = None
        log_handle = getattr(self, "agent_bridge_log_handle", None)
        if log_handle:
            try:
                log_handle.close()
            except Exception:
                pass
        self.agent_bridge_log_handle = None
        self.update_agent_bridge_status("Bridge stopped.")
        if not silent:
            self.append_agent_console("Bridge stopped.")

    def append_agent_console(self, message):
        if not hasattr(self, "agent_console_output"):
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.agent_console_output.append(f"[{timestamp}] {message}")
        scrollbar = self.agent_console_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def clear_agent_output(self):
        if hasattr(self, "agent_console_output"):
            self.agent_console_output.clear()
        if hasattr(self, "agent_proposals_table"):
            self.agent_proposals_table.setRowCount(0)
        self.agent_latest_proposals = []
        self.agent_latest_result = {}
        self.agent_current_request_id = None
        self.agent_seen_trace_count = 0

    def capture_agent_panel_state(self):
        """Keep the active agent conversation when it applies a project result."""
        return {
            "story": (
                self.agent_story_input.toPlainText()
                if hasattr(self, "agent_story_input")
                else getattr(self, "agent_story_text", "")
            ),
            "instructions": (
                self.agent_run_instructions_input.toPlainText()
                if hasattr(self, "agent_run_instructions_input")
                else getattr(self, "agent_run_instructions_text", "")
            ),
            "console": (
                self.agent_console_output.toPlainText()
                if hasattr(self, "agent_console_output")
                else ""
            ),
            "proposals": self.capture_agent_proposals_table(),
            "latest_proposals": copy.deepcopy(
                getattr(self, "agent_latest_proposals", [])
            ),
            "latest_result": copy.deepcopy(
                getattr(self, "agent_latest_result", {})
            ),
        }

    def restore_agent_panel_state(self, state):
        """Restore the agent conversation without affecting the loaded model."""
        state = state or {}
        self.agent_story_text = state.get("story", "")
        self.agent_run_instructions_text = state.get("instructions", "")
        self.agent_latest_proposals = copy.deepcopy(
            state.get("latest_proposals", []) or []
        )
        self.agent_latest_result = copy.deepcopy(state.get("latest_result", {}) or {})
        if hasattr(self, "agent_story_input"):
            self.agent_story_input.setPlainText(self.agent_story_text)
        if hasattr(self, "agent_run_instructions_input"):
            self.agent_run_instructions_input.setPlainText(
                self.agent_run_instructions_text
            )
        if hasattr(self, "agent_console_output"):
            self.agent_console_output.setPlainText(state.get("console", "") or "")
        self.restore_agent_proposals_table(state.get("proposals", []) or [])

    def capture_agent_proposals_table(self):
        if not hasattr(self, "agent_proposals_table"):
            return []
        rows = []
        for row in range(self.agent_proposals_table.rowCount()):
            values = []
            for column in range(self.agent_proposals_table.columnCount()):
                item = self.agent_proposals_table.item(row, column)
                values.append(item.text() if item is not None else "")
            apply_item = self.agent_proposals_table.item(row, 0)
            rows.append({
                "values": values,
                "checkable": bool(
                    apply_item is not None
                    and apply_item.flags() & Qt.ItemIsUserCheckable
                ),
                "check_state": int(apply_item.checkState()) if apply_item is not None else 0,
            })
        return rows

    def restore_agent_proposals_table(self, rows):
        if not hasattr(self, "agent_proposals_table"):
            return
        self.agent_proposals_table.setRowCount(0)
        for row_state in rows or []:
            if not isinstance(row_state, dict):
                continue
            row = self.agent_proposals_table.rowCount()
            self.agent_proposals_table.insertRow(row)
            values = row_state.get("values") or []
            for column in range(self.agent_proposals_table.columnCount()):
                item = QTableWidgetItem(str(values[column]) if column < len(values) else "")
                if column == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                    if row_state.get("checkable"):
                        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                        item.setCheckState(Qt.CheckState(row_state.get("check_state", 0)))
                    else:
                        item.setFlags(Qt.ItemIsEnabled)
                self.agent_proposals_table.setItem(row, column, item)
        if rows:
            self.agent_proposals_table.resizeColumnsToContents()

    def submit_agent_request(self):
        if not getattr(self, "agent_enabled_choice", False):
            QMessageBox.information(self, "BlendMaster Agent", "Enable the agent bridge from Site Configuration first.")
            return
        self.start_agent_bridge()
        if not self.is_agent_bridge_healthy():
            QMessageBox.warning(self, "BlendMaster Agent", "The agent bridge is not running.")
            return

        self.store_solver_config_inputs(show_errors=False)
        payload = {
            "story": self.agent_story_input.toPlainText() if hasattr(self, "agent_story_input") else "",
            "instructions": self.agent_run_instructions_input.toPlainText() if hasattr(self, "agent_run_instructions_input") else "",
            "context": self.build_agent_context(),
        }

        try:
            response = requests.post(f"{self.agent_bridge_base_url()}/app/request", json=payload, timeout=5)
            response.raise_for_status()
            data = response.json()
            request_item = data.get("request", {})
            self.agent_current_request_id = request_item.get("request_id")
            self.agent_seen_trace_count = 0
            self.agent_latest_proposals = []
            self.agent_proposals_table.setRowCount(0)
            self.append_agent_console(f"Submitted request {self.agent_current_request_id}. Waiting for Codex result.")
            self.agent_poll_timer.start()
        except Exception as exc:
            QMessageBox.warning(self, "BlendMaster Agent", f"Unable to submit agent request: {exc}")

    def poll_agent_request_result(self):
        request_id = getattr(self, "agent_current_request_id", None)
        if not request_id:
            self.agent_poll_timer.stop()
            return

        try:
            response = requests.get(f"{self.agent_bridge_base_url()}/app/request/{request_id}", timeout=3)
            response.raise_for_status()
            request_item = response.json().get("request", {})
        except Exception as exc:
            self.append_agent_console(f"Unable to poll bridge: {exc}")
            return

        trace = request_item.get("trace", [])
        for message in trace[getattr(self, "agent_seen_trace_count", 0):]:
            self.append_agent_console(message)
        self.agent_seen_trace_count = len(trace)

        status = request_item.get("status")
        if status == "pending":
            return

        self.agent_poll_timer.stop()
        result = request_item.get("result") or {}
        self.populate_agent_proposals(result)
        if status == "completed":
            self.append_agent_console("Agent result received.")
        else:
            self.append_agent_console("Agent request ended without a completed result.")

    def build_agent_context(self):
        selected_stockpiles = sorted(
            name for name, selected in (self.stockpile_data_use_column or {}).items() if selected
        )
        selected_amt_stockpiles = sorted(
            name for name, selected in (self.stockpile_data_AMT_column or {}).items() if selected
        )
        context = {
            "agent_result_contract": {
                "preferred_apply_path": (
                    "Return broad workflow sections when the app should visibly apply a run through the UI: "
                    "site_configuration, selected_stockpiles, selected_amt_stockpiles, amt_chunking, "
                    "solver_config, product_build_settings, and calendar_rates. If any stockpile is selected as AMT, "
                    "hex_sequence_table is required. The app cannot submit AMT stockpiles from agent output "
                    "without the generated chunk rows. site_configuration must identify one explicit "
                    "hub / mine / opf / crusher scenario. When that OPF has multiple crushers it must also "
                    "provide crusher_contribution_ratio (0 to 1) or crusher_contribution_ratio_percent (0 to 100)."
                ),
                "direct_tip_movement_rule_contract": (
                    "Direct-tip permission is explicit. Return direct_tip_movement_rules as a list of objects "
                    "with grade_block_source (the Source.NamePart2 substring) and crusher_destination "
                    "(the APS Destination.Name). A grade block is not eligible for direct tip unless a rule "
                    "matches both its full source name and this scenario's operating crusher."
                ),
                "product_build_settings_contract": (
                    "For product build targeting, include product_build_settings as a list of rows with brand, "
                    "target_tonnes, target_fe_min, target_fe_max, target_si_min, target_si_max, "
                    "target_al_min, target_al_max, target_p_min, target_p_max, target_mn_min, and target_mn_max. "
                    "The app applies these rows through the Product Build Settings tab before Calendar."
                ),
                "hex_sequence_table_contract": (
                    "For AMT workflows, include hex_sequence_table as a list of chunk dictionaries that can be "
                    "loaded into the AMT map and submitted downstream. Each row should include footprint, "
                    "sequence, hex chunk id, balance/tonnes, weighted grade fields, hex_count, chunk_size, "
                    "and member_hexes. Do not return placeholder rows containing only footprint/sequence/chunk_id; "
                    "those rows cannot build AMT maps or opening balances. If the full table is large, return "
                    "hex_sequence_table_file pointing to a JSON file containing these complete rows."
                ),
                "fallback_apply_path": (
                    "Return project_file/project_state only for complete restore, or proposed_constraints "
                    "only for small leaf-level edits. Use targets such as "
                    "solver_config.blend_option_timeout_seconds, calendar_rates.crusher_rate.Period_1, "
                    "selected_stockpiles, product_build_settings, or amt_chunking.STOCKPILE.chunk_reclaim_hours."
                ),
            },
            "site_configuration": {
                "hub": getattr(self, "hub_input_choice", None),
                "mine": getattr(self, "mine_input_choice", None),
                "opf": getattr(self, "opf_input_choice", None),
                "crusher": getattr(self, "crusher_input_choice", None),
                "crusher_contribution_ratio": getattr(
                    self, "crusher_contribution_ratio_choice", 1.0
                ),
                "crusher_ratio_mode": getattr(
                    self, "crusher_ratio_mode_choice", "manual"
                ),
                "aps_ratio_crushers": getattr(
                    self, "aps_ratio_crusher_choices", []
                ),
                "start_time": getattr(self, "start_time_choice", None),
                "aps_mining_csv": getattr(self, "file_path_choice", ""),
                "two_wp_mining_csv": getattr(self, "file_path_choice", ""),
                "twenty_four_hour_mining_csv": getattr(
                    self, "file_path_24hr_choice", ""
                ),
                "selected_24hr_expit_agents": getattr(
                    self, "selected_24hr_expit_agents", []
                ),
                "haul_cycle_csv": getattr(
                    self, "haul_cycle_file_path_choice", ""
                ),
                "selected_haul_cycle_crushers": getattr(
                    self, "selected_haul_cycle_crushers", []
                ),
                "product_brands": getattr(self, "product_brand_labels_choice", self.default_product_brand_labels()),
                "auto_load_2wp_targets": getattr(
                    self,
                    "auto_load_2wp_targets_choice",
                    True,
                ),
                "reevaluate_aps_direct_tip": getattr(self, "reevaluate_aps_direct_tip_choice", False),
                "selected_aps_crushers": getattr(self, "aps_direct_tip_crusher_choice", []),
                "direct_tip_grade_block_sources": getattr(
                    self, "direct_tip_grade_block_sources", []
                ),
                "direct_tip_crusher_destinations": getattr(
                    self, "direct_tip_crusher_destinations", []
                ),
                "direct_tip_movement_rules": getattr(
                    self, "direct_tip_movement_rules", []
                ),
                "blend_mode": getattr(self, "blend_mode_choice", None),
            },
            "blend_settings": {
                "min_stockpiles": getattr(self, "min_stockpiles", None),
                "max_stockpiles": getattr(self, "max_stockpiles", None),
                "min_stockpile_contribution_ratio": getattr(self, "min_stockpile_contribution_ratio", None),
            },
            "solver_configuration": self.normalized_solver_config(getattr(self, "solver_config", {})),
            "selected_stockpiles": selected_stockpiles,
            "selected_amt_stockpiles": selected_amt_stockpiles,
            "product_build_settings": getattr(self, "product_build_settings", []),
            "calendar_inputs": getattr(self, "calendar_inputs", {}),
            "latest_decision_trace": (
                self.decision_output.toPlainText()[-8000:]
                if hasattr(self, "decision_output")
                else ""
            ),
        }
        return self.make_agent_json_safe(context)

    def make_agent_json_safe(self, value, depth=0):
        if depth > 6:
            return str(value)
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, datetime):
            return value.isoformat(timespec="seconds")
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if isinstance(value, pd.DataFrame):
            return {
                "columns": list(value.columns),
                "row_count": len(value),
                "sample_rows": self.make_agent_json_safe(value.head(25).to_dict("records"), depth + 1),
            }
        if isinstance(value, dict):
            return {str(key): self.make_agent_json_safe(item, depth + 1) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            values = list(value)
            limited = values[:100]
            result = [self.make_agent_json_safe(item, depth + 1) for item in limited]
            if len(values) > len(limited):
                result.append(f"... {len(values) - len(limited)} more item(s)")
            return result
        return str(value)

    def populate_agent_proposals(self, result):
        if not hasattr(self, "agent_proposals_table"):
            return
        self.agent_proposals_table.setRowCount(0)
        self.agent_latest_proposals = []

        summary = result.get("summary") if isinstance(result, dict) else None
        if summary:
            self.append_agent_console(f"Summary: {summary}")
        for message in (result.get("messages", []) if isinstance(result, dict) else []):
            self.append_agent_console(message)

        self.agent_latest_result = result if isinstance(result, dict) else {}
        workflow_payload = self.extract_agent_workflow_payload(self.agent_latest_result)
        if workflow_payload:
            row = self.agent_proposals_table.rowCount()
            self.agent_proposals_table.insertRow(row)
            apply_item = QTableWidgetItem()
            apply_item.setFlags(apply_item.flags() | Qt.ItemIsUserCheckable)
            apply_item.setCheckState(Qt.Checked)
            apply_item.setTextAlignment(Qt.AlignCenter)
            self.agent_proposals_table.setItem(row, 0, apply_item)
            self.agent_proposals_table.setItem(row, 1, QTableWidgetItem("agent_workflow"))
            self.agent_proposals_table.setItem(row, 2, QTableWidgetItem("Fresh app workflow"))
            self.agent_proposals_table.setItem(
                row,
                3,
                QTableWidgetItem(self.summarize_agent_workflow_payload(workflow_payload)),
            )
            self.agent_proposals_table.setItem(
                row,
                4,
                QTableWidgetItem(
                    "Drive the normal UI sequence: Site Configuration, "
                    "Guidance Schedules, Stockpiles, AMT if needed, Solver "
                    "Configuration, Product Build Settings, Decision Levers, "
                    "Calendar."
                ),
            )
            self.agent_latest_proposals.append({
                "target": "agent_workflow",
                "value": workflow_payload,
                "rationale": "Drive the app through the normal UI workflow.",
                "applyable": True,
                "action": "agent_workflow",
                "workflow_payload": workflow_payload,
            })

        project_load_source = self.extract_agent_project_load_source(self.agent_latest_result)
        if project_load_source:
            source_kind, source_payload, source_label = project_load_source
            row = self.agent_proposals_table.rowCount()
            self.agent_proposals_table.insertRow(row)
            apply_item = QTableWidgetItem()
            apply_item.setFlags(apply_item.flags() | Qt.ItemIsUserCheckable)
            apply_item.setCheckState(Qt.Unchecked if workflow_payload else Qt.Checked)
            apply_item.setTextAlignment(Qt.AlignCenter)
            self.agent_proposals_table.setItem(row, 0, apply_item)
            self.agent_proposals_table.setItem(row, 1, QTableWidgetItem("project_load"))
            self.agent_proposals_table.setItem(row, 2, QTableWidgetItem("Current app state"))
            self.agent_proposals_table.setItem(row, 3, QTableWidgetItem(source_label))
            self.agent_proposals_table.setItem(
                row,
                4,
                QTableWidgetItem("Restore this agent result through the same path as Load Project."),
            )
            self.agent_latest_proposals.append({
                "target": "project_load",
                "value": source_label,
                "rationale": "Restore through project load path.",
                "applyable": True,
                "action": "project_load",
                "source_kind": source_kind,
                "source_payload": source_payload,
            })

        proposals = []
        if isinstance(result, dict):
            proposals = result.get("proposed_constraints") or result.get("constraints") or []
        if isinstance(proposals, dict):
            proposals = [
                {"target": key, "value": value, "rationale": ""}
                for key, value in proposals.items()
            ]
        if not isinstance(proposals, list):
            proposals = []
        proposals = self.expand_agent_proposals(proposals)

        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue
            target = str(proposal.get("target") or proposal.get("name") or "").strip()
            if not target:
                continue
            value = proposal.get("value", proposal.get("proposed_value", ""))
            rationale = str(proposal.get("rationale", proposal.get("reason", "")))
            current_value = self.get_agent_target_current_value(target)
            applyable = self.is_known_agent_target(target)
            workflow_managed = bool(workflow_payload and self.is_agent_workflow_target(target))

            row = self.agent_proposals_table.rowCount()
            self.agent_proposals_table.insertRow(row)
            apply_item = QTableWidgetItem()
            if applyable:
                apply_item.setFlags(apply_item.flags() | Qt.ItemIsUserCheckable)
                apply_item.setCheckState(Qt.Checked)
            elif workflow_managed:
                apply_item.setText("Workflow")
                apply_item.setFlags(Qt.ItemIsEnabled)
            else:
                apply_item.setText("Review")
                apply_item.setFlags(Qt.ItemIsEnabled)
            apply_item.setTextAlignment(Qt.AlignCenter)
            self.agent_proposals_table.setItem(row, 0, apply_item)
            self.agent_proposals_table.setItem(row, 1, QTableWidgetItem(target))
            self.agent_proposals_table.setItem(row, 2, QTableWidgetItem(self.format_agent_value(current_value)))
            self.agent_proposals_table.setItem(row, 3, QTableWidgetItem(self.format_agent_value(value)))
            self.agent_proposals_table.setItem(row, 4, QTableWidgetItem(rationale))
            self.agent_latest_proposals.append(
                {"target": target, "value": value, "rationale": rationale, "applyable": applyable}
            )

        self.agent_proposals_table.resizeColumnsToContents()
        if not self.agent_latest_proposals:
            self.append_agent_console("No proposed constraints were returned.")

    def extract_agent_workflow_payload(self, result):
        if not isinstance(result, dict):
            return {}

        proposals = self.agent_result_proposal_items(result)
        target_values = {}
        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue
            target = str(proposal.get("target") or proposal.get("name") or "").strip()
            if not target:
                continue
            target_values[target.lower()] = proposal.get("value", proposal.get("proposed_value", ""))

        def section(keys, dict_only=True):
            for key in keys:
                if key in result:
                    value = result.get(key)
                    if not dict_only or isinstance(value, dict):
                        return copy.deepcopy(value)
            for key in keys:
                value = target_values.get(str(key).lower())
                if value is not None and (not dict_only or isinstance(value, dict)):
                    return copy.deepcopy(value)
            return None

        payload = {}

        site_config = section(["site_configuration", "site_config"], dict_only=True)
        if site_config:
            payload["site_configuration"] = site_config

        selected_value = section(["selected_stockpiles", "stockpiles"], dict_only=False)
        selected_amt_value = section(["selected_amt_stockpiles", "amt_stockpiles"], dict_only=False)
        selected_stockpiles = self.normalized_agent_selected_stockpile_payload(selected_value, selected_amt_value)
        if selected_stockpiles:
            payload["selected_stockpiles"] = selected_stockpiles

        solver_config = section(["solver_configuration", "solver_config"], dict_only=True)
        if not solver_config:
            solver_config = self.nested_agent_target_values(target_values, "solver_config.")
        blend_settings = section(["blend_settings"], dict_only=True)
        if not blend_settings:
            blend_settings = self.nested_agent_target_values(target_values, "blend_settings.")
        if solver_config:
            payload["solver_configuration"] = solver_config
        if blend_settings:
            payload["blend_settings"] = blend_settings

        product_build_settings = section(
            ["product_build_settings", "product_builds", "product_build_settings_tab"],
            dict_only=False,
        )
        if isinstance(product_build_settings, dict):
            product_build_settings = (
                product_build_settings.get("builds")
                or product_build_settings.get("rows")
                or product_build_settings.get("product_builds")
                or []
            )
        if isinstance(product_build_settings, list):
            payload["product_build_settings"] = self.normalized_agent_product_build_settings(
                product_build_settings
            )

        calendar_rates = section(["calendar_rates", "calendar"], dict_only=True)
        if not calendar_rates:
            calendar_rates = self.nested_agent_target_values(target_values, "calendar_rates.")
        if calendar_rates:
            payload["calendar_rates"] = calendar_rates

        amt_chunking = section(["amt_chunking", "AMT_chunk_settings"], dict_only=True)
        if not amt_chunking:
            amt_chunking = self.nested_agent_target_values(target_values, "amt_chunking.")
        if not amt_chunking:
            amt_chunking = self.amt_chunking_from_agent_results(
                result.get("amt_chunk_results") or target_values.get("amt_chunk_results")
            )
        if amt_chunking:
            payload["amt_chunking"] = amt_chunking

        hex_sequence_table = section(
            ["hex_sequence_table", "hex_sequence_table_argument", "amt_hex_sequence", "amt_chunk_sequence"],
            dict_only=False,
        )
        hex_sequence_table_file = section(
            ["hex_sequence_table_file", "amt_hex_sequence_file", "amt_chunk_sequence_file"],
            dict_only=False,
        )
        file_hex_sequence_table = self.load_agent_hex_sequence_table_file(hex_sequence_table_file)
        if file_hex_sequence_table and not self.agent_hex_sequence_table_has_required_fields(hex_sequence_table):
            hex_sequence_table = file_hex_sequence_table
        if isinstance(hex_sequence_table, list):
            payload["hex_sequence_table"] = self.normalized_agent_hex_sequence_table(hex_sequence_table)
        if hex_sequence_table_file:
            payload["hex_sequence_table_file"] = str(hex_sequence_table_file)

        return payload

    def agent_result_proposal_items(self, result):
        proposals = result.get("proposed_constraints") or result.get("constraints") or []
        if isinstance(proposals, dict):
            return [
                {"target": key, "value": value, "rationale": ""}
                for key, value in proposals.items()
            ]
        if isinstance(proposals, list):
            return proposals
        return []

    @staticmethod
    def nested_agent_target_values(target_values, prefix):
        nested = {}
        prefix = str(prefix).lower()
        for target, value in target_values.items():
            if not target.startswith(prefix):
                continue
            path = [part for part in target[len(prefix):].split(".") if part]
            if not path:
                continue
            cursor = nested
            for part in path[:-1]:
                cursor = cursor.setdefault(part, {})
            cursor[path[-1]] = copy.deepcopy(value)
        return nested

    def normalized_agent_product_build_settings(self, product_build_settings):
        if isinstance(product_build_settings, dict):
            product_build_settings = (
                product_build_settings.get("builds")
                or product_build_settings.get("rows")
                or product_build_settings.get("product_builds")
                or []
            )
        if not isinstance(product_build_settings, list):
            return []

        def first_value(mapping, keys, default=None):
            for key in keys:
                if isinstance(mapping, dict) and key in mapping and mapping.get(key) not in (None, ""):
                    return mapping.get(key)
            return default

        def nested_grade_value(setting, grade, bound, default):
            grade_key = grade.lower()
            direct_keys = [
                f"target_{grade_key}_{bound}",
                f"{grade_key}_{bound}",
                f"grade_{grade_key}_{bound}",
                f"target_grade_{grade_key}_{bound}",
            ]
            direct_value = first_value(setting, direct_keys)
            if direct_value not in (None, ""):
                return direct_value

            for grades_key in ("grades", "grade_targets", "target_grades"):
                grades = setting.get(grades_key) if isinstance(setting, dict) else None
                if not isinstance(grades, dict):
                    continue
                grade_entry = (
                    grades.get(grade_key)
                    or grades.get(grade_key.upper())
                    or grades.get(grade.capitalize())
                )
                if isinstance(grade_entry, dict):
                    value = first_value(grade_entry, [bound, "minimum" if bound == "min" else "maximum"])
                    if value not in (None, ""):
                        return value
                elif grade_entry not in (None, ""):
                    return grade_entry
            return default

        normalized = []
        brand_counts = {}
        for index, setting in enumerate(product_build_settings):
            if not isinstance(setting, dict):
                continue
            brand = str(first_value(setting, ["brand", "brand_label", "product_brand"], "") or "").strip().upper()
            if brand:
                brand_counts[brand] = brand_counts.get(brand, 0) + 1
                default_build_name = f"{brand} Build {brand_counts[brand]}"
            else:
                default_build_name = f"Build {index + 1}"
            row = {
                "build_id": int(first_value(setting, ["build_id", "id", "sequence"], index + 1) or index + 1),
                "build_name": default_build_name,
                "brand": brand,
                "target_tonnes": first_value(setting, ["target_tonnes", "tonnes", "target_wmt", "target_quantity"], 0),
            }
            for grade in ["fe", "si", "al", "p", "mn"]:
                row[f"target_{grade}_min"] = nested_grade_value(setting, grade, "min", 0)
                row[f"target_{grade}_max"] = nested_grade_value(setting, grade, "max", 100)
            normalized.append(row)
        return normalized

    def normalized_agent_selected_stockpile_payload(self, selected_value, selected_amt_value=None):
        use_names = []
        amt_names = []

        if isinstance(selected_value, dict):
            use_names = (
                selected_value.get("use")
                or selected_value.get("selected")
                or selected_value.get("stockpiles")
                or selected_value.get("inventory")
                or []
            )
            amt_names = selected_value.get("amt") or selected_value.get("selected_amt") or []
        elif selected_value is not None:
            use_names = selected_value

        if selected_amt_value is not None:
            amt_names = selected_amt_value

        use_names = self.agent_value_to_list(use_names)
        amt_names = self.agent_value_to_list(amt_names)
        if not use_names and amt_names:
            use_names = list(amt_names)

        result = {}
        if use_names:
            result["use"] = use_names
        if amt_names:
            result["amt"] = amt_names
        return result

    @staticmethod
    def agent_value_to_list(value):
        if value is None or value == "":
            return []
        if isinstance(value, (list, tuple, set)):
            return [item for item in value if str(item).strip()]
        return [value]

    def amt_chunking_from_agent_results(self, chunk_results):
        if not isinstance(chunk_results, list):
            return {}
        chunking = {}
        for item in chunk_results:
            if not isinstance(item, dict):
                continue
            stockpile = item.get("stockpile") or item.get("footprint") or item.get("name")
            if not stockpile:
                continue
            settings = {}
            if item.get("average_reclaim_rate") is not None:
                settings["average_reclaim_rate_tph"] = item.get("average_reclaim_rate")
            if item.get("average_reclaim_rate_tph") is not None:
                settings["average_reclaim_rate_tph"] = item.get("average_reclaim_rate_tph")
            if item.get("chunk_reclaim_hours") is not None:
                settings["chunk_reclaim_hours"] = item.get("chunk_reclaim_hours")
            if settings:
                chunking[str(stockpile)] = settings
        return chunking

    def load_agent_hex_sequence_table_file(self, path_value):
        if not path_value:
            return []
        path = os.path.abspath(os.path.expanduser(str(path_value)))
        if not os.path.exists(path):
            self.append_agent_console(f"hex_sequence_table_file was supplied but not found: {path}")
            return []
        try:
            if path.lower().endswith(".json"):
                with open(path, "r", encoding="utf-8") as file:
                    data = json.load(file)
                if isinstance(data, dict):
                    data = data.get("hex_sequence_table") or data.get("rows") or data.get("data") or []
                return data if isinstance(data, list) else []
            if path.lower().endswith(".csv"):
                return pd.read_csv(path).to_dict("records")
        except Exception as exc:
            self.append_agent_console(f"Unable to load hex_sequence_table_file {path}: {exc}")
        return []

    def normalized_agent_hex_sequence_table(self, rows):
        if not isinstance(rows, list):
            return []
        normalized_rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            normalized = copy.deepcopy(row)
            footprint = (
                normalized.get("footprint")
                or normalized.get("stockpile")
                or normalized.get("stockpile_name")
                or normalized.get("source")
                or normalized.get("source_id")
            )
            sequence = normalized.get("sequence") or normalized.get("chunk_sequence") or normalized.get("chunk_number")
            if footprint is not None:
                normalized["footprint"] = str(footprint)
            if sequence is not None:
                try:
                    normalized["sequence"] = int(float(sequence))
                except (TypeError, ValueError):
                    normalized["sequence"] = sequence

            chunk_hex = (
                normalized.get("hex")
                or normalized.get("chunk_id")
                or normalized.get("chunk")
                or normalized.get("chunk_name")
            )
            if chunk_hex is None and footprint is not None and sequence is not None:
                try:
                    chunk_hex = f"{footprint}_CHUNK_{int(float(sequence)):03d}"
                except (TypeError, ValueError):
                    chunk_hex = f"{footprint}_CHUNK_{sequence}"
            if chunk_hex is not None:
                normalized["hex"] = str(chunk_hex)

            balance = self.first_agent_numeric_value(
                normalized,
                ["balance", "tonnes", "total_tonnes", "chunk_tonnes", "chunk_balance", "source_opening_balance"],
            )
            if balance is not None:
                normalized["balance"] = balance

            grade_aliases = {
                "grade_fe": ["grade_fe", "fe", "fe_grade", "grade_fe_pct", "Fe Grade", "Grade Fe (%)"],
                "grade_si": ["grade_si", "si", "si_grade", "grade_si_pct", "Si Grade", "Grade Si (%)"],
                "grade_al": ["grade_al", "al", "al_grade", "grade_al_pct", "Al Grade", "Grade Al (%)"],
                "grade_p": ["grade_p", "p", "p_grade", "grade_p_pct", "P Grade", "Grade P (%)"],
                "grade_mn": ["grade_mn", "mn", "mn_grade", "grade_mn_pct", "Mn Grade", "Grade Mn (%)"],
            }
            for target, aliases in grade_aliases.items():
                value = self.first_agent_numeric_value(normalized, aliases)
                if value is not None:
                    normalized[target] = value

            member_hexes = (
                normalized.get("member_hexes")
                or normalized.get("member_hex_ids")
                or normalized.get("hexes")
                or normalized.get("member_hex")
                or normalized.get("hex_members")
                or normalized.get("members")
            )
            if isinstance(member_hexes, list):
                normalized["member_hexes"] = ",".join(str(item) for item in member_hexes if str(item).strip())
            elif member_hexes is not None:
                normalized["member_hexes"] = str(member_hexes)

            if normalized.get("hex_count") is None and normalized.get("member_hexes"):
                normalized["hex_count"] = len([item for item in str(normalized["member_hexes"]).split(",") if item.strip()])

            normalized_rows.append(normalized)
        return normalized_rows

    @staticmethod
    def first_agent_numeric_value(row, keys):
        for key in keys:
            if key not in row:
                continue
            value = row.get(key)
            if value in (None, ""):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    def agent_hex_sequence_table_has_required_fields(self, rows):
        valid, _ = self.validate_agent_hex_sequence_table(rows)
        return valid

    def validate_agent_hex_sequence_table(self, rows):
        rows = self.normalized_agent_hex_sequence_table(rows)
        if not rows:
            return False, "hex_sequence_table is empty."

        missing = {
            "footprint": 0,
            "sequence": 0,
            "hex": 0,
            "balance": 0,
            "member_hexes": 0,
            "grade_fe": 0,
            "grade_si": 0,
            "grade_al": 0,
            "grade_p": 0,
            "grade_mn": 0,
        }
        total_positive_balance = 0.0
        for row in rows:
            if not row.get("footprint"):
                missing["footprint"] += 1
            if row.get("sequence") in (None, ""):
                missing["sequence"] += 1
            if not row.get("hex"):
                missing["hex"] += 1
            balance = self.first_agent_numeric_value(row, ["balance"])
            if balance is None:
                missing["balance"] += 1
            else:
                total_positive_balance += max(balance, 0)
            if not str(row.get("member_hexes") or "").strip():
                missing["member_hexes"] += 1
            for grade_key in ("grade_fe", "grade_si", "grade_al", "grade_p", "grade_mn"):
                if self.first_agent_numeric_value(row, [grade_key]) is None:
                    missing[grade_key] += 1

        missing_parts = [
            f"{field} missing in {count} row(s)"
            for field, count in missing.items()
            if count
        ]
        if total_positive_balance <= 0:
            missing_parts.append("total positive balance is 0")
        if missing_parts:
            return False, "; ".join(missing_parts)
        return True, ""

    def summarize_agent_workflow_payload(self, payload):
        parts = []
        if payload.get("site_configuration"):
            parts.append("Site Config")
        selected = payload.get("selected_stockpiles") or {}
        if selected:
            use_count = len(selected.get("use") or [])
            amt_count = len(selected.get("amt") or [])
            parts.append(f"Stockpiles ({use_count} use, {amt_count} AMT)")
        if payload.get("amt_chunking") or payload.get("hex_sequence_table"):
            if payload.get("hex_sequence_table"):
                valid_hex_table, _ = self.validate_agent_hex_sequence_table(payload.get("hex_sequence_table"))
                parts.append("AMT chunks ready" if valid_hex_table else "AMT chunks incomplete")
            else:
                parts.append("AMT settings only")
        if payload.get("solver_configuration") or payload.get("blend_settings"):
            parts.append("Solver Config")
        product_builds = payload.get("product_build_settings") or []
        if product_builds:
            parts.append(f"Product Builds ({len(product_builds)})")
        if payload.get("calendar_rates"):
            parts.append("Calendar")
        return " -> ".join(parts) if parts else "No workflow sections"

    def is_agent_workflow_target(self, target):
        target_text = str(target or "").strip()
        target_key = target_text.lower()
        if target_key in {
            "site_configuration",
            "site_config",
            "selected_stockpiles",
            "selected_amt_stockpiles",
            "stockpiles",
            "amt_stockpiles",
            "solver_configuration",
            "solver_config",
            "blend_settings",
            "product_build_settings",
            "product_builds",
            "product_build_settings_tab",
            "calendar_rates",
            "calendar",
            "amt_chunking",
            "hex_sequence_table",
            "hex_sequence_table_file",
        }:
            return True
        return target_text.startswith((
            "solver_config.",
            "blend_settings.",
            "product_build_settings.",
            "calendar_rates.",
            "calendar.",
            "amt_chunking.",
        ))

    def extract_agent_project_load_source(self, result):
        if not isinstance(result, dict):
            return None

        project_file = (
            result.get("project_file")
            or result.get("project_path")
            or result.get("prj_file")
            or result.get("blendmaster_project_file")
        )
        if project_file:
            return ("project_file", project_file, str(project_file))

        project_state = result.get("project_state") or result.get("blendmaster_project_state")
        if isinstance(project_state, dict):
            return ("project_state", project_state, "Embedded project state")

        proposals = result.get("proposed_constraints") or result.get("constraints") or []
        if isinstance(proposals, dict):
            proposals = [
                {"target": key, "value": value}
                for key, value in proposals.items()
            ]
        if not isinstance(proposals, list):
            return None

        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue
            target = str(proposal.get("target") or proposal.get("name") or "").strip().lower()
            value = proposal.get("value", proposal.get("proposed_value", ""))
            if target in {"project_file", "project_path", "prj_file", "blendmaster_project_file"} and value:
                return ("project_file", value, str(value))
            if target in {"project_state", "blendmaster_project_state"} and isinstance(value, dict):
                return ("project_state", value, "Embedded project state")

        return None

    def expand_agent_proposals(self, proposals):
        expanded = []
        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue
            target = str(proposal.get("target") or proposal.get("name") or "").strip()
            value = proposal.get("value", proposal.get("proposed_value", ""))
            rationale = str(proposal.get("rationale", proposal.get("reason", "")))
            target_key = target.strip().lower()

            if target_key in {"solver_configuration", "solver_config"} and isinstance(value, dict):
                expanded.extend(self.expand_agent_solver_configuration(value, rationale))
                continue

            if target_key == "blend_settings" and isinstance(value, dict):
                for key, item_value in value.items():
                    expanded.append({
                        "target": f"blend_settings.{key}",
                        "value": item_value,
                        "rationale": rationale,
                    })
                continue

            if target_key == "calendar_rates" and isinstance(value, dict):
                expanded.extend(self.expand_agent_calendar_rates(value, rationale))
                continue

            if target_key == "amt_chunking" and isinstance(value, dict):
                expanded.extend(self.expand_agent_amt_chunking(value, rationale))
                continue

            expanded.append(proposal)
        return expanded

    def expand_agent_solver_configuration(self, solver_values, rationale):
        expanded = []
        for key, value in solver_values.items():
            if key == "contaminant_thresholds" and isinstance(value, dict):
                for contaminant, threshold in value.items():
                    expanded.append({
                        "target": f"solver_config.contaminant_thresholds.{str(contaminant).lower()}",
                        "value": threshold,
                        "rationale": rationale,
                    })
                continue
            expanded.append({
                "target": f"solver_config.{key}",
                "value": value,
                "rationale": rationale,
            })
        return expanded

    def expand_agent_calendar_rates(self, calendar_values, rationale):
        expanded = []
        for raw_key, value in calendar_values.items():
            normalized_raw_key = str(raw_key or "").strip().lower().replace(" ", "_")

            if normalized_raw_key in {
                "direct_tip_ratio",
                "direct_tip",
                "crusher_direct_tip_ratio",
                "crusher_direct_feed_ratio",
            } and isinstance(value, dict):
                for bound_key, bound_value in value.items():
                    normalized_bound = str(bound_key or "").strip().lower().replace(" ", "_")
                    if normalized_bound in {"min", "minimum"}:
                        expanded.extend(self.expand_agent_calendar_rates({"direct_tip_ratio_min": bound_value}, rationale))
                    elif normalized_bound in {"max", "maximum"}:
                        expanded.extend(self.expand_agent_calendar_rates({"direct_tip_ratio_max": bound_value}, rationale))
                continue

            if normalized_raw_key in {"grade_limits", "grade_targets", "target_grades", "grades"} and isinstance(value, dict):
                for grade_key, grade_value in value.items():
                    normalized_grade = str(grade_key or "").strip().lower().replace("grade_", "")
                    if normalized_grade not in {"fe", "si", "al", "p", "mn"}:
                        continue
                    if isinstance(grade_value, dict):
                        for bound_key, bound_value in grade_value.items():
                            normalized_bound = str(bound_key or "").strip().lower().replace(" ", "_")
                            if normalized_bound in {"min", "minimum"}:
                                expanded.extend(self.expand_agent_calendar_rates({f"{normalized_grade}_min": bound_value}, rationale))
                            elif normalized_bound in {"max", "maximum"}:
                                expanded.extend(self.expand_agent_calendar_rates({f"{normalized_grade}_max": bound_value}, rationale))
                    else:
                        expanded.extend(self.expand_agent_calendar_rates({normalized_grade: grade_value}, rationale))
                continue

            row_key = self.agent_calendar_key_alias(raw_key)
            if not row_key:
                expanded.append({
                    "target": f"calendar_rates.{raw_key}",
                    "value": value,
                    "rationale": rationale,
                })
                continue

            if isinstance(value, dict):
                for raw_period, period_value in value.items():
                    period = self.agent_period_key_alias(raw_period)
                    if not period:
                        expanded.append({
                            "target": f"calendar_rates.{row_key}.{raw_period}",
                            "value": period_value,
                            "rationale": rationale,
                        })
                        continue
                    expanded.append({
                        "target": f"calendar_rates.{row_key}.{period}",
                        "value": period_value,
                        "rationale": rationale,
                    })
            else:
                for period in ["Preplan", "Period_1", "Period_2"]:
                    expanded.append({
                        "target": f"calendar_rates.{row_key}.{period}",
                        "value": value,
                        "rationale": rationale,
                    })
        return expanded

    def expand_agent_amt_chunking(self, chunking_values, rationale):
        expanded = []
        for stockpile, settings in chunking_values.items():
            if not isinstance(settings, dict):
                expanded.append({
                    "target": f"amt_chunking.{stockpile}",
                    "value": settings,
                    "rationale": rationale,
                })
                continue
            for key, value in settings.items():
                normalized_key = str(key).strip().lower()
                if normalized_key in {"average_reclaim_rate", "average_reclaim_rate_tph", "average_reclaim_rate_t/h", "reclaim_rate_tph"}:
                    field = "average_reclaim_rate_tph"
                elif normalized_key in {"chunk_reclaim_hours", "chunk_hours", "reclaim_hours"}:
                    field = "chunk_reclaim_hours"
                else:
                    field = normalized_key
                expanded.append({
                    "target": f"amt_chunking.{stockpile}.{field}",
                    "value": value,
                    "rationale": rationale,
                })
        return expanded

    def format_agent_value(self, value):
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, default=str)
        return "" if value is None else str(value)

    def normalized_agent_target(self, target):
        target = str(target or "").strip()
        if target.startswith("solver_config."):
            return target.split(".", 1)[1]
        if target.startswith("blend_settings."):
            return target.split(".", 1)[1]
        return target

    def agent_calendar_key_alias(self, key):
        normalized = str(key or "").strip().lower().replace(" ", "_")
        normalized = normalized.replace(".", "_")
        normalized = normalized.replace("(t/h)", "").replace("tph", "tph")
        aliases = {
            "crusher_rate": "crusher_rate",
            "crusher_rate_tph": "crusher_rate",
            "crusher_rate_output": "crusher_rate",
            "reclaim_rate": "reclaim_equipment_max_reclaim_rate",
            "reclaim_rate_tph": "reclaim_equipment_max_reclaim_rate",
            "max_reclaim_rate": "reclaim_equipment_max_reclaim_rate",
            "max_reclaim_rate_tph": "reclaim_equipment_max_reclaim_rate",
            "reclaim_equipment_max_reclaim_rate": "reclaim_equipment_max_reclaim_rate",
            "direct_tip_ratio_minimum": "crusher_direct_tip_ratio_min",
            "direct_tip_ratio_min": "crusher_direct_tip_ratio_min",
            "direct_tip_ratio_min_min": "crusher_direct_tip_ratio_min",
            "direct_tip_ratio_minimum_min": "crusher_direct_tip_ratio_min",
            "direct_tip_ratio_minimum_minimum": "crusher_direct_tip_ratio_min",
            "direct_tip_ratio_minimum_value": "crusher_direct_tip_ratio_min",
            "direct_tip_minimum": "crusher_direct_tip_ratio_min",
            "direct_tip_min": "crusher_direct_tip_ratio_min",
            "direct_tip_min_value": "crusher_direct_tip_ratio_min",
            "direct_tip_ratio_min_value": "crusher_direct_tip_ratio_min",
            "direct_tip_ratio_min_preplan": "crusher_direct_tip_ratio_min",
            "crusher_direct_feed_ratio_min": "crusher_direct_tip_ratio_min",
            "crusher_direct_feed_ratio_minimum": "crusher_direct_tip_ratio_min",
            "crusher_direct_tip_ratio_min": "crusher_direct_tip_ratio_min",
            "direct_tip_ratio_maximum": "crusher_direct_tip_ratio_max",
            "direct_tip_ratio_max": "crusher_direct_tip_ratio_max",
            "direct_tip_ratio_max_max": "crusher_direct_tip_ratio_max",
            "direct_tip_ratio_maximum_max": "crusher_direct_tip_ratio_max",
            "direct_tip_ratio_maximum_maximum": "crusher_direct_tip_ratio_max",
            "direct_tip_ratio_maximum_value": "crusher_direct_tip_ratio_max",
            "direct_tip_maximum": "crusher_direct_tip_ratio_max",
            "direct_tip_max": "crusher_direct_tip_ratio_max",
            "direct_tip_max_value": "crusher_direct_tip_ratio_max",
            "direct_tip_ratio_max_value": "crusher_direct_tip_ratio_max",
            "crusher_direct_feed_ratio_max": "crusher_direct_tip_ratio_max",
            "crusher_direct_feed_ratio_maximum": "crusher_direct_tip_ratio_max",
            "crusher_direct_tip_ratio_max": "crusher_direct_tip_ratio_max",
        }
        for element in ["fe", "si", "al", "p", "mn"]:
            aliases[f"{element}_min"] = f"crusher_target_{element}_min"
            aliases[f"{element}_max"] = f"crusher_target_{element}_max"
            aliases[f"crusher_target_{element}_min"] = f"crusher_target_{element}_min"
            aliases[f"crusher_target_{element}_max"] = f"crusher_target_{element}_max"
        return aliases.get(normalized, normalized if self.find_calendar_row_by_key(normalized) is not None else None)

    def agent_period_key_alias(self, period):
        normalized = str(period or "").strip().lower().replace(" ", "_")
        aliases = {
            "preplan": "Preplan",
            "period_1": "Period_1",
            "period1": "Period_1",
            "p1": "Period_1",
            "1": "Period_1",
            "period_2": "Period_2",
            "period2": "Period_2",
            "p2": "Period_2",
            "2": "Period_2",
        }
        return aliases.get(normalized, str(period) if str(period) in getattr(self, "calendar_headers", []) else None)

    def find_calendar_row_by_key(self, row_key):
        if not hasattr(self, "calendar_rows"):
            return None
        for row_idx, row in enumerate(self.calendar_rows):
            if isinstance(row, dict) and row_key in row:
                return row_idx
        return None

    def find_calendar_column_by_period(self, period):
        if not hasattr(self, "calendar_headers"):
            return None
        try:
            return self.calendar_headers.index(period)
        except ValueError:
            return None

    def parse_agent_calendar_target(self, target):
        target = str(target or "").strip()
        prefixes = ("calendar_rates.", "calendar.")
        for prefix in prefixes:
            if target.startswith(prefix):
                remainder = target[len(prefix):]
                parts = remainder.split(".")
                if len(parts) >= 2:
                    period = self.agent_period_key_alias(parts[-1])
                    row_key = self.agent_calendar_key_alias(".".join(parts[:-1]))
                    if row_key and period:
                        return row_key, period
        return None

    def is_known_agent_target(self, target):
        key = self.normalized_agent_target(target)
        if target in {"site_configuration", "site_config"}:
            return True
        if target in {"selected_stockpiles", "selected_amt_stockpiles", "amt_stockpiles"}:
            return hasattr(self, "stockpile_table")
        if target in {"product_build_settings", "product_builds", "product_build_settings_tab"}:
            return hasattr(self, "product_build_table")
        if str(target).startswith("calendar_rates.") or str(target).startswith("calendar."):
            parsed = self.parse_agent_calendar_target(target)
            return bool(parsed and self.find_calendar_row_by_key(parsed[0]) is not None and self.find_calendar_column_by_period(parsed[1]) is not None)
        if str(target).startswith("amt_chunking."):
            return hasattr(self, "AMT_stockpile_table")
        known = {
            "min_stockpiles",
            "max_stockpiles",
            "min_stockpile_contribution_ratio",
            "throughput_incentive_per_tonne",
            "source_selection_tie_break_penalty",
            "stockpile_feasibility_mode",
            "allow_offspec_steady_states_for_product_build",
            "brand_guidance_mode",
            "brand_guidance_enabled",
            "brand_guidance_incentive",
            "timing_guidance_enabled",
            "timing_guidance_incentive",
            "timing_guidance_tolerance_hours",
            "active_blend_guidance_enabled",
            "active_blend_guidance_incentive",
            "rehandle_cycle_time_penalty_enabled",
            "haulage_cost_per_hour",
            "min_feed_duration_hours",
            "direct_tip_enabled",
            "direct_tip_cash_incentive",
            "stay_on_same_blend_incentive",
            "blend_option_timeout_seconds",
            "max_blend_options_per_steady_state",
            "min_grade_block_pair_duration_hours",
            "stay_on_same_grade_block_pair_incentive",
            "grade_block_lock_enabled",
            "prefer_fewer_stockpiles",
            "fewer_stockpiles_incentive",
            "balance_preference",
            "balance_preference_incentive",
            "prefer_amt_stockpiles",
            "amt_preference_incentive",
            "prefer_contaminated_stockpiles",
            "contaminated_preference_incentive",
            "contaminant_thresholds.si",
            "contaminant_thresholds.al",
            "contaminant_thresholds.p",
            "contaminant_thresholds.mn",
            "prefer_low_fe_stockpiles",
            "low_fe_preference_incentive",
            "low_fe_threshold",
        }
        return key in known

    def get_agent_target_current_value(self, target):
        if target == "selected_stockpiles":
            return sorted(
                name for name, selected in (self.stockpile_data_use_column or {}).items() if selected
            )
        if target in {"product_build_settings", "product_builds", "product_build_settings_tab"}:
            return getattr(self, "product_build_settings", [])
        if str(target).startswith("calendar_rates.") or str(target).startswith("calendar."):
            parsed = self.parse_agent_calendar_target(target)
            if parsed:
                row_key, period = parsed
                row_idx = self.find_calendar_row_by_key(row_key)
                col_idx = self.find_calendar_column_by_period(period)
                if row_idx is not None and col_idx is not None and hasattr(self, "main_table"):
                    return self.get_main_table_cell_text(row_idx, col_idx)
        if str(target).startswith("amt_chunking."):
            return self.get_agent_amt_chunking_current_value(target)
        key = self.normalized_agent_target(target)
        if key == "min_stockpiles":
            return self.min_stockpiles_input.text() if hasattr(self, "min_stockpiles_input") else None
        if key == "max_stockpiles":
            return self.max_stockpiles_input.text() if hasattr(self, "max_stockpiles_input") else None
        if key == "min_stockpile_contribution_ratio":
            return self.min_stockpile_contribution_ratio_input.text() if hasattr(self, "min_stockpile_contribution_ratio_input") else None
        solver_config = self.normalized_solver_config(getattr(self, "solver_config", {}))
        if key.startswith("contaminant_thresholds."):
            contaminant = key.split(".", 1)[1]
            return solver_config.get("contaminant_thresholds", {}).get(contaminant)
        return solver_config.get(key, "Review only")

    def get_agent_amt_chunking_current_value(self, target):
        parts = str(target).split(".")
        if len(parts) < 3 or not hasattr(self, "AMT_stockpile_table"):
            return None
        stockpile, field = parts[1], parts[2]
        field_column = {
            "average_reclaim_rate_tph": 1,
            "chunk_reclaim_hours": 2,
        }.get(field)
        if field_column is None:
            return None
        for row in range(self.AMT_stockpile_table.rowCount()):
            item = self.AMT_stockpile_table.item(row, 0)
            if item and item.text().strip().upper() == stockpile.upper():
                value_item = self.AMT_stockpile_table.item(row, field_column)
                return value_item.text() if value_item else None
        return None

    def apply_selected_agent_proposals(self):
        if not getattr(self, "agent_latest_proposals", None):
            QMessageBox.information(self, "BlendMaster Agent", "No proposals are available to apply.")
            return

        applied = 0
        skipped = 0
        calendar_changed = False
        for row, proposal in enumerate(self.agent_latest_proposals):
            if not proposal.get("applyable", False):
                continue
            apply_item = self.agent_proposals_table.item(row, 0)
            if apply_item is None or apply_item.checkState() != Qt.Checked:
                continue
            if proposal.get("action") == "agent_workflow":
                self.start_agent_workflow_apply(proposal.get("workflow_payload") or proposal.get("value") or {})
                return
            if proposal.get("action") == "project_load":
                if self.apply_agent_project_load(proposal):
                    self.append_agent_console("Agent result restored through the project-load path.")
                    QMessageBox.information(self, "BlendMaster Agent", "Agent result loaded as a project.")
                return
            if self.apply_agent_target_value(proposal["target"], proposal["value"]):
                applied += 1
                if str(proposal["target"]).startswith(("calendar_rates.", "calendar.")):
                    calendar_changed = True
            else:
                skipped += 1

        if calendar_changed:
            self.store_calendar_inputs_no_run()
        self.store_solver_config_inputs(show_errors=False)
        self.append_agent_console(f"Applied {applied} proposal(s). Skipped {skipped} proposal(s). Review-only rows were left unchanged.")
        QMessageBox.information(self, "BlendMaster Agent", f"Applied {applied} proposal(s).")

    def start_agent_workflow_apply(self, payload):
        if not isinstance(payload, dict) or not payload:
            QMessageBox.information(self, "BlendMaster Agent", "No workflow proposal is available to apply.")
            return

        self.agent_workflow_active = True
        self.agent_workflow_payload = copy.deepcopy(payload)
        self.agent_workflow_after_site_config = False
        self.agent_workflow_waiting_for_amt = False
        self.append_agent_console("Starting guided agent apply through the normal UI workflow.")
        QTimer.singleShot(0, self.agent_workflow_apply_site_configuration)

    def stop_agent_workflow_apply(self, message):
        self.agent_workflow_active = False
        self.agent_workflow_after_site_config = False
        self.agent_workflow_waiting_for_amt = False
        self.append_agent_console(message)

    def agent_workflow_apply_site_configuration(self):
        payload = getattr(self, "agent_workflow_payload", {}) or {}
        site_config = payload.get("site_configuration") or {}
        self.show_page(self.site_config_tab_index)
        self.apply_agent_site_configuration_payload(site_config)
        if not self.submit_button.isEnabled():
            self.stop_agent_workflow_apply(
                "Agent workflow stopped on Site Configuration: required site fields are still missing or invalid."
            )
            return
        self.agent_workflow_after_site_config = True
        self.is_project_loaded = False
        self.append_agent_console("Agent workflow: Site Configuration populated; submitting.")
        self.handle_site_config_submit()

    def apply_agent_site_configuration_payload(self, site_config):
        if not isinstance(site_config, dict):
            site_config = {}

        def set_combo(combo, value):
            if value is None:
                return
            text = str(value)
            index = combo.findText(text)
            if index < 0:
                normalized_text = self.normalized_agent_combo_text(text)
                for item_index in range(combo.count()):
                    if self.normalized_agent_combo_text(combo.itemText(item_index)) == normalized_text:
                        index = item_index
                        break
            if index >= 0:
                combo.setCurrentIndex(index)
            else:
                combo.setCurrentText(text)

        set_combo(self.hub_input, site_config.get("hub"))
        if site_config.get("hub") is not None:
            self.update_mine_dropdown()
        set_combo(self.mine_input, site_config.get("mine"))
        self.update_opf_dropdown(site_config.get("opf"))
        if site_config.get("opf") is not None:
            set_combo(self.opf_input, site_config.get("opf"))
        self.update_site_crusher_options()
        operational_crushers = (
            site_config.get("crushers")
            or site_config.get("operational_crushers")
            or site_config.get("crusher")
            or []
        )
        if isinstance(operational_crushers, str):
            operational_crushers = [operational_crushers]
        operational_crushers = list(operational_crushers)[:1]
        self.update_site_crusher_options(operational_crushers or None)

        start_time = self.parse_agent_datetime_value(
            site_config.get("start_time")
            or site_config.get("start_datetime")
            or site_config.get("set_datetime")
        )
        if isinstance(start_time, datetime):
            self.time_mode.setCurrentIndex(1)
            self.start_time.setEnabled(True)
            self.start_time.setDateTime(QDateTime(
                start_time.year,
                start_time.month,
                start_time.day,
                start_time.hour,
                start_time.minute,
                start_time.second,
            ))

        file_path = (
            site_config.get("two_wp_mining_csv")
            or site_config.get("aps_mining_csv")
            or site_config.get("mining_csv")
            or site_config.get("file_path")
        )
        if file_path:
            self.file_path.setText(str(file_path))
        twenty_four_hour_path = (
            site_config.get("twenty_four_hour_mining_csv")
            or site_config.get("24hr_mining_csv")
        )
        if twenty_four_hour_path:
            self.file_path_24hr.setText(str(twenty_four_hour_path))
        if any(key in site_config for key in (
            "selected_24hr_expit_agents",
            "expit_dig_circuits",
        )):
            selected_agents = self.normalized_expit_agent_names(
                site_config.get("selected_24hr_expit_agents")
                or site_config.get("expit_dig_circuits")
                or []
            )
            self.set_24hr_expit_agent_items(selected_agents, selected_agents)
        haul_cycle_path = (
            site_config.get("haul_cycle_csv")
            or site_config.get("haul_infinity_cycles_csv")
        )
        if haul_cycle_path:
            self.haul_cycle_file_path.setText(str(haul_cycle_path))
        if "selected_haul_cycle_crushers" in site_config:
            selected_crushers = self.normalized_expit_agent_names(
                site_config.get("selected_haul_cycle_crushers") or []
            )
            self.set_haul_cycle_crusher_items(
                selected_crushers,
                selected_crushers,
            )

        ratio_mode_value = (
            site_config.get("crusher_ratio_mode")
            or site_config.get("crusher_contribution_mode")
        )
        if ratio_mode_value is not None:
            ratio_mode_text = str(ratio_mode_value).strip().lower()
            ratio_mode = (
                "aps"
                if ratio_mode_text in {"aps", "derived", "mining.csv"}
                or "derive" in ratio_mode_text
                else "manual"
            )
            ratio_mode_index = self.crusher_ratio_mode_input.findData(ratio_mode)
            self.crusher_ratio_mode_input.setCurrentIndex(max(ratio_mode_index, 0))

        if "aps_ratio_crushers" in site_config or "aps_feed_crushers" in site_config:
            aps_ratio_crushers = (
                site_config.get("aps_ratio_crushers")
                or site_config.get("aps_feed_crushers")
                or []
            )
            if isinstance(aps_ratio_crushers, str):
                aps_ratio_crushers = [aps_ratio_crushers]
            self.set_aps_ratio_crusher_items(aps_ratio_crushers, aps_ratio_crushers)

        ratio = None
        if site_config.get("crusher_contribution_ratio_percent") is not None:
            try:
                ratio = float(site_config.get("crusher_contribution_ratio_percent")) / 100.0
            except (TypeError, ValueError):
                ratio = None
        elif site_config.get("crusher_contribution_ratio") is not None:
            ratio = self.normalized_crusher_ratio(
                site_config.get("crusher_contribution_ratio"), default=None
            )
        if ratio is not None:
            self.crusher_ratio_input.setText(
                f"{ratio * 100:.4f}".rstrip("0").rstrip(".")
            )
        self.toggle_crusher_ratio_controls()

        product_brands = (
            site_config.get("product_brands")
            or site_config.get("brand_labels")
            or site_config.get("brands")
        )
        if product_brands is not None:
            self.product_brand_labels_choice = self.parse_product_brand_labels(product_brands)
            if hasattr(self, "product_brand_labels_input"):
                self.product_brand_labels_input.setText(", ".join(self.product_brand_labels_choice))

        automatic_targets = site_config.get(
            "auto_load_2wp_targets",
            site_config.get("automatic_2wp_product_build_targets"),
        )
        if automatic_targets is not None and hasattr(self, "auto_load_2wp_targets_checkbox"):
            self.auto_load_2wp_targets_checkbox.setChecked(bool(automatic_targets))

        direct_tip_enabled = (
            site_config.get("reevaluate_aps_direct_tip")
            if "reevaluate_aps_direct_tip" in site_config
            else site_config.get("re_evaluate_aps_direct_tip")
        )
        aps_crushers = (
            site_config.get("selected_aps_crushers")
            or site_config.get("aps_direct_tip_crushers")
            or site_config.get("aps_direct_tip_crusher")
            or []
        )
        if isinstance(aps_crushers, str):
            aps_crushers = [aps_crushers]
        aps_crushers = list(aps_crushers)[:1]
        if direct_tip_enabled is not None:
            self.reevaluate_aps_direct_tip_checkbox.setChecked(bool(direct_tip_enabled))
        if aps_crushers:
            self.reevaluate_aps_direct_tip_checkbox.setChecked(True)
            self.set_aps_crusher_items(aps_crushers, aps_crushers)
        elif direct_tip_enabled is not None:
            self.set_aps_crusher_items([], [])

        movement_fields_present = any(key in site_config for key in (
            "direct_tip_movement_rules",
            "direct_tip_grade_block_sources",
            "direct_tip_crusher_destinations",
        ))
        if movement_fields_present:
            movement_rules = ExpitDataHandler._normalize_movement_rules(
                site_config.get("direct_tip_movement_rules") or []
            )
            movement_sources = site_config.get("direct_tip_grade_block_sources")
            movement_destinations = site_config.get("direct_tip_crusher_destinations")
            if movement_sources is None:
                movement_sources = [
                    rule["grade_block_source"] for rule in movement_rules
                ]
            if movement_destinations is None:
                movement_destinations = [
                    rule["crusher_destination"] for rule in movement_rules
                ]
            if isinstance(movement_sources, str):
                movement_sources = [movement_sources]
            if isinstance(movement_destinations, str):
                movement_destinations = [movement_destinations]
            self.direct_tip_grade_block_sources = list(movement_sources or [])
            self.direct_tip_crusher_destinations = list(movement_destinations or [])
            self.direct_tip_movement_rules = movement_rules
            self.set_direct_tip_movement_options(
                self.direct_tip_grade_block_sources,
                self.direct_tip_crusher_destinations,
            )
            self.refresh_direct_tip_rule_list()

        blend_mode = site_config.get("blend_mode") or site_config.get("optimised_blend_choices")
        if blend_mode is not None:
            if isinstance(blend_mode, str):
                blend_text = (
                    "Prompt User at Decision Point"
                    if "prompt" in blend_mode.lower() or "manual" in blend_mode.lower()
                    else "Select Best Result Automatically"
                )
                set_combo(self.blend_mode, blend_text)
            else:
                index = int(blend_mode) - 1
                if 0 <= index < self.blend_mode.count():
                    self.blend_mode.setCurrentIndex(index)

        if hasattr(self, "agent_enabled_checkbox"):
            self.agent_enabled_checkbox.setChecked(True)
        self.validate_form()

    @staticmethod
    def normalized_agent_combo_text(value):
        text = str(value or "").strip().lower()
        text = text.replace("_", " ").replace("-", " ")
        for suffix in (" mine", " hub"):
            if text.endswith(suffix):
                text = text[: -len(suffix)]
        return " ".join(text.split())

    def agent_workflow_apply_stockpiles(self):
        payload = getattr(self, "agent_workflow_payload", {}) or {}
        selected_payload = payload.get("selected_stockpiles")
        self.show_page(self.stockpile_tab_index)

        if selected_payload:
            if self.apply_agent_selected_stockpiles(selected_payload):
                selected = self.normalized_agent_selected_stockpile_payload(selected_payload)
                use_count = len(selected.get("use") or [])
                amt_count = len(selected.get("amt") or [])
                self.append_agent_console(f"Agent workflow: selected {use_count} stockpile(s), {amt_count} as AMT.")
            else:
                self.stop_agent_workflow_apply(
                    "Agent workflow stopped: selected stockpiles could not be applied to the Stockpile Inventories table. "
                    f"{self.agent_stockpile_match_diagnostic(selected_payload)}"
                )
                return
        else:
            self.append_agent_console("Agent workflow: no selected_stockpiles section returned; leaving Stockpile Inventories as currently shown.")

        selected = self.normalized_agent_selected_stockpile_payload(selected_payload) if selected_payload else {}
        has_amt = bool(selected.get("amt"))
        self.agent_workflow_waiting_for_amt = has_amt
        self.store_stockpile_table()
        if not has_amt:
            QTimer.singleShot(250, self.agent_workflow_apply_solver_configuration)

    def agent_stockpile_match_diagnostic(self, selected_payload):
        selected = self.normalized_agent_selected_stockpile_payload(selected_payload)
        requested = (selected.get("use") or [])[:6]
        available = []
        if hasattr(self, "stockpile_table"):
            for row in range(min(self.stockpile_table.rowCount(), 6)):
                item = self.stockpile_table.item(row, 2)
                if item:
                    available.append(item.text().strip())
        return (
            f"Requested sample: {requested or 'none'}. "
            f"Available table sample: {available or 'none'}. "
            f"Current site is Hub={self.hub_input.currentText()}, Mine={self.mine_input.currentText()}."
        )

    def agent_workflow_apply_amt_stockpiles(self):
        payload = getattr(self, "agent_workflow_payload", {}) or {}
        self.show_page(self.AMT_stockpile_tab_index)

        amt_chunking = payload.get("amt_chunking") or {}
        if amt_chunking:
            applied = self.apply_agent_amt_chunking_payload(amt_chunking)
            self.append_agent_console(f"Agent workflow: applied AMT chunk settings for {applied} stockpile(s).")

        hex_sequence_table = self.normalized_agent_hex_sequence_table(payload.get("hex_sequence_table") or [])
        if not hex_sequence_table:
            self.stop_agent_workflow_apply(
                "Agent workflow paused on AMT Stockpiles: AMT workflow results must include hex_sequence_table with the generated chunk rows."
            )
            return

        if not all(isinstance(item, dict) for item in hex_sequence_table):
            self.stop_agent_workflow_apply("Agent workflow stopped: AMT hex/chunk sequence contains invalid rows.")
            return

        valid, validation_message = self.validate_agent_hex_sequence_table(hex_sequence_table)
        if not valid:
            file_note = ""
            if payload.get("hex_sequence_table_file"):
                file_note = f" Supplied hex_sequence_table_file: {payload.get('hex_sequence_table_file')}."
            self.stop_agent_workflow_apply(
                "Agent workflow paused on AMT Stockpiles: the supplied hex_sequence_table is incomplete. "
                f"{validation_message}. Full AMT chunk rows must include footprint, sequence, hex chunk id, "
                "positive balance, grade_fe/grade_si/grade_al/grade_p/grade_mn, and member_hexes so the map, "
                f"chunk sequence table, and opening balances can be built.{file_note}"
            )
            return

        if not self.store_AMT_chunk_settings():
            self.stop_agent_workflow_apply("Agent workflow stopped: AMT chunk settings are not valid.")
            return

        if not self.load_agent_amt_sequence_into_map(hex_sequence_table):
            self.stop_agent_workflow_apply("Agent workflow stopped: unable to load the supplied AMT hex/chunk sequence into the AMT map.")
            return

        self.total_AMT_stockpile_balances = {}
        self.populate_total_AMT_stockpile_balances()
        self.activate_manual_setup_tab()
        self.navigate_to_solver_configuration()
        QTimer.singleShot(250, self.agent_workflow_apply_solver_configuration)

    def load_agent_amt_sequence_into_map(self, hex_sequence_table):
        if not isinstance(hex_sequence_table, list) or not all(isinstance(item, dict) for item in hex_sequence_table):
            return False

        self.hex_sequence_table = copy.deepcopy(hex_sequence_table)
        self.hex_sequence_table_argument = copy.deepcopy(hex_sequence_table)

        draw_AMT_map = getattr(self, "draw_AMT_map", None)
        if draw_AMT_map is None:
            return False

        draw_AMT_map.update_chunk_settings(copy.deepcopy(self.AMT_chunk_settings))
        draw_AMT_map.selected_points = copy.deepcopy(self.hex_sequence_table)
        draw_AMT_map.clean_up_hex_sequence_table()
        draw_AMT_map.update_sequence_counter()

        self.hex_sequence_table = copy.deepcopy(draw_AMT_map.selected_points)
        self.hex_sequence_table_argument = copy.deepcopy(draw_AMT_map.selected_points)
        self.refresh_AMT_map_data_from_database()

        if hasattr(self, "AMT_map_view"):
            self.load_AMT_map()
            self.append_agent_console("Agent workflow: loaded supplied AMT chunk sequence into the AMT map.")
        return True

    def apply_agent_amt_chunking_payload(self, amt_chunking):
        applied = 0
        if not isinstance(amt_chunking, dict):
            return applied
        for stockpile, settings in amt_chunking.items():
            if not isinstance(settings, dict):
                continue
            if settings.get("average_reclaim_rate_tph") is not None:
                if self.apply_agent_amt_chunking_target(
                    f"amt_chunking.{stockpile}.average_reclaim_rate_tph",
                    settings.get("average_reclaim_rate_tph"),
                ):
                    applied += 1
            elif settings.get("average_reclaim_rate") is not None:
                if self.apply_agent_amt_chunking_target(
                    f"amt_chunking.{stockpile}.average_reclaim_rate_tph",
                    settings.get("average_reclaim_rate"),
                ):
                    applied += 1
            if settings.get("chunk_reclaim_hours") is not None:
                self.apply_agent_amt_chunking_target(
                    f"amt_chunking.{stockpile}.chunk_reclaim_hours",
                    settings.get("chunk_reclaim_hours"),
                )
        if hasattr(self, "AMT_stockpile_table"):
            self.store_AMT_chunk_settings()
        return applied

    def agent_workflow_apply_solver_configuration(self):
        payload = getattr(self, "agent_workflow_payload", {}) or {}
        if not hasattr(self, "min_stockpiles_input"):
            self.stop_agent_workflow_apply("Agent workflow stopped: Solver Configuration tab is not ready.")
            return

        self.show_page(self.solver_config_tab_index)
        applied = 0
        blend_settings = payload.get("blend_settings") or {}
        for proposal in self.expand_agent_proposals([{"target": "blend_settings", "value": blend_settings}]):
            if self.apply_agent_target_value(proposal.get("target"), proposal.get("value")):
                applied += 1

        solver_config = payload.get("solver_configuration") or {}
        for proposal in self.expand_agent_proposals([{"target": "solver_config", "value": solver_config}]):
            if self.apply_agent_target_value(proposal.get("target"), proposal.get("value")):
                applied += 1

        if applied:
            self.append_agent_console(f"Agent workflow: applied {applied} Solver Configuration setting(s); submitting.")
        else:
            self.append_agent_console("Agent workflow: no Solver Configuration settings returned; submitting current values.")

        if not self.store_solver_config_inputs():
            self.stop_agent_workflow_apply("Agent workflow stopped: Solver Configuration inputs are not valid.")
            return

        self.navigate_to_product_build_settings()
        QTimer.singleShot(250, self.agent_workflow_apply_product_build_settings)

    def agent_workflow_apply_product_build_settings(self):
        payload = getattr(self, "agent_workflow_payload", {}) or {}
        product_builds = (
            payload.get("product_build_settings")
            or payload.get("product_builds")
            or payload.get("product_build_settings_tab")
            or []
        )
        if isinstance(product_builds, dict):
            product_builds = product_builds.get("builds") or product_builds.get("rows") or []
        if isinstance(product_builds, list):
            self.product_build_settings = self.normalized_agent_product_build_settings(product_builds)
            self.populate_product_build_table()

        if not self.store_product_build_settings(show_errors=False):
            self.stop_agent_workflow_apply("Agent workflow stopped: Product Build Settings inputs are not valid.")
            return

        self.setup_calendar()
        self.set_page_enabled(self.calendar_tab_index, True)
        self.show_page(self.calendar_tab_index)
        QTimer.singleShot(250, self.agent_workflow_apply_calendar)

    def agent_workflow_apply_calendar(self):
        payload = getattr(self, "agent_workflow_payload", {}) or {}
        calendar_rates = payload.get("calendar_rates") or {}
        self.show_page(self.calendar_tab_index)

        applied = 0
        if calendar_rates:
            for proposal in self.expand_agent_proposals([{"target": "calendar_rates", "value": calendar_rates}]):
                if self.apply_agent_target_value(proposal.get("target"), proposal.get("value")):
                    applied += 1
            self.append_agent_console(f"Agent workflow: applied {applied} Calendar value(s); submitting and starting optimisation.")
            self.agent_workflow_active = False
            self.store_calendar_inputs()
            return

        self.stop_agent_workflow_apply("Agent workflow completed at Calendar: no calendar_rates were returned, so optimisation was not started.")

    def apply_agent_project_load(self, proposal):
        source_kind = proposal.get("source_kind")
        source_payload = proposal.get("source_payload")

        try:
            agent_panel_state = self.capture_agent_panel_state()
            if source_kind == "project_file":
                project_path = str(source_payload)
                loaded_state = self.load_project_state_from_path(project_path)
                source_label = project_path
            elif source_kind == "project_state":
                loaded_state = copy.deepcopy(source_payload)
                source_label = "embedded project state"
            else:
                QMessageBox.warning(self, "BlendMaster Agent", "The selected agent result is not a project-load proposal.")
                return False

            loaded_state = self.normalized_agent_project_state(loaded_state)
            self.restore_loaded_state(loaded_state, source_label=source_label, show_success=False)
            # The project result changes the modelling state, not the active
            # conversation which led to it. Only Clear Agent Output should
            # remove Story, Instructions, Console, or proposal rows.
            self.restore_agent_panel_state(agent_panel_state)
            self.append_agent_console("Agent result restored through the project-load path.")
            return True
        except Exception as exc:
            QMessageBox.warning(self, "BlendMaster Agent", f"Unable to load agent project result: {exc}")
            self.append_agent_console(f"Agent project load failed: {exc}")
            return False

    def load_project_state_from_path(self, project_path):
        if not project_path:
            raise ValueError("Project path is empty.")
        project_path = os.path.abspath(os.path.expanduser(str(project_path)))
        if not os.path.exists(project_path):
            raise FileNotFoundError(project_path)
        if project_path.lower().endswith(".prj"):
            with open(project_path, "rb") as file:
                return pickle.load(file)
        if project_path.lower().endswith(".json"):
            with open(project_path, "r", encoding="utf-8") as file:
                return json.load(file)
        raise ValueError("Agent project_file must point to a .prj or .json project-state file.")

    def normalized_agent_project_state(self, loaded_state):
        if not isinstance(loaded_state, dict):
            raise ValueError("Agent project state must be a dictionary.")

        loaded_state = copy.deepcopy(loaded_state)
        site_config = loaded_state.pop("site_configuration", None)
        if isinstance(site_config, dict):
            self.merge_agent_site_configuration_into_state(loaded_state, site_config)

        if "solver_configuration" in loaded_state and "solver_config" not in loaded_state:
            loaded_state["solver_config"] = loaded_state.pop("solver_configuration")

        for datetime_key in [
            "start_time_choice",
            "default_start_datetime",
            "default_end_datetime",
        ]:
            loaded_state[datetime_key] = self.parse_agent_datetime_value(loaded_state.get(datetime_key))

        if loaded_state.get("stockpile_data_use_column") is None:
            loaded_state["stockpile_data_use_column"] = {}
        if loaded_state.get("stockpile_data_AMT_column") is None:
            loaded_state["stockpile_data_AMT_column"] = {}
        if loaded_state.get("saved_blends_for_schedule") is None:
            loaded_state["saved_blends_for_schedule"] = []
        if loaded_state.get("stored_blend_sequence_table_for_gantt") is None:
            loaded_state["stored_blend_sequence_table_for_gantt"] = []
        if loaded_state.get("stored_blend_sequence_table_for_gantt_default") is None:
            loaded_state["stored_blend_sequence_table_for_gantt_default"] = []
        if loaded_state.get("manual_direct_tip_allocations") is None:
            loaded_state["manual_direct_tip_allocations"] = {}
        if loaded_state.get("manual_steady_states") is None:
            loaded_state["manual_steady_states"] = []
        if loaded_state.get("hex_sequence_table") is None:
            loaded_state["hex_sequence_table"] = []
        if loaded_state.get("AMT_stockpile_data") is None:
            loaded_state["AMT_stockpile_data"] = {}
        if loaded_state.get("AMT_chunk_settings") is None:
            loaded_state["AMT_chunk_settings"] = {}
        if loaded_state.get("product_brand_labels_choice") is None:
            loaded_state["product_brand_labels_choice"] = self.default_product_brand_labels()
        loaded_state["product_brand_labels_choice"] = self.parse_product_brand_labels(
            loaded_state.get("product_brand_labels_choice")
        )
        if loaded_state.get("product_build_settings") is None:
            loaded_state["product_build_settings"] = []
        if loaded_state.get("auto_load_2wp_targets_choice") is None:
            loaded_state["auto_load_2wp_targets_choice"] = True
        mine = str(loaded_state.get("mine_input_choice") or "").strip().upper()
        opf = str(loaded_state.get("opf_input_choice") or "").strip()
        crusher = str(loaded_state.get("crusher_input_choice") or "").strip()
        if not opf:
            opf = self.default_opf_for_site(mine, crusher)
        loaded_state["opf_input_choice"] = opf or None
        if not crusher:
            crusher_options = crusher_options_for_site(mine, opf)
            crusher = crusher_options[0] if crusher_options else ""
        loaded_state["crusher_input_choice"] = crusher or None
        loaded_state["selected_site_crushers"] = [crusher] if crusher else []
        loaded_state["crusher_ratio_mode_choice"] = self.normalized_crusher_ratio_mode(
            loaded_state.get("crusher_ratio_mode_choice")
        )
        loaded_state["crusher_contribution_ratio_choice"] = self.normalized_crusher_ratio(
            loaded_state.get("crusher_contribution_ratio_choice", 1.0),
            default=1.0,
        )
        loaded_state["crusher_ratio_configured"] = bool(
            loaded_state.get("crusher_ratio_configured", False)
        )
        loaded_state["aps_ratio_crusher_choices"] = self.normalized_aps_crusher_choice(
            loaded_state.get("aps_ratio_crusher_choices") or []
        )
        loaded_state["direct_tip_grade_block_sources"] = list(
            loaded_state.get("direct_tip_grade_block_sources") or []
        )
        loaded_state["direct_tip_crusher_destinations"] = list(
            loaded_state.get("direct_tip_crusher_destinations") or []
        )
        loaded_state["direct_tip_movement_rules"] = ExpitDataHandler._normalize_movement_rules(
            loaded_state.get("direct_tip_movement_rules") or []
        )
        if loaded_state.get("aps_stockpile_brand_map") is None:
            loaded_state["aps_stockpile_brand_map"] = {}
        if loaded_state.get("aps_stockpile_timing_guidance") is None:
            loaded_state["aps_stockpile_timing_guidance"] = {}
        if loaded_state.get("aps_active_blend_guidance") is None:
            loaded_state["aps_active_blend_guidance"] = []
        if loaded_state.get("aps_destination_guidance") is None:
            loaded_state["aps_destination_guidance"] = {}
        loaded_state["available_24hr_expit_agents"] = (
            self.normalized_expit_agent_names(
                loaded_state.get("available_24hr_expit_agents") or []
            )
        )
        loaded_state["selected_24hr_expit_agents"] = (
            self.normalized_expit_agent_names(
                loaded_state.get("selected_24hr_expit_agents") or []
            )
        )
        if (
            not loaded_state["available_24hr_expit_agents"]
            and loaded_state["selected_24hr_expit_agents"]
        ):
            loaded_state["available_24hr_expit_agents"] = list(
                loaded_state["selected_24hr_expit_agents"]
            )
        loaded_state["haul_cycle_file_path_choice"] = str(
            loaded_state.get("haul_cycle_file_path_choice") or ""
        )
        loaded_state["available_haul_cycle_crushers"] = (
            self.normalized_expit_agent_names(
                loaded_state.get("available_haul_cycle_crushers") or []
            )
        )
        loaded_state["selected_haul_cycle_crushers"] = (
            self.normalized_expit_agent_names(
                loaded_state.get("selected_haul_cycle_crushers") or []
            )
        )
        if (
            not loaded_state["available_haul_cycle_crushers"]
            and loaded_state["selected_haul_cycle_crushers"]
        ):
            loaded_state["available_haul_cycle_crushers"] = list(
                loaded_state["selected_haul_cycle_crushers"]
            )
        if loaded_state.get("haul_cycle_routes") is None:
            loaded_state["haul_cycle_routes"] = {}
        if (
            "file_path_24hr_choice" not in loaded_state
            and loaded_state.get("file_path_choice")
        ):
            # Flat/single-schedule projects used the one file for both roles.
            loaded_state["file_path_24hr_choice"] = loaded_state["file_path_choice"]
        if loaded_state.get("solver_config") is None:
            loaded_state["solver_config"] = {}
        if loaded_state.get("time_mode_choice") is None:
            loaded_state["time_mode_choice"] = 2 if loaded_state.get("start_time_choice") else 1
        if loaded_state.get("expit_mode_choice") is None:
            loaded_state["expit_mode_choice"] = 1
        if loaded_state.get("blend_mode_choice") is None:
            loaded_state["blend_mode_choice"] = 1
        if loaded_state.get("start_time_choice") is None:
            loaded_state["start_time_choice"] = datetime.now()
        if not loaded_state.get("default_start_datetime_str") and loaded_state.get("default_start_datetime"):
            loaded_state["default_start_datetime_str"] = loaded_state["default_start_datetime"].strftime("%Y-%m-%d %H:%M:%S")
        if not loaded_state.get("default_end_datetime_str") and loaded_state.get("default_end_datetime"):
            loaded_state["default_end_datetime_str"] = loaded_state["default_end_datetime"].strftime("%Y-%m-%d %H:%M:%S")

        loaded_state["solver_config"] = self.normalized_solver_config(loaded_state.get("solver_config", {}))
        if loaded_state.get("calendar_inputs") is not None:
            loaded_state["calendar_inputs"]["solver_config"] = copy.deepcopy(loaded_state["solver_config"])

        return loaded_state

    def merge_agent_site_configuration_into_state(self, loaded_state, site_config):
        if "hub" in site_config:
            loaded_state["hub_input_choice"] = site_config.get("hub")
        if "mine" in site_config:
            loaded_state["mine_input_choice"] = site_config.get("mine")
        if "opf" in site_config or "operating_opf" in site_config:
            loaded_state["opf_input_choice"] = (
                site_config.get("opf") or site_config.get("operating_opf")
            )
        crushers = (
            site_config.get("crushers")
            or site_config.get("operational_crushers")
            or site_config.get("crusher")
        )
        if crushers:
            if isinstance(crushers, str):
                crushers = [crushers]
            loaded_state["selected_site_crushers"] = list(crushers)[:1]
            loaded_state["crusher_input_choice"] = list(crushers)[0]
        ratio = site_config.get("crusher_contribution_ratio")
        if site_config.get("crusher_contribution_ratio_percent") is not None:
            try:
                ratio = float(
                    site_config.get("crusher_contribution_ratio_percent")
                ) / 100.0
            except (TypeError, ValueError):
                ratio = None
        if ratio is not None:
            loaded_state["crusher_contribution_ratio_choice"] = ratio
            loaded_state["crusher_ratio_configured"] = True
        if "crusher_ratio_mode" in site_config or "crusher_contribution_mode" in site_config:
            loaded_state["crusher_ratio_mode_choice"] = self.normalized_crusher_ratio_mode(
                site_config.get("crusher_ratio_mode")
                or site_config.get("crusher_contribution_mode")
            )
        if "aps_ratio_crushers" in site_config or "aps_feed_crushers" in site_config:
            loaded_state["aps_ratio_crusher_choices"] = (
                site_config.get("aps_ratio_crushers")
                or site_config.get("aps_feed_crushers")
                or []
            )
        if "start_time" in site_config:
            loaded_state["start_time_choice"] = self.parse_agent_datetime_value(site_config.get("start_time"))
            loaded_state["time_mode_choice"] = 2
        if "aps_mining_csv" in site_config:
            loaded_state["file_path_choice"] = site_config.get("aps_mining_csv")
        if "two_wp_mining_csv" in site_config:
            loaded_state["file_path_choice"] = site_config.get("two_wp_mining_csv")
        if "twenty_four_hour_mining_csv" in site_config or "24hr_mining_csv" in site_config:
            loaded_state["file_path_24hr_choice"] = (
                site_config.get("twenty_four_hour_mining_csv")
                or site_config.get("24hr_mining_csv")
            )
        if (
            "selected_24hr_expit_agents" in site_config
            or "expit_dig_circuits" in site_config
        ):
            selected_agents = (
                site_config.get("selected_24hr_expit_agents")
                or site_config.get("expit_dig_circuits")
                or []
            )
            loaded_state["selected_24hr_expit_agents"] = (
                self.normalized_expit_agent_names(selected_agents)
            )
            loaded_state["available_24hr_expit_agents"] = list(
                loaded_state["selected_24hr_expit_agents"]
            )
        if (
            "haul_cycle_csv" in site_config
            or "haul_infinity_cycles_csv" in site_config
        ):
            loaded_state["haul_cycle_file_path_choice"] = (
                site_config.get("haul_cycle_csv")
                or site_config.get("haul_infinity_cycles_csv")
            )
        if "selected_haul_cycle_crushers" in site_config:
            loaded_state["selected_haul_cycle_crushers"] = (
                self.normalized_expit_agent_names(
                    site_config.get("selected_haul_cycle_crushers") or []
                )
            )
            loaded_state["available_haul_cycle_crushers"] = list(
                loaded_state["selected_haul_cycle_crushers"]
            )
        if "file_path" in site_config:
            loaded_state["file_path_choice"] = site_config.get("file_path")
        if "reevaluate_aps_direct_tip" in site_config:
            loaded_state["reevaluate_aps_direct_tip_choice"] = bool(site_config.get("reevaluate_aps_direct_tip"))
        if any(key in site_config for key in (
            "selected_aps_crushers", "aps_direct_tip_crushers", "aps_direct_tip_crusher"
        )):
            selected = (
                site_config.get("selected_aps_crushers")
                or site_config.get("aps_direct_tip_crushers")
                or site_config.get("aps_direct_tip_crusher")
                or []
            )
            if isinstance(selected, str):
                selected = [selected]
            loaded_state["aps_direct_tip_crusher_choice"] = list(selected)
        if "direct_tip_grade_block_sources" in site_config:
            loaded_state["direct_tip_grade_block_sources"] = copy.deepcopy(
                site_config.get("direct_tip_grade_block_sources") or []
            )
        if "direct_tip_crusher_destinations" in site_config:
            loaded_state["direct_tip_crusher_destinations"] = copy.deepcopy(
                site_config.get("direct_tip_crusher_destinations") or []
            )
        if "direct_tip_movement_rules" in site_config:
            rules = ExpitDataHandler._normalize_movement_rules(
                site_config.get("direct_tip_movement_rules") or []
            )
            loaded_state["direct_tip_movement_rules"] = rules
            if "direct_tip_grade_block_sources" not in site_config:
                loaded_state["direct_tip_grade_block_sources"] = sorted({
                    rule["grade_block_source"] for rule in rules
                })
            if "direct_tip_crusher_destinations" not in site_config:
                loaded_state["direct_tip_crusher_destinations"] = sorted({
                    rule["crusher_destination"] for rule in rules
                })
        if "product_brands" in site_config:
            loaded_state["product_brand_labels_choice"] = self.parse_product_brand_labels(
                site_config.get("product_brands")
            )
        if "auto_load_2wp_targets" in site_config:
            loaded_state["auto_load_2wp_targets_choice"] = bool(
                site_config.get("auto_load_2wp_targets")
            )
        if "blend_mode" in site_config:
            blend_mode = site_config.get("blend_mode")
            if isinstance(blend_mode, str):
                loaded_state["blend_mode_choice"] = 2 if "prompt" in blend_mode.lower() or "manual" in blend_mode.lower() else 1
            else:
                loaded_state["blend_mode_choice"] = blend_mode

    def parse_agent_datetime_value(self, value):
        if value is None or isinstance(value, datetime):
            return value
        if isinstance(value, pd.Timestamp):
            return value.to_pydatetime()
        text = str(value).strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return value

    def apply_agent_target_value(self, target, value):
        if target in {"site_configuration", "site_config"}:
            self.apply_agent_site_configuration_payload(value)
            return True
        if target == "selected_stockpiles":
            return self.apply_agent_selected_stockpiles(value)
        if target in {"selected_amt_stockpiles", "amt_stockpiles"}:
            return self.apply_agent_selected_stockpiles({"amt": value})
        if target in {"product_build_settings", "product_builds", "product_build_settings_tab"}:
            if not hasattr(self, "product_build_table"):
                return False
            self.product_build_settings = self.normalized_agent_product_build_settings(value)
            self.populate_product_build_table()
            return self.store_product_build_settings(show_errors=False)
        if str(target).startswith("calendar_rates.") or str(target).startswith("calendar."):
            return self.apply_agent_calendar_target(target, value)
        if str(target).startswith("amt_chunking."):
            return self.apply_agent_amt_chunking_target(target, value)

        key = self.normalized_agent_target(target)

        def set_line_edit(widget, proposed_value):
            widget.setText("" if proposed_value is None else str(proposed_value))

        def to_bool(proposed_value):
            if isinstance(proposed_value, bool):
                return proposed_value
            return str(proposed_value).strip().lower() in {"1", "true", "yes", "y", "enabled", "on"}

        if key == "min_stockpiles":
            set_line_edit(self.min_stockpiles_input, value)
            return True
        if key == "max_stockpiles":
            set_line_edit(self.max_stockpiles_input, value)
            return True
        if key == "min_stockpile_contribution_ratio":
            set_line_edit(self.min_stockpile_contribution_ratio_input, value)
            return True
        if key == "throughput_incentive_per_tonne":
            set_line_edit(self.throughput_incentive_input, value)
            return True
        if key == "source_selection_tie_break_penalty":
            set_line_edit(self.source_selection_tie_break_penalty_input, value)
            return True
        if key == "stockpile_feasibility_mode":
            label = {
                "stockpile_must_be_feasible": "Stockpile blend must be feasible",
                "stockpile_can_rely_on_grade_blocks": "Stockpile blend can rely on grade blocks",
            }.get(str(value), str(value))
            self.stockpile_feasibility_combo.setCurrentText(label)
            return True
        if key == "allow_offspec_steady_states_for_product_build":
            self.allow_offspec_steady_states_checkbox.setChecked(to_bool(value))
            return True
        if key == "brand_guidance_mode":
            normalized = str(value or "").strip().lower().replace(" ", "_")
            self.brand_guidance_enabled_checkbox.setChecked(
                normalized not in {
                    "ignore",
                    "none",
                    "ignore_aps_brand_guidance",
                    "ignore_2wp_brand_guidance",
                }
            )
            return True
        if key == "brand_guidance_enabled":
            self.brand_guidance_enabled_checkbox.setChecked(to_bool(value))
            return True
        if key == "brand_guidance_incentive":
            set_line_edit(self.brand_guidance_incentive_input, value)
            return True
        if key == "timing_guidance_enabled":
            self.timing_guidance_enabled_checkbox.setChecked(to_bool(value))
            return True
        if key == "timing_guidance_incentive":
            set_line_edit(self.timing_guidance_incentive_input, value)
            return True
        if key == "timing_guidance_tolerance_hours":
            set_line_edit(self.timing_guidance_tolerance_input, value)
            return True
        if key == "active_blend_guidance_enabled":
            self.active_blend_guidance_enabled_checkbox.setChecked(to_bool(value))
            return True
        if key == "active_blend_guidance_incentive":
            set_line_edit(self.active_blend_guidance_incentive_input, value)
            return True
        if key == "rehandle_cycle_time_penalty_enabled":
            self.rehandle_cycle_time_penalty_checkbox.setChecked(
                to_bool(value)
            )
            return True
        if key == "haulage_cost_per_hour":
            set_line_edit(self.haulage_cost_per_hour_input, value)
            return True
        if key == "min_feed_duration_hours":
            set_line_edit(self.min_feed_duration_input, value)
            return True
        if key == "direct_tip_enabled":
            self.direct_tip_enabled_checkbox.setChecked(to_bool(value))
            return True
        if key == "direct_tip_cash_incentive":
            set_line_edit(self.direct_tip_cash_incentive_input, value)
            return True
        if key == "stay_on_same_blend_incentive":
            set_line_edit(self.stay_on_same_blend_incentive_input, value)
            return True
        if key == "blend_option_timeout_seconds":
            set_line_edit(self.blend_option_timeout_input, value)
            return True
        if key == "max_blend_options_per_steady_state":
            set_line_edit(self.max_blend_options_input, value)
            return True
        if key == "min_grade_block_pair_duration_hours":
            set_line_edit(self.min_grade_block_pair_duration_input, value)
            return True
        if key == "stay_on_same_grade_block_pair_incentive":
            set_line_edit(self.stay_on_same_grade_block_pair_incentive_input, value)
            return True
        if key == "grade_block_lock_enabled":
            self.grade_block_lock_checkbox.setChecked(to_bool(value))
            return True
        if key == "prefer_fewer_stockpiles":
            self.prefer_fewer_stockpiles_checkbox.setChecked(to_bool(value))
            return True
        if key == "fewer_stockpiles_incentive":
            set_line_edit(self.fewer_stockpiles_incentive_input, value)
            return True
        if key == "balance_preference":
            label = {
                "none": "No balance preference",
                "lower": "Lower balance first",
                "higher": "Higher balance first",
            }.get(str(value), str(value))
            self.balance_preference_combo.setCurrentText(label)
            return True
        if key == "balance_preference_incentive":
            set_line_edit(
                self.balance_preference_incentive_input,
                value,
            )
            return True
        if key == "prefer_amt_stockpiles":
            self.prefer_amt_stockpiles_checkbox.setChecked(to_bool(value))
            return True
        if key == "amt_preference_incentive":
            set_line_edit(self.amt_preference_incentive_input, value)
            return True
        if key == "prefer_contaminated_stockpiles":
            self.prefer_contaminated_stockpiles_checkbox.setChecked(to_bool(value))
            return True
        if key == "contaminated_preference_incentive":
            set_line_edit(
                self.contaminated_preference_incentive_input,
                value,
            )
            return True
        if key == "contaminant_thresholds.si":
            set_line_edit(self.contaminant_si_threshold_input, value)
            return True
        if key == "contaminant_thresholds.al":
            set_line_edit(self.contaminant_al_threshold_input, value)
            return True
        if key == "contaminant_thresholds.p":
            set_line_edit(self.contaminant_p_threshold_input, value)
            return True
        if key == "contaminant_thresholds.mn":
            set_line_edit(self.contaminant_mn_threshold_input, value)
            return True
        if key == "prefer_low_fe_stockpiles":
            self.prefer_low_fe_stockpiles_checkbox.setChecked(to_bool(value))
            return True
        if key == "low_fe_preference_incentive":
            set_line_edit(self.low_fe_preference_incentive_input, value)
            return True
        if key == "low_fe_threshold":
            set_line_edit(self.low_fe_threshold_input, value)
            return True
        return False

    def apply_agent_selected_stockpiles(self, value):
        if not hasattr(self, "stockpile_table"):
            return False
        use_names = []
        amt_names = None
        if isinstance(value, dict):
            use_names = value.get("use") or value.get("selected") or value.get("stockpiles") or []
            amt_names = value.get("amt")
        elif isinstance(value, (list, tuple, set)):
            use_names = value
        else:
            use_names = [value]

        use_set = {str(name).strip().upper() for name in use_names if str(name).strip()}
        amt_set = {str(name).strip().upper() for name in (amt_names or []) if str(name).strip()} if amt_names is not None else None
        if not use_set and amt_set:
            use_set = set(amt_set)
        if not use_set:
            return False

        matched_any = False
        for row in range(self.stockpile_table.rowCount()):
            stockpile_item = self.stockpile_table.item(row, 2)
            if not stockpile_item:
                continue
            stockpile_name = stockpile_item.text().strip()
            stockpile_key = stockpile_name.upper()
            use_checked = stockpile_key in use_set
            matched_any = matched_any or use_checked or (amt_set is not None and stockpile_key in amt_set)
            self.set_stockpile_checkbox(row, 0, use_checked)
            self.stockpile_data_use_column[stockpile_name] = use_checked
            if amt_set is not None:
                amt_checked = stockpile_key in amt_set
                self.set_stockpile_checkbox(row, 1, amt_checked)
                self.stockpile_data_AMT_column[stockpile_name] = amt_checked
        return matched_any

    def set_stockpile_checkbox(self, row, column, checked):
        checkbox_widget = self.stockpile_table.cellWidget(row, column)
        if not checkbox_widget or not checkbox_widget.layout() or checkbox_widget.layout().count() == 0:
            return
        checkbox = checkbox_widget.layout().itemAt(0).widget()
        if isinstance(checkbox, QCheckBox):
            checkbox.setChecked(bool(checked))

    def apply_agent_calendar_target(self, target, value):
        parsed = self.parse_agent_calendar_target(target)
        if not parsed or not hasattr(self, "main_table"):
            return False
        row_key, period = parsed
        row_idx = self.find_calendar_row_by_key(row_key)
        col_idx = self.find_calendar_column_by_period(period)
        if row_idx is None or col_idx is None:
            return False

        widget = self.main_table.cellWidget(row_idx, col_idx)
        if isinstance(widget, QComboBox):
            text = str(value)
            if widget.findText(text) == -1:
                widget.addItem(text)
            widget.setCurrentText(text)
            return True

        item = self.main_table.item(row_idx, col_idx)
        if item is None:
            item = QTableWidgetItem()
            item.setTextAlignment(Qt.AlignCenter)
            self.main_table.setItem(row_idx, col_idx, item)
        item.setText("" if value is None else str(value))
        return True

    def apply_agent_amt_chunking_target(self, target, value):
        parts = str(target).split(".")
        if len(parts) < 3 or not hasattr(self, "AMT_stockpile_table"):
            return False
        stockpile, field = parts[1], parts[2]
        field_column = {
            "average_reclaim_rate_tph": 1,
            "chunk_reclaim_hours": 2,
        }.get(field)
        if field_column is None:
            return False
        for row in range(self.AMT_stockpile_table.rowCount()):
            stockpile_item = self.AMT_stockpile_table.item(row, 0)
            if not stockpile_item or stockpile_item.text().strip().upper() != stockpile.upper():
                continue
            item = self.AMT_stockpile_table.item(row, field_column)
            if item is None:
                item = QTableWidgetItem()
                item.setTextAlignment(Qt.AlignCenter)
                self.AMT_stockpile_table.setItem(row, field_column, item)
            item.setText("" if value is None else str(value))
            if self.AMT_stockpile_table.columnCount() > 3:
                try:
                    rate = float(self.AMT_stockpile_table.item(row, 1).text())
                    hours = float(self.AMT_stockpile_table.item(row, 2).text())
                    chunk_item = self.AMT_stockpile_table.item(row, 3)
                    if chunk_item is None:
                        chunk_item = QTableWidgetItem()
                        chunk_item.setTextAlignment(Qt.AlignCenter)
                        self.AMT_stockpile_table.setItem(row, 3, chunk_item)
                    chunk_item.setText(f"{rate * hours:.0f}")
                except Exception:
                    pass
            return True
        return False
    
    def stockpile_inventory_headers(self):
        headers = [
            "Use",
            "AMT",
            "Stockpile Name",
            "2WP Brand",
            "Nearest Crusher",
            "Build",
            "Balance (WMT)",
            "Grade Fe (%)",
            "Grade Si (%)",
            "Grade Al (%)",
            "Grade P (%)",
            "Grade Mn (%)",
        ]
        if self.is_total_feed_operating_crusher():
            headers.append("Max Reclaim Rate (t/h)")
        headers.append("Reclaim Threshold (WMT)")
        return headers

    def stockpile_table_column_index(self, caption):
        for column in range(self.stockpile_table.columnCount()):
            header_item = self.stockpile_table.horizontalHeaderItem(column)
            if header_item and header_item.text() == caption:
                return column
        return None

    def setup_stockpile_table(self):
        """Setup for the stockpile table in the new Stockpiles tab with live conditional formatting."""
        headers = self.stockpile_inventory_headers()
        self.stockpile_table.setColumnCount(len(headers))
        self.stockpile_table.setHorizontalHeaderLabels(headers)
        self.stockpile_table.verticalHeader().setVisible(False)

        # Bold headers
        header_font = self.stockpile_table.horizontalHeader().font()
        header_font.setBold(True)
        self.stockpile_table.horizontalHeader().setFont(header_font)

        # Choose data source
        data_source = self.stockpile_data
        self.apply_aps_brand_guidance_to_stockpile_data()

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

            brand_item = QTableWidgetItem(str(attributes.get("aps_brand_summary", "")))
            brand_item.setFlags(Qt.ItemIsEnabled)
            brand_item.setTextAlignment(Qt.AlignCenter)
            self.stockpile_table.setItem(row_idx, 3, brand_item)

            nearest_crusher = str(
                attributes.get(
                    "nearest_crusher",
                    attributes.get("NEAREST_CRUSHER", ""),
                )
                or ""
            )
            nearest_crusher_item = QTableWidgetItem(nearest_crusher)
            nearest_crusher_item.setFlags(Qt.ItemIsEnabled)
            nearest_crusher_item.setTextAlignment(Qt.AlignCenter)
            cycle_minutes = attributes.get(
                "rehandle_cycle_time_minutes",
                attributes.get("REHANDLE_CYCLE_TIME_MINUTES"),
            )
            if cycle_minutes not in (None, ""):
                try:
                    cycle_tooltip = (
                        f"Shortest selected haul cycle: "
                        f"{float(cycle_minutes):g} min"
                    )
                except (TypeError, ValueError):
                    cycle_tooltip = "Shortest selected haul cycle unavailable"
                nearest_crusher_item.setToolTip(cycle_tooltip)
            self.stockpile_table.setItem(
                row_idx,
                headers.index("Nearest Crusher"),
                nearest_crusher_item,
            )

            # Attributes (Balance and Grades, Center-aligned)
            keys = ["BUILD", "BALANCE", "GRADE_FE", "GRADE_SI", "GRADE_AL", "GRADE_P", "GRADE_MN"]
       
            for col_idx, key in enumerate(
                keys,
                start=headers.index("Build"),
            ):

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

            if self.is_total_feed_operating_crusher():
                max_reclaim_rate = attributes.get(
                    "max_reclaim_rate",
                    attributes.get("MAX_RECLAIM_RATE", 1000),
                )
                try:
                    max_reclaim_rate = float(max_reclaim_rate)
                except (TypeError, ValueError):
                    max_reclaim_rate = 1000.0
                max_rate_item = QTableWidgetItem(f"{max_reclaim_rate:g}")
                max_rate_item.setTextAlignment(Qt.AlignCenter)
                self.stockpile_table.setItem(
                    row_idx,
                    headers.index("Max Reclaim Rate (t/h)"),
                    max_rate_item,
                )

            # Reclaim Threshold (Editable, Center-aligned)
            reclaim_value = attributes.get("reclaim_threshold", 0)
            reclaim_value = round(float(reclaim_value))  # Ensure reclaim threshold is rounded
            reclaim_item = QTableWidgetItem(str(reclaim_value))
            reclaim_item.setTextAlignment(Qt.AlignCenter)
            self.stockpile_table.setItem(
                row_idx,
                headers.index("Reclaim Threshold (WMT)"),
                reclaim_item,
            )

        # Resize Columns
        for column, header in enumerate(headers):
            resize_mode = (
                QHeaderView.Stretch
                if header.startswith("Grade ")
                else QHeaderView.ResizeToContents
            )
            self.stockpile_table.horizontalHeader().setSectionResizeMode(
                column, resize_mode
            )

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
        """Handle validation formatting for editable inventory properties."""
        headers = self.stockpile_inventory_headers()

        if (
            "Max Reclaim Rate (t/h)" in headers
            and column == headers.index("Max Reclaim Rate (t/h)")
        ):
            rate_item = self.stockpile_table.item(row, column)
            if rate_item is None:
                return
            try:
                valid_rate = float(rate_item.text()) > 0
            except (AttributeError, TypeError, ValueError):
                valid_rate = False
            rate_item.setForeground(QColor("green" if valid_rate else "red"))
            return

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
        max_rate_column = self.stockpile_table_column_index(
            "Max Reclaim Rate (t/h)"
        )
        reclaim_threshold_column = self.stockpile_table_column_index(
            "Reclaim Threshold (WMT)"
        )

        if max_rate_column is not None:
            for row in range(self.stockpile_table.rowCount()):
                stockpile_item = self.stockpile_table.item(row, 2)
                max_rate_item = self.stockpile_table.item(row, max_rate_column)
                stockpile_name = stockpile_item.text() if stockpile_item else ""
                try:
                    max_reclaim_rate = float(max_rate_item.text())
                except (AttributeError, TypeError, ValueError):
                    max_reclaim_rate = 0.0
                if max_reclaim_rate <= 0:
                    QMessageBox.warning(
                        self,
                        "Invalid Input",
                        f"Max Reclaim Rate must be greater than 0 t/h for "
                        f"{stockpile_name or 'each stockpile'}.",
                    )
                    return
                if stockpile_name in self.stockpile_data:
                    self.stockpile_data[stockpile_name][
                        "max_reclaim_rate"
                    ] = max_reclaim_rate

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
                    reclaim_item = self.stockpile_table.item(
                        row, reclaim_threshold_column
                    )
                    reclaim_threshold = float(reclaim_item.text()) if reclaim_item else 0

                    # Update stockpile data
                    updated_stockpile_data[stockpile_name] = self.stockpile_data.get(stockpile_name, {})
                    updated_stockpile_data[stockpile_name]["reclaim_threshold"] = reclaim_threshold
                    if max_rate_column is not None:
                        updated_stockpile_data[stockpile_name][
                            "max_reclaim_rate"
                        ] = float(
                            self.stockpile_table.item(
                                row, max_rate_column
                            ).text()
                        )
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
            self.save_active_scenario_state()
            # Enable the next tab (Calendar Tab)
            self.setup_calendar()
            has_AMT_stockpiles = any(value.get("amt", False) for value in self.updated_stockpile_data.values())

            if has_AMT_stockpiles:
                self.setup_AMT_stockpile_table()
                self.set_page_enabled(self.AMT_stockpile_tab_index, True)
                self.show_page(self.AMT_stockpile_tab_index)  # Switch to AMT tab
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
        self.set_page_enabled(self.blend_config_tab_index, True)

    def fetch_optimised_blend_report(self):
        connection = sqlite3.connect(get_database_path())
        try:
            return pd.read_sql(
                "SELECT * FROM optimised_blend_report",
                connection,
            )
        except (sqlite3.Error, pd.errors.DatabaseError):
            return pd.DataFrame()
        finally:
            connection.close()

    def prepopulate_manual_from_optimised_result(
        self, automatic=False
    ):
        has_manual_plan = bool(
            getattr(
                self, "stored_blend_sequence_table_for_gantt", []
            )
            or getattr(self, "manual_direct_tip_allocations", {})
            or getattr(self, "saved_blends_for_schedule", [])
            or getattr(self, "blend_config_table_inputs", {})
        )
        if automatic and has_manual_plan:
            return False
        if not automatic and has_manual_plan:
            reply = QMessageBox.question(
                self,
                "Replace Manual Blend Plan?",
                "This will replace the current Setup Blends, Blend Sequence, "
                "and manual direct-tip selections with the latest optimized "
                "result. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return False

        optimised_report = self.fetch_optimised_blend_report()
        if optimised_report.empty:
            if not automatic:
                QMessageBox.information(
                    self,
                    "Prepopulate Manual Blending",
                    "Run the optimiser first. No optimized blend result is "
                    "available for this site scenario.",
                )
            return False

        previous_manual_state = {
            field: copy.deepcopy(getattr(self, field, None))
            for field in (
                "blend_config_table_inputs",
                "saved_blends_for_schedule",
                "stored_blend_sequence_table_for_gantt",
                "stored_blend_sequence_table_for_gantt_default",
                "manual_direct_tip_allocations",
                "manual_steady_states",
                "manual_blend_report",
                "crusher_rate",
                "crusher_rate_input_value",
                "crusher_rate_input_values",
            )
        }
        try:
            transfer = OptimisedToManualPlan(
                optimised_report,
                getattr(self, "updated_stockpile_data", {}),
            ).build()

            self.blend_config_table_inputs = copy.deepcopy(
                transfer["blend_config_table_inputs"]
            )
            transferred_rates = self.calendar_crusher_rate_values()
            transferred_rates.update({
                period: rate
                for period, rate in (
                    transfer.get("period_crusher_rates", {}) or {}
                ).items()
                if float(rate or 0) > 0
            })
            self.set_manual_crusher_rate_inputs(transferred_rates)
            self.saved_blends_for_schedule = copy.deepcopy(
                transfer["blend_definitions"]
            )
            imported_sequence = copy.deepcopy(
                transfer["sequence_rows"]
            )
            self.stored_blend_sequence_table_for_gantt = (
                imported_sequence
            )
            self.stored_blend_sequence_table_for_gantt_default = []
            self.manual_gantt_legend_and_tooltip = (
                self.saved_blends_for_schedule
            )

            planner = self.create_manual_blend_planner()
            states = planner.build_steady_states()
            allocations = OptimisedToManualPlan.direct_tip_allocations(
                states, transfer["direct_tip_rows"]
            )
            report = planner.build_report(states, allocations)
        except Exception as error:
            for field, value in previous_manual_state.items():
                setattr(self, field, value)
            if automatic:
                print(
                    "Manual prepopulation from optimized result was not "
                    f"completed: {error}"
                )
            else:
                QMessageBox.warning(
                    self,
                    "Prepopulate Manual Blending",
                    str(error),
                )
            return False

        self.manual_direct_tip_allocations = allocations
        self.manual_steady_states = states
        self.manual_blend_report = report
        DatabaseManager().write_manual_blend_report_to_database(report)

        # Rebuild Setup Blends so every optimized source-to-ratio pattern is
        # visible, including plans containing more than five Blend IDs.
        if hasattr(self, "blend_config_table"):
            previous_project_loaded = self.is_project_loaded
            self.is_project_loaded = True
            try:
                self.populate_blend_config_table()
                self.populate_blend_config_weights_and_ids()
                self.update_blend_results()
            finally:
                self.is_project_loaded = previous_project_loaded

        previous_project_loaded = self.is_project_loaded
        self.is_project_loaded = True
        try:
            self.setup_sequence_tab()
            self.populate_blend_sequence_table_if_project_is_loaded()
            self.apply_manual_gantt_rows_to_table(imported_sequence)
        finally:
            self.is_project_loaded = previous_project_loaded

        # Table hydration contains user-facing columns only. Restore the
        # optimized-state metadata used to preserve exact decision boundaries
        # and per-state crusher rates.
        self.stored_blend_sequence_table_for_gantt = imported_sequence
        self.manual_gantt_legend_and_tooltip = (
            self.saved_blends_for_schedule
        )
        self.start_or_update_dash_manual_chart_thread()
        self.set_page_enabled(self.blend_sequence_tab_index, True)
        self.set_page_enabled(self.grade_profile_tab_index, True)
        self.refresh_sqlite_reports()
        self.save_active_scenario_state()

        if not automatic:
            QMessageBox.information(
                self,
                "BlendMaster",
                f"Manual blending was prepopulated from "
                f"{transfer['steady_state_count']} optimized steady "
                f"state(s) using {transfer['blend_count']} manual blend "
                f"definition(s).\n\n"
                f"Transferred selected direct tip: "
                f"{transfer['direct_tip_tonnes']:,.1f} t.",
            )
            self.show_page(self.blend_sequence_tab_index)
        return True

    def navigate_to_solver_configuration(self):
        self.load_solver_config_inputs()
        self.set_page_enabled(self.solver_config_tab_index, True)
        self.set_page_enabled(self.decision_levers_tab_index, True)
        self.show_page(self.solver_config_tab_index)

    def handle_solver_configuration_submit(self):
        if not self.store_solver_config_inputs():
            return
        self.save_active_scenario_state()
        self.navigate_to_product_build_settings()

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

        if getattr(self, "agent_workflow_waiting_for_amt", False):
            self.agent_workflow_waiting_for_amt = False
            QTimer.singleShot(250, self.agent_workflow_apply_amt_stockpiles)

    def handle_AMT_stockpile_fetch_error(self, error_message):
        project_load_failed = bool(
            getattr(self, "project_load_restore_in_progress", False)
        )
        self.project_load_waiting_for_AMT = False
        self.agent_workflow_waiting_for_amt = False
        if getattr(self, "agent_workflow_active", False):
            self.stop_agent_workflow_apply(f"Agent workflow stopped while fetching AMT stockpile data: {error_message}")
        self.project_load_restore_in_progress = False
        if project_load_failed:
            self.finish_project_load_ui(success=False)
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
            self.AMT_map_frame.setObjectName("amtMapFrame")
            self.AMT_map_frame.setFrameShape(QFrame.NoFrame)
            frame_layout = QVBoxLayout()
            frame_layout.setContentsMargins(8, 8, 8, 8)
            frame_layout.setSpacing(0)
            frame_layout.addWidget(self.AMT_map_view, stretch=1)
            self.AMT_map_frame.setLayout(frame_layout)

            # Horizontal layout for map view and button
            self.AMT_stockpile_tab_vertical_layout = QVBoxLayout()
            self.AMT_stockpile_tab_vertical_layout.setContentsMargins(0, 0, 0, 0)
            self.AMT_stockpile_tab_vertical_layout.setSpacing(8)
            self.AMT_stockpile_tab_vertical_layout.addWidget(self.AMT_map_frame)

            # Add a button to load the chart
            self.load_AMT_button = QPushButton("Load or Update AMT Map")
            self.load_AMT_button.setObjectName("loadAMTMapButton")
            self.style_green_action_button(self.load_AMT_button, 220)
            self.load_AMT_button.clicked.connect(self.load_AMT_map)  # Connect button to function

            # Add Submit Button at the Bottom
            submit_button = QPushButton("Submit")
            submit_button.setObjectName("submitAMTChunksButton")
            submit_button.setMinimumWidth(110)
            submit_button.clicked.connect(self.store_hex_sequence_table)

            # Align button to the bottom-left using layout
            button_layout = QHBoxLayout()
            button_layout.setContentsMargins(0, 0, 0, 0)
            button_layout.addWidget(self.load_AMT_button)
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
            self.save_active_scenario_state()
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
        stockpile_reclaim_rates = self.is_total_feed_operating_crusher()
        reclaim_rate_editables = [
            not stockpile_reclaim_rates,
            not stockpile_reclaim_rates,
            not stockpile_reclaim_rates,
        ]
        reclaim_rate_defaults = (
            ["Per stockpile", "Per stockpile", "Per stockpile"]
            if stockpile_reclaim_rates
            else ["1000", "1000", "1000"]
        )

        self.calendar_rows.extend([
            ("Reclaim Equipment", [False, False, False], "green", ["", "", ""]),
            {"reclaim_equipment_max_reclaim_rate": (
                "  Max Reclaim Rate",
                reclaim_rate_editables,
                "green",
                reclaim_rate_defaults,
            )},

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

        selected_stockpiles = list(getattr(self, "updated_stockpile_data_keys", []) or [])
        selected_stockpile_keys = {str(stockpile).strip().upper() for stockpile in selected_stockpiles}
        aps_destination_stockpiles = []
        if getattr(self, "file_path_choice", None):
            try:
                aps_destination_stockpiles = ExpitDataHandler.get_distinct_stockpile_destinations(
                    self.file_path_choice
                )
            except Exception as error:
                print(f"Unable to read APS destination stockpiles for Calendar: {error}")

        self.calendar_build_only_stockpiles = {
            str(stockpile).strip().upper()
            for stockpile in aps_destination_stockpiles
            if str(stockpile).strip().upper() not in selected_stockpile_keys
        }
        self.calendar_stockpile_names = selected_stockpiles + [
            stockpile
            for stockpile in aps_destination_stockpiles
            if str(stockpile).strip().upper() not in selected_stockpile_keys
        ]

        for stockpile in self.calendar_stockpile_names:
            default_state = (
                "Build"
                if str(stockpile).strip().upper() in self.calendar_build_only_stockpiles
                else "Auto"
            )
            self.calendar_rows.append({f"stockpiles_{stockpile.lower()}" : (f"  {stockpile}", [False, False, False], "red", ["", "", ""])})
            self.calendar_rows.append({f"stockpiles_{stockpile.lower()}_state" : (f"    State", [True, True, True], "red", [default_state, default_state, default_state])})
            self.calendar_rows.append({f"stockpiles_{stockpile.lower()}_maximum_quantity": (f"    Maximum Quantity", [True, True, True], "red", ["100000", "100000", "100000"])})

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
            "green": QColor("#e5f7ed"),
            "blue": QColor("#e8f0ff"),
            "red": QColor("#ffe7ea"),
        }
        section_colors = {
            "green": QColor("#c8f0d6"),
            "blue": QColor("#d7e4ff"),
            "red": QColor("#ffd3da"),
        }
        section_foreground = {
            "green": QColor("#14532d"),
            "blue": QColor("#1e3a8a"),
            "red": QColor("#7f1d1d"),
        }

        # Configure the main table
        self.main_table.setColumnCount(len(self.calendar_headers))
        self.main_table.setRowCount(len(self.calendar_rows))
        self.main_table.setHorizontalHeaderLabels(self.calendar_headers)
        self.main_table.verticalHeader().setVisible(False)
        self.main_table.setAlternatingRowColors(True)
        self.main_table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.main_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.main_table.setShowGrid(True)
        self.main_table.setStyleSheet("""
            QTableWidget {
                background-color: #ffffff;
                alternate-background-color: #f8fafc;
                gridline-color: #d8e0ea;
                border: 1px solid #d8e0ea;
                border-radius: 6px;
                selection-background-color: #bfdbfe;
                selection-color: #0f172a;
            }
            QHeaderView::section {
                background-color: #f1f5f9;
                color: #172033;
                font-weight: 700;
                border: 0;
                border-right: 1px solid #d8e0ea;
                border-bottom: 1px solid #cbd5e1;
                padding: 7px 8px;
            }
            QTableWidget::item {
                padding: 5px 8px;
            }
            QComboBox {
                background-color: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                padding: 3px 8px;
            }
        """)

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
            is_section_row = not any(editables)
            
            # Caption Column
            item_caption = QTableWidgetItem(caption)
            item_caption.setFlags(Qt.ItemIsEnabled)  # Non-editable
            item_caption.setFont(bold_font)
            item_caption.setForeground(QBrush(section_foreground[color_group]))
            item_caption.setBackground(QBrush(
                section_colors[color_group] if is_section_row else parent_colors[color_group]
            ))
            if (
                row_key == "reclaim_equipment_max_reclaim_rate"
                and self.is_total_feed_operating_crusher()
            ):
                item_caption.setToolTip(
                    "Total_Feed scenarios use Max Reclaim Rate from each "
                    "Stockpile Inventories footprint."
                )
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
                    item.setBackground(QBrush(
                        section_colors[color_group] if is_section_row else QColor("#eef2f7")
                    ))
                    if (
                        row_key == "reclaim_equipment_max_reclaim_rate"
                        and self.is_total_feed_operating_crusher()
                    ):
                        item.setToolTip(
                            "Configured per footprint in Stockpile Inventories."
                        )
                    if is_section_row:
                        item.setFont(bold_font)
                else:
                    item.setBackground(QBrush(QColor("#ffffff")))
                self.main_table.setItem(row_idx, col_idx, item)
            self.main_table.setRowHeight(row_idx, 30)

        # Resize Columns
        self.main_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.main_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.main_table.setColumnWidth(0, max(260, self.main_table.columnWidth(0)))

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

            stockpile_reclaim_rates = self.is_total_feed_operating_crusher()
            self.calendar_rows[1]["reclaim_equipment_max_reclaim_rate"] = (
                "  Max Reclaim Rate",
                [not stockpile_reclaim_rates] * 3,
                "green",
                (
                    ["Per stockpile"] * 3
                    if stockpile_reclaim_rates
                    else calendar_values(
                        "reclaim_equipment_max_reclaim_rate",
                        thousand_defaults,
                    )
                ),
            )
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

            for stockpile in getattr(self, "calendar_stockpile_names", self.updated_stockpile_data_keys):
                default_state = (
                    "Build"
                    if str(stockpile).strip().upper() in getattr(self, "calendar_build_only_stockpiles", set())
                    else "Auto"
                )
                # Populate the rows using calendar_index
                self.calendar_rows[calendar_index][f"stockpiles_{stockpile.lower()}"] = (
                    f"  {stockpile}", [False, False, False], "red", ["", "", ""]
                )
                self.calendar_rows[calendar_index + 1][f"stockpiles_{stockpile.lower()}_state"] = (
                    f"    State", [True, True, True], "red",
                    calendar_values(
                        f"stockpiles_{stockpile.lower()}_state",
                        {header: default_state for header in self.calendar_headers[1:]},
                    )
                )
                self.calendar_rows[calendar_index + 2][f"stockpiles_{stockpile.lower()}_maximum_quantity"] = (
                    f"    Maximum Quantity", [True, True, True], "red",
                    calendar_values(
                        f"stockpiles_{stockpile.lower()}_maximum_quantity",
                        {header: 100000 for header in self.calendar_headers[1:]},
                    )
                )
                calendar_index += 3
                    
            self.populate_calendar()
            self.store_calendar_inputs_no_run()

    def get_main_table_cell_text(self, row_idx, col_idx):
        widget = self.main_table.cellWidget(row_idx, col_idx)
        if isinstance(widget, QComboBox):
            return widget.currentText().strip()

        item = self.main_table.item(row_idx, col_idx)
        return item.text().strip() if item and item.text().strip() else None

    @staticmethod
    def normalized_calendar_input_value(outer_key, value):
        if value is None or "state" in str(outer_key).lower():
            return value
        try:
            return float(value)
        except (TypeError, ValueError):
            return value

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
                    self.normalized_calendar_input_value(
                        outer_key, inner_value
                    )
                )
                for inner_key, inner_value in outer_value.items()
            }
            for outer_key, outer_value in self.calendar_inputs.items()
        }

        # Calendar Cash is retained in the project schema for compatibility,
        # but it is no longer a user input or an optimisation objective term.
        zero_cash = {header: 0.0 for header in headers}
        for stockpile in getattr(self, "calendar_stockpile_names", []):
            self.calendar_inputs[
                f"stockpiles_{str(stockpile).lower()}_cash"
            ] = copy.deepcopy(zero_cash)

        self.store_stockpile_constraint_inputs()
        self.calendar_inputs["product_build_settings"] = copy.deepcopy(getattr(self, "product_build_settings", []))
        self.calendar_inputs["product_brand_labels"] = copy.deepcopy(self.product_brand_options())

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
        self.throughput_incentive_input.setText(str(
            solver_config.get("throughput_incentive_per_tonne", 1_000_100.0)
        ))
        self.source_selection_tie_break_penalty_input.setText(str(
            solver_config.get("source_selection_tie_break_penalty", 0.001)
        ))
        feasibility_label = {
            "stockpile_must_be_feasible": "Stockpile blend must be feasible",
            "stockpile_can_rely_on_grade_blocks": "Stockpile blend can rely on grade blocks"
        }.get(
            solver_config.get("stockpile_feasibility_mode", "stockpile_must_be_feasible"),
            "Stockpile blend must be feasible"
        )
        self.stockpile_feasibility_combo.setCurrentText(feasibility_label)
        self.allow_offspec_steady_states_checkbox.setChecked(
            bool(solver_config.get("allow_offspec_steady_states_for_product_build", False))
        )
        self.brand_guidance_enabled_checkbox.setChecked(
            bool(solver_config.get("brand_guidance_enabled", False))
        )
        self.brand_guidance_incentive_input.setText(str(solver_config.get("brand_guidance_incentive", 0.0)))
        self.timing_guidance_enabled_checkbox.setChecked(
            bool(solver_config.get("timing_guidance_enabled", False))
        )
        self.timing_guidance_incentive_input.setText(
            str(solver_config.get("timing_guidance_incentive", 0.0))
        )
        self.timing_guidance_tolerance_input.setText(
            str(solver_config.get("timing_guidance_tolerance_hours", 0.0))
        )
        self.active_blend_guidance_enabled_checkbox.setChecked(
            bool(solver_config.get("active_blend_guidance_enabled", False))
        )
        self.active_blend_guidance_incentive_input.setText(
            str(solver_config.get("active_blend_guidance_incentive", 0.0))
        )
        self.rehandle_cycle_time_penalty_checkbox.setChecked(
            bool(solver_config.get(
                "rehandle_cycle_time_penalty_enabled",
                False,
            ))
        )
        self.haulage_cost_per_hour_input.setText(str(
            solver_config.get("haulage_cost_per_hour", 0.0)
        ))
        self.update_rehandle_cycle_time_input_state()
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
        self.fewer_stockpiles_incentive_input.setText(str(
            solver_config.get("fewer_stockpiles_incentive", 10.0)
        ))
        balance_preference = solver_config.get("balance_preference", "none")
        balance_label = {
            "none": "No balance preference",
            "lower": "Lower balance first",
            "higher": "Higher balance first"
        }.get(balance_preference, "No balance preference")
        self.balance_preference_combo.setCurrentText(balance_label)
        self.balance_preference_incentive_input.setText(str(
            solver_config.get("balance_preference_incentive", 1.0)
        ))
        self.prefer_amt_stockpiles_checkbox.setChecked(bool(solver_config.get("prefer_amt_stockpiles", False)))
        self.amt_preference_incentive_input.setText(str(
            solver_config.get("amt_preference_incentive", 1.0)
        ))
        self.prefer_contaminated_stockpiles_checkbox.setChecked(bool(solver_config.get("prefer_contaminated_stockpiles", False)))
        self.contaminated_preference_incentive_input.setText(str(
            solver_config.get(
                "contaminated_preference_incentive",
                1.0,
            )
        ))
        contaminant_thresholds = solver_config.get("contaminant_thresholds", {})
        self.contaminant_si_threshold_input.setText(str(contaminant_thresholds.get("si", 5.0)))
        self.contaminant_al_threshold_input.setText(str(contaminant_thresholds.get("al", 3.0)))
        self.contaminant_p_threshold_input.setText(str(contaminant_thresholds.get("p", 0.1)))
        self.contaminant_mn_threshold_input.setText(str(contaminant_thresholds.get("mn", 0.1)))
        self.prefer_low_fe_stockpiles_checkbox.setChecked(bool(solver_config.get("prefer_low_fe_stockpiles", False)))
        self.low_fe_preference_incentive_input.setText(str(
            solver_config.get("low_fe_preference_incentive", 1.0)
        ))
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

        def parse_signed_input(input_widget, label, default=0.0):
            text = input_widget.text().strip()
            if not text:
                return default
            try:
                return float(text)
            except ValueError:
                if show_errors:
                    QMessageBox.warning(
                        self, "Invalid Input", f"{label} must be a number."
                    )
                return None

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
        throughput_incentive_per_tonne = parse_non_negative_input(
            self.throughput_incentive_input,
            "Throughput Incentive",
            1_000_100.0,
        )
        source_selection_tie_break_penalty = parse_non_negative_input(
            self.source_selection_tie_break_penalty_input,
            "Source Selection Tie-break Penalty",
            0.001,
        )
        fewer_stockpiles_incentive = parse_non_negative_input(
            self.fewer_stockpiles_incentive_input,
            "Fewer Stockpiles Incentive",
            10.0,
        )
        balance_preference_incentive = parse_non_negative_input(
            self.balance_preference_incentive_input,
            "Balance Preference Incentive",
            1.0,
        )
        amt_preference_incentive = parse_non_negative_input(
            self.amt_preference_incentive_input,
            "AMT Preference Incentive",
            1.0,
        )
        contaminated_preference_incentive = parse_non_negative_input(
            self.contaminated_preference_incentive_input,
            "Contaminated Stockpile Incentive",
            1.0,
        )
        low_fe_preference_incentive = parse_non_negative_input(
            self.low_fe_preference_incentive_input,
            "Low Grade Stockpile Incentive",
            1.0,
        )
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
        brand_guidance_incentive = parse_signed_input(
            self.brand_guidance_incentive_input,
            "2WP Product Guidance Reward/Penalty",
            0.0,
        )
        timing_guidance_incentive = parse_signed_input(
            self.timing_guidance_incentive_input,
            "2WP Source Timing Reward/Penalty",
            0.0,
        )
        timing_guidance_tolerance_hours = parse_non_negative_input(
            self.timing_guidance_tolerance_input,
            "2WP Source Timing Tolerance",
            0.0,
        )
        active_blend_guidance_incentive = parse_signed_input(
            self.active_blend_guidance_incentive_input,
            "2WP Active Blend Reward/Penalty",
            0.0,
        )
        haulage_cost_per_hour = parse_non_negative_input(
            self.haulage_cost_per_hour_input,
            "Haulage Cost",
            0.0,
        )
        min_feed_duration_hours = parse_optional_non_negative(
            min_feed_duration_text,
            "Min Stockpile Feed Duration",
        )

        if (
            any(value is None for value in contaminant_thresholds.values())
            or low_fe_threshold is None
            or throughput_incentive_per_tonne is None
            or source_selection_tie_break_penalty is None
            or fewer_stockpiles_incentive is None
            or balance_preference_incentive is None
            or amt_preference_incentive is None
            or contaminated_preference_incentive is None
            or low_fe_preference_incentive is None
            or direct_tip_cash_incentive is None
            or stay_on_same_blend_incentive is None
            or blend_option_timeout_seconds is None
            or max_blend_options_per_steady_state is None
            or min_grade_block_pair_duration_hours is None
            or stay_on_same_grade_block_pair_incentive is None
            or brand_guidance_incentive is None
            or timing_guidance_incentive is None
            or timing_guidance_tolerance_hours is None
            or active_blend_guidance_incentive is None
            or haulage_cost_per_hour is None
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
        brand_guidance_enabled = self.brand_guidance_enabled_checkbox.isChecked()
        brand_guidance_mode = "prefer_match" if brand_guidance_enabled else "ignore"
        rehandle_penalty_enabled = (
            self.rehandle_cycle_time_penalty_checkbox.isChecked()
        )
        if rehandle_penalty_enabled:
            if haulage_cost_per_hour <= 0:
                if show_errors:
                    QMessageBox.warning(
                        self,
                        "Invalid Input",
                        "Haulage Cost must be greater than 0 $/hr when "
                        "Rehandle Cycle Time Penalty is enabled.",
                    )
                return False
            missing_routes = [
                stockpile
                for stockpile, attributes in (
                    getattr(self, "updated_stockpile_data", {}) or {}
                ).items()
                if not attributes.get("rehandle_cycle_time_minutes")
            ]
            if missing_routes:
                if show_errors:
                    QMessageBox.warning(
                        self,
                        "Missing Haul Cycles",
                        "No selected-crusher haul cycle was found for: "
                        + ", ".join(sorted(missing_routes)),
                    )
                return False

        self.solver_config = {
            "throughput_incentive_per_tonne": throughput_incentive_per_tonne,
            "source_selection_tie_break_penalty": source_selection_tie_break_penalty,
            "stockpile_feasibility_mode": stockpile_feasibility_mode,
            "allow_offspec_steady_states_for_product_build": self.allow_offspec_steady_states_checkbox.isChecked(),
            "brand_guidance_mode": brand_guidance_mode,
            "brand_guidance_enabled": brand_guidance_enabled,
            "brand_guidance_incentive": brand_guidance_incentive,
            "timing_guidance_enabled": self.timing_guidance_enabled_checkbox.isChecked(),
            "timing_guidance_incentive": timing_guidance_incentive,
            "timing_guidance_tolerance_hours": timing_guidance_tolerance_hours,
            "active_blend_guidance_enabled": self.active_blend_guidance_enabled_checkbox.isChecked(),
            "active_blend_guidance_incentive": active_blend_guidance_incentive,
            "rehandle_cycle_time_penalty_enabled": rehandle_penalty_enabled,
            "haulage_cost_per_hour": haulage_cost_per_hour,
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
            "fewer_stockpiles_incentive": fewer_stockpiles_incentive,
            "balance_preference": balance_preference,
            "balance_preference_incentive": balance_preference_incentive,
            "prefer_amt_stockpiles": self.prefer_amt_stockpiles_checkbox.isChecked(),
            "amt_preference_incentive": amt_preference_incentive,
            "prefer_contaminated_stockpiles": self.prefer_contaminated_stockpiles_checkbox.isChecked(),
            "contaminated_preference_incentive": contaminated_preference_incentive,
            "contaminant_thresholds": contaminant_thresholds,
            "prefer_low_fe_stockpiles": self.prefer_low_fe_stockpiles_checkbox.isChecked(),
            "low_fe_preference_incentive": low_fe_preference_incentive,
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

        valid_ratio_group, ratio_message = self.validate_active_ratio_group_for_run()
        if not valid_ratio_group:
            QMessageBox.information(
                self,
                "Crusher Contribution Ratios",
                ratio_message,
            )
            return

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
            self.normalized_calendar_input_value(outer_key, inner_value)
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
        self.calendar_inputs["product_build_settings"] = copy.deepcopy(getattr(self, "product_build_settings", []))
        self.calendar_inputs["product_brand_labels"] = copy.deepcopy(self.product_brand_options())
        self.calendar_inputs["site_context"] = self.active_site_context()
        self.save_active_scenario_state()

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
            self.file_path_24hr_choice,
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
            self.active_site_context(),
            self.file_path_choice,
            getattr(self, "selected_24hr_expit_agents", []),
        )

    def finish_project_load_ui(self, success):
        """Return project loading to Site Configuration and notify once."""
        show_success = bool(
            getattr(self, "project_load_show_success", False)
        )
        source_label = str(
            getattr(self, "project_load_source_label", "") or ""
        )
        self.project_load_keep_site_configuration_visible = False
        self.project_load_show_success = False
        self.project_load_source_label = ""
        self.show_page(self.site_config_tab_index)
        if success and show_success:
            message = "Project loaded successfully!"
            if source_label:
                message += f"\n\n{source_label}"
            QMessageBox.information(
                self,
                "BlendMaster",
                message,
            )

    def finish_run_program(self, periods):
        self.set_start_and_end_datetime(periods=periods)
        self.update_decision_point_tab_state()
        self.activate_manual_setup_tab()
        self.prepopulate_manual_from_optimised_result(automatic=True)
        self.refresh_sqlite_reports()
        self.start_dash_optimised_charts_thread()
        self.save_active_scenario_state()

        if self.project_load_continuation_pending:
            self.project_load_continuation_pending = False
            self.on_blend_data_change()
            self.store_blend_results()
            self.finish_project_load_ui(success=True)

    def handle_run_program_error(self, error_message):
        project_load_failed = bool(
            self.project_load_continuation_pending
        )
        self.project_load_continuation_pending = False
        if project_load_failed:
            self.finish_project_load_ui(success=False)

        error_title = "Error"
        error_text = str(error_message)
        if isinstance(error_message, dict):
            error_title = error_message.get("title") or error_title
            error_text = str(error_message.get("message", ""))

        if hasattr(self, "decision_status_label"):
            self.decision_status_label.setText(f"{error_title}. Review diagnostics below.")
        if hasattr(self, "decision_output"):
            self.display_decision_output(f"\n--- {error_title} ---\n{error_text}")

        self.set_page_enabled(self.calendar_tab_index, True)
        self.set_page_enabled(self.decision_point_tab_index, True)
        for tab_index in [
            self.results_tab_index,
            self.profiles_tab_index,
            self.sqlite_reports_tab_index,
            self.optimised_grade_profile_tab_index,
        ]:
            self.set_page_enabled(tab_index, False)
        self.decision_input.setEnabled(False)
        self.enter_button.setEnabled(False)
        self.decision_select_button.setEnabled(False)
        self.show_page(self.decision_point_tab_index)
        self.show_error_popup(error_message)

    def setup_results_tab(self):
        self.results_tab = QWidget()
        self.results_tab.setObjectName("resultsTab")
        self.results_tab_index = self.register_page(
            "optimised_blend_sequence",
            self.results_tabs,
            self.results_tab,
            "Optimised Blend Sequence",
            position=0,
        )
        self.results_layout = QVBoxLayout(self.results_tab)
        self.results_layout.setContentsMargins(12, 10, 12, 10)
        self.results_layout.setSpacing(10)
        self.results_tab.setStyleSheet("""
            QWidget#resultsTab {
                background-color: #f8fafc;
            }
            QFrame#resultsChartFrame {
                background-color: #ffffff;
                border: 1px solid #d8e0ea;
                border-radius: 6px;
            }
            QPushButton#loadResultsChartButton {
                background-color: #0f766e;
                color: white;
                border: 1px solid #0f766e;
                border-radius: 4px;
                font-size: 14px;
                font-weight: 650;
                padding: 8px 14px;
            }
            QPushButton#loadResultsChartButton:hover {
                background-color: #115e59;
            }
            QPushButton#loadResultsChartButton:pressed {
                background-color: #134e4a;
            }
        """)

        header_layout = QVBoxLayout()
        header_layout.setSpacing(2)
        results_title = QLabel("Results")
        results_title.setStyleSheet("font-weight: 750; font-size: 20px; color: #172033;")
        results_subtitle = QLabel("Blend schedule, source mix and crusher performance")
        results_subtitle.setStyleSheet("font-size: 12px; color: #64748b;")
        header_layout.addWidget(results_title)
        header_layout.addWidget(results_subtitle)
        self.results_layout.addLayout(header_layout)

        # Create a QFrame
        self.top_frame = QFrame()
        self.top_frame.setObjectName("resultsChartFrame")
        self.top_frame.setFrameStyle(QFrame.NoFrame)

        # Add layout to the frame
        self.top_layout = QHBoxLayout()
        self.top_layout.setContentsMargins(8, 8, 8, 8)
        self.top_layout.setSpacing(0)
        self.top_frame.setLayout(self.top_layout)

        # Add the frame to the parent layout
        self.results_layout.addWidget(self.top_frame)

        # Top (Gantt Chart with CustomWebEngineView)
        self.gantt_chart_view = CustomWebEngineView()
        self.gantt_chart_view.setStyleSheet("border: 0; background-color: #ffffff;")
        self.top_layout.addWidget(self.gantt_chart_view)

        # Add a button to load the chart
        self.load_chart_button = QPushButton("Load or Update Chart")
        self.load_chart_button.setObjectName("loadResultsChartButton")
        self.style_green_action_button(self.load_chart_button, 210)
        self.load_chart_button.clicked.connect(self.load_gantt_chart)  # Connect button to function

        controls_layout = QHBoxLayout()
        controls_layout.addWidget(self.load_chart_button)
        controls_layout.addStretch()
        self.results_layout.addLayout(controls_layout)

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
            conn = sqlite3.connect(get_database_path())
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

        available_height = self.results_tab.height() - self.load_chart_button.sizeHint().height() - 92
        available_height = max(420, available_height)
        desired_height = max(420, min(desired_height, available_height))

        self.top_frame.setMinimumHeight(desired_height)
        self.top_frame.setMaximumHeight(desired_height)
    
    def load_manual_gantt_chart(self):
        # Load the Dash app into the QWebEngineView
        self.manual_gantt_view.setUrl(QUrl("http://localhost:8052"))
        
    def setup_profiles_tab(self):
        self.profiles_tab = QWidget()
        self.profiles_tab_index = self.register_page(
            "build_depletion_profiles",
            self.results_tabs,
            self.profiles_tab,
            "Build and Depletion Profiles",
        )
        self.profiles_layout = QVBoxLayout(self.profiles_tab)
        self.profiles_layout.setContentsMargins(12, 10, 12, 10)
        self.profiles_layout.setSpacing(8)

        # Create the bottom frame
        self.bottom_frame = QFrame()
        self.bottom_frame.setFrameStyle(QFrame.NoFrame)
        self.bottom_frame.setStyleSheet("""
            QFrame {
                background-color: #f8fafc;
                border: 1px solid #d8e0ea;
                border-radius: 6px;
            }
        """)

        # Add layout to the frame
        self.bottom_layout = QHBoxLayout()
        self.bottom_layout.setContentsMargins(8, 8, 8, 8)
        self.bottom_frame.setLayout(self.bottom_layout)

        # Add the frame to the parent layout
        self.profiles_layout.addWidget(self.bottom_frame)

        # Bottom Section (Stockpile Profiles Chart Placeholder)
        self.stockpile_profile_chart_view = CustomWebEngineView()  # Embed the Dash app
        self.stockpile_profile_chart_view.setStyleSheet("border: 0; background-color: #f8fafc;")
        self.bottom_layout.addWidget(self.stockpile_profile_chart_view)

        # Add a button to load the chart
        self.load_profile_chart_button = QPushButton("Load or Update Build and Depletion Profiles")
        self.style_green_action_button(self.load_profile_chart_button, 360)
        self.load_profile_chart_button.clicked.connect(self.load_profiles)  # Connect button to function

        # Add the button to the layout at the bottom-left
        self.profiles_layout.addWidget(self.load_profile_chart_button)

    def setup_sqlite_reports_tab(self):
        self.sqlite_reports_tab = QWidget()
        self.sqlite_reports_tab_index = self.register_page(
            "reports",
            self.results_tabs,
            self.sqlite_reports_tab,
            "Reports",
            position=2,
        )
        self.sqlite_reports_layout = QVBoxLayout(self.sqlite_reports_tab)

        controls_layout = QHBoxLayout()
        controls_layout.addWidget(QLabel("Report Table:"))

        self.sqlite_report_selector = QComboBox()
        self.sqlite_report_selector.currentIndexChanged.connect(
            self.handle_sqlite_report_selection
        )
        controls_layout.addWidget(self.sqlite_report_selector)

        refresh_button = QPushButton("Refresh Tables")
        refresh_button.clicked.connect(self.refresh_sqlite_reports)
        controls_layout.addWidget(refresh_button)
        controls_layout.addStretch()

        self.sqlite_reports_layout.addLayout(controls_layout)

        query_layout = QHBoxLayout()
        query_layout.addWidget(QLabel("SQL Query:"))
        self.sqlite_report_query = QLineEdit()
        self.sqlite_report_query.setPlaceholderText(
            'SELECT * FROM "table_name" LIMIT 100'
        )
        self.sqlite_report_query.returnPressed.connect(
            self.execute_sqlite_report_query
        )
        query_layout.addWidget(self.sqlite_report_query, stretch=1)

        run_query_button = QPushButton("Run Query")
        run_query_button.clicked.connect(self.execute_sqlite_report_query)
        query_layout.addWidget(run_query_button)
        self.sqlite_reports_layout.addLayout(query_layout)

        self.sqlite_report_status = QLabel("")
        self.sqlite_reports_layout.addWidget(self.sqlite_report_status)

        self.sqlite_report_table = CustomTableWidget()
        self.sqlite_report_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.sqlite_reports_layout.addWidget(self.sqlite_report_table)

    def get_sqlite_report_tables(self):
        try:
            conn = sqlite3.connect(get_database_path())
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
            QMessageBox.warning(self, "Reports", f"Unable to list SQLite tables: {e}")
            return []

    def refresh_sqlite_reports(self):
        current_table = self.sqlite_report_selector.currentText()
        current_query = self.sqlite_report_query.text().strip()
        tables = self.get_sqlite_report_tables()

        self.sqlite_report_selector.blockSignals(True)
        self.sqlite_report_selector.clear()
        self.sqlite_report_selector.addItems(tables)
        if current_table in tables:
            self.sqlite_report_selector.setCurrentText(current_table)
        self.sqlite_report_selector.blockSignals(False)

        selected_table = self.sqlite_report_selector.currentText()
        if not selected_table:
            self.clear_sqlite_report_preview()
            return
        if current_table == selected_table and current_query:
            self.sqlite_report_query.setText(current_query)
        else:
            self.sqlite_report_query.setText(
                self.default_sqlite_report_query(selected_table)
            )
        self.execute_sqlite_report_query()

    @staticmethod
    def default_sqlite_report_query(table_name):
        escaped_table_name = str(table_name).replace('"', '""')
        return f'SELECT * FROM "{escaped_table_name}" LIMIT 100'

    def handle_sqlite_report_selection(self):
        table_name = self.sqlite_report_selector.currentText()
        if not table_name:
            self.clear_sqlite_report_preview()
            return
        self.sqlite_report_query.setText(
            self.default_sqlite_report_query(table_name)
        )
        self.execute_sqlite_report_query()

    def clear_sqlite_report_preview(self):
        self.sqlite_report_table.clearContents()
        self.sqlite_report_table.setRowCount(0)
        self.sqlite_report_table.setColumnCount(0)
        self.sqlite_report_status.setText("")

    @staticmethod
    def is_read_only_report_query(query):
        normalized = str(query or "").strip()
        if normalized.endswith(";"):
            normalized = normalized[:-1].rstrip()
        if not normalized or ";" in normalized:
            return False
        first_word = normalized.split(None, 1)[0].lower()
        return first_word in {"select", "with"}

    @staticmethod
    def configure_read_only_sqlite_connection(conn):
        conn.execute("PRAGMA query_only = ON")

        denied_actions = {
            getattr(sqlite3, name)
            for name in (
                "SQLITE_INSERT",
                "SQLITE_UPDATE",
                "SQLITE_DELETE",
                "SQLITE_CREATE_INDEX",
                "SQLITE_CREATE_TABLE",
                "SQLITE_CREATE_TEMP_INDEX",
                "SQLITE_CREATE_TEMP_TABLE",
                "SQLITE_CREATE_TEMP_TRIGGER",
                "SQLITE_CREATE_TEMP_VIEW",
                "SQLITE_CREATE_TRIGGER",
                "SQLITE_CREATE_VIEW",
                "SQLITE_DROP_INDEX",
                "SQLITE_DROP_TABLE",
                "SQLITE_DROP_TEMP_INDEX",
                "SQLITE_DROP_TEMP_TABLE",
                "SQLITE_DROP_TEMP_TRIGGER",
                "SQLITE_DROP_TEMP_VIEW",
                "SQLITE_DROP_TRIGGER",
                "SQLITE_DROP_VIEW",
                "SQLITE_ALTER_TABLE",
                "SQLITE_REINDEX",
                "SQLITE_ANALYZE",
                "SQLITE_ATTACH",
                "SQLITE_DETACH",
            )
            if hasattr(sqlite3, name)
        }

        def authorize(action_code, _arg1, _arg2, _database_name, _trigger_name):
            if action_code in denied_actions:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorize)

    def load_selected_sqlite_report(self):
        """Compatibility alias for callers that previously loaded by table."""
        self.handle_sqlite_report_selection()

    def execute_sqlite_report_query(self):
        query = self.sqlite_report_query.text().strip()
        if not self.is_read_only_report_query(query):
            QMessageBox.warning(
                self,
                "Reports",
                "Reports accepts one read-only SELECT or WITH query at a time.",
            )
            return

        try:
            conn = sqlite3.connect(get_database_path())
            try:
                self.configure_read_only_sqlite_connection(conn)
                df = pd.read_sql_query(query, conn)
            finally:
                conn.close()
        except Exception as e:
            self.sqlite_report_status.setText(f"Query failed: {e}")
            QMessageBox.warning(self, "Reports", f"Unable to run query: {e}")
            return

        self.sqlite_report_status.setText(
            f"Query returned {len(df):,} row{'s' if len(df) != 1 else ''}."
        )
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
        self.optimised_grade_profile_tab.setObjectName("optimisedGradeProfileTab")
        self.optimised_grade_profile_tab_index = self.register_page(
            "optimised_grade_profiles",
            self.grade_profiles_tabs,
            self.optimised_grade_profile_tab,
            "Optimised",
        )
        self.optimised_grade_profile_layout = QVBoxLayout(self.optimised_grade_profile_tab)
        self.optimised_grade_profile_layout.setContentsMargins(12, 10, 12, 10)
        self.optimised_grade_profile_layout.setSpacing(8)
        self.optimised_grade_profile_tab.setStyleSheet("""
            QWidget#optimisedGradeProfileTab {
                background-color: #f8fafc;
            }
            QFrame#optimisedGradeProfileFrame {
                background-color: #ffffff;
                border: 1px solid #d8e0ea;
                border-radius: 6px;
            }
            QPushButton#loadOptimisedGradeProfileButton {
                background-color: #0f766e;
                color: white;
                border: 1px solid #0f766e;
                border-radius: 4px;
                font-size: 14px;
                font-weight: 650;
                padding: 8px 14px;
            }
            QPushButton#loadOptimisedGradeProfileButton:hover {
                background-color: #115e59;
            }
        """)

        self.optimised_grade_profile_frame = QFrame()
        self.optimised_grade_profile_frame.setObjectName("optimisedGradeProfileFrame")
        self.optimised_grade_profile_frame.setFrameShape(QFrame.NoFrame)

        frame_layout = QVBoxLayout(self.optimised_grade_profile_frame)
        frame_layout.setContentsMargins(8, 8, 8, 8)

        self.optimised_grade_profile_chart_view = CustomWebEngineView()
        self.optimised_grade_profile_chart_view.setStyleSheet("border: 0; background-color: #ffffff;")
        frame_layout.addWidget(self.optimised_grade_profile_chart_view)

        self.optimised_grade_profile_layout.addWidget(self.optimised_grade_profile_frame)

        self.load_optimised_grade_profile_chart_button = QPushButton("Load or Update Chart")
        self.load_optimised_grade_profile_chart_button.setObjectName("loadOptimisedGradeProfileButton")
        self.style_green_action_button(self.load_optimised_grade_profile_chart_button, 210)
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
            self.draw_optimised_grade_profile_chart = DrawOptimisedGradeProfiles(get_database_path(), 8055)
            self.dash_thread_optimised_grade_profile = threading.Thread(
                target=self.draw_optimised_grade_profile_chart.run_app,
                daemon=True
            )
            self.dash_thread_optimised_grade_profile.start()
            self.start_dash_optimised_grade_profile_first_call = False

    def start_dash_optimised_charts_thread(self):
        """Start the Dash app in a separate thread."""
        db_path = get_database_path()
        if hasattr(self, "draw_gantt_chart"):
            self.draw_gantt_chart.db_path = db_path
        else:
            self.draw_gantt_chart = DrawGanttChart(db_path, port=8050)
            self.dash_thread_gantt = threading.Thread(
                target=self.draw_gantt_chart.run_app,
                daemon=True,
            )
            self.dash_thread_gantt.start()

        if hasattr(self, "draw_stockpile_profile_chart"):
            self.draw_stockpile_profile_chart.db_path = db_path
        else:
            self.draw_stockpile_profile_chart = DrawStockProfiles(db_path, port=8051)
            self.dash_thread_stockpile_profile = threading.Thread(
                target=self.draw_stockpile_profile_chart.run_app,
                daemon=True,
            )
            self.dash_thread_stockpile_profile.start()

        self.update_chart_database_context()

    def start_dash_AMT_map_thread(self):
        """Start the Dash app in a separate thread."""
        
        db_path = get_database_path()

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
            self.set_page_enabled(self.decision_point_tab_index, True)
            self.show_page(self.decision_point_tab_index)
            self.decision_input.setEnabled(True) # Enable the input
            self.enter_button.setEnabled(True) # Enable the button
            self.decision_select_button.setEnabled(True)
            self.set_page_enabled(self.results_tab_index, True)
            self.set_page_enabled(self.profiles_tab_index, True)
            self.set_page_enabled(self.sqlite_reports_tab_index, True)
            self.set_page_enabled(self.optimised_grade_profile_tab_index, True)

        else:
            self.set_page_enabled(self.decision_point_tab_index, True)
            self.decision_input.setEnabled(False) # Disable the input
            self.enter_button.setEnabled(False) # Disable the button
            self.decision_select_button.setEnabled(False)
            self.set_page_enabled(self.results_tab_index, True)
            self.set_page_enabled(self.profiles_tab_index, True)
            self.set_page_enabled(self.sqlite_reports_tab_index, True)
            self.set_page_enabled(self.optimised_grade_profile_tab_index, True)
            self.show_page(self.results_tab_index)  # Switch to Results (optimised) tab

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
        if (title or "").lower() in {
            "infeasible run",
            "run aborted",
            "stockpile selection required",
            "product builds complete",
        }:
            QMessageBox.information(self, title, str(error_message))
            return
        QMessageBox.critical(self, title or "Error", str(error_message))
    
    def calendar_crusher_rate_values(self):
        saved = (
            (getattr(self, "calendar_inputs", {}) or {})
            .get("crusher_rate", {})
        )
        if not isinstance(saved, dict):
            saved = {}
        legacy = (
            getattr(self, "crusher_rate_input_value", None)
            or getattr(self, "crusher_rate", None)
            or 1000
        )
        result = {}
        for period in ("Preplan", "Period_1", "Period_2"):
            try:
                rate = float(saved.get(period, legacy) or legacy)
            except (TypeError, ValueError):
                rate = 1000.0
            result[period] = rate if rate > 0 else 1000.0
        return result

    def manual_crusher_rate_values(self):
        inputs = getattr(self, "crusher_rate_inputs", {}) or {}
        fallback = self.calendar_crusher_rate_values()
        values = {}
        for period in ("Preplan", "Period_1", "Period_2"):
            try:
                value = float(inputs[period].text())
            except (KeyError, TypeError, ValueError, AttributeError):
                value = float(
                    (getattr(self, "crusher_rate_input_values", {}) or {})
                    .get(period, fallback[period])
                )
            values[period] = value if value > 0 else fallback[period]
        return values

    def set_manual_crusher_rate_inputs(self, values=None):
        values = values or {}
        defaults = self.calendar_crusher_rate_values()
        normalized = {}
        for period in ("Preplan", "Period_1", "Period_2"):
            try:
                rate = float(values.get(period, defaults[period]))
            except (TypeError, ValueError):
                rate = defaults[period]
            normalized[period] = (
                rate if rate > 0 else defaults[period]
            )
        self.crusher_rate_input_values = normalized
        self.crusher_rate = normalized["Preplan"]
        self.crusher_rate_input_value = str(normalized["Preplan"])
        for period, widget in (
            getattr(self, "crusher_rate_inputs", {}) or {}
        ).items():
            blocked = widget.blockSignals(True)
            try:
                widget.setText(f"{normalized[period]:g}")
            finally:
                widget.blockSignals(blocked)

    def setup_blends_tab(self):
        
        if self.setup_blends_tab_first_call:
        
            initial_crusher_rates = (
                getattr(self, "crusher_rate_input_values", {}) or {}
            ) or self.calendar_crusher_rate_values()

            crusher_label = QLabel("Crusher Rates (t/h):")
            crusher_font = crusher_label.font()
            crusher_font.setBold(True)
            crusher_label.setFont(crusher_font)
            
            crusher_layout = QHBoxLayout()
            crusher_layout.addWidget(crusher_label)
            self.crusher_rate_inputs = {}
            for period, caption in (
                ("Preplan", "Preplan"),
                ("Period_1", "Period 1"),
                ("Period_2", "Period 2"),
            ):
                crusher_layout.addWidget(QLabel(caption))
                rate_input = QLineEdit()
                rate_input.setFixedWidth(100)
                rate_input.setText(
                    f"{float(initial_crusher_rates.get(period, 1000)):g}"
                )
                rate_input.setToolTip(
                    "Defaults from Calendar. Optimised prepopulation uses "
                    "the time-weighted crusher output rate for this period; "
                    "each imported sequence state still retains its exact "
                    "output rate."
                )
                rate_input.textChanged.connect(
                    self.on_blend_data_change
                )
                self.crusher_rate_inputs[period] = rate_input
                crusher_layout.addWidget(rate_input)
            # Legacy alias retained for project compatibility.
            self.crusher_rate_input = self.crusher_rate_inputs["Preplan"]
            self.set_manual_crusher_rate_inputs(initial_crusher_rates)
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
            self.prepopulate_manual_button = QPushButton(
                "Prepopulate from Optimised Result"
            )
            self.style_green_action_button(
                self.prepopulate_manual_button, 260
            )
            self.prepopulate_manual_button.clicked.connect(
                self.prepopulate_manual_from_optimised_result
            )

            # Align button to the bottom-left using layout
            button_layout = QHBoxLayout()
            button_layout.addWidget(submit_button)
            button_layout.addWidget(self.prepopulate_manual_button)
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
            "Blend IDs", "Weights by Blend ID",
            "Reclaim Rate (t/h) by Blend ID",
            "Source Ratio of Total Feed"
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

            # A stockpile can participate in several manual blends. The same
            # weight is used in each selected blend and normalized separately
            # against that blend's other selected stockpiles.
            blend_ids_combo = MultiSelectBlendComboBox(
                blend_ids=self.manual_blend_id_options()
            )
            blend_ids_combo.setToolTip(
                "Select one or more Blend IDs. Configure each selected "
                "blend's weight in the adjacent column."
            )
            blend_ids_combo.selectionChanged.connect(
                lambda row=row_idx: self.on_manual_blend_membership_change(row)
            )
            self.blend_config_table.setCellWidget(
                row_idx,
                len(keys) + 4,
                blend_ids_combo,
            )
            
            # Weight (Editable)
            weight_item = QTableWidgetItem("0")
            weight_item.setTextAlignment(Qt.AlignCenter)
            weight_item.setForeground(QColor("green"))
            weight_item.setToolTip(
                "For one blend, enter a number. For several blends, use "
                "'1: 50; 2: 30'. Weights are normalized within each blend."
            )
            self.blend_config_table.setItem(row_idx, len(keys) + 5, weight_item)

            # Reclaim Rate (Auto-calculated)
            reclaim_item = QTableWidgetItem("0")
            reclaim_item.setFlags(Qt.ItemIsEnabled)  # Make it uneditable
            reclaim_item.setTextAlignment(Qt.AlignCenter)
            reclaim_item.setToolTip(
                "Manual blends derive this rate from the period crusher "
                "rates. Optimised prepopulation preserves each footprint's "
                "actual output reclaim rate."
            )
            self.blend_config_table.setItem(row_idx, len(keys) + 6, reclaim_item)  

            # Source Ratio (Auto-calculated)
            ratio_item = QTableWidgetItem("0")
            ratio_item.setFlags(Qt.ItemIsEnabled)  # Make it uneditable
            ratio_item.setTextAlignment(Qt.AlignCenter)
            ratio_item.setToolTip(
                "Stockpile reclaim rate divided by total crusher rate. "
                "Optimised blends may sum below 1; direct tip fills the gap."
            )
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

    @staticmethod
    def selected_manual_blend_ids(widget):
        if isinstance(widget, MultiSelectBlendComboBox):
            return widget.selected_blend_ids()
        if isinstance(widget, QComboBox):
            value = widget.currentText().strip()
            return [] if not value or value == "None" else [value]
        return []

    def manual_blend_id_options(self):
        blend_ids = {str(value) for value in range(1, 6)}
        for blend_id in (
            getattr(self, "blend_config_table_inputs", {}) or {}
        ):
            if str(blend_id).strip():
                blend_ids.add(str(blend_id).strip())
        for row in (
            getattr(self, "saved_blends_for_schedule", []) or []
        ):
            if row.get("Blend ID") not in (None, ""):
                blend_ids.add(str(row["Blend ID"]).strip())
        for row in (
            getattr(
                self, "stored_blend_sequence_table_for_gantt", []
            ) or []
        ):
            if row.get("Blend ID") not in (None, ""):
                blend_ids.add(str(row["Blend ID"]).strip())

        def sort_key(value):
            try:
                return (0, int(value))
            except (TypeError, ValueError):
                return (1, str(value))

        return sorted(blend_ids, key=sort_key)

    @staticmethod
    def parse_manual_blend_weights(text, blend_ids):
        blend_ids = [str(blend_id) for blend_id in blend_ids]
        text = str(text or "").strip()
        mapped_weights = {}
        if ":" in text:
            for entry in text.replace(",", ";").split(";"):
                blend_id, separator, weight = entry.partition(":")
                if not separator:
                    continue
                try:
                    mapped_weights[blend_id.strip()] = float(weight.strip())
                except (TypeError, ValueError):
                    continue
            return {
                blend_id: mapped_weights.get(blend_id, 0.0)
                for blend_id in blend_ids
            }

        try:
            shared_weight = float(text or 0)
        except (TypeError, ValueError):
            shared_weight = 0.0
        return {blend_id: shared_weight for blend_id in blend_ids}

    @staticmethod
    def format_manual_blend_weights(weights, blend_ids):
        blend_ids = [str(blend_id) for blend_id in blend_ids]
        if not blend_ids:
            return "0"
        if len(blend_ids) == 1:
            return f"{float(weights.get(blend_ids[0], 0.0)):g}"
        return "; ".join(
            f"{blend_id}: {float(weights.get(blend_id, 0.0)):g}"
            for blend_id in blend_ids
        )

    def manual_blend_weights_for_row(self, row_idx):
        blend_ids = self.selected_manual_blend_ids(
            self.blend_config_table.cellWidget(row_idx, 10)
        )
        weight_item = self.blend_config_table.item(row_idx, 11)
        return self.parse_manual_blend_weights(
            weight_item.text() if weight_item else "",
            blend_ids,
        )

    def on_manual_blend_membership_change(self, row_idx):
        blend_ids = self.selected_manual_blend_ids(
            self.blend_config_table.cellWidget(row_idx, 10)
        )
        weight_item = self.blend_config_table.item(row_idx, 11)
        if weight_item is None:
            weight_item = QTableWidgetItem("0")
            weight_item.setTextAlignment(Qt.AlignCenter)
            self.blend_config_table.setItem(row_idx, 11, weight_item)
        weights = self.parse_manual_blend_weights(
            weight_item.text(),
            blend_ids,
        )
        blocked = self.blend_config_table.blockSignals(True)
        try:
            weight_item.setText(
                self.format_manual_blend_weights(weights, blend_ids)
            )
        finally:
            self.blend_config_table.blockSignals(blocked)
        self.on_blend_data_change()

    def update_reclaim_rate(self):
        """
        Calculate reclaim rates for every stockpile/blend membership.

        A configuration row may belong to several Blend IDs. Each configured
        weight is normalized independently inside its selected blend.
        """
        period_rates = self.manual_crusher_rate_values()
        self.crusher_rate_input_values = period_rates
        self.crusher_rate = period_rates["Preplan"]
        self.crusher_rate_input_value = str(self.crusher_rate)

        # Temporarily disconnect the signal to prevent recursion
        self.blend_config_table.blockSignals(True)

        try:
            # Initialize a dictionary to store total weights for each blend ID
            blend_weights = {
                blend_id: 0 for blend_id
                in self.manual_blend_id_options()
            }

            # Step 1: Calculate total weights for each blend ID
            for row_idx in range(self.blend_config_table.rowCount()):
                try:
                    for blend_id, weight in (
                        self.manual_blend_weights_for_row(row_idx).items()
                    ):
                        blend_weights[blend_id] += weight
                except AttributeError:
                    continue

            # Step 2: Calculate and update Reclaim Rate for each stockpile
            for row_idx in range(self.blend_config_table.rowCount()):
                try:
                    blend_id_combo = self.blend_config_table.cellWidget(row_idx, 10)  
                    reclaim_item = self.blend_config_table.item(row_idx, 12)  
                    ratio_item = self.blend_config_table.item(row_idx, 13)  

                    selected_blend_ids = self.selected_manual_blend_ids(
                        blend_id_combo
                    )
                    if not selected_blend_ids:
                        if reclaim_item:
                            reclaim_item.setText("0")
                            ratio_item.setText("0.00")
                        continue

                    row_weights = self.manual_blend_weights_for_row(row_idx)
                    source_item = self.blend_config_table.item(
                        row_idx, 0
                    )
                    source_name = (
                        source_item.text().strip()
                        if source_item else ""
                    )
                    ratios = {}
                    reclaim_rates = {}
                    for blend_id in selected_blend_ids:
                        config = (
                            getattr(
                                self, "blend_config_table_inputs", {}
                            ) or {}
                        ).get(blend_id, {})
                        sources = [
                            str(value).strip().upper()
                            for value in config.get("sources", [])
                        ]
                        try:
                            source_index = sources.index(
                                source_name.upper()
                            )
                        except ValueError:
                            source_index = -1
                        is_optimised_rate = (
                            config.get("rate_mode") == "optimised"
                            and source_index >= 0
                        )
                        if is_optimised_rate:
                            configured_ratios = config.get(
                                "source_ratios", []
                            )
                            configured_rates = config.get(
                                "reclaim_rates", []
                            )
                            ratios[blend_id] = (
                                float(configured_ratios[source_index])
                                if source_index < len(configured_ratios)
                                else 0
                            )
                            reclaim_rates[blend_id] = {
                                "Optimised": (
                                    float(
                                        configured_rates[source_index]
                                    )
                                    if source_index
                                    < len(configured_rates)
                                    else 0
                                )
                            }
                        else:
                            ratio = (
                                row_weights.get(blend_id, 0.0)
                                / blend_weights[blend_id]
                                if blend_weights.get(blend_id, 0) > 0
                                else 0
                            )
                            ratios[blend_id] = ratio
                            reclaim_rates[blend_id] = {
                                period: ratio * rate
                                for period, rate in period_rates.items()
                            }

                    # Update the reclaim rate cell
                    if not reclaim_item:
                        reclaim_item = QTableWidgetItem()
                        self.blend_config_table.setItem(row_idx, 12, reclaim_item)
                        self.blend_config_table.setItem(row_idx, 13, ratio_item)


                    reclaim_parts = []
                    for blend_id in selected_blend_ids:
                        rates = reclaim_rates[blend_id]
                        if "Optimised" in rates:
                            text = f"{rates['Optimised']:.1f}"
                        elif len({
                            round(value, 6)
                            for value in rates.values()
                        }) == 1:
                            text = f"{next(iter(rates.values())):.1f}"
                        else:
                            text = (
                                f"P0 {rates['Preplan']:.1f} / "
                                f"P1 {rates['Period_1']:.1f} / "
                                f"P2 {rates['Period_2']:.1f}"
                            )
                        reclaim_parts.append(f"{blend_id}: {text}")
                    reclaim_item.setText("; ".join(reclaim_parts))
                    reclaim_item.setFlags(Qt.ItemIsEnabled)  # Ensure non-editable
                    reclaim_item.setTextAlignment(Qt.AlignCenter)  # Center-align the text

                    ratio_item.setText("; ".join(
                        f"{blend_id}: {ratios[blend_id]:.4f}"
                        for blend_id in selected_blend_ids
                    ))
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
        self.blend_results_table.setRowCount(
            len(self.manual_blend_id_options())
        )
        self.update_blend_results()

        # Connect edits to update results
        self.blend_config_table.itemChanged.connect(self.on_blend_data_change)     

    def populate_blend_config_weights_and_ids(self):
        assignments_by_source = {}
        if self.is_project_loaded and self.blend_config_table_inputs:
            for blend_id, row_data in self.blend_config_table_inputs.items():
                sources = row_data.get("sources", [])
                weights = row_data.get("weights", [])
                sources = (
                    sources
                    if isinstance(sources, list)
                    else str(sources).split(",")
                )
                weights = (
                    weights
                    if isinstance(weights, list)
                    else str(weights).split(",")
                )
                for source, weight in zip(sources, weights):
                    source = str(source).strip()
                    if not source:
                        continue
                    assignment = assignments_by_source.setdefault(
                        source,
                        {"blend_ids": set(), "weights": {}},
                    )
                    assignment["blend_ids"].add(str(blend_id))
                    try:
                        assignment["weights"][str(blend_id)] = float(weight)
                    except (TypeError, ValueError):
                        pass

        table_blocked = self.blend_config_table.blockSignals(True)
        crusher_blocked = self.crusher_rate_input.blockSignals(True)
        try:
            for table_row in range(self.blend_config_table.rowCount()):
                source_item = self.blend_config_table.item(table_row, 0)
                if source_item is None:
                    continue
                assignment = assignments_by_source.get(source_item.text())
                if not assignment:
                    continue

                combo_box = self.blend_config_table.cellWidget(table_row, 10)
                if isinstance(combo_box, MultiSelectBlendComboBox):
                    combo_box.set_selected_blend_ids(
                        assignment["blend_ids"]
                    )

                if assignment["weights"]:
                    ordered_blend_ids = sorted(
                        assignment["blend_ids"],
                        key=lambda value: int(value),
                    )
                    weights_item = QTableWidgetItem(
                        self.format_manual_blend_weights(
                            assignment["weights"],
                            ordered_blend_ids,
                        )
                    )
                    weights_item.setTextAlignment(Qt.AlignCenter)
                    weights_item.setToolTip(
                        "Weights are restored independently for every "
                        "selected Blend ID."
                    )
                    self.blend_config_table.setItem(
                        table_row, 11, weights_item
                    )

            if self.is_project_loaded:
                saved_rates = (
                    getattr(self, "crusher_rate_input_values", {}) or {}
                )
                if not saved_rates and self.crusher_rate_input_value:
                    saved_rates = {
                        period: self.crusher_rate_input_value
                        for period in (
                            "Preplan", "Period_1", "Period_2"
                        )
                    }
                self.set_manual_crusher_rate_inputs(saved_rates)
        finally:
            self.blend_config_table.blockSignals(table_blocked)
            self.crusher_rate_input.blockSignals(crusher_blocked)

        self.on_blend_data_change()

    def update_blend_results(self):
        """
        Recalculate and update the Blend Results Table, ensuring unused Blend IDs are cleared.
        """
        # Initialize a dictionary to aggregate data for each blend ID
        blend_id_options = self.manual_blend_id_options()
        existing_configs = copy.deepcopy(
            getattr(self, "blend_config_table_inputs", {}) or {}
        )
        self.blend_data_from_config_table_inputs = {
            blend_id: {
                "weights": [], "grades": [], "balances": [],
                "available": [], "sources": [], "source_ratios": [],
                "reclaim_rates": [],
                **{
                    key: copy.deepcopy(value)
                    for key, value in (
                        existing_configs.get(blend_id, {}) or {}
                    ).items()
                    if key in {
                        "rate_mode", "crusher_rate", "max_duration",
                        "periods",
                    }
                },
            }
            for blend_id in blend_id_options
        }

        blend_weights = {
            blend_id: 0.0 for blend_id in blend_id_options
        }
        for row_idx in range(self.blend_config_table.rowCount()):
            for blend_id, weight in (
                self.manual_blend_weights_for_row(row_idx).items()
            ):
                blend_weights[blend_id] += weight

        # Aggregate data from blend configuration table
        for row_idx in range(self.blend_config_table.rowCount()):
            try:
                blend_ids = self.selected_manual_blend_ids(
                    self.blend_config_table.cellWidget(row_idx, 10)
                )
                if not blend_ids:
                    continue

                row_weights = self.manual_blend_weights_for_row(row_idx)
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
                for blend_id in blend_ids:
                    weight = row_weights.get(blend_id, 0.0)
                    config = existing_configs.get(blend_id, {}) or {}
                    configured_sources = [
                        str(value).strip().upper()
                        for value in config.get("sources", [])
                    ]
                    source_ratio = None
                    configured_reclaim_rate = None
                    if config.get("rate_mode") == "optimised":
                        try:
                            configured_index = (
                                configured_sources.index(
                                    sources.strip().upper()
                                )
                            )
                            source_ratio = float(
                                config.get(
                                    "source_ratios", []
                                )[configured_index]
                            )
                            configured_reclaim_rate = float(
                                config.get(
                                    "reclaim_rates", []
                                )[configured_index]
                            )
                        except (
                            ValueError, IndexError, TypeError, KeyError
                        ):
                            source_ratio = None
                    if source_ratio is None:
                        source_ratio = (
                            weight / blend_weights[blend_id]
                            if blend_weights.get(blend_id, 0) > 0
                            else 0
                        )
                    blend_data = self.blend_data_from_config_table_inputs[
                        blend_id
                    ]
                    blend_data["weights"].append(weight)
                    blend_data["grades"].append([
                        grade * weight if grade != "AMT" else "AMT"
                        for grade in grades
                    ])
                    blend_data["balances"].append(balance)
                    blend_data["available"].append(available)
                    blend_data["sources"].append(sources)
                    blend_data["source_ratios"].append(
                        f"{source_ratio:.6f}"
                    )
                    blend_data["reclaim_rates"].append(
                        configured_reclaim_rate
                        if configured_reclaim_rate is not None
                        else 0
                    )

            except (ValueError, AttributeError):
                continue

        # Update the Blend Results Table
        self.blend_results_table.setRowCount(len(blend_id_options))
        for row_idx, blend_id in enumerate(blend_id_options):
            data = self.blend_data_from_config_table_inputs[blend_id]
            total_weight = sum(data["weights"])

            if total_weight > 0:
                # Calculate weighted averages
                if not any("AMT" in grades for grades in data["grades"]):
                    avg_grades = [sum(grades) / total_weight for grades in zip(*data["grades"])]
                else:
                    avg_grades = ["AMT" for grades in zip(*data["grades"])]

                balance = min(data["balances"])
                if (
                    data.get("rate_mode") == "optimised"
                    and float(data.get("max_duration") or 0) > 0
                ):
                    max_duration = float(data["max_duration"])
                else:
                    max_duration = (
                        balance / self.crusher_rate
                        if self.crusher_rate > 0 else 0
                    )
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
        self.set_page_enabled(self.blend_sequence_tab_index, True)
        self.show_page(self.blend_sequence_tab_index)
        self.save_button.setEnabled(True)
        QMessageBox.information(self, "BlendMaster", "Blend results successfully saved.")

    def fetch_build_report(self):
        """
        Fetch the build report from the database.
        """
        conn = sqlite3.connect(get_database_path())
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

        # Do not place every blend over the full horizon as a placeholder:
        # shared-stockpile blends would immediately violate mutual exclusion.
        # The chart remains empty until a valid sequence is submitted.
        self.stored_blend_sequence_table_for_gantt_default = []
        
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
        blend_ids = sorted(
            [blend["Blend ID"] for blend in self.saved_blends_for_schedule],
            key=lambda value: (
                0, int(value)
            ) if str(value).isdigit() else (1, str(value)),
        )
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
        all_blend_ids = self.manual_blend_id_options()
        missing_blend_ids = [
            blend_id for blend_id in all_blend_ids
            if str(blend_id) not in {str(value) for value in blend_ids}
        ]

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
                    blend_dropdown.addItems(
                        self.manual_blend_id_options()
                    )
                    blend_dropdown.currentIndexChanged.connect(lambda _, row=row: self.update_blend_id(row))
                    self.blend_sequence_table.setCellWidget(row, col, blend_dropdown)
                    blend_dropdown.setCurrentText(str(data[key]))  # Set initial value
                    self.blend_sequence_table.setCellWidget(row, col, blend_dropdown)

                # Add editable field for Duration
                elif key == "Duration (hrs)":
                    duration_item = QTableWidgetItem(
                        f"{float(data[key]):.1f}"
                    )
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
            self.manual_steady_state_button = QPushButton(
                "Steady States & Direct Tip"
            )
            self.style_green_action_button(
                self.manual_steady_state_button, 230
            )
            self.manual_steady_state_button.clicked.connect(
                self.open_manual_steady_state_dialog
            )
        self.manual_steady_state_button.setEnabled(
            bool(getattr(
                self, "stored_blend_sequence_table_for_gantt", []
            ))
        )
        
        if self.setup_blend_sequence_table_first_call:
            
            # Add the button to the layout
            self.blend_sequence_table_external_layout.addWidget(store_button)
            self.blend_sequence_table_external_layout.addWidget(
                self.manual_steady_state_button
            )
            
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
                blend_dropdown.addItems(
                    self.manual_blend_id_options()
                )
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
                rounded_duration_text = f"{duration:.1f}"
                duration = float(rounded_duration_text)
                if duration_item.text() != rounded_duration_text:
                    blocked = self.blend_sequence_table.blockSignals(True)
                    try:
                        duration_item.setText(rounded_duration_text)
                    finally:
                        self.blend_sequence_table.blockSignals(blocked)
                
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

    def preserve_optimised_sequence_metadata(self, rows):
        existing_rows = (
            getattr(
                self, "stored_blend_sequence_table_for_gantt", []
            ) or []
        )
        metadata_keys = {
            "_optimised_steady_state", "_fixed_steady_state",
            "_crusher_rate", "_period_name", "_exact_start", "_exact_end",
            "Direct Tip Tonnes", "Direct Tip Ratio",
        }
        for index, row in enumerate(rows or []):
            if index >= len(existing_rows):
                continue
            existing = existing_rows[index]
            if not existing.get("_fixed_steady_state"):
                continue
            unchanged = all(
                str(row.get(key) or "") == str(existing.get(key) or "")
                for key in (
                    "Blend ID", "Start Datetime",
                    "Duration (hrs)", "End Datetime",
                )
            )
            if not unchanged:
                continue
            for key in metadata_keys:
                if key in existing:
                    row[key] = copy.deepcopy(existing[key])
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
                try:
                    duration_item.setText(
                        f"{float(row_data.get('Duration (hrs)', 0)):.1f}"
                    )
                except (TypeError, ValueError):
                    pass
                duration_item.setTextAlignment(Qt.AlignCenter)

                end_item = self.blend_sequence_table.item(row_index, end_col)
                if end_item is None:
                    end_item = QTableWidgetItem()
                    self.blend_sequence_table.setItem(row_index, end_col, end_item)
                end_item.setText(str(row_data.get("End Datetime", "")))
                end_item.setTextAlignment(Qt.AlignCenter)

                self.update_blend_id(row_index)
                if row_data.get("Origin") not in (None, ""):
                    origin_item.setText(str(row_data["Origin"]))
        finally:
            self.blend_sequence_table.blockSignals(False)

        self.update_early_start_conditional_format()
        self.update_remaining_hrs()
        self.stored_blend_sequence_table_for_gantt = self.collect_blend_sequence_table_rows()

    def submit_blend_sequence_table_to_gantt(self):
                
        headers = ["Blend ID", "Origin", "Start Datetime", "Duration (hrs)", "End Datetime", "Early Start Flag", "Remaining Hrs"]
                
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

        defined_blend_ids = {
            str(blend.get("Blend ID"))
            for blend in (self.saved_blends_for_schedule or [])
            if blend.get("Blend ID") not in (None, "")
        }
        candidate_rows = [
            row
            for row in self.collect_blend_sequence_table_rows()
            if str(row.get("Blend ID")) in defined_blend_ids
        ]
        candidate_rows = self.preserve_optimised_sequence_metadata(
            candidate_rows
        )
        if not candidate_rows:
            QMessageBox.warning(
                self,
                "Manual Blend Sequence",
                "Add at least one scheduled user-defined blend before "
                "submitting the sequence.",
            )
            return False
        conflicts = ManualBlendRules.overlapping_blend_bar_conflicts(
            candidate_rows,
        )
        if conflicts:
            conflict = conflicts[0]
            additional = (
                f"\n\nThere are {len(conflicts) - 1} additional conflict(s)."
                if len(conflicts) > 1
                else ""
            )
            QMessageBox.warning(
                self,
                "Overlapping Manual Blends",
                ManualBlendRules.conflict_message(conflict)
                + "\n\nOnly one blend bar can be active at a time. Adjust "
                "a start time or duration."
                + additional,
            )
            return False

        # If all rows are valid, store data
        self.stored_blend_sequence_table_for_gantt = candidate_rows

        try:
            self.generate_manual_blend_plan(
                show_dialog=False, preserve_allocations=True
            )
        except (ManualBlendPlanningError, OSError, ValueError) as error:
            QMessageBox.warning(
                self, "Manual Blend Sequence", str(error)
            )
            return False
        
        self.start_or_update_dash_manual_chart_thread()

        self.load_manual_gantt_chart()  

        self.set_page_enabled(self.grade_profile_tab_index, True)  # Enable Grade Profile tab
        if hasattr(self, "manual_steady_state_button"):
            self.manual_steady_state_button.setEnabled(True)

        QMessageBox.information(
            self,
            "BlendMaster",
            "Blend sequence successfully submitted.\n\n"
            "Manual steady states and manual_blend_report have been "
            "generated. Use Steady States & Direct Tip to review available "
            "grade blocks and choose direct-tip tonnes.",
        )
        return True

    def manual_expit_payload_transactions(self):
        if not self.is_direct_tip_enabled():
            return pd.DataFrame()
        schedule_path = str(
            getattr(self, "file_path_24hr_choice", "") or ""
        ).strip()
        transactions = pd.DataFrame()
        # Prefer the exact payload population used by the completed
        # optimisation. Reprocessing Expit Mode 2 later can return a different
        # live progress cut and therefore regenerate different payload IDs.
        connection = sqlite3.connect(get_database_path())
        try:
            transactions = pd.read_sql(
                "SELECT * FROM expit_payload_transactions",
                connection,
            )
        except (sqlite3.Error, pd.errors.DatabaseError):
            transactions = pd.DataFrame()
        finally:
            connection.close()

        if transactions.empty and schedule_path:
            context = self.active_site_context()
            destination_guidance = context.get(
                "aps_destination_guidance"
            ) or {}
            if not destination_guidance:
                reference_path = str(
                    getattr(self, "file_path_choice", "")
                    or schedule_path
                ).strip()
                destination_guidance = (
                    ExpitDataHandler.build_2wp_destination_guidance(
                        reference_path
                    )
                )

            handler = ExpitDataHandler(
                schedule_path,
                include_crusher_destinations=getattr(
                    self, "reevaluate_aps_direct_tip_choice", False
                ),
                selected_crusher_name=getattr(
                    self, "aps_direct_tip_crusher_choice", []
                ),
                operational_mine=context.get("mine"),
                operational_crusher=context.get("crusher"),
                operational_opf=context.get("opf"),
                direct_tip_movement_rules=context.get(
                    "direct_tip_movement_rules", []
                ),
                destination_guidance=destination_guidance,
                selected_agent_names=getattr(
                    self, "selected_24hr_expit_agents", []
                ),
            )
            transactions = handler.process_transactions()
            if (
                transactions is not None
                and not transactions.empty
                and int(
                    getattr(self, "expit_mode_choice", 1) or 1
                ) == 2
            ):
                transactions = handler.update_transactions(
                    transactions, self.start_time_choice
                )

        if transactions is None or transactions.empty:
            return pd.DataFrame()

        transactions = Run._ensure_direct_tip_ids(transactions)
        if "direct_tip_eligible" not in transactions:
            transactions["direct_tip_eligible"] = transactions.get(
                "aps_direct_tip_candidate",
                pd.Series(False, index=transactions.index),
            ).map(
                lambda value: str(value).strip().lower()
                in {"true", "1", "yes"}
            )

        # Payloads already selected by the optimiser must remain available
        # when that result is transferred to Manual Blending. The persisted
        # expit schema predates direct_tip_eligible, so these IDs are the
        # authoritative bridge between the two workflows.
        optimised_report = self.fetch_optimised_blend_report()
        selected_direct_tip_ids = set()
        if (
            not optimised_report.empty
            and "source_type" in optimised_report
            and "source_id" in optimised_report
        ):
            direct_tip_rows = optimised_report[
                optimised_report["source_type"].astype(str)
                .str.strip().str.lower()
                .isin({"grade_block", "grade block", "gradeblock"})
            ]
            for source_ids in direct_tip_rows["source_id"].dropna():
                selected_direct_tip_ids.update(
                    part.strip()
                    for part in str(source_ids).split(",")
                    if part.strip()
                )
        if selected_direct_tip_ids:
            selected_mask = transactions["direct_tip_id"].astype(
                str
            ).isin(selected_direct_tip_ids)
            transactions.loc[
                selected_mask, "direct_tip_eligible"
            ] = True
        return transactions

    def create_manual_blend_planner(self):
        start_time = (
            getattr(self, "start_time_choice", None)
            or getattr(self, "default_start_datetime", None)
            or datetime.now()
        )
        periods = PeriodManager()
        periods.calculate_periods(start_time)

        rate = (
            getattr(self, "crusher_rate", None)
            or getattr(self, "crusher_rate_input_value", None)
        )
        manual_calendar_inputs = copy.deepcopy(
            getattr(self, "calendar_inputs", {}) or {}
        )
        manual_calendar_inputs["crusher_rate"] = (
            self.manual_crusher_rate_values()
        )
        return ManualBlendPlanner(
            getattr(
                self, "stored_blend_sequence_table_for_gantt", []
            ),
            getattr(self, "saved_blends_for_schedule", []),
            getattr(self, "updated_stockpile_data", {}),
            getattr(self, "hex_sequence_table", []),
            self.manual_expit_payload_transactions(),
            periods.get_periods(),
            getattr(self, "product_build_settings", []),
            rate,
            calendar_inputs=manual_calendar_inputs,
        )

    def generate_manual_blend_plan(
        self, show_dialog=False, preserve_allocations=True
    ):
        planner = self.create_manual_blend_planner()
        states = planner.build_steady_states()
        allocations = (
            copy.deepcopy(getattr(
                self, "manual_direct_tip_allocations", {}
            ))
            if preserve_allocations else {}
        )

        # Old selections whose delivery window no longer exists are harmless,
        # but keeping them in the project would be misleading.
        state_keys = {state["state_key"] for state in states}
        allocations = {
            key: value for key, value in allocations.items()
            if key in state_keys
        }

        if show_dialog:
            dialog = ManualSteadyStateDialog(
                planner, states, allocations, self
            )
            if dialog.exec_() != QDialog.Accepted:
                return False
            allocations = dialog.allocations
            report = dialog.report
        else:
            try:
                report = planner.build_report(states, allocations)
            except ManualBlendPlanningError:
                if allocations and any(
                    state.get("trigger")
                    == "Optimised decision point"
                    for state in states
                ):
                    raise
                # A schedule edit can reduce availability. Reset stale
                # allocations and still create a valid stockpile-only report.
                allocations = {}
                report = planner.build_report(states, allocations)

        self.manual_direct_tip_allocations = allocations
        self.manual_steady_states = states
        self.manual_blend_report = report
        self.update_manual_sequence_direct_tip_annotations(
            states, allocations
        )
        DatabaseManager().write_manual_blend_report_to_database(report)
        self.refresh_sqlite_reports()
        if hasattr(self, "draw_grade_profile_chart"):
            self.draw_grade_profile_chart.update_data(
                self.manual_grade_profile_data()
            )
        self.save_active_scenario_state()
        return True

    def update_manual_sequence_direct_tip_annotations(
        self, states, allocations
    ):
        sequence = getattr(
            self, "stored_blend_sequence_table_for_gantt", []
        ) or []
        for sequence_row in sequence:
            sequence_start = pd.to_datetime(
                sequence_row.get(
                    "_exact_start",
                    sequence_row.get("Start Datetime"),
                ),
                errors="coerce",
            )
            sequence_end = pd.to_datetime(
                sequence_row.get(
                    "_exact_end",
                    sequence_row.get("End Datetime"),
                ),
                errors="coerce",
            )
            matching_states = [
                state for state in (states or [])
                if (
                    str(state.get("blend_ID"))
                    == str(sequence_row.get("Blend ID"))
                    and not pd.isna(sequence_start)
                    and not pd.isna(sequence_end)
                    and pd.Timestamp(state["start_datetime"])
                    >= sequence_start
                    and pd.Timestamp(state["end_datetime"])
                    <= sequence_end
                )
            ]
            direct_tip_tonnes = sum(
                float(value or 0)
                for state in matching_states
                for value in (
                    (allocations or {}).get(
                        state["state_key"], {}
                    ) or {}
                ).values()
            )
            capacity = sum(
                float(state.get("feed_capacity_tonnes") or 0)
                for state in matching_states
            )
            sequence_row["Direct Tip Tonnes"] = direct_tip_tonnes
            sequence_row["Direct Tip Ratio"] = (
                direct_tip_tonnes / capacity if capacity > 0 else 0
            )

        manual_chart = getattr(
            self, "draw_manual_gantt_chart", None
        )
        if manual_chart is not None:
            manual_chart.update_data(
                sequence,
                getattr(
                    self, "manual_gantt_legend_and_tooltip", []
                ),
            )

    def open_manual_steady_state_dialog(self):
        if not getattr(
            self, "stored_blend_sequence_table_for_gantt", []
        ):
            QMessageBox.information(
                self,
                "Manual Steady States",
                "Submit a manual blend sequence first.",
            )
            return
        try:
            applied = self.generate_manual_blend_plan(
                show_dialog=True, preserve_allocations=True
            )
        except (ManualBlendPlanningError, OSError, ValueError) as error:
            QMessageBox.warning(
                self, "Manual Steady States", str(error)
            )
            return
        if applied:
            QMessageBox.information(
                self,
                "BlendMaster",
                "Direct-tip selections applied and manual_blend_report "
                "regenerated.",
            )

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
                duration_item.setText(
                    f"{float(duration):.1f}"
                    if duration is not None else ""
                )
            else:  # Create a new QTableWidgetItem if not already set
                self.blend_sequence_table.setItem(
                    row_index, 3,
                    QTableWidgetItem(
                        f"{float(duration):.1f}"
                        if duration is not None else ""
                    ),
                )
            
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
        self.grade_profile_tab_index = self.register_page(
            "manual_grade_profiles",
            self.grade_profiles_tabs,
            self.grade_profile_tab,
            "Manual",
        )

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
        self.style_green_action_button(self.load_grade_profile_chart_button, 210)
        self.load_grade_profile_chart_button.clicked.connect(self.load_grade_profiles)  # Connect button to function

        # Add the button to the layout at the bottom-left
        self.grade_profile_layout.addWidget(self.load_grade_profile_chart_button)

    def start_or_update_dash_manual_grade_profile_thread(self):
        """Update or start the Dash app."""
        hex_sequence_table = copy.deepcopy(self.hex_sequence_table)
        updated_stockpile_data = copy.deepcopy(self.updated_stockpile_data)
        grade_profile_data = self.manual_grade_profile_data()
        if grade_profile_data.empty:
            grade_profile_data = (
                self.draw_manual_gantt_chart.return_grade_profile_data()
            )

        if hasattr(self, 'draw_grade_profile_chart') and self.dash_thread_grade_profile.is_alive():
            # Update data in the running Dash app
            self.draw_grade_profile_chart.update_data(grade_profile_data)  
        else:
            # Start the Dash app if not already running
            self.draw_grade_profile_chart = DrawGradeProfiles(grade_profile_data, hex_sequence_table, updated_stockpile_data, 8053)
            self.dash_thread_grade_profile = threading.Thread(target=self.draw_grade_profile_chart.run_app, daemon=True)
            self.dash_thread_grade_profile.start()
            self.draw_grade_profile_chart.update_data(grade_profile_data)  

    def manual_grade_profile_data(self):
        report = getattr(self, "manual_blend_report", None)
        if not isinstance(report, pd.DataFrame) or report.empty:
            try:
                connection = sqlite3.connect(get_database_path())
                report = pd.read_sql(
                    "SELECT * FROM manual_blend_report", connection
                )
            except (sqlite3.Error, pd.errors.DatabaseError):
                return pd.DataFrame()
            finally:
                if "connection" in locals():
                    connection.close()
        if report.empty:
            return pd.DataFrame()

        rows = []
        group_columns = [
            "steady_state_number", "blend_ID", "start_datetime",
            "end_datetime", "steady_state_duration",
        ]
        for keys, group in report.groupby(
            group_columns, sort=False, dropna=False
        ):
            (
                _state_number, blend_id, start, end, duration
            ) = keys
            first = group.iloc[0]
            rows.append({
                "Blend ID": blend_id,
                "Origin": "Manual",
                "Start Datetime": start,
                "End Datetime": end,
                "Duration (hrs)": duration,
                "Feed Tonnes": first.get(
                    "crusher_actual_tonnes", 0
                ),
                "Grade Fe": first.get(
                    "crusher_actual_grade_fe", 0
                ),
                "Grade Si": first.get(
                    "crusher_actual_grade_si", 0
                ),
                "Grade Al": first.get(
                    "crusher_actual_grade_al", 0
                ),
                "Grade P": first.get(
                    "crusher_actual_grade_p", 0
                ),
                "Grade Mn": first.get(
                    "crusher_actual_grade_mn", 0
                ),
            })
        return pd.DataFrame(rows)

    def save_state(self, show_success=True):
        """Save the application state to a file using pickle."""

        if hasattr(self, "hub_input"):
            self.hub_input_choice = self.hub_input.currentText().strip()
        if hasattr(self, "mine_input"):
            self.mine_input_choice = self.mine_input.currentText().strip()
        if hasattr(self, "site_crusher_input"):
            self.capture_site_ratio_and_movement_controls()
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
            self.file_path_24hr_choice = self.file_path_24hr.text()
        if hasattr(self, "expit_agent_input"):
            self.available_24hr_expit_agents = (
                self.available_24hr_expit_agent_names()
            )
            self.selected_24hr_expit_agents = (
                self.selected_24hr_expit_agent_names()
            )
        if hasattr(self, "haul_cycle_file_path"):
            self.haul_cycle_file_path_choice = (
                self.haul_cycle_file_path.text().strip()
            )
            self.available_haul_cycle_crushers = (
                self.available_haul_cycle_crusher_names()
            )
            self.selected_haul_cycle_crushers = (
                self.selected_haul_cycle_crusher_names()
            )
            self.refresh_haul_cycle_routes(show_errors=False)
        if hasattr(self, "product_brand_labels_input"):
            self.product_brand_labels_choice = self.parse_product_brand_labels(
                self.product_brand_labels_input.text()
            )
        if hasattr(self, "auto_load_2wp_targets_checkbox"):
            self.auto_load_2wp_targets_choice = (
                self.auto_load_2wp_targets_checkbox.isChecked()
            )
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
        if hasattr(self, "agent_enabled_checkbox"):
            self.agent_enabled_choice = self.agent_enabled_checkbox.isChecked()
        if hasattr(self, "agent_story_input"):
            self.agent_story_text = self.agent_story_input.toPlainText()
        if hasattr(self, "agent_run_instructions_input"):
            self.agent_run_instructions_text = self.agent_run_instructions_input.toPlainText()
        if hasattr(self, "agent_bridge_port_input"):
            self.agent_bridge_port = self.agent_bridge_port_value()
        agent_console_text = (
            self.agent_console_output.toPlainText()
            if hasattr(self, "agent_console_output")
            else ""
        )
        agent_proposals_table = self.capture_agent_proposals_table()
        if hasattr(self, "product_build_table"):
            self.store_product_build_settings(show_errors=False)

        if hasattr(self, "blend_data_from_config_table_inputs"):
            self.blend_config_table_inputs = self.blend_data_from_config_table_inputs
        elif self.blend_config_table_inputs is None:
            self.blend_config_table_inputs = {}

        if hasattr(self, "crusher_rate_input"):
            self.crusher_rate_input_values = (
                self.manual_crusher_rate_values()
            )
            self.crusher_rate_input_value = str(
                self.crusher_rate_input_values["Preplan"]
            )

        self.store_solver_config_inputs(show_errors=False)
        self.save_active_scenario_state()
        
        try:
            scenarios_to_save = copy.deepcopy(self.site_scenarios)
            for scenario_id, scenario_state in scenarios_to_save.items():
                database_path = scenario_state.get("database_path") or self.scenario_database_path(scenario_id)
                scenario_state["database_snapshot"] = self.snapshot_database(database_path)
                scenario_state.pop("database_path", None)
            
            # Save the enabled/disabled state of tabs
            tab_states = self.capture_page_states()

            # Combine all class variables into a dictionary
            state_to_save = {
                "project_format_version": 10,
                "active_scenario_id": self.active_scenario_id,
                "site_scenarios": scenarios_to_save,
                "tab_states": tab_states,
                "blend_mode_choice": self.blend_mode_choice,
                "agent_enabled_choice": self.agent_enabled_choice,
                "agent_story_text": self.agent_story_text,
                "agent_run_instructions_text": self.agent_run_instructions_text,
                "agent_bridge_port": self.agent_bridge_port,
                "agent_console_text": agent_console_text,
                "agent_latest_result": copy.deepcopy(getattr(self, "agent_latest_result", {})),
                "agent_latest_proposals": copy.deepcopy(getattr(self, "agent_latest_proposals", [])),
                "agent_proposals_table": agent_proposals_table,
                "calendar_inputs": self.calendar_inputs,
                "crusher_rate": self.crusher_rate,
                "default_end_datetime": self.default_end_datetime,
                "default_end_datetime_str": self.default_end_datetime_str,
                "default_start_datetime": self.default_start_datetime,
                "default_start_datetime_str": self.default_start_datetime_str,
                "expit_mode_choice": self.expit_mode_choice,
                "file_path_choice": self.file_path_choice,
                "file_path_24hr_choice": self.file_path_24hr_choice,
                "available_24hr_expit_agents": self.available_24hr_expit_agents,
                "selected_24hr_expit_agents": self.selected_24hr_expit_agents,
                "haul_cycle_file_path_choice": self.haul_cycle_file_path_choice,
                "available_haul_cycle_crushers": self.available_haul_cycle_crushers,
                "selected_haul_cycle_crushers": self.selected_haul_cycle_crushers,
                "haul_cycle_routes": self.haul_cycle_routes,
                "product_brand_labels_choice": self.product_brand_labels_choice,
                "product_build_settings": self.product_build_settings,
                "auto_load_2wp_targets_choice": self.auto_load_2wp_targets_choice,
                "aps_stockpile_brand_map": getattr(self, "aps_stockpile_brand_map", {}),
                "aps_stockpile_timing_guidance": getattr(
                    self, "aps_stockpile_timing_guidance", {}
                ),
                "aps_active_blend_guidance": getattr(
                    self, "aps_active_blend_guidance", []
                ),
                "aps_destination_guidance": getattr(
                    self, "aps_destination_guidance", {}
                ),
                "reevaluate_aps_direct_tip_choice": self.reevaluate_aps_direct_tip_choice,
                "aps_direct_tip_crusher_choice": self.aps_direct_tip_crusher_choice,
                "mine_input_choice": self.mine_input_choice,
                "hub_input_choice": self.hub_input_choice,
                "opf_input_choice": self.opf_input_choice,
                "crusher_input_choice": self.crusher_input_choice,
                "selected_site_crushers": self.selected_site_crushers,
                "crusher_ratio_mode_choice": self.crusher_ratio_mode_choice,
                "crusher_contribution_ratio_choice": self.crusher_contribution_ratio_choice,
                "crusher_ratio_configured": self.crusher_ratio_configured,
                "aps_ratio_crusher_choices": self.aps_ratio_crusher_choices,
                "direct_tip_grade_block_sources": self.direct_tip_grade_block_sources,
                "direct_tip_crusher_destinations": self.direct_tip_crusher_destinations,
                "direct_tip_movement_rules": self.direct_tip_movement_rules,
                "opening_stockpile_inventories": self.opening_stockpile_inventories,
                "saved_blends_for_schedule": self.saved_blends_for_schedule,
                "start_time_choice": self.start_time_choice,
                "stockpile_data": self.stockpile_data,
                "stockpile_data_use_column": self.stockpile_data_use_column,
                "stored_blend_sequence_table_for_gantt": self.stored_blend_sequence_table_for_gantt,
                "stored_blend_sequence_table_for_gantt_default": self.stored_blend_sequence_table_for_gantt_default,
                "manual_direct_tip_allocations": self.manual_direct_tip_allocations,
                "manual_steady_states": self.manual_steady_states,
                "time_mode_choice": self.time_mode_choice,
                "updated_stockpile_data": self.updated_stockpile_data,
                "blend_config_table_inputs":  self.blend_config_table_inputs,
                "crusher_rate_input_value": self.crusher_rate_input_value,
                "crusher_rate_input_values": (
                    self.crusher_rate_input_values
                ),
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

            if show_success:
                QMessageBox.information(self, "BlendMaster", "Project saved successfully!")
            return True
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save project: {str(e)}")
            return False

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

            self.restore_loaded_state(
                loaded_state,
                source_label=file_path,
                show_success=True,
            )
            return

        except FileNotFoundError:
            QMessageBox.warning(self, "Error", "No saved projects found!")
            self.is_project_loaded = False
            self.finish_project_load_ui(success=False)
            return
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load project: {str(e)}")
            self.is_project_loaded = False
            self.finish_project_load_ui(success=False)
            return

    def resolve_missing_aps_mining_csv_paths(self, loaded_state):
        """Prompt once per missing APS file path before restoring project state."""
        if not isinstance(loaded_state, dict):
            return True

        state_records = [loaded_state]
        scenarios = loaded_state.get("site_scenarios")
        if isinstance(scenarios, dict):
            state_records.extend(
                state for state in scenarios.values() if isinstance(state, dict)
            )

        path_keys = (
            "file_path_choice",
            "file_path_24hr_choice",
            "twenty_four_hour_file_path_choice",
            "aps_mining_csv",
            "mining_csv",
            "file_path",
            "haul_cycle_file_path_choice",
        )
        replacements = {}
        for state in state_records:
            for key in path_keys:
                saved_path = str(state.get(key) or "").strip()
                if not saved_path:
                    continue
                normalized_path = os.path.normcase(os.path.abspath(os.path.expanduser(saved_path)))
                if os.path.isfile(normalized_path):
                    continue
                if normalized_path not in replacements:
                    start_directory = os.path.dirname(normalized_path)
                    if not os.path.isdir(start_directory):
                        start_directory = ""
                    replacement, _ = QFileDialog.getOpenFileName(
                        self,
                        (
                            "Locate 2WP Haul Infinity Cycles.csv"
                            if "haul_cycle" in key.lower()
                            else (
                                "Locate 24HR Mining.csv"
                                if "24hr" in key.lower() or "twenty_four" in key.lower()
                                else "Locate 2WP Mining.csv"
                            )
                        ),
                        start_directory,
                        (
                            "Haul Infinity Cycles.csv (*.csv);;All Files (*)"
                            if "haul_cycle" in key.lower()
                            else "APS Mining.csv (*.csv);;All Files (*)"
                        ),
                    )
                    if not replacement:
                        QMessageBox.information(
                            self,
                            "Project Load Cancelled",
                            "The project was not loaded because a referenced APS Mining.csv file was not located.",
                        )
                        return False
                    replacements[normalized_path] = replacement

                replacement = replacements[normalized_path]
                for path_key in path_keys:
                    if str(state.get(path_key) or "").strip() == saved_path:
                        state[path_key] = replacement
        return True

    def normalize_loaded_scenario_ratio_groups(self, scenarios):
        """Give pre-ratio multi-site projects a valid, deterministic split."""
        grouped = {}
        for scenario_id, state in scenarios.items():
            key = (
                str(state.get("hub_input_choice") or "").strip(),
                str(state.get("mine_input_choice") or "").strip(),
                str(state.get("opf_input_choice") or "").strip(),
            )
            grouped.setdefault(key, []).append((scenario_id, state))

        for group in grouped.values():
            missing = [
                state for _, state in group
                if not state.get("crusher_ratio_configured", False)
            ]
            if not missing:
                continue
            configured_total = sum(
                self.normalized_crusher_ratio(
                    state.get("crusher_contribution_ratio_choice"),
                    default=0.0,
                )
                for _, state in group
                if state.get("crusher_ratio_configured", False)
            )
            remaining = max(0.0, 1.0 - configured_total)
            fallback_ratio = remaining / len(missing) if remaining > 0 else 1.0 / len(group)
            for state in missing:
                state["crusher_ratio_mode_choice"] = "manual"
                state["crusher_contribution_ratio_choice"] = fallback_ratio
                state["crusher_ratio_configured"] = True

    def prepare_loaded_site_scenarios(self, loaded_state):
        """Restore v2 multi-site projects and wrap legacy projects as one scenario."""
        saved_scenarios = loaded_state.get("site_scenarios")
        if not isinstance(saved_scenarios, dict) or not saved_scenarios:
            legacy_state = copy.deepcopy(loaded_state)
            legacy_state["scenario_id"] = self.active_scenario_id
            legacy_state["database_path"] = self.scenario_database_path(self.active_scenario_id)
            self.site_scenarios = {self.active_scenario_id: legacy_state}
            set_database_path(legacy_state["database_path"])
            return loaded_state

        restored_scenarios = {}
        for scenario_id, raw_state in saved_scenarios.items():
            if not isinstance(raw_state, dict):
                continue
            scenario_state = copy.deepcopy(raw_state)
            database_snapshot = scenario_state.pop("database_snapshot", None)
            scenario_state.pop("site_scenarios", None)
            scenario_state = self.normalized_agent_project_state(scenario_state)
            scenario_state["scenario_id"] = scenario_id
            scenario_state["database_path"] = self.scenario_database_path(scenario_id)
            self.restore_database_snapshot(
                scenario_state["database_path"],
                database_snapshot,
            )
            restored_scenarios[scenario_id] = scenario_state

        if not restored_scenarios:
            raise ValueError("The project does not contain a valid site scenario.")
        self.normalize_loaded_scenario_ratio_groups(restored_scenarios)

        requested_active_id = loaded_state.get("active_scenario_id")
        self.active_scenario_id = (
            requested_active_id
            if requested_active_id in restored_scenarios
            else next(iter(restored_scenarios))
        )
        self.site_scenarios = restored_scenarios
        active_state = copy.deepcopy(restored_scenarios[self.active_scenario_id])
        global_state = copy.deepcopy(loaded_state)
        global_state.pop("site_scenarios", None)
        global_state.update(active_state)
        set_database_path(active_state["database_path"])
        return global_state

    def restore_loaded_state(self, loaded_state, source_label=None, show_success=False):
        """Restore app state using the same path as Load Project."""
        self.is_project_loaded = True
        self.project_load_keep_site_configuration_visible = bool(show_success)
        self.project_load_show_success = bool(show_success)
        self.project_load_source_label = str(source_label or "")
        if show_success:
            self.show_page(self.site_config_tab_index)
        loaded_state = self.normalized_agent_project_state(loaded_state)
        if not self.resolve_missing_aps_mining_csv_paths(loaded_state):
            self.is_project_loaded = False
            self.finish_project_load_ui(success=False)
            return False
        loaded_state = self.prepare_loaded_site_scenarios(loaded_state)

        # Unpack loaded state into variables
        self.blend_mode_choice = loaded_state.get("blend_mode_choice", None)
        self.agent_enabled_choice = loaded_state.get("agent_enabled_choice", False)
        self.agent_story_text = loaded_state.get("agent_story_text", "")
        self.agent_run_instructions_text = loaded_state.get("agent_run_instructions_text", "")
        self.agent_bridge_port = loaded_state.get("agent_bridge_port", 8765)
        self.agent_latest_result = copy.deepcopy(loaded_state.get("agent_latest_result", {}) or {})
        self.agent_latest_proposals = copy.deepcopy(loaded_state.get("agent_latest_proposals", []) or [])
        if hasattr(self, "agent_enabled_checkbox"):
            self.agent_enabled_checkbox.setChecked(bool(self.agent_enabled_choice))
        if hasattr(self, "agent_story_input"):
            self.agent_story_input.setPlainText(self.agent_story_text)
        if hasattr(self, "agent_run_instructions_input"):
            self.agent_run_instructions_input.setPlainText(self.agent_run_instructions_text)
        if hasattr(self, "agent_bridge_port_input"):
            self.agent_bridge_port_input.setText(str(self.agent_bridge_port))
        if hasattr(self, "agent_console_output"):
            self.agent_console_output.setPlainText(loaded_state.get("agent_console_text", "") or "")
        self.restore_agent_proposals_table(loaded_state.get("agent_proposals_table", []) or [])
        self.apply_app_theme()
        self.crusher_rate = loaded_state.get("crusher_rate", None)
        self.calendar_inputs = loaded_state.get("calendar_inputs", None)
        self.default_end_datetime = loaded_state.get("default_end_datetime", None)
        self.default_end_datetime_str = loaded_state.get("default_end_datetime_str", "")
        self.default_start_datetime = loaded_state.get("default_start_datetime", None)
        self.default_start_datetime_str = loaded_state.get("default_start_datetime_str", "")
        self.expit_mode_choice = loaded_state.get("expit_mode_choice", None)
        self.file_path_choice = loaded_state.get("file_path_choice", "")
        self.file_path_24hr_choice = (
            loaded_state.get("file_path_24hr_choice")
            or loaded_state.get("twenty_four_hour_file_path_choice")
            or ""
        )
        self.available_24hr_expit_agents = self.normalized_expit_agent_names(
            loaded_state.get("available_24hr_expit_agents") or []
        )
        self.selected_24hr_expit_agents = self.normalized_expit_agent_names(
            loaded_state.get("selected_24hr_expit_agents") or []
        )
        self.haul_cycle_file_path_choice = str(
            loaded_state.get("haul_cycle_file_path_choice") or ""
        )
        self.available_haul_cycle_crushers = (
            self.normalized_expit_agent_names(
                loaded_state.get("available_haul_cycle_crushers") or []
            )
        )
        self.selected_haul_cycle_crushers = (
            self.normalized_expit_agent_names(
                loaded_state.get("selected_haul_cycle_crushers") or []
            )
        )
        self.haul_cycle_routes = copy.deepcopy(
            loaded_state.get("haul_cycle_routes") or {}
        )
        self.product_brand_labels_choice = self.parse_product_brand_labels(
            loaded_state.get("product_brand_labels_choice", self.default_product_brand_labels())
        )
        self.product_build_settings = self.normalized_agent_product_build_settings(
            loaded_state.get("product_build_settings", []) or []
        )
        self.auto_load_2wp_targets_choice = bool(
            loaded_state.get("auto_load_2wp_targets_choice", True)
        )
        self.aps_stockpile_brand_map = loaded_state.get("aps_stockpile_brand_map", {}) or {}
        self.aps_stockpile_timing_guidance = (
            loaded_state.get("aps_stockpile_timing_guidance", {}) or {}
        )
        self.aps_active_blend_guidance = (
            loaded_state.get("aps_active_blend_guidance", []) or []
        )
        self.aps_destination_guidance = (
            loaded_state.get("aps_destination_guidance", {}) or {}
        )
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
        self.opf_input_choice = loaded_state.get("opf_input_choice") or self.default_opf_for_site(
            self.mine_input_choice,
            loaded_state.get("crusher_input_choice"),
        )
        self.crusher_input_choice = loaded_state.get("crusher_input_choice", None)
        self.selected_site_crushers = loaded_state.get("selected_site_crushers") or (
            [self.crusher_input_choice] if self.crusher_input_choice else []
        )
        self.selected_site_crushers = self.selected_site_crushers[:1]
        self.crusher_ratio_mode_choice = self.normalized_crusher_ratio_mode(
            loaded_state.get("crusher_ratio_mode_choice")
        )
        self.crusher_contribution_ratio_choice = self.normalized_crusher_ratio(
            loaded_state.get("crusher_contribution_ratio_choice", 1.0),
            default=1.0,
        )
        self.crusher_ratio_configured = bool(
            loaded_state.get("crusher_ratio_configured", False)
        )
        self.aps_ratio_crusher_choices = self.normalized_aps_crusher_choice(
            loaded_state.get("aps_ratio_crusher_choices") or []
        )
        self.direct_tip_grade_block_sources = list(
            loaded_state.get("direct_tip_grade_block_sources") or []
        )
        self.direct_tip_crusher_destinations = list(
            loaded_state.get("direct_tip_crusher_destinations") or []
        )
        self.direct_tip_movement_rules = ExpitDataHandler._normalize_movement_rules(
            loaded_state.get("direct_tip_movement_rules") or []
        )
        self.opening_stockpile_inventories = loaded_state.get("opening_stockpile_inventories", None)
        if self.opening_stockpile_inventories is None:
            self.opening_stockpile_inventories = OpeningStockpileInventories()
        self.saved_blends_for_schedule = loaded_state.get("saved_blends_for_schedule") or []
        self.start_time_choice = loaded_state.get("start_time_choice", None)
        self.stockpile_data = loaded_state.get("stockpile_data", None)
        self.stockpile_data_use_column = loaded_state.get("stockpile_data_use_column", {})
        self.stored_blend_sequence_table_for_gantt = (
            loaded_state.get("stored_blend_sequence_table_for_gantt") or []
        )
        self.stored_blend_sequence_table_for_gantt_default = (
            loaded_state.get("stored_blend_sequence_table_for_gantt_default") or []
        )
        self.manual_direct_tip_allocations = copy.deepcopy(
            loaded_state.get("manual_direct_tip_allocations") or {}
        )
        self.manual_steady_states = copy.deepcopy(
            loaded_state.get("manual_steady_states") or []
        )
        self.manual_blend_report = pd.DataFrame()
        self.time_mode_choice = loaded_state.get("time_mode_choice", None)
        self.updated_stockpile_data = loaded_state.get("updated_stockpile_data", None)
        self.blend_config_table_inputs =  loaded_state.get("blend_config_table_inputs", None)
        self.crusher_rate_input_value = loaded_state.get("crusher_rate_input_value", None)
        self.crusher_rate_input_values = copy.deepcopy(
            loaded_state.get("crusher_rate_input_values") or {}
        )
        self.hex_sequence_table = loaded_state.get("hex_sequence_table", [])
        self.hex_sequence_table_argument = copy.deepcopy(self.hex_sequence_table or [])
        self.stockpile_data_AMT_column = loaded_state.get("stockpile_data_AMT_column", {})
        self.AMT_stockpile_data = loaded_state.get("AMT_stockpile_data", {}) or {}
        self.AMT_chunk_settings = loaded_state.get("AMT_chunk_settings", {})
        self.seed_active_scenario_database()
        self.solver_config = self.normalized_solver_config(
            loaded_state.get("solver_config", {})
        )
        if self.calendar_inputs is not None:
            self.calendar_inputs["solver_config"] = copy.deepcopy(self.solver_config)
            self.calendar_inputs["site_context"] = self.active_site_context()

        self.refresh_scenario_selector()

        tab_states = loaded_state.get("tab_states", {})
        self.restore_page_states(tab_states)

        # Hydrate these widgets before Site Configuration saves the restored
        # scenario. Otherwise that save reads the empty/default widgets and
        # overwrites the loaded product-build and solver settings.
        self.populate_product_build_table()
        self.load_solver_config_inputs()

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
        self.scenario_session_directory = tempfile.mkdtemp(prefix="blendmaster_sites_")
        self.active_scenario_id = self.new_scenario_id()
        initial_database_path = os.path.join(
            self.scenario_session_directory,
            f"{self.active_scenario_id}.db",
        )
        self.site_scenarios = {
            self.active_scenario_id: {
                "scenario_id": self.active_scenario_id,
                "database_path": initial_database_path,
            }
        }
        self.scenario_switch_in_progress = False
        self.project_load_keep_site_configuration_visible = False
        self.project_load_show_success = False
        self.project_load_source_label = ""
        set_database_path(initial_database_path)
        self.blend_mode_choice = None
        self.calendar_inputs = None
        self.crusher_rate = None
        self.default_end_datetime = None
        self.default_end_datetime_str = None
        self.default_start_datetime = None
        self.default_start_datetime_str = None
        self.expit_mode_choice = None
        self.file_path_choice = None
        self.file_path_24hr_choice = None
        self.available_24hr_expit_agents = []
        self.selected_24hr_expit_agents = []
        self.haul_cycle_file_path_choice = None
        self.available_haul_cycle_crushers = []
        self.selected_haul_cycle_crushers = []
        self.haul_cycle_routes = {}
        self.product_brand_labels_choice = self.default_product_brand_labels()
        self.product_build_settings = []
        self.auto_load_2wp_targets_choice = True
        self.aps_stockpile_brand_map = {}
        self.aps_stockpile_timing_guidance = {}
        self.aps_active_blend_guidance = []
        self.aps_destination_guidance = {}
        self.aps_brand_guidance_cache = {}
        self.scenario_report_refresh_pending = False
        self.reevaluate_aps_direct_tip_choice = False
        self.aps_direct_tip_crusher_choice = []
        self.agent_enabled_choice = False
        self.agent_story_text = ""
        self.agent_run_instructions_text = ""
        self.agent_bridge_port = 8765
        self.agent_bridge_process = None
        self.agent_bridge_log_handle = None
        self.agent_current_request_id = None
        self.agent_seen_trace_count = 0
        self.agent_latest_proposals = []
        self.agent_latest_result = {}
        self.agent_workflow_active = False
        self.agent_workflow_payload = {}
        self.agent_workflow_after_site_config = False
        self.agent_workflow_waiting_for_amt = False
        self.mine_input_choice = None
        self.hub_input_choice = None
        self.opf_input_choice = None
        self.crusher_input_choice = None
        self.selected_site_crushers = []
        self.crusher_ratio_mode_choice = "manual"
        self.crusher_contribution_ratio_choice = 1.0
        self.crusher_ratio_configured = False
        self.aps_ratio_crusher_choices = []
        self.direct_tip_grade_block_sources = []
        self.direct_tip_crusher_destinations = []
        self.direct_tip_movement_rules = []
        self.planning_plan_targets = PlanningPlanTargets()
        self.opening_stockpile_inventories = None
        self.saved_blends_for_schedule = None
        self.start_time_choice = None
        self.stockpile_data = None
        self.stockpile_data_use_column = {}
        self.stored_blend_sequence_table_for_gantt = None
        self.stored_blend_sequence_table_for_gantt_default = None
        self.manual_direct_tip_allocations = {}
        self.manual_steady_states = []
        self.manual_blend_report = pd.DataFrame()
        self.time_mode_choice = None
        self.updated_stockpile_data = None
        self.blend_config_table_inputs = None
        self.crusher_rate_input_value = None
        self.crusher_rate_input_values = {}
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

    def closeEvent(self, event):
        reply = QMessageBox.question(
            self,
            "Save Project?",
            "Save project before closing?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Cancel:
            event.ignore()
            return
        if reply == QMessageBox.Yes and not self.save_state(show_success=False):
            event.ignore()
            return
        self.stop_agent_bridge(silent=True)
        shutil.rmtree(getattr(self, "scenario_session_directory", ""), ignore_errors=True)
        super().closeEvent(event)
    
class CustomTableWidget(QTableWidget):
    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Copy):
            self.copy_selection_to_clipboard()
            event.accept()
            return

        if event.matches(QKeySequence.Paste):
            self.paste_clipboard_to_selection()
            event.accept()
            return

        if event.key() in (Qt.Key_Return, Qt.Key_Enter):  # Check for Enter key
            current_row = self.currentRow()
            current_column = self.currentColumn()

            # Move one row down, wrap around if at the last row
            next_row = (current_row + 1) % self.rowCount()
            self.setCurrentCell(next_row, current_column)

        else:
            # Default behavior for other keys
            super().keyPressEvent(event)

    def copy_selection_to_clipboard(self):
        indexes = sorted(self.selectedIndexes(), key=lambda index: (index.row(), index.column()))
        if not indexes:
            return

        rows = sorted({index.row() for index in indexes})
        columns = sorted({index.column() for index in indexes})
        selected_cells = {(index.row(), index.column()) for index in indexes}

        copied_rows = []
        for row in rows:
            values = []
            for column in columns:
                values.append(self.cell_display_text(row, column) if (row, column) in selected_cells else "")
            copied_rows.append("\t".join(values))

        QApplication.clipboard().setText("\n".join(copied_rows))

    def paste_clipboard_to_selection(self):
        clipboard_text = QApplication.clipboard().text()
        if not clipboard_text:
            return

        rows = [
            row.split("\t")
            for row in clipboard_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        ]
        if rows and rows[-1] == [""]:
            rows.pop()
        if not rows:
            return

        selected_indexes = sorted(self.selectedIndexes(), key=lambda index: (index.row(), index.column()))
        if selected_indexes:
            start_row = selected_indexes[0].row()
            start_column = selected_indexes[0].column()
        else:
            start_row = max(self.currentRow(), 0)
            start_column = max(self.currentColumn(), 0)

        if len(rows) == 1 and len(rows[0]) == 1 and selected_indexes:
            value = rows[0][0]
            for index in selected_indexes:
                self.set_cell_display_text(index.row(), index.column(), value)
            return

        for row_offset, row_values in enumerate(rows):
            target_row = start_row + row_offset
            if target_row >= self.rowCount():
                break
            for column_offset, value in enumerate(row_values):
                target_column = start_column + column_offset
                if target_column >= self.columnCount():
                    break
                self.set_cell_display_text(target_row, target_column, value)

    def cell_display_text(self, row, column):
        widget = self.cellWidget(row, column)
        if isinstance(widget, QComboBox):
            return widget.currentText()

        item = self.item(row, column)
        return item.text() if item else ""

    def set_cell_display_text(self, row, column, value):
        widget = self.cellWidget(row, column)
        if isinstance(widget, QComboBox):
            value = str(value).strip()
            if widget.findText(value) == -1:
                widget.addItem(value)
            widget.setCurrentText(value)
            return
        if widget is not None:
            return

        item = self.item(row, column)
        if item is not None and not (item.flags() & Qt.ItemIsEditable):
            return

        if item is None:
            item = QTableWidgetItem()
            item.setTextAlignment(Qt.AlignCenter)
            self.setItem(row, column, item)
        item.setText(str(value))

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
    set_windows_app_user_model_id()

    if "--agent-bridge-server" in sys.argv:
        close_bootloader_splash()
        sys.argv = [arg for arg in sys.argv if arg != "--agent-bridge-server"]
        try:
            from GUI.AgentBridgeServer import main as run_agent_bridge_server
        except ImportError:
            from AgentBridgeServer import main as run_agent_bridge_server
        run_agent_bridge_server()
        sys.exit(0)

    app = QApplication(sys.argv)
    
    # Set the global font to Segoe UI, size 12
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    app.setWindowIcon(blendmaster_app_icon())
    splash = None
    if not getattr(sys, "frozen", False):
        splash = create_startup_splash()
        splash.show()
        app.processEvents()
    
    window = UserInputs()  # Create an instance of the imported class
    window.show()              # Show the GUI
    window.apply_windows_taskbar_icon()
    QTimer.singleShot(500, window.apply_windows_taskbar_icon)
    QTimer.singleShot(1500, window.apply_windows_taskbar_icon)
    close_bootloader_splash()
    if splash is not None:
        splash.finish(window)
    sys.exit(app.exec_())      # Run the event loop

