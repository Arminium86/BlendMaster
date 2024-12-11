# This is the control centre in which user and inventory data are imported and the program is executed
# Import necessary classes from your modules
from classes.CaseModeller import CaseModeller
from execute.DataLoader import DataLoader
from execute.PeriodManager import PeriodManager
from execute.ExpitDataHandler import ExpitDataHandler
from datetime import datetime

# Initialize periods
periods = PeriodManager().calculate_periods()

# Process APS expit data (mining.csv)
expit_data_handler = ExpitDataHandler(r"C:\BlendMaster\blendmaster_backend_OOP\input\data.xlsx", "aps_transactions")
expit_payload_transactions = expit_data_handler.process_transactions()

# User interaction required to choose between original time and updated time methods
user_interaction_mode = input("\033[92mEnter 1 to execute original expit payload transactions or 2 to update transactions based on current time and dig block (this will account for load agent delays or breakdowns): \033[0m")

# Cast user choice to appropriate type
try:
    user_interaction_mode = int(user_interaction_mode)
except ValueError:
    print("Invalid input. Please enter a number.")

if user_interaction_mode == 2:
    #now = datetime.now()
    now = datetime(2024, 11, 28, 6, 0, 0)
    expit_payload_transactions = expit_data_handler.update_transactions(expit_payload_transactions, now)
    expit_payload_transactions.to_excel(fr"C:\BlendMaster\blendmaster_backend_OOP\output\expit_payload_transactions.xlsx")

elif user_interaction_mode == 1:
    expit_payload_transactions.to_excel(fr"C:\BlendMaster\blendmaster_backend_OOP\output\expit_payload_transactions.xlsx")

else: print("Invalid input. Please enter a number.")

# Load input data (this is combined user input and opening inventories)
input_data = DataLoader(r"C:\BlendMaster\blendmaster_backend_OOP\input\data.xlsx", expit_payload_transactions)

stockpile_data_objects, grade_block_data_objects, equipment_data_objects, crusher_target_data = input_data.load_data()


# Initialize and run CaseModeller
case_modeller = CaseModeller(
    stockpiles=stockpile_data_objects,
    grade_blocks=grade_block_data_objects,
    equipment=equipment_data_objects,
    crusher_targets=crusher_target_data,
    expit_payload_transactions=expit_payload_transactions,
    periods=periods
)

case_modeller.run()
