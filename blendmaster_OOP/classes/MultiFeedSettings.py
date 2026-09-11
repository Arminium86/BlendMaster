"""Scenario-owned simultaneous tipping-point configuration."""

from copy import deepcopy
import math


def number(value, label, *, positive=False):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be numeric.") from None
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(f"{label} must be {'positive' if positive else 'non-negative'}.")
    return result


def multi_feed_settings(value=None):
    value = value or {}
    if value.get("schema_version", 1) != 1:
        raise ValueError("Unsupported multi-tipping-point settings version.")
    mode = value.get("mode", "single")
    if mode not in {"single", "multi_tipping_point", "combined_opf"}:
        raise ValueError("Invalid material-flow mode.")
    lanes, names = [], set()
    for original in value.get("tipping_points", []):
        row = deepcopy(original)
        name, opf = str(row.get("name", "")).strip(), str(row.get("opf", "")).strip()
        if not name or not opf or name.casefold() in names:
            raise ValueError("Each tipping point needs a unique name and an OPF.")
        names.add(name.casefold())
        if name.upper().replace("-", "_") == "TOTAL_FEED_PC":
            raise ValueError("Configure the physical tipping points that make up Total_Feed.")
        row.update(name=name, opf=opf, direct_tip_enabled=bool(row.get("direct_tip_enabled", True)),
                   rom_area=str(row.get("rom_area") or name).strip(),
                   targets_by_period=deepcopy(row.get("targets_by_period") or {}),
                   solver_config=deepcopy(row.get("solver_config") or {}))
        if row["solver_config"].get("selected_data_stream", "adjusted_product") not in {"insitu", "modelled_rom", "adjusted_rom", "modelled_product", "adjusted_product"}:
            raise ValueError(f"{name}: invalid grade stream.")
        for key in ("min_stockpiles", "max_stockpiles"):
            if row.get(key) is not None:
                count = number(row[key], f"{name} {key}")
                if count != int(count):
                    raise ValueError(f"{name}: stockpile counts must be whole numbers.")
                row[key] = int(count)
        if row.get("min_stockpiles") is not None and row.get("max_stockpiles") is not None and row["min_stockpiles"] > row["max_stockpiles"]:
            raise ValueError(f"{name}: minimum stockpiles exceeds maximum.")
        for period, target in row["targets_by_period"].items():
            target["crusher_rate"] = number(target.get("crusher_rate"), f"{name} / {period} crusher rate")
            if 'max_reclaim_rate' in target:
                target['max_reclaim_rate'] = number(target['max_reclaim_rate'], f'{name} / {period} Max Reclaim Rate')
            lo, hi = (number(target.get(k, default), f"{name} {k}") for k, default in (("direct_feed_ratio_min", 0), ("direct_feed_ratio_max", 1)))
            if not 0 <= lo <= hi <= 1:
                raise ValueError(f"{name}: direct-tip ratios must satisfy 0 ≤ min ≤ max ≤ 1.")
            for analyte in ("fe", "si", "al", "p", "mn"):
                low, high = f"target_{analyte}_min", f"target_{analyte}_max"
                if low in target and high in target:
                    if number(target[low], low) > number(target[high], high):
                        raise ValueError(f"{name} / {period}: {analyte.upper()} minimum exceeds maximum.")
        lanes.append(row)
    if mode != "single" and len(lanes) < 2:
        raise ValueError("Simultaneous feed requires at least two physical tipping points.")
    if mode == "multi_tipping_point" and len({r["opf"] for r in lanes}) != 1:
        raise ValueError("Multi-tipping-point mode feeds one OPF. Use Combined OPF for multiple OPFs.")
    rules, seen = [], set()
    for original in value.get("rehandle_rules", []):
        subset = str(original.get("subset", "")).strip()
        point = str(original.get("tipping_point", "")).strip()
        key = (subset.casefold(), point.casefold())
        if not subset or point.casefold() not in names or key in seen:
            raise ValueError("Rehandle Movement Rules need a unique subset/tipping-point pair.")
        seen.add(key)
        rules.append(dict(subset=subset, tipping_point=point, allowed=bool(original.get("allowed", True))))
    rates = {}
    for source, points in (value.get("route_reclaim_rates") or {}).items():
        if not str(source).strip() or any(str(point).casefold() not in names for point in points):
            raise ValueError("Route capacity needs a stockpile and a configured tipping point.")
        rates[str(source)] = {str(point): number(rate, f"{source} → {point} reclaim rate") for point, rate in points.items()}
    return dict(schema_version=1, mode=mode, tipping_points=lanes, rehandle_rules=rules,
                route_reclaim_rates=rates,
                source_subsets={str(k): str(v).strip() for k, v in (value.get("source_subsets") or {}).items()},
                opf_scenarios=deepcopy(value.get("opf_scenarios") or {}),
                allow_opf_compensation=bool(value.get("allow_opf_compensation", False)))


def build_opfs(setting):
    raw = setting.get("opf", "")
    return tuple(sorted({str(v).strip() for v in (raw.split(",") if isinstance(raw, str) else raw or []) if str(v).strip()}))


def scoped_builds(builds, settings):
    """Disjoint OPF groups own independent sequential product-build balances."""
    rows = deepcopy(builds or [])
    if settings["mode"] != "combined_opf":
        return rows
    opfs = {p["opf"] for p in settings["tipping_points"]}
    groups = set()
    for row in rows:
        group = build_opfs(row)
        if not group or not set(group) <= opfs:
            raise ValueError(f"{row.get('build_name', 'Product target')}: OPF must name configured OPFs, separated by commas for a shared build.")
        if len(group) > 1 and not settings["allow_opf_compensation"]:
            raise ValueError("Enable 'Allow OPFs to compensate in shared builds' to use a multi-OPF product target.")
        if any(set(group) & set(other) and group != other for other in groups):
            raise ValueError("Each OPF must belong to one product-build group. Separate and shared groups cannot overlap.")
        groups.add(group)
        from urllib.parse import quote
        row.update(opf=", ".join(group), opf_scope="+".join(quote(x, safe='') for x in group), contributing_opfs=list(group))
    return rows


def period_lanes(settings, period, base_target):
    result = []
    for point in settings["tipping_points"]:
        target = {**deepcopy(base_target), **deepcopy(point["targets_by_period"].get(period, {}))}
        result.append({**point, "target": target})
    for opf in {r["opf"] for r in result}:
        brands = {r["target"].get("brand", "") for r in result if r["opf"] == opf and r["target"]["crusher_rate"] > 0}
        if len(brands) > 1:
            raise ValueError(f"All tipping points feeding {opf} must run the same brand in {period}.")
    return result


def route_allowed(settings, source, point):
    subset = settings["source_subsets"].get(source, "")
    rule = next((r for r in settings["rehandle_rules"] if r["subset"].casefold() == subset.casefold()
                 and r["tipping_point"].casefold() == point.casefold()), None)
    area = next((r["rom_area"] for r in settings["tipping_points"] if r["name"] == point), point)
    return rule["allowed"] if rule is not None else bool(subset and subset.casefold() == area.casefold())
