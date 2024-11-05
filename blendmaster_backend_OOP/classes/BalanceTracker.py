class BalanceTracker:
    def __init__(self, stockpiles, grade_blocks):
        self.balances = {item["name"]: item["balance"] for item in stockpiles + grade_blocks}

    def update_balances(self, events):
        """Update balance after each optimization step."""
        for event in events:
            name = event["Source"]
            self.balances[name] -= event["Actual Tonnes (Reclaimed)"]

    def get_balance(self, name):
        """Retrieve the current balance for a stockpile or grade block."""
        return self.balances.get(name, 0)
