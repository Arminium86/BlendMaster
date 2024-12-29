# This is the control centre in which user and inventory data are imported and the program is executed
from PyQt5.QtCore import QObject, pyqtSignal, QEventLoop
import builtins, pandas as pd
from classes.CaseModeller import CaseModeller
from classes.DataLoader import DataLoader
from classes.PeriodManager import PeriodManager
from classes.ExpitDataHandler import ExpitDataHandler
from database.SQLiteDatabase import DatabaseManager
from execute.Requirements import Requirements

class Run:
    
    def __init__(self, gui):
        self.gui = gui
        self.case_bridge = CaseModellerBridge()

        # Connect bridge signals to the GUI
        self.case_bridge.output_signal.connect(gui.display_decision_output)
        self.case_bridge.dataframe_signal.connect(gui.display_decision_dataframe)
        self.case_bridge.input_request_signal.connect(gui.display_decision_output)
        self.case_modeller = None
    
    def execute(self, start_time, expit_mode, file_path, blend_mode, stockpile_data, calendar_inputs):

        # Install required libraries
        requirements = Requirements()
        requirements.install_requirements()

        # Initialize periods
        periods = PeriodManager()
        periods.calculate_periods(start_time)

        # Process APS expit data (mining.csv)
        expit_data_handler = ExpitDataHandler(file_path)
        expit_payload_transactions = expit_data_handler.process_transactions()

        # User interaction required to choose between original time and updated time methods
        user_interaction_mode = expit_mode

        # Cast user choice to appropriate type
        try:
            user_interaction_mode = int(user_interaction_mode)
        except ValueError:
            print("Invalid input. Please enter a number.")

        database_manager = DatabaseManager()

        if user_interaction_mode == 2:

            expit_payload_transactions = expit_data_handler.update_transactions(expit_payload_transactions, start_time)
            #expit_payload_transactions.to_excel(fr"C:\BlendMaster\blendmaster_OOP\output\expit_payload_transactions.xlsx")
            expit_payload_transactions_copy = expit_payload_transactions.copy()
            database_manager.write_expit_payload_transactions_to_database(expit_payload_transactions_copy)

        elif user_interaction_mode == 1:
            #expit_payload_transactions.to_excel(fr"C:\BlendMaster\blendmaster_OOP\output\expit_payload_transactions.xlsx")
            expit_payload_transactions_copy = expit_payload_transactions.copy()
            database_manager.write_expit_payload_transactions_to_database(expit_payload_transactions_copy)

        else: 
            print("Invalid input. Please enter a number.")
            return  # Exit execution for invalid input

        # Load input data (this is combined user input and opening inventories)
        input_data = DataLoader(stockpile_data, calendar_inputs, expit_payload_transactions)

        stockpile_data_objects, equipment_data_objects, crusher_target_data = input_data.load_data()

        # Initialize and run CaseModeller
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

class CaseModellerBridge(QObject):
    output_signal = pyqtSignal(str)  # Emit CaseModeller's outputs to the GUI
    dataframe_signal = pyqtSignal(object)  # Use object to pass a DataFrame
    input_request_signal = pyqtSignal(str)  # Emit input requests to the GUI
    input_response_signal = pyqtSignal(str)  # Emit responses back to CaseModeller


    def __init__(self):
        super().__init__()
        self._input_response = None

    def print(self, message):
            """Redirect print to the GUI."""
            if isinstance(message, pd.DataFrame):
                self.dataframe_signal.emit(message)  # Emit DataFrame directly
            else:
                self.output_signal.emit(str(message))  # Emit as string

    def input(self, prompt):
        """Redirect input to the GUI."""
        self.input_request_signal.emit(prompt)
        loop = QEventLoop()

        # Wait for the input response
        self.input_response_signal.connect(lambda text: loop.quit())
        loop.exec_()
        return self._input_response

    def send_input(self, user_input):
        """Receive input from the GUI and send it back to CaseModeller."""
        self._input_response = user_input
        self.input_response_signal.emit(user_input)