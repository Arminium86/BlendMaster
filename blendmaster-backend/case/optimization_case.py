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

def case_run_blending_optimization():
    # Initial setup
    period_tracker = "preplan"
    periods = calculate_periods()
    current_time = periods["preplan_start"]
    event_pool = generate_event_pool(stockpile_data, equipment_data, grade_block_data, period_tracker)
    period_crusher_target = crusher_targets(crusher_target_data, period_tracker)

    # Initialize an empty DataFrame to store all outcomes
    all_outcomes_df = pd.DataFrame()

    # Loop until the end of period 2
    while current_time < periods["period_2_end"]:
        # Run blending optimization
        result = run_with_dynamic_steady_state(event_pool, period_crusher_target, period_tracker, periods)
        
        if result["status"] != "success":
            print("Optimization failed; exiting loop.")
            break
        
        steady_state_duration = float(result["steady state duration"])
        
        # Print result for monitoring
        print(f"Period: {period_tracker}")
        print(f"Current Time: {current_time}")
        print(f"Status: {result['status']}")
        print(f"Steady state duration: {steady_state_duration}")
        print(f"Actual Crusher Tonnes: {result['Actual Crusher Tonnes']}")
        
        # Prepare outcome data for the current iteration
        outcome_data = []
        for event in result["outcome"]:
            outcome_data.append({
                "start_datetime": current_time,
                "end_datetime": current_time + timedelta(hours=steady_state_duration),
                "steady_state_duration": steady_state_duration,
                "source": event["Source"].strip() if isinstance(event["Source"], str) else event["Source"],
                "opening_balance": float(event["Opening Balance"]),
                "actual_tonnes": float(event["Actual Tonnes (Reclaimed)"]),
                "grade_fe": float(event["Grade Fe"]),
                "equipment": event["Equipment"].strip() if isinstance(event["Equipment"], str) else event["Equipment"],
                "equipment_rate_input": float(event["Equipment Rate (Input)"]),
                "equipment_actual_rate": float(event["Equipment Actual Rate"])
            })

        # Convert outcome data to DataFrame and append to the cumulative DataFrame
        outcome_df = pd.DataFrame(outcome_data)
        all_outcomes_df = pd.concat([all_outcomes_df, outcome_df], ignore_index=True)

        # Update `current_time`
        current_time += timedelta(hours=steady_state_duration)
        
        print(f"Results processed for period {period_tracker}.\n")

        # Determine new period_tracker based on `current_time`
        if current_time >= periods["preplan_end"] and period_tracker == "preplan":
            period_tracker = "period_1"
        elif current_time >= periods["period_1_end"] and period_tracker == "period_1":
            period_tracker = "period_2"

        # Update event pool and crusher target for the new period if needed
        event_pool = generate_event_pool(stockpile_data, equipment_data, grade_block_data, period_tracker)
        period_crusher_target = crusher_targets(crusher_target_data, period_tracker)

    # Write the cumulative outcomes to Excel once at the end
    all_outcomes_df.to_excel(r"C:\BlendMaster\blendmaster-backend\output\outcome_data.xlsx", index=False)
    print("All steady state results successfully written to outcome_data.xlsx.")

# Run the function
if __name__ == "__main__":
    case_run_blending_optimization()
