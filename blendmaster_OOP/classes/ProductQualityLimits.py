"""Row-owned specifications and mode configuration; Hard retains Min/Max bounds.

Flat target_<analyte>_{lql,target,hql} fields are the editable/persisted values.
The existing versioned PhaseSchemas record is derived for runtime consumers.
Missing values stay open (None); legacy Min/Max are never inferred as targets.
"""

from copy import deepcopy
import math

from classes.PhaseSchemas import SCHEMA_ANALYTES, product_quality_limits
from classes.ProductTargetModes import target_mode_fields


QUALITY_PARTS = ("lql", "target", "hql")
QUALITY_FIELDS = tuple(f"target_{a}_{part}" for a in SCHEMA_ANALYTES for part in QUALITY_PARTS)


def quality_fields(row, *, validate=True):
    """Read canonical fields or agent grade dictionaries; explicit blanks win."""
    nested = (row.get("quality_limits") or {}).get("limits", {})
    values = {}
    for a in SCHEMA_ANALYTES:
        entry = nested.get(a, {})
        for name in ("grades", "grade_targets", "target_grades"):
            grades = row.get(name) or {}
            supplied = next((grades[k] for k in (a, a.upper(), a.capitalize()) if k in grades), None)
            if isinstance(supplied, dict):
                entry = supplied
                break
        for part in QUALITY_PARTS:
            key = f"target_{a}_{part}"
            raw = row[key] if key in row else entry.get(part)
            if not validate:
                values[key] = deepcopy(raw)
                continue
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                values[key] = None
                continue
            try:
                value = float(raw.replace(",", "") if isinstance(raw, str) else raw)
            except (TypeError, ValueError, OverflowError):
                value = math.nan
            if isinstance(raw, bool) or not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError(f"{a.title()} {part.upper() if part != 'target' else 'Target'} must be a finite number from 0 to 100, or blank.")
            values[key] = value
        if validate:
            low, target, high = (values[f"target_{a}_{part}"] for part in QUALITY_PARTS)
            for left, right, label in ((low, high, "LQL must not exceed HQL"),
                                       (low, target, "Target must be at least LQL"),
                                       (target, high, "Target must not exceed HQL")):
                if left is not None and right is not None and left > right:
                    raise ValueError(f"{a.title()}: {label}.")
    return values


def with_quality_configuration(row):
    """Validate and attach this build's selected mode and specifications."""
    modes = target_mode_fields(row)
    result = {**deepcopy(row), **quality_fields(row), **modes}
    planning = row.get("planning_grade_targets") or {}
    limits = {}
    for a in SCHEMA_ANALYTES:
        target = result[f"target_{a}_target"]
        limits[a] = {
            "minimum": row.get(f"target_{a}_min"), "maximum": row.get(f"target_{a}_max"),
            **{part: result[f"target_{a}_{part}"] for part in QUALITY_PARTS},
            "limit_mode": modes[f"target_{a}_limit_mode"],
            "target_source": "" if target is None else "2wp" if planning.get(a) == target else "manual",
        }
    result["quality_limits"] = {
        **product_quality_limits(row.get("opf"), row.get("brand"), row.get("byproduct") or "product", limits=limits,
                                 target_mode=modes["target_mode"], evaluation_basis=modes["target_evaluation_basis"]),
        "enforcement": "hard_min_max" if modes["target_mode"] == "hard" else "soft_target",
    }
    return result
