"""Deterministic destination candidates, separate from physical build capacity."""

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
import re

import pandas as pd

from classes.GradeBlockIdentity import parent_grade_block_name, grade_block_material_type


VERSION = 2
LEVELS = (
    ("Pit + stage + bench + flitch + material", (0, 1, 2, 4, 5)),
    ("Pit + stage + bench + material", (0, 1, 2, 5)),
    ("Pit + stage + material", (0, 1, 5)),
    ("Pit + material", (0, 5)),
    ("Pit + stage + bench + flitch + any non-waste material", (0, 1, 2, 4)),
    ("Pit area + material", (6, 5)),
)
METADATA_COLUMNS = (
    "primary_destination", "fallback_1_destination", "fallback_2_destination",
    "fallback_1_rule", "fallback_2_rule", "destination_rule_trace",
)


def text(value):
    return "" if value is None or (not isinstance(value, (dict, list)) and pd.isna(value)) else str(value).strip()


def stockpile(value):
    name = text(value).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r":(?:IN|OUT)$", "", name.upper())


def source_key(value):
    return parent_grade_block_name(text(value).replace("|", "/")).upper()


def address(value):
    """Full APS address or site-scoped warehouse identity; never invent levels."""
    parts = source_key(value).split("/")
    mine = ""
    if len(parts) == 8 and parts[0] in {"RESERVE", "RESERVES"}:
        mine, parts = parts[1], parts[2:]
    elif len(parts) != 6:
        return None
    if not all(parts) or not re.match(r"^[A-Z]+", parts[-1]):
        return None
    parts[1:5] = [str(int(p)) if p.isdigit() else p for p in parts[1:5]]
    return mine, tuple(parts[:-1] + [grade_block_material_type(parts[-1])])


def history_address(value):
    location = address(value)
    if location is None:
        return None
    mine, parts = location
    # YOU80, YOU02 and YOU13 share the YOU pit area. Keep the full pit
    # identifier in the existing, more specific levels.
    pit_area = re.sub(r"\d+$", "", parts[0])
    return mine, (*parts, pit_area)


def timestamp(value):
    try:
        result = pd.to_datetime(value, dayfirst="/" in text(value), errors="coerce")
        if pd.isna(result):
            return None
        return result.tz_localize("Australia/Perth") if result.tzinfo is None else result.tz_convert("Australia/Perth")
    except (ValueError, TypeError, OverflowError):
        return None


def positive(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else 0.0
    except (ValueError, TypeError):
        return 0.0


class DestinationRuleEngine:
    """Index the complete 2WP once; cache repeated payload/source decisions.

    Fallback 1 ranks spatial level, then total 2WP ROM WMT, then name.
    Fallback 2 requires a measured stockpile cycle and a matching Nearest Crusher.
    The last resort uses the latest dated non-waste 2WP movement in the same mine.
    Neither role establishes or consumes a physical build's remaining capacity.
    """

    def __init__(self, guidance=None, *, areas=None, haul_routes=None):
        self.guidance = guidance or {}
        self.areas = {stockpile(k): text(v).upper() for k, v in (areas or {}).items()}
        self.routes = {stockpile(k): v for k, v in (haul_routes or {}).items()}
        self.exact = defaultdict(list)
        self.index = [defaultdict(dict) for _ in LEVELS]
        self.latest = {}
        self.cache = {}
        self.signature = hashlib.sha256(json.dumps(
            [VERSION, self.guidance, self.areas, self.routes], sort_keys=True, default=str).encode()).hexdigest()
        for key, allocations in (self.guidance.get("source_destinations") or {}).items():
            for raw in allocations:
                source = text(raw.get("source")) or key
                dest = stockpile(raw.get("destination"))
                tonnes = positive(raw.get("two_wp_tonnes"))
                material = grade_block_material_type(source)
                if (not dest or raw.get("route_only_waste") is True or material in {"WS", "WASTE"}
                        or "waste" in text(raw.get("ore_type")).lower()):
                    continue
                item = dict(raw, source=source, destination="Stockpiles/" + dest)
                self.exact[source_key(key)].append(item)
                location = history_address(source)
                if not location or not tonnes:
                    continue
                mine, parts = location
                start = timestamp(raw.get("guidance_datetime"))
                end = timestamp(raw.get("guidance_end_datetime"))
                used_at = end if end is not None else start
                if mine and used_at is not None and (not self.areas or self.areas.get(dest)):
                    # The complete guidance horizon is authoritative. End time,
                    # then start time and file order identify its latest use.
                    rank = (-used_at.value, -start.value if start is not None else math.inf,
                            -positive(raw.get("row_order")), dest, source)
                    if mine not in self.latest or rank < self.latest[mine][0]:
                        self.latest[mine] = (rank, dict(
                            destination=item["destination"], rule="Latest 2WP destination within the same mine",
                            mine=mine, evidence_source=source, history_wmt=tonnes, history_rows=1,
                            guidance_datetime=start.isoformat() if start is not None else "",
                            guidance_end_datetime=end.isoformat() if end is not None else "",
                            latest_use_datetime=used_at.isoformat(), row_order=raw.get("row_order", 0)))
                for index, (_, axes) in zip(self.index, LEVELS):
                    values = tuple(parts[i] for i in axes)
                    if not all(values):
                        continue
                    bucket = index[(mine, *values)]
                    record = bucket.setdefault(dest, dict(destination="Stockpiles/" + dest, history_wmt=0.0,
                                                          history_rows=0, evidence_source=source))
                    record["history_wmt"] += tonnes
                    record["history_rows"] += 1
                    record["evidence_source"] = min(record["evidence_source"], source)

    def exact_allocation(self, source, when):
        choices = self.exact.get(source_key(source), [])
        if not choices:
            return None
        day = timestamp(when)
        def rank(row):
            date = timestamp(row.get("guidance_datetime"))
            distance = abs((date.date() - day.date()).days) if date is not None and day is not None else math.inf
            return distance, -positive(row.get("two_wp_tonnes")), row.get("row_order", 0), row["destination"]
        return min(choices, key=rank)

    def _history(self, source):
        location = history_address(source)
        if not location:
            return []
        mine, parts = location
        seen, results = set(), []
        for level, (index, (label, axes)) in enumerate(zip(self.index, LEVELS), 1):
            entries = index.get((mine, *(parts[i] for i in axes)), {})
            for dest, record in sorted(entries.items(), key=lambda pair: (-pair[1]["history_wmt"], pair[0])):
                if dest in seen or (self.areas and not self.areas.get(dest)):
                    continue
                seen.add(dest)
                results.append(dict(record, rule=label, level=level))
        return results

    def resolve(self, source, when=None, *, primary_destination=None, rom_area="", route_only_waste=False, anchor_destination=""):
        """None selects exact 2WP guidance; an explicit primary comes from Task 23."""
        day = timestamp(when)
        key = (source_key(source), day.date().isoformat() if day is not None else "", primary_destination, text(rom_area), bool(route_only_waste), stockpile(anchor_destination))
        if key in self.cache:
            return deepcopy(self.cache[key])
        exact = self.exact_allocation(source, when)
        primary = stockpile(primary_destination) if primary_destination is not None else stockpile((exact or {}).get("destination"))
        result = dict(schema_version=VERSION, primary_destination="Stockpiles/" + primary if primary else "",
                      primary_rule="2WP build order" if primary_destination is not None and primary else "Exact 2WP grade block" if primary else "",
                      fallback_1_destination="", fallback_2_destination="", fallback_1_rule="", fallback_2_rule="",
                      last_resort_destination="", last_resort_rule="",
                      selected_destination="", resolution="unresolved", alternate_destinations=[], candidates=[], reason="")
        if route_only_waste or grade_block_material_type(source) in {"WS", "WASTE"}:
            result.update(primary_destination="", primary_rule="", reason="Waste is outside ROM destination rules.")
            return result
        history = [r for r in self._history(source) if stockpile(r["destination"]) != primary]
        first = history[0] if history else None
        if first:
            result.update(fallback_1_destination=first["destination"], fallback_1_rule=first["rule"])
        # The resolved 2WP destination establishes the ROM area before the
        # original 24HR destination (which guidance may have replaced).
        origin = primary or stockpile((first or {}).get("destination")) or stockpile(anchor_destination)
        area = self.areas.get(origin, "")
        result.update(nearby_origin="Stockpiles/" + origin if origin else "", rom_area=area)
        excluded = {primary, stockpile((first or {}).get("destination")), origin}
        nearby = {}
        for route in self.routes.get(origin, []):
            dest = stockpile(route.get("destination"))
            cycle = positive(route.get("cycle_time_minutes"))
            if not area or not cycle or not dest or self.areas.get(dest) != area or dest in excluded:
                continue
            candidate = dict(destination="Stockpiles/" + dest, rule="Shortest stockpile-to-stockpile haul cycle",
                             cycle_time_minutes=cycle, source_node=route.get("source_node"), destination_node=route.get("destination_node"))
            if dest not in nearby or cycle < nearby[dest]["cycle_time_minutes"]:
                nearby[dest] = candidate
        nearby = sorted(nearby.values(), key=lambda r: (r["cycle_time_minutes"], r["destination"]))
        if nearby:
            result.update(fallback_2_destination=nearby[0]["destination"], fallback_2_rule=nearby[0]["rule"])
        selected = result["primary_destination"] or result["fallback_1_destination"] or result["fallback_2_destination"]
        candidates = ([first] if first else []) + nearby[:1] + history[1:] + nearby[1:]
        latest = None
        if not selected:
            location = address(source)
            latest = self.latest.get(location[0], (None, None))[1] if location else None
            if latest:
                selected = latest["destination"]
                candidates.append(latest)
                result.update(last_resort_destination=selected, last_resort_rule=latest["rule"])
        seen = {primary}
        for candidate in candidates:
            dest = stockpile(candidate["destination"])
            if dest in seen:
                continue
            seen.add(dest)
            result["candidates"].append(candidate)
        # Payloads repeat the same source decision many times. Retain evidence
        # for the selected roles and the two published alternates, rather than
        # duplicating every haul route into every payload/database record.
        result["candidate_count"] = len(result["candidates"])
        result["alternate_destinations"] = [r["destination"] for r in result["candidates"] if r["destination"] != selected][:2]
        reported = {selected, *result["alternate_destinations"]}
        result["candidates"] = [r for r in result["candidates"] if r["destination"] in reported]
        result.update(selected_destination=selected,
                      resolution=("primary_build_order" if primary_destination is not None else "exact_2wp") if primary else "spatial_fallback" if first else "nearby_fallback" if nearby else "last_destination_fallback" if latest else "unresolved",
                      reason="" if nearby else "No distinct eligible stockpile-to-stockpile haul route from a mapped origin in the ROM area.")
        if not selected:
            result["reason"] = "No exact 2WP destination, matching spatial history, eligible stockpile-to-stockpile haul route or dated destination history within the same mine."
        self.cache[key] = result
        return deepcopy(result)

    @staticmethod
    def metadata(result):
        return {**{k: result[k] for k in METADATA_COLUMNS if k != "destination_rule_trace"},
                "destination_rule_trace": json.dumps(result, sort_keys=True)}
