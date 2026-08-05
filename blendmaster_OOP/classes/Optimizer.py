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
from copy import deepcopy
import time
from typing import List, Optional
from types import SimpleNamespace

import pandas as pd

from pulp import (
    LpBinary,
    LpMinimize,
    LpProblem,
    LpStatus,
    LpVariable,
    PULP_CBC_CMD,
    lpSum,
    value,
)

from classes.StockpileData import StockpileData
from classes.EventData import EventData
from classes.GradeStreams import DEFAULT_STREAM, apply_selected_stream
from classes.CustomConstraints import (
    CustomConstraintError,
    constraint_key,
    constraint_report_fields,
    custom_constraint_coefficients,
    filter_source_properties,
    scale_additive_source_properties,
    source_property_balance_report_fields,
    source_property_report_fields,
)


class RetryingCBCSolver(PULP_CBC_CMD):
    """CBC solver that tolerates Windows file-handle cleanup races.

    CBC can finish solving while another short-lived process still has the
    generated MPS/SOL file open.  PuLP normally deletes those files
    immediately and lets the resulting ``PermissionError`` abort the run.
    Retrying briefly preserves the solver result; if Windows still reports a
    lock, leaving the temporary file behind is safe and preferable to failing
    an otherwise completed optimisation.
    """

    def delete_tmp_files(self, *args):
        for attempt in range(5):
            try:
                return super().delete_tmp_files(*args)
            except PermissionError:
                if attempt == 4:
                    return
                time.sleep(0.1 * (attempt + 1))

class Optimizer:
    MIN_SELECTED_STOCKPILE_BLEND_RATIO = 0.01
    SOLUTION_TOLERANCE = 1e-6
    # Legacy-compatible defaults used when loading projects without these
    # user-facing Solver Configuration fields.
    THROUGHPUT_REWARD_PER_TONNE = 1_000_100
    TIE_BREAK_REWARD_PER_TONNE = 1.0
    FEWER_STOCKPILE_PENALTY = 10.0
    SOURCE_SELECTION_EPSILON_PENALTY = 0.001
    PRODUCT_BUILD_TONNES_TOLERANCE = 0.1

    @staticmethod
    def selection_source_name(event):
        """Return the source label used for blend-option no-good cuts."""
        source_name = event.stockpile if event.is_stockpile else (event.source_name or event.grade_block)
        return str(source_name) if source_name is not None else ""

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
        solver_config: Optional[dict] = None,
        excluded_source_sets: Optional[List[set]] = None,
        excluded_stockpile_sets: Optional[List[set]] = None,
    ):
        """Runs blending optimization and adjusts steady state if needed."""
        # A blend option can require a second solve after the initial solution
        # identifies an earlier depletion/build boundary (and a third fallback
        # solve if that boundary constraint is infeasible).  Treat the user
        # timeout as a budget for the *whole option*, not for each of those
        # internal solves.
        solver_config = dict(solver_config or {})
        try:
            option_timeout = float(solver_config.get("blend_option_timeout_seconds") or 0.0)
        except (TypeError, ValueError):
            option_timeout = 0.0
        if option_timeout > Optimizer.SOLUTION_TOLERANCE:
            solver_config["blend_option_deadline_monotonic"] = time.monotonic() + option_timeout

        steady_state_controller_source = None
        steady_state_controller_tonnes = None
        initial_duration = steady_state_duration
        filtered_event_pool = self.filter_events_by_steady_state_window(
            event_pool, current_time, steady_state_duration
        )

        result = self.run_blending_optimization(
            filtered_event_pool,
            period_crusher_target,
            steady_state_duration,
            steady_state_controller_source,
            steady_state_controller_tonnes,
            periods,
            period_tracker,
            min_stockpiles,
            max_stockpiles,
            min_stockpile_contribution_ratio,
            solver_config,
            excluded_source_sets,
            excluded_stockpile_sets,
        )
        
        if result['Linprog_result_object'].success: 
            steady_state_duration, steady_state_controller_source, steady_state_controller_tonnes = self.update_steady_state_duration(result["transactions"], steady_state_duration, current_time, stockpile_data, period_tracker)
            (
                steady_state_duration,
                product_build_controller_source,
                product_build_controller_tonnes,
            ) = self.update_steady_state_duration_for_product_build_completion(
                result,
                steady_state_duration,
                solver_config,
            )
            if product_build_controller_source is not None:
                # Product-build completion is a time boundary only. Do not add
                # the depleted-source equality constraint used for stockpile
                # depletion boundaries.
                steady_state_controller_source = None
                steady_state_controller_tonnes = None

            # The first solution already applies to the requested window.  A
            # second MILP solve is only necessary when a depletion, turnover,
            # or product-build boundary actually shortened that window.
            if abs(steady_state_duration - initial_duration) <= Optimizer.SOLUTION_TOLERANCE:
                return result

            filtered_event_pool = self.filter_events_by_steady_state_window(
                event_pool, current_time, steady_state_duration
            )
            result = self.run_blending_optimization(
                filtered_event_pool,
                period_crusher_target,
                steady_state_duration,
                steady_state_controller_source,
                steady_state_controller_tonnes,
                periods,
                period_tracker,
                min_stockpiles,
                max_stockpiles,
                min_stockpile_contribution_ratio,
                solver_config,
                excluded_source_sets,
                excluded_stockpile_sets,
            )

            if result['Linprog_result_object'].success: 
                return result
            
            elif not result['Linprog_result_object'].success: 
                steady_state_controller_source, steady_state_controller_tonnes = None, None
                filtered_event_pool = self.filter_events_by_steady_state_window(
                    event_pool, current_time, steady_state_duration
                )
                result = self.run_blending_optimization(
                    filtered_event_pool,
                    period_crusher_target,
                    steady_state_duration,
                    steady_state_controller_source,
                    steady_state_controller_tonnes,
                    periods,
                    period_tracker,
                    min_stockpiles,
                    max_stockpiles,
                    min_stockpile_contribution_ratio,
                    solver_config,
                    excluded_source_sets,
                    excluded_stockpile_sets,
                )

                if result['Linprog_result_object'].success: 
                    return result
                
                else: return result

        else: return result

    @staticmethod
    def update_steady_state_duration_for_product_build_completion(
        result,
        steady_state_duration,
        solver_config,
    ):
        """Shorten a steady state if the active product build reaches target tonnes."""
        solver_config = solver_config or {}
        target_build = solver_config.get("target_product_build") or {}
        target_build_state = solver_config.get("target_product_build_state") or {}
        if not target_build:
            return steady_state_duration, None, None

        def safe_float(value, default=0.0):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        target_tonnes = safe_float(target_build.get("target_tonnes"), 0.0)
        opening_tonnes = safe_float(target_build_state.get("tonnes"), 0.0)
        remaining_tonnes = target_tonnes - opening_tonnes
        product_build_rate_output = safe_float(
            result.get("product_build_actual_tonnes"), 0.0
        ) / max(float(steady_state_duration), Optimizer.SOLUTION_TOLERANCE)
        if (
            target_tonnes <= Optimizer.SOLUTION_TOLERANCE
            or remaining_tonnes <= Optimizer.PRODUCT_BUILD_TONNES_TOLERANCE
            or product_build_rate_output <= Optimizer.SOLUTION_TOLERANCE
        ):
            return steady_state_duration, None, None

        duration_to_complete = remaining_tonnes / product_build_rate_output
        if (
            duration_to_complete > Optimizer.SOLUTION_TOLERANCE
            and duration_to_complete < steady_state_duration - Optimizer.SOLUTION_TOLERANCE
        ):
            build_name = target_build.get("build_name") or "Product build"
            return duration_to_complete, f"{build_name} complete", remaining_tonnes

        return steady_state_duration, None, None

    @staticmethod
    def product_build_can_complete_in_steady_state(
        target_tonnes,
        opening_tonnes,
        crusher_rate,
        steady_state_duration,
    ):
        """Use the same tonnes tolerance as the runtime completion check."""
        remaining_tonnes = max(float(target_tonnes) - float(opening_tonnes), 0.0)
        steady_state_capacity = max(
            float(crusher_rate) * float(steady_state_duration),
            0.0,
        )
        return (
            float(target_tonnes) > Optimizer.SOLUTION_TOLERANCE
            and remaining_tonnes
            <= steady_state_capacity + Optimizer.PRODUCT_BUILD_TONNES_TOLERANCE
        )

    @staticmethod
    def filter_events_by_steady_state_window(event_pool, current_time, steady_state_duration):
        steady_state_end_time = current_time + timedelta(hours=steady_state_duration)
        filtered_events = []
        for event in event_pool:
            if not event.is_grade_block:
                filtered_events.append(event)
                continue

            delivered_datetime = getattr(event, "delivered_datetime", None)
            if delivered_datetime is None:
                continue
            try:
                is_available = current_time <= delivered_datetime < steady_state_end_time
            except TypeError:
                is_available = False
            if is_available:
                filtered_events.append(event)

        return filtered_events

    @staticmethod
    def update_steady_state_duration(selected_events, steady_state_duration, start_of_steady_state_datetime, stockpile_data: List[StockpileData], period_tracker):
        """Apply stockpile depletion/turnover boundaries to a steady state.

        Delivered grade-block payloads are inventory choices within the
        already-defined window. Their arrival or selection must not shorten
        the window or create another decision point.
        """
        end_of_steady_state_datetime = start_of_steady_state_datetime + timedelta(hours=steady_state_duration)
        updated_duration = steady_state_duration
        updated_duration_auto_turnover = steady_state_duration
        source_name = "Null"
        source_tonnes = "Null"
        minimum_duration = 0.016666667

        def apply_depletion_boundary(source, opening_balance, actual_tonnes, rate):
            nonlocal updated_duration, source_name, source_tonnes

            balance_tolerance = max(0.01, abs(opening_balance) * 1e-6)
            depletes_source = (
                actual_tonnes > 0
                and opening_balance > 0
                and rate > 0
                and opening_balance - actual_tonnes <= balance_tolerance
            )
            if not depletes_source:
                return

            time_to_depletion = float(opening_balance / rate)
            effective_duration = max(time_to_depletion, minimum_duration)

            # If a source will deplete sooner than the current steady state duration, then update the steady state duration
            if effective_duration < updated_duration - Optimizer.SOLUTION_TOLERANCE:
                updated_duration = effective_duration
                source_name = source
                source_tonnes = opening_balance

        for selected_event in selected_events:
            actual_tonnes = float(selected_event.get("actual_tonnes") or 0)
            opening_balance = float(selected_event.get("opening_balance") or 0)
            rate = float(selected_event.get("equipment_rate_input") or 0)

            if selected_event.get("source_type") == "grade_block":
                # Payloads are aggregated direct-tip inventory for this
                # window, not steady-state boundary events.
                continue

            apply_depletion_boundary(
                selected_event.get("source_id", selected_event["source"]),
                opening_balance,
                actual_tonnes,
                rate,
            )

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
    def calculate_grouped_payload_depletion_duration(
        delivered_datetimes,
        start_of_steady_state_datetime,
    ):
        """Return the selected payload group's delivery window in hours.

        This utility is used only to validate a configured minimum grade-block
        pairing duration. Grade-block payload arrivals remain excluded from
        steady-state boundary generation in ``update_steady_state_duration``.
        """
        valid_datetimes = []
        for delivered_datetime in delivered_datetimes or []:
            parsed = pd.to_datetime(delivered_datetime, errors="coerce")
            if not pd.isna(parsed):
                valid_datetimes.append(parsed.to_pydatetime())
        if not valid_datetimes or start_of_steady_state_datetime is None:
            return None

        latest_delivery_datetime = max(valid_datetimes) + timedelta(seconds=1)
        return max(
            (
                latest_delivery_datetime - start_of_steady_state_datetime
            ).total_seconds() / 3600,
            0.0,
        )

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
        solver_config: Optional[dict] = None,
        excluded_source_sets: Optional[List[set]] = None,
        excluded_stockpile_sets: Optional[List[set]] = None,
    ):
        """Core optimisation logic using a mixed integer solver."""

        if min_stockpile_contribution_ratio is None:
            min_stockpile_contribution_ratio = Optimizer.MIN_SELECTED_STOCKPILE_BLEND_RATIO

        if not 0.01 <= min_stockpile_contribution_ratio <= 1:
            raise ValueError("Min Stockpile Contribution Ratio must be between 0.01 and 1.")

        solver_config = solver_config or {}
        try:
            blend_option_timeout_seconds = float(
                solver_config.get("blend_option_timeout_seconds", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            blend_option_timeout_seconds = 0.0
        if blend_option_timeout_seconds <= Optimizer.SOLUTION_TOLERANCE:
            blend_option_timeout_seconds = None
        else:
            deadline = solver_config.get("blend_option_deadline_monotonic")
            if deadline is not None:
                try:
                    remaining_seconds = float(deadline) - time.monotonic()
                except (TypeError, ValueError):
                    remaining_seconds = None
                if remaining_seconds is not None:
                    # CBC accepts fractional seconds.  Keep a very small
                    # positive allowance so it exits cleanly with a solver
                    # status rather than treating zero as "no limit".
                    blend_option_timeout_seconds = max(remaining_seconds, 0.01)
        direct_tip_enabled = bool(solver_config.get("direct_tip_enabled", True))
        require_whole_direct_tip_payloads = bool(
            solver_config.get("require_whole_direct_tip_payloads", False)
        )
        direct_tip_cash_incentive = 0.0
        if direct_tip_enabled:
            try:
                direct_tip_cash_incentive = float(solver_config.get("direct_tip_cash_incentive", 10.0))
            except (TypeError, ValueError):
                direct_tip_cash_incentive = 10.0
        else:
            event_pool = [event for event in event_pool if not event.is_grade_block]

        try:
            stay_on_same_blend_incentive = float(
                solver_config.get("stay_on_same_blend_incentive", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            stay_on_same_blend_incentive = 0.0
        previous_blend_stockpile_source_ids = {
            str(source_id)
            for source_id in solver_config.get("previous_blend_stockpile_source_ids", [])
        }
        try:
            stay_on_same_grade_block_pair_incentive = float(
                solver_config.get("stay_on_same_grade_block_pair_incentive", 0.0) or 0.0
            )
        except (TypeError, ValueError):
            stay_on_same_grade_block_pair_incentive = 0.0
        previous_grade_block_pairs = solver_config.get("previous_grade_block_pairs", {}) or {}
        previous_grade_block_pair_sources = {
            str(source)
            for source, stockpiles in previous_grade_block_pairs.items()
            if stockpiles
        }
        previous_grade_block_pair_stockpile_source_ids = {
            str(source_id)
            for stockpiles in previous_grade_block_pairs.values()
            for source_id in (stockpiles or [])
        }

        excluded_source_sets = [
            set(source_set)
            for source_set in (excluded_source_sets or [])
            if source_set
        ]
        # Empty means a direct-tip-only option and must be retained so the
        # next hard-mode candidate is required to use a stockpile.
        excluded_stockpile_sets = [
            set(source_set)
            for source_set in (excluded_stockpile_sets or [])
        ]

        def safe_float(value, default=0.0):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        def stream_tonnes(event, stream_name, fallback):
            """Return a mapped additive tonne stream for one source."""
            value = safe_float(
                (getattr(event, "source_properties", {}) or {}).get(stream_name),
                None,
            )
            # Legacy APS/direct-tip rows often do not carry the canonical
            # additive field.  Treat a missing/zero value as un-mapped and
            # preserve their physical ROM quantity.
            return value if value is not None and value > Optimizer.SOLUTION_TOLERANCE else fallback

        target_product_brand = str(solver_config.get("target_product_brand") or "").strip().upper()
        selected_data_stream = str(
            solver_config.get("selected_data_stream") or DEFAULT_STREAM
        ).strip().lower()
        # Grade compliance and objectives consume the single stream selected
        # for this run. Resolution is per analyte so incomplete upstream data
        # falls back without discarding otherwise valid grades.
        for event in event_pool:
            apply_selected_stream(event, selected_data_stream, target_product_brand)
        crusher_tonnes_stream = str(
            solver_config.get("crusher_tonnes_stream") or "modelled_rom_wmt"
        )
        reclaimer_tonnes_stream = str(
            solver_config.get("reclaimer_tonnes_stream") or "modelled_rom_wmt"
        )
        product_build_tonnes_stream = str(
            solver_config.get("product_build_tonnes_stream") or "modelled_product_wmt"
        )
        # Coefficients are source-stream tonnes per physical ROM tonne.  A
        # missing mapping deliberately falls back to physical ROM tonnes for
        # backwards-compatible projects.
        physical_tonnes = [max(safe_float(event.balance), 0.0) for event in event_pool]
        crusher_coefficients = [
            stream_tonnes(event, crusher_tonnes_stream, physical) / physical
            if physical > Optimizer.SOLUTION_TOLERANCE else 0.0
            for event, physical in zip(event_pool, physical_tonnes)
        ]
        reclaimer_coefficients = [
            stream_tonnes(event, reclaimer_tonnes_stream, physical) / physical
            if physical > Optimizer.SOLUTION_TOLERANCE else 0.0
            for event, physical in zip(event_pool, physical_tonnes)
        ]
        product_build_coefficients = [
            stream_tonnes(event, product_build_tonnes_stream, physical) / physical
            if physical > Optimizer.SOLUTION_TOLERANCE else 0.0
            for event, physical in zip(event_pool, physical_tonnes)
        ]
        source_property_weights = {
            str(name).strip().lower(): str(weight).strip().lower()
            for name, weight in dict(
                solver_config.get("source_property_weights") or {}
            ).items()
            if str(name).strip() and str(weight).strip()
        }

        def used_grade_stream(event, analyte):
            for warning in getattr(event, "grade_stream_warnings", []) or []:
                if str(warning.get("analyte") or "").lower() == analyte:
                    return str(warning.get("used_stream") or selected_data_stream).lower()
            return selected_data_stream

        grade_weight_coefficients = {}
        for analyte in ("fe", "si", "al", "p", "mn"):
            coefficients = []
            for event, physical in zip(event_pool, physical_tonnes):
                grade_field = f"{used_grade_stream(event, analyte)}_{analyte}"
                weight_field = source_property_weights.get(grade_field)
                weight_tonnes = stream_tonnes(
                    event, weight_field, physical
                ) if weight_field else physical
                coefficients.append(
                    weight_tonnes / physical
                    if physical > Optimizer.SOLUTION_TOLERANCE else 0.0
                )
            grade_weight_coefficients[analyte] = coefficients

        # The decision variable is physical ROM depletion. Reclaimer capacity
        # is converted from the selected reclaimer stream back to that physical
        # basis for each source independently.
        bounds = []
        for event, coefficient in zip(event_pool, reclaimer_coefficients):
            reclaim_capacity = max(safe_float(event.rate), 0.0) * steady_state_duration
            physical_capacity = (
                reclaim_capacity / coefficient
                if coefficient > Optimizer.SOLUTION_TOLERANCE else 0.0
            )
            bounds.append((0, min(max(safe_float(event.balance), 0.0), physical_capacity)))
        brand_guidance_mode = solver_config.get("brand_guidance_mode", "ignore")
        brand_guidance_enabled = bool(
            solver_config.get(
                "brand_guidance_enabled",
                brand_guidance_mode != "ignore",
            )
        )
        brand_guidance_incentive = safe_float(solver_config.get("brand_guidance_incentive", 0.0), 0.0)
        timing_guidance_enabled = bool(
            solver_config.get("timing_guidance_enabled", False)
        )
        timing_guidance_incentive = safe_float(
            solver_config.get("timing_guidance_incentive", 0.0),
            0.0,
        )
        timing_guidance_tolerance_hours = max(
            safe_float(
                solver_config.get("timing_guidance_tolerance_hours", 0.0),
                0.0,
            ),
            0.0,
        )
        active_blend_guidance_enabled = bool(
            solver_config.get("active_blend_guidance_enabled", False)
        )
        active_blend_guidance_incentive = safe_float(
            solver_config.get("active_blend_guidance_incentive", 0.0),
            0.0,
        )
        current_steady_state_datetime = pd.to_datetime(
            solver_config.get("current_steady_state_datetime"),
            errors="coerce",
        )

        def normalized_stockpile_name(value):
            return (
                str(value or "")
                .strip()
                .upper()
                .replace("\\", "/")
                .replace("STOCKPILES/", "")
            )

        stockpile_timing_guidance = {
            normalized_stockpile_name(stockpile): guidance
            for stockpile, guidance in (
                solver_config.get("stockpile_timing_guidance", {}) or {}
            ).items()
        }

        def event_timing_compliance(event):
            if (
                not timing_guidance_enabled
                or not event.is_stockpile
                or pd.isna(current_steady_state_datetime)
            ):
                return 0.0
            guidance = stockpile_timing_guidance.get(
                normalized_stockpile_name(event.stockpile),
                {},
            )
            windows = guidance.get("windows", []) if isinstance(guidance, dict) else []
            nearest_distance_hours = None
            tolerance = pd.Timedelta(hours=timing_guidance_tolerance_hours)
            for window in windows:
                start = pd.to_datetime(
                    window.get("start_datetime"), errors="coerce"
                )
                end = pd.to_datetime(
                    window.get("end_datetime"), errors="coerce"
                )
                if pd.isna(start) or pd.isna(end):
                    continue
                expanded_start = start - tolerance
                expanded_end = end + tolerance
                if expanded_start <= current_steady_state_datetime <= expanded_end:
                    return 1.0
                if current_steady_state_datetime < expanded_start:
                    distance = (
                        expanded_start - current_steady_state_datetime
                    ).total_seconds() / 3600
                else:
                    distance = (
                        current_steady_state_datetime - expanded_end
                    ).total_seconds() / 3600
                nearest_distance_hours = (
                    distance
                    if nearest_distance_hours is None
                    else min(nearest_distance_hours, distance)
                )
            if nearest_distance_hours is None:
                return 0.0
            return 1.0 / (1.0 + max(nearest_distance_hours, 0.0))

        expected_active_blend = set()
        if (
            active_blend_guidance_enabled
            and target_product_brand
            and not pd.isna(current_steady_state_datetime)
        ):
            for window in solver_config.get("active_blend_guidance", []) or []:
                brand = str(window.get("product_brand") or "").strip().upper()
                start = pd.to_datetime(
                    window.get("start_datetime"), errors="coerce"
                )
                end = pd.to_datetime(
                    window.get("end_datetime"), errors="coerce"
                )
                if (
                    brand == target_product_brand
                    and not pd.isna(start)
                    and not pd.isna(end)
                    and start <= current_steady_state_datetime < end
                ):
                    expected_active_blend = {
                        normalized_stockpile_name(stockpile)
                        for stockpile in window.get("stockpiles", [])
                    }
                    break

        def signed_guidance_cost(value, compliance):
            compliance = max(0.0, min(1.0, safe_float(compliance)))
            if value >= 0:
                return -value * compliance
            return abs(value) * (1.0 - compliance)

        def event_brand_match_proportion(event):
            if not target_product_brand or not event.is_stockpile:
                return 0.0
            proportions = getattr(event, "aps_brand_proportions", {}) or {}
            normalized_proportions = {
                str(brand or "").strip().upper(): safe_float(proportion)
                for brand, proportion in proportions.items()
            }
            if target_product_brand in normalized_proportions:
                return max(0.0, min(1.0, normalized_proportions[target_product_brand]))
            event_brand = str(getattr(event, "aps_brand", "") or "").strip().upper()
            return 1.0 if event_brand == target_product_brand else 0.0

        if (
            target_product_brand
            and brand_guidance_mode == "force_match"
        ):
            bounds = [
                (0, upper if (not event.is_stockpile or event_brand_match_proportion(event) > 0) else 0)
                for (lower, upper), event in zip(bounds, event_pool)
            ]

        preference_rewards = [0.0] * len(event_pool)
        balance_preference = solver_config.get("balance_preference", "none")
        balance_preference_incentive = max(
            safe_float(
                solver_config.get(
                    "balance_preference_incentive",
                    Optimizer.TIE_BREAK_REWARD_PER_TONNE,
                ),
                Optimizer.TIE_BREAK_REWARD_PER_TONNE,
            ),
            0.0,
        )
        stockpile_indices_for_preferences = [
            i for i, event in enumerate(event_pool) if event.is_stockpile
        ]
        if (
            balance_preference in ("lower", "higher")
            and stockpile_indices_for_preferences
            and balance_preference_incentive
        ):
            balances = [
                safe_float(event_pool[i].balance)
                for i in stockpile_indices_for_preferences
            ]
            min_balance = min(balances)
            max_balance = max(balances)
            balance_range = max_balance - min_balance
            if balance_range > Optimizer.SOLUTION_TOLERANCE:
                for i, balance in zip(
                    stockpile_indices_for_preferences,
                    balances,
                ):
                    if balance_preference == "lower":
                        preference_rewards[i] += (
                            balance_preference_incentive
                            * (max_balance - balance)
                            / balance_range
                        )
                    else:
                        preference_rewards[i] += (
                            balance_preference_incentive
                            * (balance - min_balance)
                            / balance_range
                        )

        if solver_config.get("prefer_amt_stockpiles", False):
            amt_preference_incentive = max(
                safe_float(
                    solver_config.get(
                        "amt_preference_incentive",
                        Optimizer.TIE_BREAK_REWARD_PER_TONNE,
                    ),
                    Optimizer.TIE_BREAK_REWARD_PER_TONNE,
                ),
                0.0,
            )
            for i, event in enumerate(event_pool):
                if event.is_stockpile and getattr(event, "is_amt", False):
                    preference_rewards[i] += amt_preference_incentive

        if solver_config.get("prefer_contaminated_stockpiles", False):
            contaminated_preference_incentive = max(
                safe_float(
                    solver_config.get(
                        "contaminated_preference_incentive",
                        Optimizer.TIE_BREAK_REWARD_PER_TONNE,
                    ),
                    Optimizer.TIE_BREAK_REWARD_PER_TONNE,
                ),
                0.0,
            )
            thresholds = solver_config.get("contaminant_thresholds", {})
            contaminant_grades = {
                "si": "grade_si",
                "al": "grade_al",
                "p": "grade_p",
                "mn": "grade_mn",
            }
            for i, event in enumerate(event_pool):
                if not event.is_stockpile:
                    continue
                for contaminant, attribute in contaminant_grades.items():
                    threshold = safe_float(thresholds.get(contaminant))
                    grade = safe_float(getattr(event, attribute, 0))
                    if grade > threshold:
                        preference_rewards[i] += (
                            contaminated_preference_incentive
                            * (
                                1.0
                                + (
                                    (grade - threshold)
                                    / max(abs(threshold), 1.0)
                                )
                            )
                        )

        if solver_config.get("prefer_low_fe_stockpiles", False):
            low_fe_preference_incentive = max(
                safe_float(
                    solver_config.get(
                        "low_fe_preference_incentive",
                        Optimizer.TIE_BREAK_REWARD_PER_TONNE,
                    ),
                    Optimizer.TIE_BREAK_REWARD_PER_TONNE,
                ),
                0.0,
            )
            threshold = safe_float(solver_config.get("low_fe_threshold", 58.0), 58.0)
            for i, event in enumerate(event_pool):
                if not event.is_stockpile:
                    continue
                grade_fe = safe_float(event.grade_fe)
                if grade_fe < threshold:
                    preference_rewards[i] += (
                        low_fe_preference_incentive
                        * (
                            1.0
                            + (
                                (threshold - grade_fe)
                                / max(abs(threshold), 1.0)
                            )
                        )
                    )
        
        # Step 2: Build the cost and constraints based on event pool
        base_costs = []
        for i, event in enumerate(event_pool):
            # Per-tonne source costs and enabled rewards/penalties. Throughput
            # incentive is applied separately below so it can be configured.
            preference_reward = preference_rewards[i]
            direct_tip_reward = direct_tip_cash_incentive if event.is_grade_block else 0
            continuity_reward = (
                stay_on_same_blend_incentive
                if event.is_stockpile and str(event.stockpile) in previous_blend_stockpile_source_ids
                else 0
            )
            grade_block_pair_reward = 0
            if stay_on_same_grade_block_pair_incentive:
                if (
                    event.is_grade_block
                    and str(event.source_name or event.grade_block) in previous_grade_block_pair_sources
                ):
                    grade_block_pair_reward = stay_on_same_grade_block_pair_incentive
                elif (
                    event.is_stockpile
                    and str(event.stockpile) in previous_grade_block_pair_stockpile_source_ids
                ):
                    grade_block_pair_reward = stay_on_same_grade_block_pair_incentive
            brand_match_proportion = event_brand_match_proportion(event)
            brand_guidance_cost = 0
            if (
                brand_guidance_enabled
                and target_product_brand
                and brand_guidance_incentive
                and event.is_stockpile
            ):
                if brand_guidance_mode == "penalize_mismatch":
                    brand_guidance_cost = abs(brand_guidance_incentive) * (
                        1 - brand_match_proportion
                    )
                else:
                    brand_guidance_cost = signed_guidance_cost(
                        brand_guidance_incentive,
                        brand_match_proportion,
                    )
            timing_guidance_cost = 0
            if timing_guidance_incentive and event.is_stockpile:
                timing_guidance_cost = signed_guidance_cost(
                    timing_guidance_incentive,
                    event_timing_compliance(event),
                )
            base_costs.append(
                event.cost + event.cash + brand_guidance_cost
                + timing_guidance_cost
                - preference_reward - direct_tip_reward - continuity_reward
                - grade_block_pair_reward
            )

        throughput_incentive_per_tonne = max(
            safe_float(
                solver_config.get(
                    "throughput_incentive_per_tonne",
                    Optimizer.THROUGHPUT_REWARD_PER_TONNE,
                ),
                Optimizer.THROUGHPUT_REWARD_PER_TONNE,
            ),
            0.0,
        )
        source_selection_tie_break_penalty = max(
            safe_float(
                solver_config.get(
                    "source_selection_tie_break_penalty",
                    Optimizer.SOURCE_SELECTION_EPSILON_PENALTY,
                ),
                Optimizer.SOURCE_SELECTION_EPSILON_PENALTY,
            ),
            0.0,
        )
        c = [
            cost - throughput_incentive_per_tonne * crusher_coefficient
            for cost, crusher_coefficient in zip(base_costs, crusher_coefficients)
        ]

        # Equality constraint is only used when a stockpile is depleted early
        # in a steady state. This tries to force that stockpile to deplete fully
        # in the subsequent shortened-state solve. There is a fail-safe in
        # run_with_dynamic_steady_state if this rigid constraint is infeasible.
        if (steady_state_controller_source != None and steady_state_controller_source != "Null"):
            indices = [
                i
                for i, event in enumerate(event_pool)
                if (
                    event.is_stockpile
                    and event.stockpile == steady_state_controller_source
                )
            ]
            A_eq = [[1 if i in indices else 0 for i in range(len(event_pool))]] 
            b_eq = [steady_state_controller_tonnes] * len(A_eq)

        else:
            A_eq = None
            b_eq = None

        # Calendar crusher targets are independent from product-build guidance.
        # In particular, allowing off-spec product-build steady states must not
        # silently turn Calendar crusher targets into advisory targets.
        enforce_calendar_crusher_grades = solver_config.get(
            "enforce_calendar_crusher_grade_targets", True
        )

        # Minimum crusher grade (turned into an upper-bound inequality)
        if enforce_calendar_crusher_grades:
            A_ub_min_crusher_grade_fe = [[coefficient * (-event.grade_fe + period_crusher_target["target_fe_min"]) for event, coefficient in zip(event_pool, grade_weight_coefficients["fe"])]]
            b_ub_min_crusher_grade_fe = [0]

            A_ub_min_crusher_grade_si = [[coefficient * (-event.grade_si + period_crusher_target["target_si_min"]) for event, coefficient in zip(event_pool, grade_weight_coefficients["si"])]]
            b_ub_min_crusher_grade_si = [0]

            A_ub_min_crusher_grade_al = [[coefficient * (-event.grade_al + period_crusher_target["target_al_min"]) for event, coefficient in zip(event_pool, grade_weight_coefficients["al"])]]
            b_ub_min_crusher_grade_al = [0]

            A_ub_min_crusher_grade_p = [[coefficient * (-event.grade_p + period_crusher_target["target_p_min"]) for event, coefficient in zip(event_pool, grade_weight_coefficients["p"])]]
            b_ub_min_crusher_grade_p = [0]

            A_ub_min_crusher_grade_mn = [[coefficient * (-event.grade_mn + period_crusher_target["target_mn_min"]) for event, coefficient in zip(event_pool, grade_weight_coefficients["mn"])]]
            b_ub_min_crusher_grade_mn = [0]

            # Max crusher grade (upper-bound inequality)
            A_ub_max_crusher_grade_fe = [[coefficient * (event.grade_fe - period_crusher_target["target_fe_max"]) for event, coefficient in zip(event_pool, grade_weight_coefficients["fe"])]]
            b_ub_max_crusher_grade_fe = [0]

            A_ub_max_crusher_grade_si = [[coefficient * (event.grade_si - period_crusher_target["target_si_max"]) for event, coefficient in zip(event_pool, grade_weight_coefficients["si"])]]
            b_ub_max_crusher_grade_si = [0]

            A_ub_max_crusher_grade_al = [[coefficient * (event.grade_al - period_crusher_target["target_al_max"]) for event, coefficient in zip(event_pool, grade_weight_coefficients["al"])]]
            b_ub_max_crusher_grade_al = [0]

            A_ub_max_crusher_grade_p = [[coefficient * (event.grade_p - period_crusher_target["target_p_max"]) for event, coefficient in zip(event_pool, grade_weight_coefficients["p"])]]
            b_ub_max_crusher_grade_p = [0]

            A_ub_max_crusher_grade_mn = [[coefficient * (event.grade_mn - period_crusher_target["target_mn_max"]) for event, coefficient in zip(event_pool, grade_weight_coefficients["mn"])]]
            b_ub_max_crusher_grade_mn = [0]
        else:
            A_ub_min_crusher_grade_fe = []
            b_ub_min_crusher_grade_fe = []
            A_ub_min_crusher_grade_si = []
            b_ub_min_crusher_grade_si = []
            A_ub_min_crusher_grade_al = []
            b_ub_min_crusher_grade_al = []
            A_ub_min_crusher_grade_p = []
            b_ub_min_crusher_grade_p = []
            A_ub_min_crusher_grade_mn = []
            b_ub_min_crusher_grade_mn = []
            A_ub_max_crusher_grade_fe = []
            b_ub_max_crusher_grade_fe = []
            A_ub_max_crusher_grade_si = []
            b_ub_max_crusher_grade_si = []
            A_ub_max_crusher_grade_al = []
            b_ub_max_crusher_grade_al = []
            A_ub_max_crusher_grade_p = []
            b_ub_max_crusher_grade_p = []
            A_ub_max_crusher_grade_mn = []
            b_ub_max_crusher_grade_mn = []

        # Step 3: Crusher capacity constraint
        A_ub = [crusher_coefficients]
        b_ub = [period_crusher_target["crusher_rate"] * steady_state_duration]  # Must be <= crusher rate * steady state duration

        # Generate a list of indices for each unique stockpile/source.
        stockpile_event_indices = {}
        stockpile_indices = []
        grade_block_indices = []
        source_event_indices = {}

        for i, event in enumerate(event_pool):
            source_name = Optimizer.selection_source_name(event)
            source_event_indices.setdefault(source_name, []).append(i)
            stockpile_name = event.stockpile
            if event.is_stockpile:
                stockpile_event_indices.setdefault(stockpile_name, []).append(i)
                stockpile_indices.append(i)
            elif event.is_grade_block:
                grade_block_indices.append(i)

        # Optional stockpile-only grade feasibility. In the default mode,
        # grade blocks can improve the final crusher blend but cannot rescue
        # a stockpile blend that is infeasible on its own.
        A_ub_stockpile_grade_feasibility = []
        b_ub_stockpile_grade_feasibility = []
        stockpile_feasibility_mode = solver_config.get(
            "stockpile_feasibility_mode", "stockpile_must_be_feasible"
        )
        if enforce_calendar_crusher_grades and stockpile_feasibility_mode == "stockpile_must_be_feasible" and stockpile_indices:
            grade_constraint_specs = [
                ("grade_fe", "target_fe_min", "target_fe_max"),
                ("grade_si", "target_si_min", "target_si_max"),
                ("grade_al", "target_al_min", "target_al_max"),
                ("grade_p", "target_p_min", "target_p_max"),
                ("grade_mn", "target_mn_min", "target_mn_max"),
            ]
            for grade_attribute, min_key, max_key in grade_constraint_specs:
                min_row = [0] * len(event_pool)
                max_row = [0] * len(event_pool)
                for event_index in stockpile_indices:
                    analyte = grade_attribute.replace("grade_", "")
                    grade_weight = grade_weight_coefficients[analyte][event_index]
                    min_row[event_index] = (
                        period_crusher_target[min_key] - getattr(event_pool[event_index], grade_attribute)
                    ) * grade_weight
                    max_row[event_index] = (
                        getattr(event_pool[event_index], grade_attribute) - period_crusher_target[max_key]
                    ) * grade_weight
                A_ub_stockpile_grade_feasibility.extend([min_row, max_row])
                b_ub_stockpile_grade_feasibility.extend([0, 0])

        # Product-build grade targeting. When the active product build can be
        # completed inside this steady state, constrain the cumulative build
        # inventory plus the candidate feed so the build is on spec at target
        # tonnes. A repair pass can apply the same cumulative constraint from
        # an earlier checkpoint after the normal rolling pass proves that the
        # terminal state is infeasible or off spec.
        A_ub_product_build_grade = []
        b_ub_product_build_grade = []
        target_product_build = solver_config.get("target_product_build") or {}
        target_product_build_state = solver_config.get("target_product_build_state") or {}
        if target_product_build:
            opening_product_tonnes = safe_float(target_product_build_state.get("tonnes"), 0.0)
            target_product_tonnes = safe_float(target_product_build.get("target_tonnes"), 0.0)
            product_build_can_complete = Optimizer.product_build_can_complete_in_steady_state(
                target_product_tonnes,
                opening_product_tonnes,
                min(
                    sum(
                        upper * coefficient
                        for (_, upper), coefficient in zip(bounds, product_build_coefficients)
                    ),
                    safe_float(period_crusher_target.get("crusher_rate"), 0.0)
                    * steady_state_duration
                    * max(
                        (
                            product_coefficient / crusher_coefficient
                            if crusher_coefficient > Optimizer.SOLUTION_TOLERANCE
                            else 0.0
                        )
                        for product_coefficient, crusher_coefficient in zip(
                            product_build_coefficients, crusher_coefficients
                        )
                    ),
                ) / max(steady_state_duration, Optimizer.SOLUTION_TOLERANCE),
                steady_state_duration,
            )
            allow_offspec_build_state = bool(
                solver_config.get(
                    "allow_offspec_steady_states_for_product_build", False
                )
                and solver_config.get(
                    "active_product_build_completes_within_horizon", False
                )
                and not solver_config.get(
                    "force_product_build_state_grades_on_spec", False
                )
            )
            # If the build is not capacity-projected to finish by the end of
            # the planning horizon, do not relax its targets. Applying the
            # build bounds to each state keeps every partial build on spec.
            if not allow_offspec_build_state:
                for grade_key in ["fe", "si", "al", "p", "mn"]:
                    min_target = safe_float(target_product_build.get(f"target_{grade_key}_min"), 0.0)
                    max_target = safe_float(target_product_build.get(f"target_{grade_key}_max"), 100.0)
                    A_ub_product_build_grade.extend([
                        [coefficient * (min_target - safe_float(getattr(event, f"grade_{grade_key}", 0.0))) for event, coefficient in zip(event_pool, grade_weight_coefficients[grade_key])],
                        [
                            coefficient * (safe_float(
                                getattr(event, f"grade_{grade_key}", 0.0)
                            ) - max_target)
                            for event, coefficient in zip(event_pool, grade_weight_coefficients[grade_key])
                        ],
                    ])
                    b_ub_product_build_grade.extend([0, 0])
            enforce_cumulative_build_grade = bool(
                solver_config.get("enforce_cumulative_product_build_grade", False)
            )
            if product_build_can_complete or enforce_cumulative_build_grade:
                for grade_key in ["fe", "si", "al", "p", "mn"]:
                    min_target = safe_float(target_product_build.get(f"target_{grade_key}_min"), 0.0)
                    max_target = safe_float(target_product_build.get(f"target_{grade_key}_max"), 100.0)
                    opening_grade_metal = safe_float(
                        target_product_build_state.get(f"grade_{grade_key}_metal"),
                        0.0,
                    )
                    opening_grade_weight = safe_float(
                        target_product_build_state.get(f"grade_{grade_key}_weight"),
                        opening_product_tonnes,
                    )

                    min_row = [
                        coefficient * (min_target - safe_float(getattr(event, f"grade_{grade_key}", 0.0)))
                        for event, coefficient in zip(event_pool, grade_weight_coefficients[grade_key])
                    ]
                    max_row = [
                        coefficient * (safe_float(getattr(event, f"grade_{grade_key}", 0.0)) - max_target)
                        for event, coefficient in zip(event_pool, grade_weight_coefficients[grade_key])
                    ]
                    A_ub_product_build_grade.extend([min_row, max_row])
                    b_ub_product_build_grade.extend([
                        opening_grade_metal - min_target * opening_grade_weight,
                        max_target * opening_grade_weight - opening_grade_metal,
                    ])

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
        if not grade_block_indices:
            A_ub_min_feed_ratio = [[0 for _ in range(len(event_pool))]]
            # A positive minimum cannot be met when no direct-tip source is
            # available. Make the model explicitly infeasible instead of
            # silently accepting an all-zero constraint row.
            b_ub_min_feed_ratio = [
                -1 if direct_feed_ratio_min > 0 else 0
            ]

        elif direct_feed_ratio_min <= 0 or direct_feed_ratio_min > 1:
            
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

        # User-defined ratios use the same linear form as Direct Tip Ratio:
        #   sum(x * numerator) / sum(x * denominator) between min and max.
        # Arithmetic inside each expression is evaluated once per source, so
        # cross-multiplication remains a linear constraint in selected tonnes.
        A_ub_custom_constraints = []
        b_ub_custom_constraints = []
        compiled_custom_constraints = []
        for definition in period_crusher_target.get("custom_constraints", []) or []:
            numerator_values, denominator_values = (
                custom_constraint_coefficients(event_pool, definition)
            )
            minimum = definition.get("minimum")
            maximum = definition.get("maximum")
            minimum = (
                safe_float(minimum) if minimum not in (None, "") else None
            )
            maximum = (
                safe_float(maximum) if maximum not in (None, "") else None
            )
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError(
                    f"{definition.get('name')}: minimum cannot exceed maximum."
                )
            if minimum is not None or maximum is not None:
                # A bounded ratio is undefined at a zero selected denominator;
                # prevent the solver from satisfying it vacuously.
                A_ub_custom_constraints.append([
                    -denominator for denominator in denominator_values
                ])
                b_ub_custom_constraints.append(
                    -Optimizer.SOLUTION_TOLERANCE
                )
            if minimum is not None:
                A_ub_custom_constraints.append([
                    minimum * denominator - numerator
                    for numerator, denominator in zip(
                        numerator_values, denominator_values
                    )
                ])
                b_ub_custom_constraints.append(0.0)
            if maximum is not None:
                A_ub_custom_constraints.append([
                    numerator - maximum * denominator
                    for numerator, denominator in zip(
                        numerator_values, denominator_values
                    )
                ])
                b_ub_custom_constraints.append(0.0)
            compiled_custom_constraints.append({
                "definition": definition,
                "numerator_values": numerator_values,
                "denominator_values": denominator_values,
                "minimum": minimum,
                "maximum": maximum,
            })

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

        for event_index, event in enumerate(event_pool):
            # Create a row filled with zeros
            A_ub_row = [0] * len(event_pool)

            # Stockpile maximum quantity is a period-level reclaim limit, so
            # it is prorated to the steady-state duration. A delivered
            # grade-block payload is already an available inventory parcel;
            # prorating its payload tonnes across the calendar period would
            # make less than one payload available and conflict with the
            # optional minimum-payload commitment.
            if event.is_stockpile and event_index in source_indices:
                A_ub_row[event_index] = 1 / steady_state_duration
                A_ub_max_quantity.append(A_ub_row)
                b_ub_max_quantity.append(
                    event.max_quantity
                    / periods.get_periods()[f"{period_tracker}_duration"]
                )

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
            + A_ub_stockpile_grade_feasibility
            + A_ub_product_build_grade
            + A_ub_custom_constraints
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
            + b_ub_stockpile_grade_feasibility
            + b_ub_product_build_grade
            + b_ub_custom_constraints
            + b_ub_max_quantity
        )

        prob = LpProblem("blending", LpMinimize)
        x_vars = [
            LpVariable(f"x_{i}", lowBound=0, upBound=bounds[i][1])
            for i in range(len(event_pool))
        ]

        # Optional minimum direct-tip commitment. Payload transactions remain
        # continuous, but a grade-block source must contribute either zero
        # tonnes or at least one payload. This permits selections such as 1.5
        # payloads while rejecting a 0.5-payload blend.
        if direct_tip_enabled and require_whole_direct_tip_payloads:
            payload_indices_by_source = {}
            for event_index, event in enumerate(event_pool):
                if event.is_grade_block:
                    payload_indices_by_source.setdefault(
                        Optimizer.selection_source_name(event), []
                    ).append(event_index)

            for source_index, indices in enumerate(
                payload_indices_by_source.values()
            ):
                positive_payload_tonnes = [
                    safe_float(
                        event_pool[index].max_quantity,
                        safe_float(event_pool[index].balance),
                    )
                    for index in indices
                    if safe_float(
                        event_pool[index].max_quantity,
                        safe_float(event_pool[index].balance),
                    )
                    > Optimizer.SOLUTION_TOLERANCE
                ]
                source_capacity = sum(bounds[index][1] for index in indices)
                source_selected = LpVariable(
                    f"y_min_payload_{source_index}", cat=LpBinary
                )
                source_feed = lpSum(x_vars[index] for index in indices)
                if (
                    not positive_payload_tonnes
                    or source_capacity <= Optimizer.SOLUTION_TOLERANCE
                ):
                    prob += source_selected == 0
                    prob += source_feed == 0
                    continue

                minimum_payload_tonnes = min(positive_payload_tonnes)
                prob += source_feed <= source_capacity * source_selected
                prob += (
                    source_feed
                    >= minimum_payload_tonnes * source_selected
                )

        objective = lpSum(c[i] * x_vars[i] for i in range(len(event_pool)))
        fewer_stockpiles_incentive = max(
            safe_float(
                solver_config.get(
                    "fewer_stockpiles_incentive",
                    Optimizer.FEWER_STOCKPILE_PENALTY,
                ),
                Optimizer.FEWER_STOCKPILE_PENALTY,
            ),
            0.0,
        )
        prefer_fewer_stockpiles = bool(
            solver_config.get("prefer_fewer_stockpiles", False)
            and fewer_stockpiles_incentive
            > Optimizer.SOLUTION_TOLERANCE
        )

        # Inequality constraints
        for row, rhs in zip(A_ub_total, b_ub_total):
            prob += lpSum(row[i] * x_vars[i] for i in range(len(event_pool))) <= rhs

        # Equality constraints if applicable
        if A_eq is not None and b_eq is not None:
            for row, rhs in zip(A_eq, b_eq):
                prob += lpSum(row[i] * x_vars[i] for i in range(len(event_pool))) == rhs

        # Binary variables to control selected sources. Stockpile count
        # constraints still only count stockpiles, while candidate no-good cuts
        # apply to every source type.
        use_binary_selection = (
            min_stockpiles is not None
            or max_stockpiles is not None
            or prefer_fewer_stockpiles
            or bool(excluded_source_sets)
            or bool(excluded_stockpile_sets)
            or bool(
                active_blend_guidance_enabled
                and active_blend_guidance_incentive
                and expected_active_blend
            )
        )
        if source_event_indices and use_binary_selection:
            y_vars = {}
            total_feed = lpSum(x_vars)
            max_total_feed = period_crusher_target["crusher_rate"] * steady_state_duration
            enforce_count_contribution = min_stockpiles is not None or max_stockpiles is not None

            for source_index, (source_name, indices) in enumerate(source_event_indices.items()):
                y_var = LpVariable(f"y_source_{source_index}", cat=LpBinary)
                y_vars[source_name] = y_var
                source_feed = lpSum(x_vars[i] for i in indices)
                source_feed_upper_bound = sum(bounds[i][1] for i in indices)

                prob += source_feed <= source_feed_upper_bound * y_var
                if source_feed_upper_bound <= Optimizer.SOLUTION_TOLERANCE:
                    prob += y_var == 0
                elif (
                    (
                        active_blend_guidance_enabled
                        and active_blend_guidance_incentive
                        and expected_active_blend
                    )
                    or bool(excluded_source_sets)
                    or bool(excluded_stockpile_sets)
                ):
                    prob += (
                        source_feed
                        >= min(1.0, source_feed_upper_bound) * y_var
                    )

            for stockpile_name, indices in stockpile_event_indices.items():
                stockpile_feed = lpSum(x_vars[i] for i in indices)
                if enforce_count_contribution:
                    prob += (
                        stockpile_feed
                        >= min_stockpile_contribution_ratio * total_feed
                        - max_total_feed * (1 - y_vars[stockpile_name])
                    )

            if min_stockpiles is not None:
                prob += lpSum(y_vars[name] for name in stockpile_event_indices) >= min_stockpiles
            if max_stockpiles is not None:
                prob += lpSum(y_vars[name] for name in stockpile_event_indices) <= max_stockpiles
            if excluded_source_sets:
                all_source_names = set(source_event_indices)
                for source_set in excluded_source_sets:
                    known_sources = source_set & all_source_names
                    outside_sources = all_source_names - known_sources
                    if not known_sources:
                        continue
                    prob += (
                        lpSum(1 - y_vars[source_name] for source_name in known_sources)
                        + lpSum(y_vars[source_name] for source_name in outside_sources)
                        >= 1
                    )
            if excluded_stockpile_sets:
                all_stockpile_names = set(stockpile_event_indices)
                for stockpile_set in excluded_stockpile_sets:
                    known_stockpiles = (
                        stockpile_set & all_stockpile_names
                    )
                    if not stockpile_set:
                        if all_stockpile_names:
                            prob += lpSum(
                                y_vars[name]
                                for name in all_stockpile_names
                            ) >= 1
                        continue
                    if known_stockpiles != stockpile_set:
                        continue
                    outside_stockpiles = (
                        all_stockpile_names - known_stockpiles
                    )
                    prob += (
                        lpSum(
                            1 - y_vars[name]
                            for name in known_stockpiles
                        )
                        + lpSum(
                            y_vars[name]
                            for name in outside_stockpiles
                        )
                        >= 1
                    )
            if excluded_source_sets or excluded_stockpile_sets:
                objective += source_selection_tie_break_penalty * lpSum(
                    y_vars.values()
                )
            if prefer_fewer_stockpiles:
                objective += fewer_stockpiles_incentive * lpSum(
                    y_vars[name] for name in stockpile_event_indices
                )
            if (
                active_blend_guidance_enabled
                and active_blend_guidance_incentive
                and expected_active_blend
                and stockpile_event_indices
            ):
                available_stockpile_names = {
                    stockpile_name
                    for stockpile_name, indices in stockpile_event_indices.items()
                    if sum(bounds[index][1] for index in indices)
                    > Optimizer.SOLUTION_TOLERANCE
                }
                expected_names = {
                    stockpile_name
                    for stockpile_name in available_stockpile_names
                    if normalized_stockpile_name(stockpile_name)
                    in expected_active_blend
                }
                outside_names = available_stockpile_names - expected_names
                exact_match = LpVariable(
                    "two_wp_active_blend_exact_match",
                    cat=LpBinary,
                )
                if {
                    normalized_stockpile_name(name)
                    for name in expected_names
                } != expected_active_blend:
                    prob += exact_match == 0
                else:
                    match_terms = (
                        [y_vars[name] for name in expected_names]
                        + [1 - y_vars[name] for name in outside_names]
                    )
                    for name in expected_names:
                        prob += exact_match <= y_vars[name]
                    for name in outside_names:
                        prob += exact_match <= 1 - y_vars[name]
                    prob += exact_match >= lpSum(match_terms) - (
                        len(match_terms) - 1
                    )
                active_blend_tie_break = (
                    abs(active_blend_guidance_incentive) * max_total_feed
                )
                if active_blend_guidance_incentive >= 0:
                    objective -= active_blend_tie_break * exact_match
                else:
                    objective += active_blend_tie_break * (1 - exact_match)

        # Objective function
        prob += objective

        # Solve the problem
        prob.solve(RetryingCBCSolver(msg=False, timeLimit=blend_option_timeout_seconds))

        solver_status = LpStatus[prob.status]
        success = solver_status == "Optimal"
        solution_values = [
            0.0 if abs(var.value() or 0.0) < Optimizer.SOLUTION_TOLERANCE else var.value()
            for var in x_vars
        ]
        result = SimpleNamespace(
            success=success,
            x=solution_values,
            status=solver_status,
            status_code=prob.status,
        )
        objective_value = value(prob.objective)
        selected_tonnes = sum(solution_values)
        solver_score = (
            (-objective_value / selected_tonnes)
            if objective_value is not None and selected_tonnes > Optimizer.SOLUTION_TOLERANCE
            else ""
        )
        diagnostics = Optimizer.build_diagnostics(
            event_pool,
            period_crusher_target,
            steady_state_duration,
            periods,
            period_tracker,
            min_stockpiles,
            max_stockpiles,
            min_stockpile_contribution_ratio,
            bounds,
            solver_status,
            selected_tonnes,
            solver_config,
        )

        if result.success:

            custom_constraint_fields = {}
            custom_constraint_source_fields = []
            for compiled in compiled_custom_constraints:
                numerator_total = sum(
                    value * result.x[index]
                    for index, value in enumerate(
                        compiled["numerator_values"]
                    )
                )
                denominator_total = sum(
                    value * result.x[index]
                    for index, value in enumerate(
                        compiled["denominator_values"]
                    )
                )
                custom_constraint_fields.update(constraint_report_fields(
                    compiled["definition"],
                    numerator_total,
                    denominator_total,
                    compiled["minimum"],
                    compiled["maximum"],
                ))
                key = constraint_key(
                    compiled["definition"].get("key")
                    or compiled["definition"].get("name")
                )
                custom_constraint_source_fields.append((
                    key,
                    compiled["numerator_values"],
                    compiled["denominator_values"],
                ))

            transactions = []
            for i, event in enumerate(event_pool):
                if result.x[i] >= 0:
                    source_id = event.stockpile if event.is_stockpile else event.grade_block
                    source_name = event.source_name or source_id
                    reported_source_properties = (
                        scale_additive_source_properties(
                            event.source_properties,
                            result.x[i] / event.balance
                            if event.balance > 0 else 0.0,
                            getattr(event, "source_property_kinds", None),
                        )
                    )
                    visible_source_properties = filter_source_properties(
                        reported_source_properties,
                        solver_config.get(
                            "optimisation_source_property_fields"
                        ),
                    )
                    active_property_fields = solver_config.get(
                        "optimisation_source_property_fields"
                    ) or []
                    property_audit_fields = source_property_balance_report_fields(
                        event.source_properties,
                        visible_source_properties,
                        active_fields=active_property_fields,
                        property_kinds=getattr(
                            event, "source_property_kinds", None
                        ),
                    )
                    transaction = {
                            "source": source_name,
                            "source_id": source_id,
                            "source_type": event.type,
                            "estimated_delivery_datetime": (
                                event.delivered_datetime if event.is_grade_block else ""
                            ),
                            "opening_balance": event.balance,
                            "actual_tonnes": result.x[i],
                            "reclaimer_source_tonnes": result.x[i] * reclaimer_coefficients[i],
                            "crusher_source_tonnes": result.x[i] * crusher_coefficients[i],
                            "product_build_source_tonnes": result.x[i] * product_build_coefficients[i],
                            **{
                                f"selected_grade_weight_{analyte}_tonnes": (
                                    result.x[i] * grade_weight_coefficients[analyte][i]
                                )
                                for analyte in ("fe", "si", "al", "p", "mn")
                            },
                            "reclaimer_tonnes_stream": reclaimer_tonnes_stream,
                            "crusher_tonnes_stream": crusher_tonnes_stream,
                            "product_build_tonnes_stream": product_build_tonnes_stream,
                            "grade_fe": event.grade_fe,
                            "grade_si": event.grade_si,
                            "grade_al": event.grade_al,
                            "grade_p": event.grade_p,
                            "grade_mn": event.grade_mn,
                            "selected_grade_stream": event.selected_grade_stream,
                            "selected_grade_brand": event.selected_grade_brand,
                            "grade_stream_warnings": event.grade_stream_warnings,
                            "grade_streams": deepcopy(event.grade_streams),
                            "source_properties": deepcopy(
                                visible_source_properties
                            ),
                            **source_property_report_fields(
                                visible_source_properties,
                                property_kinds=getattr(
                                    event, "source_property_kinds", None
                                ),
                                active_fields=active_property_fields,
                            ),
                            **property_audit_fields,
                            "equipment": event.equipment,
                            "equipment_rate_input": event.rate,
                            "equipment_rate_output": result.x[i] * reclaimer_coefficients[i]
                            / steady_state_duration
                            if steady_state_duration != 0
                            else 0,
                        }
                    for key, numerator_values, denominator_values in custom_constraint_source_fields:
                        numerator_coefficient = numerator_values[i]
                        denominator_coefficient = denominator_values[i]
                        transaction[
                            f"custom_constraint_{key}_source_numerator_coefficient"
                        ] = numerator_coefficient
                        transaction[
                            f"custom_constraint_{key}_source_denominator_coefficient"
                        ] = denominator_coefficient
                        transaction[
                            f"custom_constraint_{key}_source_numerator_contribution"
                        ] = numerator_coefficient * result.x[i]
                        transaction[
                            f"custom_constraint_{key}_source_denominator_contribution"
                        ] = denominator_coefficient * result.x[i]
                    transactions.append(transaction)

            return {
                "Linprog_result_object": result,
                "transactions": transactions,
                "steady_state_duration": steady_state_duration,
                "crusher_actual_grade_fe": sum(
                    event.grade_fe * result.x[i] * grade_weight_coefficients["fe"][i] for i, event in enumerate(event_pool)
                )
                / sum(result.x[i] * grade_weight_coefficients["fe"][i] for i in range(len(event_pool)))
                if sum(result.x[i] * grade_weight_coefficients["fe"][i] for i in range(len(event_pool))) != 0
                else "",
                "crusher_actual_grade_si": sum(
                    event.grade_si * result.x[i] * grade_weight_coefficients["si"][i] for i, event in enumerate(event_pool)
                )
                / sum(result.x[i] * grade_weight_coefficients["si"][i] for i in range(len(event_pool)))
                if sum(result.x[i] * grade_weight_coefficients["si"][i] for i in range(len(event_pool))) != 0
                else "",
                "crusher_actual_grade_al": sum(
                    event.grade_al * result.x[i] * grade_weight_coefficients["al"][i] for i, event in enumerate(event_pool)
                )
                / sum(result.x[i] * grade_weight_coefficients["al"][i] for i in range(len(event_pool)))
                if sum(result.x[i] * grade_weight_coefficients["al"][i] for i in range(len(event_pool))) != 0
                else "",
                "crusher_actual_grade_p": sum(
                    event.grade_p * result.x[i] * grade_weight_coefficients["p"][i] for i, event in enumerate(event_pool)
                )
                / sum(result.x[i] * grade_weight_coefficients["p"][i] for i in range(len(event_pool)))
                if sum(result.x[i] * grade_weight_coefficients["p"][i] for i in range(len(event_pool))) != 0
                else "",
                "crusher_actual_grade_mn": sum(
                    event.grade_mn * result.x[i] * grade_weight_coefficients["mn"][i]
                    for i, event in enumerate(event_pool)
                )
                / sum(result.x[i] * grade_weight_coefficients["mn"][i] for i in range(len(event_pool)))
                if sum(result.x[i] * grade_weight_coefficients["mn"][i] for i in range(len(event_pool))) != 0
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
                "crusher_rate_output": sum(result.x[i] * crusher_coefficients[i] for i in range(len(event_pool))) / steady_state_duration
                if steady_state_duration != 0
                else 0,
                "crusher_actual_tonnes": sum(result.x[i] * crusher_coefficients[i] for i in range(len(event_pool))),
                "product_build_actual_tonnes": sum(result.x[i] * product_build_coefficients[i] for i in range(len(event_pool))),
                **custom_constraint_fields,
                "solver_score": solver_score,
                "solver_objective_value": objective_value,
                "diagnostics": diagnostics,
            }

        else:
            return {
                "Linprog_result_object": result,
                "steady_state_duration": steady_state_duration,
                "solver_score": solver_score,
                "solver_objective_value": objective_value,
                "diagnostics": diagnostics,
            }

    @staticmethod
    def build_diagnostics(
        event_pool,
        period_crusher_target,
        steady_state_duration,
        periods,
        period_tracker,
        min_stockpiles,
        max_stockpiles,
        min_stockpile_contribution_ratio,
        bounds,
        solver_status,
        selected_tonnes,
        solver_config=None,
    ):
        solver_config = solver_config or {}

        def safe_float(value, default=0.0):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        source_summaries = []
        for event, bound in zip(event_pool, bounds):
            source_name = event.stockpile if event.is_stockpile else event.grade_block
            available_tonnes = max(0.0, safe_float(bound[1]))
            source_summaries.append(
                {
                    "source": source_name,
                    "type": event.type,
                    "balance": safe_float(event.balance),
                    "rate": safe_float(event.rate),
                    "max_quantity": safe_float(event.max_quantity),
                    "available_tonnes": available_tonnes,
                    "grade_fe": safe_float(event.grade_fe),
                    "grade_si": safe_float(event.grade_si),
                    "grade_al": safe_float(event.grade_al),
                    "grade_p": safe_float(event.grade_p),
                    "grade_mn": safe_float(event.grade_mn),
                }
            )

        positive_sources = [
            source for source in source_summaries
            if source["available_tonnes"] > Optimizer.SOLUTION_TOLERANCE
        ]
        positive_stockpiles = [
            source for source in positive_sources if source["type"] == "stockpile"
        ]
        positive_grade_blocks = [
            source for source in positive_sources if source["type"] == "grade_block"
        ]

        grade_ranges = {}
        stockpile_grade_ranges = {}
        likely_causes = []
        grade_names = {
            "fe": "Fe",
            "si": "Si",
            "al": "Al",
            "p": "P",
            "mn": "Mn",
        }
        for grade_key, label in grade_names.items():
            values = [source[f"grade_{grade_key}"] for source in positive_sources]
            if not values:
                continue
            available_min = min(values)
            available_max = max(values)
            target_min = safe_float(period_crusher_target.get(f"target_{grade_key}_min"))
            target_max = safe_float(period_crusher_target.get(f"target_{grade_key}_max"))
            grade_ranges[label] = {
                "available_min": available_min,
                "available_max": available_max,
                "target_min": target_min,
                "target_max": target_max,
            }
            if target_min > available_max + Optimizer.SOLUTION_TOLERANCE:
                likely_causes.append(
                    f"{label} minimum {target_min:g} is above the available {label} range "
                    f"({available_min:g} to {available_max:g})."
                )
            if target_max < available_min - Optimizer.SOLUTION_TOLERANCE:
                likely_causes.append(
                    f"{label} maximum {target_max:g} is below the available {label} range "
                    f"({available_min:g} to {available_max:g})."
                )

            stockpile_values = [source[f"grade_{grade_key}"] for source in positive_stockpiles]
            if stockpile_values:
                stockpile_available_min = min(stockpile_values)
                stockpile_available_max = max(stockpile_values)
                stockpile_grade_ranges[label] = {
                    "available_min": stockpile_available_min,
                    "available_max": stockpile_available_max,
                    "target_min": target_min,
                    "target_max": target_max,
                }
                if (
                    solver_config.get("stockpile_feasibility_mode", "stockpile_must_be_feasible")
                    == "stockpile_must_be_feasible"
                ):
                    if target_min > stockpile_available_max + Optimizer.SOLUTION_TOLERANCE:
                        likely_causes.append(
                            f"Stockpile-only {label} minimum {target_min:g} is above the available "
                            f"stockpile {label} range ({stockpile_available_min:g} to {stockpile_available_max:g})."
                        )
                    if target_max < stockpile_available_min - Optimizer.SOLUTION_TOLERANCE:
                        likely_causes.append(
                            f"Stockpile-only {label} maximum {target_max:g} is below the available "
                            f"stockpile {label} range ({stockpile_available_min:g} to {stockpile_available_max:g})."
                        )

        crusher_rate = safe_float(period_crusher_target.get("crusher_rate"))
        direct_feed_ratio_min = safe_float(period_crusher_target.get("direct_feed_ratio_min"), 0.0)
        direct_feed_ratio_max = safe_float(period_crusher_target.get("direct_feed_ratio_max"), 1.0)
        blend_option_timeout_seconds = safe_float(
            solver_config.get("blend_option_timeout_seconds"),
            0.0,
        )
        if blend_option_timeout_seconds > Optimizer.SOLUTION_TOLERANCE and solver_status in {"Not Solved", "Undefined"}:
            likely_causes.append(
                f"Blend option search reached the configured {blend_option_timeout_seconds:g} second solver timeout."
            )
        if not event_pool:
            likely_causes.append("No sources were available in the event pool.")
        if crusher_rate <= Optimizer.SOLUTION_TOLERANCE:
            likely_causes.append("Crusher rate is zero or negative for this period.")
        if not positive_sources:
            likely_causes.append(
                "No available source has positive reclaimable tonnes after balance, rate and state checks."
            )
        if min_stockpiles is not None and len(positive_stockpiles) < min_stockpiles:
            likely_causes.append(
                f"Only {len(positive_stockpiles)} stockpile(s) have positive capacity, "
                f"but Decision Levers requires at least {min_stockpiles}."
            )
        if direct_feed_ratio_min > direct_feed_ratio_max + Optimizer.SOLUTION_TOLERANCE:
            likely_causes.append(
                f"Direct tip ratio minimum ({direct_feed_ratio_min:g}) is greater than maximum ({direct_feed_ratio_max:g})."
            )
        if direct_feed_ratio_max < 1 and positive_grade_blocks and not positive_stockpiles:
            likely_causes.append(
                f"Direct tip ratio maximum is {direct_feed_ratio_max:g}, but no stockpile has positive capacity to pair with direct tip."
            )
        if min_stockpiles is not None and max_stockpiles is not None and min_stockpiles > max_stockpiles:
            likely_causes.append(
                f"Minimum stockpiles ({min_stockpiles}) is greater than maximum stockpiles ({max_stockpiles})."
            )
        if (
            min_stockpiles is not None
            and min_stockpile_contribution_ratio is not None
            and min_stockpiles * min_stockpile_contribution_ratio > 1 + Optimizer.SOLUTION_TOLERANCE
        ):
            likely_causes.append(
                f"The minimum stockpile count and contribution ratio require more than 100% "
                f"of crusher feed ({min_stockpiles} x {min_stockpile_contribution_ratio:g})."
            )

        custom_constraint_ranges = []
        for definition in period_crusher_target.get(
            "custom_constraints", []
        ) or []:
            name = str(
                definition.get("name")
                or definition.get("key")
                or "Custom constraint"
            )
            minimum = definition.get("minimum")
            maximum = definition.get("maximum")
            minimum = (
                safe_float(minimum) if minimum not in (None, "") else None
            )
            maximum = (
                safe_float(maximum) if maximum not in (None, "") else None
            )
            diagnostic = {
                "name": name,
                "numerator_expression": definition.get("numerator"),
                "denominator_expression": definition.get("denominator"),
                "target_min": minimum,
                "target_max": maximum,
                "available_min": None,
                "available_max": None,
            }
            try:
                numerator_values, denominator_values = (
                    custom_constraint_coefficients(event_pool, definition)
                )
            except CustomConstraintError as error:
                diagnostic["error"] = str(error)
                likely_causes.append(str(error))
                custom_constraint_ranges.append(diagnostic)
                continue

            active_coefficients = [
                (numerator_values[index], denominator_values[index])
                for index, bound in enumerate(bounds)
                if safe_float(bound[1]) > Optimizer.SOLUTION_TOLERANCE
            ]
            source_ratios = [
                numerator / denominator
                for numerator, denominator in active_coefficients
                if denominator > Optimizer.SOLUTION_TOLERANCE
            ]
            nonzero_zero_denominator = any(
                denominator <= Optimizer.SOLUTION_TOLERANCE
                and abs(numerator) > Optimizer.SOLUTION_TOLERANCE
                for numerator, denominator in active_coefficients
            )
            if source_ratios and not nonzero_zero_denominator:
                available_min = min(source_ratios)
                available_max = max(source_ratios)
                diagnostic["available_min"] = available_min
                diagnostic["available_max"] = available_max
                if (
                    minimum is not None
                    and minimum
                    > available_max + Optimizer.SOLUTION_TOLERANCE
                ):
                    likely_causes.append(
                        f"Custom ratio '{name}' minimum {minimum:g} is above "
                        f"the available source-coefficient range "
                        f"({available_min:g} to {available_max:g})."
                    )
                if (
                    maximum is not None
                    and maximum
                    < available_min - Optimizer.SOLUTION_TOLERANCE
                ):
                    likely_causes.append(
                        f"Custom ratio '{name}' maximum {maximum:g} is below "
                        f"the available source-coefficient range "
                        f"({available_min:g} to {available_max:g})."
                    )
            elif not source_ratios:
                likely_causes.append(
                    f"Custom ratio '{name}' has no positive denominator "
                    "among sources with available tonnes."
                )
            else:
                diagnostic["range_note"] = (
                    "A source has a zero denominator and non-zero numerator; "
                    "a simple coefficient range is not defined."
                )
            custom_constraint_ranges.append(diagnostic)

        if not likely_causes and selected_tonnes <= Optimizer.SOLUTION_TOLERANCE:
            likely_causes.append(
                "The solver returned zero crusher feed. Check stockpile State, Max Quantity, "
                "reclaim rates, balances and grade targets for this period."
            )

        return {
            "solver_status": solver_status,
            "steady_state_duration": steady_state_duration,
            "crusher_rate": crusher_rate,
            "target_tonnes": crusher_rate * steady_state_duration,
            "selected_tonnes": selected_tonnes,
            "available_source_count": len(source_summaries),
            "positive_source_count": len(positive_sources),
            "positive_stockpile_count": len(positive_stockpiles),
            "positive_grade_block_count": len(positive_grade_blocks),
            "source_summaries": source_summaries,
            "grade_ranges": grade_ranges,
            "stockpile_grade_ranges": stockpile_grade_ranges,
            "custom_constraint_ranges": custom_constraint_ranges,
            "likely_causes": likely_causes,
            "min_stockpiles": min_stockpiles,
            "max_stockpiles": max_stockpiles,
            "min_stockpile_contribution_ratio": min_stockpile_contribution_ratio,
            "direct_feed_ratio_min": direct_feed_ratio_min,
            "direct_feed_ratio_max": direct_feed_ratio_max,
            "throughput_incentive_per_tonne": solver_config.get(
                "throughput_incentive_per_tonne",
                Optimizer.THROUGHPUT_REWARD_PER_TONNE,
            ),
            "source_selection_tie_break_penalty": solver_config.get(
                "source_selection_tie_break_penalty",
                Optimizer.SOURCE_SELECTION_EPSILON_PENALTY,
            ),
            "direct_tip_enabled": solver_config.get("direct_tip_enabled", True),
            "direct_tip_cash_incentive": solver_config.get("direct_tip_cash_incentive", 10.0),
            "stay_on_same_blend_incentive": solver_config.get("stay_on_same_blend_incentive", 0.0),
            "blend_option_timeout_seconds": solver_config.get("blend_option_timeout_seconds", 0.0),
            "max_blend_options_per_steady_state": solver_config.get("max_blend_options_per_steady_state", 12),
            "min_grade_block_pair_duration_hours": solver_config.get("min_grade_block_pair_duration_hours", 0.0),
            "stay_on_same_grade_block_pair_incentive": solver_config.get("stay_on_same_grade_block_pair_incentive", 0.0),
            "grade_block_lock_enabled": solver_config.get("grade_block_lock_enabled", False),
            "stockpile_feasibility_mode": solver_config.get("stockpile_feasibility_mode", "stockpile_must_be_feasible"),
            "prefer_fewer_stockpiles": solver_config.get("prefer_fewer_stockpiles", False),
            "fewer_stockpiles_incentive": solver_config.get("fewer_stockpiles_incentive", Optimizer.FEWER_STOCKPILE_PENALTY),
            "balance_preference": solver_config.get("balance_preference", "none"),
            "balance_preference_incentive": solver_config.get("balance_preference_incentive", Optimizer.TIE_BREAK_REWARD_PER_TONNE),
            "prefer_amt_stockpiles": solver_config.get("prefer_amt_stockpiles", False),
            "amt_preference_incentive": solver_config.get("amt_preference_incentive", Optimizer.TIE_BREAK_REWARD_PER_TONNE),
            "prefer_contaminated_stockpiles": solver_config.get("prefer_contaminated_stockpiles", False),
            "contaminated_preference_incentive": solver_config.get("contaminated_preference_incentive", Optimizer.TIE_BREAK_REWARD_PER_TONNE),
            "prefer_low_fe_stockpiles": solver_config.get("prefer_low_fe_stockpiles", False),
            "low_fe_preference_incentive": solver_config.get("low_fe_preference_incentive", Optimizer.TIE_BREAK_REWARD_PER_TONNE),
            "period_duration": periods.get_periods().get(f"{period_tracker}_duration"),
        }
