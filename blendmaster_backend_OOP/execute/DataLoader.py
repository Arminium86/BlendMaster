# This loads the data from external sources (currently an Excel file with multiple tabs which represents the combined user input and opening inventories)
import pandas as pd

class DataLoader:
    def __init__(self, input_data):
        self.data = input_data

    def load_data(self):
        """Loads data from Excel and returns it in structured format."""
        xls = pd.ExcelFile(self.data)

        # Load sheets into DataFrames
        stockpile_data_df = pd.read_excel(xls, 'final_input_stockpile_data')
        grade_block_data_df = pd.read_excel(xls, 'final_input_grade_block_data')
        equipment_data_df = pd.read_excel(xls, 'final_input_equipment_data')
        crusher_data_df = pd.read_excel(xls, 'final_input_crusher_data')

        # Convert each DataFrame to a list of dictionaries for easy access
        stockpile_data = stockpile_data_df.to_dict(orient='records')
        grade_block_data = grade_block_data_df.to_dict(orient='records')
        equipment_data = equipment_data_df.to_dict(orient='records')

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

        return stockpile_data, grade_block_data, equipment_data, crusher_target_data