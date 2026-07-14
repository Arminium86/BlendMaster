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

class BlendMasterRunError(Exception):
    def __init__(self, message, title="BlendMaster"):
        super().__init__(message)
        self.user_message = message
        self.title = title


class InfeasibleRunError(BlendMasterRunError):
    def __init__(self, message):
        super().__init__(message, title="Infeasible Run")


class StockpileSelectionRunError(BlendMasterRunError):
    def __init__(self, stockpile_name):
        message = (
            f"APS Mining.csv contains transactions with destination stockpile '{stockpile_name}', "
            "but that stockpile is not selected in Stockpile Inventories.\n\n"
            "Go back to Stockpile Inventories and tick Use for this stockpile, or remove/change "
            "those destination transactions in APS Mining.csv."
        )
        super().__init__(message, title="Stockpile Selection Required")


class Run:
    
    def __init__(self, gui):
        self.gui = gui
        self.case_bridge = CaseModellerBridge()

        # Connect bridge signals to the GUI
        self.case_bridge.output_signal.connect(gui.display_decision_output)
        if hasattr(gui, "update_progress_message"):
            self.case_bridge.output_signal.connect(gui.update_progress_message)
        self.case_bridge.dataframe_signal.connect(gui.display_decision_dataframe)
        self.case_bridge.input_request_signal.connect(gui.display_decision_output)
        self.case_modeller = None
        self.manual_case_modeller = None
        self.manual_blend_dash = None
        self.abort_requested = False

    def request_abort(self):
        self.abort_requested = True
        if self.case_modeller is not None and hasattr(self.case_modeller, "request_abort"):
            self.case_modeller.request_abort()

    def is_abort_requested(self):
        return bool(self.abort_requested)
    
    def execute(
        self,
        start_time,
        expit_mode,
        file_path,
        blend_mode,
        stockpile_data,
        calendar_inputs,
        hex_sequence_table,
        min_stockpiles=None,
        max_stockpiles=None,
        min_stockpile_contribution_ratio=None,
        solver_config=None,
        reevaluate_aps_direct_tip=False,
        selected_aps_crusher=None,
        site_context=None,
    ):
        self.abort_requested = False

        # Install required libraries
        #requirements = Requirements()
        #requirements.install_requirements()

        # Initialize periods
        periods = PeriodManager()
        periods.calculate_periods(start_time)

        # Process APS expit data (mining.csv)
        if file_path:
            expit_data_handler = ExpitDataHandler(
                file_path,
                include_crusher_destinations=reevaluate_aps_direct_tip,
                selected_crusher_name=selected_aps_crusher,
                operational_mine=(site_context or {}).get("mine"),
                operational_crusher=(site_context or {}).get("crusher"),
            )
            expit_payload_transactions = expit_data_handler.process_transactions()
            expit_payload_transactions = self._ensure_direct_tip_ids(expit_payload_transactions)
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
        expit_payload_transactions_to_save = None
        solver_config = dict(solver_config or {})
        solver_config["product_builds_configured"] = bool(
            (calendar_inputs or {}).get("product_build_settings")
        )

        if user_interaction_mode == 2 and file_path:

            expit_payload_transactions = expit_data_handler.update_transactions(expit_payload_transactions, start_time)
            expit_payload_transactions = self._ensure_direct_tip_ids(expit_payload_transactions)
            expit_payload_transactions_to_save = expit_payload_transactions.copy()

        elif user_interaction_mode == 1 and file_path:
            expit_payload_transactions_to_save = expit_payload_transactions.copy()

        else: 
            print("No APS schedule imported.")

        # Load input data (this is combined user input and opening inventories)
        input_data = DataLoader(stockpile_data, calendar_inputs, expit_payload_transactions, hex_sequence_table, periods)

        stockpile_data_objects, grade_block_data_objects, equipment_data_objects, crusher_target_data = input_data.load_data()

        min_stockpile_contribution_ratio = self._normalize_stockpile_contribution_ratio(
            min_stockpile_contribution_ratio
        )

        # Initialise and run CaseModeller
        self.case_modeller = CaseModeller(
            stockpiles=stockpile_data_objects,
            grade_blocks=grade_block_data_objects,
            equipment=equipment_data_objects,
            crusher_targets=crusher_target_data,
            expit_payload_transactions=expit_payload_transactions,
            periods=periods,
            user_interaction_mode=blend_mode,
            hex_sequence_table=hex_sequence_table,
            min_stockpiles=min_stockpiles,
            max_stockpiles=max_stockpiles,
            min_stockpile_contribution_ratio=min_stockpile_contribution_ratio,
            solver_config=solver_config,
            product_build_settings=(calendar_inputs or {}).get("product_build_settings", []),
            abort_callback=self.is_abort_requested,
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
            stockpile_selection_error = self._stockpile_selection_error(run_exception)
            if stockpile_selection_error is not None:
                raise stockpile_selection_error
            raise run_exception

        self._validate_optimization_results()
        self._validate_stockpile_count_constraints(
            min_stockpiles,
            max_stockpiles,
            min_stockpile_contribution_ratio,
        )

        self.case_bridge.print("Optimisation complete. Writing database tables...")

        if expit_payload_transactions_to_save is not None:
            self.case_bridge.print("Writing expit payload transactions to database...")
            database_manager.write_expit_payload_transactions_to_database(expit_payload_transactions_to_save)

        self.case_bridge.print("Writing build report to database...")
        self.case_modeller.save_build_report()

        self.case_bridge.print("Writing optimised blend, depletion and profile reports to database...")
        self.case_modeller.save_optimised_blend_report()

        self.case_bridge.print("Writing product build report to database...")
        self.case_modeller.save_product_build_report()

        self.case_bridge.print("Database tables written successfully.")

        return periods

    @staticmethod
    def _ensure_direct_tip_ids(expit_payload_transactions):
        if expit_payload_transactions is None or expit_payload_transactions.empty:
            return expit_payload_transactions
        expit_payload_transactions = expit_payload_transactions.copy()
        if "direct_tip_id" not in expit_payload_transactions.columns:
            expit_payload_transactions["direct_tip_id"] = [
                f"GB_{index + 1:06d}" for index in range(len(expit_payload_transactions))
            ]
        return expit_payload_transactions

    @staticmethod
    def _stockpile_selection_error(exception):
        message = str(exception)
        marker = "Stockpile '"
        if (
            marker not in message
            or "not selected or found in Stockpile Inventories" not in message
        ):
            return None

        stockpile_name = message.split(marker, 1)[1].split("'", 1)[0]
        return StockpileSelectionRunError(stockpile_name)

    @staticmethod
    def _normalize_stockpile_contribution_ratio(min_stockpile_contribution_ratio=None):
        if min_stockpile_contribution_ratio is None:
            return Optimizer.MIN_SELECTED_STOCKPILE_BLEND_RATIO

        min_stockpile_contribution_ratio = float(min_stockpile_contribution_ratio)
        if not 0.01 <= min_stockpile_contribution_ratio <= 1:
            raise ValueError("Min Stockpile Contribution Ratio must be between 0.01 and 1.")

        return min_stockpile_contribution_ratio

    def _validate_optimization_results(self):
        results = getattr(self.case_modeller, "results", None)
        diagnostics = self._first_optimization_diagnostic()
        if results is None or results.empty:
            raise InfeasibleRunError(
                self._format_infeasible_run_message(
                    "No feasible blend was produced.",
                    diagnostics,
                )
            )

        results = results.copy()
        results["source_actual_tonnes"] = pd.to_numeric(
            results.get("source_actual_tonnes"), errors="coerce"
        ).fillna(0)
        results["crusher_actual_tonnes"] = pd.to_numeric(
            results.get("crusher_actual_tonnes"), errors="coerce"
        ).fillna(0)

        failed_blend_labels = {"no blend selected", "no blend found", "no blend", "rare case"}
        if "blend_option" in results:
            failed_rows = results[
                results["blend_option"].astype(str).str.lower().isin(failed_blend_labels)
            ]
            if not failed_rows.empty:
                failed_row = failed_rows.iloc[0]
                steady_state = failed_row.get("steady_state_number", "unknown")
                diagnostics = self._first_optimization_diagnostic(steady_state) or diagnostics
                raise InfeasibleRunError(
                    self._format_infeasible_run_message(
                        f"Steady state {steady_state} did not produce a feasible blend.",
                        diagnostics,
                    )
                )

        valid_feed = results[results["crusher_actual_tonnes"] > Optimizer.SOLUTION_TOLERANCE]
        if valid_feed.empty:
            raise InfeasibleRunError(
                self._format_infeasible_run_message(
                    "The run completed without selecting any positive crusher feed.",
                    diagnostics,
                )
            )

    def _validate_stockpile_count_constraints(self, min_stockpiles=None, max_stockpiles=None, min_stockpile_contribution_ratio=None):
        if min_stockpiles is None and max_stockpiles is None:
            return

        min_stockpile_contribution_ratio = self._normalize_stockpile_contribution_ratio(
            min_stockpile_contribution_ratio
        )

        results = getattr(self.case_modeller, "results", None)
        if results is None or results.empty:
            raise InfeasibleRunError(self._stockpile_constraint_message(
                min_stockpiles,
                max_stockpiles,
                min_stockpile_contribution_ratio,
                "No feasible blend was produced.",
                self._first_optimization_diagnostic(),
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
                raise InfeasibleRunError(self._stockpile_constraint_message(
                    min_stockpiles,
                    max_stockpiles,
                    min_stockpile_contribution_ratio,
                    "At least one steady state has no feasible blend.",
                    self._first_optimization_diagnostic(),
                ))

        valid_feed = results[results["crusher_actual_tonnes"] > Optimizer.SOLUTION_TOLERANCE]
        if valid_feed.empty:
            raise InfeasibleRunError(self._stockpile_constraint_message(
                min_stockpiles,
                max_stockpiles,
                min_stockpile_contribution_ratio,
                "No crusher feed was selected.",
                self._first_optimization_diagnostic(),
            ))

        ratio_tolerance = Optimizer.SOLUTION_TOLERANCE
        grade_block_names = {
            grade_block.name
            for grade_block in getattr(self.case_modeller, "grade_blocks", [])
        }
        for (steady_state, blend_id), blend_rows in valid_feed.groupby(
            ["steady_state_number", "blend_ID"], dropna=False
        ):
            source_ratios = (
                blend_rows["source_actual_tonnes"]
                / blend_rows["crusher_actual_tonnes"].replace(0, pd.NA)
            ).fillna(0)
            source_identifier = (
                blend_rows["source_id"] if "source_id" in blend_rows.columns else blend_rows["source"]
            )
            stockpile_source_ratios = source_ratios[
                ~source_identifier.isin(grade_block_names)
            ]
            active_stockpile_count = int(
                (stockpile_source_ratios >= min_stockpile_contribution_ratio - ratio_tolerance).sum()
            )

            if min_stockpiles is not None and active_stockpile_count < min_stockpiles:
                raise InfeasibleRunError(self._stockpile_constraint_message(
                    min_stockpiles,
                    max_stockpiles,
                    min_stockpile_contribution_ratio,
                    f"Steady state {steady_state}, blend {blend_id} only has {active_stockpile_count} stockpile(s) contributing at least {self._format_stockpile_contribution_ratio(min_stockpile_contribution_ratio)}.",
                    self._first_optimization_diagnostic(steady_state),
                ))

            if max_stockpiles is not None and active_stockpile_count > max_stockpiles:
                raise InfeasibleRunError(self._stockpile_constraint_message(
                    min_stockpiles,
                    max_stockpiles,
                    min_stockpile_contribution_ratio,
                    f"Steady state {steady_state}, blend {blend_id} has {active_stockpile_count} stockpile(s) contributing at least {self._format_stockpile_contribution_ratio(min_stockpile_contribution_ratio)}.",
                    self._first_optimization_diagnostic(steady_state),
                ))

    @staticmethod
    def _format_stockpile_contribution_ratio(min_stockpile_contribution_ratio):
        return f"{min_stockpile_contribution_ratio:g} ({min_stockpile_contribution_ratio * 100:g}%)"

    def _stockpile_constraint_message(self, min_stockpiles, max_stockpiles, min_stockpile_contribution_ratio, detail, diagnostics=None):
        constraints = []
        if min_stockpiles is not None:
            constraints.append(f"minimum {min_stockpiles}")
        if max_stockpiles is not None:
            constraints.append(f"maximum {max_stockpiles}")
        constraint_text = ", ".join(constraints)
        stockpile_message = (
            f"BlendMaster could not satisfy the stockpile-count constraints ({constraint_text}) "
            f"with each selected stockpile contributing at least "
            f"{Run._format_stockpile_contribution_ratio(min_stockpile_contribution_ratio)} of crusher feed. "
            "Review Solver Configuration and the Calendar inputs, then rerun."
        )
        return self._format_infeasible_run_message(detail, diagnostics, stockpile_message)

    def _first_optimization_diagnostic(self, steady_state=None):
        diagnostics = getattr(self.case_modeller, "optimization_diagnostics", []) or []
        if steady_state is not None:
            for diagnostic in diagnostics:
                if str(diagnostic.get("steady_state_number")) == str(steady_state):
                    return diagnostic
        return diagnostics[0] if diagnostics else None

    @staticmethod
    def _format_datetime(value):
        if hasattr(value, "strftime"):
            return value.strftime("%Y-%m-%d %H:%M")
        return str(value) if value not in (None, "") else "unknown"

    @staticmethod
    def _format_infeasible_run_message(detail, diagnostics=None, extra_detail=None):
        lines = [detail]

        if diagnostics:
            lines.extend([
                "",
                "Where:",
                f"- Steady state: {diagnostics.get('steady_state_number', 'unknown')}",
                f"- Period: {diagnostics.get('period', 'unknown')}",
                f"- Time window: {Run._format_datetime(diagnostics.get('start_datetime'))} to {Run._format_datetime(diagnostics.get('end_datetime'))}",
                f"- Solver status: {diagnostics.get('solver_status', 'unknown')}",
                f"- Available sources: {diagnostics.get('available_source_count', 0)} total, {diagnostics.get('positive_source_count', 0)} with positive reclaimable tonnes",
                f"- Positive stockpiles / grade blocks: {diagnostics.get('positive_stockpile_count', 0)} / {diagnostics.get('positive_grade_block_count', 0)}",
                f"- Crusher target tonnes in window: {float(diagnostics.get('target_tonnes') or 0):,.1f}",
                f"- Selected crusher tonnes: {float(diagnostics.get('selected_tonnes') or 0):,.1f}",
                f"- Direct tip: {'enabled' if diagnostics.get('direct_tip_enabled', True) else 'disabled'}",
                f"- Direct tip ratio target: {float(diagnostics.get('direct_feed_ratio_min') or 0):g} to {float(diagnostics.get('direct_feed_ratio_max') if diagnostics.get('direct_feed_ratio_max') is not None else 1):g}",
            ])

            likely_causes = diagnostics.get("likely_causes") or []
            if likely_causes:
                lines.extend(["", "Useful checks:"])
                lines.extend(f"- {cause}" for cause in likely_causes[:6])

            grade_ranges = diagnostics.get("grade_ranges") or {}
            if grade_ranges:
                lines.extend(["", "Grade target vs available range:"])
                for grade_name, values in grade_ranges.items():
                    lines.append(
                        f"- {grade_name}: target {values['target_min']:g} to {values['target_max']:g}; "
                        f"available {values['available_min']:g} to {values['available_max']:g}"
                    )

        if extra_detail:
            lines.extend(["", extra_detail])

        lines.extend([
            "",
            "Suggested next checks: Solver Configuration, Calendar grade targets, stockpile State, Max Quantity, balances and reclaim rates.",
        ])
        return "\n".join(lines)
        
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
