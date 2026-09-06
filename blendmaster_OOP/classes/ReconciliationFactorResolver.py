"""Deterministic, read-only factor resolution from Task 5 history samples.

The confirmed Q32/Q40 contract replaces the original plan's shorter hierarchy
and independent analyte fallback. One spatial level must support both factor
kinds and all five analytes. Grades and application state are not changed here.
"""

from collections import defaultdict, OrderedDict
from collections.abc import Mapping
from copy import deepcopy
from datetime import timedelta
import math
import re

import pandas as pd

from classes.GradeStreams import normalise_opf
from classes.ReconciliationControls import normalise_reconciliation_settings, spatial_cell
from classes.PhaseSchemas import (
    FACTOR_LEVELS, FACTOR_LEVEL_GLOBAL, FACTOR_METHODS,
    FACTOR_METHOD_LOOKBACK, FACTOR_METHOD_SPATIAL_COMPOSITIONAL,
    FACTOR_METHOD_STANDARD, FACTOR_METHOD_MAX_CONFIDENCE, RECONCILIATION_SAMPLE_SCHEMA_VERSION,
    SAMPLE_GRAINS, SCHEMA_ANALYTES, is_readable, resolved_factor,
)
from setup.InventoryBuildLineage import canonical_block, clean_text, finite_number


WINDOW_CALENDAR_DAYS = "calendar_days"
WINDOW_PRODUCTION_DAYS = "production_days"
WINDOW_LATEST_CAMPAIGN = "latest_campaign"
WINDOW_MODES = (WINDOW_CALENDAR_DAYS, WINDOW_PRODUCTION_DAYS, WINDOW_LATEST_CAMPAIGN)
CONFIDENCE_METHOD = "hierarchical_composition_overlap_v1"
KINDS = ("blend", "regression")


def _time(value):
    if hasattr(value, "toPyDateTime"):
        value = value.toPyDateTime()
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("A valid reconciliation timestamp is required.")
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("Australia/Perth").tz_localize(None)
    return stamp.to_pydatetime()


def _positive_integer(value, label):
    number = finite_number(value)
    if isinstance(value, bool) or number is None or number < 1 or not number.is_integer():
        raise ValueError(f"{label} must be a positive whole number.")
    return int(number)


def _cells(key):
    parts = key.split("|")
    material = re.match(r"[A-Z]+", parts[-1]).group()
    return [tuple(parts[:depth]) + (material,) for depth in (5, 4, 3, 2, 1)]


def _distributions(weights, denominator):
    """Exact parent plus five nested spatial/material partitions; no unknown bin."""
    bins = [defaultdict(float) for _ in range(6)]
    if denominator <= 0:
        return bins
    for key, tonnes in weights.items():
        share = tonnes / denominator
        bins[0][key] += share
        for index, cell in enumerate(_cells(key), 1):
            bins[index][cell] += share
    return bins


def _similarity(source, history):
    """Diagnostic similarity, not a probability or statistical interval.

    Average histogram intersection over six nested partitions. Exact matching
    composition scores 100; weaker spatial matches and unknown history reduce
    the score. Unknown history is never redistributed over known blocks.
    """
    overlaps = []
    for left, right in zip(source, history):
        if len(left) > len(right):
            left, right = right, left
        overlaps.append(math.fsum(min(share, right.get(key, 0.0)) for key, share in left.items()))
    return min(100.0, max(0.0, 100.0 * math.fsum(overlaps) / 6.0))


def aggregate_source_confidence(sources):
    """Combine source scores by physical source WMT, retaining unscored weight."""
    total, weighted = 0.0, 0.0
    for source in sources:
        tonnes = finite_number(source.get("source_wmt"))
        if tonnes is None or tonnes < 0:
            raise ValueError("Confidence aggregation requires non-negative finite source WMT.")
        score = finite_number(source.get("confidence_percent"))
        if score is not None and not 0 <= score <= 100:
            raise ValueError("Confidence must be between 0 and 100.")
        total += tonnes
        weighted += tonnes * (score or 0.0)
    confidence = min(100.0, max(0.0, weighted / total)) if total > 0 else None
    return {"source_wmt": total, "confidence_percent": confidence,
            "uncertainty_percent": 100 - confidence if confidence is not None else None}


class ReconciliationFactorResolver:
    """Resolve one OPF/brand using supplied samples and its standard factor record.

    ``standard_factors`` is the single brand record returned by the existing
    DataStreamReconciliation service, with effective blend/regression factors.
    It is required: this resolver must never invent a terminal factor of 1.0.
    Instantiate once per OPF/brand/configuration, then reuse for many sources.
    """

    def __init__(self, samples, *, opf, brand, scenario_start, standard_factors,
                 method=FACTOR_METHOD_SPATIAL_COMPOSITIONAL,
                 max_lookback_days=30, min_production_days=1,
                 window_mode=WINDOW_CALENDAR_DAYS, lookback_days=7, cells=None):
        self.opf = normalise_opf(opf)
        self.brand = clean_text(brand).upper()
        if not self.opf or not self.brand or self.brand == "*":
            raise ValueError("An OPF and product brand are required.")
        if method not in FACTOR_METHODS or window_mode not in WINDOW_MODES:
            raise ValueError("Unsupported reconciliation method or lookback mode.")
        self.method, self.window_mode = method, window_mode
        self.max_lookback_days = _positive_integer(max_lookback_days, "Maximum lookback")
        self.min_production_days = _positive_integer(min_production_days, "Minimum production days")
        self.lookback_days = _positive_integer(lookback_days, "Lookback days")
        self.end = _time(scenario_start)
        self.local = { (r["cell"], r["analyte"]): r for r in
                       normalise_reconciliation_settings({"cells": cells}).get("cells", [])
                       if r["opf"] == self.opf and r["brand"] == self.brand }
        widest = max([self.max_lookback_days, *[r.get("window", {}).get("max_lookback_days", 0)
                                             for r in self.local.values()]])
        self.start = self.end - timedelta(days=widest)
        if not isinstance(standard_factors, Mapping):
            raise ValueError("Supply the existing standard factor record for this brand.")
        self.standard = deepcopy(standard_factors)
        self.global_maps = {kind: {} for kind in KINDS}
        self.global_override = False
        for kind in KINDS:
            for analyte in SCHEMA_ANALYTES:
                value = (self.standard.get(kind) or {}).get(analyte)
                effective = finite_number(value.get("effective") if isinstance(value, Mapping) else value)
                if effective is None or effective <= 0:
                    raise ValueError(f"Supply the existing effective standard {kind}/{analyte} factor.")
                self.global_maps[kind][analyte] = effective
                calculated = finite_number(value.get("calculated")) if isinstance(value, Mapping) else None
                self.global_override |= calculated is not None and not math.isclose(calculated, effective)
        self._keys, self._selection_cache = {}, OrderedDict()
        self._index = defaultdict(set)
        self.history_warnings = []
        self.periods = self._prepare(samples) if method != FACTOR_METHOD_STANDARD else []
        self._window_cache = OrderedDict()
        for index in range(len(self.periods)):
            for depth, bins in enumerate(self.periods[index]["distributions"][1:]):
                for cell in bins:
                    self._index[depth, cell].add(index)

    def _key(self, name):
        text = clean_text(name)
        if text not in self._keys:
            self._keys[text] = canonical_block(text)
        return self._keys[text]

    def _lineage(self, blocks, total):
        weights = defaultdict(float)
        supplied = []
        for block in blocks or []:
            tonnes = finite_number(block.get("feed_wmt"))
            if tonnes is None or tonnes < 0:
                raise ValueError("Lineage tonnes must be finite and non-negative.")
            supplied.append(tonnes)
            key = self._key(block.get("grade_block_key"))
            if key and tonnes > 0:
                weights[key] += tonnes
        supplied_total = math.fsum(supplied)
        if supplied_total > total:
            if not math.isclose(supplied_total, total, rel_tol=1e-8, abs_tol=1e-6):
                raise ValueError("Lineage tonnes exceed total source/period feed tonnes.")
            # Correct floating-point overshoot only; never inflate incomplete lineage.
            weights = {key: value * (total / supplied_total) for key, value in weights.items()}
        return dict(sorted(weights.items()))

    def _prepare(self, samples):
        candidates = []
        for sample in samples:
            if not isinstance(sample, Mapping):
                raise ValueError("History samples must be mappings.")
            if normalise_opf(sample.get("opf")) != self.opf:
                continue
            if not is_readable(sample, RECONCILIATION_SAMPLE_SCHEMA_VERSION):
                raise ValueError("Unreadable reconciliation sample schema version.")
            start, end = _time(sample.get("period_start")), _time(sample.get("period_end"))
            if end <= start:
                raise ValueError("Invalid reconciliation period interval.")
            if start < self.start or end > self.end:
                continue
            brand = clean_text(sample.get("brand")).upper()
            candidates.append((brand, start, end, sample))
        available = {row[0] for row in candidates}
        matches = [self.brand] if self.brand in available else sorted(b for b in available if b.endswith(self.brand))
        if len(matches) != 1:
            self.history_warnings.append("Ambiguous history brand alias." if matches else "No matching brand history inside maximum lookback.")
            self.history_brand = None
            return []
        self.history_brand = matches[0]
        paired = defaultdict(dict)
        for brand, start, end, sample in candidates:
            if brand != self.history_brand:
                continue
            kind = sample.get("kind")
            if kind not in KINDS:
                raise ValueError(f"Unsupported factor kind: {kind!r}.")
            if sample.get("grain") not in SAMPLE_GRAINS:
                raise ValueError("Unsupported reconciliation period grain.")
            key = (start, end, sample.get("grain"))
            if kind in paired[key]:
                raise ValueError("Duplicate history sample would double-weight a period.")
            paired[key][kind] = sample
        result = []
        for (start, end, grain), pair in sorted(paired.items()):
            if set(pair) != set(KINDS):
                self.history_warnings.append(f"{start.isoformat()}: unpaired factor kind; period excluded.")
                continue
            feed = finite_number(pair["blend"].get("feed_wmt"))
            other_feed = finite_number(pair["regression"].get("feed_wmt"))
            if feed is None or feed <= 0 or other_feed is None or other_feed <= 0:
                self.history_warnings.append(f"{start.isoformat()}: non-positive period feed; period excluded.")
                continue
            weights = self._lineage(pair["blend"].get("contributing_blocks"), feed)
            other = self._lineage(pair["regression"].get("contributing_blocks"), other_feed)
            if (not math.isclose(feed, other_feed, rel_tol=1e-8, abs_tol=1e-6)
                or weights.keys() != other.keys()
                or any(not math.isclose(value, other[key], rel_tol=1e-8, abs_tol=1e-6) for key, value in weights.items())):
                raise ValueError("Paired blend/regression samples disagree on period feed composition.")
            factors = {kind: {a: finite_number((pair[kind].get("factors") or {}).get(a))
                              for a in SCHEMA_ANALYTES} for kind in KINDS}
            if result and start < result[-1]["end"]:
                raise ValueError("Overlapping history periods would double-weight feed.")
            result.append({
                "start": start, "end": end, "grain": grain, "day": start.date(),
                "feed": feed, "weights": weights, "distributions": _distributions(weights, feed),
                "factors": factors,
                "rows": max(int(pair[k].get("source_rows") or 0) for k in KINDS),
                "sample_ids": {k: (pair[k].get("provenance") or {}).get("sample_id")
                               or f"{self.opf}|{self.history_brand}|{start.isoformat()}|{k}" for k in KINDS},
                "lineage_basis": (pair["blend"].get("provenance") or {}).get("lineage_basis", "unspecified"),
            })
        return result

    def _window_periods(self, config=None, method=None):
        config = config or self._default_window()
        start_bound = self.end - timedelta(days=config["max_lookback_days"])
        bounded = {i for i, p in enumerate(self.periods) if p["start"] >= start_bound}
        if (method or self.method) != FACTOR_METHOD_LOOKBACK:
            return bounded
        if config["window_mode"] == WINDOW_CALENDAR_DAYS:
            # Same completed-calendar-date convention as the standard path;
            # this chosen window is fixed and never silently expanded.
            end = self.end.replace(hour=0, minute=0, second=0, microsecond=0)
            start = max(start_bound, end - timedelta(days=config["lookback_days"]))
            return {i for i in bounded if start <= self.periods[i]["start"] < end}
        days = sorted({self.periods[i]["day"] for i in bounded})
        if config["window_mode"] == WINDOW_LATEST_CAMPAIGN and days:
            # No campaign ID is supplied: use consecutive dates with this brand.
            campaign = [days[-1]]
            for day in reversed(days[:-1]):
                if campaign[-1] - day != timedelta(days=1):
                    break
                campaign.append(day)
            days = sorted(campaign)
        selected = set(days[-config["lookback_days"]:])
        return {i for i in bounded if self.periods[i]["day"] in selected}

    def _default_window(self):
        return dict(window_mode=self.window_mode, lookback_days=self.lookback_days,
                    min_production_days=self.min_production_days, max_lookback_days=self.max_lookback_days)

    def _cell_windows(self, key):
        cell = spatial_cell(key)
        return {a: {**self._default_window(), **self.local.get((cell, a), {}).get("window", {})}
                for a in SCHEMA_ANALYTES}

    def _allowed_periods(self, config, method):
        lookback = method == FACTOR_METHOD_LOOKBACK
        signature = (method, config["window_mode"] if lookback else None,
                     config["lookback_days"] if lookback else None, config["max_lookback_days"])
        if signature not in self._window_cache:
            self._window_cache[signature] = self._window_periods(config, method)
            if len(self._window_cache) > 2048:
                self._window_cache.popitem(last=False)
        else:
            self._window_cache.move_to_end(signature)
        return self._window_cache[signature]

    def _selection(self, key, *, windows=None, method=None, scores=None, level_depth=None):
        cells = _cells(key)
        windows = windows or self._cell_windows(key)
        method = method or self.method
        cache_key = (cells[0], method, level_depth,
                     tuple(tuple(sorted(windows[a].items())) for a in SCHEMA_ANALYTES))
        spatial = method == FACTOR_METHOD_SPATIAL_COMPOSITIONAL
        if spatial:
            if scores is None:
                source = _distributions({key: 1.0}, 1.0)
                scores = {i: _similarity(source, p["distributions"]) for i, p in enumerate(self.periods)}
            # Ranking depends on the whole source, even for a shared cell.
            ranked_scores = tuple(round(scores[i], 10) for i in range(len(self.periods)))
            cache_key += (ranked_scores,)
        if cache_key in self._selection_cache:
            self._selection_cache.move_to_end(cache_key)
            return self._selection_cache[cache_key]
        allowed = {}
        for analyte, config in windows.items():
            allowed[analyte] = self._allowed_periods(config, method)
        attempts = []
        for depth, cell in enumerate(cells):
            # Auto evaluates each level independently. Ordinary Spatial and
            # Lookback still stop at the first level with sufficient evidence.
            if level_depth is not None and depth != level_depth:
                continue
            indices = sorted(self._index.get((depth, cell), ()))
            eligible, missing, thresholds = {}, [], []
            for kind in KINDS:
                for analyte in SCHEMA_ANALYTES:
                    valid = tuple(i for i in indices if i in allowed[analyte] and self.periods[i]["factors"][kind][analyte] is not None
                                  and self.periods[i]["factors"][kind][analyte] > 0)
                    eligible[kind, analyte] = valid
                    dates = {self.periods[i]["day"] for i in valid}
                    minimum = windows[analyte]["min_production_days"]
                    if len(dates) < minimum:
                        missing.append(f"{kind}.{analyte}")
                    elif spatial:
                        best_by_date = {}
                        for i in valid:
                            day = self.periods[i]["day"]
                            best_by_date[day] = max(best_by_date.get(day, -1), ranked_scores[i])
                        thresholds.append(sorted(best_by_date.values(), reverse=True)[minimum - 1])
            if missing:
                attempts.append({"level": FACTOR_LEVELS[depth], "period_count": len(indices),
                                 "reason": "No spatially matching periods." if not indices else
                                 f"Minimum production days not met within selected windows for {', '.join(missing)}."})
                continue
            # A shared score cutoff includes the highest-matching shifts until
            # every series has enough distinct dates. Include all ties at the
            # cutoff; neither factor values nor recency break relevance ties.
            cutoff = min(thresholds) if spatial else None
            factors = {kind: {} for kind in KINDS}
            details = {kind: {} for kind in KINDS}
            used = set()
            shared_indices = {}
            for kind in KINDS:
                for analyte in SCHEMA_ANALYTES:
                    valid = tuple(i for i in eligible[kind, analyte] if not spatial or ranked_scores[i] >= cutoff)
                    valid = shared_indices.setdefault(valid, valid)
                    production_days = {self.periods[i]["day"] for i in valid}
                    feed = math.fsum(self.periods[i]["feed"] for i in valid)
                    factors[kind][analyte] = (math.fsum(
                        self.periods[i]["factors"][kind][analyte] * (self.periods[i]["feed"] / feed)
                        for i in valid) if feed else None)
                    details[kind][analyte] = {
                        "period_indices": valid, "production_days": len(production_days),
                        "period_count": len(valid), "feed_wmt": feed,
                        "row_count": sum(self.periods[i]["rows"] for i in valid),
                        "window": deepcopy(windows[analyte]),
                    }
                    used.update(valid)
            if not missing:
                result = {"depth": depth, "cell": cell, "indices": sorted(used),
                          "factors": factors, "details": details, "attempts": attempts}
                if spatial:
                    candidate_count = len(set().union(*eligible.values()))
                    result["spatial_selection"] = {
                        "rule": "whole_source_match_ranked_shifts", "version": 2,
                        "cutoff_evidence_match_score_percent": cutoff,
                        "candidate_period_count": candidate_count, "selected_period_count": len(used),
                        "excluded_period_count": candidate_count - len(used),
                        "ties": "All equally matching shifts at the cutoff are retained.",
                    }
                self._selection_cache[cache_key] = result
                if len(self._selection_cache) > 2048:
                    self._selection_cache.popitem(last=False)
                return result
        result = {"depth": None, "attempts": attempts}
        self._selection_cache[cache_key] = result
        if len(self._selection_cache) > 2048:
            self._selection_cache.popitem(last=False)
        return result

    def _config(self):
        return {"window_mode": self.window_mode if self.method == FACTOR_METHOD_LOOKBACK else None,
                "lookback_days": self.lookback_days if self.method == FACTOR_METHOD_LOOKBACK else None,
                "max_lookback_days": self.max_lookback_days,
                "min_production_days": self.min_production_days,
                "history_brand": getattr(self, "history_brand", None),
                "scenario_start": self.end.isoformat(),
                "confidence_method": CONFIDENCE_METHOD,
                "confidence_is_statistical": False}

    def _resolve(self, key, **kwargs):
        result = self._resolve_automatic(key, **kwargs)
        if self.method == FACTOR_METHOD_STANDARD or not key:
            return result
        cell = spatial_cell(key)
        result["provenance"]["cell_windows"] = deepcopy(kwargs.get("windows") or self._cell_windows(key))
        overrides = {}
        for analyte in SCHEMA_ANALYTES:
            setting = self.local.get((cell, analyte), {})
            for kind in KINDS:
                if kind in setting:
                    overrides.setdefault(kind, {})[analyte] = {
                        "automatic": result[f"{kind}_factors"][analyte], "effective": setting[kind]}
                    result[f"{kind}_factors"][analyte] = setting[kind]
        if overrides:
            result["manual_override"] = True
            result["provenance"]["local_override"] = {
                "opf": self.opf, "brand": self.brand, "cell": cell, "factors": overrides,
                "confidence_note": "The evidence match score is unchanged by manual factor edits."}
        return result

    def _resolve_automatic(self, key, *, source_id, source_kind, hex_id, fraction, distributions, coverage,
                           scores=None, selection=None, windows=None):
        base = dict(source_id=source_id, source_kind=source_kind, grade_block_key=key,
                    opf=self.opf, brand=self.brand, hex_id=hex_id, lineage_fraction=fraction, method=self.method)
        chosen = selection
        selection = {"depth": None, "attempts": []}
        if self.method == FACTOR_METHOD_STANDARD:
            reason = "Standard global mode requested; no spatial evidence match assessed."
        elif not key or coverage <= 0:
            reason = "No positive attributed source lineage; using supplied standard global factors."
        else:
            if self.method == FACTOR_METHOD_SPATIAL_COMPOSITIONAL and scores is None:
                scores = {i: _similarity(distributions, p["distributions"]) for i, p in enumerate(self.periods)}
            selection = chosen if chosen is not None else self._selection(key, scores=scores)
            reason = "All spatial levels exhausted within the configured window; using supplied standard global factors."
        level_search = selection.get("level_search")
        level_provenance = {"level_search": deepcopy(level_search)} if level_search else {}
        if selection["depth"] is None:
            return resolved_factor(
                **base, resolution_level=FACTOR_LEVEL_GLOBAL,
                blend_factors=self.global_maps["blend"], regression_factors=self.global_maps["regression"],
                confidence_percent=0.0, uncertainty_percent=100.0,
                fallback_reason=reason, manual_override=self.global_override,
                source_history=[{"source": "standard_global", "opf": self.opf, "brand": self.brand,
                                 "source_brand": self.standard.get("source_brand", self.brand),
                                 "source_brand_by_analyte": deepcopy(self.standard.get("source_brand_by_analyte", {})),
                                 "lookback_days": deepcopy(self.standard.get("lookback_days", {})),
                                 "standard_record": deepcopy(self.standard)}],
                provenance={**self._config(), **level_provenance, "attempts": deepcopy(selection["attempts"]),
                            "history_warnings": list(self.history_warnings),
                            "source_lineage_coverage": coverage,
                            "confidence_basis": "no_accepted_spatial_evidence"},
            )
        indices, depth = selection["indices"], selection["depth"]
        # All components of one source compare the same complete composition.
        # Keep this cache local to that source, so another hex cannot reuse it.
        if scores is None:
            scores = {}
        for index in indices:
            if index not in scores:
                scores[index] = _similarity(distributions, self.periods[index]["distributions"])
        detail = deepcopy(selection["details"])
        for kind in KINDS:
            for analyte in SCHEMA_ANALYTES:
                item = detail[kind][analyte]
                valid = item.pop("period_indices")
                item["confidence_percent"] = min(100.0, max(0.0, math.fsum(
                    scores[i] * (self.periods[i]["feed"] / item["feed_wmt"]) for i in valid)))
        confidence = min(detail[k][a]["confidence_percent"] for k in KINDS for a in SCHEMA_ANALYTES)
        history = []
        for index in indices:
            period = self.periods[index]
            history.append({
                "period_start": period["start"].isoformat(), "period_end": period["end"].isoformat(),
                "production_day": period["day"].isoformat(), "feed_wmt": period["feed"],
                "source_rows": period["rows"], "sample_ids": dict(period["sample_ids"]),
                "matched_feed_wmt": period["distributions"][depth + 1][selection["cell"]] * period["feed"],
                "lineage_coverage": math.fsum(period["weights"].values()) / period["feed"],
                "lineage_basis": period["lineage_basis"], "confidence_percent": scores[index],
                "valid_analytes": {k: [a for a in SCHEMA_ANALYTES if period["factors"][k][a] is not None
                                       and period["factors"][k][a] > 0] for k in KINDS},
            })
        first, last = min(self.periods[i]["start"] for i in indices), max(self.periods[i]["end"] for i in indices)
        return resolved_factor(
            **base, resolution_level=FACTOR_LEVELS[depth], matched_spatial_key=selection["cell"],
            blend_factors=selection["factors"]["blend"], regression_factors=selection["factors"]["regression"],
            source_rows=sum(self.periods[i]["rows"] for i in indices),
            source_feed_wmt=math.fsum(self.periods[i]["feed"] for i in indices), source_history=history,
            lookback_start=first, lookback_end=last,
            lookback_days=max(1, math.ceil((last - first).total_seconds() / 86400)),
            fallback_reason=(level_search["reason"] if level_search else
                             "; ".join(f"{a['level']}: {a['reason']}" for a in selection["attempts"])),
            confidence_percent=confidence, uncertainty_percent=100.0 - confidence,
            provenance={**self._config(), **level_provenance, "attempts": deepcopy(selection["attempts"]),
                        **({"spatial_selection": deepcopy(selection["spatial_selection"])} if "spatial_selection" in selection else {}),
                        "factor_history": detail, "history_warnings": list(self.history_warnings),
                        "source_lineage_coverage": coverage,
                        "confidence_basis": "conditional_on_attributed_source_lineage"},
        )

    def resolve(self, grade_block, *, source_id="", source_kind="inventory", hex_id=None,
                source_composition=None, source_wmt=None, lineage_fraction=None):
        """Resolve one lineage component; supply full source composition for its score.

        Without composition, the reference source is this one parent block.
        Component scores condition on known source lineage. Use resolve_source
        for whole-hex/inventory scores, including the unknown source fraction.
        """
        key = self._key(grade_block)
        if source_composition is None:
            total, weights = 1.0, ({key: 1.0} if key else {})
        else:
            total = finite_number(source_wmt)
            if total is None or total <= 0:
                raise ValueError("Source composition requires positive total source WMT.")
            weights = self._lineage(source_composition, total)
        known = math.fsum(weights.values())
        fraction = weights.get(key, 0.0) / total if lineage_fraction is None else finite_number(lineage_fraction)
        if fraction is None or not 0 <= fraction <= 1:
            raise ValueError("Lineage fraction must be between zero and one.")
        if self.method == FACTOR_METHOD_MAX_CONFIDENCE:
            # Select against the full physical source, even when a caller only
            # requests one component's result.
            blocks = [{"grade_block_key": k, "feed_wmt": v} for k, v in weights.items()]
            result = self.resolve_source(source_id, source_kind, blocks, total, hex_id=hex_id)
            record = next((r for r in result["records"] if r["grade_block_key"] == key), None)
            if record is None:
                raise ValueError("Requested component is not present in the supplied source composition.")
            record["lineage_fraction"] = fraction
            return record
        return self._resolve(key, source_id=source_id, source_kind=source_kind, hex_id=hex_id,
                             fraction=fraction, distributions=_distributions(weights, known),
                             coverage=min(known / total, 1.0))

    def resolve_source(self, source_id, source_kind, contributing_blocks, source_wmt, *, hex_id=None):
        """Return component factors and source confidence, without applying grades.

        One additional global record represents all missing lineage. Physical
        source fractions are retained; unknown source mass reduces the whole
        source score exactly once rather than being dropped or double-penalized.
        """
        total = finite_number(source_wmt)
        if total is None or total < 0:
            raise ValueError("Source WMT must be finite and non-negative.")
        weights = self._lineage(contributing_blocks, total)
        known = math.fsum(weights.values())
        distributions = _distributions(weights, known)
        records, scores = [], {}
        if self.method == FACTOR_METHOD_SPATIAL_COMPOSITIONAL and weights:
            scores = {i: _similarity(distributions, p["distributions"]) for i, p in enumerate(self.periods)}
        search = None
        if self.method == FACTOR_METHOD_MAX_CONFIDENCE:
            from classes.ReconciliationConfidenceSearch import ConfidenceSearch
            search = ConfidenceSearch(self).select(weights, total, distributions)
            scores = search["scores"]
        if total > 0:
            for key, tonnes in weights.items():
                records.append(self._resolve(
                    key, source_id=source_id, source_kind=source_kind, hex_id=hex_id,
                    fraction=tonnes / total, distributions=distributions, coverage=min(known / total, 1.0), scores=scores,
                    selection=search["selections"].get(key) if search else None,
                    windows=search["windows"].get(key) if search else None))
            unknown = max(total - known, 0.0)
            if unknown > 0:
                records.append(self._resolve(
                    "", source_id=source_id, source_kind=source_kind, hex_id=hex_id,
                    fraction=unknown / total, distributions=distributions, coverage=min(known / total, 1.0)))
        confidence = min(100.0, math.fsum(r["lineage_fraction"] * r["confidence_percent"] for r in records)) if total > 0 else None
        if search:
            for record in records:
                record["provenance"]["auto_selection"] = deepcopy(search["audit"])
        return {"source_id": clean_text(source_id), "source_kind": clean_text(source_kind),
                "hex_id": clean_text(hex_id), "source_wmt": total, "records": records,
                "confidence_percent": confidence,
                "uncertainty_percent": 100.0 - confidence if confidence is not None else None,
                "lineage_coverage": min(known / total, 1.0) if total > 0 else 0.0,
                "confidence_method": CONFIDENCE_METHOD,
                **({"auto_selection": search["audit"]} if search else {})}
