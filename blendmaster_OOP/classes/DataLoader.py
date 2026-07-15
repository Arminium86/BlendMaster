# This loads the data from external sources (currently an Excel file with multiple tabs which represents the combined user input and opening inventories)
import pandas as pd
from classes.EquipmentData import EquipmentData
from classes.StockpileData import StockpileData
from classes.GradeBlockData import GradeBlockData
from pandas import DataFrame

class DataLoader:
    def __init__(self, stockpile_data: dict, calendar_inputs: dict, expit_payload_transactions: DataFrame, hex_sequence_table: list, periods=None):
        self.stockpile_data = stockpile_data
        self.calendar_inputs = calendar_inputs
        self.expit_payload_transactions = expit_payload_transactions
        self.hex_sequence_table = hex_sequence_table
        self.periods = periods
        self.solver_config = (calendar_inputs or {}).get("solver_config", {})
        self.direct_tip_enabled = bool(self.solver_config.get("direct_tip_enabled", True))

    def load_data(self):
        """Loads data from GUI and returns it in structured format."""

        # Convert GUI and APS payload data into model objects.
        self.set_first_hex_tonnes_and_grades_to_AMT_stockpile()
        stockpile_data = self.process_stockpile_data()
        calendar_inputs = self.calendar_inputs
        stockpile_data_objects = self.create_stockpile_data_objects(stockpile_data, calendar_inputs)
        grade_block_data_objects = self.create_grade_block_data_objects(self.expit_payload_transactions)

        equipment_data = self.calendar_inputs['reclaim_equipment_max_reclaim_rate']
        crusher_rate_data = self.calendar_inputs['crusher_rate']
        equipment_data_objects = self.create_equipment_data_objects(equipment_data, crusher_rate_data)

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
                "direct_feed_ratio_min": self.get_direct_feed_ratio('crusher_direct_feed_ratio_min', 'Preplan', 0),
                "direct_feed_ratio_max": self.get_direct_feed_ratio('crusher_direct_feed_ratio_max', 'Preplan', 1),
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
                "direct_feed_ratio_min": self.get_direct_feed_ratio('crusher_direct_feed_ratio_min', 'Period_1', 0),
                "direct_feed_ratio_max": self.get_direct_feed_ratio('crusher_direct_feed_ratio_max', 'Period_1', 1),
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
                "direct_feed_ratio_min": self.get_direct_feed_ratio('crusher_direct_feed_ratio_min', 'Period_2', 0),
                "direct_feed_ratio_max": self.get_direct_feed_ratio('crusher_direct_feed_ratio_max', 'Period_2', 1),
                "crusher_rate": self.calendar_inputs['crusher_rate']['Period_2']
            }
        }

        for target in crusher_target_data.values():
            if target["direct_feed_ratio_min"] > target["direct_feed_ratio_max"]:
                raise ValueError("Direct Tip Ratio Min cannot be greater than Direct Tip Ratio Max.")

        return stockpile_data_objects, grade_block_data_objects, equipment_data_objects, crusher_target_data

    def get_direct_feed_ratio(self, key, period, default):
        if not self.direct_tip_enabled:
            return 0

        direct_tip_key = key.replace("direct_feed", "direct_tip")
        ratio_values = self.calendar_inputs.get(direct_tip_key, self.calendar_inputs.get(key, {}))
        ratio = float(ratio_values.get(period, default))
        if not 0 <= ratio <= 1:
            raise ValueError("Direct Tip Ratio Min/Max values must be between 0 and 1.")
        return ratio
    
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
            
            if not self.expit_payload_transactions.empty:
                
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
    
    def create_equipment_data_objects(self, equipment_data_dicts: dict, crusher_rate_dicts: dict):
        return [
            EquipmentData(
                name="RC",
                priority_preplan=0, 
                priority_period_1=0,
                priority_period_2=0,
                rate_preplan=equipment_data_dicts['Preplan'],
                rate_period_1=equipment_data_dicts['Period_1'],
                rate_period_2=equipment_data_dicts['Period_2']
            ),
            EquipmentData(
                name="EX",
                priority_preplan=0,
                priority_period_1=0,
                priority_period_2=0,
                rate_preplan=crusher_rate_dicts['Preplan'],
                rate_period_1=crusher_rate_dicts['Period_1'],
                rate_period_2=crusher_rate_dicts['Period_2']
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
                is_ready=nested_record["is_ready"],
                is_AMT=nested_record["amt"],
                aps_brand=nested_record.get("aps_brand", ""),
                aps_brand_proportions=nested_record.get("aps_brand_proportions", {}),
                aps_brand_tonnes=nested_record.get("aps_brand_tonnes", {})

            )
            for record, nested_record in stockpile_data.items()
        ]

    def create_grade_block_data_objects(self, expit_payload_transactions):
        """Create a list of GradeBlockData objects from a list of dictionaries."""
        if not self.direct_tip_enabled:
            return []

        if expit_payload_transactions is None or expit_payload_transactions.empty:
            return []

        payload_transactions = expit_payload_transactions.copy()
        if "direct_tip_eligible" in payload_transactions.columns:
            payload_transactions = payload_transactions[
                payload_transactions["direct_tip_eligible"].map(
                    lambda value: str(value).strip().lower() in {"true", "1", "yes"}
                )
            ].copy()
        else:
            # New runs require an explicit source-to-crusher movement rule.
            return []
        if payload_transactions.empty:
            return []
        if "direct_tip_id" not in payload_transactions.columns:
            payload_transactions["direct_tip_id"] = [
                f"GB_{index + 1:06d}" for index in range(len(payload_transactions))
            ]

        return [
            GradeBlockData(
                name=record["direct_tip_id"],
                balance=record["payload"],
                max_quantity_preplan=self.payload_quantity_for_period(record, "preplan"),
                max_quantity_period_1=self.payload_quantity_for_period(record, "period_1"),
                max_quantity_period_2=self.payload_quantity_for_period(record, "period_2"),
                cost_preplan=0,
                cost_period_1=0,
                cost_period_2=0,
                cash_preplan=0,
                cash_period_1=0,
                cash_period_2=0,
                equipment="EX",
                grade_fe=record["source_grade_fe"],
                grade_si=record["source_grade_si"],
                grade_al=record["source_grade_al"],
                grade_p=record["source_grade_p"],
                grade_mn=record["source_grade_mn"],
                delivered_datetime=record.get("delivered_datetime"),
                destination=record.get("destination"),
                agent=record.get("agent"),
                start_datetime=record.get("start_datetime"),
                source=record.get("source"),
            )
            for record in payload_transactions.to_dict(orient="records")
        ]

    def payload_quantity_for_period(self, payload_record, period_name):
        delivered_datetime = pd.to_datetime(
            payload_record.get("delivered_datetime"),
            errors="coerce",
        )
        if pd.isna(delivered_datetime):
            return 0

        if self.periods is None:
            return payload_record["payload"]

        period_data = self.periods.get_periods()
        period_start = period_data[f"{period_name}_start"]
        period_end = period_data[f"{period_name}_end"]
        if period_start <= delivered_datetime < period_end:
            return payload_record["payload"]
        return 0

    def set_first_hex_tonnes_and_grades_to_AMT_stockpile(self):
        for stockpile_name, stockpile_data in self.stockpile_data.items():
            # Check if the stockpile has 'amt' set to True
            if stockpile_data.get('amt'):
                corresponding_hexes = sorted(
                    (
                        hex_entry for hex_entry in self.hex_sequence_table
                        if hex_entry['footprint'] == stockpile_name
                    ),
                    key=lambda hex_entry: hex_entry.get('sequence', float('inf'))
                )
                corresponding_hex = next(
                    (
                        hex_entry for hex_entry in corresponding_hexes
                        if max(float(hex_entry.get('balance', 0) or 0), 0) > 0
                    ),
                    None
                )
                if corresponding_hex:
                    # Update the balance and grades in stockpile_data
                    stockpile_data['balance'] = max(float(corresponding_hex.get('balance', 0) or 0), 0)
                    for key in corresponding_hex:
                        if key.startswith('grade_'):
                            stockpile_data[key] = corresponding_hex[key]


