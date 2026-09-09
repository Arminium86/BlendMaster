"""Read-only Product Targets overlays for the OPF Production Report."""

from classes.GradeStreams import normalise_opf
from classes.ProductQualityLimits import quality_fields
from setup.InventoryBuildLineage import clean_text, finite_number
from setup.ProductAssayHistory import awst, optional_time


def target_overlays(targets, opfs, brand, start, end):
    """Use dated build intervals, or explicitly labelled undated references.

    Never infer a historical build duration from target tonnes, and never turn
    legacy Min/Max into central Target/LQL/HQL values.
    """
    start, end = awst(start), awst(end)
    brand = clean_text(brand).upper()
    result, warnings = [], []
    for opf in opfs:
        rows = [row for row in targets if normalise_opf(row.get("opf")) == opf]
        available = {clean_text(row.get("brand")).upper() for row in rows}
        matches = {brand} if brand in available else {b for b in available if b and (brand.endswith(b) or b.endswith(brand))}
        # CBFL and CBSF name physical product lanes; both can have SF build rows.
        cb_lump = opf == "CB_OPF" and brand == "CBFL"
        if cb_lump:
            matches |= {clean_text(r.get("brand")).upper() for r in rows if r.get("byproduct") == "lump"}
        if len(matches) > 1 and not cb_lump:
            warnings.append(f"{opf}: several Product Targets brands match {brand}; target overlays are omitted.")
            continue
        rows = [r for r in rows if clean_text(r.get("brand")).upper() in matches]
        if opf == "CB_OPF" and brand in {"CBFL", "CBSF", "SF"}:
            lane = "lump" if cb_lump else "fines"
            rows = [r for r in rows if not r.get("byproduct") or r["byproduct"] == lane]
        candidates, undated = [], []
        for row in rows:
            values = {k: finite_number(v) for k, v in quality_fields(row, validate=False).items()}
            if not any(v is not None for v in values.values()):
                continue
            first, last = optional_time(row.get("planning_period_start")), optional_time(row.get("planning_period_end"))
            name = clean_text(row.get("build_name")) or f"Build {row.get('build_id', '')}"
            record = dict(opf=opf, brand=brand, name=name, values=values, reference_only=False)
            if first and last and awst(first) < awst(last):
                a, b = max(awst(first), start), min(awst(last), end)
                if a < b:
                    candidates.append({**record, "start": a.isoformat(), "end": b.isoformat()})
            elif first or last:
                warnings.append(f"{opf} / {name}: incomplete build timing; no dated overlay is inferred.")
            else:
                undated.append(record)
        if not candidates and undated:
            # An undated build is a current reference, never historical production evidence.
            candidates = [{**undated[0], "start": start.isoformat(), "end": end.isoformat(), "reference_only": True}]
            warnings.append(f"{opf}: first listed build {undated[0]['name']} is an undated current reference; historical build changes are unavailable.")
        # Multiple crushers/scenarios can carry the same OPF row. Collapse exact
        # duplicates, but suppress genuinely conflicting intervals explicitly.
        unique = {}
        for row in candidates:
            key = (row["start"], row["end"], tuple(row["values"].items()))
            unique.setdefault(key, row)
        candidates = sorted(unique.values(), key=lambda r: (r["start"], r["end"]))
        conflicts = {i for i, a in enumerate(candidates) for j, b in enumerate(candidates) if i != j
                     and a["start"] < b["end"] and b["start"] < a["end"]}
        if conflicts:
            warnings.append(f"{opf}: overlapping Product Targets have ambiguous build ownership; conflicting overlays are omitted.")
        result.extend(r for i, r in enumerate(candidates) if i not in conflicts)
        if rows and not candidates:
            warnings.append(f"{opf}: no dated Product Target/LQL/HQL values overlap the selected window.")
    return result, list(dict.fromkeys(warnings))
