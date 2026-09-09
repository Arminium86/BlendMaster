"""Auditable 2WP ROM build order. This module does not allocate payloads."""

from collections import defaultdict
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path

import pandas as pd

from classes.ExpitDataHandler import ExpitDataHandler
from classes.GradeBlockIdentity import grade_block_material_type
from setup.InventoryBuildLineage import canonical_block, clean_text, finite_number


VERSION = 1
stockpile_key = ExpitDataHandler.destination_guidance_stockpile_key


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def inventory_areas(inventories):
    """Use the displayed Nearest Crusher; conflicting aliases stay unresolved."""
    candidates = defaultdict(set)
    for name, row in (inventories or {}).items():
        area = clean_text(row.get("nearest_crusher", row.get("NEAREST_CRUSHER"))).upper()
        for alias in (name, row.get("name"), row.get("NAME"), row.get("stockpile_name")):
            if clean_text(alias):
                candidates[stockpile_key(alias)].add(area)
    return {key: next(iter(areas)) if len(areas) == 1 else "" for key, areas in candidates.items()}


def extract_build_order(path, inventories):
    """Read one immutable file snapshot; fingerprints include bytes and ROM mapping."""
    content = Path(path).read_bytes()
    frame = pd.read_csv(io.BytesIO(content))
    return build_order(frame, inventory_areas(inventories),
                       source_signature=hashlib.sha256(content).hexdigest(), source_file=Path(path).name)


def build_order(frame, areas, *, source_signature=None, source_file="Mining.csv"):
    required = {"Source.Type", "Source.FullName", "Destination.Type", "Time.StartTime", "Time.EndTime", "Mining.wetTonnes"}
    missing = required - set(frame.columns)
    if not ({"Destination.Name", "Destination.FullName"} & set(frame.columns)):
        missing.add("Destination.Name")
    if missing:
        raise ValueError("2WP Mining.csv is missing build-order columns: " + ", ".join(sorted(missing)))
    areas = {stockpile_key(k): clean_text(v).upper() for k, v in areas.items()}
    signature = digest(dict(version=VERSION, source=source_signature or frame.to_json(orient="split"), areas=areas))
    starts = ExpitDataHandler._parse_datetime_column(frame["Time.StartTime"], "Time.StartTime")
    ends = ExpitDataHandler._parse_datetime_column(frame["Time.EndTime"], "Time.EndTime")
    audit, inbound, reclaim, warnings = [], [], defaultdict(list), []
    for number, ((_, row), start, end) in enumerate(zip(frame.iterrows(), starts, ends), 2):
        source = clean_text(row.get("Source.FullName"))
        destination = stockpile_key(clean_text(row.get("Destination.Name")) or clean_text(row.get("Destination.FullName")))
        source_type, destination_type = clean_text(row.get("Source.Type")).lower(), clean_text(row.get("Destination.Type")).lower()
        tonnes = finite_number(row.get("Mining.wetTonnes"))
        record = dict(source_file=source_file, csv_record=number, source=source, destination=destination,
                      start=start.isoformat() if pd.notna(start) else "", end=end.isoformat() if pd.notna(end) else "",
                      planned_wmt=tonnes, rom_area=areas.get(destination, ""), material_type="",
                      instance_id="", build_instance=None, order_position=None, outcome="excluded", reason="Not a ROM inbound or reclaim row")
        audit.append(record)
        valid = pd.notna(start) and pd.notna(end) and end > start and tonnes is not None and tonnes > 0
        is_reclaim = destination_type == "crusher" and (source_type == "stockpile" or
                     (source_type == "flow" and clean_text(row.get("Agent.Name")).lower() == "plantagent"))
        if is_reclaim:
            name = stockpile_key(source if source_type == "stockpile" else clean_text(row.get("OriginalSource.Name")))
            if valid and name:
                reclaim[name].append((start, end, record))
                record.update(outcome="reclaim", reason="Reclaim evidence", destination=name)
            else:
                record["reason"] = "Invalid reclaim time, tonnes or stockpile identity"
            continue
        if source_type != "reserve" or destination_type != "stockpile":
            continue
        material = grade_block_material_type(source)
        record["material_type"] = material
        reason = ("Invalid inbound time or tonnes" if not valid else
                  "Missing source or destination" if not source or not destination else
                  "Missing or conflicting Nearest Crusher" if not record["rom_area"] else
                  "Missing material type" if not material else "")
        if reason:
            record["reason"] = reason
            warnings.append(f"CSV record {number}: {reason}.")
            continue
        record.update(outcome="included", reason="ROM inbound")
        inbound.append((start, end, record))
    by_stockpile = defaultdict(list)
    for event in inbound:
        by_stockpile[event[2]["destination"]].append(event)
    for destination, events in by_stockpile.items():
        last_end, instance, instance_id = None, 0, ""
        windows = sorted(reclaim[destination], key=lambda r: (r[0], r[1], r[2]["csv_record"]))
        for start, end, record in sorted(events, key=lambda r: (r[0], r[1], r[2]["csv_record"])):
            transitions = [(s, e, a) for s, e, a in windows if last_end is not None and s >= last_end and e <= start]
            if last_end is None or transitions:
                instance += 1
                instance_id = digest([destination, start.isoformat(), instance])[:24]
            record.update(build_instance=instance, instance_id=instance_id)
            if transitions:
                record["reason"] = "Build resumed after reclaim; new build instance"
            if any(s < end and e > start for s, e, _ in windows):
                warnings.append(f"{destination}, CSV record {record['csv_record']}: build and reclaim overlap; no turnover inferred from the overlap.")
            last_end = max(last_end, end) if last_end is not None else end
    grouped = {}
    for _, _, record in sorted(inbound, key=lambda r: (r[0], r[2]["csv_record"])):
        key = (record["rom_area"], record["material_type"], record["instance_id"])
        if key not in grouped:
            grouped[key] = {k: record[k] for k in ("rom_area", "material_type", "destination", "instance_id", "build_instance")}
            grouped[key].update(first_inbound=record["start"], last_inbound=record["end"], planned_wmt=0., csv_records=[], source_blocks=[])
        item = grouped[key]
        item["last_inbound"] = max(item["last_inbound"], record["end"])
        item["planned_wmt"] += record["planned_wmt"]
        item["csv_records"].append(record["csv_record"])
        block = canonical_block(record["source"])
        if block and block not in item["source_blocks"]:
            item["source_blocks"].append(block)
    counters = defaultdict(int)
    orders = sorted(grouped.values(), key=lambda r: (r["rom_area"], r["material_type"], r["first_inbound"], r["csv_records"][0]))
    previous = {}
    for item in orders:
        lane = (item["rom_area"], item["material_type"])
        counters[lane] += 1
        item["order_position"] = counters[lane]
        if previous.get(lane) == item["first_inbound"]:
            warnings.append(f"{lane[0]} / {lane[1]}: simultaneous planned destinations; CSV record order breaks the tie.")
        previous[lane] = item["first_inbound"]
        for number in item["csv_records"]:
            audit[number - 2]["order_position"] = item["order_position"]
    return dict(schema_version=VERSION, signature=signature, source_file=source_file,
                orders=orders, audit=audit, warnings=list(dict.fromkeys(warnings)), areas=areas)


def write_order_audit(snapshot, database_path):
    """Publish readable tables to the active scenario's SQLite report database."""
    import sqlite3
    with closing(sqlite3.connect(database_path)) as connection:
        for name, rows, columns in (
            ("destination_build_order", snapshot["orders"], ["rom_area", "material_type", "destination", "instance_id", "order_position"]),
            ("destination_build_order_audit", snapshot["audit"], ["source_file", "csv_record", "source", "destination", "outcome", "reason"]),
        ):
            records = [{k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in row.items()} for row in rows]
            frame = pd.DataFrame(records) if records else pd.DataFrame(columns=columns)
            frame["source_signature"] = snapshot["signature"]
            frame.to_sql(name, connection, if_exists="replace", index=False)
