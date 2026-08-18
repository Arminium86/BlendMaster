"""Reconcile an APS Expit sequence to actual parent-grade-block progress.

The APS 24HR schedule remains authoritative for sliced source properties and
payload construction.  Snowflake actuals are authoritative only for progress
through parent grade blocks and for the observed route across the mining face.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import math
import re

import pandas as pd

from classes.GradeBlockIdentity import parent_grade_block_name
from classes.CustomConstraints import source_property_kind


AUDIT_COLUMNS = [
    "agent",
    "record_type",
    "parent_grade_block",
    "grade_block_key",
    "material_class",
    "original_sequence",
    "actual_first_sequence",
    "actual_last_sequence",
    "updated_sequence",
    "aps_planned_wmt",
    "actual_schedule_wmt",
    "aps_remaining_wmt",
    "planned_wmt",
    "actual_wmt",
    "remaining_wmt",
    "geological_data_available",
    "nominal_geological_wmt",
    "nominal_geological_dmt",
    "cumulative_actual_wmt",
    "estimated_geological_remaining_wmt",
    "geological_remaining_fraction",
    "geological_completion_status",
    "aps_share_of_nominal_pct",
    "geological_depletion_pct",
    "geological_material",
    "geological_is_ore",
    "geological_record_created_dt",
    "geological_warning",
    "schedule_completion_pct",
    "completion_ratio",
    "completion_tolerance_pct",
    "completion_status",
    "inferred_direction",
    "direction_reversals",
    "geometry_available",
    "centroid_easting",
    "centroid_northing",
    "is_latest_actual_block",
    "reconciliation_confidence",
    "warning",
]


def _normal_number_token(value):
    text = str(value or "").strip().upper()
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        return str(int(float(text)))
    return re.sub(r"[^A-Z0-9]", "", text)


def grade_block_key(value):
    """Return a site-prefix-independent key for one parent grade block."""
    parent = parent_grade_block_name(value)
    if not parent:
        return ""
    text = parent.replace("\\", "/").strip("/").upper()
    if "/" in text:
        parts = [part for part in text.split("/") if part]
        if parts and parts[0] in {"RESERVE", "RESERVES"}:
            parts = parts[1:]
        # APS paths carry the operation immediately after Reserves.  The
        # polygon/SOURCE_FMS identity is the final six operational tokens.
        if len(parts) > 6:
            parts = parts[-6:]
    else:
        parts = [part for part in re.split(r"[_*]+", text) if part]
        # SOURCE may prefix the mine/site abbreviation.  SOURCE_FMS normally
        # does not.  Keeping the final six tokens makes both representations
        # converge without hard-coding every site code.
        if len(parts) > 6:
            parts = parts[-6:]
    return "|".join(_normal_number_token(part) for part in parts)


def polygon_lookup_name(value):
    """Return the grade-block polygon FULL_NAME convention for a source."""
    key = grade_block_key(value)
    parts = key.split("|") if key else []
    if len(parts) != 6:
        return ""
    location, phase, blast_rl, blast_no, flitch_rl, block = parts
    return "_".join([
        location,
        phase.zfill(2) if phase.isdigit() else phase,
        blast_rl.zfill(4) if blast_rl.isdigit() else blast_rl,
        blast_no,
        flitch_rl.zfill(4) if flitch_rl.isdigit() else flitch_rl,
        block,
    ])


def is_route_only_waste(value):
    material = str(value or "").strip().upper()
    return bool(material and "WASTE" in material)


def _finite(value, default=0.0):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _first_text(*values):
    for value in values:
        try:
            if pd.isna(value):
                continue
        except (TypeError, ValueError):
            pass
        text = str(value or "").strip()
        if text and text.lower() not in {"nan", "none", "<na>"}:
            return text
    return ""


def _segment_token(value, fallback):
    number = _finite(value, float("nan"))
    return int(number) if math.isfinite(number) else int(fallback)


def _perth_naive_timestamp(value):
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return pd.NaT
    if pd.isna(timestamp):
        return pd.NaT
    if timestamp.tzinfo is not None:
        return timestamp.tz_convert("Australia/Perth").tz_localize(None)
    return timestamp


def _compressed(values):
    result = []
    for value in values:
        if value and (not result or result[-1] != value):
            result.append(value)
    return result


def _agent_key(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _agent_matches(actual, planned):
    actual_key = _agent_key(actual)
    planned_key = _agent_key(planned)
    if not actual_key or not planned_key:
        return False
    return actual_key == planned_key or actual_key.endswith(planned_key)


def agent_matches(actual, planned):
    """Public wrapper used by the live audit view."""
    return _agent_matches(actual, planned)


@dataclass
class ExpitSequenceResult:
    transactions: pd.DataFrame
    audit: pd.DataFrame
    geometry: pd.DataFrame
    geological_blocks: pd.DataFrame
    actual_movements: pd.DataFrame
    summary: dict
    warnings: list


class ExpitSequenceReconciler:
    """Apply actual parent-block progress to sliced APS payloads."""

    def __init__(
        self, completion_tolerance_pct=10.0, source_property_kinds=None
    ):
        tolerance = _finite(completion_tolerance_pct, 10.0)
        self.tolerance = min(max(tolerance / 100.0, 0.0), 0.99)
        self.source_property_kinds = dict(source_property_kinds or {})

    @staticmethod
    def normalize_actual_movements(actual_movements):
        actual = (
            actual_movements.copy()
            if isinstance(actual_movements, pd.DataFrame)
            else pd.DataFrame()
        )
        if actual.empty:
            return pd.DataFrame(columns=[
                "agent", "source_actual", "source_fms",
                "transaction_datetime", "actual_wmt", "movement_type",
                "parent_grade_block", "grade_block_key",
            ])
        aliases = {
            "LOAD_EQUIPMENT": "agent",
            "AGENT": "agent",
            "SOURCE": "source_actual",
            "SOURCE_ACTUAL": "source_actual",
            "SOURCE_FMS": "source_fms",
            "TRANSACTION_DATETIME": "transaction_datetime",
            "WMT_REPORTING": "actual_wmt",
            "ACTUAL_WMT": "actual_wmt",
            "MOVEMENT_TYPE": "movement_type",
        }
        actual = actual.rename(columns={
            column: aliases.get(str(column).upper(), column)
            for column in actual.columns
        })
        for column in (
            "agent", "source_actual", "source_fms", "transaction_datetime",
            "actual_wmt", "movement_type",
        ):
            if column not in actual:
                actual[column] = "" if column != "actual_wmt" else 0.0
        actual["transaction_datetime"] = actual[
            "transaction_datetime"
        ].map(_perth_naive_timestamp)
        actual["actual_wmt"] = pd.to_numeric(
            actual["actual_wmt"], errors="coerce"
        ).fillna(0.0)
        actual["agent"] = actual["agent"].astype(str).str.strip()
        actual["parent_grade_block"] = actual.apply(
            lambda row: _first_text(
                row.get("source_fms"), row.get("source_actual")
            ),
            axis=1,
        )
        actual["grade_block_key"] = actual["parent_grade_block"].map(
            grade_block_key
        )
        return actual[
            actual["transaction_datetime"].notna()
            & actual["grade_block_key"].ne("")
            & actual["actual_wmt"].gt(0)
        ].sort_values("transaction_datetime").reset_index(drop=True)

    @staticmethod
    def normalize_geometry(geometry):
        frame = geometry.copy() if isinstance(geometry, pd.DataFrame) else pd.DataFrame()
        if frame.empty:
            return pd.DataFrame(columns=[
                "full_name", "grade_block_key", "point", "easting",
                "northing", "elevation", "elevation_name",
                "record_created_dt",
            ])
        aliases = {
            "FULL_NAME": "full_name",
            "GB_NAME": "gb_name",
            "POINT": "point",
            "EASTING": "easting",
            "NORTHING": "northing",
            "ELEVATION": "elevation",
            "ELEVATION_NAME": "elevation_name",
            "RECORD_CREATED_DT": "record_created_dt",
        }
        frame = frame.rename(columns={
            column: aliases.get(str(column).upper(), column)
            for column in frame.columns
        })
        for column in aliases.values():
            if column not in frame:
                frame[column] = pd.NA
        frame["grade_block_key"] = frame["full_name"].map(grade_block_key)
        frame["easting"] = pd.to_numeric(frame["easting"], errors="coerce")
        frame["northing"] = pd.to_numeric(frame["northing"], errors="coerce")
        frame["point"] = pd.to_numeric(frame["point"], errors="coerce")
        return frame[
            frame["grade_block_key"].ne("")
            & frame["easting"].notna()
            & frame["northing"].notna()
        ].sort_values(["grade_block_key", "point"], na_position="last")

    @staticmethod
    def normalize_geological_blocks(blocks):
        """Normalize the active GRADE_BLOCKS snapshot used by the route audit."""
        frame = blocks.copy() if isinstance(blocks, pd.DataFrame) else pd.DataFrame()
        columns = [
            "full_name", "grade_block_key", "mine_code", "location_no",
            "phase", "blast_rl", "blast_no", "flitch_rl", "gb_name",
            "nominal_geological_wmt", "nominal_geological_dmt",
            "geological_material", "geological_is_ore",
            "geological_record_created_dt",
        ]
        if frame.empty:
            return pd.DataFrame(columns=columns)
        aliases = {
            "FULL_NAME": "full_name",
            "MINE_CODE": "mine_code",
            "LOCATION_NO": "location_no",
            "PHASE": "phase",
            "BLAST_RL": "blast_rl",
            "BLAST_NO": "blast_no",
            "FLITCH_RL": "flitch_rl",
            "GB_NAME": "gb_name",
            "GB_WET_TONNES": "nominal_geological_wmt",
            "GB_DRY_TONNES": "nominal_geological_dmt",
            "GB_MATERIAL": "geological_material",
            "IS_ORE": "geological_is_ore",
            "RECORD_CREATED_DT": "geological_record_created_dt",
        }
        frame = frame.rename(columns={
            column: aliases.get(str(column).upper(), column)
            for column in frame.columns
        })
        for column in aliases.values():
            if column not in frame:
                frame[column] = pd.NA
        missing_name = frame["full_name"].isna() | frame["full_name"].astype(
            str
        ).str.strip().isin({"", "nan", "None", "<NA>"})
        if missing_name.any():
            frame.loc[missing_name, "full_name"] = frame.loc[missing_name].apply(
                lambda row: "_".join(_first_text(row.get(column)) for column in (
                    "location_no", "phase", "blast_rl", "blast_no",
                    "flitch_rl", "gb_name",
                )),
                axis=1,
            )
        frame["grade_block_key"] = frame["full_name"].map(grade_block_key)
        for column in (
            "nominal_geological_wmt", "nominal_geological_dmt",
        ):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["geological_record_created_dt"] = pd.to_datetime(
            frame["geological_record_created_dt"], errors="coerce"
        )
        source_order = frame.index
        frame["__source_order"] = source_order
        frame = frame[frame["grade_block_key"].ne("")].sort_values(
            [
                "grade_block_key", "geological_record_created_dt",
                "__source_order",
            ],
            na_position="first",
        ).drop_duplicates("grade_block_key", keep="last")
        return frame[columns].reset_index(drop=True)

    @staticmethod
    def normalize_cumulative_actual(cumulative_actual):
        """Normalize lifetime ExPit tonnes used only for geological depletion."""
        frame = (
            cumulative_actual.copy()
            if isinstance(cumulative_actual, pd.DataFrame) else pd.DataFrame()
        )
        columns = [
            "grade_block_key", "cumulative_actual_wmt",
            "cumulative_first_actual_datetime", "cumulative_last_actual_datetime",
        ]
        if frame.empty:
            return pd.DataFrame(columns=columns)
        aliases = {
            "FULL_NAME": "full_name",
            "SOURCE_FMS": "full_name",
            "CUMULATIVE_ACTUAL_WMT": "cumulative_actual_wmt",
            "CUMULATIVE_FIRST_ACTUAL_DATETIME": "cumulative_first_actual_datetime",
            "CUMULATIVE_LAST_ACTUAL_DATETIME": "cumulative_last_actual_datetime",
        }
        frame = frame.rename(columns={
            column: aliases.get(str(column).upper(), column)
            for column in frame.columns
        })
        for column in aliases.values():
            if column not in frame:
                frame[column] = pd.NA
        frame["grade_block_key"] = frame["full_name"].map(grade_block_key)
        frame["cumulative_actual_wmt"] = pd.to_numeric(
            frame["cumulative_actual_wmt"], errors="coerce"
        )
        for column in (
            "cumulative_first_actual_datetime", "cumulative_last_actual_datetime",
        ):
            frame[column] = pd.to_datetime(frame[column], errors="coerce")
        frame = frame[
            frame["grade_block_key"].ne("")
            & frame["cumulative_actual_wmt"].notna()
        ]
        if frame.empty:
            return pd.DataFrame(columns=columns)
        return frame.groupby("grade_block_key", as_index=False).agg({
            "cumulative_actual_wmt": "sum",
            "cumulative_first_actual_datetime": "min",
            "cumulative_last_actual_datetime": "max",
        })[columns]

    def _geological_audit_values(
        self, grade_block_key_value, aps_planned_wmt, geological_lookup,
        cumulative_actual_lookup,
    ):
        geological = geological_lookup.get(grade_block_key_value)
        cumulative = cumulative_actual_lookup.get(grade_block_key_value)
        nominal_wmt = (
            _finite(geological.get("nominal_geological_wmt"), float("nan"))
            if geological is not None else float("nan")
        )
        nominal_dmt = (
            _finite(geological.get("nominal_geological_dmt"), float("nan"))
            if geological is not None else float("nan")
        )
        cumulative_wmt = (
            _finite(cumulative.get("cumulative_actual_wmt"), float("nan"))
            if cumulative is not None else float("nan")
        )
        warning = ""
        if geological is None:
            warning = "No active nominal grade-block record was matched."
        elif not math.isfinite(nominal_wmt) or nominal_wmt <= 0:
            warning = "Matched grade block has no positive nominal wet tonnes."
        elif cumulative is None:
            warning = "Cumulative ExPit actual tonnes were unavailable."
        estimated_remaining = (
            max(nominal_wmt - cumulative_wmt, 0.0)
            if math.isfinite(nominal_wmt) and math.isfinite(cumulative_wmt)
            else None
        )
        remaining_fraction = (
            min(max(estimated_remaining / nominal_wmt, 0.0), 1.0)
            if estimated_remaining is not None
            and math.isfinite(nominal_wmt) and nominal_wmt > 0
            else None
        )
        if remaining_fraction is None:
            geological_status = "Unavailable"
        elif remaining_fraction <= self.tolerance + 1e-12:
            geological_status = "Complete"
        elif remaining_fraction < 1.0 - 1e-12:
            geological_status = "Partial"
        else:
            geological_status = "Not started"
        return {
            "geological_data_available": bool(
                geological is not None and math.isfinite(nominal_wmt)
                and nominal_wmt > 0
            ),
            "nominal_geological_wmt": nominal_wmt if math.isfinite(nominal_wmt) else None,
            "nominal_geological_dmt": nominal_dmt if math.isfinite(nominal_dmt) else None,
            "cumulative_actual_wmt": cumulative_wmt if math.isfinite(cumulative_wmt) else None,
            "estimated_geological_remaining_wmt": estimated_remaining,
            "geological_remaining_fraction": remaining_fraction,
            "geological_completion_status": geological_status,
            "aps_share_of_nominal_pct": (
                aps_planned_wmt / nominal_wmt * 100.0
                if math.isfinite(nominal_wmt) and nominal_wmt > 0 else None
            ),
            "geological_depletion_pct": (
                cumulative_wmt / nominal_wmt * 100.0
                if math.isfinite(nominal_wmt) and nominal_wmt > 0
                and math.isfinite(cumulative_wmt) else None
            ),
            "geological_material": (
                geological.get("geological_material") if geological is not None else None
            ),
            "geological_is_ore": (
                geological.get("geological_is_ore") if geological is not None else None
            ),
            "geological_record_created_dt": (
                geological.get("geological_record_created_dt")
                if geological is not None else None
            ),
            "geological_warning": warning,
        }

    @staticmethod
    def centroids(geometry):
        if geometry.empty:
            return {}
        return {
            key: (float(group["easting"].mean()), float(group["northing"].mean()))
            for key, group in geometry.groupby("grade_block_key", sort=False)
        }

    @staticmethod
    def _polygon_area(points):
        if len(points) < 3:
            return 0.0
        return abs(sum(
            left[0] * right[1] - right[0] * left[1]
            for left, right in zip(points, points[1:] + points[:1])
        )) / 2.0

    @staticmethod
    def _clip_polygon_south_of(points, northing):
        """Clip a polygon to y <= northing (depletion proceeds north-south)."""
        if len(points) < 3:
            return []
        clipped = []
        previous = points[-1]
        previous_inside = previous[1] <= northing + 1e-9
        for current in points:
            current_inside = current[1] <= northing + 1e-9
            if current_inside != previous_inside:
                dy = current[1] - previous[1]
                ratio = (
                    (northing - previous[1]) / dy
                    if abs(dy) > 1e-12 else 0.0
                )
                clipped.append((
                    previous[0] + ratio * (current[0] - previous[0]),
                    northing,
                ))
            if current_inside:
                clipped.append(current)
            previous = current
            previous_inside = current_inside
        return clipped

    @staticmethod
    def _clip_polygon_north_of(points, northing):
        """Clip a polygon to y >= northing (the already-depleted portion)."""
        if len(points) < 3:
            return []
        clipped = []
        previous = points[-1]
        previous_inside = previous[1] >= northing - 1e-9
        for current in points:
            current_inside = current[1] >= northing - 1e-9
            if current_inside != previous_inside:
                dy = current[1] - previous[1]
                ratio = (
                    (northing - previous[1]) / dy
                    if abs(dy) > 1e-12 else 0.0
                )
                clipped.append((
                    previous[0] + ratio * (current[0] - previous[0]),
                    northing,
                ))
            if current_inside:
                clipped.append(current)
            previous = current
            previous_inside = current_inside
        return clipped

    @classmethod
    def remaining_polygon(cls, points, remaining_fraction):
        """Return the southern polygon area representing geological balance."""
        cleaned = [
            (float(point[0]), float(point[1]))
            for point in points
            if len(point) >= 2
            and math.isfinite(_finite(point[0], float("nan")))
            and math.isfinite(_finite(point[1], float("nan")))
        ]
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1]:
            cleaned = cleaned[:-1]
        fraction = min(max(_finite(remaining_fraction, 1.0), 0.0), 1.0)
        if len(cleaned) < 3 or fraction <= 1e-9:
            return []
        if fraction >= 1.0 - 1e-9:
            return cleaned
        full_area = cls._polygon_area(cleaned)
        if full_area <= 1e-12:
            return cleaned
        south = min(point[1] for point in cleaned)
        north = max(point[1] for point in cleaned)
        target_area = full_area * fraction
        for _ in range(50):
            boundary = (south + north) / 2.0
            candidate = cls._clip_polygon_south_of(cleaned, boundary)
            if cls._polygon_area(candidate) < target_area:
                south = boundary
            else:
                north = boundary
        return cls._clip_polygon_south_of(cleaned, north)

    @classmethod
    def depleted_polygon(cls, points, remaining_fraction):
        """Return the northern polygon area already depleted geologically."""
        cleaned = [
            (float(point[0]), float(point[1]))
            for point in points
            if len(point) >= 2
            and math.isfinite(_finite(point[0], float("nan")))
            and math.isfinite(_finite(point[1], float("nan")))
        ]
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1]:
            cleaned = cleaned[:-1]
        fraction = min(max(_finite(remaining_fraction, 1.0), 0.0), 1.0)
        depleted_fraction = 1.0 - fraction
        if len(cleaned) < 3 or depleted_fraction <= 1e-9:
            return []
        if depleted_fraction >= 1.0 - 1e-9:
            return cleaned
        full_area = cls._polygon_area(cleaned)
        if full_area <= 1e-12:
            return []
        south = min(point[1] for point in cleaned)
        north = max(point[1] for point in cleaned)
        target_area = full_area * depleted_fraction
        for _ in range(50):
            boundary = (south + north) / 2.0
            candidate = cls._clip_polygon_north_of(cleaned, boundary)
            if cls._polygon_area(candidate) > target_area:
                south = boundary
            else:
                north = boundary
        return cls._clip_polygon_north_of(cleaned, north)

    @staticmethod
    def _direction(actual_keys, planned_positions, centroids):
        matched = [key for key in actual_keys if key in planned_positions]
        matched = _compressed(matched)
        signs = []
        for left, right in zip(matched, matched[1:]):
            difference = planned_positions[right] - planned_positions[left]
            if difference:
                signs.append(1 if difference > 0 else -1)
        reversals = sum(
            1 for left, right in zip(signs, signs[1:]) if left != right
        )
        sign = signs[-1] if signs else 1
        label = "forward" if sign > 0 else "reverse"
        if reversals:
            label = f"{label}_after_{reversals}_reversal{'s' if reversals != 1 else ''}"

        vector = None
        geometry_keys = [key for key in actual_keys if key in centroids]
        if len(geometry_keys) >= 2:
            left = centroids[geometry_keys[-2]]
            right = centroids[geometry_keys[-1]]
            dx, dy = right[0] - left[0], right[1] - left[1]
            length = math.hypot(dx, dy)
            if length > 0:
                vector = (dx / length, dy / length)
        return sign, label, reversals, vector

    @staticmethod
    def _map_actual_route_keys(
        actual_geometry_keys, planned_positions, geometry_keys
    ):
        """Map actual parent keys to planned route occurrences.

        Ore parents normally map directly. Planned waste occurrences use a
        synthetic route key so repeated visits remain distinguishable; this
        method maps the parent-only Snowflake identity back to the nearest
        occurrence in the observed progression.
        """
        candidates = {}
        for route_key, geometry_key in geometry_keys.items():
            candidates.setdefault(geometry_key, []).append(route_key)
        for geometry_key in candidates:
            candidates[geometry_key].sort(
                key=lambda key: planned_positions.get(key, float("inf"))
            )

        mapped = []
        last_position = None
        used_occurrences = set()
        for geometry_key in actual_geometry_keys:
            if geometry_key in planned_positions:
                route_key = geometry_key
            else:
                options = candidates.get(geometry_key, [])
                unused = [key for key in options if key not in used_occurrences]
                selectable = unused or options
                if not selectable:
                    route_key = geometry_key
                elif last_position is None:
                    route_key = selectable[0]
                else:
                    route_key = min(
                        selectable,
                        key=lambda key: (
                            abs(planned_positions.get(key, last_position) - last_position),
                            planned_positions.get(key, float("inf")),
                        ),
                    )
                if route_key in options:
                    used_occurrences.add(route_key)
            mapped.append(route_key)
            if route_key in planned_positions:
                last_position = planned_positions[route_key]
        return mapped

    @staticmethod
    def _operational_slice_name(value):
        """Return a stable APS operational-slice identity."""
        return re.sub(
            r"/+", "/", str(value or "").strip().replace("\\", "/")
        ).rstrip("/").upper()

    @classmethod
    def _assign_route_occurrences(cls, agent_plan):
        """Keep every APS operational-slice visit as a route occurrence.

        Completion remains parent-based, but route inference must retain the
        sliced APS pattern. Consecutive payload rows from the same slice share
        one occurrence; a later revisit receives a new occurrence identity.
        """
        frame = agent_plan.copy()
        counters = {}
        occurrences = []
        previous_base = None
        current_occurrence = None
        for _, row in frame.iterrows():
            if bool(row.get("route_only_waste")):
                base = str(row.get("route_identity") or "")
            else:
                base = "SLICE::" + cls._operational_slice_name(
                    row.get("source")
                )
            if base != previous_base:
                counters[base] = counters.get(base, 0) + 1
                current_occurrence = (
                    f"{base}::OCCURRENCE::{counters[base]}"
                )
            occurrences.append(current_occurrence)
            previous_base = base
        frame["route_occurrence"] = occurrences
        return frame

    @staticmethod
    def _actual_occurrence_route(
        actual_context,
        schedule_start,
        planned_positions,
        occurrence_geometry_keys,
        occurrence_planned_wmt,
    ):
        """Infer which APS slice occurrences each parent-level actual depleted.

        Snowflake identifies only the parent block. Actual tonnes are therefore
        allocated FIFO through that parent's APS operational occurrences while
        retaining the chronological parent visits observed from the agent.
        """
        candidates = {}
        for occurrence, geometry_key in occurrence_geometry_keys.items():
            candidates.setdefault(geometry_key, []).append(occurrence)
        for geometry_key in candidates:
            candidates[geometry_key].sort(
                key=lambda key: planned_positions.get(key, float("inf"))
            )

        remaining = {
            occurrence: max(_finite(occurrence_planned_wmt.get(occurrence)), 0.0)
            for occurrence in planned_positions
        }
        mapped = []
        last_position = None
        context = actual_context.sort_values(
            "transaction_datetime", na_position="last", kind="mergesort"
        )
        for _, row in context.iterrows():
            geometry_key = str(row.get("grade_block_key") or "")
            options = candidates.get(geometry_key, [])
            if not options:
                mapped.append(geometry_key)
                continue

            timestamp = row.get("transaction_datetime")
            within_schedule = (
                pd.notna(timestamp) and timestamp >= schedule_start
            )
            if not within_schedule:
                if last_position is None:
                    occurrence = options[0]
                else:
                    occurrence = min(
                        options,
                        key=lambda key: (
                            abs(
                                planned_positions.get(key, last_position)
                                - last_position
                            ),
                            planned_positions.get(key, float("inf")),
                        ),
                    )
                mapped.append(occurrence)
                last_position = planned_positions.get(
                    occurrence, last_position
                )
                continue

            actual_wmt = max(_finite(row.get("actual_wmt")), 0.0)
            for occurrence in options:
                if actual_wmt <= 1e-9:
                    break
                available = remaining.get(occurrence, 0.0)
                if available <= 1e-9:
                    continue
                mapped.append(occurrence)
                consumed = min(actual_wmt, available)
                remaining[occurrence] = max(available - consumed, 0.0)
                actual_wmt -= consumed
                last_position = planned_positions.get(
                    occurrence, last_position
                )
            if actual_wmt > 1e-9:
                # Actual tonnes beyond APS coverage still belong to the last
                # known occurrence, but never manufacture a new future slice.
                occurrence = options[-1]
                mapped.append(occurrence)
                last_position = planned_positions.get(
                    occurrence, last_position
                )
        return _compressed(mapped)

    @staticmethod
    def _spatial_order(
        remaining_keys, latest_key, planned_positions, centroids,
        direction_sign, direction_vector,
    ):
        remaining = list(dict.fromkeys(remaining_keys))
        if not remaining:
            return []
        current_key = latest_key if latest_key in centroids else None
        ordered = []
        vector = direction_vector

        while remaining:
            if current_key not in centroids:
                current_position = planned_positions.get(
                    current_key,
                    0 if direction_sign > 0 else max(
                        planned_positions.values(), default=0
                    ) + 1,
                )
                def sequence_distance(key):
                    position = planned_positions.get(key, current_position)
                    directed_delta = direction_sign * (
                        position - current_position
                    )
                    return (
                        0 if directed_delta >= 0 else 1,
                        abs(directed_delta),
                        position if direction_sign > 0 else -position,
                    )
                candidate = min(remaining, key=sequence_distance)
            else:
                current = centroids[current_key]
                distances = []
                for key in remaining:
                    if key not in centroids:
                        distances.append((float("inf"), 1.0, planned_positions.get(key, 0), key))
                        continue
                    point = centroids[key]
                    dx, dy = point[0] - current[0], point[1] - current[1]
                    distance = math.hypot(dx, dy)
                    backwards = 0.0
                    if vector and distance > 0:
                        projection = (dx * vector[0] + dy * vector[1]) / distance
                        backwards = max(-projection, 0.0)
                    planned_delta = abs(
                        planned_positions.get(key, 0)
                        - planned_positions.get(current_key, 0)
                    )
                    distances.append((distance * (1.0 + backwards), planned_delta, planned_positions.get(key, 0), key))
                candidate = min(distances)[-1]

            if current_key in centroids and candidate in centroids:
                left, right = centroids[current_key], centroids[candidate]
                dx, dy = right[0] - left[0], right[1] - left[1]
                length = math.hypot(dx, dy)
                if length > 0:
                    vector = (dx / length, dy / length)
            ordered.append(candidate)
            remaining.remove(candidate)
            current_key = candidate
        return ordered

    @staticmethod
    def _spatial_faces(route_keys, centroids):
        """Cluster nearby route blocks into faces without external ML libraries."""
        keys = [key for key in dict.fromkeys(route_keys) if key in centroids]
        if len(keys) < 4:
            return {key: 0 for key in keys}
        nearest = []
        for key in keys:
            point = centroids[key]
            distances = [
                math.hypot(
                    centroids[other][0] - point[0],
                    centroids[other][1] - point[1],
                )
                for other in keys if other != key
            ]
            positive = [distance for distance in distances if distance > 0]
            if positive:
                nearest.append(min(positive))
        if not nearest:
            return {key: 0 for key in keys}
        characteristic = float(pd.Series(nearest).median())
        # Adjacent blocks on one face are normally separated by roughly one
        # block width. A relocation to another face/pit is materially larger.
        threshold = max(characteristic * 3.0, 30.0)
        parent = {key: key for key in keys}

        def find(key):
            while parent[key] != key:
                parent[key] = parent[parent[key]]
                key = parent[key]
            return key

        def union(left, right):
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        for index, left in enumerate(keys):
            for right in keys[index + 1:]:
                distance = math.hypot(
                    centroids[right][0] - centroids[left][0],
                    centroids[right][1] - centroids[left][1],
                )
                if distance <= threshold:
                    union(left, right)
        roots = {}
        result = {}
        for key in keys:
            root = find(key)
            roots.setdefault(root, len(roots))
            result[key] = roots[root]
        return result

    @classmethod
    def _alternating_face_order(
        cls, remaining_keys, actual_keys, latest_key, planned_positions,
        centroids, direction_sign, direction_vector,
    ):
        """Project a recurring relocation pattern between distant dig faces."""
        face_by_key = cls._spatial_faces(
            list(planned_positions) + list(actual_keys), centroids
        )
        actual_with_faces = [
            (key, face_by_key.get(key)) for key in actual_keys
            if face_by_key.get(key) is not None
        ]
        face_visits = []
        for _, face in actual_with_faces:
            if not face_visits or face_visits[-1] != face:
                face_visits.append(face)
        # One revisit after leaving a face is the minimum defensible evidence
        # of an alternating day/night relocation pattern.
        relocation_detected = bool(
            len(set(face_visits)) >= 2
            and any(
                face in face_visits[:index - 1]
                for index, face in enumerate(face_visits)
                if index >= 2
            )
        )
        if not relocation_detected:
            return [], {
                "detected": False,
                "face_count": len(set(face_visits)),
                "observed_pattern": face_visits,
            }

        run_lengths = {}
        current_face = None
        current_keys = []
        for key, face in actual_with_faces:
            if face != current_face:
                if current_face is not None:
                    run_lengths.setdefault(current_face, []).append(
                        len(dict.fromkeys(current_keys))
                    )
                current_face, current_keys = face, []
            current_keys.append(key)
        if current_face is not None:
            run_lengths.setdefault(current_face, []).append(
                len(dict.fromkeys(current_keys))
            )
        typical_run = {
            face: max(1, int(round(float(pd.Series(lengths).median()))))
            for face, lengths in run_lengths.items()
        }

        queues = {}
        unclustered = []
        for key in remaining_keys:
            face = face_by_key.get(key)
            if face is None:
                unclustered.append(key)
            else:
                queues.setdefault(face, []).append(key)
        for face, keys in list(queues.items()):
            queues[face] = cls._spatial_order(
                keys, latest_key, planned_positions, centroids,
                direction_sign, direction_vector,
            )

        # Preserve the observed visit cycle, starting with the current face.
        cycle = []
        for face in reversed(face_visits):
            if face not in cycle:
                cycle.insert(0, face)
        if face_visits and face_visits[-1] in cycle:
            current_index = cycle.index(face_visits[-1])
            cycle = cycle[current_index:] + cycle[:current_index]
        ordered = []
        current_visit_keys = []
        if actual_with_faces:
            latest_face = actual_with_faces[-1][1]
            for key, face in reversed(actual_with_faces):
                if face != latest_face:
                    break
                current_visit_keys.append(key)
        first_face_remaining = max(
            typical_run.get(cycle[0], 1)
            - len(dict.fromkeys(current_visit_keys)),
            0,
        ) if cycle else 0
        first_pass = True
        while any(queues.get(face) for face in cycle):
            progressed = False
            for face in cycle:
                queue = queues.get(face, [])
                if not queue:
                    continue
                take = (
                    first_face_remaining
                    if first_pass and face == cycle[0]
                    else typical_run.get(face, 1)
                )
                first_pass = False
                if take <= 0:
                    continue
                ordered.extend(queue[:take])
                del queue[:take]
                progressed = True
            if not progressed:
                # The current visit already reached its typical length; move
                # to the next face rather than looping indefinitely.
                first_pass = False
                for face in cycle[1:] + cycle[:1]:
                    queue = queues.get(face, [])
                    if queue:
                        take = typical_run.get(face, 1)
                        ordered.extend(queue[:take])
                        del queue[:take]
                        progressed = True
                        break
            if not progressed:
                break
        ordered.extend(cls._spatial_order(
            unclustered, latest_key, planned_positions, centroids,
            direction_sign, direction_vector,
        ))
        return ordered, {
            "detected": True,
            "face_count": len(set(face_visits)),
            "observed_pattern": face_visits,
            "typical_blocks_per_visit": typical_run,
        }

    def _consume_parent_payloads(self, group, actual_wmt, force_complete):
        group = group.sort_values(
            ["start_datetime", "delivered_datetime"],
            na_position="last",
            kind="mergesort",
        ).copy()
        if force_complete:
            return group.iloc[0:0].copy()
        remaining_actual = max(_finite(actual_wmt), 0.0)
        retained = []
        for _, row in group.iterrows():
            payload = max(_finite(row.get("payload")), 0.0)
            if remaining_actual >= payload - 1e-9:
                remaining_actual -= payload
                continue
            adjusted = row.copy()
            if remaining_actual > 0:
                adjusted["payload"] = payload - remaining_actual
                ratio = adjusted["payload"] / payload if payload > 0 else 0.0
                properties = adjusted.get("source_properties")
                if isinstance(properties, dict):
                    properties = dict(properties)
                    for key, value in list(properties.items()):
                        if source_property_kind(
                            key, self.source_property_kinds
                        ) == "additive":
                            properties[key] = _finite(value) * ratio
                    adjusted["source_properties"] = properties
                remaining_actual = 0.0
            retained.append(adjusted)
        return pd.DataFrame(retained, columns=group.columns)

    def reconcile(
        self,
        planned_transactions,
        actual_movements,
        geometry=None,
        geological_blocks=None,
        cumulative_actual=None,
        schedule_start=None,
        as_of=None,
    ):
        planned = (
            planned_transactions.copy()
            if isinstance(planned_transactions, pd.DataFrame)
            else pd.DataFrame()
        )
        if planned.empty:
            return ExpitSequenceResult(
                planned, pd.DataFrame(columns=AUDIT_COLUMNS),
                self.normalize_geometry(geometry),
                self.normalize_geological_blocks(geological_blocks),
                pd.DataFrame(), {}, [],
            )

        for column in ("start_datetime", "delivered_datetime"):
            planned[column] = planned[column].map(_perth_naive_timestamp)
        planned["payload"] = pd.to_numeric(planned["payload"], errors="coerce").fillna(0.0)
        planned["parent_grade_block"] = planned["source"].map(
            parent_grade_block_name
        )
        planned["grade_block_key"] = planned["parent_grade_block"].map(grade_block_key)
        if "route_material" not in planned:
            planned["route_material"] = ""
        if "route_only_waste" not in planned:
            planned["route_only_waste"] = planned["route_material"].map(is_route_only_waste)
        planned["route_identity"] = planned.apply(
            lambda row: (
                f"WASTE::{row.get('parent_grade_block')}::"
                f"{_segment_token(row.get('route_segment'), row.name)}"
                if bool(row.get("route_only_waste"))
                else str(row.get("grade_block_key") or "")
            ),
            axis=1,
        )

        actual = self.normalize_actual_movements(actual_movements)
        geometry = self.normalize_geometry(geometry)
        geological_blocks = self.normalize_geological_blocks(geological_blocks)
        cumulative_actual = self.normalize_cumulative_actual(cumulative_actual)
        centroids = self.centroids(geometry)
        geological_lookup = {
            row["grade_block_key"]: row
            for _, row in geological_blocks.iterrows()
        }
        cumulative_actual_lookup = {
            row["grade_block_key"]: row
            for _, row in cumulative_actual.iterrows()
        }
        as_of = _perth_naive_timestamp(as_of or pd.Timestamp.now())
        minimum_start = planned["start_datetime"].dropna().min()
        schedule_start = _perth_naive_timestamp(
            schedule_start if schedule_start is not None
            else minimum_start if pd.notna(minimum_start)
            else as_of
        )
        deduction_actual = actual[
            (actual["transaction_datetime"] >= schedule_start)
            & (actual["transaction_datetime"] <= as_of)
        ].copy()

        updated_groups = []
        audit_rows = []
        warnings = []
        agent_summaries = {}

        planned_agents = list(dict.fromkeys(planned["agent"].astype(str)))
        for agent in planned_agents:
            agent_plan = planned[planned["agent"].astype(str) == agent].copy()
            agent_plan = agent_plan.sort_values(
                ["start_datetime", "delivered_datetime"],
                kind="mergesort",
            )
            agent_plan = self._assign_route_occurrences(agent_plan)
            context_mask = actual["agent"].map(
                lambda value: _agent_matches(value, agent)
            ).astype(bool)
            deduction_mask = deduction_actual["agent"].map(
                lambda value: _agent_matches(value, agent)
            ).astype(bool)
            agent_actual_context = actual.loc[context_mask].copy()
            agent_actual = deduction_actual.loc[deduction_mask].copy()

            # Tonne completion stays at parent level, because Snowflake does
            # not identify APS slices. Route progression remains at the APS
            # operational-slice occurrence level so planned returns to a
            # parent are not misclassified as reversals.
            parent_keys = list(dict.fromkeys(
                _compressed(agent_plan["route_identity"].tolist())
            ))
            original_keys = list(dict.fromkeys(
                _compressed(agent_plan["route_occurrence"].tolist())
            ))
            planned_positions = {
                key: index + 1 for index, key in enumerate(original_keys)
            }
            actual_geometry_keys = _compressed(
                agent_actual_context["grade_block_key"].tolist()
            )

            actual_totals = (
                agent_actual.groupby("grade_block_key")["actual_wmt"].sum().to_dict()
                if not agent_actual.empty else {}
            )
            planned_totals = agent_plan.groupby("route_identity")["payload"].sum().to_dict()
            parent_names = agent_plan.groupby("route_identity")["parent_grade_block"].first().to_dict()
            material = agent_plan.groupby("route_identity")["route_material"].first().to_dict()
            parent_geometry_keys = agent_plan.groupby(
                "route_identity"
            )["grade_block_key"].first().to_dict()
            occurrence_geometry_keys = agent_plan.groupby(
                "route_occurrence"
            )["grade_block_key"].first().to_dict()
            occurrence_parent_keys = agent_plan.groupby(
                "route_occurrence"
            )["route_identity"].first().to_dict()
            occurrence_planned_wmt = agent_plan.groupby(
                "route_occurrence"
            )["payload"].sum().to_dict()
            parent_occurrences = {}
            for occurrence in original_keys:
                parent_occurrences.setdefault(
                    occurrence_parent_keys.get(occurrence), []
                ).append(occurrence)
            parent_positions = {
                key: min(
                    planned_positions[occurrence]
                    for occurrence in occurrences
                )
                for key, occurrences in parent_occurrences.items()
                if occurrences
            }
            actual_keys = self._actual_occurrence_route(
                agent_actual_context,
                schedule_start,
                planned_positions,
                occurrence_geometry_keys,
                occurrence_planned_wmt,
            )
            actual_first = {}
            actual_last = {}
            parent_actual_first = {}
            parent_actual_last = {}
            for index, key in enumerate(actual_keys, start=1):
                actual_first.setdefault(key, index)
                actual_last[key] = index
                parent_key = occurrence_parent_keys.get(key)
                if parent_key is not None:
                    parent_actual_first.setdefault(parent_key, index)
                    parent_actual_last[parent_key] = index

            latest_actual_geometry_key = (
                actual_geometry_keys[-1] if actual_geometry_keys else ""
            )
            latest_actual_route_key = actual_keys[-1] if actual_keys else ""
            matched_actual = [key for key in actual_keys if key in planned_positions]
            latest_matched_key = matched_actual[-1] if matched_actual else ""
            planned_geometry_keys = set(occurrence_geometry_keys.values())
            unmatched_keys = list(dict.fromkeys(
                geometry_key for geometry_key in actual_geometry_keys
                if geometry_key not in planned_geometry_keys
            ))
            route_centroids = {
                route_key: centroids[geometry_key]
                for route_key, geometry_key in occurrence_geometry_keys.items()
                if geometry_key in centroids
            }
            direction_centroids = dict(centroids)
            direction_centroids.update(route_centroids)
            sign, direction, reversals, vector = self._direction(
                actual_keys, planned_positions, direction_centroids
            )

            remaining_parent_frames = {}
            completed = 0
            partial = 0
            actual_remaining = {
                key: max(_finite(value), 0.0)
                for key, value in actual_totals.items()
            }
            geometry_occurrences = {
                geometry_key: sum(
                    1 for parent_key in parent_keys
                    if parent_geometry_keys.get(
                        parent_key, parent_key
                    ) == geometry_key
                )
                for geometry_key in set(parent_geometry_keys.values())
            }
            for key in parent_keys:
                parent_group = agent_plan[agent_plan["route_identity"] == key]
                geometry_key = parent_geometry_keys.get(key, key)
                planned_wmt = _finite(planned_totals.get(key))
                available_actual = max(
                    _finite(actual_remaining.get(geometry_key)), 0.0
                )
                occurrences_left = geometry_occurrences.get(geometry_key, 1)
                actual_wmt = (
                    min(available_actual, planned_wmt)
                    if occurrences_left > 1 else available_actual
                )
                actual_remaining[geometry_key] = max(
                    available_actual - actual_wmt, 0.0
                )
                geometry_occurrences[geometry_key] = max(
                    occurrences_left - 1, 0
                )
                ratio = actual_wmt / planned_wmt if planned_wmt > 0 else 0.0
                force_complete = (
                    planned_wmt > 0
                    and ratio >= 1.0 - self.tolerance
                )
                retained = self._consume_parent_payloads(
                    parent_group, actual_wmt, force_complete
                )
                if force_complete:
                    status = "Complete"
                    completed += 1
                elif actual_wmt > 0:
                    status = "Partial"
                    partial += 1
                else:
                    status = "Not started"
                remaining_wmt = 0.0 if force_complete else max(planned_wmt - actual_wmt, 0.0)
                geological_values = self._geological_audit_values(
                    geometry_key, planned_wmt, geological_lookup,
                    cumulative_actual_lookup,
                )
                if not retained.empty and remaining_wmt > 0:
                    remaining_parent_frames[key] = retained
                audit_rows.append({
                    "agent": agent,
                    "record_type": "planned_parent",
                    "parent_grade_block": parent_names.get(key, ""),
                    "grade_block_key": geometry_key,
                    "material_class": material.get(key, ""),
                    "original_sequence": parent_positions.get(key),
                    "actual_first_sequence": parent_actual_first.get(key),
                    "actual_last_sequence": parent_actual_last.get(key),
                    "updated_sequence": None,
                    "aps_planned_wmt": planned_wmt,
                    "actual_schedule_wmt": actual_wmt,
                    "aps_remaining_wmt": remaining_wmt,
                    "planned_wmt": planned_wmt,
                    "actual_wmt": actual_wmt,
                    "remaining_wmt": remaining_wmt,
                    **geological_values,
                    "schedule_completion_pct": ratio * 100.0,
                    "completion_ratio": ratio,
                    "completion_tolerance_pct": self.tolerance * 100.0,
                    "completion_status": status,
                    "inferred_direction": direction,
                    "direction_reversals": reversals,
                    "geometry_available": geometry_key in centroids,
                    "centroid_easting": centroids.get(geometry_key, (None, None))[0],
                    "centroid_northing": centroids.get(geometry_key, (None, None))[1],
                    "is_latest_actual_block": (
                        key
                        == occurrence_parent_keys.get(latest_actual_route_key)
                    ),
                    "reconciliation_confidence": "",
                    "warning": "",
                })

            retained_rows = (
                pd.concat(
                    list(remaining_parent_frames.values()),
                    ignore_index=True,
                )
                if remaining_parent_frames
                else agent_plan.iloc[0:0].copy()
            )
            retained_occurrences = set(
                retained_rows.get("route_occurrence", pd.Series(dtype=str))
                .dropna().astype(str)
            )
            remaining_keys = [
                key for key in original_keys if key in retained_occurrences
            ]
            remaining_frames = {
                key: retained_rows[
                    retained_rows["route_occurrence"].eq(key)
                ].copy()
                for key in remaining_keys
            }

            matched_positions = [
                planned_positions[key] for key in actual_keys
                if key in planned_positions
            ]
            non_adjacent_jump = any(
                abs(right - left) > 1
                for left, right in zip(
                    matched_positions, matched_positions[1:]
                )
            )
            latest_position = planned_positions.get(latest_matched_key)
            incomplete_behind = bool(
                latest_position is not None
                and any(
                    planned_positions.get(key, latest_position) < latest_position
                    for key in remaining_keys
                )
            )
            correction_reasons = []
            if unmatched_keys:
                correction_reasons.append("unmatched actual route evidence")
            if reversals:
                correction_reasons.append("route reversal")
            if non_adjacent_jump:
                correction_reasons.append("non-adjacent route jump")
            if incomplete_behind:
                correction_reasons.append("incomplete block behind agent")
            relocation_order, relocation = self._alternating_face_order(
                remaining_keys,
                actual_keys,
                (
                    latest_actual_route_key
                    if latest_actual_route_key in direction_centroids
                    else latest_actual_geometry_key
                    if latest_actual_geometry_key in direction_centroids
                    else latest_matched_key
                ),
                planned_positions,
                direction_centroids,
                sign,
                vector,
            )
            if relocation.get("detected"):
                correction_reasons.append(
                    "alternating relocation between distant dig faces"
                )
            needs_course_correction = bool(correction_reasons)

            if needs_course_correction:
                updated_order = relocation_order or self._spatial_order(
                    remaining_keys,
                    (
                        latest_actual_route_key
                        if latest_actual_route_key in direction_centroids
                        else latest_actual_geometry_key
                        if latest_actual_geometry_key in direction_centroids
                        else latest_matched_key
                    ),
                    planned_positions,
                    direction_centroids,
                    sign,
                    vector,
                )
            else:
                # Actual progress agrees with APS. Geometry is an audit layer,
                # not authority to rewrite an otherwise valid planned route.
                updated_order = list(remaining_keys)
            if not updated_order:
                updated_order = remaining_keys

            ordered_frames = []
            updated_positions = {
                key: sequence
                for sequence, key in enumerate(updated_order, start=1)
            }
            if needs_course_correction:
                frame_sequence = [
                    remaining_frames[key].copy() for key in updated_order
                ]
            else:
                # Retain the exact sliced/payload APS arrival order. Parent
                # reconciliation may remove or reduce rows, but must not make
                # non-contiguous slices artificially consecutive.
                frame_sequence = [pd.concat(
                    list(remaining_frames.values()), ignore_index=True
                ).sort_values(
                    ["start_datetime", "delivered_datetime"],
                    na_position="last",
                    kind="mergesort",
                )] if remaining_frames else []

            for frame in frame_sequence:
                frame["original_parent_sequence"] = frame[
                    "route_occurrence"
                ].map(planned_positions)
                frame["updated_parent_sequence"] = frame[
                    "route_occurrence"
                ].map(updated_positions)
                frame["expit_reconciliation_status"] = "reconciled"
                ordered_frames.append(frame)
            parent_updated_positions = {}
            for occurrence, sequence in updated_positions.items():
                parent_key = occurrence_parent_keys.get(occurrence)
                if parent_key is not None:
                    parent_updated_positions.setdefault(
                        parent_key, sequence
                    )
            for row in reversed(audit_rows):
                if (
                    row["agent"] == agent
                    and row["record_type"] == "planned_parent"
                ):
                    matching_key = next((
                        key for key, name in parent_names.items()
                        if name == row["parent_grade_block"]
                        and parent_positions.get(key)
                        == row["original_sequence"]
                    ), None)
                    if matching_key is not None:
                        row["updated_sequence"] = (
                            parent_updated_positions.get(matching_key)
                        )

            for key in unmatched_keys:
                rows = agent_actual_context[agent_actual_context["grade_block_key"] == key]
                parent = rows["parent_grade_block"].iloc[-1] if not rows.empty else key
                geological_values = self._geological_audit_values(
                    key, 0.0, geological_lookup, cumulative_actual_lookup,
                )
                audit_rows.append({
                    "agent": agent,
                    "record_type": "unmatched_actual_context",
                    "parent_grade_block": parent,
                    "grade_block_key": key,
                    "material_class": "",
                    "original_sequence": None,
                    "actual_first_sequence": actual_first.get(key),
                    "actual_last_sequence": actual_last.get(key),
                    "updated_sequence": None,
                    "aps_planned_wmt": 0.0,
                    "actual_schedule_wmt": _finite(actual_totals.get(key)),
                    "aps_remaining_wmt": 0.0,
                    "planned_wmt": 0.0,
                    "actual_wmt": _finite(actual_totals.get(key)),
                    "remaining_wmt": 0.0,
                    **geological_values,
                    "schedule_completion_pct": None,
                    "completion_ratio": None,
                    "completion_tolerance_pct": self.tolerance * 100.0,
                    "completion_status": "Actual only - route evidence",
                    "inferred_direction": direction,
                    "direction_reversals": reversals,
                    "geometry_available": key in centroids,
                    "centroid_easting": centroids.get(key, (None, None))[0],
                    "centroid_northing": centroids.get(key, (None, None))[1],
                    "is_latest_actual_block": key == latest_actual_geometry_key,
                    "reconciliation_confidence": "",
                    "warning": "Not inserted into the future BlendMaster payload sequence.",
                })

            geometry_coverage = (
                sum(
                    occurrence_geometry_keys.get(key, key) in centroids
                    for key in original_keys
                ) / len(original_keys)
                if original_keys else 0.0
            )
            geological_coverage = (
                sum(
                    bool(self._geological_audit_values(
                        occurrence_geometry_keys.get(key, key), 0.0,
                        geological_lookup,
                        cumulative_actual_lookup,
                    )["geological_data_available"])
                    for key in original_keys
                ) / len(original_keys)
                if original_keys else 0.0
            )
            if agent_actual_context.empty:
                confidence = "Fallback"
                warning = (
                    "No usable ExPit actual movements were returned; original "
                    "APS transactions were retained for this agent."
                )
                warnings.append(f"{agent}: {warning}")
                fallback = agent_plan.copy()
                fallback["original_parent_sequence"] = fallback[
                    "route_occurrence"
                ].map(planned_positions)
                fallback["updated_parent_sequence"] = fallback["original_parent_sequence"]
                fallback["expit_reconciliation_status"] = "fallback_original"
                ordered_frames = [fallback]
                updated_order = original_keys
            elif matched_actual:
                confidence = "High" if geometry_coverage >= 0.8 else "Medium"
                warning = "" if geometry_coverage >= 0.8 else "Partial polygon coverage; APS order supplemented the spatial route."
            else:
                confidence = "Low"
                warning = "Actual movements did not match an APS parent block; original APS order anchors the future route."
                warnings.append(f"{agent}: {warning}")

            for row in audit_rows:
                if row["agent"] == agent and not row["reconciliation_confidence"]:
                    row["reconciliation_confidence"] = confidence
                    if not row["warning"]:
                        row["warning"] = warning

            if ordered_frames:
                reordered = pd.concat(ordered_frames, ignore_index=True)
                # Reuse the remaining APS time slots while allowing block
                # order to change.  Each payload keeps its own haul-to-delivery
                # lag and all source properties.
                original_slots = sorted(
                    timestamp for timestamp in agent_plan["start_datetime"].dropna().tolist()
                )
                if not original_slots:
                    original_slots = [as_of] * len(reordered)
                if len(original_slots) < len(reordered):
                    cadence = pd.Timedelta(minutes=5)
                    if len(original_slots) >= 2:
                        differences = pd.Series(original_slots).diff().dropna()
                        positive = differences[differences > pd.Timedelta(0)]
                        if not positive.empty:
                            cadence = positive.median()
                    while len(original_slots) < len(reordered):
                        original_slots.append(original_slots[-1] + cadence)
                slot_zero = original_slots[0]
                for index in range(len(reordered)):
                    row = reordered.iloc[index]
                    old_start = row.get("start_datetime")
                    old_delivery = row.get("delivered_datetime")
                    delivery_lag = (
                        old_delivery - old_start
                        if pd.notna(old_start) and pd.notna(old_delivery)
                        else pd.Timedelta(0)
                    )
                    new_start = as_of + (original_slots[index] - slot_zero)
                    reordered.at[index, "start_datetime"] = new_start
                    reordered.at[index, "delivered_datetime"] = new_start + delivery_lag

                # Waste stays in the reconstructed route and consumes time,
                # but it is not a BlendMaster ore/direct-tip source.
                updated_groups.append(
                    reordered[~reordered["route_only_waste"].astype(bool)].copy()
                )

            agent_summaries[agent] = {
                "planned_parent_blocks": len(parent_keys),
                "completed_parent_blocks": completed,
                "partial_parent_blocks": partial,
                "remaining_parent_blocks": len({
                    occurrence_parent_keys.get(key) for key in updated_order
                    if occurrence_parent_keys.get(key) is not None
                }),
                "planned_operational_occurrences": len(original_keys),
                "remaining_operational_occurrences": len(updated_order),
                "actual_route_blocks": len(actual_geometry_keys),
                "unmatched_actual_blocks": len(unmatched_keys),
                "direction": direction,
                "direction_reversals": reversals,
                "relocation_pattern_detected": bool(
                    relocation.get("detected")
                ),
                "relocation_face_count": int(
                    relocation.get("face_count", 0) or 0
                ),
                "relocation_observed_pattern": relocation.get(
                    "observed_pattern", []
                ),
                "relocation_typical_blocks_per_visit": relocation.get(
                    "typical_blocks_per_visit", {}
                ),
                "course_correction_applied": needs_course_correction,
                "course_correction_reason": "; ".join(correction_reasons),
                "geometry_coverage_pct": geometry_coverage * 100.0,
                "geological_coverage_pct": geological_coverage * 100.0,
                "confidence": confidence,
                "latest_actual_parent": (
                    agent_actual_context["parent_grade_block"].iloc[-1]
                    if not agent_actual_context.empty else ""
                ),
                "last_actual_transaction": (
                    agent_actual_context["transaction_datetime"].max()
                    if not agent_actual_context.empty else None
                ),
            }

        updated = (
            pd.concat(updated_groups, ignore_index=True)
            if updated_groups else planned.iloc[0:0].copy()
        )
        audit = pd.DataFrame(audit_rows, columns=AUDIT_COLUMNS)
        summary = {
            "as_of": as_of,
            "schedule_start": schedule_start,
            "context_start": schedule_start - timedelta(hours=24),
            "completion_tolerance_pct": self.tolerance * 100.0,
            "agents": agent_summaries,
        }
        return ExpitSequenceResult(
            updated.reset_index(drop=True),
            audit,
            geometry.reset_index(drop=True),
            geological_blocks.reset_index(drop=True),
            actual.reset_index(drop=True),
            summary,
            warnings,
        )
