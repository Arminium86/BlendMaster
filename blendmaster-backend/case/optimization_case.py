import sys
import os
import pandas as pd
from datetime import timedelta

# Add the project root directory to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), r'C:\BlendMaster\blendmaster-backend\routes')))

from routes.optimization_engine import run_with_dynamic_steady_state
from routes.event_generator import generate_event_pool
from routes.crusher_targets import crusher_targets
from routes.time_handler import calculate_periods
from case.input import stockpile_data, grade_block_data, crusher_target_data, equipment_data

import pandas as pd
from datetime import timedelta

# Importing from case.input based on the file structure you provided
from case.input import stockpile_data, grade_block_data, crusher_target_data, equipment_data

def case_run_blending_optimization():
    # Initialize the balance tracker with both stockpile and grade block data
    balance_tracker = {item["name"]: item["balance"] for item in stockpile_data + grade_block_data}
    period_tracker = "preplan"
    periods = calculate_periods()
    current_time = periods["preplan_start"]
    event_pool = generate_event_pool(stockpile_data, equipment_data, grade_block_data, period_tracker)
    period_crusher_target = crusher_targets(crusher_target_data, period_tracker)

    # Initialize DataFrame to store all outcomes
    all_outcomes_df = pd.DataFrame()

    while current_time < periods["period_2_end"]:
        # Update opening balance in event_pool for each stockpile and grade block independently
        for event in event_pool:
            # Set balance for stockpile if present in the event
            if "stockpile" in event:
                event["balance"] = balance_tracker.get(event["stockpile"], 0)
            
            # Set balance for grade block if present in the event
            if "grade_block" in event:
                event["balance"] = balance_tracker.get(event["grade_block"], 0)
        
        # Run blending optimization with updated event pool
        result = run_with_dynamic_steady_state(event_pool, period_crusher_target, periods, current_time)
        
        if result["status"] != "success":
            print("Optimization failed; exiting loop.")
            break

        steady_state_duration = float(result["steady state duration"])

        # Prepare data for the current iteration's outcome
        outcome_data = []
        for event in result["outcome"]:
            # Initialize data_entry with all possible fields, setting defaults
           
            source_name = event["Source"].strip()
            actual_source_tonnes= float(event["Actual Tonnes (Reclaimed)"])
            balance_tracker[source_name] -= actual_source_tonnes # Update balance
            
            report = {
                "start_datetime": current_time,
                "end_datetime": current_time + timedelta(hours=steady_state_duration),
                "steady_state_duration": steady_state_duration,
                "period": period_tracker,
                "source": event["Source"],
                "opening_balance": balance_tracker.get(source_name, 0) + actual_source_tonnes,
                "actual_tonnes": actual_source_tonnes,
                "remaining_tonnes":  balance_tracker.get(source_name, 0),
                "grade_fe": float(event["Grade Fe"]),
                "equipment": event["Equipment"].strip(),
                "equipment_rate_input": float(event["Equipment Rate (Input)"]),
                "equipment_rate_output": float(event["Equipment Actual Rate"])
            }

            outcome_data.append(report)

        # Update DataFrame with the outcome for the current iteration
        outcome_df = pd.DataFrame(outcome_data)
        all_outcomes_df = pd.concat([all_outcomes_df, outcome_df], ignore_index=True)

        # Update current time
        current_time += timedelta(hours=steady_state_duration)

        # Adjust period and event pool for the new period if needed
        if current_time >= periods["preplan_end"] and period_tracker == "preplan":
            period_tracker = "period_1"
        elif current_time >= periods["period_1_end"] and period_tracker == "period_1":
            period_tracker = "period_2"

        # Re-generate event pool with updated balances
        event_pool = generate_event_pool(stockpile_data, equipment_data, grade_block_data, period_tracker)
        period_crusher_target = crusher_targets(crusher_target_data, period_tracker)

    # Write the cumulative outcomes to Excel at the end
    all_outcomes_df.to_excel(r"C:\BlendMaster\blendmaster-backend\output\outcome_data.xlsx", index=False)
    print("All results written to outcome_data.xlsx.")


# Run the function
if __name__ == "__main__":
    case_run_blending_optimization()
