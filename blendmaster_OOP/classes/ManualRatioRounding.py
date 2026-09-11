"""Optional conversion of optimiser feed shares into practical manual recipes.

Shares are physical feed WMT, including direct tip. Stored exact conversions
remain untouched. Every rounded recipe is replayed against current inventory.
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
import math

import pandas as pd

from classes.GradeBlockIdentity import parent_grade_block_name
from classes.ManualBlendPlanner import ManualBlendPlanningError
from classes.OptimisedToManualPlan import OptimisedToManualPlan


INCREMENTS = (1, 2, 5, 10, 20, 25, 50)
AUDIT_COLUMNS = ("ratio_rounding_increment", "original_steady_state_number",
                 "original_source_feed_ratio", "rounded_source_feed_ratio",
                 "original_state_start", "original_state_end", "rounding_timing_reason", "rounding_stop_reason")


def rounding_settings(value=None):
    value = value or {}
    enabled, increment = value.get("enabled", False), value.get("increment", 5)
    if not isinstance(enabled, bool) or isinstance(increment, bool) or increment not in INCREMENTS:
        raise ValueError("Choose a rounding increment of 1, 2, 5, 10, 20, 25 or 50 percent.")
    return {"enabled": enabled, "increment": int(increment)}


def round_feed_ratios(ratios, increment):
    """Half-up whole percent then increment; source order resolves residuals."""
    rounding_settings({"increment": increment})
    if not ratios or any(not math.isfinite(r) or r < 0 for r in ratios) or sum(ratios) <= 0:
        raise ManualBlendPlanningError("Rounding requires finite, nonnegative feed ratios.")
    total = sum(ratios)
    units, remaining = [], 100 // increment
    for ratio in ratios:
        whole = (Decimal(str(ratio / total)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        snapped = int((whole / increment).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        amount = min(snapped, remaining)
        units.append(amount)
        remaining -= amount
    # First positive source receives any remaining increment units. A zero
    # original source is never introduced solely to repair the total.
    if remaining:
        units[next(i for i, r in enumerate(ratios) if r > 0)] += remaining
    return [u * increment / 100 for u in units]


def rounding_audit_rows(sequence):
    rows = []
    for state in sequence or []:
        for key, audit in (state.get("_rounding_audit") or {}).items():
            kind, source = key.split("|", 1)
            rows.append({"Blend ID": state["Blend ID"], "Source": source, "Source type": kind,
                         "Original ratio (%)": 100 * audit["original_source_feed_ratio"],
                         "Rounded ratio (%)": 100 * audit["rounded_source_feed_ratio"],
                         "Increment (%)": audit["ratio_rounding_increment"],
                         "Original start": audit["original_state_start"], "Original end": audit["original_state_end"],
                         "Recalculated start": state.get("_exact_start", state["Start Datetime"]),
                         "Recalculated end": state.get("_exact_end", state["End Datetime"]),
                         "Timing adjustment": audit.get("rounding_timing_reason", ""),
                         "Sequence status": audit.get("rounding_stop_reason", "")})
    return rows


def _direct_tip_duration(planner, start, end, physical_rate, shares, blend_id):
    """Find the latest feasible end no later than end, using [start, end) deliveries.

    Availability is a step function, so binary search on duration is unsafe.
    Each shortage gives an upper bound on every feasible shorter duration.
    Recheck that bound until it fits or no positive duration remains.
    """
    rates = {parent_grade_block_name(source).upper(): physical_rate * ratio
             for (kind, source), ratio in shares.items()
             if kind == "grade_block" and ratio > 0}
    if not rates:
        return end, []
    payloads = planner.payload_transactions
    eligible = pd.DataFrame(columns=["source", "payload", "delivered_datetime"])
    if not payloads.empty and "direct_tip_eligible" in payloads:
        eligible = payloads[payloads["direct_tip_eligible"].map(planner._truthy)].copy()
    eligible["_delivered"] = pd.to_datetime(eligible["delivered_datetime"], errors="coerce")
    eligible["_tonnes"] = pd.to_numeric(eligible["payload"], errors="coerce").fillna(0).clip(lower=0)
    eligible = eligible[(eligible["_delivered"] >= start) & (eligible["_delivered"] < end)]
    eligible["_parent"] = eligible["source"].map(lambda s: parent_grade_block_name(s).upper())
    histories = {}
    for source in rates:
        rows = eligible[eligible["_parent"].eq(source)].sort_values("_delivered")
        histories[source] = (rows["_delivered"], rows["_tonnes"].cumsum())
    limited = set()
    # Each retry either converges or excludes at least one delivery timestamp.
    for _ in range(len(eligible) + 2):
        duration = (end - start).total_seconds() / 3600
        fitted_end = end
        for source, rate in rates.items():
            times, cumulative = histories[source]
            count = times.searchsorted(pd.Timestamp(end), side="left")
            available = float(cumulative.iloc[count - 1]) if count else 0.0
            if rate * duration <= available + 1e-7:
                continue
            limited.add(source)
            # Keep exact minute boundaries despite float noise. Otherwise floor
            # to datetime precision to respect the allocation tolerance.
            raw_micros = available / rate * 3_600_000_000
            micros = round(raw_micros)
            if micros * rate / 3_600_000_000 > available + 1e-7:
                micros = math.floor(raw_micros)
            fitted_end = min(fitted_end, start + timedelta(microseconds=micros))
        if fitted_end == end:
            return end, sorted(limited)
        if fitted_end <= start:
            return start, sorted(limited)
        end = fitted_end
    raise ManualBlendPlanningError(f"Rounded Blend {blend_id}: direct-tip duration did not converge.")


def recalculate_rounded_plan(optimised_report, planner, increment=5):
    """Replay each optimiser state independently, then freeze the new result.

    Depletion-controlled boundaries move with the new recipes. Calendar and
    horizon boundaries stay absolute; payload eligibility uses the new windows.
    Direct-tip shortages shorten a recipe; the shortened delivery window must
    still be feasible. Otherwise retry with the direct-tip share divided evenly
    among stockpile sources. If that fails, retain the valid contiguous prefix.
    """
    converter = OptimisedToManualPlan(optimised_report, planner.stockpile_data)
    data = converter._prepared_report()
    data["_source_name"] = data["source"].astype(str).str.strip().str.upper()
    blocks = data["_source_type"].eq("grade_block")
    data.loc[blocks, "_source_name"] = data.loc[blocks, "_source_name"].map(parent_grade_block_name)
    if "parent_stockpile" in data:
        parent = data["parent_stockpile"].fillna("").astype(str).str.strip().str.upper()
        mask = data["_source_type"].eq("stockpile") & parent.ne("")
        data.loc[mask, "_source_name"] = parent[mask]
    groups = list(data.groupby(converter._state_group_columns(data), sort=False, dropna=False))
    inventory = deepcopy(planner._inventory_template)
    states, allocations, audits = [], {}, {}
    stop_reason = ""
    cursor, previous_end, produced = None, None, 0.0
    horizon = data["_end"].max().to_pydatetime()
    period_boundaries = set(planner._period_boundaries())
    for recipe_index, (_, group) in enumerate(groups, 1):
        first = group.iloc[0]
        original_start, original_end = first["_start"].to_pydatetime(), first["_end"].to_pydatetime()
        current = original_start if cursor is None or original_start > previous_end else cursor
        if original_start in period_boundaries:
            current = max(current, original_start)
        if current >= horizon:
            break
        duration = (original_end - original_start).total_seconds() / 3600
        physical_rate = float(group["_tonnes"].sum()) / duration
        amounts = group.groupby(["_source_type", "_source_name"], sort=False)["_tonnes"].sum()
        names = list(amounts.index)
        original = [float(t / amounts.sum()) for t in amounts]
        rounded = round_feed_ratios(original, increment)
        shares = dict(zip(names, rounded))
        blend_id = str(recipe_index)
        recipe_start, opening_inventory, opening_produced = current, inventory, produced
        state_offset = len(states)

        def attempt_recipe(recipe_shares, fallback_reason=""):
            shares = recipe_shares
            inventory = deepcopy(opening_inventory)
            produced, current = opening_produced, recipe_start
            states, allocations, audit = [], {}, {}
            error = None
            try:
                sources = [name for (kind, name), ratio in shares.items() if kind == "stockpile" and ratio > 0]
                definition = {"Blend ID": blend_id, "Sources": ", ".join(sources),
                              "Source Ratios": ", ".join(str(shares[("stockpile", s)]) for s in sources)}
                planner.blends.update(planner._normalise_blends([definition]))
                exhausted_at_original_end = False
                for _, row in group[group["_source_type"].eq("stockpile")].iterrows():
                    closing = pd.to_numeric(row.get("source_closing_balance"), errors="coerce")
                    exhausted_at_original_end |= pd.notna(closing) and closing <= 1e-5 and shares.get(("stockpile", row["_source_name"]), 0) > 0
                depletion_times = []
                for source in sources:
                    balance = sum(c["balance"] for c in inventory.get(source, []))
                    if balance <= 1e-7:
                        raise ManualBlendPlanningError(f"Rounded Blend {blend_id}: {source} has no remaining inventory. Review the manual recipe.")
                    depletion_times.append(current + timedelta(hours=balance / (physical_rate * shares[("stockpile", source)])))
                intended_end = min(depletion_times) if exhausted_at_original_end and depletion_times else current + timedelta(hours=duration)
                if original_end in period_boundaries:
                    intended_end = min(intended_end, original_end)
                if recipe_index < len(groups):
                    next_start = groups[recipe_index][1].iloc[0]["_start"].to_pydatetime()
                    if next_start > original_end:
                        intended_end = min(intended_end, original_end)
                recipe_end = min([intended_end, horizon, *depletion_times])
                if recipe_index == len(groups) and not exhausted_at_original_end:
                    recipe_end = min([horizon, *depletion_times])
                audit = {f"{kind}|{name}": {
                    "ratio_rounding_increment": increment, "original_steady_state_number": converter._native_value(first["steady_state_number"]),
                    "original_source_feed_ratio": old, "rounded_source_feed_ratio": new,
                    "original_state_start": original_start.isoformat(), "original_state_end": original_end.isoformat(),
                } for (kind, name), old, new in zip(names, original, [shares[key] for key in names])}
                if fallback_reason:
                    for entry in audit.values():
                        entry["rounding_timing_reason"] = fallback_reason
                while current < recipe_end - timedelta(microseconds=1):
                    ends = [(recipe_end, "Rounded recipe completion")]
                    ends.extend((p, "Period boundary") for p in planner._period_boundaries() if current < p < recipe_end)
                    for source in sources:
                        chunk = planner._current_chunk(inventory.get(source))
                        if chunk is None:
                            raise ManualBlendPlanningError(f"Rounded recipe exhausted {source}.")
                        ends.append((current + timedelta(hours=chunk["balance"] / (physical_rate * shares[("stockpile", source)])), "Inventory boundary"))
                    end, trigger = min(ends, key=lambda item: item[0])
                    state = {"steady_state_number": state_offset + len(states) + 1, "blend_ID": blend_id,
                             "start_datetime": current, "end_datetime": end, "trigger": trigger,
                             "crusher_rate": float(first.get("crusher_rate_output", physical_rate)),
                             "period": planner._period_number(current), "rounding_audit": audit}
                    # Product-build boundaries are recalculated from the rounded mix.
                    for _ in range(20):
                        fitted_end, limited_sources = _direct_tip_duration(
                            planner, current, end, physical_rate, shares, blend_id
                        )
                        if fitted_end < end:
                            end = fitted_end
                            recipe_end = min(recipe_end, end)
                            state["trigger"] = "Direct-tip availability"
                            reason = "Shortened for direct-tip availability: " + ", ".join(limited_sources)
                            for entry in audit.values():
                                entry["rounding_timing_reason"] = reason
                        if end <= current:
                            raise ManualBlendPlanningError(
                                f"Rounded Blend {blend_id}: no positive duration fits the eligible direct tip "
                                f"for {', '.join(limited_sources)}. Shortening the window leaves insufficient "
                                "deliveries. Use exact conversion or review the inputs."
                            )
                        hours = (end - current).total_seconds() / 3600
                        feed = physical_rate * hours
                        state.update(end_datetime=end, steady_state_duration=hours, feed_capacity_tonnes=feed,
                                     state_key=planner.state_key(current, end, blend_id))
                        planner.attach_direct_tip_candidates([state])
                        selected = {}
                        for (kind, source), ratio in shares.items():
                            if kind != "grade_block" or ratio <= 0:
                                continue
                            candidates = [c for c in state["direct_tip_candidates"] if parent_grade_block_name(c["source"]).upper() == parent_grade_block_name(source).upper()]
                            available = sum(c["available_tonnes"] for c in candidates)
                            requested = feed * ratio
                            if requested > available + 1e-7:
                                raise ManualBlendPlanningError(f"Rounded Blend {blend_id}: {source} needs {requested:,.2f} t of direct tip but only {available:,.2f} t is eligible in the recalculated window. Use exact conversion or review the inputs.")
                            for candidate in candidates:
                                selected[candidate["source"]] = requested * candidate["available_tonnes"] / available if available else 0
                        product = sum(feed * shares[("stockpile", source)] * planner._chunk_quantity_coefficient(
                            planner._current_chunk(inventory[source]), planner.product_build_tonnes_stream, source) for source in sources)
                        for c in state["direct_tip_candidates"]:
                            amount = selected.get(c["source"], 0)
                            quantity = c["source_properties"].get(planner.product_build_tonnes_stream, c["available_tonnes"])
                            product += amount * float(quantity or 0) / c["available_tonnes"]
                        remaining = planner._build_completion_distance(produced)
                        if remaining is not None and product > remaining + 1e-6:
                            new_end = current + timedelta(hours=hours * remaining / product)
                            if new_end >= end - timedelta(microseconds=1):
                                break
                            end = new_end
                            state["trigger"] = "Product build completion"
                            continue
                        break
                    else:
                        raise ManualBlendPlanningError("Rounded product-build timing did not converge; review the direct-tip windows.")
                    if end <= current:
                        break
                    state["stockpile_source_tonnes"] = {s: feed * shares[("stockpile", s)] for s in sources}
                    planner.validate_allocations([state], {state["state_key"]: selected})
                    for source, amount in state["stockpile_source_tonnes"].items():
                        planner._consume_inventory(inventory, source, amount)
                    produced += product
                    states.append(state)
                    allocations[state["state_key"]] = selected
                    current = end
                if not states:
                    raise ManualBlendPlanningError(f"Rounded Blend {blend_id}: no positive duration is available.")
            except ManualBlendPlanningError as failure:
                error = failure
            return {"states": states, "allocations": allocations, "audit": audit,
                    "inventory": inventory, "produced": produced, "shares": shares,
                    "error": error, "end": states[-1]["end_datetime"] if states else recipe_start}

        chosen = attempt_recipe(shares)
        failure_details = str(chosen["error"] or "")
        if chosen["error"]:
            stockpile_keys = [key for key in shares if key[0] == "stockpile"]
            direct_tip_share = sum(ratio for (kind, _), ratio in shares.items() if kind == "grade_block")
            if stockpile_keys and direct_tip_share > 0:
                reclaim_shares = {
                    key: (ratio + direct_tip_share / len(stockpile_keys) if key[0] == "stockpile" else 0.0)
                    for key, ratio in shares.items()
                }
                # Apply the selected increment again after redistribution.
                # Preserve source order so ties and the 100% residual follow
                # the same rule as the initial rounding pass.
                reclaim_shares = dict(zip(reclaim_shares, round_feed_ratios(
                    list(reclaim_shares.values()), increment
                )))
                reason = ("Reclaim-only fallback: direct-tip share split evenly across stockpile sources; "
                          f"rounded again to {increment}% increments; duration and grades recalculated")
                fallback = attempt_recipe(reclaim_shares, reason)
                if fallback["error"]:
                    failure_details += f" Reclaim-only fallback failed: {fallback['error']}"
                    # Retain the longest contiguous validated portion. Never
                    # combine two attempts that consumed the same opening stock.
                    if fallback["end"] > chosen["end"]:
                        chosen = fallback
                else:
                    chosen = fallback
            elif direct_tip_share > 0:
                failure_details += " Reclaim-only fallback has no stockpile sources."

        if chosen["states"]:
            states.extend(chosen["states"])
            allocations.update(chosen["allocations"])
            audits[blend_id] = chosen["audit"]
            inventory, produced = chosen["inventory"], chosen["produced"]
            chosen_sources = [name for (kind, name), ratio in chosen["shares"].items()
                              if kind == "stockpile" and ratio > 0]
            planner.blends.update(planner._normalise_blends([{
                "Blend ID": blend_id, "Sources": ", ".join(chosen_sources),
                "Source Ratios": ", ".join(str(chosen["shares"][("stockpile", name)]) for name in chosen_sources),
            }]))
        if chosen["error"]:
            stop_reason = f"Partial sequence: stopped at Rounded Blend {blend_id}. {failure_details}"
            if not states:
                raise ManualBlendPlanningError(stop_reason + " No rounded states could be generated; the previous plan is unchanged.")
            break
        cursor, previous_end = chosen["end"], original_end
    try:
        report = planner.build_report(states, allocations)
    except ManualBlendPlanningError as error:
        valid_count = 0
        for count in range(1, len(states) + 1):
            try:
                planner.build_report(states[:count], allocations)
            except ManualBlendPlanningError:
                break
            valid_count = count
        if not valid_count:
            raise
        stop_reason = f"Partial sequence: stopped at Rounded Blend {states[valid_count]['blend_ID']}. {error}"
        states = states[:valid_count]
        allocations = {state["state_key"]: allocations[state["state_key"]] for state in states}
        report = planner.build_report(states, allocations)
    if stop_reason:
        for entry in states[-1]["rounding_audit"].values():
            entry["rounding_stop_reason"] = stop_reason
        report = planner.build_report(states, allocations)
    if report.empty:
        raise ManualBlendPlanningError("The rounded conversion produced no feed.")
    transfer = OptimisedToManualPlan(report, planner.stockpile_data).build()
    # The recalculated report is a new immutable starting point for manual edits.
    for row in transfer["sequence_rows"]:
        row["Origin"] = "Rounded"
        row["_rounding_audit"] = audits[row["Blend ID"]]
        state = next(s for s in states if s["steady_state_number"] == row["_optimised_steady_state"])
        row["_exact_start"] = state["start_datetime"].isoformat()
        row["_exact_end"] = state["end_datetime"].isoformat()
    for definition in transfer["blend_definitions"]:
        source_audit = audits[definition["Blend ID"]]
        source_names = converter._identifier_values(definition["Sources"])
        ratios = [source_audit[f"stockpile|{s}"]["rounded_source_feed_ratio"] for s in source_names]
        definition["Source Ratios"] = ", ".join(f"{r:.6f}" for r in ratios)
        config = transfer["blend_config_table_inputs"][definition["Blend ID"]]
        config["source_ratios"] = ratios
        config["weights"] = [r / sum(ratios) for r in ratios] if ratios else []
        config["rate_mode"] = "rounded"
    transfer["rounding_stop_reason"] = stop_reason
    transfer["rounding_settings"] = rounding_settings({"enabled": True, "increment": increment})
    transfer["rounding_unfilled_hours"] = max(0.0, (horizon - states[-1]["end_datetime"]).total_seconds() / 3600)
    return transfer, states, allocations, report
