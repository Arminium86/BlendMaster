"""Apply Task 6 component factors to stockpile/hex baselines before chunking.

Physical ROM WMT weights factors within a source (Q39). The existing mapped
grade and mass definitions still govern aggregation of those sources into chunks.
"""

from copy import deepcopy
import hashlib
import json
import math

from classes.GradeStreams import (
    ANALYTES, configured_brands, internal_product_slot, is_dry_plant,
    normalise_grade_streams, normalise_opf,
)
from classes.ReconciliationControls import normalise_reconciliation_settings, resolution_levels
from classes.ReconciliationFactorResolver import (
    CONFIDENCE_METHOD, ReconciliationFactorResolver,
    aggregate_source_confidence,
)
from setup.InventoryBuildLineage import canonical_block, clean_text, finite_number


APPLICATION_VERSION = 1


def reconciliation_fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def amt_reconciliation_lineage(row):
    """Read final aligned tonnes, never inbound tonnes or inventory balances."""
    raw = row.get("GRADE_BLOCK_LINEAGE_JSON", row.get("grade_block_lineage", []))
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return [], ["Invalid AMT lineage JSON; using global factors."]
    if not isinstance(raw, list):
        return [], ["AMT lineage is unavailable; using global factors."]
    blocks = []
    for item in raw:
        if not isinstance(item, dict):
            return [], ["Invalid AMT lineage record; using global factors."]
        amount = finite_number(item.get("remaining_wmt"))
        if amount is None or amount < 0:
            return [], ["AMT lineage has no valid aligned remaining WMT; using global factors."]
        # A resolved EXPIT ID alone is not a spatial grade-block identity.
        blocks.append({"grade_block_key": canonical_block(item.get("grade_block_name")),
                       "feed_wmt": amount})
    return blocks, []


class ReconciliationApplication:
    """One immutable-in-use OPF/configuration context shared by many sources."""

    def __init__(self, *, samples, standard_factors, opf, brands, scenario_start, settings=None):
        self.settings = normalise_reconciliation_settings(settings)
        self.opf = normalise_opf(opf)
        self.brands = configured_brands(brands)
        self.resolvers = {}
        if self.settings["method"] != "standard":
            for brand in self.brands:
                self.resolvers[brand] = ReconciliationFactorResolver(
                    samples, opf=self.opf, brand=brand, scenario_start=scenario_start,
                    standard_factors=(standard_factors or {}).get(brand), **self.settings,
                )

    def apply(self, streams, *, source_id, source_kind, source_wmt, contributing_blocks,
              hex_id=None, warnings=(), grade_coverage=None):
        if source_kind not in {"inventory", "amt"}:
            raise ValueError("Advanced reconciliation applies only to inventory stockpiles and AMT hexes.")
        if self.settings["method"] == "standard":
            return deepcopy(streams), {}
        total = finite_number(source_wmt)
        if total is None or total < 0:
            raise ValueError("Advanced reconciliation requires non-negative physical source WMT.")
        result = normalise_grade_streams(streams)
        audit = {"schema_version": APPLICATION_VERSION, "method": self.settings["method"],
                 "opf": self.opf, "source_id": clean_text(source_id), "source_kind": source_kind,
                 "hex_id": clean_text(hex_id), "source_wmt": total, "by_brand": {},
                 "warnings": list(warnings), "confidence_method": CONFIDENCE_METHOD}
        for brand, resolver in self.resolvers.items():
            try:
                resolved = resolver.resolve_source(source_id, source_kind, contributing_blocks, total, hex_id=hex_id)
            except (ValueError, TypeError, AttributeError) as exc:
                # Malformed source lineage is unavailable evidence, never a reason
                # to discard source mass or invent factors. Configuration/history
                # validation happens in the constructor and is not caught here.
                resolved = resolver.resolve_source(source_id, source_kind, [], total, hex_id=hex_id)
                audit["warnings"].append(f"Invalid source lineage; using global factors. {exc}")
            records = resolved["records"]
            applied = {kind: {a: math.fsum(r["lineage_fraction"] * r[f"{kind}_factors"][a]
                                         for r in records) if total > 0 else None
                              for a in ANALYTES} for kind in ("blend", "regression")}
            global_fraction = math.fsum(r["lineage_fraction"] for r in records if r["resolution_level"] == "global")
            resolved["applied_factors"] = applied
            resolved["global_fraction"] = min(global_fraction, 1.0)
            resolved["manual_override_fraction"] = min(math.fsum(
                r["lineage_fraction"] for r in records if r.get("manual_override")), 1.0)
            resolved["spatial_fraction"] = max(1.0 - global_fraction, 0.0) if total > 0 else 0.0
            if total > 0 and resolved["lineage_coverage"] < 1 - 1e-9:
                audit["warnings"].append(f"{brand}: {1 - resolved['lineage_coverage']:.2%} of source WMT has no usable grade-block lineage; that fraction retains global factors.")
            if global_fraction > 0:
                share = "<0.01%" if global_fraction < .0001 else f"{global_fraction:.2%}"
                audit["warnings"].append(f"{brand}: {share} of source WMT has global fallback evidence; local edits, if any, are shown separately.")
            # A zero-mass source has no adjustment contribution. Do not change its
            # stored grade values or infer a positive physical balance.
            if total > 0:
                rom = result.get("modelled_rom", {}).get(brand) or result.get("modelled_rom", {}).get("*", {})
                product = result.get("modelled_product", {}).get(brand, {})
                for target, baseline, kind in (("adjusted_rom", rom, "blend"),
                                               ("adjusted_product", product, "regression")):
                    result.setdefault(target, {})[brand] = {
                        a: finite_number(baseline.get(a)) * applied[kind][a]
                        if finite_number(baseline.get(a)) is not None else None for a in ANALYTES
                    }
                if is_dry_plant(self.opf) or (source_kind == "inventory" and internal_product_slot(self.opf) is None):
                    for target in ("modelled_product", "adjusted_product"):
                        result.setdefault(target, {})[brand] = deepcopy(result["adjusted_rom"][brand])
            coverage = {}
            for target, baseline in (("adjusted_rom", "modelled_rom"), ("adjusted_product", "modelled_product")):
                if target == "adjusted_product" and (is_dry_plant(self.opf) or
                        (source_kind == "inventory" and internal_product_slot(self.opf) is None)):
                    baseline = "modelled_rom"
                coverage[target] = {
                    a: min(max(finite_number((grade_coverage or {}).get(baseline, {}).get(a, 1.0)) or 0.0, 0.0), 1.0)
                    if total > 0 and result.get(target, {}).get(brand, {}).get(a) is not None else 0.0
                    for a in ANALYTES
                }
            # Factor fallback provides a factor, not a missing assay. Preserve
            # the mapped grade's coverage separately from spatial lineage coverage.
            resolved["grade_coverage"] = coverage
            audit["by_brand"][brand] = resolved
        audit["warnings"] = list(dict.fromkeys(audit["warnings"]))
        return result, audit


def aggregate_reconciliation(sources, *, source_id="", source_kind="amt_chunk"):
    """Aggregate source confidence/coverage using physical WMT, retaining gaps.

    ``sources`` contains (audit, physical_wmt) pairs. Keep component evidence on
    member hexes and compact references here; chunk grades are aggregated by the
    existing per-field grade weights, not by an average reconciliation factor.
    """
    # Mixed legacy/current DataFrames can represent a missing JSON cell as NaN.
    sources = [(audit if isinstance(audit, dict) else {}, wmt) for audit, wmt in sources]
    present = [audit for audit, _ in sources if isinstance(audit, dict) and audit.get("by_brand")]
    if not present:
        return {}
    opfs = {a["opf"] for a in present}
    if len(opfs) != 1:
        raise ValueError("Aggregate reconciliation within one OPF before combining OPF outputs.")
    total = math.fsum(max(finite_number(wmt) or 0.0, 0.0) for _, wmt in sources)
    result = {"schema_version": APPLICATION_VERSION, "method": "advanced_aggregate",
              "opf": next(iter(opfs)), "source_id": source_id, "source_kind": source_kind,
              "source_wmt": total, "by_brand": {}, "confidence_method": CONFIDENCE_METHOD,
              "warnings": list(dict.fromkeys(w for a in present for w in a.get("warnings", [])))}
    signatures = {a.get("enrichment_signature", "") for a in present}
    result["enrichment_signature"] = next(iter(signatures)) if len(signatures) == 1 else ""
    for brand in sorted({b for a in present for b in a["by_brand"]}):
        members, global_wmt, lineage_wmt, manual_wmt = [], 0.0, 0.0, 0.0
        for audit, raw_wmt in sources:
            wmt = max(finite_number(raw_wmt) or 0.0, 0.0)
            audit = audit if isinstance(audit, dict) else {}
            detail = audit.get("by_brand", {}).get(brand, {})
            members.append({"source_id": audit.get("source_id", ""), "hex_id": audit.get("hex_id", ""),
                            "source_wmt": wmt, "confidence_percent": detail.get("confidence_percent"),
                            "resolution_levels": resolution_levels(detail)})
            global_wmt += wmt * detail.get("global_fraction", 1.0)
            lineage_wmt += wmt * detail.get("lineage_coverage", 0.0)
            manual_wmt += wmt * detail.get("manual_override_fraction", 0.0)
        result["by_brand"][brand] = {
            **aggregate_source_confidence(members), "members": members,
            "global_fraction": min(global_wmt / total, 1.0) if total else 0.0,
            "lineage_coverage": min(lineage_wmt / total, 1.0) if total else 0.0,
            "manual_override_fraction": min(manual_wmt / total, 1.0) if total else 0.0,
            "grade_coverage": {
                stream: {a: math.fsum(
                    max(finite_number(wmt) or 0.0, 0.0) *
                    (audit or {}).get("by_brand", {}).get(brand, {}).get("grade_coverage", {}).get(stream, {}).get(a, 0.0)
                    for audit, wmt in sources) / total if total else 0.0 for a in ANALYTES}
                for stream in ("adjusted_rom", "adjusted_product")
            },
        }
    return result
