"""Optimization engine for building blends.

This module previously relied on ``scipy.optimize.linprog`` which only
supported continuous variables.  In order to allow the user to restrict the
number of stockpiles that can be used in a blend we now require binary
decision variables.  The solver has therefore been migrated to `PuLP` which
provides a mixed integer programming interface.

The core linear logic remains the same, but additional binary variables are
introduced for each stockpile so that we can constrain the total number of
stockpiles selected in a blend.  The user can provide optional
``min_stockpiles`` and ``max_stockpiles`` values which are then enforced by the
optimizer.
"""

from datetime import timedelta
from typing import List, Optional
from types import SimpleNamespace

from pulp import (
    LpBinary,
    LpMinimize,
    LpProblem,
    LpStatus,
    LpVariable,
    PULP_CBC_CMD,
    lpSum,
)

from classes.StockpileData import StockpileData
from classes.EventData import EventData

class Optimizer:
    MIN_SELECTED_STOCKPILE_BLEND_RATIO = 0.01
    SOLUTION_TOLERANCE = 1e-6
    THROUGHPUT_REWARD_PER_TONNE = 1_000_000

    def run_with_dynamic_steady_state(
        self,
        event_pool: List[EventData],
        period_crusher_target,
        steady_state_duration,
        periods,
        period_tracker,
        current_time,
        stockpile_data: List[StockpileData],
        min_stockpiles: Optional[int] = None,
        max_stockpiles: Optional[int] = None,
        min_stockpile_contribution_ratio: Optional[float] = None,
    ):
        """Runs blending optimization and adjusts steady state if needed."""
        
        steady_state_controller_source = None
        steady_state_controller_tonnes = None

        result = self.run_blending_optimization(
            event_pool,
            period_crusher_target,
            steady_state_duration,
            steady_state_controller_source,
            steady_state_controller_tonnes,
            periods,
            period_tracker,
            min_stockpiles,
            max_stockpiles,
            min_stockpile_contribution_ratio,
        )
        
        if result['Linprog_result_object'].success: 
            steady_state_duration, steady_state_controller_source, steady_state_controller_tonnes = self.update_steady_state_duration(result["transactions"], steady_state_duration, current_time, stockpile_data, period_tracker)
            result = self.run_blending_optimization(
                event_pool,
                period_crusher_target,
                steady_state_duration,
                steady_state_controller_source,
                steady_state_controller_tonnes,
                periods,
                period_tracker,
                min_stockpiles,
                max_stockpiles,
                min_stockpile_contribution_ratio,
            )

            if result['Linprog_result_object'].success: 
                return result
            
            elif not result['Linprog_result_object'].success: 
                steady_state_controller_source, steady_state_controller_tonnes = None, None
                result = self.run_blending_optimization(
                    event_pool,
                    period_crusher_target,
                    steady_state_duration,
                    steady_state_controller_source,
                    steady_state_controller_tonnes,
                    periods,
                    period_tracker,
                    min_stockpiles,
                    max_stockpiles,
                    min_stockpile_contribution_ratio,
                )

                if result['Linprog_result_object'].success: 
                    return result
                
                else: return result

        else: return result

    @staticmethod
    def update_steady_state_duration(selected_events, steady_state_duration, start_of_steady_state_datetime, stockpile_data: List[StockpileData], period_tracker):
        """Update steady state duration if any source is depleted early."""
        end_of_steady_state_datetime = start_of_steady_state_datetime + timedelta(hours=steady_state_duration)
        updated_duration = steady_state_duration
        updated_duration_auto_turnover = steady_state_duration
        source_name = "Null"
        source_tonnes = "Null"

        for selected_event in selected_events:
            actual_tonnes = float(selected_event.get("actual_tonnes") or 0)
            opening_balance = float(selected_event.get("opening_balance") or 0)
            rate = float(selected_event.get("equipment_rate_input") or 0)
            balance_tolerance = max(0.01, abs(opening_balance) * 1e-6)
            depletes_source = (
                actual_tonnes > 0
                and opening_balance > 0
                and rate > 0
                and opening_balance - actual_tonnes <= balance_tolerance
            )

            if depletes_source:

                # Calculate time to depletion based on the source opening tonnes.
                time_to_depletion = float(opening_balance / rate)

                # If a source will deplete sooner than the current steady state duration, then update the steady state duration
                if time_to_depletion < updated_duration and time_to_depletion > 0.016666667:
                    updated_duration = time_to_depletion
                    source_name = selected_event["source"]
                    source_tonnes = opening_balance

                elif time_to_depletion > updated_duration: 
                    continue

                else: 
                    updated_duration =  0.016666667 # Min steady state duration is 1 minute (this else block should be reached in rare cases)
                    source_name = selected_event["source"]
                    source_tonnes = opening_balance

        for stockpile in stockpile_data:
            if (stockpile.auto_turnover_datetime != None and 
                (stockpile.to_dict().get(f"state_{period_tracker}", 0) == "Auto")):
               if start_of_steady_state_datetime < stockpile.auto_turnover_datetime <= end_of_steady_state_datetime:
                   time_to_turnover = (stockpile.auto_turnover_datetime - start_of_steady_state_datetime).total_seconds() / 3600
                   if time_to_turnover < updated_duration_auto_turnover:
                       updated_duration_auto_turnover = time_to_turnover

                   else: continue

               else: continue

            else: continue

        if updated_duration <= updated_duration_auto_turnover:
            return updated_duration, source_name, source_tonnes
        else: return updated_duration_auto_turnover, "Null", "Null"
    
    @staticmethod
    def run_blending_optimization(
        event_pool: List[EventData],
        period_crusher_target,
        steady_state_duration,
        steady_state_controller_source,
        steady_state_controller_tonnes,
        periods,
        period_tracker,
        min_stockpiles: Optional[int] = None,
        max_stockpiles: Optional[int] = None,
        min_stockpile_contribution_ratio: Optional[float] = None,
    ):
        """Core optimisation logic using a mixed integer solver."""

        if min_stockpile_contribution_ratio is None:
            min_stockpile_contribution_ratio = Optimizer.MIN_SELECTED_STOCKPILE_BLEND_RATIO

        if not 0.01 <= min_stockpile_contribution_ratio <= 1:
            raise ValueError("Min Stockpile Contribution Ratio must be between 0.01 and 1.")

        dmc = -100  # Default movement cash flow in $/tonne (negative for minimisation)

        # Step 1: Define bounds (how many tonnes each event contributes)
        bounds = [(0, min(event.rate * steady_state_duration, event.balance)) for event in event_pool]
        
        # Step 2: Build the cost and constraints based on event pool
        base_costs = []
        for event in event_pool:
            # Movement cash flow = dmc + combined priority (think about this value as a $/tonne cost) of stockpile / grade block and reclaimer / digger
            base_costs.append(dmc + event.cost + event.cash)

        throughput_reward = max(
            Optimizer.THROUGHPUT_REWARD_PER_TONNE,
            (max((abs(cost) for cost in base_costs), default=0) + 1) * 1000,
        )
        c = [cost - throughput_reward for cost in base_costs]

        # Equality constraint is only used when there is source that is depleted early in a steady state. This tries to force that source to deplete fully
        # in a subsequent, updated (shortened) steady state. There is a fail safe mechanism in the run_with_dynamic_steady_state method should this rigid
        # constraint fail the optimization
        if (steady_state_controller_source != None and steady_state_controller_source != "Null"):
            indices = [i for i, event in enumerate(event_pool) if ((event.is_stockpile and event.stockpile == steady_state_controller_source) or (event.is_grade_block and event.grade_block == steady_state_controller_source))]
            A_eq = [[1 if i in indices else 0 for i in range(len(event_pool))]] 
            b_eq = [steady_state_controller_tonnes] * len(A_eq)

        else:
            A_eq = None
            b_eq = None

        # Minimum crusher grade (turned into an upper-bound inequality)
        A_ub_min_crusher_grade_fe = [[-event.grade_fe + period_crusher_target["target_fe_min"] for event in event_pool]]  # Multiply by -1 to enforce "greater than or equal to"
        b_ub_min_crusher_grade_fe = [0]
        
        A_ub_min_crusher_grade_si = [[-event.grade_si + period_crusher_target["target_si_min"] for event in event_pool]]  # Multiply by -1 to enforce "greater than or equal to"
        b_ub_min_crusher_grade_si = [0]

        A_ub_min_crusher_grade_al = [[-event.grade_al + period_crusher_target["target_al_min"] for event in event_pool]]  # Multiply by -1 to enforce "greater than or equal to"
        b_ub_min_crusher_grade_al = [0]

        A_ub_min_crusher_grade_p = [[-event.grade_p + period_crusher_target["target_p_min"] for event in event_pool]]  # Multiply by -1 to enforce "greater than or equal to"
        b_ub_min_crusher_grade_p = [0]

        A_ub_min_crusher_grade_mn = [[-event.grade_mn + period_crusher_target["target_mn_min"] for event in event_pool]]  # Multiply by -1 to enforce "greater than or equal to"
        b_ub_min_crusher_grade_mn = [0]

        # Max crusher grade (upper-bound inequality)
        A_ub_max_crusher_grade_fe = [[event.grade_fe - period_crusher_target["target_fe_max"] for event in event_pool]]
        b_ub_max_crusher_grade_fe = [0]

        A_ub_max_crusher_grade_si = [[event.grade_si - period_crusher_target["target_si_max"] for event in event_pool]]
        b_ub_max_crusher_grade_si = [0]

        A_ub_max_crusher_grade_al = [[event.grade_al - period_crusher_target["target_al_max"] for event in event_pool]]
        b_ub_max_crusher_grade_al = [0]

        A_ub_max_crusher_grade_p = [[event.grade_p - period_crusher_target["target_p_max"] for event in event_pool]]
        b_ub_max_crusher_grade_p = [0]

        A_ub_max_crusher_grade_mn = [[event.grade_mn - period_crusher_target["target_mn_max"] for event in event_pool]]
        b_ub_max_crusher_grade_mn = [0]

        # Step 3: Crusher capacity constraint
        A_ub = [[1] * len(event_pool)]  # Sum of all events' tonnes
        b_ub = [period_crusher_target["crusher_rate"] * steady_state_duration]  # Must be <= crusher rate * steady state duration

        # Generate a list of indicies for each unique stockpile
        stockpile_event_indices = {}
        stockpile_indices = []

        for i, event in enumerate(event_pool):
            stockpile_name = event.stockpile
            if event.is_stockpile:
                stockpile_event_indices.setdefault(stockpile_name, []).append(i)
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
            stockpile_coef = direct_feed_ratio_min * 10
            grade_block_coef = -10 * (1 - direct_feed_ratio_min)
            A_ub_min_feed_ratio = [[stockpile_coef if i in stockpile_indices else grade_block_coef for i in range(len(event_pool))]]
            b_ub_min_feed_ratio = [0]

        # Step 5: Maximum quantities for each source
        # Generate a list of indicies for each unique source
        unique_sources = {}
        source_indices = []

        for i, event in enumerate(event_pool):
            source_name = event.stockpile if event.is_stockpile else event.grade_block
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
            b_ub_max_quantity.append(event.max_quantity / periods.get_periods()[f"{period_tracker}_duration"])

        # Step 6: Run the optimization using PuLP

        # Combine all inequality constraints and bounds
        A_ub_total = (
            A_ub
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
            + A_ub_max_quantity
        )

        b_ub_total = (
            b_ub
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
            + b_ub_max_quantity
        )

        prob = LpProblem("blending", LpMinimize)
        x_vars = [
            LpVariable(f"x_{i}", lowBound=0, upBound=bounds[i][1])
            for i in range(len(event_pool))
        ]

        # Objective function
        prob += lpSum(c[i] * x_vars[i] for i in range(len(event_pool)))

        # Inequality constraints
        for row, rhs in zip(A_ub_total, b_ub_total):
            prob += lpSum(row[i] * x_vars[i] for i in range(len(event_pool))) <= rhs

        # Equality constraints if applicable
        if A_eq is not None and b_eq is not None:
            for row, rhs in zip(A_eq, b_eq):
                prob += lpSum(row[i] * x_vars[i] for i in range(len(event_pool))) == rhs

        # Binary variables to control the number of stockpiles selected
        if stockpile_event_indices and (min_stockpiles is not None or max_stockpiles is not None):
            y_vars = {}
            total_feed = lpSum(x_vars)
            max_total_feed = period_crusher_target["crusher_rate"] * steady_state_duration

            for stockpile_name, indices in stockpile_event_indices.items():
                y_var = LpVariable(f"y_{stockpile_name}", cat=LpBinary)
                y_vars[stockpile_name] = y_var
                stockpile_feed = lpSum(x_vars[i] for i in indices)
                stockpile_feed_upper_bound = sum(bounds[i][1] for i in indices)

                prob += stockpile_feed <= stockpile_feed_upper_bound * y_var
                prob += (
                    stockpile_feed
                    >= min_stockpile_contribution_ratio * total_feed
                    - max_total_feed * (1 - y_var)
                )

            if min_stockpiles is not None:
                prob += lpSum(y_vars.values()) >= min_stockpiles
            if max_stockpiles is not None:
                prob += lpSum(y_vars.values()) <= max_stockpiles

        # Solve the problem
        prob.solve(PULP_CBC_CMD(msg=False))

        success = LpStatus[prob.status] == "Optimal"
        solution_values = [
            0.0 if abs(var.value() or 0.0) < Optimizer.SOLUTION_TOLERANCE else var.value()
            for var in x_vars
        ]
        result = SimpleNamespace(success=success, x=solution_values)

        if result.success:

            transactions = []
            for i, event in enumerate(event_pool):
                if result.x[i] >= 0:
                    transactions.append(
                        {
                            "source": event.to_dict().get(
                                "stockpile", event.to_dict().get("grade_block")
                            ),
                            "opening_balance": event.balance,
                            "actual_tonnes": result.x[i],
                            "grade_fe": event.grade_fe,
                            "grade_si": event.grade_si,
                            "grade_al": event.grade_al,
                            "grade_p": event.grade_p,
                            "grade_mn": event.grade_mn,
                            "equipment": event.equipment,
                            "equipment_rate_input": event.rate,
                            "equipment_rate_output": result.x[i]
                            / steady_state_duration
                            if steady_state_duration != 0
                            else 0,
                        }
                    )

            return {
                "Linprog_result_object": result,
                "transactions": transactions,
                "steady_state_duration": steady_state_duration,
                "crusher_actual_grade_fe": sum(
                    event.grade_fe * result.x[i] for i, event in enumerate(event_pool)
                )
                / sum(result.x)
                if sum(result.x) != 0
                else "",
                "crusher_actual_grade_si": sum(
                    event.grade_si * result.x[i] for i, event in enumerate(event_pool)
                )
                / sum(result.x)
                if sum(result.x) != 0
                else "",
                "crusher_actual_grade_al": sum(
                    event.grade_al * result.x[i] for i, event in enumerate(event_pool)
                )
                / sum(result.x)
                if sum(result.x) != 0
                else "",
                "crusher_actual_grade_p": sum(
                    event.grade_p * result.x[i] for i, event in enumerate(event_pool)
                )
                / sum(result.x)
                if sum(result.x) != 0
                else "",
                "crusher_actual_grade_mn": sum(
                    event.grade_mn * result.x[i]
                    for i, event in enumerate(event_pool)
                )
                / sum(result.x)
                if sum(result.x) != 0
                else "",
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
                "crusher_rate_output": sum(result.x) / steady_state_duration
                if steady_state_duration != 0
                else 0,
                "crusher_actual_tonnes": sum(result.x),
            }

        else:
            return {
                "Linprog_result_object": result,
                "steady_state_duration": steady_state_duration,
            }
