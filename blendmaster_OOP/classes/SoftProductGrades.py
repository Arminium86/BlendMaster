"""Linear soft-grade objectives and the matching numeric audit calculation.

Grades are percentages. Penalty tonnes use each grade's declared weight (e.g.
product DMT), so weighted assay averages and the LP use the same denominator.
The perspective of a convex piecewise-linear function remains linear when the
blend tonnes are variables. Cumulative objectives subtract the fixed opening
penalty: only the change enters this decision's objective.
"""

import math

from pulp import LpVariable, lpSum
from classes.PhaseSchemas import SCHEMA_ANALYTES
from classes.ProductQualityLimits import quality_fields
from classes.ProductTargetModes import target_mode_fields


SCALES = {"fe": 1.0, "si": 1.0, "al": 1.0, "p": .01, "mn": .01}


def objective_config(value=None):
    if value is not None and not isinstance(value, dict):
        raise ValueError("Soft grade preferences must be a mapping.")
    value = value or {}
    if type(value.get("schema_version", 1)) is not int or value.get("schema_version", 1) != 1:
        raise ValueError("Unsupported soft grade preferences schema version.")

    def number(raw, name, minimum=0, strict=False):
        try:
            result = float(raw)
        except (TypeError, ValueError):
            result = float("nan")
        if isinstance(raw, bool) or not math.isfinite(result) or result < minimum or (strict and result == minimum):
            raise ValueError(f"{name} must be finite and {'greater than' if strict else 'at least'} {minimum}.")
        return result

    def enabled(raw, name):
        if not isinstance(raw, bool):
            raise ValueError(f"{name} must be true or false.")
        return raw

    result = {
        "schema_version": 1,
        "target_weight": number(value.get("target_weight", 1), "Soft target weight"),
        "limit_multiplier": number(value.get("limit_multiplier", 5), "LQL/HQL breach multiplier", 1, True),
        "shape": value.get("shape", "piecewise_linear"),
        "similarity_mode": value.get("similarity_mode", "off"),
        "closeness_weight": number(value.get("closeness_weight", 1), "Source closeness weight"),
        "dispersion_weight": number(value.get("dispersion_weight", 1), "Source dispersion weight"),
        "include_direct_tip": enabled(value.get("include_direct_tip", False), "Include direct tip"),
        "analytes": {},
    }
    if not isinstance(result["shape"], str) or result["shape"] not in {"linear", "piecewise_linear"}:
        raise ValueError("Penalty shape must be linear or piecewise_linear.")
    if not isinstance(result["similarity_mode"], str) or result["similarity_mode"] not in {"off", "closeness", "dispersion", "both"}:
        raise ValueError("Source similarity must be off, closeness, dispersion or both.")
    supplied = value.get("analytes", {})
    if not isinstance(supplied, dict):
        raise ValueError("Soft grade analytes must be a mapping.")
    for a in SCHEMA_ANALYTES:
        item = supplied.get(a, {})
        if not isinstance(item, dict):
            raise ValueError(f"{a.upper()} preferences must be a mapping.")
        result["analytes"][a] = {
            "enabled": enabled(item.get("enabled", True), f"{a.upper()} penalty enabled"),
            "scale": number(item.get("scale", SCALES[a]), f"{a.upper()} scale", 0, True),
            "weight": number(item.get("weight", 1), f"{a.upper()} penalty weight"),
            "similarity_enabled": enabled(item.get("similarity_enabled", True), f"{a.upper()} similarity enabled"),
            "similarity_weight": number(item.get("similarity_weight", 1), f"{a.upper()} similarity weight"),
        }
    return result


def shape_cost(distance, weight, shape):
    """distance is normalized absolute grade-metal deviation, not an average."""
    return distance + (2 * max(distance - weight, 0) + 2 * max(distance - 2 * weight, 0)
                       if shape == "piecewise_linear" else 0)


def analyte_audit(build, analyte, metal, weight, config=None):
    config = objective_config(config)
    modes, grades = target_mode_fields(build), quality_fields(build)
    soft = modes["target_mode"] == "soft"
    target = grades[f"target_{analyte}_target"]
    low = grades[f"target_{analyte}_lql"] if soft else build.get(f"target_{analyte}_min", 0)
    high = grades[f"target_{analyte}_hql"] if soft else build.get(f"target_{analyte}_max", 100)
    weight = max(float(weight), 0)
    actual = float(metal) / weight if weight > 1e-9 else None
    deviation = actual - target if actual is not None and target is not None else None
    below = max(float(low) - actual, 0) if low is not None and actual is not None else 0.0
    above = max(actual - float(high), 0) if high is not None and actual is not None else 0.0
    lql, hql = grades[f"target_{analyte}_lql"], grades[f"target_{analyte}_hql"]
    below_lql = max(lql - actual, 0) if lql is not None and actual is not None else 0.0
    above_hql = max(actual - hql, 0) if hql is not None and actual is not None else 0.0
    settings = config["analytes"][analyte]
    factor = 100 * config["target_weight"] * settings["weight"] if soft and settings["enabled"] else 0
    distance = abs(deviation or 0) * weight / settings["scale"]
    target_penalty = factor * shape_cost(distance, weight, config["shape"])
    limit_mode = modes[f"target_{analyte}_limit_mode"] if soft else "hard"
    limit_penalty = (factor * config["limit_multiplier"] * (below + above) * weight / settings["scale"]
                     if limit_mode == "soft" else 0.0)
    within = actual is not None and below < 1e-7 and above < 1e-7
    return dict(actual_grade=actual, grade_weight_tonnes=weight, target=target, lql=grades[f"target_{analyte}_lql"],
                hql=grades[f"target_{analyte}_hql"], lower_bound=low, upper_bound=high,
                target_deviation=deviation, below_lql=below_lql, above_hql=above_hql,
                below_active_lower_bound=below, above_active_upper_bound=above,
                limit_mode=limit_mode, within_limits=within,
                hard_limits_satisfied=within if limit_mode == "hard" else actual is not None,
                target_penalty=target_penalty, limit_penalty=limit_penalty,
                total_penalty=target_penalty + limit_penalty)


def add_soft_objective(prob, x_vars, builds, states, values, coefficients, config=None):
    """Add Soft constraints independently of legacy completion/repair switches."""
    config = objective_config(config)
    objective = 0
    for lane_index, (lane, build) in enumerate(builds.items()):
        modes, grades = target_mode_fields(build), quality_fields(build)
        if modes["target_mode"] != "soft":
            continue
        cumulative = modes["target_evaluation_basis"] == "cumulative_build"
        state = states.get(lane) or {}
        for a in SCHEMA_ANALYTES:
            key = f"soft_{lane_index}_{a}"
            settings = config["analytes"][a]
            opening_weight = float(state.get(f"grade_{a}_weight", state.get("tonnes", 0)) or 0) if cumulative else 0
            opening_metal = float(state.get(f"grade_{a}_metal", 0) or 0) if cumulative else 0
            weight = opening_weight + lpSum(x * c for x, c in zip(x_vars, coefficients[lane][a]))
            metal = opening_metal + lpSum(x * c * (g or 0) for x, c, g in zip(x_vars, coefficients[lane][a], values[lane][a]))
            target, low, high = (grades[f"target_{a}_{p}"] for p in ("target", "lql", "hql"))
            factor = 100 * config["target_weight"] * settings["weight"] if settings["enabled"] else 0
            if target is not None and factor > 0:
                positive = LpVariable(key + "_above_target", lowBound=0)
                negative = LpVariable(key + "_below_target", lowBound=0)
                prob += positive - negative == (metal - target * weight) / settings["scale"]
                distance = positive + negative
                objective += factor * distance
                if config["shape"] == "piecewise_linear":
                    for breakpoint in (1, 2):
                        excess = LpVariable(key + f"_beyond_{breakpoint}_scales", lowBound=0)
                        prob += excess >= distance - breakpoint * weight
                        objective += 2 * factor * excess
            for bound_name, bound, direction in (("lql", low, -1), ("hql", high, 1)):
                if bound is None:
                    continue
                breach = direction * (metal - bound * weight) / settings["scale"]
                if modes[f"target_{a}_limit_mode"] == "hard":
                    prob += breach <= 0, key + "_hard_" + bound_name
                elif factor > 0:
                    slack = LpVariable(key + "_breach_" + bound_name, lowBound=0)
                    prob += slack >= breach
                    objective += factor * config["limit_multiplier"] * slack
            if cumulative:
                objective -= analyte_audit(build, a, opening_metal, opening_weight, config)["total_penalty"]
    return objective


def solution_audit(builds, states, values, coefficients, solution, config=None):
    config = objective_config(config)
    records = []
    for lane, build in builds.items():
        modes = target_mode_fields(build)
        state = states.get(lane) or {}
        cumulative = modes["target_mode"] == "soft" and modes["target_evaluation_basis"] == "cumulative_build"
        for a in SCHEMA_ANALYTES:
            added_weight = sum(x * c for x, c in zip(solution, coefficients[lane][a]))
            added_metal = sum(x * c * (g or 0) for x, c, g in zip(solution, coefficients[lane][a], values[lane][a]))
            opening_weight = float(state.get(f"grade_{a}_weight", state.get("tonnes", 0)) or 0) if cumulative else 0
            opening_metal = float(state.get(f"grade_{a}_metal", 0) or 0) if cumulative else 0
            current = analyte_audit(build, a, opening_metal + added_metal, opening_weight + added_weight, config)
            opening = analyte_audit(build, a, opening_metal, opening_weight, config)
            records.append(dict(lane=lane, build_name=build.get("build_name", ""), opf=build.get("opf", ""),
                                brand=build.get("brand", ""), analyte=a, target_mode=modes["target_mode"],
                                evaluation_basis=modes["target_evaluation_basis"], **current,
                                opening_penalty=opening["total_penalty"],
                                applied_penalty=current["total_penalty"] - opening["total_penalty"]))
    return records


SIMILARITY_TOTALS = ("eligible_weight", "closeness_sum", "dispersion_sum", "closeness_reward", "dispersion_penalty")


def source_preference(analyte, grade, target, weight, direct_tip, config):
    """Linear coefficients from fixed source-to-Target distances, per grade weight."""
    settings = config["analytes"][analyte]
    result = dict.fromkeys(SIMILARITY_TOTALS, 0.0)
    if (config["similarity_mode"] == "off" or not settings["similarity_enabled"] or target is None
            or grade is None or weight <= 0 or (direct_tip and not config["include_direct_tip"])):
        return result
    distance = abs(grade - target) / settings["scale"]
    closeness = 1 / (1 + distance)
    result.update(eligible_weight=weight, closeness_sum=closeness * weight, dispersion_sum=distance ** 2 * weight)
    if config["similarity_mode"] in {"closeness", "both"}:
        result["closeness_reward"] = 100 * settings["similarity_weight"] * config["closeness_weight"] * result["closeness_sum"]
    if config["similarity_mode"] in {"dispersion", "both"}:
        result["dispersion_penalty"] = 100 * settings["similarity_weight"] * config["dispersion_weight"] * result["dispersion_sum"]
    return result


def similarity_coefficients(events, builds, values, coefficients, config=None, selected_stream="adjusted_product"):
    config = objective_config(config)
    soft = {lane: b for lane, b in builds.items() if target_mode_fields(b)["target_mode"] == "soft"}
    validate_similarity_stream(soft.values(), config, selected_stream)
    result = {}
    for lane, build in soft.items():
        grades = quality_fields(build)
        for a in SCHEMA_ANALYTES:
            result[(lane, a)] = [source_preference(a, g, grades[f"target_{a}_target"], c,
                                bool(event.is_grade_block or getattr(event, '_transport_material', {}).get('source_type') == 'grade_block'), config)
                                if event.is_stockpile or event.is_grade_block or getattr(event, '_transport_arrival', False)
                                else dict.fromkeys(SIMILARITY_TOTALS, 0.0)
                                for event, g, c in zip(events, values[lane][a], coefficients[lane][a])]
    return result


def validate_similarity_stream(builds, config, selected_stream):
    config = objective_config(config)
    if (config["similarity_mode"] != "off" and selected_stream not in {"modelled_product", "adjusted_product"}
            and any(target_mode_fields(b)["target_mode"] == "soft" for b in builds)):
        raise ValueError("Source similarity requires a product grade stream. Select modelled_product or adjusted_product in Data Streams; ROM and insitu grades cannot be compared with a product Target.")


def similarity_summary(totals):
    weight = totals["eligible_weight"]
    return {**totals, "source_closeness_score": totals["closeness_sum"] / weight if weight > 1e-9 else None,
            "source_dispersion_score": totals["dispersion_sum"] / weight if weight > 1e-9 else None,
            "applied_similarity_penalty": totals["dispersion_penalty"] - totals["closeness_reward"]}
