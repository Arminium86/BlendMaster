"""Whole-source historical analogues for Auto, with one common set of shifts."""

from copy import deepcopy
import math

from classes.PhaseSchemas import FACTOR_LEVELS, SCHEMA_ANALYTES
from classes.ReconciliationFactorResolver import KINDS, _cells


class SharedHistorySearch:
    def __init__(self, search, weights, total):
        self.search, self.engine = search, search.resolver
        self.weights, self.total = weights, total
        self.cells = {key: _cells(key) for key in weights}
        # A common set supplies every factor series. Partial factor records
        # remain available to the existing component-based candidate.
        self.complete = {
            i for i, p in enumerate(self.engine.periods)
            if all(p["factors"][k][a] is not None and p["factors"][k][a] > 0
                   for k in KINDS for a in SCHEMA_ANALYTES)
        }
        self.pools = []
        for depth in range(5):
            required = {cells[depth] for cells in self.cells.values()}
            pools = [self.engine._index.get((depth, cell), set()) for cell in required]
            self.pools.append(set.intersection(set(self.complete), *pools) if pools else set())

    def evaluate(self, policy, scores):
        """Compare one shared level/set per source against the same physical-WMT objective."""
        if not self.weights or self.total <= 0:
            return None
        spatial = policy[0] == "spatial_compositional"
        method = "spatial_compositional" if spatial else "lookback"
        windows = {key: self.search.windows(key, policy) for key in self.weights}
        configs = [config for by_analyte in windows.values() for config in by_analyte.values()]
        minimum = max(c["min_production_days"] for c in configs)
        maximum = min(c["max_lookback_days"] for c in configs)
        distinct = {tuple(sorted(c.items())): c for c in configs}
        allowed = set.intersection(*[set(self.engine._allowed_periods(c, method)) for c in distinct.values()])
        common_window = {**configs[0], "min_production_days": minimum, "max_lookback_days": maximum}
        known_share = min(math.fsum(self.weights.values()) / self.total, 1.0)
        ranked_scores = {i: round(scores[i], 10) for i in allowed}
        comparisons, best = [], None
        for depth, level in enumerate(FACTOR_LEVELS[:-1]):
            eligible = self.pools[depth] & allowed
            dates = {self.engine.periods[i]["day"] for i in eligible}
            if len(dates) < minimum:
                comparisons.append(dict(level=level, eligible=False, selected=False,
                    reason=f"Only {len(dates)} common production dates; {minimum} required with all source groups and ten valid factor series inside local bounds."))
                continue
            cutoff = None
            if spatial:
                best_by_date = {}
                for i in eligible:
                    day = self.engine.periods[i]["day"]
                    best_by_date[day] = max(best_by_date.get(day, -1), ranked_scores[i])
                cutoff = sorted(best_by_date.values(), reverse=True)[minimum - 1]
            indices = tuple(sorted(i for i in eligible if not spatial or ranked_scores[i] >= cutoff))
            days = len({self.engine.periods[i]["day"] for i in indices})
            feed = math.fsum(self.engine.periods[i]["feed"] for i in indices)
            factors = {k: {a: math.fsum(self.engine.periods[i]["factors"][k][a] * (self.engine.periods[i]["feed"] / feed)
                                       for i in indices) for a in SCHEMA_ANALYTES} for k in KINDS}
            details = {k: {a: dict(period_indices=indices, production_days=days, period_count=len(indices),
                                   feed_wmt=feed, row_count=sum(self.engine.periods[i]["rows"] for i in indices),
                                   window=deepcopy(common_window)) for a in SCHEMA_ANALYTES} for k in KINDS}
            template = dict(depth=depth, indices=list(indices), factors=factors, details=details, attempts=[])
            metrics = self.search.selection_metrics(template, scores)
            score = min(100.0, known_share * metrics["score"])
            comparisons.append(dict(level=level, eligible=True, selected=False,
                evidence_match_score_percent=metrics["score"], physical_source_evidence_match_score_percent=score,
                min_production_days=days, period_count=len(indices), feed_wmt=feed))
            # Use exactly the existing source-level tie rules. Unknown material
            # retains zero evidence and its supplied global factors.
            rank = (round(score, 10), -round(1 - known_share, 12),
                    -round(depth * known_share + 5 * (1 - known_share), 12),
                    round(days * known_share, 10), round(feed * known_share, 6))
            if best is None or rank > best["rank"]:
                if spatial:
                    template["spatial_selection"] = dict(
                        rule="whole_source_match_ranked_shared_shifts", version=1,
                        cutoff_evidence_match_score_percent=cutoff, candidate_period_count=len(eligible),
                        selected_period_count=len(indices), excluded_period_count=len(eligible) - len(indices),
                        ties="All equally matching shifts at the cutoff are retained.")
                best = dict(rank=rank, confidence_percent=score, policy=policy, windows=windows,
                            history_approach="shared_history", template=template,
                            shared_history=dict(min_production_days=minimum, max_lookback_days=maximum,
                                selected_period_count=len(indices), production_days=days, feed_wmt=feed,
                                known_source_fraction=known_share,
                                scoring="Individual shift matches weighted by total shift feed; compositions are not pooled before scoring.",
                                eligibility="Each shift contains every known source group at one common spatial/material level and supports all ten factor series."))
        if best is None:
            return {"eligible": False, "history_approach": "shared_history", "level_comparison": comparisons,
                    "reason": "No common shift set meets all source-group, factor-validity and local-window requirements."}
        template = best.pop("template")
        chosen_level = FACTOR_LEVELS[template["depth"]]
        for c in comparisons:
            c["selected"] = c["level"] == chosen_level
        template["level_search"] = dict(strategy="best_eligible_shared_level", selected_level=chosen_level,
            reason="Auto selected a common set of individually source-matched shifts for all known source components and all ten factor series.",
            candidates=comparisons)
        template["shared_history"] = best["shared_history"]
        best["eligible"] = True
        best["level_comparison"] = comparisons
        best["selections"] = {key: {**template, "cell": cells[template["depth"]]} for key, cells in self.cells.items()}
        signature = tuple(template["details"][k][a]["period_indices"] for k in KINDS for a in SCHEMA_ANALYTES)
        best["signature"] = tuple((key, template["depth"], signature) for key in self.weights)
        return best
