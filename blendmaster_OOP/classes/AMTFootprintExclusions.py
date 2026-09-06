"""Scenario-owned AMT participation decisions, separate from raw tonne audits."""

from copy import deepcopy

from classes.PhaseSchemas import amt_footprint_audit, AMT_OUTCOME_EXCLUDED


def footprint_key(value):
    return str(value or "").strip().upper()


def normalize_amt_exclusions(value):
    result = {}
    for name, record in (value if isinstance(value, dict) else {}).items():
        key = footprint_key(name)
        if not key or not isinstance(record, dict):
            continue
        result[key] = deepcopy(record)
        result[key]["footprint_id"] = key
        result[key]["excluded"] = record.get("excluded") is True
    return result


def excluded_footprints(value):
    return {name for name, record in normalize_amt_exclusions(value).items() if record["excluded"]}


def included_footprints(mapping, exclusions):
    excluded = excluded_footprints(exclusions)
    return {name: value for name, value in (mapping or {}).items() if footprint_key(name) not in excluded}


def exclusion_audit(name, *, reason, timestamp, raw_wmt=None, inventory_wmt=None,
                    spatial_wmt=None, final_wmt=None, source_rows=None):
    return amt_footprint_audit(
        footprint_key(name), excluded=True, exclusion_reason=reason,
        excluded_by="User", excluded_at=timestamp, outcome=AMT_OUTCOME_EXCLUDED,
        outcome_reason="Excluded in AMT setup; omitted from processing and scheduling.",
        raw_wmt=raw_wmt, inventory_wmt=inventory_wmt,
        spatially_reconciled_wmt=spatial_wmt, final_wmt=final_wmt,
        source_rows=source_rows, eligible_for_processing=False,
    )
