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
from typing import Callable, List, Optional
from types import SimpleNamespace

class SolverRunAborted(Exception):
    def __init__(self, message="Optimisation run aborted by user."):
        super().__init__(message)
        self.user_message = message
        self.title = "Run Aborted"

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
        abort_callback: Optional[Callable[[], bool]] = None,
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
        self.previous_selected_stockpile_source_ids = set()
        self.previous_selected_grade_block_pairs = {}
        self.grade_block_pair_locks = {}
        self.abort_callback = abort_callback or (lambda: False)
        self.abort_requested = False

    def request_abort(self):
        self.abort_requested = True

    def check_abort_requested(self):
        if self.abort_requested or self.abort_callback():
            raise SolverRunAborted()

    def run(self):
        """Runs the modeling process, coordinating optimization and time tracking."""
        print(
            "Active solver configuration: "
            f"Min Grade Block Pair Duration = "
            f"{float(self.solver_config.get('min_grade_block_pair_duration_hours') or 0):.2f} hrs; "
            f"Min Stockpile Feed Duration = "
            f"{float(self.solver_config.get('min_feed_duration_hours') or 0):.2f} hrs; "
            f"Blend Option Timeout = "
            f"{float(self.solver_config.get('blend_option_timeout_seconds') or 0):.0f} sec; "
            f"Max Blend Options per Steady State = "
            f"{self.configured_max_decision_blend_options()}."
        )
        while self.current_time < self.periods.get_periods()["period_2_end"]:
            self.check_abort_requested()
            # Run optimization and only advance time if successful
            self.run_optimization_step()
            self.steady_state_tracker += 1
            self.check_abort_requested()

    def run_optimization_step(self):
        """Run a single optimization step for the initial steady state duration."""
        self.check_abort_requested()
        initial_steady_state_duration = self.calculate_initial_steady_state_duration()
        steady_state_end = self.current_time + timedelta(hours=initial_steady_state_duration)
        events = self.event_pool.get_events(
            self.period_tracker,
            pd.DataFrame(),
            self.current_time,
            steady_state_end,
            self.balance_tracker,
        )
        
        # This method will be ultimately redundant as stockpile balances are updated in the is_stockpile_ready method of EventPoolGenerator and the same can be done for grade blocks (at which point this method is no longer required)
        self.event_pool.update_event_balances(events, self.balance_tracker)
        
        period_crusher_target = CrusherTarget(self.crusher_targets).get_targets(self.period_tracker)
        candidate_source_sets = []
        excluded_source_sets = []
        candidate_source_signatures = set()
        required_min_feed_duration = self.configured_min_feed_duration_hours()
        blend_option_timeout_seconds = self.configured_blend_option_timeout_seconds()
        max_decision_blend_options = self.configured_max_decision_blend_options()
        step_solver_config = self.solver_config_for_current_step()
        last_solver_result = None
        no_selected_blend_message = "No feasible blend found."

        if not events:
            result = {
                "Linprog_result_object": SimpleNamespace(
                    success=False,
                    status="No sources available",
                    status_code=None,
                ),
                "steady_state_duration": initial_steady_state_duration,
                "diagnostics": Optimizer.build_diagnostics(
                    events,
                    period_crusher_target,
                    initial_steady_state_duration,
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
            print(
                f"Solving steady state {self.steady_state_tracker} ({self.period_tracker}) "
                f"from {self.current_time:%Y-%m-%d %H:%M} to {steady_state_end:%Y-%m-%d %H:%M}. "
                f"{len(events)} source/equipment option(s) are available."
            )

        while events and len(candidate_source_sets) < max_decision_blend_options:
            self.check_abort_requested()
            print(f"Searching feasible blend option {self.blend_option}...")
            # Run optimization with dynamic steady states
            result = self.optimizer.run_with_dynamic_steady_state(
                events,
                period_crusher_target,
                initial_steady_state_duration,
                self.periods,
                self.period_tracker,
                self.current_time,
                self.stockpiles,
                self.min_stockpiles,
                self.max_stockpiles,
                self.min_stockpile_contribution_ratio,
                step_solver_config,
                excluded_source_sets,
            )
            last_solver_result = result
            self.check_abort_requested()

            if not result['Linprog_result_object'].success:
                solver_status = getattr(result.get("Linprog_result_object"), "status", "")
                if not candidate_source_sets:
                    self.register_optimization_diagnostic(result, events, "No feasible blend found.")
                    store_blend_option = self.blend_option
                    self.blend_option = "No blend found"
                    self.record_results(result)
                    self.blend_option = store_blend_option
                else:
                    if (
                        blend_option_timeout_seconds is not None
                        and str(solver_status) in {"Not Solved", "Undefined"}
                    ):
                        print(
                            f"Stopped searching additional blend options after "
                            f"{blend_option_timeout_seconds:g} seconds for blend option {self.blend_option}. "
                            f"{len(candidate_source_sets)} feasible option(s) already found; moving on."
                        )
                    else:
                        print(f"No further feasible blend options found after {len(candidate_source_sets)} option(s).")
                break
        
            elif result['Linprog_result_object'].success:
                if result['crusher_actual_tonnes'] > Optimizer.SOLUTION_TOLERANCE:
                    active_source_ids = self.active_source_ids_from_result(result)
                    active_source_signature = frozenset(active_source_ids)
                    if not active_source_ids or active_source_signature in candidate_source_signatures:
                        if not candidate_source_sets:
                            no_selected_blend_message = (
                                "No further distinct blend options remained after applying guardrails."
                            )
                        print("No further distinct blend options found.")
                        break

                    lock_violations = self.grade_block_pair_lock_violations_from_result(result)
                    if lock_violations:
                        no_selected_blend_message = (
                            "All candidate blends were rejected by the grade block lock rule."
                        )
                        excluded_source_sets.append(set(active_source_ids))
                        candidate_source_signatures.add(active_source_signature)
                        print(
                            f"Rejected blend option {self.blend_option} because grade block lock "
                            f"would be breached: {'; '.join(lock_violations)}."
                        )
                        continue

                    grade_block_duration_issues = self.grade_block_pair_duration_issues_from_result(result)
                    if grade_block_duration_issues:
                        no_selected_blend_message = (
                            "All candidate blends were rejected by Min Grade Block Pair Duration."
                        )
                        excluded_source_sets.append(set(active_source_ids))
                        candidate_source_signatures.add(active_source_signature)
                        print(
                            f"Rejected blend option {self.blend_option} because grade block pair "
                            f"duration is too short: {'; '.join(grade_block_duration_issues)}."
                        )
                        continue

                    potential_feed_duration = self.candidate_potential_feed_duration(result)
                    if (
                        required_min_feed_duration is not None
                        and potential_feed_duration + Optimizer.SOLUTION_TOLERANCE < required_min_feed_duration
                    ):
                        no_selected_blend_message = (
                            "All candidate blends were rejected by Min Stockpile Feed Duration."
                        )
                        excluded_source_sets.append(set(active_source_ids))
                        candidate_source_signatures.add(active_source_signature)
                        active_source_names = self.active_source_names_from_result(result)
                        print(
                            f"Rejected blend option {self.blend_option} using "
                            f"{', '.join(active_source_names)} because its stockpile blend can "
                            f"potentially feed for {potential_feed_duration:.2f} hours; minimum feed duration is "
                            f"{required_min_feed_duration:.2f} hours."
                        )
                        continue

                    self.record_results(result)
                    candidate_source_sets.append(set(active_source_ids))
                    excluded_source_sets.append(set(active_source_ids))
                    candidate_source_signatures.add(active_source_signature)
                    active_source_names = self.active_source_names_from_result(result)
                    print(
                        f"Found blend option {self.blend_option} using "
                        f"{', '.join(active_source_names)}."
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

        if events and len(candidate_source_sets) >= max_decision_blend_options:
            print(f"Stopped after {max_decision_blend_options} feasible blend options.")

        if events and self.decision_point_results.empty:
            self.record_no_selected_blend(
                last_solver_result,
                events,
                period_crusher_target,
                initial_steady_state_duration,
                no_selected_blend_message,
                step_solver_config,
            )

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
            self.previous_selected_stockpile_source_ids = self.stockpile_source_ids_from_dataframe(
                filtered_decision_point_results_to_user_choice
            )
            self.update_grade_block_pair_memory(filtered_decision_point_results_to_user_choice)

            # Prepare for next cycle
            
            steady_state_start_time = self.current_time
            steady_state_duration = self.latest_result_duration(initial_steady_state_duration)
            steady_state_end_time = steady_state_start_time + timedelta(hours=steady_state_duration)
            
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
            self.previous_selected_stockpile_source_ids = self.stockpile_source_ids_from_dataframe(
                self.decision_point_results
            )
            self.update_grade_block_pair_memory(self.decision_point_results)

            # Prepare for next cycle (no results)
            
            steady_state_start_time = self.current_time
            steady_state_duration = self.latest_result_duration(initial_steady_state_duration)
            steady_state_end_time = steady_state_start_time + timedelta(hours=steady_state_duration)

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
            "start_datetime",
            "end_datetime",
            "steady_state_duration",
            "blend_option",
            "solver_score",
            "source",
            "estimated_delivery_datetime",
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
            print(self.group_decision_results_for_display(self.decision_point_results_to_display)[available_columns])

        blend_options = sorted(
            self.decision_point_results_to_display["blend_option"].dropna().unique()
        )
        option_count = len(blend_options)
        if self.user_interaction_mode == 1:
            print(f"Auto select mode: Blend option 1 will be selected from {option_count} feasible option(s). Higher solver score is better.")
        elif self.user_interaction_mode == 2:
            print("Manual mode: choose a blend option from the table. Higher solver score is better.")

    def configured_min_feed_duration_hours(self):
        try:
            value = float(self.solver_config.get("min_feed_duration_hours") or 0)
        except (TypeError, ValueError):
            return None
        return value if value > Optimizer.SOLUTION_TOLERANCE else None

    def configured_blend_option_timeout_seconds(self):
        try:
            value = float(self.solver_config.get("blend_option_timeout_seconds") or 0)
        except (TypeError, ValueError):
            return None
        return value if value > Optimizer.SOLUTION_TOLERANCE else None

    def configured_max_decision_blend_options(self):
        try:
            value = int(self.solver_config.get(
                "max_blend_options_per_steady_state",
                self.MAX_DECISION_BLEND_OPTIONS,
            ) or self.MAX_DECISION_BLEND_OPTIONS)
        except (TypeError, ValueError):
            value = self.MAX_DECISION_BLEND_OPTIONS
        return max(1, value)

    def solver_config_for_current_step(self):
        solver_config = dict(self.solver_config or {})
        solver_config["previous_blend_stockpile_source_ids"] = sorted(
            self.previous_selected_stockpile_source_ids
        )
        solver_config["previous_grade_block_pairs"] = {
            source: list(stockpiles)
            for source, stockpiles in self.previous_selected_grade_block_pairs.items()
        }
        solver_config["grade_block_pair_locks"] = {
            source: list(stockpiles)
            for source, stockpiles in self.grade_block_pair_locks.items()
        }
        return solver_config

    def configured_min_grade_block_pair_duration_hours(self):
        try:
            value = float(self.solver_config.get("min_grade_block_pair_duration_hours") or 0)
        except (TypeError, ValueError):
            return None
        return value if value > Optimizer.SOLUTION_TOLERANCE else None

    def grade_block_pair_duration_issues_from_result(self, result):
        required_duration = self.configured_min_grade_block_pair_duration_hours()
        if required_duration is None:
            return []

        try:
            result_duration = float(result.get("steady_state_duration") or 0)
        except (TypeError, ValueError):
            result_duration = 0.0

        if result_duration + Optimizer.SOLUTION_TOLERANCE < required_duration:
            return []

        source_details = {}
        for transaction in result.get("transactions", []):
            if transaction.get("source_type") != "grade_block":
                continue
            try:
                actual_tonnes = float(transaction.get("actual_tonnes") or 0)
            except (TypeError, ValueError):
                actual_tonnes = 0
            if actual_tonnes <= Optimizer.SOLUTION_TOLERANCE:
                continue

            source = str(transaction.get("source") or transaction.get("source_id") or "")
            if not source:
                continue
            details = source_details.setdefault(
                source,
                {
                    "actual_tonnes": 0.0,
                    "payload_tonnes": 0.0,
                    "payload_count": 0,
                    "delivered_datetimes": [],
                },
            )
            details["actual_tonnes"] += actual_tonnes
            try:
                opening_balance = float(transaction.get("opening_balance") or 0)
            except (TypeError, ValueError):
                opening_balance = 0
            details["payload_tonnes"] += max(opening_balance, 0.0)
            details["payload_count"] += 1
            delivered_datetime = pd.to_datetime(
                transaction.get("estimated_delivery_datetime"),
                errors="coerce",
            )
            if not pd.isna(delivered_datetime):
                details["delivered_datetimes"].append(delivered_datetime.to_pydatetime())

        issues = []
        steady_state_start = getattr(self, "current_time", None)
        for source, details in source_details.items():
            if steady_state_start is None:
                source_duration = result_duration
            else:
                source_duration = Optimizer.calculate_grouped_payload_depletion_duration(
                    details["delivered_datetimes"],
                    steady_state_start,
                )

            if source_duration is None:
                issues.append(
                    f"{source} has no valid payload delivery timestamp while paired in a "
                    f"{result_duration:.2f} hr steady state; minimum is {required_duration:.2f} hrs"
                )
                continue

            if source_duration + Optimizer.SOLUTION_TOLERANCE < required_duration:
                issues.append(
                    f"{source} delivery window is {source_duration:.2f} hrs from "
                    f"{details['payload_count']} payload row(s) "
                    f"({details['payload_tonnes']:.1f} t payload tonnes available, "
                    f"{details['actual_tonnes']:.1f} t selected) in a "
                    f"{result_duration:.2f} hr steady state; minimum is {required_duration:.2f} hrs"
                )
        return issues

    def grade_block_pair_lock_violations_from_result(self, result):
        if not self.solver_config.get("grade_block_lock_enabled", False):
            return []

        stockpile_ids = self.stockpile_source_ids_from_transactions(result.get("transactions", []))
        grade_block_sources = self.grade_block_sources_from_transactions(result.get("transactions", []))
        violations = []
        for source in grade_block_sources:
            locked_stockpiles = self.grade_block_pair_locks.get(source)
            if not locked_stockpiles:
                continue
            if tuple(sorted(stockpile_ids)) != tuple(locked_stockpiles):
                violations.append(
                    f"{source} is locked to {', '.join(locked_stockpiles)} "
                    f"but candidate uses {', '.join(sorted(stockpile_ids)) or 'no stockpile'}"
                )
        return violations

    def update_grade_block_pair_memory(self, data):
        pairs = self.grade_block_pair_signatures_from_dataframe(data)
        self.previous_selected_grade_block_pairs = pairs
        if not self.solver_config.get("grade_block_lock_enabled", False):
            return
        for source, stockpiles in pairs.items():
            if stockpiles and source not in self.grade_block_pair_locks:
                self.grade_block_pair_locks[source] = tuple(stockpiles)

    def required_min_feed_duration(self, available_window_duration):
        configured_duration = self.configured_min_feed_duration_hours()
        if configured_duration is None:
            return None
        try:
            available_window_duration = float(available_window_duration)
        except (TypeError, ValueError):
            return configured_duration
        if available_window_duration <= Optimizer.SOLUTION_TOLERANCE:
            return configured_duration
        return min(configured_duration, available_window_duration)

    def candidate_potential_feed_duration(self, result):
        """Estimate how long the selected stockpile blend could keep feeding from current balances."""
        stockpile_durations = []
        result_duration = float(result.get("steady_state_duration") or 0)
        for transaction in result.get("transactions", []):
            if transaction.get("source_type") != "stockpile":
                continue
            try:
                actual_tonnes = float(transaction.get("actual_tonnes") or 0)
                opening_balance = float(transaction.get("opening_balance") or 0)
                equipment_rate_output = float(transaction.get("equipment_rate_output") or 0)
            except (TypeError, ValueError):
                continue
            if actual_tonnes <= Optimizer.SOLUTION_TOLERANCE:
                continue
            if equipment_rate_output <= Optimizer.SOLUTION_TOLERANCE and result_duration > Optimizer.SOLUTION_TOLERANCE:
                equipment_rate_output = actual_tonnes / result_duration
            if opening_balance <= Optimizer.SOLUTION_TOLERANCE or equipment_rate_output <= Optimizer.SOLUTION_TOLERANCE:
                return 0
            stockpile_durations.append(opening_balance / equipment_rate_output)

        if stockpile_durations:
            return min(stockpile_durations)
        return result_duration

    def stockpile_source_ids_from_dataframe(self, data):
        if data is None or data.empty or "source_actual_tonnes" not in data.columns:
            return set()

        data = data.copy()
        data["source_actual_tonnes"] = pd.to_numeric(
            data["source_actual_tonnes"], errors="coerce"
        ).fillna(0)
        data = data[data["source_actual_tonnes"] > Optimizer.SOLUTION_TOLERANCE]
        if "source_type" in data.columns:
            data = data[data["source_type"] == "stockpile"]

        source_column = "source_id" if "source_id" in data.columns else "source"
        if source_column not in data.columns:
            return set()
        return set(str(value) for value in data[source_column].dropna() if str(value))

    def stockpile_source_ids_from_transactions(self, transactions):
        stockpile_ids = set()
        for transaction in transactions or []:
            if transaction.get("source_type") != "stockpile":
                continue
            try:
                actual_tonnes = float(transaction.get("actual_tonnes") or 0)
            except (TypeError, ValueError):
                actual_tonnes = 0
            if actual_tonnes <= Optimizer.SOLUTION_TOLERANCE:
                continue
            source_id = transaction.get("source_id") or transaction.get("source")
            if source_id:
                stockpile_ids.add(str(source_id))
        return stockpile_ids

    def grade_block_sources_from_transactions(self, transactions):
        grade_block_sources = set()
        for transaction in transactions or []:
            if transaction.get("source_type") != "grade_block":
                continue
            try:
                actual_tonnes = float(transaction.get("actual_tonnes") or 0)
            except (TypeError, ValueError):
                actual_tonnes = 0
            if actual_tonnes <= Optimizer.SOLUTION_TOLERANCE:
                continue
            source = transaction.get("source") or transaction.get("source_id")
            if source:
                grade_block_sources.add(str(source))
        return grade_block_sources

    def grade_block_pair_signatures_from_dataframe(self, data):
        if data is None or data.empty or "source_actual_tonnes" not in data.columns:
            return {}

        data = data.copy()
        data["source_actual_tonnes"] = pd.to_numeric(
            data["source_actual_tonnes"], errors="coerce"
        ).fillna(0)
        data = data[data["source_actual_tonnes"] > Optimizer.SOLUTION_TOLERANCE]
        if data.empty or "source_type" not in data.columns:
            return {}

        stockpile_ids = self.stockpile_source_ids_from_dataframe(data)
        if not stockpile_ids:
            return {}

        grade_block_data = data[data["source_type"] == "grade_block"]
        if grade_block_data.empty or "source" not in grade_block_data.columns:
            return {}

        return {
            str(source): tuple(sorted(stockpile_ids))
            for source in grade_block_data["source"].dropna().unique()
            if str(source)
        }

    def active_source_ids_from_result(self, result):
        """Return source labels used for distinct-option exclusion."""
        active_source_ids = []
        for transaction in result.get("transactions", []):
            try:
                actual_tonnes = float(transaction.get("actual_tonnes") or 0)
            except (TypeError, ValueError):
                actual_tonnes = 0
            if actual_tonnes > Optimizer.SOLUTION_TOLERANCE:
                if transaction.get("source_type") == "grade_block":
                    active_source_ids.append(transaction.get("source") or transaction.get("source_id"))
                else:
                    active_source_ids.append(transaction.get("source_id") or transaction.get("source"))
        return sorted(source for source in set(active_source_ids) if source)

    def active_source_names_from_result(self, result):
        """Return display source names with positive tonnes in an optimisation result."""
        active_sources = []
        for transaction in result.get("transactions", []):
            try:
                actual_tonnes = float(transaction.get("actual_tonnes") or 0)
            except (TypeError, ValueError):
                actual_tonnes = 0
            if actual_tonnes > Optimizer.SOLUTION_TOLERANCE:
                active_sources.append(transaction.get("source") or transaction.get("source_id"))
        return sorted(source for source in set(active_sources) if source)

    def group_decision_results_for_display(self, data: pd.DataFrame):
        return self.group_grade_block_rows(data)

    def group_grade_block_rows(self, data: pd.DataFrame):
        """Group selected grade-block payload rows by readable grade-block source."""
        if data is None or data.empty or "source_type" not in data.columns or "source" not in data.columns:
            return data

        data = data.copy()
        grade_block_rows = data[data["source_type"] == "grade_block"].copy()
        other_rows = data[data["source_type"] != "grade_block"].copy()
        if grade_block_rows.empty:
            return data

        for column in [
            "source_actual_tonnes",
            "crusher_actual_tonnes",
            "source_opening_balance",
            "source_closing_balance",
            "equipment_rate_output",
            "crusher_rate_output",
            "source_blend_ratio",
        ]:
            if column in grade_block_rows.columns:
                grade_block_rows[column] = pd.to_numeric(grade_block_rows[column], errors="coerce").fillna(0)

        group_keys = [
            column for column in [
                "start_datetime",
                "end_datetime",
                "steady_state_number",
                "blend_option",
                "blend_ID",
                "solver_score",
                "steady_state_duration",
                "period",
                "source",
                "source_type",
                "equipment",
            ]
            if column in grade_block_rows.columns
        ]

        grouped_records = []
        for _, group in grade_block_rows.groupby(group_keys, dropna=False, sort=False):
            record = group.iloc[0].copy()
            actual_tonnes = group["source_actual_tonnes"].sum()
            crusher_actual_tonnes = group["crusher_actual_tonnes"].iloc[0] if "crusher_actual_tonnes" in group else 0
            record["source_actual_tonnes"] = actual_tonnes
            if "source_blend_ratio" in group:
                record["source_blend_ratio"] = (
                    actual_tonnes / crusher_actual_tonnes
                    if crusher_actual_tonnes not in (0, None)
                    else group["source_blend_ratio"].sum()
                )
            if "source_opening_balance" in group:
                record["source_opening_balance"] = group["source_opening_balance"].sum()
            if "source_closing_balance" in group:
                record["source_closing_balance"] = group["source_closing_balance"].sum()
            if "equipment_rate_output" in group:
                record["equipment_rate_output"] = group["equipment_rate_output"].sum()
            if "source_id" in group:
                record["source_id"] = ", ".join(str(value) for value in group["source_id"].dropna().unique())
            if "estimated_delivery_datetime" in group:
                delivered_datetimes = pd.to_datetime(
                    group["estimated_delivery_datetime"],
                    errors="coerce",
                ).dropna().drop_duplicates().sort_values()
                if len(delivered_datetimes) == 1:
                    record["estimated_delivery_datetime"] = delivered_datetimes.iloc[0].strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                elif len(delivered_datetimes) > 1:
                    record["estimated_delivery_datetime"] = (
                        f"from {delivered_datetimes.iloc[0]:%Y-%m-%d %H:%M:%S} "
                        f"to {delivered_datetimes.iloc[-1]:%Y-%m-%d %H:%M:%S}"
                    )
                else:
                    record["estimated_delivery_datetime"] = ""

            for grade_column in [
                "source_grade_fe",
                "source_grade_si",
                "source_grade_al",
                "source_grade_p",
                "source_grade_mn",
            ]:
                if grade_column in group:
                    grades = pd.to_numeric(group[grade_column], errors="coerce")
                    if actual_tonnes > Optimizer.SOLUTION_TOLERANCE:
                        record[grade_column] = (
                            grades * group["source_actual_tonnes"]
                        ).sum() / actual_tonnes

            grouped_records.append(record)

        grouped_rows = pd.DataFrame(grouped_records)
        grouped_data = pd.concat([other_rows, grouped_rows], ignore_index=True)
        sort_columns = [
            column for column in ["steady_state_number", "blend_option", "source_type", "source"]
            if column in grouped_data.columns
        ]
        if sort_columns:
            grouped_data = grouped_data.sort_values(sort_columns, kind="stable").reset_index(drop=True)
        return grouped_data

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

    def record_no_selected_blend(
        self,
        result,
        events,
        period_crusher_target,
        steady_state_duration,
        message,
        solver_config,
    ):
        """Record a no-blend row when all solver candidates were rejected before reporting."""
        if result is None:
            result = {
                "steady_state_duration": steady_state_duration,
                "diagnostics": Optimizer.build_diagnostics(
                    events,
                    period_crusher_target,
                    steady_state_duration,
                    self.periods,
                    self.period_tracker,
                    self.min_stockpiles,
                    self.max_stockpiles,
                    self.min_stockpile_contribution_ratio,
                    [],
                    message,
                    0,
                    solver_config,
                ),
            }
        else:
            result = dict(result)
            result.setdefault("steady_state_duration", steady_state_duration)
            diagnostics = dict(result.get("diagnostics") or {})
            diagnostics["solver_status"] = message
            likely_causes = list(diagnostics.get("likely_causes") or [])
            if message not in likely_causes:
                likely_causes.append(message)
            diagnostics["likely_causes"] = likely_causes
            result["diagnostics"] = diagnostics

        result["Linprog_result_object"] = SimpleNamespace(
            success=False,
            status=message,
            status_code=getattr(result.get("Linprog_result_object"), "status_code", None),
        )
        self.register_optimization_diagnostic(result, events, message)

        stored_blend_option = self.blend_option
        self.blend_option = "No blend found"
        self.record_results(result)
        self.blend_option = stored_blend_option

    def latest_result_duration(self, default_duration=None):
        if (
            self.results is not None
            and not self.results.empty
            and "steady_state_duration" in self.results.columns
        ):
            durations = pd.to_numeric(
                self.results["steady_state_duration"],
                errors="coerce",
            ).dropna()
            if not durations.empty:
                return max(float(durations.iloc[-1]), 0.0)

        if default_duration is None:
            default_duration = self.calculate_initial_steady_state_duration()
        try:
            return max(float(default_duration or 0), 0.0)
        except (TypeError, ValueError):
            return 0.0

    def advance_time(self):
        """Advance current time and update period if needed."""
        steady_state_duration = self.latest_result_duration()
        if steady_state_duration <= Optimizer.SOLUTION_TOLERANCE:
            raise ValueError("Steady state duration is zero; cannot advance optimisation time.")
        self.current_time += timedelta(hours=steady_state_duration)

        # Switch periods if needed
        if self.current_time >= self.periods.get_periods()["preplan_end"] and self.period_tracker == "preplan":
            self.period_tracker = "period_1"
        elif self.current_time >= self.periods.get_periods()["period_1_end"] and self.period_tracker == "period_1":
            self.period_tracker = "period_2"

    def record_results(self, result):
        """Record results from an optimization run into the main DataFrame."""
        if not result['Linprog_result_object'].success:
            failed_blend_label = (
                self.blend_option
                if isinstance(self.blend_option, str)
                else "No blend selected"
            )
            report_data = [
                {
                    "start_datetime": self.current_time,
                    "end_datetime": self.current_time + timedelta(hours=result["steady_state_duration"]),
                    "steady_state_number": self.steady_state_tracker,
                    "blend_option": failed_blend_label,
                    "blend_ID": "No blend selected",
                    "solver_score": result.get("solver_score", ""),
                    "steady_state_duration": result["steady_state_duration"],
                    "period": self.period_tracker,
                    "actual_direct_tip_ratio": 0,
                    "source": "",
                    "source_id": "",
                    "source_type": "",
                    "estimated_delivery_datetime": "",
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
            crusher_actual_tonnes = float(result.get("crusher_actual_tonnes") or 0)
            direct_tip_tonnes = sum(
                float(transaction.get("actual_tonnes") or 0)
                for transaction in result["transactions"]
                if transaction.get("source_type") == "grade_block"
            )
            actual_direct_tip_ratio = (
                direct_tip_tonnes / crusher_actual_tonnes
                if crusher_actual_tonnes > Optimizer.SOLUTION_TOLERANCE
                else 0
            )
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
                    "actual_direct_tip_ratio": actual_direct_tip_ratio,
                    "source": transaction["source"],
                    "source_id": transaction.get("source_id", transaction["source"]),
                    "source_type": transaction.get("source_type", ""),
                    "estimated_delivery_datetime": transaction.get("estimated_delivery_datetime", ""),
                    "source_blend_ratio": round(transaction["equipment_rate_output"] / result["crusher_rate_output"], 2) if result["crusher_rate_output"] != 0 else 0,
                    "source_opening_balance": self.total_AMT_stockpile_balances[transaction["source_id"]] if transaction.get("source_id") in self.total_AMT_stockpile_balances else transaction["opening_balance"],
                    "source_actual_tonnes": transaction["actual_tonnes"],
                    "source_closing_balance": (self.total_AMT_stockpile_balances[transaction["source_id"]] if transaction.get("source_id") in self.total_AMT_stockpile_balances else transaction["opening_balance"]) - transaction["actual_tonnes"],
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

        report_results = self.group_grade_block_rows(self.results)
        self.database_manager.write_optimised_blend_report_to_database(report_results, self.periods)

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
