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
        self.build_report = [] # Store transactions that meet the condition
        
    def update_balances(self, filtered_decision_point_results_to_user_choice: DataFrame, expit_payload_transactions: DataFrame, steady_state_start_time, steady_state_end_time, steady_state_tracker):
        """Update balance and grades after each optimisation step."""
        
        # Loop through user choice of decision point results and deplete balances
        for _, transaction in filtered_decision_point_results_to_user_choice.iterrows():
            name = transaction["source"]
            
            if self.balance[name] != 0:
                self.balance[name] -= transaction["source_actual_tonnes"]
       
        if not expit_payload_transactions.empty:

            # Sort the DataFrame by delivered_datetime (old to new)
            expit_payload_transactions = expit_payload_transactions.sort_values(by=["destination", "delivered_datetime"])

            # Loop through expit payload transactions and build stockpiles
            for _, transaction in expit_payload_transactions.iterrows():
                name = transaction["destination"].replace("Stockpiles/", "")
                delivered_datetime = transaction["delivered_datetime"]
                payload = transaction["payload"]
                agent = transaction["agent"]
                source = transaction["source"]
                mining_start_datetime = transaction["start_datetime"]

                # Check if the transaction is within the time range
                if steady_state_start_time <= delivered_datetime < steady_state_end_time:
                    if (name in self.state) and ((self.state[name] == "Build") or (self.state[name] == "Auto")):
                        # Perform weighted averaging for each grade
                        current_balance = self.balance[name]
                        updated_balance = current_balance + payload
                        
                        self.grade_fe[name] = (
                            (self.grade_fe[name] * current_balance + transaction["source_grade_fe"] * payload)
                            / updated_balance
                        )
                        self.grade_si[name] = (
                            (self.grade_si[name] * current_balance + transaction["source_grade_si"] * payload)
                            / updated_balance
                        )
                        self.grade_al[name] = (
                            (self.grade_al[name] * current_balance + transaction["source_grade_al"] * payload)
                            / updated_balance
                        )
                        self.grade_p[name] = (
                            (self.grade_p[name] * current_balance + transaction["source_grade_p"] * payload)
                            / updated_balance
                        )
                        self.grade_mn[name] = (
                            (self.grade_mn[name] * current_balance + transaction["source_grade_mn"] * payload)
                            / updated_balance
                        )
                        
                        # Update the balance
                        self.balance[name] = updated_balance
                        
                        # Add the used transaction to the tracked list
                        self.build_report.append({
                            "steady_state_number": steady_state_tracker,
                            "steady_state_start_datetime": steady_state_start_time,
                            "steady_state_end_datetime": steady_state_end_time,
                            "agent": agent,
                            "mining_start_datetime": mining_start_datetime,
                            "source": source,
                            "stockpile": name,
                            "payload": payload,
                            "delivered_datetime": delivered_datetime,
                            "closing_balance": updated_balance,
                            "grade_fe": self.grade_fe[name],
                            "grade_si": self.grade_si[name],
                            "grade_al": self.grade_al[name],
                            "grade_p": self.grade_p[name],
                            "grade_mn": self.grade_mn[name]
                            
                        })
                    
                    elif (name in self.state) and ((self.state[name] == "Reclaim") or (self.state[name] == "Off")):
                        continue

                    else: 
                        raise ValueError(f"Stockpile '{name}' is not selected or found in Stockpile Inventories. Either select the stockpile by ticking the 'Use' option or remove the transactions to this destination from APS output.")
                     
    def get_build_transactions(self):
        """Retrieve the list of build transactions."""
        return pd.DataFrame(self.build_report)
    
    def get_balance(self, name):
        """Retrieve the current balance for a stockpile or grade block."""
        return self.balance.get(name, 0)
    
