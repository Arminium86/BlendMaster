# This is where events are generated based on input data and regenerated / updated based on optimization results
import pandas as pd
from classes.EquipmentData import EquipmentData
from classes.StockpileData import StockpileData
from classes.GradeBlockData import GradeBlockData
from classes.BalanceTracker import BalanceTracker
from classes.EventData import EventData
from typing import List
class EventPoolGenerator:
    def __init__(self, stockpiles: List[StockpileData], grade_blocks: List[GradeBlockData], equipment: List[EquipmentData]):
        self.stockpiles = stockpiles
        self.grade_blocks = grade_blocks
        self.equipment = equipment


    def generate_initial_event_pool(self, period, current_time, steady_state_end_time, balance_tracker):
        """Generate potential events based on available stockpiles, grade blocks, and equipment."""
        events = []

        for stockpile in self.stockpiles:
            stockpile_state = stockpile.to_dict().get(f"state_{period}", 0)
            if (self.is_stockpile_ready(stockpile, period, current_time, balance_tracker)):
                stockpile_cost = stockpile.to_dict().get(f"cost_{period}", 0) # This can be used as a future cost per tonne for a stockpile based on haulage time / distance 
                # Compatibility field only. Calendar Cash no longer
                # participates in source selection.
                stockpile_cash = 0.0
                stockpile_max_quantity = stockpile.to_dict().get(f"max_quantity_{period}", 0)
                for equipment in self.equipment:
                    
                    if equipment.name in stockpile.to_dict().get("equipment", []) and "RC" in equipment.name: 
                        equipment_priority = equipment.to_dict().get(f"priority_{period}", 0)
                        reclaim_rate = stockpile.max_reclaim_rate
                        if reclaim_rate is None:
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
                            "auto_turnover_datetime": stockpile.auto_turnover_datetime,
                            "is_amt": stockpile.is_AMT,
                            "source_name": stockpile.name,
                            "aps_brand": stockpile.aps_brand,
                            "aps_brand_proportions": stockpile.aps_brand_proportions,
                            "grade_streams": stockpile.grade_streams,
                            "source_properties": stockpile.source_properties,
                            "source_property_kinds": getattr(
                                stockpile, "source_property_kinds", {}
                            ),
                            "source_property_weights": getattr(
                                stockpile, "source_property_weights", {}
                            ),
                        })

        for grade_block in self.grade_blocks:
            delivered_datetime = grade_block.delivered_datetime
            if (
                delivered_datetime is None
                or pd.isna(delivered_datetime)
                or not (current_time <= delivered_datetime < steady_state_end_time)
            ):
                continue

            grade_block_cost = grade_block.to_dict().get(f"cost_{period}", 0) # This can be used as a future cost per tonne for a stockpile based on haulage time / distance 
            grade_block_cash = 0.0
            grade_block_max_quantity = grade_block.to_dict().get(f"max_quantity_{period}", 0)
            if grade_block_max_quantity <= 0:
                continue
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
                        "max_quantity": grade_block_max_quantity,
                        "source_name": grade_block.source or grade_block.name,
                        "delivered_datetime": delivered_datetime,
                        "grade_streams": grade_block.grade_streams,
                        "source_properties": grade_block.source_properties,
                        "source_property_kinds": getattr(
                            grade_block, "source_property_kinds", {}
                        ),
                        "source_property_weights": getattr(
                            grade_block, "source_property_weights", {}
                        ),
                        "two_wp_planned_stockpile_destination": (
                            grade_block.two_wp_planned_stockpile_destination
                        ),
                        "two_wp_first_reclaim_datetime": (
                            grade_block.two_wp_first_reclaim_datetime
                        ),
                        "two_wp_destination_turnover_priority": (
                            grade_block.two_wp_destination_turnover_priority
                        ),
                        "two_wp_turnover_guidance_applicable": (
                            grade_block.two_wp_turnover_guidance_applicable
                        ),
                    })

        return events

    def update_pool_participants(self, decision_point_results: pd.DataFrame, initial_event_pool: List[EventData]):
        """Updates initial event pool based on stockpile state, reclaim threshold and whether an event occurred in a previous iteration of a steady state (until there is no events left). See method definition."""
        events = []

        # Exclude events if they already contributed positive tonnes in the
        # current decision point. The first iteration has no result columns yet.
        excluded_sources = set()
        if (
            not decision_point_results.empty
            and "source_actual_tonnes" in decision_point_results.columns
            and "source" in decision_point_results.columns
        ):
            decision_point_results = decision_point_results.copy()
            decision_point_results["source_actual_tonnes"] = pd.to_numeric(
                decision_point_results["source_actual_tonnes"], errors="coerce"
            ).fillna(0)
            excluded_sources = set(
                decision_point_results.loc[
                    decision_point_results["source_actual_tonnes"] > 0, "source"
                ]
            )

        for event in initial_event_pool:
            if event.is_stockpile and event.stockpile in excluded_sources:
                continue

            if event.is_grade_block and event.grade_block in excluded_sources:
                continue

            if event.is_stockpile:
                if event.state == "Build":
                    continue
                elif event.state == "Off":
                    continue
                elif event.state == "Auto" and event.balance < event.reclaim_threshold:
                    continue
                elif event.state == "Auto" and event.balance >= event.reclaim_threshold:
                    events.append(event)
                elif event.state == "Reclaim":
                    events.append(event)
            else:
                events.append(event)

        return events 

    def get_events(self, period, decision_point_results, current_time, steady_state_end_time, balance_tracker):
        """Retrieve generated blend event pool for the current period."""
        initial_event_pool = self.generate_initial_event_pool(period, current_time, steady_state_end_time, balance_tracker)
        initial_event_pool_objects = self.create_event_data_objects(initial_event_pool)
        final_event_pool = self.update_pool_participants(decision_point_results, initial_event_pool_objects)
       
        return final_event_pool

    def update_event_balances(self, event_pool: List[EventData], balance_tracker: BalanceTracker):
        """Update each event's balance in the pool based on the balance tracker."""
        for event in event_pool:
            if event.is_stockpile:
                event.balance, event.grade_fe, event.grade_si, event.grade_al, event.grade_mn, event.grade_p  = balance_tracker.get_balance(event.stockpile)
                event.grade_streams = balance_tracker.get_grade_streams(event.stockpile)
                get_properties = getattr(
                    balance_tracker, "get_source_properties", None
                )
                if callable(get_properties):
                    event.source_properties = get_properties(event.stockpile)
                get_active_chunk = getattr(
                    balance_tracker, "get_active_amt_chunk_id", None
                )
                if event.is_amt and callable(get_active_chunk):
                    event.source_name = (
                        get_active_chunk(event.stockpile) or event.stockpile
                    )
            elif event.is_grade_block:
                event.balance, event.grade_fe, event.grade_si, event.grade_al, event.grade_mn, event.grade_p = balance_tracker.get_balance(event.grade_block)
                event.grade_streams = balance_tracker.get_grade_streams(event.grade_block)
                get_properties = getattr(
                    balance_tracker, "get_source_properties", None
                )
                if callable(get_properties):
                    event.source_properties = get_properties(event.grade_block)
    
    def is_stockpile_ready(self, stockpile: StockpileData, period, current_time, balance_tracker: BalanceTracker):
        stockpile_state = stockpile.to_dict().get(f"state_{period}", 0)
        stockpile.balance, stockpile.grade_fe, stockpile.grade_si, stockpile.grade_al, stockpile.grade_mn, stockpile.grade_p = balance_tracker.get_balance(stockpile.name)
        get_properties = getattr(
            balance_tracker, "get_source_properties", None
        )
        if callable(get_properties):
            stockpile.source_properties = get_properties(stockpile.name)
        if (
            ((stockpile_state == "Auto" and self.expit_transactions_complete(stockpile, current_time)) 
            
            or stockpile_state == "Reclaim") and 
           
            all(grade is not None for grade in [
                stockpile.grade_fe, stockpile.grade_si, stockpile.grade_al, stockpile.grade_mn, stockpile.grade_p
            ]) and 
            
            stockpile.balance >= 0
        ):
            return True

        else:
            return False
    
    def expit_transactions_complete(self, stockpile: StockpileData, current_time):

        if stockpile.auto_turnover_datetime:
            if stockpile.auto_turnover_datetime > current_time:
                return False
            else: return True
        else: return True

    def create_event_data_objects(self, event_data_dicts):
        """Create a list of EventData objects from a list of dictionaries."""
        return [
            EventData(
                stockpile=record.get("stockpile"),
                grade_block=record.get("grade_block"),
                event_type=record.get("type"),
                equipment=record.get("equipment"),
                cost=record.get("cost"),
                cash=record.get("cash"),
                rate=record.get("rate"),
                grade_fe=record.get("grade_fe"),
                grade_si=record.get("grade_si"),
                grade_al=record.get("grade_al"),
                grade_p=record.get("grade_p"),
                grade_mn=record.get("grade_mn"),
                balance=record.get("balance"),
                max_quantity=record.get("max_quantity"),
                reclaim_threshold=record.get("reclaim_threshold"),
                state=record.get("state"),
                auto_turnover_datetime=record.get("auto_turnover_datetime"),
                is_amt=record.get("is_amt", False),
                source_name=record.get("source_name"),
                delivered_datetime=record.get("delivered_datetime"),
                aps_brand=record.get("aps_brand"),
                aps_brand_proportions=record.get("aps_brand_proportions"),
                grade_streams=record.get("grade_streams"),
                source_properties=record.get("source_properties"),
                source_property_kinds=record.get("source_property_kinds"),
                source_property_weights=record.get("source_property_weights"),
                two_wp_planned_stockpile_destination=record.get(
                    "two_wp_planned_stockpile_destination"
                ),
                two_wp_first_reclaim_datetime=record.get(
                    "two_wp_first_reclaim_datetime"
                ),
                two_wp_destination_turnover_priority=record.get(
                    "two_wp_destination_turnover_priority"
                ),
                two_wp_turnover_guidance_applicable=record.get(
                    "two_wp_turnover_guidance_applicable", False
                ),

            )
            for record in event_data_dicts
        ]
