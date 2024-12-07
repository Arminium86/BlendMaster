# This is where events are generated based on input data and regenerated / updated based on optimization results
import pandas as pd
from classes.EquipmentData import EquipmentData
from classes.StockpileData import StockpileData
from classes.GradeBlockData import GradeBlockData
from typing import List
class EventPool:
    def __init__(self, stockpiles: List[StockpileData], grade_blocks: List[GradeBlockData], equipment: List[EquipmentData]):
        self.stockpiles = stockpiles
        self.grade_blocks = grade_blocks
        self.equipment = equipment


    def generate_initial_event_pool(self, period, current_time, balance_tracker):
        """Generate potential events based on available stockpiles, grade blocks, and equipment."""
        events = []

        for stockpile in self.stockpiles:
            stockpile_state = stockpile.to_dict().get(f"state_{period}", 0)
            if (self.is_stockpile_ready(stockpile, period, current_time, balance_tracker)):
                stockpile_cost = stockpile.to_dict().get(f"cost_{period}", 0) # This can be used as a future cost per tonne for a stockpile based on haulage time / distance 
                stockpile_cash = -stockpile.to_dict().get(f"cash_{period}", 0) # Manual user cash flow to incentivise / disincentivise a source - negative value for Linprog to minimize
                stockpile_max_quantity = stockpile.to_dict().get(f"max_quantity_{period}", 0)
                for equipment in self.equipment:
                    
                    if equipment.name in stockpile.to_dict().get("equipment", []) and "RC" in equipment.name: 
                        equipment_priority = equipment.to_dict().get(f"priority_{period}", 0)
                        reclaim_rate = equipment.to_dict().get(f"rate_{period}", 0)

                        events.append({
                            "stockpile": stockpile.name,
                            "type": "stockpile",
                            "equipment": equipment.name,
                            "cost": stockpile_cost + equipment_priority,
                            "cash": stockpile_cash,
                            "rate": reclaim_rate,
                            "grade_fe": stockpile.grade_fe,
                            "grade_si": stockpile.grade_si,
                            "grade_al": stockpile.grade_al,
                            "grade_p": stockpile.grade_p,
                            "grade_mn": stockpile.grade_mn,
                            "balance": stockpile.balance,
                            "max_quantity": stockpile_max_quantity,
                            "reclaim_threshold": stockpile.reclaim_threshold,
                            "state": stockpile_state,
                            "auto_turnover_datetime": stockpile.auto_turnover_datetime
                        })

        for grade_block in self.grade_blocks:
            grade_block_cost = grade_block.to_dict().get(f"cost_{period}", 0) # This can be used as a future cost per tonne for a stockpile based on haulage time / distance 
            grade_block_cash = -grade_block.to_dict().get(f"cash_{period}", 0) # Manual user cash flow to incentivise / disincentivise a source - negative value for Linprog to minimize
            grade_block_max_quantity = grade_block.to_dict().get(f"max_quantity_{period}", 0)
            for equipment in self.equipment:
                if equipment.name in grade_block.to_dict().get("equipment", []) and "EX" in equipment.name:
                    equipment_priority = equipment.to_dict().get(f"priority_{period}", 0)
                    reclaim_rate = equipment.to_dict().get(f"rate_{period}", 0)

                    events.append({
                        "grade_block": grade_block.name,
                        "type": "grade_block",
                        "equipment": equipment.name,
                        "cost": grade_block_cost + equipment_priority, 
                        "cash": grade_block_cash,
                        "rate": reclaim_rate,
                        "grade_fe": grade_block.grade_fe,
                        "grade_si": grade_block.grade_si,
                        "grade_al": grade_block.grade_al,
                        "grade_p": grade_block.grade_p,
                        "grade_mn": grade_block.grade_mn,
                        "balance": grade_block.balance,
                        "max_quantity": grade_block_max_quantity
                    })

        return events
    
    def expit_transactions_complete(self, stockpile: StockpileData, current_time):

        if stockpile.auto_turnover_datetime:
            if stockpile.auto_turnover_datetime > current_time:
                return False
            else: return True
        else: return True
    
    def is_stockpile_ready(self, stockpile: StockpileData, period, current_time, balance_tracker):
        stockpile_state = stockpile.to_dict().get(f"state_{period}", 0)
        stockpile.balance = balance_tracker.get_balance(stockpile.name)
        if ((stockpile_state == "Auto" and 
            self.expit_transactions_complete(stockpile, current_time)
            ) 
            or stockpile_state == "Reclaim"): return True
        
        else: return False

    def update_pool_participants(self, decision_point_results, initial_event_pool):
    
        events = []

        if decision_point_results is None:

            for event in initial_event_pool:
            
                if event["type"] == "stockpile" and event["balance"] < event["reclaim_threshold"]:
                    continue
                else: events.append(event)
            
        elif decision_point_results is not None:
            decision_point_results["source_actual_tonnes"] = pd.to_numeric(
            decision_point_results["source_actual_tonnes"], errors='coerce'
            )

            for event in initial_event_pool:
                
                if event["type"] == "stockpile" and event["balance"] < event["reclaim_threshold"]:
                    continue
                            
                elif (event["type"] == "stockpile" and 
                    event["stockpile"] in decision_point_results["source"].values and 
                    decision_point_results.loc[
                        decision_point_results["source"] == event["stockpile"], "source_actual_tonnes"
                    ].gt(0).any()):
                    continue
        
                elif (event["type"] == "grade_block" and 
                    event["grade_block"] in decision_point_results["source"].values and 
                    decision_point_results.loc[
                        decision_point_results["source"] == event["grade_block"], "source_actual_tonnes"
                    ].gt(0).any()):
                    continue

                else: events.append(event)

        return events 

    def get_events(self, period, decision_point_results, current_time, balance_tracker):
        """Retrieve generated blend event pool for the current period."""
        initial_event_pool = self.generate_initial_event_pool(period, current_time, balance_tracker)
        final_event_pool = self.update_pool_participants(decision_point_results, initial_event_pool)
       
        return final_event_pool

    def update_event_balances(self, event_pool, balance_tracker):
        """Update each event's balance in the pool based on the balance tracker."""
        for event in event_pool:
            if event["type"] == "stockpile":
                event["balance"] = balance_tracker.get_balance(event["stockpile"])
            elif event["type"] == "grade_block":
                event["balance"] = balance_tracker.get_balance(event["grade_block"])
