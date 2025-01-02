# This tracks source balances and the program runs and provides input to the EventPool for the update functionality
import pandas as pd
from classes.StockpileData import StockpileData
from classes.GradeBlockData import GradeBlockData
from typing import List
from pandas import DataFrame
class BalanceTracker:
    def __init__(self, stockpiles: List[StockpileData], grade_blocks: List[GradeBlockData], period_tracker):
        self.state = {item.name: item.to_dict().get(f"state_{period_tracker}", 0) for item in stockpiles}
        self.balance = {item.name: item.balance for item in stockpiles + grade_blocks}
        self.grade_fe = {item.name: item.grade_fe for item in stockpiles + grade_blocks}
        self.grade_si = {item.name: item.grade_si for item in stockpiles + grade_blocks}
        self.grade_al = {item.name: item.grade_al for item in stockpiles + grade_blocks}
        self.grade_p = {item.name: item.grade_p for item in stockpiles + grade_blocks}
        self.grade_mn = {item.name: item.grade_mn for item in stockpiles + grade_blocks}
        
    def update_balances(self, filtered_decision_point_results_to_user_choice: DataFrame, expit_payload_transactions: DataFrame, steady_state_start_time, steady_state_end_time, steady_state_tracker):
        """Update balance and grades after each optimization step."""
        
        # Loop through user choice of decision point results and deplete balances
        for _, transaction in filtered_decision_point_results_to_user_choice.iterrows():
            name = transaction["source"]
            
            if self.balance[name] != 0:
                self.balance[name] -= transaction["source_actual_tonnes"]
    
    def get_balance(self, name):
        """Retrieve the current balance for a stockpile or grade block."""
        return self.balance.get(name, 0)
    
