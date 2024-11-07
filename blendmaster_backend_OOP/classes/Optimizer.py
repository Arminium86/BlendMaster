from scipy.optimize import linprog

class Optimizer:
    def run_with_dynamic_steady_state(self, event_pool, period_crusher_target, steady_state_duration):
        """Runs blending optimization and adjusts steady state if needed."""
        
        while steady_state_duration > 0:
            
            result = self.run_blending_optimization(event_pool, period_crusher_target, steady_state_duration)
        
            if result['result'].success and sum(result['result'].x) > 0: 
                steady_state_duration = self.update_steady_state_duration(result["outcome"], steady_state_duration, result["optimal_tonnes"])
                result = self.run_blending_optimization(event_pool, period_crusher_target, steady_state_duration)
                break

            elif result['result'].success and sum(result['result'].x)  == 0:
                steady_state_duration -= 1
                continue 

            else: break
             
        return result

    @staticmethod
    def update_steady_state_duration(selected_events, steady_state_duration, selected_tonnes):
        """Update steady state duration if depletion is detected early."""

        updated_duration = steady_state_duration
        
        for i, event in enumerate(selected_events):
            if (selected_tonnes[i] == event["Opening Balance"] and selected_tonnes[i] > 0):
                actual_tonnes = selected_tonnes[i]  # Actual tonnes selected by the solver
                rate = event["Equipment Rate (Input)"]  # Equipment rate for reclaim or digging

                # Calculate time to depletion based on the actual selected tonnes
                time_to_depletion = actual_tonnes / rate

                # If the event will deplete sooner than the current steady state, update the steady state duration
                if time_to_depletion < updated_duration and time_to_depletion >= 0.016666667:
                    updated_duration = time_to_depletion
                else: return 0.016666667
        
        return updated_duration

    def run_blending_optimization(self, event_pool, period_crusher_target, steady_state_duration):
        """Core linear optimization logic."""
        dmc = -10  # Default movement cash flow in $/tonne (negative value for linprog to minimize)

        # Step 1: Define bounds (how many tonnes each event contributes)
        bounds = [(0, min(event["rate"] * steady_state_duration, event["balance"])) for event in event_pool]
        
        # Step 2: Build the cost and constraints based on event pool
        c = []  # Movement cash flow for each event

        # Minimum crusher grade (turned into an upper-bound inequality)
        A_ub_min_crusher_grade = [[-event["grade_fe"] + period_crusher_target["target_fe_min"] for event in event_pool]]  # Multiply by -1 to enforce "greater than or equal to"
        b_ub_min_crusher_grade = [0]

        # Max crusher grade (upper-bound inequality)
        A_ub_max_crusher_grade = [[event["grade_fe"] - period_crusher_target["target_fe_max"] for event in event_pool]]
        b_ub_max_crusher_grade = [0]

        for event in event_pool:
            # Movement cash flow = dmc + combined priority (think about this value as a $/tonne cost) of stockpile / grade block and reclaimer / digger
            c.append(dmc + event["priority"])

        # Step 3: Crusher capacity constraint
        A_ub = [[1] * len(event_pool)]  # Sum of all events' tonnes
        b_ub = [period_crusher_target["crusher_rate"] * steady_state_duration]  # Must be <= crusher rate * duration

        # Step 4: Add stockpile selection constraint (minimum 2, maximum 3 stockpiles)
        stockpile_indices = [i for i, event in enumerate(event_pool) if "stockpile" in event]
        
        # Maximum ratio of grade block to stockpile feed (use second value. 0 means no constraint. 10 means max 0.1 grade block / stockpile feed)
        A_ub_max_feed_ratio = [[-1 if i in stockpile_indices else 5 for i in range(len(event_pool))]] 
        b_ub_max_feed_ratio = [0] 

        # Minimum ratio of grade block to stockpile feed (use first value. 0 means no constraint. 0.1 means min 0.1 grade block / stockpile feed)  )
        A_ub_min_feed_ratio = [[0 if i in stockpile_indices else -1 for i in range(len(event_pool))]]
        b_ub_min_feed_ratio = [0]  

        # Step 6: Run the optimization with the added stockpile constraints
        result = linprog(c, 
                        A_ub=A_ub
                        + A_ub_min_feed_ratio
                        + A_ub_max_feed_ratio
                        + A_ub_min_crusher_grade 
                        + A_ub_max_crusher_grade,  
                        b_ub=b_ub
                        + b_ub_min_feed_ratio
                        + b_ub_max_feed_ratio
                        + b_ub_min_crusher_grade 
                        + b_ub_max_crusher_grade, 
                        bounds=bounds, method='highs')

        if result.success:
            
            outcome = []
            for i, event in enumerate(event_pool):
                if result.x[i] > 0:  # Check if the event's tonnage is greater than zero
                    outcome.append({
                    "event_number": i+1,
                    "Source": event.get('stockpile', event.get('grade_block')),
                    "Opening Balance": event['balance'],
                    "Actual Tonnes (Reclaimed)": result.x[i],
                    "Remaining Tonnes": float(event['balance']) - float(result.x[i]),
                    "Grade Fe": event['grade_fe'],
                    "Equipment": event['equipment'],
                    "Equipment Rate (Input)": event['rate'],
                    "Equipment Actual Rate": result.x[i] / steady_state_duration if steady_state_duration != 0 else 0,
                })

            return {
                "result": result,
                "outcome": outcome,  # Return the selected events here
                "steady state duration": steady_state_duration, 
                "optimal_tonnes": result.x,
                "Actual Crusher Fe Grade" : sum(event["grade_fe"] * result.x[i] for i, event in enumerate(event_pool)) / sum(result.x) if sum(result.x) != 0 else "No tonnes selected.",
                "Crusher Fe Grade Target (Min)": period_crusher_target["target_fe_min"],
                "Crusher Fe Grade Target (max)": period_crusher_target["target_fe_max"],
                "Actual Crusher Tonnes": sum(result.x)
            }

        else:
            return {
                "result": result,
                "status": "error", 
                "message": result.message, 
                "objective_function_value": result.fun, 
                "slack": result.slack, 
                "residuals_equality_constraints": result.con
            }