# Import necessary classes from your module (assuming all classes are in a module named blend_master)
from classes.CaseModeller import CaseModeller
from execute.DataLoader import DataLoader
from execute.PeriodManager import PeriodManager

# Load data
data_loader = DataLoader(r"C:\BlendMaster\blendmaster_backend_OOP\input\data.xlsx")
stockpile_data, grade_block_data, equipment_data, crusher_target_data = data_loader.load_data()

# Initialize periods
periods = PeriodManager().calculate_periods()

# Initialize and run CaseModeller
case_modeller = CaseModeller(
    stockpiles=stockpile_data,
    grade_blocks=grade_block_data,
    equipment=equipment_data,
    crusher_targets=crusher_target_data,
    periods=periods
)

case_modeller.run()
