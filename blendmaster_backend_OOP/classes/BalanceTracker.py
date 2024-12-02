# This tracks source balances and the program runs and provides input to the EventPool for the update functionality
class BalanceTracker:
    def __init__(self, stockpiles, grade_blocks):
        self.balances = {item["name"]: item["balance"] for item in stockpiles + grade_blocks}
        self.grade_fe = {item["name"]: item["grade_fe"] for item in stockpiles + grade_blocks}
        self.grade_si = {item["name"]: item["grade_si"] for item in stockpiles + grade_blocks}
        self.grade_al = {item["name"]: item["grade_al"] for item in stockpiles + grade_blocks}
        self.grade_p = {item["name"]: item["grade_p"] for item in stockpiles + grade_blocks}
        self.grade_mn = {item["name"]: item["grade_mn"] for item in stockpiles + grade_blocks}
        
    def update_balances(self, filtered_decision_point_results_to_user_choice, expit_payload_transactions, start_time, end_time):
        """Update balance and grades after each optimization step."""
        
        # Loop through decision point results
        for _, transaction in filtered_decision_point_results_to_user_choice.iterrows():
            name = transaction["source"]
            
            if self.balances[name] != 0:
                self.balances[name] -= transaction["source_actual_tonnes"]
        
        # Loop through expit payload transactions
        for _, transaction in expit_payload_transactions.iterrows():
            name = transaction["destination"].replace("Stockpiles/", "")
            delivered_datetime = transaction["delivered_datetime"]
            payload = transaction["payload"]
            
            # Check if the transaction is within the time range
            if start_time <= delivered_datetime <= end_time:
                if name in self.balances:
                    # Perform weighted averaging for each grade
                    current_balance = self.balances[name]
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
                    self.balances[name] = updated_balance
                else: 
                    raise ValueError(f"Name '{name}' is not found in opening inventory. Either review the Snowflake query or remove the transactions to this destination from APS output.")

    def get_balance(self, name):
        """Retrieve the current balance for a stockpile or grade block."""
        return self.balances.get(name, 0)

