# This is the control centre in which user and inventory data are imported and the program is executed
from PyQt5.QtCore import QObject, pyqtSignal, QEventLoop
import builtins, pandas as pd, traceback
from classes.CaseModeller import CaseModeller
from classes.DataLoader import DataLoader
from classes.PeriodManager import PeriodManager
from classes.ExpitDataHandler import ExpitDataHandler
from classes.Optimizer import Optimizer
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
    
    def execute(self, start_time, expit_mode, file_path, blend_mode, stockpile_data, calendar_inputs, hex_sequence_table, min_stockpiles=None, max_stockpiles=None, min_stockpile_contribution_ratio=None):

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
        input_data = DataLoader(stockpile_data, calendar_inputs, expit_payload_transactions, hex_sequence_table)

        stockpile_data_objects, equipment_data_objects, crusher_target_data = input_data.load_data()

        min_stockpile_contribution_ratio = self._normalize_stockpile_contribution_ratio(
            min_stockpile_contribution_ratio
        )

        # Initialise and run CaseModeller
        self.case_modeller = CaseModeller(
            stockpiles=stockpile_data_objects,
            grade_blocks=[],  # Placeholder
            equipment=equipment_data_objects,
            crusher_targets=crusher_target_data,
            expit_payload_transactions=expit_payload_transactions,
            periods=periods,
            user_interaction_mode=blend_mode,
            hex_sequence_table=hex_sequence_table,
            min_stockpiles=min_stockpiles,
            max_stockpiles=max_stockpiles,
            min_stockpile_contribution_ratio=min_stockpile_contribution_ratio,
        )

        # Monkey-patch print and input
        original_print = builtins.print
        original_input = builtins.input
        run_exception = None
        try:
            builtins.print = self.case_bridge.print
            builtins.input = self.case_bridge.input
            self.case_modeller.run()  # Run the CaseModeller logic
        
        except Exception as e:
            run_exception = e
        
        finally:
            # Restore the original print and input functions
            builtins.print = original_print
            builtins.input = original_input

        if run_exception is not None:
            raise run_exception

        self._validate_stockpile_count_constraints(
            min_stockpiles,
            max_stockpiles,
            min_stockpile_contribution_ratio,
        )

        return periods

    @staticmethod
    def _normalize_stockpile_contribution_ratio(min_stockpile_contribution_ratio=None):
        if min_stockpile_contribution_ratio is None:
            return Optimizer.MIN_SELECTED_STOCKPILE_BLEND_RATIO

        min_stockpile_contribution_ratio = float(min_stockpile_contribution_ratio)
        if not 0.01 <= min_stockpile_contribution_ratio <= 1:
            raise ValueError("Min Stockpile Contribution Ratio must be between 0.01 and 1.")

        return min_stockpile_contribution_ratio

    def _validate_stockpile_count_constraints(self, min_stockpiles=None, max_stockpiles=None, min_stockpile_contribution_ratio=None):
        if min_stockpiles is None and max_stockpiles is None:
            return

        min_stockpile_contribution_ratio = self._normalize_stockpile_contribution_ratio(
            min_stockpile_contribution_ratio
        )

        results = getattr(self.case_modeller, "results", None)
        if results is None or results.empty:
            raise ValueError(self._stockpile_constraint_message(
                min_stockpiles,
                max_stockpiles,
                min_stockpile_contribution_ratio,
                "No feasible blend was produced.",
            ))

        results = results.copy()
        results["source_actual_tonnes"] = pd.to_numeric(
            results.get("source_actual_tonnes"), errors="coerce"
        ).fillna(0)
        results["crusher_actual_tonnes"] = pd.to_numeric(
            results.get("crusher_actual_tonnes"), errors="coerce"
        ).fillna(0)

        if "blend_option" in results:
            failed_blend_labels = {"no blend selected", "no blend found", "no blend", "rare case"}
            has_failed_blend = (
                results["blend_option"]
                .astype(str)
                .str.lower()
                .isin(failed_blend_labels)
                .any()
            )
            if has_failed_blend:
                raise ValueError(self._stockpile_constraint_message(
                    min_stockpiles,
                    max_stockpiles,
                    min_stockpile_contribution_ratio,
                    "At least one steady state has no feasible blend.",
                ))

        valid_feed = results[results["crusher_actual_tonnes"] > Optimizer.SOLUTION_TOLERANCE]
        if valid_feed.empty:
            raise ValueError(self._stockpile_constraint_message(
                min_stockpiles,
                max_stockpiles,
                min_stockpile_contribution_ratio,
                "No crusher feed was selected.",
            ))

        ratio_tolerance = Optimizer.SOLUTION_TOLERANCE
        for (steady_state, blend_id), blend_rows in valid_feed.groupby(
            ["steady_state_number", "blend_ID"], dropna=False
        ):
            source_ratios = (
                blend_rows["source_actual_tonnes"]
                / blend_rows["crusher_actual_tonnes"].replace(0, pd.NA)
            ).fillna(0)
            active_stockpile_count = int(
                (source_ratios >= min_stockpile_contribution_ratio - ratio_tolerance).sum()
            )

            if min_stockpiles is not None and active_stockpile_count < min_stockpiles:
                raise ValueError(self._stockpile_constraint_message(
                    min_stockpiles,
                    max_stockpiles,
                    min_stockpile_contribution_ratio,
                    f"Steady state {steady_state}, blend {blend_id} only has {active_stockpile_count} stockpile(s) contributing at least {self._format_stockpile_contribution_ratio(min_stockpile_contribution_ratio)}.",
                ))

            if max_stockpiles is not None and active_stockpile_count > max_stockpiles:
                raise ValueError(self._stockpile_constraint_message(
                    min_stockpiles,
                    max_stockpiles,
                    min_stockpile_contribution_ratio,
                    f"Steady state {steady_state}, blend {blend_id} has {active_stockpile_count} stockpile(s) contributing at least {self._format_stockpile_contribution_ratio(min_stockpile_contribution_ratio)}.",
                ))

    @staticmethod
    def _format_stockpile_contribution_ratio(min_stockpile_contribution_ratio):
        return f"{min_stockpile_contribution_ratio:g} ({min_stockpile_contribution_ratio * 100:g}%)"

    @staticmethod
    def _stockpile_constraint_message(min_stockpiles, max_stockpiles, min_stockpile_contribution_ratio, detail):
        constraints = []
        if min_stockpiles is not None:
            constraints.append(f"minimum {min_stockpiles}")
        if max_stockpiles is not None:
            constraints.append(f"maximum {max_stockpiles}")
        constraint_text = ", ".join(constraints)
        return (
            f"{detail}\n\n"
            f"BlendMaster could not satisfy the stockpile-count constraints ({constraint_text}) "
            f"with each selected stockpile contributing at least "
            f"{Run._format_stockpile_contribution_ratio(min_stockpile_contribution_ratio)} of crusher feed. "
            "Please go back to the Calendar tab, update the stockpile limits or other blend constraints, and rerun."
        )
        
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
        # Extract the traceback information
        tb_lines = traceback.format_exception(type(exception), exception, exception.__traceback__)
        error_message = "".join(tb_lines)  # Combine the traceback into a single string
        self.error_signal.emit(error_message)  # Emit the full traceback to the GUI
