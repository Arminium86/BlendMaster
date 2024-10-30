from routes.optimization_logic import run_blending_optimization
from time_handler import depletion_time_tracker

def run_with_dynamic_steady_state(event_pool, period_crusher_target, period, periods):
    # Step 1: Set the initial steady state duration
    if period == "preplan":
        steady_state_duration = (periods["preplan_end"] - periods["preplan_start"]).total_seconds() / 3600
    elif period == "period_1":
        steady_state_duration = 12
    elif period == "period_2":
        steady_state_duration = 12
    
    # Step 2: Run the initial optimization
    result = run_blending_optimization(event_pool, period_crusher_target, steady_state_duration)
    
    if result["status"] == "success":
        # Step 3: Check for early depletion using the actual selected tonnes from the result
        updated_steady_state_duration = depletion_time_tracker(result["outcome"], steady_state_duration, result["optimal_tonnes"])
        
        
        # Steady state duration was updated
        steady_state_duration = updated_steady_state_duration
        result = run_blending_optimization(event_pool, period_crusher_target, steady_state_duration)

    return result











