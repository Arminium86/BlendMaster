from collections.abc import Mapping


def _coverage_section(label, coverage, *, applicable=True):
    if not applicable:
        return f"{label}: Not applicable"
    coverage = coverage if isinstance(coverage, Mapping) else {}
    if not coverage:
        return f"{label}: Missing"
    missing = []
    partial = []
    for name, raw_value in coverage.items():
        try:
            value = min(max(float(raw_value), 0.0), 100.0)
        except (TypeError, ValueError):
            missing.append(str(name))
            continue
        if value <= 1e-8:
            missing.append(str(name))
        elif value < 99.999999:
            partial.append(f"{name} {value:.2f}%")
    if missing:
        suffix = ", ".join(missing)
        if partial:
            suffix += "; partial - " + ", ".join(partial)
        return f"{label}: Missing - {suffix}"
    if partial:
        return f"{label}: Partial - " + ", ".join(partial)
    return f"{label}: OK"


def format_chunk_quality_warning(
    quality,
    *,
    fallbacks=None,
    product_grades_applicable=True,
):
    """Return the fixed, one-line AMT chunk audit template."""
    quality = quality if isinstance(quality, Mapping) else {}
    lineage = quality.get("lineage_coverage_pct")
    if lineage is None:
        lineage_text = "Lineage: Missing"
    else:
        try:
            lineage_value = min(max(float(lineage), 0.0), 100.0)
            lineage_text = (
                "Lineage: OK"
                if lineage_value >= 99.999999
                else f"Lineage: Partial {lineage_value:.2f}%"
            )
        except (TypeError, ValueError):
            lineage_text = "Lineage: Missing"

    mapped_text = _coverage_section(
        "Mapped fields", quality.get("mapped_field_coverage_pct")
    )
    product_text = _coverage_section(
        "Product grades",
        quality.get("product_grade_coverage_pct"),
        applicable=product_grades_applicable,
    )
    fallback_values = [str(value) for value in (fallbacks or []) if str(value)]
    fallback_text = (
        "Fallbacks: " + "; ".join(dict.fromkeys(fallback_values))
        if fallback_values else "Fallbacks: None"
    )
    geometry_count = int(quality.get("geometry_quarantine_count") or 0)
    geometry_hexes = str(quality.get("geometry_quarantine_hexes") or "").strip()
    geometry_text = "Geometry: OK"
    if geometry_count:
        detail = f"{geometry_count} quarantined hex(es)"
        if geometry_hexes:
            detail += f" - {geometry_hexes}"
        geometry_text = "Geometry: " + detail
    return " | ".join((
        lineage_text,
        mapped_text,
        product_text,
        fallback_text,
        geometry_text,
    ))
