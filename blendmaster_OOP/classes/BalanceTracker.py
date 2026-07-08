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
        self.grade_block_names = {item.name for item in grade_blocks}
        self.balance = {item.name: item.balance for item in stockpiles + grade_blocks}
        self.grade_fe = {item.name: item.grade_fe for item in stockpiles + grade_blocks}
        self.grade_si = {item.name: item.grade_si for item in stockpiles + grade_blocks}
        self.grade_al = {item.name: item.grade_al for item in stockpiles + grade_blocks}
        self.grade_p = {item.name: item.grade_p for item in stockpiles + grade_blocks}
        self.grade_mn = {item.name: item.grade_mn for item in stockpiles + grade_blocks}
        self.is_amt = {item.name: item.is_AMT for item in stockpiles}
        self.balance_copy = self.balance.copy()
        self.build_report = [] # Store transactions that meet the condition
        self.direct_tipped_tonnes_by_payload = {}
        self.hex_sequence_table = copy.deepcopy(hex_sequence_table)
        self.total_AMT_stockpile_balances = {}
        self.populate_total_AMT_stockpile_balances()
        
    def update_balances(self, filtered_decision_point_results_to_user_choice: DataFrame, expit_payload_transactions: DataFrame, steady_state_start_time, steady_state_end_time, steady_state_tracker):
        """Update balance and grades after each optimisation step."""
        self.register_direct_tipped_tonnes(filtered_decision_point_results_to_user_choice)
        
        # Loop through user choice of decision point results and deplete balances
        for _, transaction in filtered_decision_point_results_to_user_choice.iterrows():
            name = transaction.get("source_id", transaction["source"])
            if name not in self.balance_copy:
                continue

            reclaimed_tonnes = float(transaction["source_actual_tonnes"] or 0)
            if reclaimed_tonnes <= 0:
                continue

            # Check if the stockpile is marked as 'amt'
            if self.is_amt.get(name, False):

                self.process_hex_sequence(name, reclaimed_tonnes)
            
            if not self.is_amt.get(name, False) and self.balance_copy[name] != 0:

                self.balance_copy[name] = max(self.balance_copy[name] - reclaimed_tonnes, 0)
       
        if not expit_payload_transactions.empty:

            # Sort the DataFrame by delivered_datetime (old to new)
            expit_payload_transactions = expit_payload_transactions.sort_values(by=["destination", "delivered_datetime"])

            # Loop through expit payload transactions and build stockpiles
            for _, transaction in expit_payload_transactions.iterrows():
                name = self.resolve_payload_build_stockpile(transaction)
                if not name:
                    continue
                delivered_datetime = transaction["delivered_datetime"]
                payload = float(transaction["payload"] or 0)
                agent = transaction["agent"]
                source = transaction["source"]
                mining_start_datetime = transaction["start_datetime"]
                direct_tip_id = transaction.get("direct_tip_id", None)

                # Check if the transaction is within the time range
                if steady_state_start_time <= delivered_datetime < steady_state_end_time:
                    if direct_tip_id in self.direct_tipped_tonnes_by_payload:
                        direct_tipped_payload = min(payload, self.direct_tipped_tonnes_by_payload[direct_tip_id])
                        self.direct_tipped_tonnes_by_payload[direct_tip_id] -= direct_tipped_payload
                        payload -= direct_tipped_payload
                        if self.direct_tipped_tonnes_by_payload[direct_tip_id] <= 0:
                            del self.direct_tipped_tonnes_by_payload[direct_tip_id]

                    if payload <= 0:
                        continue

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

    def resolve_payload_build_stockpile(self, transaction):
        destination = self.clean_stockpile_name(transaction.get("destination", ""))
        if destination:
            return destination

        fallback_destination = self.clean_stockpile_name(transaction.get("fallback_destination", ""))
        if fallback_destination:
            return fallback_destination

        destination_type = str(transaction.get("destination_type", "") or "").strip().lower()
        aps_direct_tip_candidate = str(
            transaction.get("aps_direct_tip_candidate", False)
        ).strip().lower() in {"true", "1", "yes"}
        if destination_type == "crusher" or aps_direct_tip_candidate:
            return self.first_receiving_stockpile()

        return ""

    @staticmethod
    def clean_stockpile_name(value):
        if value is None or pd.isna(value):
            return ""
        value = str(value).strip()
        if not value:
            return ""
        return value.replace("Stockpiles/", "")

    def first_receiving_stockpile(self):
        for name, state in self.state.items():
            if str(state).strip().lower() in {"build", "auto"}:
                return name
        return ""

    def register_direct_tipped_tonnes(self, filtered_decision_point_results_to_user_choice: DataFrame):
        for payload_id, tonnes in self.get_direct_tipped_tonnes(filtered_decision_point_results_to_user_choice).items():
            self.direct_tipped_tonnes_by_payload[payload_id] = (
                self.direct_tipped_tonnes_by_payload.get(payload_id, 0) + tonnes
            )

    def get_direct_tipped_tonnes(self, filtered_decision_point_results_to_user_choice: DataFrame):
        """Return selected direct-tip tonnes by grade-block payload id."""
        if (
            filtered_decision_point_results_to_user_choice is None
            or filtered_decision_point_results_to_user_choice.empty
            or "source" not in filtered_decision_point_results_to_user_choice.columns
            or "source_actual_tonnes" not in filtered_decision_point_results_to_user_choice.columns
        ):
            return {}

        direct_tip_results = filtered_decision_point_results_to_user_choice.copy()
        direct_tip_results["source_actual_tonnes"] = pd.to_numeric(
            direct_tip_results["source_actual_tonnes"], errors="coerce"
        ).fillna(0)
        source_identifier_column = "source_id" if "source_id" in direct_tip_results.columns else "source"
        direct_tip_results = direct_tip_results[
            direct_tip_results[source_identifier_column].isin(self.grade_block_names)
            & (direct_tip_results["source_actual_tonnes"] > 0)
        ]
        if direct_tip_results.empty:
            return {}

        return direct_tip_results.groupby(source_identifier_column)["source_actual_tonnes"].sum().to_dict()
    
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

        reclaimed_tonnes = max(float(reclaimed_tonnes or 0), 0)
        if reclaimed_tonnes <= 0:
            return

        for current_hex in sorted_hexes:
            current_balance = max(float(current_hex.get('balance', 0) or 0), 0)
            current_hex['balance'] = current_balance

            if current_balance <= 0:
                continue

            depleted_tonnes = min(current_balance, reclaimed_tonnes)
            current_hex['balance'] = current_balance - depleted_tonnes
            self.total_AMT_stockpile_balances[name] = max(
                self.total_AMT_stockpile_balances.get(name, 0) - depleted_tonnes,
                0
            )
            break

        next_hex = next(
            (hex_entry for hex_entry in sorted_hexes if max(float(hex_entry.get('balance', 0) or 0), 0) > 0),
            None
        )

        if next_hex:
            self.balance_copy[name] = max(float(next_hex.get('balance', 0) or 0), 0)
            for key, value in next_hex.items():
                if key.startswith('grade_'):
                    if hasattr(self, key) and isinstance(getattr(self, key), dict):
                        getattr(self, key)[name] = value
                    else:
                        print(f"Warning: {key} is not a dictionary, skipping update")
        else:
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
            total_balance = sum(max(float(hex_entry.get('balance', 0) or 0), 0) for hex_entry in filtered_hexes)
            self.total_AMT_stockpile_balances[footprint] = total_balance

