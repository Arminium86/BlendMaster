# This is the control centre in which user and inventory data are imported and the program is executed
# Import necessary classes from your modules
from classes.CaseModeller import CaseModeller
from execute.DataLoader import DataLoader
from execute.PeriodManager import PeriodManager
from execute.ExpitDataHandler import ExpitDataHandler

# Load input data (this is combined user input and opening inventories)
input_data = DataLoader(r"C:\BlendMaster\blendmaster_backend_OOP\input\data.xlsx")
expit_data_handler = ExpitDataHandler(filepath=r"C:\BlendMaster\blendmaster_backend_OOP\input\data.xlsx", sheet_name="aps_transactions")
expit_payload_transactions = expit_data_handler.process_transactions()
stockpile_data, grade_block_data, equipment_data, crusher_target_data = input_data.load_data()

# Initialize periods
periods = PeriodManager().calculate_periods()

# Initialize and run CaseModeller
case_modeller = CaseModeller(
    stockpiles=stockpile_data,
    grade_blocks=grade_block_data,
    equipment=equipment_data,
    crusher_targets=crusher_target_data,
    expit_payload_transactions=expit_payload_transactions,
    periods=periods
)

case_modeller.run()
