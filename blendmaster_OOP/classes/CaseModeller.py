# This is where the main workflow is defined (everything happens here)
from classes.BalanceTracker import BalanceTracker
from classes.EventPoolGenerator import EventPoolGenerator
from classes.EquipmentData import EquipmentData
from classes.StockpileData import StockpileData
from classes.GradeBlockData import GradeBlockData
from classes.Optimizer import Optimizer
from classes.CrusherTarget import CrusherTarget
from database.SQLiteDatabase import DatabaseManager
from classes.PeriodManager import PeriodManager
import pandas as pd
from datetime import timedelta
from typing import List, Optional
from types import SimpleNamespace

class CaseModeller:
    MAX_DECISION_BLEND_OPTIONS = 12

    def __init__(
        self,
        stockpiles: List[StockpileData],
        grade_blocks: List[GradeBlockData],
        equipment: List[EquipmentData],
        crusher_targets,
        expit_payload_transactions,
        periods: PeriodManager,
        user_interaction_mode,
        hex_sequence_table,
        min_stockpiles: Optional[int] = None,
        max_stockpiles: Optional[int] = None,
        min_stockpile_contribution_ratio: Optional[float] = None,
        solver_config: Optional[dict] = None,
    ):
        self.stockpiles = stockpiles
        self.grade_blocks = grade_blocks
        self.equipment = equipment
        self.crusher_targets = crusher_targets
        self.periods = periods
        self.current_time = periods.get_periods()["preplan_start"]
        self.start_time = periods.get_periods()["preplan_start"]
        self.period_tracker = "preplan"
        self.balance_tracker = BalanceTracker(stockpiles, grade_blocks, self.period_tracker, hex_sequence_table)
        self.expit_payload_transactions = expit_payload_transactions
        self.event_pool = EventPoolGenerator(stockpiles, grade_blocks, equipment)
        self.optimizer = Optimizer()
        self.results = pd.DataFrame()
        self.build_report = pd.DataFrame()
        self.steady_state_tracker = 0
        self.blend_option = 1
        self.user_blend_choice = None
        self.blend_ID = 1
        self.decision_point_results = pd.DataFrame()
        self.decision_point_results_to_display = pd.DataFrame()
        self.decision_point_results_to_display_filtered_to_current_blend_choice = pd.DataFrame()
        self.user_interaction_mode = user_interaction_mode
        self.database_manager = DatabaseManager()
        self.total_AMT_stockpile_balances = self.balance_tracker.return_total_AMT_stockpile_balances()
        self.min_stockpiles = min_stockpiles
        self.max_stockpiles = max_stockpiles
        self.min_stockpile_contribution_ratio = min_stockpile_contribution_ratio
        self.solver_config = solver_config or {}
        self.optimization_diagnostics = []

    def run(self):
        """Runs the modeling process, coordinating optimization and time tracking."""
        try:
            while self.current_time < self.periods.get_periods()["period_2_end"]:
                # Run optimization and only advance time if successful
                self.run_optimization_step()
                self.steady_state_tracker += 1
        finally:
            # Save stockpile build report to database
            self.save_build_report()

            # Save blend results to database
            self.save_optimised_blend_report()

    def run_optimization_step(self):
        """Run a single optimization step for the initial steady state duration."""
        events = self.event_pool.get_events(self.period_tracker, pd.DataFrame(), self.current_time, self.balance_tracker)
        
        # This method will be ultimately redundant as stockpile balances are updated in the is_stockpile_ready method of EventPoolGenerator and the same can be done for grade blocks (at which point this method is no longer required)
        self.event_pool.update_event_balances(events, self.balance_tracker)
        
        period_crusher_target = CrusherTarget(self.crusher_targets).get_targets(self.period_tracker)
        candidate_source_sets = []
        candidate_source_signatures = set()

        if not events:
            result = {
                "Linprog_result_object": SimpleNamespace(
                    success=False,
                    status="No sources available",
                    status_code=None,
                ),
                "steady_state_duration": self.calculate_initial_steady_state_duration(),
                "diagnostics": Optimizer.build_diagnostics(
                    events,
                    period_crusher_target,
                    self.calculate_initial_steady_state_duration(),
                    self.periods,
                    self.period_tracker,
                    self.min_stockpiles,
                    self.max_stockpiles,
                    self.min_stockpile_contribution_ratio,
                    [],
                    "No sources available",
                    0,
                ),
            }
            self.register_optimization_diagnostic(result, events, "No sources available.")
            store_blend_option = self.blend_option
            self.blend_option = "No blend found"
            self.record_results(result)
            self.blend_option = store_blend_option

        if events:
            steady_state_end = self.current_time + timedelta(hours=self.calculate_initial_steady_state_duration())
            print(
                f"Solving steady state {self.steady_state_tracker} ({self.period_tracker}) "
                f"from {self.current_time:%Y-%m-%d %H:%M} to {steady_state_end:%Y-%m-%d %H:%M}. "
                f"{len(events)} source/equipment option(s) are available."
            )

        while events and len(candidate_source_sets) < self.MAX_DECISION_BLEND_OPTIONS:
            print(f"Searching feasible blend option {self.blend_option}...")
            # Run optimization with dynamic steady states
            result = self.optimizer.run_with_dynamic_steady_state(
                events,
                period_crusher_target,
                self.calculate_initial_steady_state_duration(),
                self.periods,
                self.period_tracker,
                self.current_time,
                self.stockpiles,
                self.min_stockpiles,
                self.max_stockpiles,
                self.min_stockpile_contribution_ratio,
                self.solver_config,
                candidate_source_sets,
            )

            if not result['Linprog_result_object'].success:
                if not candidate_source_sets:
                    self.register_optimization_diagnostic(result, events, "No feasible blend found.")
                    store_blend_option = self.blend_option
                    self.blend_option = "No blend found"
                    self.record_results(result)
                    self.blend_option = store_blend_option
                else:
                    print(f"No further feasible blend options found after {len(candidate_source_sets)} option(s).")
                break
        
            elif result['Linprog_result_object'].success:
                if result['crusher_actual_tonnes'] > Optimizer.SOLUTION_TOLERANCE:
                    active_sources = self.active_sources_from_result(result)
                    active_source_signature = frozenset(active_sources)
                    if not active_sources or active_source_signature in candidate_source_signatures:
                        print("No further distinct blend options found.")
                        break

                    self.record_results(result)
                    candidate_source_sets.append(set(active_sources))
                    candidate_source_signatures.add(active_source_signature)
                    print(
                        f"Found blend option {self.blend_option} using "
                        f"{', '.join(active_sources)}."
                    )
                    self.blend_option += 1
                    continue
                else:
                    if not candidate_source_sets:
                        self.register_optimization_diagnostic(result, events, "Solver returned zero crusher feed.")
                        store_blend_option = self.blend_option
                        self.blend_option = "No blend"
                        self.record_results(result)
                        self.blend_option = store_blend_option
                    else:
                        print(f"No further positive-feed blend options found after {len(candidate_source_sets)} option(s).")
                    break
            else:
                if not candidate_source_sets:
                    self.register_optimization_diagnostic(result, events, "Rare optimisation case.")
                    store_blend_option = self.blend_option
                    self.blend_option = "Rare case"
                    self.record_results(result)
                    self.blend_option = store_blend_option
                break

        if events and len(candidate_source_sets) >= self.MAX_DECISION_BLEND_OPTIONS:
            print(f"Stopped after {self.MAX_DECISION_BLEND_OPTIONS} feasible blend options.")

        # Check if there is any decision point results
        if "source_actual_tonnes" in self.decision_point_results:
            self.decision_point_results["source_actual_tonnes"] = (
                pd.to_numeric(
                    self.decision_point_results["source_actual_tonnes"],
                    errors="coerce",
                ).fillna(0)
            )
        if "crusher_actual_tonnes" in self.decision_point_results:
            self.decision_point_results["crusher_actual_tonnes"] = (
                pd.to_numeric(
                    self.decision_point_results["crusher_actual_tonnes"],
                    errors="coerce",
                ).fillna(0)
            )

        if (
            "source_actual_tonnes" in self.decision_point_results
            and (self.decision_point_results["source_actual_tonnes"] > 0).any()
        ):

            # Manage user interaction
            
            # Filter results to display
            self.decision_point_results_to_display = self.decision_point_results.loc[
                (
                    (self.decision_point_results["source_actual_tonnes"] != 0) & 
                    (self.decision_point_results["crusher_actual_tonnes"] != 0)
                )
            ]

            # Filter results to previous blend choice to compare results between iterations
            self.decision_point_results_to_display_filtered_to_current_blend_choice = self.decision_point_results.loc[
                (
                    (self.decision_point_results["source_actual_tonnes"] != 0) & 
                    (self.decision_point_results["crusher_actual_tonnes"] != 0) &
                    (self.decision_point_results["blend_option"] == self.user_blend_choice)

                )
            ]

            # Prompt user for interaction mode
            if self.user_interaction_mode == None:
                self.user_interaction_mode = input("\033[92mEnter 1 to automatically select the top blend option in every steady state or 2 to select manually: \033[0m")


            # Cast user choice to appropriate type
            try:
                self.user_interaction_mode = int(self.user_interaction_mode)
            except ValueError:
                print("Invalid input. Please enter a number.")
                return

            self.publish_decision_options()
            
            if self.steady_state_tracker != 0:

                # Compare the sources for a blend option between two iteration and avoid user interaction if no change
                current_filtered_sources =  self.decision_point_results_to_display_filtered_to_current_blend_choice["source"]
                previous_filtered_sources = self.results[
                    (self.results["blend_option"] == self.user_blend_choice) &
                    (self.results["steady_state_number"] == self.steady_state_tracker - 1)
                ]["source"]

            else: pass
            
            if self.user_interaction_mode == 1:
                
                self.user_blend_choice = 1

                if self.steady_state_tracker != 0:
                    if not list(current_filtered_sources) == list(previous_filtered_sources):
                        self.results.loc[self.results['blend_ID'] == self.blend_ID, 'blend_ID'] -= 1
                        self.blend_ID += 1
                    else: pass
                else: pass


            elif self.user_interaction_mode == 2 and self.steady_state_tracker != 0:
                
                if not list(current_filtered_sources) == list(previous_filtered_sources):
                    print("Blend fully depleted.")
                    self.user_blend_choice = input("Choose new blend: ")
                    self.results.loc[self.results['blend_ID'] == self.blend_ID, 'blend_ID'] -= 1
                    self.blend_ID += 1
                    # Cast user choice to appropriate type
                    try:
                        self.user_blend_choice = int(self.user_blend_choice)
                    except ValueError:
                        print("Invalid input. Please enter a number.")
                        return
                else:
                    pass
            
            elif self.user_interaction_mode == 2 and self.steady_state_tracker == 0:
                self.user_blend_choice = input("Choose blend: ")
                # Cast user choice to appropriate type
                try:
                    self.user_blend_choice = int(self.user_blend_choice)
                except ValueError:
                    print("Invalid input. Please enter a number.")
                    return
            
            # Filter results based on user choice
            filtered_decision_point_results_to_user_choice = self.decision_point_results.loc[
                (
                    (self.decision_point_results["source_actual_tonnes"] != 0) & 
                    (self.decision_point_results["crusher_actual_tonnes"] != 0) & 
                    (self.decision_point_results["blend_option"] == self.user_blend_choice)
                ) | 
                    (self.decision_point_results["blend_option"] == "No blend found")
                    |
                    (self.decision_point_results["blend_option"] == "Rare case")
                ]
            
            self.append_results(filtered_decision_point_results_to_user_choice)

            # Prepare for next cycle
            
            steady_state_start_time = self.current_time
            steady_state_end_time = steady_state_start_time + timedelta(hours=float(self.results.iloc[-1]["steady_state_duration"]))
            
            try:
                self.balance_tracker.update_balances(filtered_decision_point_results_to_user_choice, 
                                                    self.expit_payload_transactions,
                                                    steady_state_start_time,
                                                    steady_state_end_time,
                                                    self.steady_state_tracker
                                                    )
            except ValueError as e:
                error_message = str(e)
                print(f"Caught Error: {error_message}")
                raise

            self.total_AMT_stockpile_balances = self.balance_tracker.return_total_AMT_stockpile_balances()
            
            self.advance_time()
            
            self.decision_point_results = pd.DataFrame()
            self.blend_option = 1
        
        else:
            self.append_results(self.decision_point_results)

            # Prepare for next cycle (no results)
            
            steady_state_start_time = self.current_time
            steady_state_end_time = steady_state_start_time + timedelta(hours=float(self.results.iloc[-1]["steady_state_duration"]))

            try:
                self.balance_tracker.update_balances(self.decision_point_results, 
                                                    self.expit_payload_transactions,
                                                    steady_state_start_time,
                                                    steady_state_end_time,
                                                    self.steady_state_tracker
                                                    )
            except ValueError as e:
                error_message = str(e)
                print(f"Caught Error: {error_message}")
                raise

            self.total_AMT_stockpile_balances = self.balance_tracker.return_total_AMT_stockpile_balances()

            self.advance_time()
            
            self.decision_point_results = pd.DataFrame()
            self.blend_option = 1
    
    def publish_decision_options(self):
        display_columns = [
            "steady_state_number",
            "blend_option",
            "solver_score",
            "source",
            "source_blend_ratio",
            "source_actual_tonnes",
            "crusher_rate_output",
            "crusher_actual_grade_fe",
            "crusher_actual_grade_si",
            "crusher_actual_grade_al",
            "crusher_actual_grade_p",
            "crusher_actual_grade_mn",
        ]
        available_columns = [
            column for column in display_columns
            if column in self.decision_point_results_to_display.columns
        ]
        if available_columns:
            print(self.decision_point_results_to_display[available_columns])

        blend_options = sorted(
            self.decision_point_results_to_display["blend_option"].dropna().unique()
        )
        option_count = len(blend_options)
        if self.user_interaction_mode == 1:
            print(f"Auto select mode: Blend option 1 will be selected from {option_count} feasible option(s). Higher solver score is better.")
        elif self.user_interaction_mode == 2:
            print("Manual mode: choose a blend option from the table. Higher solver score is better.")

    def active_sources_from_result(self, result):
        """Return source names with positive tonnes in an optimisation result."""
        active_sources = []
        for transaction in result.get("transactions", []):
            try:
                actual_tonnes = float(transaction.get("actual_tonnes") or 0)
            except (TypeError, ValueError):
                actual_tonnes = 0
            if actual_tonnes > Optimizer.SOLUTION_TOLERANCE:
                active_sources.append(transaction.get("source"))
        return sorted(source for source in set(active_sources) if source)

    def register_optimization_diagnostic(self, result, events, message):
        diagnostics = dict(result.get("diagnostics") or {})
        steady_state_duration = diagnostics.get(
            "steady_state_duration",
            result.get("steady_state_duration", self.calculate_initial_steady_state_duration()),
        )
        diagnostics.update(
            {
                "message": message,
                "steady_state_number": self.steady_state_tracker,
                "period": self.period_tracker,
                "start_datetime": self.current_time,
                "end_datetime": self.current_time + timedelta(hours=float(steady_state_duration or 0)),
                "blend_option": self.blend_option,
                "event_count": len(events),
            }
        )
        self.optimization_diagnostics.append(diagnostics)

    def advance_time(self):
        """Advance current time and update period if needed."""
        steady_state_duration = float(self.results.iloc[-1]["steady_state_duration"])
        self.current_time += timedelta(hours=steady_state_duration)

        # Switch periods if needed
        if self.current_time >= self.periods.get_periods()["preplan_end"] and self.period_tracker == "preplan":
            self.period_tracker = "period_1"
        elif self.current_time >= self.periods.get_periods()["period_1_end"] and self.period_tracker == "period_1":
            self.period_tracker = "period_2"

    def record_results(self, result):
        """Record results from an optimization run into the main DataFrame."""
        if not result['Linprog_result_object'].success:
            report_data = [
                {
                    "start_datetime": self.current_time,
                    "end_datetime": self.current_time + timedelta(hours=result["steady_state_duration"]),
                    "steady_state_number": self.steady_state_tracker,
                    "blend_option": "No blend selected",
                    "blend_ID": "No blend selected",
                    "solver_score": result.get("solver_score", ""),
                    "steady_state_duration": result["steady_state_duration"],
                    "period": self.period_tracker,
                    "source": "",
                    "source_blend_ratio": "No tonnes selected",
                    "source_opening_balance": "",
                    "source_actual_tonnes": "No tonnes selected",
                    "source_closing_balance": "",
                    "source_grade_fe": "",
                    "source_grade_si": "",
                    "source_grade_al": "",
                    "source_grade_p": "",
                    "source_grade_mn": "",
                    "equipment": "",
                    "equipment_rate_input": "",
                    "equipment_rate_output": "",
                    "crusher_actual_tonnes": "No crusher feed in steady state",
                    "crusher_rate_input": "",
                    "crusher_rate_output": "",
                    "crusher_actual_grade_fe": "",
                    "crusher_actual_grade_si": "",
                    "crusher_actual_grade_al": "",
                    "crusher_actual_grade_p": "",
                    "crusher_actual_grade_mn": "",
                    "crusher_grade_target_min_fe": "",
                    "crusher_grade_target_max_fe": "",
                    "crusher_grade_target_min_si": "",
                    "crusher_grade_target_max_si": "",
                    "crusher_grade_target_min_al": "",
                    "crusher_grade_target_max_al": "",
                    "crusher_grade_target_min_p": "",
                    "crusher_grade_target_max_p": "",
                    "crusher_grade_target_min_mn": "",
                    "crusher_grade_target_max_mn": ""
                }
            ]
        else:
            report_data = [
                {
                    "start_datetime": self.current_time,
                    "end_datetime": self.current_time + timedelta(hours=result["steady_state_duration"]),
                    "steady_state_number": self.steady_state_tracker,
                    "blend_option": self.blend_option,
                    "blend_ID": self.blend_ID,
                    "solver_score": result.get("solver_score", ""),
                    "steady_state_duration": result["steady_state_duration"],
                    "period": self.period_tracker,
                    "source": transaction["source"],
                    "source_blend_ratio": round(transaction["equipment_rate_output"] / result["crusher_rate_output"], 2) if result["crusher_rate_output"] != 0 else 0,
                    "source_opening_balance": self.total_AMT_stockpile_balances[transaction["source"]] if transaction["source"] in self.total_AMT_stockpile_balances else transaction["opening_balance"],
                    "source_actual_tonnes": transaction["actual_tonnes"],
                    "source_closing_balance": (self.total_AMT_stockpile_balances[transaction["source"]] if transaction["source"] in self.total_AMT_stockpile_balances else transaction["opening_balance"]) - transaction["actual_tonnes"],
                    "source_grade_fe": transaction["grade_fe"],
                    "source_grade_si": transaction["grade_si"],
                    "source_grade_al": transaction["grade_al"],
                    "source_grade_p": transaction["grade_p"],
                    "source_grade_mn": transaction["grade_mn"],
                    "equipment": transaction["equipment"],
                    "equipment_rate_input": transaction["equipment_rate_input"],
                    "equipment_rate_output": transaction["equipment_rate_output"],
                    "crusher_actual_tonnes": result["crusher_actual_tonnes"],
                    "crusher_rate_input": result["crusher_rate_input"],
                    "crusher_rate_output": result["crusher_rate_output"],
                    "crusher_actual_grade_fe": 0 if self.blend_option == "No blend" else result["crusher_actual_grade_fe"],
                    "crusher_actual_grade_si": 0 if self.blend_option == "No blend" else result["crusher_actual_grade_si"],
                    "crusher_actual_grade_al": 0 if self.blend_option == "No blend" else result["crusher_actual_grade_al"],
                    "crusher_actual_grade_p": 0 if self.blend_option == "No blend" else result["crusher_actual_grade_p"],
                    "crusher_actual_grade_mn": 0 if self.blend_option == "No blend" else result["crusher_actual_grade_mn"],
                    "crusher_grade_target_min_fe": result["crusher_grade_target_min_fe"],
                    "crusher_grade_target_max_fe": result["crusher_grade_target_max_fe"],
                    "crusher_grade_target_min_si": result["crusher_grade_target_min_si"],
                    "crusher_grade_target_max_si": result["crusher_grade_target_max_si"],
                    "crusher_grade_target_min_al": result["crusher_grade_target_min_al"],
                    "crusher_grade_target_max_al": result["crusher_grade_target_max_al"],
                    "crusher_grade_target_min_p": result["crusher_grade_target_min_p"],
                    "crusher_grade_target_max_p": result["crusher_grade_target_max_p"],
                    "crusher_grade_target_min_mn": result["crusher_grade_target_min_mn"],
                    "crusher_grade_target_max_mn": result["crusher_grade_target_max_mn"]
                }
                for transaction in result["transactions"]
            ]

        self.decision_point_results = pd.concat([self.decision_point_results, pd.DataFrame(report_data)], ignore_index=True)

    def append_results(self, filtered_decision_point_results_to_user_choice):
        """Append filtered results to the main DataFrame."""
        self.results = pd.concat([self.results, pd.DataFrame(filtered_decision_point_results_to_user_choice)], ignore_index=True)
    
    def save_optimised_blend_report(self):
        """Save results to an Excel file."""
        # Ensure numeric columns for comparison
        if (
            "source_actual_tonnes" in self.results
            and "crusher_actual_tonnes" in self.results
        ):
            self.results["source_actual_tonnes"] = pd.to_numeric(
                self.results["source_actual_tonnes"], errors="coerce"
            )
            self.results["crusher_actual_tonnes"] = pd.to_numeric(
                self.results["crusher_actual_tonnes"], errors="coerce"
            )

            self.results = self.results.loc[
                (
                    (self.results["source_actual_tonnes"] != 0)
                    & (self.results["crusher_actual_tonnes"] != 0)
                )
                |
                (
                    (self.results["source_actual_tonnes"] == 0)
                    & (self.results["crusher_actual_tonnes"] == 0)
                )
            ]

        #self.results.to_excel(filename, index=False)
        #print(f"All results written to {filename}")

        self.database_manager.write_optimised_blend_report_to_database(self.results, self.periods)

    def save_build_report(self):
        """Save stockpile build report to an Excel file."""
        self.build_report = self.balance_tracker.get_build_transactions()

        #self.build_report.to_excel(filename, index=False)
        #print(f"All results written to {filename}")

        self.database_manager.write_build_report_to_database(self.build_report)
       
    def calculate_initial_steady_state_duration(self):
        """Calculate initial steady state duration based on the current time and periods."""
        if self.current_time >= self.periods.get_periods()["preplan_start"] and self.current_time < self.periods.get_periods()["preplan_end"]: 
            return (self.periods.get_periods()["preplan_end"] - self.current_time).total_seconds() / 3600
        elif self.current_time >= self.periods.get_periods()["period_1_start"] and self.current_time < self.periods.get_periods()["period_1_end"]:
            return (self.periods.get_periods()["period_1_end"] - self.current_time).total_seconds() / 3600
        elif self.current_time >= self.periods.get_periods()["period_2_start"] and self.current_time < self.periods.get_periods()["period_2_end"]:
            return (self.periods.get_periods()["period_2_end"] - self.current_time).total_seconds() / 3600
        else:
            return 0
