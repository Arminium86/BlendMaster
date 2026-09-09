"""Versioned, row-owned target mode configuration and runtime capability checks."""

from copy import deepcopy

from classes.PhaseSchemas import (
    SCHEMA_ANALYTES, TARGET_MODES, QUALITY_LIMIT_MODES, QUALITY_EVALUATION_BASES,
    QUALITY_LIMIT_SCHEMA_VERSION, is_readable,
)


PRODUCT_TARGET_SCHEMA_VERSION = 2
TARGET_MODE_FIELDS = (
    "product_target_schema_version", "target_mode", "target_evaluation_basis",
    *[f"target_{a}_limit_mode" for a in SCHEMA_ANALYTES],
)
TARGET_MODE_LABELS = {"hard": "Hard — Min / Max", "soft": "Soft — Target / LQL / HQL"}
LIMIT_MODE_LABELS = {"hard": "Hard limits", "soft": "Soft limits (higher penalty)"}
EVALUATION_LABELS = {
    "steady_state": "Steady-state product output",
    "cumulative_build": "Cumulative active build average",
}
SOFT_MODE_NOTICE = (
    "Soft targets use the selected evaluation basis. Configure penalty weights "
    "in Setup > Solver Configuration > Soft Product Grades. Calendar constraints remain independent hard bounds."
)


def target_mode_fields(row):
    """Upgrade absent/v1 modes to v2 hard defaults without changing grades.

    Explicit flat values take precedence over the derived quality record. An
    unknown mode/version is rejected instead of silently becoming Hard.
    """
    version = row.get("product_target_schema_version", 1)
    if isinstance(version, bool) or str(version) not in {"1", "2"}:
        raise ValueError(f"Unsupported Product Targets schema version: {version!r}.")
    nested = row.get("quality_limits") or {}
    if not isinstance(nested, dict) or (nested and not is_readable(nested, QUALITY_LIMIT_SCHEMA_VERSION)):
        raise ValueError("Unsupported product quality configuration/schema version.")
    limits = nested.get("limits") or {}
    if not isinstance(limits, dict):
        raise ValueError("Product quality limits must be an analyte mapping.")

    def choice(value, allowed, label):
        value = str(value).strip().lower()
        if value not in allowed:
            raise ValueError(f"{label} must be one of: {', '.join(allowed)}.")
        return value

    result = {
        "product_target_schema_version": PRODUCT_TARGET_SCHEMA_VERSION,
        "target_mode": choice(row.get("target_mode", nested.get("target_mode", "hard")), TARGET_MODES, "Target mode"),
        "target_evaluation_basis": choice(row.get("target_evaluation_basis", nested.get("evaluation_basis", "steady_state")),
                                          QUALITY_EVALUATION_BASES, "Target evaluation basis"),
    }
    for a in SCHEMA_ANALYTES:
        entry = limits.get(a) or {}
        if not isinstance(entry, dict):
            raise ValueError(f"{a.title()} quality limits must be a mapping.")
        key = f"target_{a}_limit_mode"
        result[key] = choice(row.get(key, entry.get("limit_mode", "hard")), QUALITY_LIMIT_MODES, f"{a.title()} LQL/HQL mode")
    return result


def migrate_target_row(row):
    return {**deepcopy(row), **target_mode_fields(row)}


def require_supported_target_modes(rows):
    """Validate both supported modes at execution boundaries."""
    for row in rows or []:
        if isinstance(row, dict):
            target_mode_fields(row)
