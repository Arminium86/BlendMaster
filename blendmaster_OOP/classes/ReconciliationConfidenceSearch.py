"""Bounded search over reconciliation windows and every eligible spatial level.

The objective separates by physical source and brand. One policy is selected for
the whole inventory/hex composition; each component retains the shared-level
rule for all ten factor series. Spatial candidates select source-matched shift
prefixes; lookback candidates retain all eligible shifts in their time window.
"""

import math
from datetime import timedelta

from classes.PhaseSchemas import FACTOR_LEVELS, FACTOR_LEVEL_GLOBAL, SCHEMA_ANALYTES


SEARCH_VERSION = 3
KINDS = ("blend", "regression")
SPATIAL = "spatial_compositional"
LOOKBACK = "lookback"


class ConfidenceSearch:
    def __init__(self, resolver):
        self.resolver = resolver
        self.series_scores = {}

    def policies(self, widest):
        engine = self.resolver
        if not hasattr(engine, "_confidence_policies"):
            engine._confidence_policies = {}
        if widest in engine._confidence_policies:
            return engine._confidence_policies[widest]
        midnight = engine.end.replace(hour=0, minute=0, second=0, microsecond=0)
        # Membership only changes when an integer-day boundary includes a
        # period. This covers every N without iterating through empty years.
        horizons = {widest, 1}
        calendar = {1}
        bounded = [p for p in engine.periods if p["start"] >= engine.end - timedelta(days=widest)]
        for period in bounded:
            horizons.add(max(1, math.ceil((engine.end - period["start"]).total_seconds() / 86400)))
            days = (midnight.date() - period["day"]).days
            if days > 0:
                calendar.add(days)
        production_dates = len({p["day"] for p in bounded})
        policies = [(SPATIAL, widest)]
        policies += [(SPATIAL, n) for n in sorted(horizons, reverse=True) if 1 <= n < widest]
        policies += [("calendar_days", n) for n in sorted(calendar) if n <= widest]
        policies += [(mode, n) for mode in ("production_days", "latest_campaign")
                     for n in range(1, production_dates + 1)]
        engine._confidence_policies[widest] = policies
        return policies

    def windows(self, key, policy):
        family, n = policy
        windows = self.resolver._cell_windows(key)
        for config in windows.values():
            if family == SPATIAL:
                config["max_lookback_days"] = min(n, config["max_lookback_days"])
            else:
                config["window_mode"], config["lookback_days"] = family, n
        return windows

    def selection_metrics(self, selection, scores):
        """Use the same fixed whole-source score at every eligibility level."""
        engine = self.resolver
        detail = selection["details"]
        series_scores, signature = [], []
        for kind in KINDS:
            for analyte in SCHEMA_ANALYTES:
                evidence = detail[kind][analyte]
                indices = evidence["period_indices"]
                signature.append(tuple(indices))
                if indices not in self.series_scores:
                    self.series_scores[indices] = min(100.0, max(0.0, math.fsum(
                        scores[i] * (engine.periods[i]["feed"] / evidence["feed_wmt"]) for i in indices)))
                series_scores.append(self.series_scores[indices])
        return dict(score=min(series_scores), signature=tuple(signature),
                    production_days=min(detail[k][a]["production_days"] for k in KINDS for a in SCHEMA_ANALYTES),
                    feed_wmt=math.fsum(engine.periods[i]["feed"] for i in selection["indices"]))

    def best_level(self, key, config, method, scores):
        """Components separate in the WMT-weighted objective; no level cross-product is needed."""
        candidates, failures = [], []
        best, best_rank = None, None
        for depth, level in enumerate(FACTOR_LEVELS[:-1]):
            selection = self.resolver._selection(
                key, windows=config, method=method, scores=scores, level_depth=depth)
            if selection["depth"] is None:
                failures.extend(selection["attempts"])
                candidates.append(dict(level=level, eligible=False, selected=False,
                                       reason="; ".join(a["reason"] for a in selection["attempts"])))
                continue
            metrics = self.selection_metrics(selection, scores)
            # Specificity breaks score ties; it is not a deduction from the score.
            rank = (round(metrics["score"], 10), -depth, metrics["production_days"], metrics["feed_wmt"])
            candidates.append(dict(level=level, eligible=True, selected=False,
                                   evidence_match_score_percent=metrics["score"],
                                   min_production_days=metrics["production_days"],
                                   period_count=len(selection["indices"]), feed_wmt=metrics["feed_wmt"]))
            if best is None or rank > best_rank:
                best, best_rank = selection, rank
        if best is None:
            best = {"depth": None, "attempts": failures}
            chosen_level = FACTOR_LEVEL_GLOBAL
            reason = "No spatial level has sufficient valid history within this window; using standard global factors."
        else:
            chosen_level = FACTOR_LEVELS[best["depth"]]
            reason = "Auto compared all five spatial levels; selected the highest evidence match score, preferring specificity on score ties."
            for candidate in candidates:
                candidate["selected"] = candidate["level"] == chosen_level
        return {**best, "level_search": {
            "strategy": "best_eligible_level", "selected_level": chosen_level,
            "reason": reason, "candidates": candidates,
        }}

    def evaluate(self, weights, total, policy, scores, *, search_levels=True):
        engine = self.resolver
        method = SPATIAL if policy[0] == SPATIAL else LOOKBACK
        selections, windows, signature = {}, {}, []
        confidence, global_share, depth_score, days_score, feed_score = [], [], [], [], []
        for key, wmt in weights.items():
            share = wmt / total
            config = self.windows(key, policy)
            selection = (self.best_level(key, config, method, scores) if search_levels else
                         engine._selection(key, windows=config, method=method, scores=scores))
            windows[key], selections[key] = config, selection
            depth = selection["depth"]
            if depth is None:
                global_share.append(share)
                depth_score.append(5 * share)
                signature.append((key, None))
                continue
            metrics = self.selection_metrics(selection, scores)
            signature.append((key, depth, metrics["signature"]))
            confidence.append(share * metrics["score"])
            depth_score.append(share * depth)
            days_score.append(share * metrics["production_days"])
            feed_score.append(share * metrics["feed_wmt"])
        score = min(100.0, math.fsum(confidence)) if total > 0 else None
        unknown = max(1 - math.fsum(weights.values()) / total, 0) if total > 0 else 0
        rank = (round(score or 0, 10), -round(math.fsum(global_share) + unknown, 12),
                -round(math.fsum(depth_score) + 5 * unknown, 12),
                round(math.fsum(days_score), 10), round(math.fsum(feed_score), 6))
        return {"rank": rank, "confidence_percent": score, "policy": policy,
                "selections": selections, "windows": windows, "signature": tuple(signature)}

    def membership(self, caps, policy):
        family, n = policy
        method = SPATIAL if family == SPATIAL else LOOKBACK
        profiles = []
        for cap in caps:
            config = dict(window_mode="calendar_days" if family == SPATIAL else family,
                          lookback_days=n, max_lookback_days=min(cap, n) if family == SPATIAL else cap)
            profiles.append(tuple(sorted(self.resolver._allowed_periods(config, method))))
        # Spatial now ranks shifts by source match. Its result must never be
        # reused for a lookback window, even with identical temporal membership.
        return method, tuple(profiles)

    @staticmethod
    def description(result):
        family, n = result["policy"]
        return {"selected_method": SPATIAL if family == SPATIAL else LOOKBACK,
                "window_mode": None if family == SPATIAL else family,
                "window_days": n, "confidence_percent": result["confidence_percent"],
                "selected_levels": sorted({FACTOR_LEVELS[s["depth"]] if s["depth"] is not None else FACTOR_LEVEL_GLOBAL
                                           for s in result["selections"].values()})}

    def select(self, weights, total, distributions):
        from classes.ReconciliationFactorResolver import _similarity
        engine = self.resolver
        # Similarity depends on the complete physical source, not the window.
        # Compute once per period/source, and reuse across components/policies.
        scores = {i: _similarity(distributions, p["distributions"]) for i, p in enumerate(engine.periods)} if weights else {}
        caps = sorted({c["max_lookback_days"] for key in weights for c in engine._cell_windows(key).values()})
        policies = self.policies(max(caps) if caps else engine.max_lookback_days)
        # Keep the ordinary full-horizon Spatial result as the comparison
        # baseline. Auto also searches every level inside that same horizon.
        baseline = self.evaluate(weights, total, policies[0], scores, search_levels=False)
        best, families, unique = None, {}, set()
        evaluated = {}
        candidates = policies if weights and total > 0 else policies[:1]
        for policy in candidates:
            membership = self.membership(caps, policy)
            if membership not in evaluated:
                evaluated[membership] = self.evaluate(weights, total, policy, scores)
            # Equivalent temporal families have identical factors and scores.
            # Keep their labels for comparison, and materialise only the winner.
            result = {**evaluated[membership], "policy": policy}
            unique.add(result["signature"])
            family = policy[0]
            if family not in families or result["rank"] > families[family]["rank"]:
                families[family] = result
            if best is None or result["rank"] > best["rank"]:
                best = result
        status = ("zero_mass" if total <= 0 else "no_lineage" if not weights else
                  "global_fallback" if all(s["depth"] is None for s in best["selections"].values()) else "selected")
        audit = {"search_version": SEARCH_VERSION, "status": status, **self.description(best),
                 "baseline_confidence_percent": baseline["confidence_percent"],
                 "improvement_percent": max((best["confidence_percent"] or 0) - (baseline["confidence_percent"] or 0), 0)
                                        if total > 0 else None,
                 "candidate_count": len(candidates) if weights else 0,
                 "level_candidate_count": sum(len(s.get("level_search", {}).get("candidates", []))
                                              for r in evaluated.values() for s in r["selections"].values()),
                 "unique_evidence_count": len(unique) if weights else 0,
                 "best_by_family": {family: self.description(result) for family, result in families.items()} if weights else {},
                 "objective": "physical_source_wmt_weighted_evidence_match_score",
                 "tie_break": "less_global_then_more_specific_then_more_production_days_then_more_feed_then_fixed_policy_order",
                 "fallback_strategy": "best_eligible_level",
                 "scope": "All five spatial levels within source-matched Spatial and lookback windows; one method/window per source and brand, best eligible level per component.",
                 "baseline": "Ordinary source-matched Spatial within the full maximum lookback, using its first sufficient shared level."}
        return {"audit": audit, "scores": scores, "selections": best["selections"], "windows": best["windows"]}
