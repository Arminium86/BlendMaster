"""Portable quality audit rows shared by optimised and manual plan reports."""

import json

from classes.PhaseSchemas import SCHEMA_ANALYTES
from classes.ProductTargetModes import target_mode_fields
from classes.SoftProductGrades import analyte_audit, objective_config, similarity_summary


QUALITY_REPORT_SUFFIXES = ("target_mode", "evaluation_basis", "quality_status", "hard_limits_satisfied",
                           "soft_grade_penalty", "source_similarity_penalty", "quality_audit")


def quality_state_audit(build, opening_metal, opening_weight, added_metal, added_weight,
                        source_totals, cumulative_sources, preferences=None):
    config = objective_config(preferences)
    modes = target_mode_fields(build)
    rows = []
    for grain in ("steady_state", "cumulative_build"):
        for a in SCHEMA_ANALYTES:
            cumulative = grain == "cumulative_build"
            weight = added_weight[a] + (opening_weight[a] if cumulative else 0)
            metal = added_metal[a] + (opening_metal[a] if cumulative else 0)
            audit = analyte_audit(build, a, metal, weight, config)
            opening = analyte_audit(build, a, opening_metal[a], opening_weight[a], config)
            selected = grain == modes["target_evaluation_basis"]
            source = similarity_summary((cumulative_sources if cumulative else source_totals)[a])
            state_source = similarity_summary(source_totals[a])
            source["applied_similarity_penalty"] = state_source["applied_similarity_penalty"] if selected else None
            if audit["actual_grade"] is None:
                status = "No production"
            elif not audit["hard_limits_satisfied"]:
                status = "Hard limit breached"
            elif not audit["within_limits"]:
                status = "Soft limit breached"
            else:
                status = "Within limits"
            rows.append(dict(schema_version=1, build_id=build.get("build_id"), build_name=build.get("build_name", ""),
                             opf=build.get("opf", ""), brand=build.get("brand", ""),
                             target_mode=modes["target_mode"], evaluation_basis=modes["target_evaluation_basis"],
                             grain=grain, analyte=a, **audit, **source, quality_status=status,
                             opening_penalty=opening["total_penalty"] if cumulative else 0.0,
                             applied_penalty=(audit["total_penalty"] - (opening["total_penalty"] if cumulative else 0)) if selected else None))
    return {"schema_version": 1, "preferences": config, "rows": rows}


def serialize_audit(audit):
    return json.dumps(audit, allow_nan=False, separators=(",", ":"))


def quality_report_rows(report, *, grain="steady_state", analyte=None):
    """Unpack one row per build/lane/state/analyte, never per repeated source."""
    if report is None or report.empty:
        return []
    columns = [c for c in report.columns if c.endswith("quality_audit")]
    records, seen = [], set()
    for _, source in report.iterrows():
        for column in columns:
            raw = source.get(column)
            if not isinstance(raw, str) or not raw:
                continue
            try:
                audit = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(audit, dict) or audit.get("schema_version") != 1:
                continue
            lane = ("lump" if "_lump_" in column else "fines" if "_fines_" in column
                    else str(source.get("product_build_lane") or "product"))
            for row in audit.get("rows", []):
                if row.get("grain") != grain or (analyte and row.get("analyte") != analyte):
                    continue
                start = str(source.get("start_datetime", source.get("steady_state_start_datetime", "")))
                state = source.get("steady_state_number", "")
                key = (str(source.get("scenario_id", "")), str(source.get("plan_id", "")), start, str(state),
                       str(source.get("blend_ID", "")), str(source.get("blend_option", "")), lane, row.get("opf"),
                       str(row.get("build_id")), row.get("build_name"), row.get("analyte"))
                if key in seen:
                    continue
                seen.add(key)
                records.append({**row, "lane": lane, "steady_state_number": state, "start_datetime": start})
    return records
