"""Serializable Data Streams controls at the confirmed OPF/brand/cell/analyte grain."""

from copy import deepcopy
import re

from classes.GradeStreams import is_dry_plant, normalise_opf
from classes.PhaseSchemas import FACTOR_METHODS, SCHEMA_ANALYTES
from setup.InventoryBuildLineage import canonical_block, clean_text, finite_number

WINDOW_MODES = ("calendar_days", "production_days", "latest_campaign")
WINDOW_DEFAULTS = {"window_mode": "calendar_days", "lookback_days": 7,
                   "min_production_days": 1, "max_lookback_days": 30}
RECONCILIATION_ALGORITHM_VERSION = 4
HISTORY_APPROACH_LABELS = {"component_based": "Component-based", "shared_history": "Shared history"}
METHOD_LABELS = {"standard": "Standard · global factors",
                 "lookback": "Advanced · lookback window",
                 "spatial_compositional": "Advanced · spatial and compositional",
                 "auto_max_confidence": "Auto · maximise evidence match score"}


def evidence_display_column(name):
    """Translate legacy presentation fields without rewriting saved audits."""
    if not str(name).startswith("recon_"):
        return name
    for old, new in (("_uncertainty_pct", None),
                     ("_baseline_confidence_pct", "_baseline_evidence_match_score_pct"),
                     ("_confidence_gain_pp", "_evidence_match_score_gain_pp"),
                     ("_confidence_pct", "_evidence_match_score_pct")):
        if name.endswith(old):
            return name[:-len(old)] + new if new else None
    return name


def evidence_display_record(record):
    result = {evidence_display_column(k): v for k, v in record.items() if evidence_display_column(k) is not None}
    # Prefer an explicitly supplied current field if both names were saved.
    result.update({k: v for k, v in record.items() if evidence_display_column(k) == k})
    return result


def spatial_cell(value):
    key = canonical_block(value)
    if not key:
        return ""
    parts = key.split("|")
    parts[-1] = re.match(r"[A-Z]+", parts[-1]).group()
    return "|".join(parts)


def normalise_window(settings=None, *, partial=False):
    settings = dict(settings or {})
    result = {} if partial else dict(WINDOW_DEFAULTS)
    for name in WINDOW_DEFAULTS:
        if name not in settings:
            continue
        raw = settings[name]
        if name == "window_mode":
            if raw not in WINDOW_MODES:
                raise ValueError("Unsupported reconciliation lookback mode.")
            result[name] = raw
        else:
            value = finite_number(raw)
            if isinstance(raw, bool) or value is None or value < 1 or not value.is_integer():
                raise ValueError(f"{name} must be a positive whole number.")
            result[name] = int(value)
    return result


def normalise_reconciliation_settings(settings=None):
    settings = dict(settings or {})
    result = {"method": settings.get("method", "standard"), **normalise_window(settings)}
    if result["method"] not in FACTOR_METHODS:
        raise ValueError("Unsupported reconciliation method.")
    cells, seen = [], set()
    for raw in settings.get("cells", []) or []:
        opf, brand = normalise_opf(raw.get("opf")), clean_text(raw.get("brand")).upper()
        cell, analyte = spatial_cell(raw.get("cell")), clean_text(raw.get("analyte")).lower()
        if not opf or not brand or brand == "*" or not cell or analyte not in SCHEMA_ANALYTES:
            raise ValueError("Local settings require OPF, brand, full spatial cell, and one of the five analytes.")
        key = (opf, brand, cell, analyte)
        if key in seen:
            raise ValueError("Duplicate local reconciliation cell/analyte settings.")
        seen.add(key)
        record = dict(zip(("opf", "brand", "cell", "analyte"), key))
        window = normalise_window(raw.get("window"), partial=True)
        if window:
            record["window"] = window
        for kind in ("blend", "regression"):
            if raw.get(kind) is None or raw.get(kind) == "":
                continue
            value = finite_number(raw[kind])
            if isinstance(raw[kind], bool) or value is None or value <= 0:
                raise ValueError(f"Local {kind} factor must be finite and greater than zero; leave blank to inherit.")
            if kind == "regression" and is_dry_plant(opf):
                raise ValueError("Dry-plant regression is fixed; local regression overrides are unavailable.")
            record[kind] = value
        if len(record) > 4:
            cells.append(record)
    if cells:
        result["cells"] = sorted(cells, key=lambda r: (r["opf"], r["brand"], r["cell"], r["analyte"]))
    return deepcopy(result)


def required_history_days(settings, opf, brands):
    settings = normalise_reconciliation_settings(settings)
    return max([settings["max_lookback_days"], *[
        r.get("window", {}).get("max_lookback_days", settings["max_lookback_days"])
        for r in settings.get("cells", []) if r["opf"] == normalise_opf(opf) and r["brand"] in brands]])


def resolution_levels(detail):
    return sorted({r["resolution_level"] for r in detail.get("records", [])} |
                  {level for r in detail.get("members", []) for level in r.get("resolution_levels", [])})


def history_approaches(detail):
    search = detail.get("auto_selection") or {}
    if not search or search.get("status") in {"no_lineage", "zero_mass", "global_fallback"}:
        return []
    return search.get("selected_history_approaches", [search.get("history_approach", "component_based")])


def history_approach_labels(detail):
    return [HISTORY_APPROACH_LABELS.get(value, value) for value in history_approaches(detail)]


def confidence_search_labels(detail):
    search = detail.get("auto_selection") or {}
    if not search:
        return []
    if "selected_windows" in search:
        return search["selected_windows"]
    if search.get("status") in {"no_lineage", "zero_mass", "global_fallback"}:
        return [{"no_lineage": "Global fallback (no lineage)", "zero_mass": "Unscored (zero WMT)",
                 "global_fallback": "Global fallback (no eligible history)"}[search["status"]]]
    family = search.get("window_mode") or "spatial_compositional"
    label = {"spatial_compositional": "Spatial", "calendar_days": "Calendar",
             "production_days": "Production days", "latest_campaign": "Latest campaign"}.get(family, family)
    n = search.get("window_days")
    return [f"{label} · {n} {'day' if n == 1 else 'days'}" + (" max" if family == "spatial_compositional" else "")]


def reconciliation_columns(audit):
    """Scalar evidence columns for Database View and reconciliation reports."""
    result = {}
    for brand, detail in (audit or {}).get("by_brand", {}).items():
        prefix = "recon_" + re.sub(r"[^a-z0-9]+", "_", brand.lower()).strip("_")
        result.update({f"{prefix}_evidence_match_score_pct": detail.get("confidence_percent"),
                       f"{prefix}_fallback_levels": ", ".join(resolution_levels(detail)),
                       f"{prefix}_global_pct": 100 * detail.get("global_fraction", 0),
                       f"{prefix}_lineage_coverage_pct": 100 * detail.get("lineage_coverage", 0),
                       f"{prefix}_manual_override_pct": 100 * detail.get("manual_override_fraction", 0)})
        if detail.get("auto_selection"):
            result.update({f"{prefix}_selected_window": "; ".join(confidence_search_labels(detail)),
                           f"{prefix}_history_selection": "; ".join(history_approach_labels(detail)),
                           f"{prefix}_baseline_evidence_match_score_pct": detail["auto_selection"].get("baseline_confidence_percent"),
                           f"{prefix}_evidence_match_score_gain_pp": detail["auto_selection"].get("improvement_percent")})
    return result


def reconciliation_report_rows(audits, overall):
    """One report row per source/AMT chunk plus the WMT-weighted overall row."""
    return [{"opf": audit.get("opf", ""), "source_kind": audit.get("source_kind", ""),
             "source_id": audit.get("source_id", ""), "source": audit.get("review_label") or audit.get("source_id") or "Overall",
             "source_wmt": audit.get("source_wmt", 0), "method": METHOD_LABELS.get(audit.get("method"), audit.get("method", "")),
             **reconciliation_columns(audit), "warnings": "; ".join(audit.get("warnings", []))}
            for audit in [overall, *audits] if audit.get("by_brand")]
