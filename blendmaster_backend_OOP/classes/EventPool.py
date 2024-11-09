# This is where events are generated based on input data and regenerated / updated based on optimization results
class EventPool:
    def __init__(self, stockpiles, grade_blocks, equipment):
        self.stockpiles = stockpiles
        self.grade_blocks = grade_blocks
        self.equipment = equipment

    def generate_event_pool(self, period):
        """Generate potential events based on available stockpiles, grade blocks, and equipment."""
        events = []

        for stockpile in self.stockpiles:
            stockpile_priority = stockpile.get(f"priority_{period}", 0)
            for equipment in self.equipment:
                if equipment["name"] in stockpile.get("equipment", []): 
                    equipment_priority = equipment.get(f"priority_{period}", 0)
                    reclaim_rate = equipment.get(f"rate_{period}", 0)

                    events.append({
                        "stockpile": stockpile["name"],
                        "type": "stockpile",
                        "equipment": equipment["name"],
                        "priority": stockpile_priority + equipment_priority,
                        "rate": reclaim_rate,
                        "grade_fe": stockpile["grade_fe"],
                        "balance": stockpile["balance"]
                    })
        
        for grade_block in self.grade_blocks:
            for equipment in self.equipment:
                if equipment["name"] in grade_block.get("equipment", []) and "EX" in equipment["name"]:
                    equipment_priority = equipment.get(f"priority_{period}", 0)
                    reclaim_rate = equipment.get(f"rate_{period}", 0)

                    events.append({
                        "grade_block": grade_block["name"],
                        "type": "grade_block",
                        "equipment": equipment["name"],
                        "priority": equipment_priority,  # No grade block priority (their priority is inherently higher which is 0)
                        "rate": reclaim_rate,
                        "grade_fe": grade_block["grade_fe"],
                        "balance": grade_block["balance"]
                    })

        return events

    def get_events(self, period):
        """Retrieve generated event pool for the current period."""
        return self.generate_event_pool(period)

    def update_event_balances(self, event_pool, balance_tracker):
        """Update each event's balance in the pool based on the balance tracker."""
        for event in event_pool:
            if event["type"] == "stockpile":
                event["balance"] = balance_tracker.get_balance(event["stockpile"])
            elif event["type"] == "grade_block":
                event["balance"] = balance_tracker.get_balance(event["grade_block"])
