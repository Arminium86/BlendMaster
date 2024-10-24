from scipy.optimize import linprog
from datetime import datetime, timedelta

# Define the periods (preplan, Period 1, Period 2)
from datetime import datetime, timedelta

def calculate_periods():
    now = datetime.now()
    
    # Define next 6AM and 6PM, adjusting for current time
    if now.hour >= 6 and now.hour < 18:  # Between 6AM and 6PM
        next_6am = now.replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
        next_6pm = now.replace(hour=18, minute=0, second=0, microsecond=0)

    elif now.hour >= 18:  # Between 6PM and midnight
        next_6am = now.replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
        next_6pm = now.replace(hour=18, minute=0, second=0, microsecond=0) + timedelta(days=1)

    else:  # Between midnight and 6AM
        next_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
        next_6pm = now.replace(hour=18, minute=0, second=0, microsecond=0)

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

def generate_event_pool(stockpile_data, equipment_data, grade_block_data, period):
    events = []

    # Loop through each stockpile and match it with all reclaimers it can be reclaimed by
    for stockpile in stockpile_data:
        stockpile_priority = stockpile[f"priority_{period}"]  # Get stockpile priority for the period
        for equipment in equipment_data:
            if equipment["name"] in stockpile["equipment"]:  # Based on movement rules
                equipment_priority = equipment[f"priority_{period}"]  # Get equipment priority for the period
                reclaim_rate = equipment[f"rate_{period}"]  # Get reclaim rate for the period
                
                events.append({
                    "stockpile": stockpile["name"],
                    "equipment": equipment["name"],
                    "priority": stockpile_priority + equipment_priority,  # Combined priority for cost minimization
                    "rate": reclaim_rate,  # Reclaim rate (time-dependent)
                    "grade_fe": stockpile["grade_fe"],
                    "balance": stockpile["balance"]
                })
    
    # Add direct tip opportunities from grade blocks
    for grade_block in grade_block_data:
        for equipment in equipment_data:
            if (equipment["name"] in grade_block["equipment"]) and ("EX" in equipment["name"]):  # Ensure only excavators (diggers) are used for grade blocks
                equipment_priority = equipment[f"priority_{period}"]  # Get digger priority for the period
                reclaim_rate = equipment[f"rate_{period}"]  # Get reclaim rate for the period

                events.append({
                    "grade_block": grade_block["name"],
                    "equipment": equipment["name"],
                    "priority": equipment_priority,  # Diggers have no stockpile priority
                    "rate": reclaim_rate,  # Reclaim rate (time-dependent)
                    "grade_fe": grade_block["grade_fe"],
                    "balance": grade_block["balance"]
                })

    return events

def crusher_targets(grade_targets, crusher_rates, period):
    # Get the Fe grade target for the current period
    fe_target_min = grade_targets[period]["target_fe_min"]
    fe_target_max = grade_targets[period]["target_fe_max"]
    # Get the crusher rate for the current period
    crusher_rate = crusher_rates["crusher_rate"][period]
    
    # Return both the Fe grade target and crusher rate
    return {
        "fe_target_min": fe_target_min,
        "fe_target_max": fe_target_max,
        "crusher_rate": crusher_rate
    }

# Main blending optimization logic
def run_blending_optimization(event_pool, crusher_target, period, periods):
    dmc = -5  # Default movement cash in $/tonne

    # Step 1: Extract time-dependent equipment rates
    if period == "preplan":
        steady_state_duration = (periods["preplan_end"] - periods["preplan_start"]).total_seconds() / 3600
    elif period == "period_1":
        steady_state_duration = 12
    elif period == "period_2":
        steady_state_duration = 12

    
    # Step 5: Define bounds (how much tonnage each event contributes)
    bounds = [(0, min(event["rate"] * steady_state_duration, event["balance"])) for event in event_pool]
    
    # Step 2: Build the cost and constraints based on event pool
    c = []  # Movement cost for each event
    A_eq = [[event["grade_fe"] - crusher_target["fe_target_max"] for event in event_pool]]
    b_eq = [0]  # The difference between both sides should equal 0

    # Minimum crusher grade (turned into an upper-bound inequality)
    A_ub_min_crusher_grade = [[-event["grade_fe"] + crusher_target["fe_target_min"] for event in event_pool]]  # Multiply by -1 to enforce "greater than or equal to"
    b_ub_min_crusher_grade = [0]

    # Max crusher grade (upper-bound inequality)
    A_ub_max_crusher_grade = [[event["grade_fe"] - crusher_target["fe_target_max"] for event in event_pool]]
    b_ub_max_crusher_grade = [0]


    for event in event_pool:
        # Movement cost = dmc + combined priority of stockpile / grade block and reclaimer / digger
        c.append(dmc + event["priority"])

    # Step 3: Crusher capacity constraint
    A_ub = [[1] * len(event_pool)]  # Sum of all events' tonnes
    b_ub = [crusher_target["crusher_rate"] * steady_state_duration]  # Must be <= crusher rate * duration

    # Step 4: Add stockpile selection constraint (minimum 2, maximum 3 stockpiles)
    stockpile_indices = [i for i, event in enumerate(event_pool) if "stockpile" in event]
    
    # Minimum 2 stockpiles constraint (turned into an upper-bound inequality)
    A_ub_min_stockpiles = [[-1 if i in stockpile_indices else 0 for i in range(len(event_pool))]]
    b_ub_min_stockpiles = [-2]  # At least 2 stockpiles

    # Maximum 3 stockpiles constraint
    A_ub_max_stockpiles = [[1 if i in stockpile_indices else 0 for i in range(len(event_pool))]]
    b_ub_max_stockpiles = [3]  # At most 3 stockpiles

 

    # Step 6: Run the optimization with the added stockpile constraints
    result = linprog(c, 
                     #A_eq=A_eq, 
                     #b_eq=b_eq, 
                     A_ub=A_ub
                     #+ A_ub_min_stockpiles 
                     #+ A_ub_max_stockpiles 
                     + A_ub_min_crusher_grade, 
                     #+ A_ub_max_crusher_grade, 
                     b_ub=b_ub
                     #+ b_ub_min_stockpiles 
                     #+ b_ub_max_stockpiles 
                     + b_ub_min_crusher_grade, 
                     #+ b_ub_max_crusher_grade, 
                     bounds=bounds, method='highs')

    if result.success:
        return {
            "status": "success", 
            "steady state duration:": steady_state_duration, 
            "optimal_tonnages": result.x,
            "Actual Fe Grade" : sum(event["grade_fe"] * result.x[i] for i, event in enumerate(event_pool)) / sum(result.x),
            "Crusher Fe Grade Target (Min)": crusher_target["fe_target_min"],
            "Crusher Fe Grade Target (max)": crusher_target["fe_target_max"],
            "Actual Tonnes": sum(result.x)
        }
    else:
        # Print useful debug information when optimization fails
        print(f"Optimization failed with message: {result.message}")
        print(f"Status: {result.status}")
        print(f"Objective function value: {result.fun}")
        print(f"Slack variables: {result.slack}")
        print(f"Residuals of equality constraints: {result.con}")
        
        return {
            "status": "error", 
            "message": result.message, 
            "objective_function_value": result.fun, 
            "slack": result.slack, 
            "residuals_equality_constraints": result.con
        }

# Simulate stockpile depletion and trigger new steady state
def track_stockpile_depletion(blend, reclaimer_rates, steady_state_duration):
    for stockpile in blend:
        reclaim_rate = reclaimer_rates[stockpile["reclaimer"]]
        time_to_depletion = stockpile["tonnage"] / reclaim_rate
        
        if time_to_depletion < steady_state_duration:
            return time_to_depletion  # Trigger a new steady state at this point
    
    return steady_state_duration  # No stockpile runs out before the steady state ends





