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
        self.period_tracker = "preplan"
        self.balance_tracker = BalanceTracker(stockpiles, grade_blocks)
        self.event_pool = EventPool(stockpiles, grade_blocks, equipment)
        self.optimizer = Optimizer()
        self.results = pd.DataFrame()

    def run(self):
        """Runs the modeling process, coordinating optimization and time advancement."""
        while self.current_time < self.periods["period_2_end"]:
            # Run optimization and only advance time if successful
            if self.run_optimization_step():
                self.advance_time()
            else:
                print("Optimization failed; exiting loop.")
                break

        # Save results to an Excel file at the end
        self.save_results(r"C:\BlendMaster\blendmaster_backend_OOP\output\outcome_data.xlsx")

    def run_optimization_step(self):
        """Run a single optimization step for the initial steady state duration."""
        events = self.event_pool.get_events(self.period_tracker)
        self.event_pool.update_event_balances(events, self.balance_tracker)
        period_crusher_target = CrusherTarget(self.crusher_targets).get_targets(self.period_tracker)

        # Run optimization with dynamic steady states
        result = self.optimizer.run_with_dynamic_steady_state(
            events, period_crusher_target, self.calculate_initial_steady_state_duration()
        )

        if result["status"] != "success":
            print("Optimization failed; exiting loop.")
            return

        self.record_results(result)
        self.balance_tracker.update_balances(result["outcome"])

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
        outcome_data = [
            {
                "start_datetime": self.current_time,
                "end_datetime": self.current_time + timedelta(hours=result["steady state duration"]),
                "steady_state_duration": result["steady state duration"],
                "period": self.period_tracker,
                "source": event["Source"],
                "opening_balance": event["Opening Balance"],
                "actual_tonnes": event["Actual Tonnes (Reclaimed)"],
                "remaining_tonnes": event["remaining_tonnes"],
                "grade_fe": event["Grade Fe"],
                "equipment": event["Equipment"],
                "equipment_rate_input": event["Equipment Rate (Input)"],
                "equipment_rate_output": event["Equipment Actual Rate"]
            }
            for event in result["outcome"]
        ]
        self.results = pd.concat([self.results, pd.DataFrame(outcome_data)], ignore_index=True)

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
                return  (self.periods["period_2_end"] - self.current_time).total_seconds() / 3600
            else: return 0
