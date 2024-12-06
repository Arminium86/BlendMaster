# This is the main optimization engine and logic
from scipy.optimize import linprog
import numpy as np
from datetime import timedelta

class Optimizer:

    def run_with_dynamic_steady_state(self, event_pool, period_crusher_target, steady_state_duration, periods, period_tracker, current_time, stockpile_data):
        """Runs blending optimization and adjusts steady state if needed."""
        
        steady_state_controller_source = None
        steady_state_controller_tonnes = None

        result = self.run_blending_optimization(event_pool, period_crusher_target, steady_state_duration, steady_state_controller_source, steady_state_controller_tonnes, periods, period_tracker)
        
        if result['Linprog_result_object'].success: 
            steady_state_duration, steady_state_controller_source, steady_state_controller_tonnes = self.update_steady_state_duration(result["transactions"], steady_state_duration, current_time, stockpile_data)
            result = self.run_blending_optimization(event_pool, period_crusher_target, steady_state_duration, steady_state_controller_source, steady_state_controller_tonnes, periods, period_tracker)

            if result['Linprog_result_object'].success: 
                return result
            
            elif not result['Linprog_result_object'].success: 
                steady_state_controller_source, steady_state_controller_tonnes = None, None
                result = self.run_blending_optimization(event_pool, period_crusher_target, steady_state_duration, steady_state_controller_source, steady_state_controller_tonnes, periods, period_tracker)

                if result['Linprog_result_object'].success: 
                    return result
                
                else: return result

        else: return result

    @staticmethod
    def update_steady_state_duration(selected_events, steady_state_duration, start_of_steady_state_datetime, stockpile_data):
        """Update steady state duration if any source is depleted early."""
        end_of_steady_state_datetime = start_of_steady_state_datetime + timedelta(hours=steady_state_duration)
        updated_duration = steady_state_duration
        updated_duration_auto_turnover = steady_state_duration
        source_name = "Null"
        source_tonnes = "Null"

        for selected_event in selected_events:
            if (selected_event["actual_tonnes"] == selected_event["opening_balance"] and selected_event["actual_tonnes"] > 0):
                actual_tonnes = selected_event["actual_tonnes"]  
                rate = selected_event["equipment_rate_input"]  

                # Calculate time to depletion based on the actual selected tonnes
                time_to_depletion = float(actual_tonnes / rate)

                # If a source will deplete sooner than the current steady state duration, then update the steady state duration
                if time_to_depletion < updated_duration and time_to_depletion > 0.016666667:
                    updated_duration = time_to_depletion
                    source_name = selected_event["source"]
                    source_tonnes = selected_event["opening_balance"]

                elif time_to_depletion > updated_duration: 
                    continue

                else: 
                    updated_duration =  0.016666667 # Min steady state duration is 1 minute (this else block should be reached in rare cases)
                    source_name = selected_event["source"]
                    source_tonnes = selected_event["opening_balance"]

 

        for stockpile in stockpile_data:
            if (stockpile['auto_turnover_datetime']) != None:
               if start_of_steady_state_datetime < (stockpile['auto_turnover_datetime']) < end_of_steady_state_datetime:
                   time_to_turnover = (stockpile['auto_turnover_datetime'] - start_of_steady_state_datetime).total_seconds() / 3600
                   if time_to_turnover < updated_duration_auto_turnover:
                       updated_duration_auto_turnover = time_to_turnover

                   else: continue

               else: continue

            else: continue

        if updated_duration <= updated_duration_auto_turnover:
            return updated_duration, source_name, source_tonnes
        else: return updated_duration_auto_turnover, "Null", "Null"
    
    @staticmethod
    def run_blending_optimization(event_pool, period_crusher_target, steady_state_duration, steady_state_controller_source, steady_state_controller_tonnes, periods, period_tracker):
        """Core linear optimization logic."""
        dmc = -100  # Default movement cash flow in $/tonne (negative value for linprog to minimize)

        # Step 1: Define bounds (how many tonnes each event contributes)
        bounds = [(0, min(event["rate"] * steady_state_duration, event["balance"])) for event in event_pool]
        
        # Step 2: Build the cost and constraints based on event pool
        c = []  # Movement cash flow for each event
        for event in event_pool:
            # Movement cash flow = dmc + combined priority (think about this value as a $/tonne cost) of stockpile / grade block and reclaimer / digger
            c.append(dmc + event["cost"] + event["cash"])

        # Equality constraint is only used when there is source that is depleted early in a steady state. This tries to force that source to deplete fully
        # in a subsequent, updated (shortened) steady state. There is a fail safe mechanism in the run_with_dynamic_steady_state method should this rigid
        # constraint fail the optimization
        if (steady_state_controller_source != None and steady_state_controller_source != "Null"):
            indices = [i for i, event in enumerate(event_pool) if ((event["type"] == "stockpile" and event["stockpile"] == steady_state_controller_source and "grade_block" not in event) or (event["type"] == "grade_block" and event["grade_block"] == steady_state_controller_source and "stockpile" not in event))]
            A_eq = [[1 if i in indices else 0 for i in range(len(event_pool))]] 
            b_eq = [steady_state_controller_tonnes] * len(A_eq)

        else:
            A_eq = None
            b_eq = None

        # Minimum crusher grade (turned into an upper-bound inequality)
        A_ub_min_crusher_grade_fe = [[-event["grade_fe"] + period_crusher_target["target_fe_min"] for event in event_pool]]  # Multiply by -1 to enforce "greater than or equal to"
        b_ub_min_crusher_grade_fe = [0]
        
        A_ub_min_crusher_grade_si = [[-event["grade_si"] + period_crusher_target["target_si_min"] for event in event_pool]]  # Multiply by -1 to enforce "greater than or equal to"
        b_ub_min_crusher_grade_si = [0]

        A_ub_min_crusher_grade_al = [[-event["grade_al"] + period_crusher_target["target_al_min"] for event in event_pool]]  # Multiply by -1 to enforce "greater than or equal to"
        b_ub_min_crusher_grade_al = [0]

        A_ub_min_crusher_grade_p = [[-event["grade_p"] + period_crusher_target["target_p_min"] for event in event_pool]]  # Multiply by -1 to enforce "greater than or equal to"
        b_ub_min_crusher_grade_p = [0]

        A_ub_min_crusher_grade_mn = [[-event["grade_mn"] + period_crusher_target["target_mn_min"] for event in event_pool]]  # Multiply by -1 to enforce "greater than or equal to"
        b_ub_min_crusher_grade_mn = [0]

        # Max crusher grade (upper-bound inequality)
        A_ub_max_crusher_grade_fe = [[event["grade_fe"] - period_crusher_target["target_fe_max"] for event in event_pool]]
        b_ub_max_crusher_grade_fe = [0]

        A_ub_max_crusher_grade_si = [[event["grade_si"] - period_crusher_target["target_si_max"] for event in event_pool]]
        b_ub_max_crusher_grade_si = [0]

        A_ub_max_crusher_grade_al = [[event["grade_al"] - period_crusher_target["target_al_max"] for event in event_pool]]
        b_ub_max_crusher_grade_al = [0]

        A_ub_max_crusher_grade_p = [[event["grade_p"] - period_crusher_target["target_p_max"] for event in event_pool]]
        b_ub_max_crusher_grade_p = [0]

        A_ub_max_crusher_grade_mn = [[event["grade_mn"] - period_crusher_target["target_mn_max"] for event in event_pool]]
        b_ub_max_crusher_grade_mn = [0]

        # Step 3: Crusher capacity constraint
        A_ub = [[1] * len(event_pool)]  # Sum of all events' tonnes
        b_ub = [period_crusher_target["crusher_rate"] * steady_state_duration]  # Must be <= crusher rate * steady state duration

        # Generate a list of indicies for each unique stockpile
        unique_stockpiles = {}
        stockpile_indices = []

        for i, event in enumerate(event_pool):
            stockpile_name = event.get("stockpile")
            if "stockpile" in event and stockpile_name not in unique_stockpiles:
                unique_stockpiles[stockpile_name] = i
                stockpile_indices.append(i)

        # Step 4: Add a constraint for grade block to stockpile feed ratio
        # Maximum ratio of grade block to stockpile feed (use second value below. 0 means no constraint. 10 means max 0.1 grade block / stockpile feed)

        direct_feed_ratio_max = period_crusher_target["direct_feed_ratio_max"]
        if direct_feed_ratio_max >= 1 or direct_feed_ratio_max < 0:

            A_ub_max_feed_ratio = [[-1 if i in stockpile_indices else 0 for i in range(len(event_pool))]] 
            b_ub_max_feed_ratio = [0] 
        
        elif direct_feed_ratio_max == 0:
           
            A_ub_max_feed_ratio = [[0 if i in stockpile_indices else 1 for i in range(len(event_pool))]] 
            b_ub_max_feed_ratio = [0] 
       
        else:
            stockpile_coef = -direct_feed_ratio_max * 10
            grade_block_coef = 10 + stockpile_coef
            A_ub_max_feed_ratio = [[stockpile_coef if i in stockpile_indices else grade_block_coef for i in range(len(event_pool))]] 
            b_ub_max_feed_ratio = [0]          

        # Minimum ratio of grade block to stockpile feed (use first value below. 0 means no constraint. 0.1 means min 0.1 grade block / stockpile feed)
        direct_feed_ratio_min = period_crusher_target["direct_feed_ratio_min"]
        if direct_feed_ratio_min <= 0 or direct_feed_ratio_min > 1:
            
            A_ub_min_feed_ratio = [[0 if i in stockpile_indices else -1 for i in range(len(event_pool))]] 
            b_ub_min_feed_ratio = [0] 
       
        elif direct_feed_ratio_min == 1:
           
            A_ub_min_feed_ratio = [[1 if i in stockpile_indices else 0 for i in range(len(event_pool))]] 
            b_ub_min_feed_ratio = [0] 

        else:
            stockpile_coef = direct_feed_ratio_max * 10
            grade_block_coef = -10 + stockpile_coef
            A_ub_min_feed_ratio = [[stockpile_coef if i in stockpile_indices else grade_block_coef for i in range(len(event_pool))]] 
            b_ub_min_feed_ratio = [0]   

        # Step 5: Maximum quantities for each source
        # Generate a list of indicies for each unique source
        unique_sources = {}
        source_indices = []

        for i, event in enumerate(event_pool):
            source_name = event.get("stockpile") if event['type'] == "stockpile" else event.get("grade_block")
            if source_name not in unique_sources:
                unique_sources[source_name] = i
                source_indices.append(i)

        # Inequality constraint to handle max quantity
        A_ub_max_quantity = []
        b_ub_max_quantity = []

        for event_index in range(len(event_pool)):
            # Create a row filled with zeros
            A_ub_row = [0] * len(event_pool)
            
            # Set the diagonal element (where row index matches column index)
            if event_index in source_indices:
                A_ub_row[event_index] = 1 / steady_state_duration
                
            A_ub_max_quantity.append(A_ub_row)
        
        for event in event_pool:
            # Create the corresponding entry for b_ub for this event
            b_ub_max_quantity.append(event["max_quantity"] / periods[f"{period_tracker}_duration"])

        # Step 6: Run the optimization
        
        if A_eq == None and b_eq == None:
            result = linprog(c,
                            A_ub=A_ub
                            + A_ub_min_feed_ratio
                            + A_ub_max_feed_ratio
                            + A_ub_min_crusher_grade_fe 
                            + A_ub_max_crusher_grade_fe
                            + A_ub_min_crusher_grade_si 
                            + A_ub_max_crusher_grade_si
                            + A_ub_min_crusher_grade_al 
                            + A_ub_max_crusher_grade_al
                            + A_ub_min_crusher_grade_p 
                            + A_ub_max_crusher_grade_p
                            + A_ub_min_crusher_grade_mn 
                            + A_ub_max_crusher_grade_mn
                            + A_ub_max_quantity,
                            b_ub=b_ub
                            + b_ub_min_feed_ratio
                            + b_ub_max_feed_ratio
                            + b_ub_min_crusher_grade_fe 
                            + b_ub_max_crusher_grade_fe
                            + b_ub_min_crusher_grade_si
                            + b_ub_max_crusher_grade_si
                            + b_ub_min_crusher_grade_al 
                            + b_ub_max_crusher_grade_al
                            + b_ub_min_crusher_grade_p 
                            + b_ub_max_crusher_grade_p
                            + b_ub_min_crusher_grade_mn 
                            + b_ub_max_crusher_grade_mn
                            + b_ub_max_quantity, 
                            bounds=bounds, method='highs')
        
        else:
            result = linprog(c,
                        A_eq=A_eq, 
                        b_eq=b_eq,  
                        A_ub=A_ub
                        + A_ub_min_feed_ratio
                        + A_ub_max_feed_ratio
                        + A_ub_min_crusher_grade_fe 
                        + A_ub_max_crusher_grade_fe
                        + A_ub_min_crusher_grade_si 
                        + A_ub_max_crusher_grade_si
                        + A_ub_min_crusher_grade_al 
                        + A_ub_max_crusher_grade_al
                        + A_ub_min_crusher_grade_p 
                        + A_ub_max_crusher_grade_p
                        + A_ub_min_crusher_grade_mn 
                        + A_ub_max_crusher_grade_mn
                        + A_ub_max_quantity,  
                        b_ub=b_ub
                        + b_ub_min_feed_ratio
                        + b_ub_max_feed_ratio
                        + b_ub_min_crusher_grade_fe 
                        + b_ub_max_crusher_grade_fe
                        + b_ub_min_crusher_grade_si
                        + b_ub_max_crusher_grade_si
                        + b_ub_min_crusher_grade_al 
                        + b_ub_max_crusher_grade_al
                        + b_ub_min_crusher_grade_p 
                        + b_ub_max_crusher_grade_p
                        + b_ub_min_crusher_grade_mn 
                        + b_ub_max_crusher_grade_mn
                        + b_ub_max_quantity, 
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
                    "grade_si": event['grade_si'],
                    "grade_al": event['grade_al'],
                    "grade_p": event['grade_p'],
                    "grade_mn": event['grade_mn'],
                    "equipment": event['equipment'],
                    "equipment_rate_input": event['rate'],
                    "equipment_rate_output": result.x[i] / steady_state_duration if steady_state_duration != 0 else 0,
                })

            return {
                "Linprog_result_object": result,
                "transactions": transactions,
                "steady_state_duration": steady_state_duration, 
                "crusher_actual_grade_fe" : sum(event["grade_fe"] * result.x[i] for i, event in enumerate(event_pool)) / sum(result.x) if sum(result.x) != 0 else "",
                "crusher_actual_grade_si" : sum(event["grade_si"] * result.x[i] for i, event in enumerate(event_pool)) / sum(result.x) if sum(result.x) != 0 else "",
                "crusher_actual_grade_al" : sum(event["grade_al"] * result.x[i] for i, event in enumerate(event_pool)) / sum(result.x) if sum(result.x) != 0 else "",
                "crusher_actual_grade_p" : sum(event["grade_p"] * result.x[i] for i, event in enumerate(event_pool)) / sum(result.x) if sum(result.x) != 0 else "",
                "crusher_actual_grade_mn" : sum(event["grade_mn"] * result.x[i] for i, event in enumerate(event_pool)) / sum(result.x) if sum(result.x) != 0 else "",
                "crusher_grade_target_min_fe": period_crusher_target["target_fe_min"],
                "crusher_grade_target_max_fe": period_crusher_target["target_fe_max"],
                "crusher_grade_target_min_si": period_crusher_target["target_si_min"],
                "crusher_grade_target_max_si": period_crusher_target["target_si_max"],
                "crusher_grade_target_min_al": period_crusher_target["target_al_min"],
                "crusher_grade_target_max_al": period_crusher_target["target_al_max"],
                "crusher_grade_target_min_p": period_crusher_target["target_p_min"],
                "crusher_grade_target_max_p": period_crusher_target["target_p_max"],
                "crusher_grade_target_min_mn": period_crusher_target["target_mn_min"],
                "crusher_grade_target_max_mn": period_crusher_target["target_mn_max"],
                "crusher_rate_input": period_crusher_target["crusher_rate"],
                "crusher_rate_output": sum(result.x) / steady_state_duration if steady_state_duration != 0 else 0,
                "crusher_actual_tonnes": sum(result.x)
            }

        else:
            return {"Linprog_result_object": result,
                    "steady_state_duration": steady_state_duration
            }