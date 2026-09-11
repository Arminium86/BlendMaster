"""Joint MILP for simultaneous physical tipping points and shared ROM balances."""

from collections import defaultdict
from copy import deepcopy

from pulp import LpBinary, LpMinimize, LpProblem, LpVariable, lpSum, value

from classes.Optimizer import Optimizer, RetryingCBCSolver
from classes.MultiFeedSettings import multi_feed_settings, period_lanes, route_allowed


class MultiLaneOptimizer(Optimizer):
    def __init__(self, settings):
        self.settings = multi_feed_settings(settings)

    @staticmethod
    def _resume(steps):
        try:
            next(steps)
        except StopIteration as finished:
            return finished.value
        raise RuntimeError("Unexpected repeated optimisation boundary.")

    def run_blending_optimization(self, event_pool, period_crusher_target, steady_state_duration,
                                  steady_state_controller_source, steady_state_controller_tonnes,
                                  periods, period_tracker, min_stockpiles=None, max_stockpiles=None,
                                  min_stockpile_contribution_ratio=None, solver_config=None,
                                  excluded_source_sets=None, excluded_stockpile_sets=None):
        config = dict(solver_config or {})
        points = period_lanes(self.settings, period_tracker, period_crusher_target)
        target_builds = config.get("target_product_builds") or {}
        for point in points:
            builds = [b for b in target_builds.values() if not b.get("contributing_opfs") or point["opf"] in b["contributing_opfs"]]
            brands = {b.get("brand") for b in builds if b.get("brand")}
            if len(brands) > 1:
                raise ValueError(f"{point['opf']}: active product builds must use the same brand.")
            point["active_brand"] = next(iter(brands), point["target"].get("brand", ""))
            if config.get("product_builds_configured") and not builds:
                point["target"]["crusher_rate"] = 0.0
        models, all_events = [], []
        for point_index, point in enumerate(points):
            events = []
            for event_index, original in enumerate(event_pool):
                source = Optimizer.selection_source_name(original)
                if original.is_stockpile and not route_allowed(self.settings, source, point["name"]):
                    continue
                if original.is_grade_block:
                    allowed = (config.get("direct_tip_point_by_payload") or {}).get(str(original.grade_block), [])
                    if not point["direct_tip_enabled"] or point["name"] not in allowed:
                        continue
                event = deepcopy(original)
                event._multi_point = point["name"]
                event._multi_opf = point["opf"]
                event._multi_key = (point_index, event_index)
                if self.settings["mode"] == "combined_opf":
                    from classes.OPFSourceProfiles import apply_opf_profile
                    apply_opf_profile(event, point["opf"], config)
                if event.is_stockpile:
                    rate = self.settings["route_reclaim_rates"].get(source, {}).get(point["name"])
                    if rate is not None:
                        event._rate = rate
                events.append(event)
            lane_config = {**config, **point["solver_config"], "direct_tip_enabled": point["direct_tip_enabled"],
                           "target_product_brand": point["active_brand"],
                           "target_product_builds": {}, "target_product_build_states": {}}
            for key in ("target_product_build", "target_product_build_state", "product_build_completion_constraint"):
                lane_config.pop(key, None)
            steps = Optimizer.blending_problem_steps(events, point["target"], steady_state_duration,
                      None, None, periods, period_tracker,
                      (point.get("min_stockpiles") if point.get("min_stockpiles") is not None else min_stockpiles) if point["target"]["crusher_rate"] > 0 else None,
                      point.get("max_stockpiles") if point.get("max_stockpiles") is not None else max_stockpiles,
                      min_stockpile_contribution_ratio, lane_config)
            try:
                packet = next(steps)
            except StopIteration as finished:
                return finished.value
            packet["problem"].name = f"tip_{point_index}"
            models.append((point, steps, packet))
            for event in packet["events"]:
                aggregate_event = deepcopy(event)
                aggregate_event._multi_materialized = True
                all_events.append(aggregate_event)

        # The aggregate model owns physical balances, the common equipment
        # boundary and product-build constraints. Crusher constraints stay local.
        aggregate_target = deepcopy(period_crusher_target)
        aggregate_target.update(crusher_rate=sum(p["target"]["crusher_rate"] for p in points),
                                direct_feed_ratio_min=0.0, direct_feed_ratio_max=1.0, custom_constraints=[])
        aggregate_config = {**config, "enforce_calendar_crusher_grade_targets": False,
                            "direct_tip_enabled": True, "prefer_fewer_stockpiles": False}
        aggregate_steps = Optimizer.blending_problem_steps(all_events, aggregate_target, steady_state_duration,
                           steady_state_controller_source, steady_state_controller_tonnes, periods, period_tracker,
                           None, None, min_stockpile_contribution_ratio, aggregate_config,
                           excluded_source_sets, excluded_stockpile_sets)
        try:
            aggregate = next(aggregate_steps)
        except StopIteration as finished:
            return finished.value
        aggregate["problem"].name = "shared"
        joint = LpProblem("simultaneous_tipping_points", LpMinimize)
        joint.extend(aggregate["problem"], use_objective=False)
        objective = aggregate["product_objective"]
        aggregate_vars = {event._multi_key: variable for event, variable in zip(aggregate["events"], aggregate["variables"])}
        for point, steps, packet in models:
            joint.extend(packet["problem"], use_objective=False)
            objective += packet["problem"].objective
            for event, variable in zip(packet["events"], packet["variables"]):
                joint += variable == aggregate_vars[event._multi_key]

        physical = defaultdict(list)
        point_usage = defaultdict(list)
        for event, variable in zip(aggregate["events"], aggregate["variables"]):
            identity = ("stockpile", event.stockpile) if event.is_stockpile else ("payload", event.grade_block)
            physical[identity].append((event, variable))
            if event.is_stockpile:
                point_usage[(event.stockpile, event._multi_point)].append((event, variable))
        for identity, entries in physical.items():
            joint += lpSum(v for _, v in entries) <= max(float(e.balance) for e, _ in entries)
            if identity[0] == "stockpile":
                quantity = max(float(e.max_quantity) for e, _ in entries)
                duration = periods.get_periods()[f"{period_tracker}_duration"]
                joint += lpSum(v for _, v in entries) <= quantity * steady_state_duration / duration
        stockpile_choices = defaultdict(list)
        reclaim_coefficients = {e._multi_key: c for e, c in zip(aggregate['events'], aggregate['reclaimer_coefficients'])}
        for index, ((source, point), entries) in enumerate(point_usage.items()):
            chosen = LpVariable(f"route_{index}", cat=LpBinary)
            capacity = max(float(e.balance) for e, _ in entries)
            joint += lpSum(v for _, v in entries) <= capacity * chosen
            route_rate = self.settings['route_reclaim_rates'].get(source, {}).get(point)
            if route_rate is not None:
                joint += lpSum(v * reclaim_coefficients[e._multi_key] for e, v in entries) <= route_rate * steady_state_duration
            stockpile_choices[source].append(chosen)
        for choices in stockpile_choices.values():
            joint += lpSum(choices) <= 1
        joint += objective
        limits = [p["time_limit"] for _, _, p in models if p["time_limit"] is not None]
        joint.solve(RetryingCBCSolver(msg=False, timeLimit=min(limits) if limits else None))
        aggregate["problem"].status = joint.status
        result = self._resume(aggregate_steps)
        lane_results = {}
        for point, steps, packet in models:
            packet["problem"].status = joint.status
            lane_results[point["name"]] = self._resume(steps)
        result["tipping_point_results"] = lane_results
        result["solver_objective_value"] = value(joint.objective)
        quantity = sum(float(r.get("actual_tonnes") or 0) for r in result.get("transactions", []))
        result["solver_score"] = -float(value(joint.objective) or 0) / quantity if quantity else 0.0
        if result["Linprog_result_object"].success:
            # Aggregate reporting keeps shared product-build contributions;
            # each transaction also carries its physical tipping-point identity.
            reported = [(e, v) for e, v in zip(aggregate["events"], aggregate["variables"])
                        if float(v.value() or 0) >= 0]
            if len(reported) != len(result["transactions"]):
                raise RuntimeError("Joint solve transaction identity mismatch.")
            for transaction, (event, _) in zip(result["transactions"], reported):
                transaction.update(tipping_point=event._multi_point, opf=event._multi_opf,
                                   reconciliation_opf=event._multi_opf,
                                   reconciliation_scenario=getattr(event, "_reconciliation_scenario", ""))
        return result
