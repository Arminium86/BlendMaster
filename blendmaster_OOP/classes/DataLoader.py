# This loads the data from external sources (currently an Excel file with multiple tabs which represents the combined user input and opening inventories)
import pandas as pd
from classes.EquipmentData import EquipmentData
from classes.StockpileData import StockpileData
from classes.GradeBlockData import GradeBlockData

class DataLoader:
    def __init__(self, stockpile_data: dict, calendar_inputs: dict, expit_payload_transactions):
        self.stockpile_data = stockpile_data
        self.calendar_inputs = calendar_inputs
        self.expit_payload_transactions = expit_payload_transactions

    def load_data(self):
        """Loads data from GUI and returns it in structured format."""

        # Convert data to a list of dictionaries for easy access (grade block not implemented)
        stockpile_data = self.process_stockpile_data()
        calendar_inputs = self.calendar_inputs
        stockpile_data_objects = self.create_stockpile_data_objects(stockpile_data, calendar_inputs)

        equipment_data = self.calendar_inputs['reclaim_equipment_max_reclaim_rate']
        equipment_data_objects = self.create_equipment_data_objects(equipment_data)

        # Structure crusher target data as a nested dictionary
        crusher_target_data = {
            "preplan": {
                "target_fe_min": self.calendar_inputs['crusher_target_fe_min']['Preplan'],
                "target_fe_max": self.calendar_inputs['crusher_target_fe_max']['Preplan'],
                "target_si_min": self.calendar_inputs['crusher_target_si_min']['Preplan'],
                "target_si_max": self.calendar_inputs['crusher_target_si_max']['Preplan'],
                "target_al_min": self.calendar_inputs['crusher_target_al_min']['Preplan'],
                "target_al_max": self.calendar_inputs['crusher_target_al_max']['Preplan'],
                "target_p_min": self.calendar_inputs['crusher_target_p_min']['Preplan'],
                "target_p_max": self.calendar_inputs['crusher_target_p_max']['Preplan'],
                "target_mn_min": self.calendar_inputs['crusher_target_mn_min']['Preplan'],
                "target_mn_max": self.calendar_inputs['crusher_target_mn_max']['Preplan'],
                "direct_feed_ratio_min": 0,
                "direct_feed_ratio_max": 100,
                "crusher_rate": self.calendar_inputs['crusher_rate']['Preplan']
            },
            "period_1": {
                "target_fe_min": self.calendar_inputs['crusher_target_fe_min']['Period_1'],
                "target_fe_max": self.calendar_inputs['crusher_target_fe_max']['Period_1'],
                "target_si_min": self.calendar_inputs['crusher_target_si_min']['Period_1'],
                "target_si_max": self.calendar_inputs['crusher_target_si_max']['Period_1'],
                "target_al_min": self.calendar_inputs['crusher_target_al_min']['Period_1'],
                "target_al_max": self.calendar_inputs['crusher_target_al_max']['Period_1'],
                "target_p_min": self.calendar_inputs['crusher_target_p_min']['Period_1'],
                "target_p_max": self.calendar_inputs['crusher_target_p_max']['Period_1'],
                "target_mn_min": self.calendar_inputs['crusher_target_mn_min']['Period_1'],
                "target_mn_max": self.calendar_inputs['crusher_target_mn_max']['Period_1'],
                "direct_feed_ratio_min": 0,
                "direct_feed_ratio_max": 100,
                "crusher_rate": self.calendar_inputs['crusher_rate']['Period_1']
            },
            "period_2": {
                "target_fe_min": self.calendar_inputs['crusher_target_fe_min']['Period_2'],
                "target_fe_max": self.calendar_inputs['crusher_target_fe_max']['Period_2'],
                "target_si_min": self.calendar_inputs['crusher_target_si_min']['Period_2'],
                "target_si_max": self.calendar_inputs['crusher_target_si_max']['Period_2'],
                "target_al_min": self.calendar_inputs['crusher_target_al_min']['Period_2'],
                "target_al_max": self.calendar_inputs['crusher_target_al_max']['Period_2'],
                "target_p_min": self.calendar_inputs['crusher_target_p_min']['Period_2'],
                "target_p_max": self.calendar_inputs['crusher_target_p_max']['Period_2'],
                "target_mn_min": self.calendar_inputs['crusher_target_mn_min']['Period_2'],
                "target_mn_max": self.calendar_inputs['crusher_target_mn_max']['Period_2'],
                "direct_feed_ratio_min": 0,
                "direct_feed_ratio_max": 100,
                "crusher_rate": self.calendar_inputs['crusher_rate']['Period_2']
            }
        }

        return stockpile_data_objects, equipment_data_objects, crusher_target_data
    
    def process_stockpile_data(self):
             
        # Load stockpile data

        stockpile_data = self.stockpile_data

        for record, nested_record in stockpile_data.items():
            # Initialize auto_turnover_datetime to None
            nested_record['auto_turnover_datetime'] = None
            nested_record['is_ready'] = None

        for record, nested_record in stockpile_data.items():

            # Filter transactions related to the current stockpile
            stockpile_id = record
            
            if self.expit_payload_transactions:
                
                stockpile_transactions = self.expit_payload_transactions[
                    self.expit_payload_transactions["destination"].str.replace("Stockpiles/", "", regex=False) == stockpile_id
                ].to_dict(orient='records')
            
            else:
                stockpile_transactions = None


            if not stockpile_transactions:
                continue  # Skip if no transactions for this stockpile

            # Find the latest transaction by delivered_datetime
            latest_transaction = max(
                stockpile_transactions,
                key=lambda txn: txn['delivered_datetime']
            )
            latest_datetime = latest_transaction['delivered_datetime']

            # Sum all payloads for the stockpile
            total_payload = sum(txn['payload'] for txn in stockpile_transactions)

            # Determine availability
            total_balance = nested_record['balance'] + total_payload
            nested_record['is_ready'] = total_balance >= nested_record['reclaim_threshold']

            # If ready, update auto_turnover_datetime
            if nested_record['is_ready']:
                nested_record['auto_turnover_datetime'] = latest_datetime
        
        return stockpile_data
    
    def create_equipment_data_objects(self, equipment_data_dicts: dict):
        return [
            EquipmentData(
                name="RC",
                priority_preplan=0, 
                priority_period_1=0,
                priority_period_2=0,
                rate_preplan=equipment_data_dicts['Preplan'],
                rate_period_1=equipment_data_dicts['Period_1'],
                rate_period_2=equipment_data_dicts['Period_2']
            )
        ]
    
    def create_stockpile_data_objects(self, stockpile_data:dict, calendar_inputs:dict):
        """Create a list of StockpileData objects from a list of dictionaries."""
        return [
            StockpileData(
                name=record,
                balance=nested_record["balance"],
                state_preplan=calendar_inputs[f"stockpiles_{nested_record['name']}_state"]['Preplan'],
                state_period_1=calendar_inputs[f"stockpiles_{nested_record['name']}_state"]['Period_1'],
                state_period_2=calendar_inputs[f"stockpiles_{nested_record['name']}_state"]['Period_2'],
                max_quantity_preplan=calendar_inputs[f"stockpiles_{nested_record['name']}_maximum_quantity"]['Preplan'],
                max_quantity_period_1=calendar_inputs[f"stockpiles_{nested_record['name']}_maximum_quantity"]['Period_1'],
                max_quantity_period_2=calendar_inputs[f"stockpiles_{nested_record['name']}_maximum_quantity"]['Period_2'],
                cost_preplan=calendar_inputs[f"stockpiles_{nested_record['name']}_cost"]['Preplan'],
                cost_period_1=calendar_inputs[f"stockpiles_{nested_record['name']}_cost"]['Period_1'],
                cost_period_2=calendar_inputs[f"stockpiles_{nested_record['name']}_cost"]['Period_2'],
                cash_preplan=calendar_inputs[f"stockpiles_{nested_record['name']}_cash"]['Preplan'],
                cash_period_1=calendar_inputs[f"stockpiles_{nested_record['name']}_cash"]['Period_1'],
                cash_period_2=calendar_inputs[f"stockpiles_{nested_record['name']}_cash"]['Period_2'],
                equipment="RC",
                reclaim_threshold=nested_record["reclaim_threshold"],
                grade_fe=nested_record["grade_fe"],
                grade_si=nested_record["grade_si"],
                grade_al=nested_record["grade_al"],
                grade_p=nested_record["grade_p"],
                grade_mn=nested_record["grade_mn"],
                auto_turnover_datetime=nested_record["auto_turnover_datetime"],
                is_ready=nested_record["is_ready"]
            )
            for record, nested_record in stockpile_data.items()
        ]

    # Not implemented
    def create_grade_block_data_objects(self, grade_block_data_dicts):
        """Create a list of GradeBlockData objects from a list of dictionaries."""
        return [
            GradeBlockData(
                name=record["name"],
                balance=record["balance"],
                max_quantity_preplan=record["max_quantity_preplan"],
                max_quantity_period_1=record["max_quantity_period_1"],
                max_quantity_period_2=record["max_quantity_period_2"],
                cost_preplan=record["cost_preplan"],
                cost_period_1=record["cost_period_1"],
                cost_period_2=record["cost_period_2"],
                cash_preplan=record["cash_preplan"],
                cash_period_1=record["cash_period_1"],
                cash_period_2=record["cash_period_2"],
                equipment=record["equipment"],
                grade_fe=record["grade_fe"],
            )
            for record in grade_block_data_dicts
        ]
