# This is the control centre in which user and inventory data are imported and the program is executed
from PyQt5.QtCore import QObject, pyqtSignal, QEventLoop
import builtins, pandas as pd
from classes.CaseModeller import CaseModeller
from classes.DataLoader import DataLoader
from classes.PeriodManager import PeriodManager
from classes.ExpitDataHandler import ExpitDataHandler
from database.SQLiteDatabase import DatabaseManager
from execute.Requirements import Requirements
from pandas import DataFrame

class Run:
    
    def __init__(self, gui):
        self.gui = gui
        self.case_bridge = CaseModellerBridge()

        # Connect bridge signals to the GUI
        self.case_bridge.output_signal.connect(gui.display_decision_output)
        self.case_bridge.dataframe_signal.connect(gui.display_decision_dataframe)
        self.case_bridge.input_request_signal.connect(gui.display_decision_output)
        self.case_modeller = None
        self.manual_case_modeller = None
        self.manual_blend_dash = None
    
    def execute(self, start_time, expit_mode, file_path, blend_mode, stockpile_data, calendar_inputs):

        # Install required libraries
        #requirements = Requirements()
        #requirements.install_requirements()

        # Initialize periods
        periods = PeriodManager()
        periods.calculate_periods(start_time)

        # Process APS expit data (mining.csv)
        if file_path:
            expit_data_handler = ExpitDataHandler(file_path)
            expit_payload_transactions = expit_data_handler.process_transactions()
        else:
            expit_payload_transactions = DataFrame()

        # User interaction required to choose between original time and updated time methods
        user_interaction_mode = expit_mode

        # Cast user choice to appropriate type
        try:
            user_interaction_mode = int(user_interaction_mode)
        except ValueError:
            print("Invalid input. Please enter a number.")

        database_manager = DatabaseManager()

        if user_interaction_mode == 2 and file_path:

            expit_payload_transactions = expit_data_handler.update_transactions(expit_payload_transactions, start_time)
            #expit_payload_transactions.to_excel(fr"C:\BlendMaster\blendmaster_OOP\output\expit_payload_transactions.xlsx")
            expit_payload_transactions_copy = expit_payload_transactions.copy()
            database_manager.write_expit_payload_transactions_to_database(expit_payload_transactions_copy)

        elif user_interaction_mode == 1 and file_path:
            #expit_payload_transactions.to_excel(fr"C:\BlendMaster\blendmaster_OOP\output\expit_payload_transactions.xlsx")
            expit_payload_transactions_copy = expit_payload_transactions.copy()
            database_manager.write_expit_payload_transactions_to_database(expit_payload_transactions_copy)

        else: 
            print("No APS schedule imported.")

        # Load input data (this is combined user input and opening inventories)
        input_data = DataLoader(stockpile_data, calendar_inputs, expit_payload_transactions)

        stockpile_data_objects, equipment_data_objects, crusher_target_data = input_data.load_data()

        # Initialise and run CaseModeller
        self.case_modeller = CaseModeller(
            stockpiles=stockpile_data_objects,
            grade_blocks=[], # Placeholder
            equipment=equipment_data_objects,
            crusher_targets=crusher_target_data,
            expit_payload_transactions=expit_payload_transactions,
            periods=periods,
            user_interaction_mode=blend_mode
        )

        # Monkey-patch print and input
        original_print = builtins.print
        original_input = builtins.input
        try:
            builtins.print = self.case_bridge.print
            builtins.input = self.case_bridge.input
            self.case_modeller.run()  # Run the CaseModeller logic
        finally:
            # Restore the original print and input functions
            builtins.print = original_print
            builtins.input = original_input

        # Set start and end datetime in main GUI
        self.gui.set_start_and_end_datetime(periods=periods)
        
class CaseModellerBridge(QObject):
    output_signal = pyqtSignal(str)
    dataframe_signal = pyqtSignal(object)
    input_request_signal = pyqtSignal(str)
    input_response_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)  # Forwards errors to the GUI

    def __init__(self):
        super().__init__()
        self._input_response = None

    def print(self, message):
        if isinstance(message, pd.DataFrame):
            self.dataframe_signal.emit(message)
        else:
            self.output_signal.emit(str(message))

    def input(self, prompt):
        self.input_request_signal.emit(prompt)
        loop = QEventLoop()
        self.input_response_signal.connect(lambda text: loop.quit())
        loop.exec_()
        return self._input_response

    def send_input(self, user_input):
        self._input_response = user_input
        self.input_response_signal.emit(user_input)

    def handle_exception(self, exception):
        """Handle exceptions raised by the Case Modeller."""
        error_message = str(exception)
        self.error_signal.emit(error_message)
