# This loads the data from external sources (currently an Excel file with multiple tabs which represents the combined user input and opening inventories)
import pandas as pd
from classes.EquipmentData import EquipmentData
from classes.StockpileData import StockpileData
from classes.GradeBlockData import GradeBlockData

class DataLoader:
    def __init__(self, input_data, expit_payload_transactions):
        self.data = input_data
        self.expit_payload_transactions = expit_payload_transactions

    def load_data(self):
        """Loads data from Excel and returns it in structured format."""
        xls = pd.ExcelFile(self.data)

        # Load sheets into DataFrames
        grade_block_data_df = pd.read_excel(xls, 'final_input_grade_block_data')
        equipment_data_df = pd.read_excel(xls, 'final_input_equipment_data')
        crusher_data_df = pd.read_excel(xls, 'final_input_crusher_data')

        # Convert each DataFrame to a list of dictionaries for easy access
        stockpile_data = self.process_stockpile_data()
        stockpile_data_objects = self.create_stockpile_data_objects(stockpile_data)

        grade_block_data = grade_block_data_df.to_dict(orient='records')
        grade_block_objects = self.create_grade_block_data_objects(grade_block_data)

        equipment_data = equipment_data_df.to_dict(orient='records')
        equipment_data_objects = self.create_equipment_data_objects(equipment_data)


        # Structure crusher target data as a nested dictionary
        crusher_target_data = {
            "preplan": {
                "target_fe_min": crusher_data_df["target_fe_min_preplan"].iloc[0],
                "target_fe_max": crusher_data_df["target_fe_max_preplan"].iloc[0],
                "target_si_min": crusher_data_df["target_si_min_preplan"].iloc[0],
                "target_si_max": crusher_data_df["target_si_max_preplan"].iloc[0],
                "target_al_min": crusher_data_df["target_al_min_preplan"].iloc[0],
                "target_al_max": crusher_data_df["target_al_max_preplan"].iloc[0],
                "target_p_min": crusher_data_df["target_p_min_preplan"].iloc[0],
                "target_p_max": crusher_data_df["target_p_max_preplan"].iloc[0],
                "target_mn_min": crusher_data_df["target_mn_min_preplan"].iloc[0],
                "target_mn_max": crusher_data_df["target_mn_max_preplan"].iloc[0],
                "direct_feed_ratio_min": crusher_data_df["direct_feed_ratio_min_preplan"].iloc[0],
                "direct_feed_ratio_max": crusher_data_df["direct_feed_ratio_max_preplan"].iloc[0],
                "crusher_rate": crusher_data_df["crusher_rate_preplan"].iloc[0]
            },
            "period_1": {
                "target_fe_min": crusher_data_df["target_fe_min_period_1"].iloc[0],
                "target_fe_max": crusher_data_df["target_fe_max_period_1"].iloc[0],
                "target_si_min": crusher_data_df["target_si_min_period_1"].iloc[0],
                "target_si_max": crusher_data_df["target_si_max_period_1"].iloc[0],
                "target_al_min": crusher_data_df["target_al_min_period_1"].iloc[0],
                "target_al_max": crusher_data_df["target_al_max_period_1"].iloc[0],
                "target_p_min": crusher_data_df["target_p_min_period_1"].iloc[0],
                "target_p_max": crusher_data_df["target_p_max_period_1"].iloc[0],
                "target_mn_min": crusher_data_df["target_mn_min_period_1"].iloc[0],
                "target_mn_max": crusher_data_df["target_mn_max_period_1"].iloc[0],
                "direct_feed_ratio_min": crusher_data_df["direct_feed_ratio_min_period_1"].iloc[0],
                "direct_feed_ratio_max": crusher_data_df["direct_feed_ratio_max_period_1"].iloc[0],
                "crusher_rate": crusher_data_df["crusher_rate_period_1"].iloc[0]
            },
            "period_2": {
                "target_fe_min": crusher_data_df["target_fe_min_period_2"].iloc[0],
                "target_fe_max": crusher_data_df["target_fe_max_period_2"].iloc[0],
                "target_si_min": crusher_data_df["target_si_min_period_2"].iloc[0],
                "target_si_max": crusher_data_df["target_si_max_period_2"].iloc[0],
                "target_al_min": crusher_data_df["target_al_min_period_2"].iloc[0],
                "target_al_max": crusher_data_df["target_al_max_period_2"].iloc[0],
                "target_p_min": crusher_data_df["target_p_min_period_2"].iloc[0],
                "target_p_max": crusher_data_df["target_p_max_period_2"].iloc[0],
                "target_mn_min": crusher_data_df["target_mn_min_period_2"].iloc[0],
                "target_mn_max": crusher_data_df["target_mn_max_period_2"].iloc[0],
                "direct_feed_ratio_min": crusher_data_df["direct_feed_ratio_min_period_2"].iloc[0],
                "direct_feed_ratio_max": crusher_data_df["direct_feed_ratio_max_period_2"].iloc[0],
                "crusher_rate": crusher_data_df["crusher_rate_period_2"].iloc[0]
            }
        }

        return stockpile_data_objects, grade_block_objects, equipment_data_objects, crusher_target_data
    
    def process_stockpile_data(self):
             
        # Load stockpile data
        xls = pd.ExcelFile(self.data)
        stockpile_data_df = pd.read_excel(xls, 'final_input_stockpile_data')
        stockpile_data = stockpile_data_df.to_dict(orient='records')

        for record in stockpile_data:
            # Initialize auto_turnover_datetime to None
            record['auto_turnover_datetime'] = None
            record['is_ready'] = None

        for record in stockpile_data:

            # Filter transactions related to the current stockpile
            stockpile_id = record['name']
            stockpile_transactions = self.expit_payload_transactions[
                self.expit_payload_transactions["destination"].str.replace("Stockpiles/", "", regex=False) == stockpile_id
            ].to_dict(orient='records')


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
            total_balance = record['balance'] + total_payload
            record['is_ready'] = total_balance >= record['reclaim_threshold']

            # If ready, update auto_turnover_datetime
            if record['is_ready']:
                record['auto_turnover_datetime'] = latest_datetime
        
        return stockpile_data
    
    def create_equipment_data_objects(self, equipment_data_dicts):
        return [
            EquipmentData(
                name=record["name"],
                priority_preplan=record["priority_preplan"],
                priority_period_1=record["priority_period_1"],
                priority_period_2=record["priority_period_2"],
                rate_preplan=record["rate_preplan"],
                rate_period_1=record["rate_period_1"],
                rate_period_2=record["rate_period_2"]
            )
            for record in equipment_data_dicts
        ]
    
    def create_stockpile_data_objects(self, stockpile_data_dicts):
        """Create a list of StockpileData objects from a list of dictionaries."""
        return [
            StockpileData(
                name=record["name"],
                balance=record["balance"],
                state_preplan=record["state_preplan"],
                state_period_1=record["state_period_1"],
                state_period_2=record["state_period_2"],
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
                reclaim_threshold=record["reclaim_threshold"],
                grade_fe=record["grade_fe"],
                grade_si=record["grade_si"],
                grade_al=record["grade_al"],
                grade_p=record["grade_p"],
                grade_mn=record["grade_mn"],
                auto_turnover_datetime=record["auto_turnover_datetime"],
                is_ready=record["is_ready"]
            )
            for record in stockpile_data_dicts
        ]

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
