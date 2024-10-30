from scipy.optimize import linprog

# Main blending optimization logic
def run_blending_optimization(event_pool, period_crusher_target, steady_state_duration):

    dmc = -5  # Default movement cash in $/tonne

    # Step 5: Define bounds (how much tonnage each event contributes)
    bounds = [(0, min(event["rate"] * steady_state_duration, event["balance"])) for event in event_pool]
    
    # Step 2: Build the cost and constraints based on event pool
    c = []  # Movement cost for each event
    A_eq = [[event["grade_fe"] - period_crusher_target["target_fe_max"] for event in event_pool]]
    b_eq = [0]  # The difference between both sides should equal 0

    # Minimum crusher grade (turned into an upper-bound inequality)
    A_ub_min_crusher_grade = [[-event["grade_fe"] + period_crusher_target["target_fe_min"] for event in event_pool]]  # Multiply by -1 to enforce "greater than or equal to"
    b_ub_min_crusher_grade = [0]

    # Max crusher grade (upper-bound inequality)
    A_ub_max_crusher_grade = [[event["grade_fe"] - period_crusher_target["target_fe_max"] for event in event_pool]]
    b_ub_max_crusher_grade = [0]


    for event in event_pool:
        # Movement cost = dmc + combined priority of stockpile / grade block and reclaimer / digger
        c.append(dmc + event["priority"])

    # Step 3: Crusher capacity constraint
    A_ub = [[1] * len(event_pool)]  # Sum of all events' tonnes
    b_ub = [period_crusher_target["crusher_rate"] * steady_state_duration]  # Must be <= crusher rate * duration

    # Step 4: Add stockpile selection constraint (minimum 2, maximum 3 stockpiles)
    stockpile_indices = [i for i, event in enumerate(event_pool) if "stockpile" in event]
    
    # Minimum 2 stockpiles constraint (turned into an upper-bound inequality)
    A_ub_min_stockpiles = [[-1 if i in stockpile_indices else 0 + 2 for i in range(len(event_pool))]] # Min 2 stockpiles
    b_ub_min_stockpiles = [0] 

    # Maximum 3 stockpiles constraint
    A_ub_max_stockpiles = [[1 if i in stockpile_indices else 0 - 3 for i in range(len(event_pool))]] # Max 3 stockpiles
    b_ub_max_stockpiles = [0]  


    # Step 3.1: Grade block contribution constraint (grade_block <= 20% of total tonnes)
    grade_block_indices = [i for i, event in enumerate(event_pool) if "grade_block" in event]  # Identify grade block events

    # A new upper-bound inequality for the grade blocks
    A_ub_grade_block = [[1 if i in grade_block_indices else -1 for i in range(len(event_pool))]]
    b_ub_grade_block = [0]  # Grade blocks must be <= 100% of total tonnes

 

    # Step 6: Run the optimization with the added stockpile constraints
    result = linprog(c, 
                     #A_eq=A_eq, 
                     #b_eq=b_eq, 
                     A_ub=A_ub
                     + A_ub_min_stockpiles 
                     + A_ub_max_stockpiles 
                     + A_ub_min_crusher_grade 
                     + A_ub_max_crusher_grade
                     + A_ub_grade_block,  
                     b_ub=b_ub
                     + b_ub_min_stockpiles 
                     + b_ub_max_stockpiles 
                     + b_ub_min_crusher_grade 
                     + b_ub_max_crusher_grade
                     + b_ub_grade_block, 
                     bounds=bounds, method='highs')

   
    
    if result.success:
        
        outcome = []
        for i, event in enumerate(event_pool):
            if result.x[i] > 0:  # Check if the event's tonnage is greater than zero
                outcome.append({
                "event_number": i+1,
                "Source": f"{event.get('stockpile', event.get('grade_block'))}\n",
                "Opening Balance": f"{event['balance']}\n",
                "Actual Tonnes (Reclaimed)": f"{result.x[i]}\n",
                "Grade Fe": f"{event['grade_fe']}\n",
                "Equipment": f"{event['equipment']}\n",
                "Equipment Rate (Input)": f"{event['rate']}\n",
                "Equipment Actual Rate": f"{result.x[i] / steady_state_duration}\n",
                "rate": event["rate"],  # Ensure the rate is passed along for depletion tracking
                "balance": event["balance"]  # Keep balance for further reference
            })

        return {
            "status": "success", 
            "outcome": outcome,  # Return the selected events here
            "steady state duration": steady_state_duration, 
            "optimal_tonnes": result.x,
            "Actual Crusher Fe Grade" : sum(event["grade_fe"] * result.x[i] for i, event in enumerate(event_pool)) / sum(result.x) if sum(result.x) != 0 else "No tonnes selected.",
            "Crusher Fe Grade Target (Min)": period_crusher_target["target_fe_min"],
            "Crusher Fe Grade Target (max)": period_crusher_target["target_fe_max"],
            "Actual Crusher Tonnes": sum(result.x)
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
