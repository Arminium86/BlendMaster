# This is the main optimization engine and logic
from scipy.optimize import linprog
import numpy as np

class Optimizer:

    def run_with_dynamic_steady_state(self, event_pool, period_crusher_target, steady_state_duration):
        """Runs blending optimization and adjusts steady state if needed."""
        
        steady_state_controller_source = None
        steady_state_controller_tonnes = None

        result = self.run_blending_optimization(event_pool, period_crusher_target, steady_state_duration, steady_state_controller_source, steady_state_controller_tonnes)
        
        if result['Linprog_result_object'].success: 
            steady_state_duration, steady_state_controller_source, steady_state_controller_tonnes = self.update_steady_state_duration(result["transactions"], steady_state_duration)
            result = self.run_blending_optimization(event_pool, period_crusher_target, steady_state_duration, steady_state_controller_source, steady_state_controller_tonnes)

            if result['Linprog_result_object'].success: 
                return result
            
            elif not result['Linprog_result_object'].success: 
                steady_state_controller_source, steady_state_controller_tonnes = None, None
                result = self.run_blending_optimization(event_pool, period_crusher_target, steady_state_duration, steady_state_controller_source, steady_state_controller_tonnes)

                if result['Linprog_result_object'].success: 
                    return result
                
                else: return

        else: return

    @staticmethod
    def update_steady_state_duration(selected_events, steady_state_duration):
        """Update steady state duration if any source is depleted early."""

        updated_duration = steady_state_duration
        source_name = "Null"
        source_tonnes = "Null"

        for selected_event in selected_events:
            if (selected_event["actual_tonnes"] == selected_event["opening_balance"] and selected_event["actual_tonnes"] > 0):
                actual_tonnes = selected_event["actual_tonnes"]  
                rate = selected_event["equipment_rate_input"]  

                # Calculate time to depletion based on the actual selected tonnes
                time_to_depletion = float(actual_tonnes / rate)

                # If a source will deplete sooner than the current steady state duration, then update the steady state duration
                if time_to_depletion < updated_duration and time_to_depletion >= 0.016666667:
                    updated_duration = time_to_depletion
                    source_name = selected_event["source"]
                    source_tonnes = selected_event["opening_balance"]
                else: updated_duration =  0.016666667
                source_name = selected_event["source"]
                source_tonnes = selected_event["opening_balance"]

        return updated_duration, source_name, source_tonnes
    
    @staticmethod
    def run_blending_optimization(event_pool, period_crusher_target, steady_state_duration, steady_state_controller_source, steady_state_controller_tonnes: float):
        """Core linear optimization logic."""
        dmc = -10  # Default movement cash flow in $/tonne (negative value for linprog to minimize)

        # Step 1: Define bounds (how many tonnes each event contributes)
        bounds = [(0, min(event["rate"] * steady_state_duration, event["balance"])) for event in event_pool]
        
        # Step 2: Build the cost and constraints based on event pool
        c = []  # Movement cash flow for each event

        # Equality constraint is only used when there is source that is depleted early in a steady state. This tries to force that source to deplete fully
        # in a subsequent, updated (shortened) steady state. There is a fail safe mechanism in the run_with_dynamic_steady_state method should this rigid
        # constraint fail
        if (steady_state_controller_source != None and steady_state_controller_source != "Null"):
            indices = [i for i, event in enumerate(event_pool) if ((event["type"] == "stockpile" and event["stockpile"] == steady_state_controller_source and "grade_block" not in event) or (event["type"] == "grade_block" and event["grade_block"] == steady_state_controller_source and "stockpile" not in event))]
            A_eq = [[1 if i in indices else 0 for i in range(len(event_pool))]] 
            b_eq = [steady_state_controller_tonnes] * len(A_eq)

        else:
            A_eq = None
            b_eq = None

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
        b_ub = [period_crusher_target["crusher_rate"] * steady_state_duration]  # Must be <= crusher rate * steady state duration

        # Step 4: Add a constraint for grade block to stockpile feed ratio
        stockpile_indices = [i for i, event in enumerate(event_pool) if "stockpile" in event]
        
        # Maximum ratio of grade block to stockpile feed (use second value below. 0 means no constraint. 10 means max 0.1 grade block / stockpile feed)
        A_ub_max_feed_ratio = [[-1 if i in stockpile_indices else 5 for i in range(len(event_pool))]] 
        b_ub_max_feed_ratio = [0] 

        # Minimum ratio of grade block to stockpile feed (use first value below. 0 means no constraint. 0.1 means min 0.1 grade block / stockpile feed)
        A_ub_min_feed_ratio = [[0 if i in stockpile_indices else -1 for i in range(len(event_pool))]]
        b_ub_min_feed_ratio = [0]  

        # Step 6: Run the optimization
        
        if A_eq == None and b_eq == None:
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
        
        else:
            result = linprog(c,
                        A_eq=A_eq, 
                        b_eq=b_eq,  
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
            
            transactions = []
            for i, event in enumerate(event_pool):
                if result.x[i] >= 0:  # Check if the event has happened
                    transactions.append({
                    "source": event.get('stockpile', event.get('grade_block')),
                    "opening_balance": event['balance'],
                    "actual_tonnes": result.x[i],
                    "grade_fe": event['grade_fe'],
                    "equipment": event['equipment'],
                    "equipment_rate_input": event['rate'],
                    "equipment_rate_output": result.x[i] / steady_state_duration if steady_state_duration != 0 else 0,
                })

            return {
                "Linprog_result_object": result,
                "transactions": transactions,
                "steady_state_duration": steady_state_duration, 
                "crusher_actual_grade_fe" : sum(event["grade_fe"] * result.x[i] for i, event in enumerate(event_pool)) / sum(result.x) if sum(result.x) != 0 else "",
                "crusher_grade_target_min_fe": period_crusher_target["target_fe_min"],
                "crusher_grade_target_max_fe": period_crusher_target["target_fe_max"],
                "crusher_rate_input": period_crusher_target["crusher_rate"],
                "crusher_rate_output": sum(result.x) / steady_state_duration if steady_state_duration != 0 else 0,
                "crusher_actual_tonnes": sum(result.x)
            }

        else:
            return {"Linprog_result_object": result}