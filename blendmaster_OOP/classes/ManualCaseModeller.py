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
from typing import List
import sqlite3


class ManualCaseModeller:
    def __init__(self, stockpiles: List[StockpileData], grade_blocks: List[GradeBlockData], equipment: List[EquipmentData], expit_payload_transactions, periods: PeriodManager):
        self.stockpiles = stockpiles
        self.grade_blocks = grade_blocks
        self.equipment = equipment
        self.periods = periods
        self.current_time = periods.get_periods()["preplan_start"]
        self.start_time = periods.get_periods()["preplan_start"]
        self.period_tracker = "preplan"
        self.balance_tracker = BalanceTracker(stockpiles, grade_blocks, self.period_tracker)
        self.expit_payload_transactions = expit_payload_transactions
        self.event_pool = EventPoolGenerator(stockpiles, grade_blocks, equipment)
        self.results = pd.DataFrame()
        self.build_report = pd.DataFrame()
        self.steady_state_tracker = 0
        self.blend_option = 1
        self.user_blend_choice = None
        self.blend_ID = 2
        self.decision_point_results = pd.DataFrame()
        self.decision_point_results_to_display = pd.DataFrame()
        self.decision_point_results_to_display_filtered_to_current_blend_choice = pd.DataFrame()
        self.database_manager = DatabaseManager()
        self.manual_blend_dash_Input = pd.DataFrame()

    def run(self):
        """Runs the modeling process, coordinating optimization and time tracking."""
        self.manual_blend_dash_Input = self.fetch_data()
        
        while self.current_time < self.periods.get_periods()["period_2_end"]:
            # Run step and advance time
            self.run_manual_step()
            self.steady_state_tracker += 1

    def run_manual_step(self):
        """Run step until a blend becomes invalid (one of its stockpiles runs out)."""


        #Get blend_ID number 1
        #Check if all stockpiles are available
        #Yes: deplete until becomes invalid. Store duration, tonnes and grades. Advance time.
        #No: Go the blend_ID number 2 and so on 
        #Stop when end of period_2 is reached
        #Store results in the database

        self.append_results(self.decision_point_results)

        # Prepare for next cycle (no results)
        
        steady_state_start_time = self.current_time
        steady_state_end_time = steady_state_start_time + timedelta(hours=float(self.results.iloc[-1]["steady_state_duration"]))

        self.balance_tracker.update_balances(self.decision_point_results, 
                                            self.expit_payload_transactions,
                                            steady_state_start_time,
                                            steady_state_end_time,
                                            self.steady_state_tracker
                                            )
        self.advance_time()
        
        self.decision_point_results = pd.DataFrame()
        self.blend_option = 1
    
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
                    "steady_state_duration": result["steady_state_duration"],
                    "period": self.period_tracker,
                    "source": transaction["source"],
                    "source_blend_ratio": round(transaction["equipment_rate_output"] / result["crusher_rate_output"], 2) if result["crusher_rate_output"] != 0 else 0,
                    "source_opening_balance": transaction["opening_balance"],
                    "source_actual_tonnes": transaction["actual_tonnes"],
                    "source_closing_balance": transaction["opening_balance"] - transaction["actual_tonnes"],
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
                    "crusher_actual_grade_fe": result["crusher_actual_grade_fe"],
                    "crusher_actual_grade_si": result["crusher_actual_grade_si"],
                    "crusher_actual_grade_al": result["crusher_actual_grade_al"],
                    "crusher_actual_grade_p": result["crusher_actual_grade_p"],
                    "crusher_actual_grade_mn": result["crusher_actual_grade_mn"],
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
        
    def fetch_data(self):
        """Fetch data for the Gantt chart."""
        conn = sqlite3.connect(self.database)
        df = pd.read_sql("SELECT * FROM manual_blend_dash_Input", conn)
        conn.close()

        if df.empty:
            # Default data if table is empty
            df = pd.DataFrame({
                "blend_ID": list(range(1, 8)),
                "start_datetime": [self.default_start_datetime] * 7,
                "end_datetime": [self.default_end_datetime] * 7,
                "lane": list(range(1, 8)),
                "Legend": ["Blend"] * 7,
            })
        return df