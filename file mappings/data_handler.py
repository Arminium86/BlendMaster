import pandas as pd

# Define file path
file_path = r"C:\BlendMaster\file mappings\data.xlsx"

# Load Excel workbook and specific sheets
with pd.ExcelFile(file_path) as xls:
    user_df = xls.parse('user_input_equipment_data')
    final_crusher_df = xls.parse('final_input_crusher_rates')

# Step 1: Identify columns to transform
# Assuming the user_df has period-related columns like 'crusher_rate_preplan', 'crusher_rate_period_1', etc.
# Filter columns in user_df that are related to crusher rates
crusher_rate_columns = [col for col in user_df.columns if 'crusher_rate' in col]

# Debugging Step: Ensure we have correctly identified the crusher_rate columns
print(f"Identified crusher rate columns in user input: {crusher_rate_columns}")

# Step 2: Match these columns with those in final_crusher_df
matching_cols = [col for col in crusher_rate_columns if col in final_crusher_df.columns]

# Debugging Step: Ensure we have correctly matched the columns with final_crusher_df
print(f"Matching columns found in final input: {matching_cols}")

# Step 3: Populate the final crusher rates DataFrame
for col in matching_cols:
    final_crusher_df[col] = user_df[col]

# Debugging Step: Check if final_crusher_df is being populated correctly
print(final_crusher_df.head())

# Step 4: Save updated data to the same Excel workbook in `final_input_crusher_rates` sheet
with pd.ExcelWriter(file_path, mode='a', engine='openpyxl', if_sheet_exists='replace') as writer:
    final_crusher_df.to_excel(writer, sheet_name='final_input_crusher_rates', index=False)

print("Transformation and saving complete.")
