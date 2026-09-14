"""Apply Task 6 component factors to stockpile/hex baselines before chunking.

Physical ROM WMT weights factors within a source (Q39). The existing mapped
grade and mass definitions still govern aggregation of those sources into chunks.
"""

from copy import deepcopy
from collections import OrderedDict
import hashlib
import json
import math
from datetime import datetime
from classes.ApprovedReconciliation import (
    ReconciliationRequired, policy_signature, source_identity, source_key,
)

from classes.GradeStreams import (
    ANALYTES, configured_brands, internal_product_slot, is_dry_plant,
    normalise_grade_streams, normalise_opf,
)
from classes.ReconciliationControls import (
    normalise_reconciliation_settings, resolution_levels, confidence_search_labels, history_approaches,
    RECONCILIATION_ALGORITHM_VERSION,
)
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

    def __init__(self, *, samples, standard_factors, opf, brands, scenario_start, settings=None,
                 allow_search=False, registry=None, mine=None, policy_revision=None):
        self.settings = normalise_reconciliation_settings(settings)
        self.opf = normalise_opf(opf)
        self.brands = configured_brands(brands)
        self.resolvers = {}
        self._resolved_sources = OrderedDict()
        self._retained_sources = {}
        self.allow_search, self.registry, self.mine = allow_search, registry, mine
        self.policy = policy_signature(self.settings, policy_revision)
        self.scenario_start = scenario_start
        # Factor selection depends on evidence and physical lineage, not on
        # grade mappings, adjusted baselines, or subsequent chunk membership.
        self.factor_context_signature = reconciliation_fingerprint(dict(
            application_version=APPLICATION_VERSION, algorithm_version=RECONCILIATION_ALGORITHM_VERSION,
            samples=samples, standard_factors=standard_factors, opf=self.opf,
            brands=self.brands, scenario_start=scenario_start, settings=self.settings))
        if allow_search and (self.settings["method"] != "standard" or registry is not None):
            for brand in self.brands:
                self.resolvers[brand] = ReconciliationFactorResolver(
                    samples, opf=self.opf, brand=brand, scenario_start=scenario_start,
                    standard_factors=(standard_factors or {}).get(brand), **self.settings,
                )

    def retain_audits(self, audits):
        """Keep completed source searches available beyond the small working LRU.

        These are read-only references to the existing review/profile evidence;
        callers always receive copies before applying current grades/coverage.
        Aggregate chunk audits have no factor signature; their member hexes do.
        """
        if self.registry is not None:
            return  # Approved records already survive working-cache eviction.
        retained = {}
        def collect(audit):
            if not isinstance(audit, dict):
                return
            for detail in (audit.get('by_brand') or {}).values():
                if not isinstance(detail, dict):
                    continue
                signature = detail.get('factor_signature')
                if signature:
                    retained[signature] = detail
            for child in audit.get('review_children') or []:
                collect(child)
        for audit in audits:
            collect(audit)
        self._retained_sources = retained

    def resolved_source(self, brand, source_id, source_kind, blocks, total, hex_id, prior=None, source_instance=None):
        # One application owns one immutable history/settings context. Reuse
        # the review's factor search only for exactly the same physical source.
        identity = source_identity(self.mine, self.opf, source_kind, source_id, source_instance, hex_id)
        approved_key = source_key(identity, brand)
        if self.registry is not None:
            approved = (self.registry.get('sources') or {}).get(approved_key) or {}
            if approved.get('policy') == self.policy:
                result = deepcopy(approved['detail'])
                result['source_wmt'] = total
                return result
            if not self.allow_search:
                raise ReconciliationRequired(f'{source_id}' + (f' / hex {hex_id}' if hex_id is not None else '')
                    + ': open Grade Reconciliation and select Update missing sources.')
        key = reconciliation_fingerprint([
            self.factor_context_signature, brand, source_id, source_kind, blocks, total, hex_id])
        if self.registry is None and isinstance(prior, dict) and prior.get('factor_signature') == key:
            return deepcopy(prior)
        if self.registry is None and key in self._retained_sources:
            return deepcopy(self._retained_sources[key])
        if self.registry is None and key in self._resolved_sources:
            self._resolved_sources.move_to_end(key)
            return deepcopy(self._resolved_sources[key])
        if not self.allow_search:
            raise ReconciliationRequired('Factor searches require a manual Grade Reconciliation update.')
        result = self.resolvers[brand].resolve_source(source_id, source_kind, blocks, total, hex_id=hex_id)
        result['factor_signature'] = key
        if self.registry is not None and total > 0:
            result.update(approval_policy=self.policy, source_identity=identity,
                          calculated_at=datetime.now().isoformat(), evidence_as_of=str(self.scenario_start))
            self.registry.setdefault('sources', {})[approved_key] = dict(policy=self.policy, detail=deepcopy(result))
        if self.registry is None:
            self._resolved_sources[key] = deepcopy(result)
            if len(self._resolved_sources) > 4096:
                self._resolved_sources.popitem(last=False)
        return result

    def apply(self, streams, *, source_id, source_kind, source_wmt, contributing_blocks,
              hex_id=None, warnings=(), grade_coverage=None, prior_audit=None, source_instance=None):
        if source_kind not in {"inventory", "amt", "amt_chunk"}:
            raise ValueError("Advanced reconciliation applies only to inventory stockpiles and AMT hexes.")
        if self.settings["method"] == "standard" and self.registry is None:
            return deepcopy(streams), {}
        total = finite_number(source_wmt)
        if total is None or total < 0:
            raise ValueError("Advanced reconciliation requires non-negative physical source WMT.")
        result = normalise_grade_streams(streams)
        audit = {"schema_version": APPLICATION_VERSION, "method": self.settings["method"],
                 "opf": self.opf, "source_id": clean_text(source_id), "source_kind": source_kind,
                 "hex_id": clean_text(hex_id), "source_wmt": total, "by_brand": {},
                 "warnings": list(warnings), "confidence_method": CONFIDENCE_METHOD}
        for brand in self.brands:
            try:
                prior = (prior_audit.get('by_brand') or {}).get(brand) if isinstance(prior_audit, dict) else None
                resolved = self.resolved_source(brand, source_id, source_kind, contributing_blocks, total, hex_id, prior, source_instance)
            except (ValueError, TypeError, AttributeError) as exc:
                # Malformed source lineage is unavailable evidence, never a reason
                # to discard source mass or invent factors. Configuration/history
                # validation happens in the constructor and is not caught here.
                resolved = self.resolved_source(brand, source_id, source_kind, [], total, hex_id, source_instance=source_instance)
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
        adjusted = [detail.get('calculated_at') for detail in audit['by_brand'].values() if detail.get('calculated_at')]
        audit['last_adjusted'] = min(adjusted) if adjusted else None
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
    adjusted = [a.get('last_adjusted') for a, wmt in sources if (finite_number(wmt) or 0) > 0]
    result['last_adjusted'] = min(adjusted) if adjusted and all(adjusted) else None
    if any(a.get('status') == 'pending' for a in present):
        result['status'] = 'pending'
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
        if any((audit.get("by_brand", {}).get(brand) or {}).get("auto_selection") for audit, _ in sources):
            baseline_members = []
            for audit, wmt in sources:
                detail = audit.get("by_brand", {}).get(brand, {})
                base = (detail["auto_selection"].get("baseline_confidence_percent") if detail.get("auto_selection")
                        else detail.get("confidence_percent"))
                baseline_members.append(max(finite_number(wmt) or 0, 0) * (base or 0))
            baseline = math.fsum(baseline_members) / total if total else None
            confidence = result["by_brand"][brand]["confidence_percent"]
            result["by_brand"][brand]["auto_selection"] = {
                "status": "aggregate", "baseline_confidence_percent": baseline,
                "confidence_percent": confidence,
                "improvement_percent": max(confidence - baseline, 0) if total else None,
                "selected_windows": sorted({label for audit, _ in sources for label in
                                            confidence_search_labels(audit.get("by_brand", {}).get(brand, {}))}),
                "selected_history_approaches": sorted({approach for audit, wmt in sources
                    if (finite_number(wmt) or 0) > 0
                    for approach in history_approaches(audit.get("by_brand", {}).get(brand, {}))}),
            }
    return result
