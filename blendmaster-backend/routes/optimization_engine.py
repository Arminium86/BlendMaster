from routes.optimization_logic import run_blending_optimization
from time_handler import depletion_time_tracker

def run_with_dynamic_steady_state(event_pool, period_crusher_target, periods, current_time):
    # Step 1: Set the initial steady state duration
    
    if current_time >= periods["preplan_start"] and current_time < periods["preplan_end"]: 
        steady_state_duration = (periods["preplan_end"] - current_time).total_seconds() / 3600
    elif current_time >= periods["period_1_start"] and current_time < periods["period_1_end"]:
        steady_state_duration = (periods["period_1_end"] - current_time).total_seconds() / 3600 
    elif current_time >= periods["period_2_start"] and current_time < periods["period_2_end"]:
        steady_state_duration = (periods["period_2_end"] - current_time).total_seconds() / 3600
    else: steady_state_duration = 0
    
    # Step 2: Run the initial optimization
    result = run_blending_optimization(event_pool, period_crusher_target, steady_state_duration)
    
    if result["status"] == "success":
        # Step 3: Check for early depletion using the actual selected tonnes from the result
        updated_steady_state_duration = depletion_time_tracker(result["outcome"], steady_state_duration, result["optimal_tonnes"])
        
        
        # Steady state duration was updated
        steady_state_duration = updated_steady_state_duration
        result = run_blending_optimization(event_pool, period_crusher_target, steady_state_duration)

    return result











