# This is where the main workflow is defined (everything happens here)
from classes.BalanceTracker import BalanceTracker
from classes.EventPool import EventPool
from classes.Optimizer import Optimizer
from classes.CrusherTarget import CrusherTarget
import pandas as pd
from datetime import timedelta

class CaseModeller:
    def __init__(self, stockpiles, grade_blocks, equipment, crusher_targets, periods):
        self.stockpiles = stockpiles
        self.grade_blocks = grade_blocks
        self.equipment = equipment
        self.crusher_targets = crusher_targets
        self.periods = periods
        self.current_time = periods["preplan_start"]
        self.current_time_for_export = periods["preplan_start"]
        self.period_tracker = "preplan"
        self.balance_tracker = BalanceTracker(stockpiles, grade_blocks)
        self.event_pool = EventPool(stockpiles, grade_blocks, equipment)
        self.optimizer = Optimizer()
        self.results = pd.DataFrame()
        self.steady_state_tracker = 0
        self.blend_option = 1
        self.user_blend_choice = None
        self.decision_point_results = None
        self.decision_point_results_to_display = None
        self.decision_point_results_to_display_filtered_to_current_blend_choice = None
        self.user_interaction_mode = None


    def run(self):
        """Runs the modeling process, coordinating optimization and time tracking."""
        
        while self.current_time < self.periods["period_2_end"]:
            # Run optimization and only advance time if successful
            self.run_optimization_step()
            self.steady_state_tracker += 1

        # Save results to an Excel file at the end
        self.save_results(fr"C:\BlendMaster\blendmaster_backend_OOP\output\report_data_{self.current_time_for_export.date()}_{self.current_time_for_export.strftime('%H-%M')}.xlsx")

    def run_optimization_step(self):
        """Run a single optimization step for the initial steady state duration."""
        events = self.event_pool.get_events(self.period_tracker, self.decision_point_results)
        self.event_pool.update_event_balances(events, self.balance_tracker)
        period_crusher_target = CrusherTarget(self.crusher_targets).get_targets(self.period_tracker)

        while events:
            events_len = len(events)
            # Run optimization with dynamic steady states
            result = self.optimizer.run_with_dynamic_steady_state(events, period_crusher_target, self.calculate_initial_steady_state_duration(), self.periods, self.period_tracker)

            if not result['Linprog_result_object'].success:
                store_blend_option = self.blend_option
                self.blend_option = "No blend found"
                self.record_results(result)
                self.blend_option = store_blend_option
                events = self.event_pool.get_events(self.period_tracker, self.decision_point_results)
                updated_events_len = len(events)
                if updated_events_len == events_len: break
                else: self.blend_option += 1
                continue
        
            elif result['Linprog_result_object'].success:
                if result['crusher_actual_tonnes'] > 0:
                    self.record_results(result)
                    events = self.event_pool.get_events(self.period_tracker, self.decision_point_results)
                    self.event_pool.update_event_balances(events, self.balance_tracker)
                    updated_events_len = len(events)
                    if updated_events_len == events_len: break
                    else: self.blend_option += 1
                    continue
                else:
                    store_blend_option = self.blend_option
                    self.blend_option = "Exclude"
                    self.record_results(result)
                    self.blend_option = store_blend_option
                    events = self.event_pool.get_events(self.period_tracker, self.decision_point_results)
                    updated_events_len = len(events)
                    if updated_events_len == events_len: break
                    else: self.blend_option += 1
                    continue
            else:
                store_blend_option = self.blend_option
                self.blend_option = "Rare case"
                self.record_results(result)
                self.blend_option = store_blend_option
                events = self.event_pool.get_events(self.period_tracker, self.decision_point_results)
                updated_events_len = len(events)
                if updated_events_len == events_len: break
                else: self.blend_option += 1
                continue

        
        # Manage user interaction
        # Ensure numeric columns for comparison
        self.decision_point_results["source_actual_tonnes"] = pd.to_numeric(
            self.decision_point_results["source_actual_tonnes"], errors='coerce'
        )
        self.decision_point_results["crusher_actual_tonnes"] = pd.to_numeric(
            self.decision_point_results["crusher_actual_tonnes"], errors='coerce'
        )
        
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
        
        if self.user_interaction_mode == 1:
            
            self.user_blend_choice = 1

        elif self.user_interaction_mode == 2 and self.steady_state_tracker != 0:
            
            # max_blend_option = self.decision_point_results_to_display["blend_option"].max()
 
           # if max_blend_option > 1:

            # Compare the sources for a blend option between two iteration and avoid user interaction if no change
            current_filtered_sources =  self.decision_point_results_to_display_filtered_to_current_blend_choice["source"]
            previous_filtered_sources = self.results[
                (self.results["blend_option"] == self.user_blend_choice) &
                (self.results["steady_state_number"] == self.steady_state_tracker - 1)
            ]["source"]
                
            if not list(current_filtered_sources) == list(previous_filtered_sources):
                print(self.decision_point_results_to_display[["steady_state_number", "blend_option", "source", "source_blend_ratio"]])
                print("\033[92mBlend fully depleted.\033[0m")
                self.user_blend_choice = input("\033[95mChoose new blend: \033[0m")
                # Cast user choice to appropriate type
                try:
                    self.user_blend_choice = int(self.user_blend_choice)
                except ValueError:
                    print("Invalid input. Please enter a number.")
                    return
            else:
                pass
        
           # else: self.user_blend_choice = max_blend_option


        elif self.user_interaction_mode == 2 and self.steady_state_tracker == 0:
            print(self.decision_point_results_to_display[["steady_state_number", "blend_option", "source", "source_blend_ratio"]])
            self.user_blend_choice = input("\033[95mChoose blend: \033[0m")
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
        self.decision_point_results = None

        # Reset for next cycle
        self.decision_point_results = None
        self.balance_tracker.update_balances(filtered_decision_point_results_to_user_choice)
        self.advance_time()
        self.blend_option = 1
    
    def advance_time(self):
        """Advance current time and update period if needed."""
        steady_state_duration = float(self.results.iloc[-1]["steady_state_duration"])
        self.current_time += timedelta(hours=steady_state_duration)

        # Switch periods if needed
        if self.current_time >= self.periods["preplan_end"] and self.period_tracker == "preplan":
            self.period_tracker = "period_1"
        elif self.current_time >= self.periods["period_1_end"] and self.period_tracker == "period_1":
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
                    "steady_state_duration": result["steady_state_duration"],
                    "period": self.period_tracker,
                    "source": "",
                    "source_blend_ratio": "No tonnes selected",
                    "source_opening_balance": "",
                    "source_actual_tonnes": "No tonnes selected",
                    "source_closing_balance": "",
                    "source_grade_fe": "",
                    "equipment": "",
                    "equipment_rate_input": "",
                    "equipment_rate_output": "",
                    "crusher_actual_tonnes": "No crusher feed in steady state",
                    "crusher_rate_input": "",
                    "crusher_rate_output": "",
                    "crusher_actual_grade_fe": "",
                    "crusher_grade_target_min_fe": "",
                    "crusher_grade_target_max_fe": ""
                }
            ]
        else:
            report_data = [
                {
                    "start_datetime": self.current_time,
                    "end_datetime": self.current_time + timedelta(hours=result["steady_state_duration"]),
                    "steady_state_number": self.steady_state_tracker,
                    "blend_option": self.blend_option,
                    "steady_state_duration": result["steady_state_duration"],
                    "period": self.period_tracker,
                    "source": transaction["source"],
                    "source_blend_ratio": round(transaction["equipment_rate_output"] / result["crusher_rate_output"], 2) if result["crusher_rate_output"] != 0 else 0,
                    "source_opening_balance": transaction["opening_balance"],
                    "source_actual_tonnes": transaction["actual_tonnes"],
                    "source_closing_balance": transaction["opening_balance"] - transaction["actual_tonnes"],
                    "source_grade_fe": transaction["grade_fe"],
                    "equipment": transaction["equipment"],
                    "equipment_rate_input": transaction["equipment_rate_input"],
                    "equipment_rate_output": transaction["equipment_rate_output"],
                    "crusher_actual_tonnes": result["crusher_actual_tonnes"],
                    "crusher_rate_input": result["crusher_rate_input"],
                    "crusher_rate_output": result["crusher_rate_output"],
                    "crusher_actual_grade_fe": result["crusher_actual_grade_fe"],
                    "crusher_grade_target_min_fe": result["crusher_grade_target_min_fe"],
                    "crusher_grade_target_max_fe": result["crusher_grade_target_max_fe"]
                }
                for transaction in result["transactions"]
            ]

        self.decision_point_results = pd.concat([self.decision_point_results, pd.DataFrame(report_data)], ignore_index=True)

    def append_results(self, filtered_decision_point_results_to_user_choice):
        """Append filtered results to the main DataFrame."""
        self.results = pd.concat([self.results, pd.DataFrame(filtered_decision_point_results_to_user_choice)], ignore_index=True)
    
    def save_results(self, filename):
        """Save results to an Excel file."""
        # Ensure numeric columns for comparison
        self.results["source_actual_tonnes"] = pd.to_numeric(
            self.results["source_actual_tonnes"], errors='coerce'
        )
        self.results["crusher_actual_tonnes"] = pd.to_numeric(
            self.results["crusher_actual_tonnes"], errors='coerce'
        )
       
        self.results = self.results.loc[
            (
                (self.results["source_actual_tonnes"] != 0) & 
                (self.results["crusher_actual_tonnes"] != 0)
            ) | 
            (
                (self.results["source_actual_tonnes"] == 0) & 
                (self.results["crusher_actual_tonnes"] == 0)
            )
        ]
        self.results.to_excel(filename, index=False)
        print(f"All results written to {filename}")
       
    def calculate_initial_steady_state_duration(self):
        """Calculate initial steady state duration based on the current time and periods."""
        if self.current_time >= self.periods["preplan_start"] and self.current_time < self.periods["preplan_end"]: 
            return (self.periods["preplan_end"] - self.current_time).total_seconds() / 3600
        elif self.current_time >= self.periods["period_1_start"] and self.current_time < self.periods["period_1_end"]:
            return (self.periods["period_1_end"] - self.current_time).total_seconds() / 3600
        elif self.current_time >= self.periods["period_2_start"] and self.current_time < self.periods["period_2_end"]:
            return (self.periods["period_2_end"] - self.current_time).total_seconds() / 3600
        else:
            return 0
