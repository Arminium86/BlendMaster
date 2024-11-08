class BalanceTracker:
    def __init__(self, stockpiles, grade_blocks):
        self.balances = {item["name"]: item["balance"] for item in stockpiles + grade_blocks}

    def adjust_results(self, result):
        for event in result["outcome"]:
            name = event["Source"]
            remaining_balance = self.balances[name] - event["Actual Tonnes (Reclaimed)"]
            
            if (remaining_balance / result["Actual Crusher Tonnes"]) < 0.1:
                event["Actual Tonnes (Reclaimed)"] += remaining_balance
                self.balances[name] = 0

    def update_balances(self, result):
        """Update balance after each optimization step."""
        for event in result["outcome"]:
            name = event["Source"]
           
            if self.balances[name] != 0:
                self.balances[name] -= event["Actual Tonnes (Reclaimed)"]

    def get_balance(self, name):
        """Retrieve the current balance for a stockpile or grade block."""
        return self.balances.get(name, 0)
