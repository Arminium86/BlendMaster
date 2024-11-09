# This tracks source balances and the program runs and provides input to the EventPool for the update functionality
class BalanceTracker:
    def __init__(self, stockpiles, grade_blocks):
        self.balances = {item["name"]: item["balance"] for item in stockpiles + grade_blocks}

    def adjust_results(self, result):
        for outcome in result["outcome"]:
            name = outcome["source"]
            remaining_balance = self.balances[name] - outcome["actual_tonnes"]
            
            if (remaining_balance / result["crusher_actual_tonnes"]) < 0.1:
                outcome["actual_tonnes"] += remaining_balance
                self.balances[name] = 0

    def update_balances(self, result):
        """Update balance after each optimization step."""
        for outcome in result["outcome"]:
            name = outcome["source"]
           
            if self.balances[name] != 0:
                self.balances[name] -= outcome["actual_tonnes"]

    def get_balance(self, name):
        """Retrieve the current balance for a stockpile or grade block."""
        return self.balances.get(name, 0)
