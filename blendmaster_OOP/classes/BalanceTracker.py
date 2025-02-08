# This tracks source balances and the program runs and provides input to the EventPool for the update functionality
import pandas as pd
import copy
from classes.StockpileData import StockpileData
from classes.GradeBlockData import GradeBlockData
from typing import List
from pandas import DataFrame
class BalanceTracker:
    def __init__(self, stockpiles: List[StockpileData], grade_blocks: List[GradeBlockData], period_tracker, hex_sequence_table):
        self.state = {item.name: item.to_dict().get(f"state_{period_tracker}", 0) for item in stockpiles}
        self.balance = {item.name: item.balance for item in stockpiles + grade_blocks}
        self.grade_fe = {item.name: item.grade_fe for item in stockpiles + grade_blocks}
        self.grade_si = {item.name: item.grade_si for item in stockpiles + grade_blocks}
        self.grade_al = {item.name: item.grade_al for item in stockpiles + grade_blocks}
        self.grade_p = {item.name: item.grade_p for item in stockpiles + grade_blocks}
        self.grade_mn = {item.name: item.grade_mn for item in stockpiles + grade_blocks}
        self.is_amt = {item.name: item.is_AMT for item in stockpiles}
        self.balance_copy = self.balance.copy()
        self.build_report = [] # Store transactions that meet the condition
        self.hex_sequence_table = copy.deepcopy(hex_sequence_table)
        self.total_AMT_stockpile_balances = {}
        self.populate_total_AMT_stockpile_balances()
        
    def update_balances(self, filtered_decision_point_results_to_user_choice: DataFrame, expit_payload_transactions: DataFrame, steady_state_start_time, steady_state_end_time, steady_state_tracker):
        """Update balance and grades after each optimisation step."""
        
        # Loop through user choice of decision point results and deplete balances
        for _, transaction in filtered_decision_point_results_to_user_choice.iterrows():
            name = transaction["source"]

            # Check if the stockpile is marked as 'amt'
            if self.is_amt.get(name, False):

                self.process_hex_sequence(name, transaction["source_actual_tonnes"])
            
            if not self.is_amt.get(name, False) and self.balance_copy[name] != 0:

                self.balance_copy[name] -= transaction["source_actual_tonnes"]
       
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
                        current_balance = self.balance_copy[name]
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
                        self.balance_copy[name] = updated_balance
                        
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
        return self.balance_copy.get(name, 0), self.grade_fe.get(name, 0), self.grade_si.get(name, 0), self.grade_al.get(name, 0), self.grade_mn.get(name, 0), self.grade_p.get(name, 0)
    
    def process_hex_sequence(self, name, reclaimed_tonnes):
        # Filter the hex sequence table for entries matching the stockpile name
        filtered_hexes = [
            hex_entry for hex_entry in self.hex_sequence_table
            if hex_entry.get('footprint') == name
        ]

        # Sort the filtered hexes by the 'sequence' column
        sorted_hexes = sorted(filtered_hexes, key=lambda x: x.get('sequence', float('inf')))

        # Iterate through the sorted hexes to handle depletion
        for idx, current_hex in enumerate(sorted_hexes):
            current_balance = current_hex.get('balance', 0)
            
            if current_balance > 0:
                # Deplete the current hex
                current_hex['balance'] -= reclaimed_tonnes
                self.total_AMT_stockpile_balances[name] -= reclaimed_tonnes
                
                # If the current hex is not fully depleted
                if current_hex['balance'] > 0:
                    self.balance_copy[name] = current_hex['balance']
                    # Update grades with the current hex
                    for key, value in current_hex.items():
                        if key.startswith('grade_'):
                            if hasattr(self, key) and isinstance(getattr(self, key), dict):
                                getattr(self, key)[name] = value  # Update the dictionary instead of overwriting
                            else:
                                print(f"Warning: {key} is not a dictionary, skipping update")
                else:
                    # Find the next hex with a positive balance
                    next_hex = next(
                        (hex_entry for hex_entry in sorted_hexes[idx + 1:] if hex_entry.get('balance', 0) > 0),
                        None
                    )
                    # Update with the balance and grades of the next hex (if available)
                    if next_hex:
                        self.balance_copy[name] = next_hex['balance']
                        # Update grades with the next hex
                        for key, value in next_hex.items():
                            if key.startswith('grade_'):
                                if hasattr(self, key) and isinstance(getattr(self, key), dict):
                                    getattr(self, key)[name] = value  # Correctly update dictionary
                                else:
                                    print(f"Warning: {key} is not a dictionary, skipping update")
                    else:
                        self.balance_copy[name] = 0  # No further hexes with positive balance
                
                return  # Exit after processing the reclaimed tonnes
            
            else:
                continue
        
        # Sum up all balances in the dictionary
        total_balance_sum = sum(self.total_AMT_stockpile_balances.values())
        
        # If all hexes are depleted, set the stockpile balance to 0
        if total_balance_sum == 0:
            self.balance_copy[name] = 0

    def return_total_AMT_stockpile_balances(self):
        return self.total_AMT_stockpile_balances
    
    def populate_total_AMT_stockpile_balances(self):
        # Extract unique footprints from the hex sequence table
        unique_footprints = set(hex_entry.get('footprint') for hex_entry in self.hex_sequence_table if 'footprint' in hex_entry)

        # Calculate the total balance for each unique footprint
        for footprint in unique_footprints:
            filtered_hexes = [
                hex_entry for hex_entry in self.hex_sequence_table
                if hex_entry.get('footprint') == footprint
            ]
            total_balance = sum(hex_entry.get('balance', 0) for hex_entry in filtered_hexes)
            self.total_AMT_stockpile_balances[footprint] = total_balance

