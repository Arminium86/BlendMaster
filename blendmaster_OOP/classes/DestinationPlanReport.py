"""Publish final destination decisions and their frozen evidence in one snapshot."""

from collections import defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pandas as pd

from classes.MaterialDestinationPlan import MaterialDestinationPlan as MDP
from classes.GradeBlockIdentity import parent_grade_block_name
from classes.DestinationProgress import resolve_progress
from classes.PrimaryDestinationAllocator import AUDIT_COLUMNS, write_allocation_audit_to_connection
from setup.ProductAssayHistory import awst


VERSION = 1
EXTRA_COLUMNS = "report_version status reason rom_area material_type primary_destination fallback_1_destination fallback_2_destination fallback_1_rule fallback_2_rule instance_id build_instance order_position first_delivery last_delivery payload_count reported_wmt unresolved_wmt out_of_scope_wmt outside_window_wmt starting_capacity_wmt consumed_capacity_wmt capacity_before_wmt capacity_after_wmt overrun_wmt capacity_basis selection_basis detected_destination latest_inbound activity_wmt activity_rows activity_status activity_fetched_at transition_count transitions destination_rule_trace context_signature destination_rule_signature".split()
SUMMARY_COLUMNS = MDP.COLUMNS + EXTRA_COLUMNS
DETAIL_COLUMNS = SUMMARY_COLUMNS + "payload_id original_grade_block delivered_datetime payload_wmt".split()
ACTIVITY_COLUMNS = "plan_type plan_id movement_id observed_at source_block destination destination_build rom_area material_type wmt".split()
PUBLICATION_COLUMNS = {
    "material_destination_plan": SUMMARY_COLUMNS,
    "material_destination_plan_payloads": DETAIL_COLUMNS,
    "material_destination_plan_activity": ACTIVITY_COLUMNS,
}
def build_publication(payloads, blend_report, allocation, context, *, plan_type, plan_id="Primary",
                      crusher_destination=None, direct_tip_movement_rules=None):
    """One row per real assignment/bucket. Fallbacks never add assigned tonnes."""
    raw = MDP.build_payload_assignments(payloads, blend_report, plan_type, plan_id,
                                        crusher_destination, direct_tip_movement_rules)
    assigned = {r["payload_id"]: r for r in allocation["assignments"]}
    capacities = {r["instance_id"]: r for r in allocation["capacities"]}
    events = defaultdict(list)
    for event in allocation["ledger"]:
        if event["event"] == "Advance":
            events[event["payload_id"]].append({k: event.get(k) for k in (
                "event_number", "instance_id", "destination", "next_instance_id", "next_destination", "reason")})
    activity = (context or {}).get("activity") or {}
    progress = {}
    if context:
        progress = {(r["rom_area"], r["material_type"]): r for r in resolve_progress(
            context["order"], activity, context["settings"].get("selected_instances"))}
    allocation["run"].update(report_version=VERSION, activity_status=activity.get("status", "Unavailable"),
                              activity_fetched_at=activity.get("fetched_at"),
                              activity_window_start=activity.get("request", {}).get("start"),
                              activity_window_end=activity.get("request", {}).get("end"))
    rows = []
    for original in raw.to_dict("records"):
        identity = original["payload_id"]
        final = assigned.get(identity, {})
        try:
            original["delivered_datetime"] = awst(original.get("delivered_datetime")).isoformat()
        except (ValueError, TypeError, OverflowError):
            original["delivered_datetime"] = None
        direct = original["assigned_destination_type"] == "Crusher"
        quantity = float(original["assigned_tonnes"])
        lane = progress.get((final.get("rom_area"), final.get("material_type")), {})
        row = {**original, "report_version": VERSION, "original_grade_block": original["grade_block"],
               "grade_block": parent_grade_block_name(original["grade_block"]), "payload_wmt": original["payload_tonnes"],
               "first_delivery": original.get("delivered_datetime"), "last_delivery": original.get("delivered_datetime"),
               "payload_count": 1, "reported_wmt": quantity, "consumed_capacity_wmt": 0.0,
               "unresolved_wmt": 0.0, "out_of_scope_wmt": 0.0, "outside_window_wmt": 0.0, "overrun_wmt": 0.0,
               "status": "Assigned" if direct else final.get("status", "Unavailable"),
               "reason": "Direct tip; no ROM destination capacity consumed." if direct else final.get("reason", allocation["run"].get("reason", "")),
               "activity_status": activity.get("status", "Unavailable"), "activity_fetched_at": activity.get("fetched_at"),
               "detected_destination": lane.get("detected_destination"), "latest_inbound": lane.get("latest_inbound"),
               "activity_wmt": lane.get("activity_wmt", 0), "activity_rows": len(lane.get("evidence", [])),
               "selection_basis": final.get("selection_basis") or lane.get("selection_basis", ""),
               "transitions": "[]", "transition_count": 0}
        for key in ("rom_area", "material_type", "primary_destination", "fallback_1_destination", "fallback_2_destination",
                    "fallback_1_rule", "fallback_2_rule", "destination_rule_trace", "context_signature", "destination_rule_signature"):
            row[key] = final.get(key, "")
        alternatives = final.get("alternate_destinations", []) or []
        row["alternate_destination_1"], row["alternate_destination_2"] = (list(alternatives) + ["", ""])[:2]
        if not direct:
            for key in ("instance_id", "build_instance", "order_position", "capacity_before_wmt", "capacity_after_wmt", "capacity_basis"):
                row[key] = final.get(key)
            row["starting_capacity_wmt"] = capacities.get(final.get("instance_id"), {}).get("starting_wmt")
            row["transitions"] = json.dumps(events[identity], sort_keys=True)
            row["transition_count"] = len(events[identity])
            row["assigned_destination"] = final.get("assigned_destination", "")
            row["assigned_tonnes"] = final.get("assigned_wmt", 0.0)
            row["consumed_capacity_wmt"] = row["assigned_tonnes"]
            row["assignment_source"] = "2WP build order" if row["assigned_tonnes"] else row["status"]
            for key in ("unresolved_wmt", "out_of_scope_wmt", "outside_window_wmt", "overrun_wmt"):
                row[key] = final.get(key, quantity if key == "unresolved_wmt" else 0.0)
            if row["out_of_scope_wmt"]:
                row["assigned_destination"] = original["assigned_destination"]
                row["assigned_destination_type"] = "Waste route"
        rows.append(row)
    details = pd.DataFrame(rows).reindex(columns=DETAIL_COLUMNS)
    summaries = []
    if not details.empty:
        details["source_tonnes"] = details.groupby(["plan_type", "plan_id", "grade_block"])["reported_wmt"].transform("sum")
        details["assigned_ratio"] = (details["assigned_tonnes"] / details["source_tonnes"].replace(0, float("nan"))).fillna(0)
        group_keys = "plan_type plan_id grade_block assigned_destination assigned_destination_type status primary_destination fallback_1_destination fallback_2_destination fallback_1_rule fallback_2_rule instance_id order_position two_wp_destination_resolution planned_2wp_destination".split()
        additive = "assigned_tonnes reported_wmt consumed_capacity_wmt unresolved_wmt out_of_scope_wmt outside_window_wmt overrun_wmt transition_count".split()
        for _, group in details.groupby(group_keys, dropna=False, sort=False):
            group = group.sort_values(["delivered_datetime", "payload_id"], kind="stable")
            item = group.iloc[0].to_dict()
            for key in additive:
                item[key] = group[key].sum()
            item.update(payload_count=group["payload_id"].nunique(), first_delivery=group.iloc[0]["delivered_datetime"],
                        last_delivery=group.iloc[-1]["delivered_datetime"], capacity_after_wmt=group.iloc[-1]["capacity_after_wmt"],
                        assigned_ratio=item["assigned_tonnes"] / item["source_tonnes"] if item["source_tonnes"] else 0,
                        reason="; ".join(dict.fromkeys(group["reason"].dropna())),
                        transitions=json.dumps([e for v in group["transitions"] for e in json.loads(v)], sort_keys=True))
            summaries.append(item)
    owner = {"plan_type": plan_type, "plan_id": plan_id}
    return {"material_destination_plan": pd.DataFrame(summaries).reindex(columns=SUMMARY_COLUMNS),
            "material_destination_plan_payloads": details,
            "material_destination_plan_activity": pd.DataFrame([dict(r, **owner) for r in activity.get("records", [])]).reindex(columns=ACTIVITY_COLUMNS)}


def ensure_schema(connection):
    """Add columns in place; existing plans remain labelled legacy until recalculated."""
    for name, columns in PUBLICATION_COLUMNS.items():
        connection.execute(f'CREATE TABLE IF NOT EXISTS "{name}" (plan_type TEXT, plan_id TEXT)')
        existing = {r[1] for r in connection.execute(f'PRAGMA table_info("{name}")')}
        for column in columns:
            if column not in existing:
                numeric = column.endswith(("_wmt", "_tonnes", "_ratio")) or column == "wmt"
                integer = column in {"report_version", "build_instance", "order_position", "payload_count", "activity_rows", "transition_count"}
                kind = "REAL" if numeric else "INTEGER" if integer else "TEXT"
                connection.execute(f'ALTER TABLE "{name}" ADD COLUMN "{column}" {kind}')
        if "status" in columns:
            connection.execute(f'UPDATE "{name}" SET status = ? WHERE report_version IS NULL', ("Legacy snapshot — recalculate",))


def scalar(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp) or hasattr(value, "isoformat"):
        return value.isoformat()
    return value.item() if hasattr(value, "item") else value


def write_publication(publication, database_path, owner, allocation=None):
    """Commit summary, payloads, actual evidence and capacity audit together."""
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("BEGIN")
        ensure_schema(connection)
        if allocation is not None:
            write_allocation_audit_to_connection(allocation, connection)
        for name, columns in PUBLICATION_COLUMNS.items():
            connection.execute(f'DELETE FROM "{name}" WHERE lower(plan_type)=? AND plan_id=?',
                               (owner["plan_type"].lower(), owner["plan_id"]))
            frame = publication.get(name, pd.DataFrame()).reindex(columns=columns)
            values = [[scalar(v) for v in row] for row in frame.itertuples(index=False, name=None)]
            if values:
                fields = ",".join(f'"{c}"' for c in columns)
                connection.executemany(f'INSERT INTO "{name}" ({fields}) VALUES ({",".join("?" for _ in columns)})', values)


def read_publication(database_path):
    """Read a consistent saved result without recalculating from current settings."""
    if not Path(database_path).is_file():
        return {}
    with closing(sqlite3.connect(Path(database_path).resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("BEGIN")
        names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result = {}
        for name in [*PUBLICATION_COLUMNS, "destination_allocation_runs", "destination_capacity_balances", "destination_capacity_ledger"]:
            if name in names:
                frame = pd.read_sql_query(f'SELECT * FROM "{name}"', connection)
                result[name] = [{k: scalar(v) for k, v in row.items()} for row in frame.to_dict("records")]
        return result
