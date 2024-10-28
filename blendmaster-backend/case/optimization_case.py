import sys
import os
import pandas as pd
# Add the project root directory to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), r'C:\BlendMaster\blendmaster-backend\routes')))

from routes.optimization_engine import run_with_dynamic_steady_state
from routes.event_generator import generate_event_pool
from routes.crusher_targets import crusher_targets
from routes.time_handler import calculate_periods
from case.input import stockpile_data, grade_block_data, crusher_target_data, equipment_data


# Case for blending optimization
def case_run_blending_optimization():

    periods = calculate_periods()
    event_pool = generate_event_pool(stockpile_data, equipment_data, grade_block_data, "preplan")
    period_crusher_target = crusher_targets(crusher_target_data, "preplan")
    result = run_with_dynamic_steady_state(event_pool, period_crusher_target, "preplan", periods)
   
    print(f"Preplan and periods calculated successfully: {periods}\n")
    print(f"status: {result['status']}\n")
    print(f"steady state duration: {result['steady state duration']}\n")
    print(f"Actual Crusher Tonnes: {result['Actual Crusher Tonnes']}\n")
    print(f"Actual Crusher Fe Grade: {result['Actual Crusher Fe Grade']}\n")
    print(f"Crusher Fe Grade Target (Min): {result['Crusher Fe Grade Target (Min)']}\n")
    print(f"Crusher Fe Grade Target (max): {result['Crusher Fe Grade Target (max)']}\n")
    for event in result["outcome"]:
        print(f"Event {event['event_number']}:\n{event['details']}")
    
 # Prepare data to write into a CSV
    outcome_data = []
    for event in result["outcome"]:
        outcome_data.append({
            "event_number": event["event_number"],
            "details": event["details"]
        })
    
    # Convert outcome data to DataFrame
    outcome_df = pd.DataFrame(outcome_data)
    
    # Add other key result data to the DataFrame if needed
    result_summary = {
        "status": result["status"],
        "steady_state_duration": result["steady state duration"],
        "actual_crusher_tonnes": result["Actual Crusher Tonnes"],
        "actual_crusher_fe_grade": result["Actual Crusher Fe Grade"],
        "crusher_fe_grade_target_min": result["Crusher Fe Grade Target (Min)"],
        "crusher_fe_grade_target_max": result["Crusher Fe Grade Target (max)"]
    }

    # Convert result_summary to DataFrame and concatenate with outcome_df if necessary
    # You can also save them separately if you prefer
    summary_df = pd.DataFrame([result_summary])
    
    # Write the outcome and result summary to a CSV file
    outcome_df.to_csv(r"C:\BlendMaster\blendmaster-backend\output\outcome_data.csv", index=False)
    summary_df.to_csv(r"C:\BlendMaster\blendmaster-backend\output\result_summary.csv", index=False)


    
    print("Results successfully written to CSV files.")

# Call your case functions
if __name__ == "__main__":
    case_run_blending_optimization()
