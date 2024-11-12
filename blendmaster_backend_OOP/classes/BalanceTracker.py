# This tracks source balances and the program runs and provides input to the EventPool for the update functionality
class BalanceTracker:
    def __init__(self, stockpiles, grade_blocks):
        self.balances = {item["name"]: item["balance"] for item in stockpiles + grade_blocks}
        
    def update_balances(self, filtered_decision_point_results_to_user_choice):
        """Update balance after each optimization step."""
        
        for _, transaction in filtered_decision_point_results_to_user_choice.iterrows():
            name = transaction["source"]
            
            if self.balances[name] != 0:
                self.balances[name] -= transaction["source_actual_tonnes"]

    def get_balance(self, name):
        """Retrieve the current balance for a stockpile or grade block."""
        return self.balances.get(name, 0)
