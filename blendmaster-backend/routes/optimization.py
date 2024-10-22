from scipy.optimize import linprog
from datetime import datetime, timedelta

# Define the periods (preplan, Period 1, Period 2)
from datetime import datetime, timedelta

def calculate_periods():
    now = datetime.now()
    
    # Define next 6AM and 6PM, adjusting for current time
    if now.hour >= 18:  # If it's after 6PM, the next 6AM is the following day
        next_6am = now.replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
    else:
        next_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
    
    if now.hour >= 6 and now.hour < 18:  # If it's between 6AM and 6PM, the next 6PM is today
        next_6pm = now.replace(hour=18, minute=0, second=0, microsecond=0)
    else:
        next_6pm = now.replace(hour=18, minute=0, second=0, microsecond=0) + timedelta(days=1)

    # Preplan ends at the closest 6AM or 6PM
    preplan_end = min(next_6am, next_6pm)

    # Period 1 and Period 2 follow the preplan period
    period_1_start = preplan_end
    period_1_end = period_1_start + timedelta(hours=12)
    period_2_start = period_1_end
    period_2_end = period_2_start + timedelta(hours=12)

    return {
        "preplan_start": now,
        "preplan_end": preplan_end,
        "period_1_start": period_1_start,
        "period_1_end": period_1_end,
        "period_2_start": period_2_start,
        "period_2_end": period_2_end
    }

# Function to calculate additional movement cost based on stockpile priority
def calculate_stockpile_movement_cost(priority):
    return priority  # The additional cost increases with lower priority (e.g., priority 1 has the least cost)

# Simulate stockpile depletion and trigger new steady state
def track_stockpile_depletion(stockpile_data, reclaim_rates, steady_state_duration):
    for stockpile in stockpile_data:
        reclaim_rate = reclaim_rates[stockpile["id"]]
        time_to_depletion = stockpile["tonnage"] / reclaim_rate
        if time_to_depletion < steady_state_duration:
            return time_to_depletion  # Trigger new steady state when a stockpile runs out
    return steady_state_duration

# Main blending optimization logic
def run_blending_optimization(stockpile_data, grade_blocks, reclaimer_data, digger_data, user_inputs, period):
    dmc = 1  # Default movement cost in $/tonne

    # Reclaimer and digger rates by period
    if period == "preplan":
        reclaim_rates = {rc["id"]: rc["rate_preplan"] for rc in reclaimer_data}
        digger_rates = {dg["id"]: dg["rate_preplan"] for dg in digger_data}
    elif period == "period_1":
        reclaim_rates = {rc["id"]: rc["rate_period_1"] for rc in reclaimer_data}
        digger_rates = {dg["id"]: dg["rate_period_1"] for dg in digger_data}
    elif period == "period_2":
        reclaim_rates = {rc["id"]: rc["rate_period_2"] for rc in reclaimer_data}
        digger_rates = {dg["id"]: dg["rate_period_2"] for dg in digger_data}

    # Filter out stockpiles and grade blocks based on rules
    filtered_stockpiles = [
        sp for sp in stockpile_data if sp["priority"] >= 0 and sp["use"] and sp["tonnage"] >= sp["reclaim_threshold"]
    ]
    filtered_grade_blocks = [gb for gb in grade_blocks if gb["use"]]

    # Objective function (minimize movement cost)
    c = [dmc + calculate_stockpile_movement_cost(sp["priority"]) for sp in filtered_stockpiles]
    c.extend([dmc for _ in filtered_grade_blocks])  # Grade blocks have no additional cost

    # Bounds (tonnage from stockpiles and grade blocks)
    stockpile_bounds = [(0, sp["tonnage"]) for sp in filtered_stockpiles]
    grade_block_bounds = [(0, gb["tonnage"]) for gb in filtered_grade_blocks]
    bounds = stockpile_bounds + grade_block_bounds

    # Constraints for grades (example: Fe)
    A_eq = [...]  # (Define your grade constraints here based on stockpile grades)
    b_eq = [...]  # (Define your grade targets here)

    # Track depletion and adjust steady state
    adjusted_steady_state_duration = track_stockpile_depletion(filtered_stockpiles, reclaim_rates, user_inputs["steady_state_duration"])

    # Run the optimization
    result = linprog(c, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method='highs')

    if result.success:
        return {"status": "success", "optimal_tonnages": result.x, "steady_state_duration": adjusted_steady_state_duration}
    else:
        return {"status": "error", "message": "Optimization failed"}
