import pandas as pd

# Load the Excel file
file_path = r"C:\BlendMaster\file mappings\data.xlsx"  # Replace with the actual path to the Excel file
xls = pd.ExcelFile(file_path)

# Load each sheet into a DataFrame
equipment_data_df = pd.read_excel(xls, 'final_input_equipment_data')
crusher_data_df = pd.read_excel(xls, 'final_input_crusher_data')
grade_block_data_df = pd.read_excel(xls, 'final_input_grade_block_data')
stockpile_data_df = pd.read_excel(xls, 'final_input_stockpile_data')

# Convert the stockpile data to dictionary format
stockpile_data = stockpile_data_df.to_dict(orient='records')

# Convert the grade block data to dictionary format
grade_block_data = grade_block_data_df.to_dict(orient='records')

# Convert the equipment data to dictionary format
equipment_data = equipment_data_df.to_dict(orient='records')

# Convert the crusher target data into nested dictionary structure
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

# At this point, the data is available in the following structures:
# stockpile_data, grade_block_data, equipment_data, crusher_target_data
