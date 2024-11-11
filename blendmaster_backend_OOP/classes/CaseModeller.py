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
        events = self.event_pool.get_events(self.period_tracker)
        self.event_pool.update_event_balances(events, self.balance_tracker)
        period_crusher_target = CrusherTarget(self.crusher_targets).get_targets(self.period_tracker)

        # Run optimization with dynamic steady states
        result = self.optimizer.run_with_dynamic_steady_state(
            events, period_crusher_target, self.calculate_initial_steady_state_duration(), self.periods, self.period_tracker
        )

        if not result['Linprog_result_object'].success:
            self.record_results(result)
            self.balance_tracker.update_balances(result)
            self.advance_time()
        
        elif result['Linprog_result_object'].success:
            self.record_results(result)
            self.balance_tracker.update_balances(result)
            self.advance_time()

        else: 
            print("Optimization failed; exiting loop.")
            return
        
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
                "steady_state_duration": result["steady_state_duration"],
                "period": self.period_tracker,
                "source": "",
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
        
        else: report_data = [
            {
                "start_datetime": self.current_time,
                "end_datetime": self.current_time + timedelta(hours=result["steady_state_duration"]),
                "steady_state_number": self.steady_state_tracker,
                "steady_state_duration": result["steady_state_duration"],
                "period": self.period_tracker,
                "source": transaction["source"],
                "source_opening_balance": transaction["opening_balance"],
                "source_actual_tonnes": transaction["actual_tonnes"] if transaction["actual_tonnes"] != 0 else "No tonnes selected",
                "source_closing_balance": transaction["opening_balance"] - transaction["actual_tonnes"],
                "source_grade_fe": transaction["grade_fe"],
                "equipment": transaction["equipment"],
                "equipment_rate_input": transaction["equipment_rate_input"],
                "equipment_rate_output": transaction["equipment_rate_output"],
                "crusher_actual_tonnes": result["crusher_actual_tonnes"] if result["crusher_actual_tonnes"] != 0 else "No crusher feed in steady state",
                "crusher_rate_input": result["crusher_rate_input"],
                "crusher_rate_output": result["crusher_rate_output"],
                "crusher_actual_grade_fe": result["crusher_actual_grade_fe"],
                "crusher_grade_target_min_fe": result["crusher_grade_target_min_fe"],
                "crusher_grade_target_max_fe": result["crusher_grade_target_max_fe"]
            }
            for transaction in result["transactions"]
            if (
            (transaction["actual_tonnes"] != 0 and result["crusher_actual_tonnes"] != 0)
            or (transaction["actual_tonnes"] == 0 and result["crusher_actual_tonnes"] == 0)
            )
        ]
        self.results = pd.concat([self.results, pd.DataFrame(report_data)], ignore_index=True)

    def save_results(self, filename):
        """Save results to an Excel file."""
        self.results.to_excel(filename, index=False)
        print(f"All results written to {filename}")
       
    def calculate_initial_steady_state_duration(self):
            
            if self.current_time >= self.periods["preplan_start"] and self.current_time < self.periods["preplan_end"]: 
                return (self.periods["preplan_end"] - self.current_time).total_seconds() / 3600
            elif self.current_time >= self.periods["period_1_start"] and self.current_time < self.periods["period_1_end"]:
                return (self.periods["period_1_end"] - self.current_time).total_seconds() / 3600
            elif self.current_time >= self.periods["period_2_start"] and self.current_time < self.periods["period_2_end"]:
                return (self.periods["period_2_end"] - self.current_time).total_seconds() / 3600
            else: return 0
