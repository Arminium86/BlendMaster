import pandas as pd

class DataLoader:
    def __init__(self, file_path):
        self.file_path = file_path

    def load_data(self):
        """Loads data from Excel and returns it in structured format."""
        xls = pd.ExcelFile(self.file_path)

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
                "crusher_rate": crusher_data_df["crusher_rate_preplan"].iloc[0]
            },
            "period_1": {
                "target_fe_min": crusher_data_df["target_fe_min_period_1"].iloc[0],
                "target_fe_max": crusher_data_df["target_fe_max_period_1"].iloc[0],
                "crusher_rate": crusher_data_df["crusher_rate_period_1"].iloc[0]
            },
            "period_2": {
                "target_fe_min": crusher_data_df["target_fe_min_period_2"].iloc[0],
                "target_fe_max": crusher_data_df["target_fe_max_period_2"].iloc[0],
                "crusher_rate": crusher_data_df["crusher_rate_period_2"].iloc[0]
            }
        }

        return stockpile_data, grade_block_data, equipment_data, crusher_target_data

data = DataLoader().load_data()
print (data)