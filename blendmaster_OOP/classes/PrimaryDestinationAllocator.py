"""Allocate final ROM payloads in 2WP order, independently for each plan.

The payload that fills a build stays whole, including any capacity overrun.
Only the next payload advances. Unconfirmed inputs remain unresolved for review
or the later fallback rule engine; this module never invents a destination.
"""

from collections import defaultdict
from contextlib import closing
from copy import deepcopy
import json
import math
import sqlite3

from classes.DestinationBuildOrder import digest, stockpile_key
from classes.DestinationProgress import progress_settings, resolve_progress
from classes.GradeBlockIdentity import grade_block_material_type
from classes.MaterialDestinationPlan import MaterialDestinationPlan
from classes.DestinationRules import DestinationRuleEngine, METADATA_COLUMNS
from setup.InventoryBuildLineage import clean_text, finite_number
from setup.ProductAssayHistory import awst


VERSION = 1
AUDIT_COLUMNS = {
    "destination_allocation_runs": "plan_type plan_id schema_version context_signature status payload_count payload_wmt direct_tipped_wmt non_direct_wmt assigned_wmt unresolved_wmt out_of_scope_wmt outside_window_wmt overrun_wmt reason input_signature scenario_context_signature allocation_window_start allocation_window_end".split(),
    "destination_primary_assignments": "plan_type plan_id payload_id source delivered_datetime payload_wmt direct_tipped_wmt non_direct_wmt rom_area material_type planned_destination assigned_destination instance_id build_instance order_position assigned_wmt unresolved_wmt out_of_scope_wmt outside_window_wmt capacity_before_wmt capacity_after_wmt overrun_wmt capacity_basis selection_basis status reason context_signature".split(),
    "destination_capacity_ledger": "plan_type plan_id event_number payload_id source delivered_datetime rom_area material_type event instance_id destination build_instance order_position capacity_before_wmt capacity_after_wmt consumed_wmt overrun_wmt next_instance_id next_destination reason context_signature".split(),
    "destination_capacity_balances": "plan_type plan_id instance_id destination build_instance rom_area planned_wmt consumed_wmt overrun_wmt starting_wmt remaining_wmt capacity_basis".split(),
}
AUDIT_COLUMNS["destination_primary_assignments"].extend([
    *METADATA_COLUMNS, "primary_rule", "alternate_destinations", "destination_rule_signature",
])
AUDIT_COLUMNS["destination_allocation_runs"].append("destination_rule_signature")


class PrimaryDestinationAllocator:
    """One chronological capacity ledger for one plan and immutable input context."""

    def __init__(self, order, activity, settings, *, plan_type="optimised", plan_id="Primary"):
        self.order = deepcopy(order)
        self.settings = progress_settings(settings)
        self.owner = dict(plan_type=clean_text(plan_type).lower(), plan_id=clean_text(plan_id) or "Primary")
        if not self.owner["plan_type"] or not order.get("signature"):
            raise ValueError("Plan type and build-order signature are required.")
        self.context_signature = digest([VERSION, order["signature"], order["orders"], order["areas"], activity, self.settings])
        self.areas = {stockpile_key(k): clean_text(v).upper() for k, v in order["areas"].items() if clean_text(v)}
        self.lanes, self.positions, self.instances = {}, {}, {}
        self.ledger, self.assignments, self.seen = [], [], {}
        self.last_key = None
        rows = resolve_progress(order, activity or {}, self.settings["selected_instances"])
        current_ids = {r["current"]["instance_id"] for r in rows if r["current"]}
        for row in rows:
            lane = (row["rom_area"], row["material_type"])
            sequence = row["sequence"]
            positions = [r["order_position"] for r in sequence]
            if positions != sorted(set(positions)) or len({r["instance_id"] for r in sequence}) != len(sequence):
                raise ValueError("2WP build order must have unique, increasing positions and instances within each ROM area/material type.")
            self.lanes[lane] = row
            self.positions[lane] = sequence.index(row["current"]) if row["current"] else None
            for entry in sequence:
                key = entry["instance_id"]
                tonnes = finite_number(entry.get("planned_wmt"))
                if not key or tonnes is None or tonnes < 0:
                    raise ValueError("Build instances require an identity and non-negative planned ROM WMT.")
                physical = (entry["destination"], entry["build_instance"], entry["rom_area"])
                if key in self.instances and self.instances[key]["physical"] != physical:
                    raise ValueError("One build instance cannot identify different physical stockpiles or ROM areas.")
                item = self.instances.setdefault(key, dict(physical=physical, planned_wmt=0.0, consumed_wmt=0.0, overrun_wmt=0.0))
                item["planned_wmt"] += tonnes
        for key, item in self.instances.items():
            # All materials share one physical capacity. A currently active
            # instance needs an entered remainder; a later instance uses the
            # total planned build tonnes across its material rows.
            entered = self.settings["remaining_wmt"].get(key)
            initial = entered if entered is not None else None if key in current_ids else item["planned_wmt"]
            item.update(starting_wmt=initial, remaining_wmt=initial,
                        capacity_basis="User entered" if entered is not None else "Not set" if initial is None else "2WP planned ROM WMT")

    @staticmethod
    def _payload(raw):
        row = dict(raw)
        identity = clean_text(row.get("payload_id"))
        if not identity:
            raise ValueError("Final payloads require a unique payload ID.")
        for key in ("payload_wmt", "direct_tipped_wmt", "non_direct_wmt"):
            number = finite_number(row.get(key))
            if number is None or number < 0 or isinstance(row.get(key), bool):
                raise ValueError(f"Payload {identity}: {key} must be a finite, non-negative ROM WMT value.")
            row[key] = number
        if not math.isclose(row["payload_wmt"], row["direct_tipped_wmt"] + row["non_direct_wmt"], rel_tol=1e-10, abs_tol=1e-7):
            raise ValueError(f"Payload {identity}: direct-tip and non-direct tonnes do not conserve payload ROM WMT.")
        try:
            delivered = awst(row.get("delivered_datetime")).isoformat()
        except (ValueError, TypeError, OverflowError):
            delivered = ""
        row.update(payload_id=identity, delivered_datetime=delivered, source=clean_text(row.get("source")),
                   route_only_waste=MaterialDestinationPlan._truthy(row.get("route_only_waste", False)))
        return row

    def _event(self, base, event, entry, *, before=None, after=None, consumed=0.0, overrun=0.0, next_entry=None, reason=""):
        self.ledger.append(dict(**self.owner, event_number=len(self.ledger)+1, payload_id=base["payload_id"],
                                source=base["source"], delivered_datetime=base["delivered_datetime"],
                                rom_area=base["rom_area"], material_type=base["material_type"], event=event,
                                instance_id=entry["instance_id"], destination=entry["destination"],
                                build_instance=entry["build_instance"], order_position=entry["order_position"],
                                capacity_before_wmt=before, capacity_after_wmt=after, consumed_wmt=consumed,
                                overrun_wmt=overrun, next_instance_id=next_entry["instance_id"] if next_entry else "",
                                next_destination=next_entry["destination"] if next_entry else "", reason=reason,
                                context_signature=self.context_signature))

    def _advance(self, lane, base, reason):
        index = self.positions[lane]
        sequence = self.lanes[lane]["sequence"]
        entry = sequence[index]
        self.positions[lane] += 1
        following = sequence[index+1] if index+1 < len(sequence) else None
        capacity = self.instances[entry["instance_id"]]["remaining_wmt"]
        self._event(base, "Advance", entry, before=capacity, after=capacity, next_entry=following, reason=reason)

    def allocate(self, payloads):
        """Allocate one chronological batch; identical retries do not consume twice."""
        batch = [self._payload(r) for r in payloads]
        if len({r["payload_id"] for r in batch}) != len(batch):
            raise ValueError("Final payload IDs must be unique within an allocation batch.")
        batch.sort(key=lambda r: (r["delivered_datetime"] or "9999", r["payload_id"]))
        # Validate the complete batch before changing any balances.
        for row in batch:
            previous = self.seen.get(row["payload_id"])
            if previous and previous[0] != digest(row):
                raise ValueError("A previously allocated payload changed. Recalculate this plan from its starting capacity.")
            key = (row["delivered_datetime"] or "9999", row["payload_id"])
            if not previous and self.last_key is not None and key < self.last_key:
                raise ValueError("Payloads arrived before the allocated history. Recalculate this plan in chronological order.")
        results = []
        for row in batch:
            previous = self.seen.get(row["payload_id"])
            if previous:
                results.append(deepcopy(previous[1]))
                continue
            area = self.areas.get(stockpile_key(row.get("destination")), "")
            material = grade_block_material_type(row["source"])
            lane = (area, material)
            base = dict(**self.owner, payload_id=row["payload_id"], source=row["source"], delivered_datetime=row["delivered_datetime"],
                        payload_wmt=row["payload_wmt"], direct_tipped_wmt=row["direct_tipped_wmt"], non_direct_wmt=row["non_direct_wmt"],
                        rom_area=area, material_type=material, planned_destination=clean_text(row.get("destination")),
                        assigned_destination="", instance_id="", build_instance=None, order_position=None,
                        assigned_wmt=0.0, unresolved_wmt=0.0, out_of_scope_wmt=0.0, outside_window_wmt=0.0, capacity_before_wmt=None,
                        capacity_after_wmt=None, overrun_wmt=0.0, capacity_basis="", selection_basis="",
                        status="Unresolved", reason="", context_signature=self.context_signature)
            tonnes = row["non_direct_wmt"]
            if tonnes == 0:
                base.update(status="Direct tip only", reason="No ROM destination capacity consumed.")
            elif row.get("in_final_window") is False:
                base.update(status="Outside finalised window", outside_window_wmt=tonnes,
                            reason="Delivery is outside the finalised plan window; no capacity consumed.")
            elif row.get("route_only_waste"):
                base.update(status="Outside ROM scope", out_of_scope_wmt=tonnes, reason="Waste retains its existing route.")
            elif not row["delivered_datetime"]:
                base["reason"] = "Missing or invalid payload delivery time."
            elif lane not in self.lanes:
                base["reason"] = "No 2WP build order for the payload's ROM area/material type."
            elif self.positions[lane] is None:
                base["reason"] = "Current build instance is unconfirmed; review Destination Reconciliation."
            else:
                sequence = self.lanes[lane]["sequence"]
                base["selection_basis"] = self.lanes[lane]["selection_basis"]
                while self.positions[lane] < len(sequence):
                    entry = sequence[self.positions[lane]]
                    capacity = self.instances[entry["instance_id"]]
                    before = capacity["remaining_wmt"]
                    if before is None:
                        base.update(instance_id=entry["instance_id"], build_instance=entry["build_instance"],
                                    order_position=entry["order_position"], capacity_basis=capacity["capacity_basis"],
                                    reason="Remaining ROM WMT is not set for the current build instance.")
                        break
                    if before == 0:
                        self._advance(lane, base, "Build instance has no remaining capacity.")
                        continue
                    after, overrun = max(0.0, before-tonnes), max(0.0, tonnes-before)
                    capacity["remaining_wmt"] = after
                    capacity["consumed_wmt"] += tonnes
                    capacity["overrun_wmt"] += overrun
                    base.update(assigned_destination=entry["destination"], instance_id=entry["instance_id"],
                                build_instance=entry["build_instance"], order_position=entry["order_position"],
                                assigned_wmt=tonnes, capacity_before_wmt=before, capacity_after_wmt=after,
                                overrun_wmt=overrun, capacity_basis=capacity["capacity_basis"], status="Assigned",
                                reason="Whole payload retained; capacity overrun recorded." if overrun else "2WP build order.")
                    self._event(base, "Allocate", entry, before=before, after=after, consumed=tonnes, overrun=overrun, reason=base["reason"])
                    if after == 0:
                        self._advance(lane, base, "Capacity consumed; the next payload advances.")
                    break
                else:
                    base["reason"] = "2WP build order exhausted; no later primary destination is available."
            if base["status"] == "Unresolved":
                base["unresolved_wmt"] = tonnes
            self.assignments.append(base)
            self.seen[row["payload_id"]] = (digest(row), base)
            if row["delivered_datetime"]:
                self.last_key = (row["delivered_datetime"], row["payload_id"])
            results.append(deepcopy(base))
        return results

    def result(self):
        totals = {key: math.fsum(r[key] for r in self.assignments) for key in
                  ("payload_wmt", "direct_tipped_wmt", "non_direct_wmt", "assigned_wmt", "unresolved_wmt", "out_of_scope_wmt", "outside_window_wmt", "overrun_wmt")}
        run = dict(**self.owner, schema_version=VERSION, context_signature=self.context_signature,
                   status="Needs review" if any(r["status"] == "Unresolved" for r in self.assignments) else "Complete",
                   payload_count=len(self.assignments), **totals, reason="")
        capacities = [dict(instance_id=key, destination=item["physical"][0], build_instance=item["physical"][1],
                           rom_area=item["physical"][2], **{k:v for k,v in item.items() if k != "physical"})
                      for key,item in self.instances.items()]
        return deepcopy(dict(run=run, assignments=self.assignments, ledger=self.ledger, capacities=capacities))


def allocate_final_plan(payload_transactions, blend_report, context, *, plan_type="optimised", plan_id="Primary",
                        crusher_destination=None, direct_tip_movement_rules=None):
    """Use the same final direct-tip reconciliation as the existing MDP report."""
    if not context:
        return dict(run=dict(plan_type=clean_text(plan_type).lower(), plan_id=clean_text(plan_id) or "Primary", schema_version=VERSION, status="Unavailable",
                             reason="Load or refresh Destination Reconciliation for this scenario before recalculating the plan."),
                    assignments=[], ledger=[], capacities=[])
    settings = progress_settings(context["settings"])
    if settings["context_signature"] != context["context_signature"]:
        raise ValueError("Destination Reconciliation settings do not match the scenario/source context.")
    allocator = PrimaryDestinationAllocator(context["order"], context.get("activity") or {}, settings, plan_type=plan_type, plan_id=plan_id)
    payloads = MaterialDestinationPlan._prepared_payloads(payload_transactions)
    report = MaterialDestinationPlan._frame(blend_report)
    start = awst(context["start"]).isoformat() if context.get("start") else None
    ends = []
    for value in report.get("end_datetime", []):
        try:
            ends.append(awst(value).isoformat())
        except (ValueError, TypeError, OverflowError):
            pass
    # A partial/failed run finalises only its solved interval. Future candidate
    # tonnes must not silently become final non-direct-tip assignments.
    end = max(ends) if ends else start if "end_datetime" in report.columns else None
    if start and end and end < start:
        raise ValueError("The finalised plan window ends before the scenario start.")
    detail = MaterialDestinationPlan.build_payload_assignments(
        payloads, blend_report, plan_type, plan_id, crusher_destination, direct_tip_movement_rules)
    direct = defaultdict(float)
    for row in detail.to_dict("records"):
        if row["assigned_destination_type"] == "Crusher":
            direct[row["payload_id"]] += row["assigned_tonnes"]
    final = []
    for row in payloads.to_dict("records"):
        identity = row["direct_tip_id"]
        planned, fallback, _ = MaterialDestinationPlan._destination_columns(row)
        destinations = [row.get("destination"), fallback, planned]
        anchor = next((d for d in destinations if stockpile_key(d) in allocator.areas), planned or fallback)
        try:
            delivered = awst(row.get("delivered_datetime")).isoformat()
        except (ValueError, TypeError, OverflowError):
            delivered = ""
        final.append(dict(payload_id=identity, source=row.get("source"), delivered_datetime=row.get("delivered_datetime"),
                          destination=anchor, payload_wmt=row["payload"], direct_tipped_wmt=direct[identity],
                          non_direct_wmt=max(0.0, row["payload"]-direct[identity]),
                          in_final_window=not delivered or ((not start or delivered >= start) and (not end or delivered < end)),
                          route_only_waste=MaterialDestinationPlan._truthy(row.get("route_only_waste", False))))
    allocator.allocate(final)
    result = allocator.result()
    rule_context = context.get("destination_rules") or {}
    rules = DestinationRuleEngine(rule_context.get("guidance"),
                                  areas=rule_context.get("areas", context["order"]["areas"]),
                                  haul_routes=rule_context.get("haul_routes"))
    by_id = {row["payload_id"]: row for row in final}
    for row in result["assignments"]:
        payload = by_id[row["payload_id"]]
        decision = rules.resolve(row["source"], row["delivered_datetime"],
                                 primary_destination=row["assigned_destination"], rom_area=row["rom_area"],
                                 anchor_destination=row["planned_destination"],
                                 route_only_waste=payload["route_only_waste"])
        row.update(rules.metadata(decision), primary_rule=decision["primary_rule"],
                   alternate_destinations=decision["alternate_destinations"], destination_rule_signature=rules.signature)
    result["run"]["destination_rule_signature"] = rules.signature
    result["run"].update(input_signature=digest(final), scenario_context_signature=context["context_signature"],
                         allocation_window_start=start, allocation_window_end=end)
    return result


def write_allocation_audit(result, database_path):
    """Atomically replace this plan's audit without consuming another plan's state."""
    owner = result["run"]
    tables = {
        "destination_allocation_runs": [result["run"]],
        "destination_primary_assignments": result["assignments"],
        "destination_capacity_ledger": result["ledger"],
        "destination_capacity_balances": [dict(plan_type=owner["plan_type"], plan_id=owner["plan_id"], **row) for row in result["capacities"]],
    }
    with closing(sqlite3.connect(database_path)) as connection, connection:
        for name, rows in tables.items():
            connection.execute(f'CREATE TABLE IF NOT EXISTS "{name}" (plan_type TEXT, plan_id TEXT)')
            existing = {r[1] for r in connection.execute(f'PRAGMA table_info("{name}")')}
            columns = AUDIT_COLUMNS[name]
            for column in columns:
                if column not in existing:
                    # Column names originate from the fixed allocator output.
                    kind = "REAL" if column.endswith("_wmt") else "INTEGER" if column in {"event_number", "build_instance", "order_position", "payload_count", "schema_version"} else "TEXT"
                    connection.execute(f'ALTER TABLE "{name}" ADD COLUMN "{column}" {kind}')
            connection.execute(f'DELETE FROM "{name}" WHERE plan_type = ? AND plan_id = ?', (owner["plan_type"], owner["plan_id"]))
            if rows:
                names = ", ".join(f'"{c}"' for c in columns)
                connection.executemany(f'INSERT INTO "{name}" ({names}) VALUES ({", ".join("?" for _ in columns)})',
                                       [[json.dumps(row.get(c)) if isinstance(row.get(c), (dict, list)) else row.get(c) for c in columns] for row in rows])
