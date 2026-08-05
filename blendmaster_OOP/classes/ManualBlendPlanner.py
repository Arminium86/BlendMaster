from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json
from typing import Dict, Iterable, List, Mapping, Optional

import pandas as pd

from classes.ProductBuildProgress import ProductBuildProgress
from classes.GradeStreams import (
    DEFAULT_STREAM,
    STREAMS,
    grade_stream_audit_fields,
    inventory_product_property_aliases,
    legacy_grade_streams,
    normalise_brand,
    normalise_grade_streams,
    reweight_grade_streams_from_properties,
    resolve_grade_vector,
    weighted_merge_grade_streams,
)
from classes.CustomConstraints import (
    CustomConstraintError,
    SafeNumericExpression,
    constraint_key,
    constraint_report_fields,
    custom_constraint_property_keys,
    expand_required_property_keys,
    filter_source_properties,
    mapping_constraint_fields,
    merge_source_properties,
    normalize_custom_constraints,
    scale_additive_source_properties,
    source_property_balance_report_fields,
    source_property_report_fields,
    source_properties_from_mapping,
)


GRADE_NAMES = ("fe", "si", "al", "p", "mn")


class ManualBlendPlanningError(ValueError):
    """A user-correctable error in a manual blend plan."""


class ManualBlendPlanner:
    """
    Build manual steady states and a source-level manual blend report.

    Payload delivery times only determine which direct-tip candidates are
    visible inside a steady state. They never create a steady-state boundary.
    """

    GRADES = GRADE_NAMES
    REPORT_COLUMNS = [
        "start_datetime", "end_datetime", "steady_state_number",
        "blend_option", "blend_ID", "steady_state_duration", "period",
        "actual_direct_tip_ratio", "source", "source_id", "source_type",
        "source_blend_ratio", "source_opening_balance",
        "source_actual_tonnes", "source_closing_balance",
        "reclaimer_source_tonnes", "crusher_source_tonnes", "product_build_source_tonnes",
        *[f"selected_grade_weight_{grade}_tonnes" for grade in GRADE_NAMES],
        "reclaimer_tonnes_stream", "crusher_tonnes_stream",
        "product_build_tonnes_stream", "product_build_actual_tonnes",
        "source_grade_fe", "source_grade_si", "source_grade_al",
        "source_grade_p", "source_grade_mn",
        "selected_grade_stream", "selected_grade_brand",
        "grade_stream_warnings",
        *[
            f"source_grade_{stream}_{grade}"
            for stream in STREAMS
            for grade in GRADE_NAMES
        ],
        "equipment",
        "equipment_rate_input", "equipment_rate_output",
        "crusher_actual_tonnes", "crusher_rate_input",
        "crusher_rate_output", "crusher_actual_grade_fe",
        "crusher_actual_grade_si", "crusher_actual_grade_al",
        "crusher_actual_grade_p", "crusher_actual_grade_mn",
        "crusher_grade_target_min_fe", "crusher_grade_target_max_fe",
        "crusher_grade_target_min_si", "crusher_grade_target_max_si",
        "crusher_grade_target_min_al", "crusher_grade_target_max_al",
        "crusher_grade_target_min_p", "crusher_grade_target_max_p",
        "crusher_grade_target_min_mn", "crusher_grade_target_max_mn",
    ] + ProductBuildProgress.COLUMNS

    def __init__(
        self,
        sequence_rows: Iterable[Mapping],
        blend_definitions: Iterable[Mapping],
        stockpile_data: Optional[Mapping],
        hex_sequence_table: Optional[Iterable[Mapping]],
        payload_transactions: Optional[pd.DataFrame],
        periods: Optional[Mapping],
        product_build_settings: Optional[Iterable[Mapping]],
        crusher_rate: float,
        calendar_inputs: Optional[Mapping] = None,
    ):
        self.sequence_rows = self._normalise_sequence(sequence_rows)
        self.blends = self._normalise_blends(blend_definitions)
        self.stockpile_data = {
            str(key).strip().upper(): deepcopy(value or {})
            for key, value in (stockpile_data or {}).items()
        }
        self.hex_sequence_table = list(hex_sequence_table or [])
        self.payload_transactions = (
            payload_transactions.copy()
            if isinstance(payload_transactions, pd.DataFrame)
            else pd.DataFrame(payload_transactions or [])
        )
        self.periods = dict(periods or {})
        self.product_build_settings = [
            dict(row) for row in (product_build_settings or [])
        ]
        self.calendar_inputs = dict(calendar_inputs or {})
        site_context = self.calendar_inputs.get("site_context") or {}
        solver_config = self.calendar_inputs.get("solver_config") or {}
        self.custom_constraints = normalize_custom_constraints(
            solver_config.get("custom_constraints")
        )
        self.required_source_property_keys = (
            custom_constraint_property_keys(self.custom_constraints)
        )
        self.required_source_property_keys.update(
            solver_config.get("optimisation_source_property_fields") or []
        )
        self.source_property_kinds = dict(
            solver_config.get("source_property_kinds") or {
                str(row.get("name")): str(row.get("kind"))
                for row in (site_context.get("field_definitions") or [])
                if isinstance(row, Mapping) and row.get("name")
            }
        )
        self.source_property_weights = dict(
            solver_config.get("source_property_weights") or {
                str(row.get("name")): str(row.get("weight_field"))
                for row in (site_context.get("field_definitions") or [])
                if isinstance(row, Mapping)
                and row.get("name")
                and row.get("weight_field")
            }
        )
        self.required_source_property_keys = expand_required_property_keys(
            self.required_source_property_keys,
            self.source_property_weights,
        )
        self.selected_data_stream = str(
            site_context.get("selected_data_stream")
            or solver_config.get("selected_data_stream")
            or DEFAULT_STREAM
        ).strip().lower()
        self.crusher_tonnes_stream = str(
            site_context.get("crusher_tonnes_stream")
            or solver_config.get("crusher_tonnes_stream")
            or "modelled_rom_wmt"
        )
        self.reclaimer_tonnes_stream = str(
            site_context.get("reclaimer_tonnes_stream")
            or solver_config.get("reclaimer_tonnes_stream")
            or "modelled_rom_wmt"
        )
        self.product_build_tonnes_stream = str(
            site_context.get("product_build_tonnes_stream")
            or solver_config.get("product_build_tonnes_stream")
            or "modelled_product_wmt"
        )
        self.required_source_property_keys.update({
            self.crusher_tonnes_stream,
            self.reclaimer_tonnes_stream,
            self.product_build_tonnes_stream,
        })
        if self.selected_data_stream in STREAMS:
            self.required_source_property_keys.update(
                f"{self.selected_data_stream}_{grade}"
                for grade in self.GRADES
            )
            self.required_source_property_keys = expand_required_property_keys(
                self.required_source_property_keys,
                self.source_property_weights,
            )
        self.opf = site_context.get("opf")
        self.crusher_rate = self._positive_number(
            crusher_rate, "Crusher rate"
        )
        configured_rates = self.calendar_inputs.get("crusher_rate", {})
        if not isinstance(configured_rates, Mapping):
            configured_rates = {}
        self.period_keys = self._configured_period_keys()
        self.period_labels = [
            "Preplan" if key == "preplan" else key.title()
            for key in self.period_keys
        ]
        self.crusher_rates = {
            period: self._positive_number(
                configured_rates.get(period, self.crusher_rate),
                f"{period.replace('_', ' ')} crusher rate",
            )
            for period in self.period_labels
        }
        self._inventory_template = self._build_inventory()

    def _configured_period_keys(self):
        keys = []
        index = 0
        while True:
            key = "preplan" if index == 0 else f"period_{index}"
            if f"{key}_start" not in self.periods:
                break
            keys.append(key)
            index += 1
        return keys or ["preplan", "period_1", "period_2"]

    @staticmethod
    def _number(value, default=0.0):
        try:
            result = float(value)
        except (TypeError, ValueError):
            return float(default)
        return result

    @classmethod
    def _positive_number(cls, value, label):
        result = cls._number(value)
        if result <= 0:
            raise ManualBlendPlanningError(f"{label} must be greater than zero.")
        return result

    @staticmethod
    def _datetime(value, label):
        result = pd.to_datetime(value, errors="coerce")
        if pd.isna(result):
            raise ManualBlendPlanningError(f"{label} is not a valid date/time.")
        return result.to_pydatetime()

    def _normalise_sequence(self, rows):
        result = []
        for row in rows or []:
            start = self._datetime(
                row.get("_exact_start", row.get("Start Datetime")),
                "Blend start",
            )
            end_value = row.get(
                "_exact_end", row.get("End Datetime")
            )
            if end_value in (None, ""):
                duration = self._number(row.get("Duration (hrs)"))
                end = start + timedelta(hours=duration)
            else:
                end = self._datetime(end_value, "Blend end")
            if end <= start:
                continue
            result.append({
                **dict(row),
                "Blend ID": str(row.get("Blend ID", "")).strip(),
                "_start": start,
                "_end": end,
            })
        return sorted(result, key=lambda row: (row["_start"], row["_end"]))

    def _normalise_blends(self, rows):
        result = {}
        for row in rows or []:
            blend_id = str(row.get("Blend ID", "")).strip()
            sources = self._split_values(row.get("Sources"))
            ratios = [
                self._number(value) for value
                in self._split_values(row.get("Source Ratios"))
            ]
            if not sources:
                result[blend_id] = {
                    **dict(row),
                    "_sources": [],
                    "_ratios": [],
                }
                continue
            if len(ratios) != len(sources) or sum(ratios) <= 0:
                ratios = [1.0 / len(sources)] * len(sources)
            else:
                total = sum(max(value, 0) for value in ratios)
                ratios = [
                    max(value, 0) / total for value in ratios
                ]
            result[blend_id] = {
                **dict(row),
                "_sources": [source.strip().upper() for source in sources],
                "_ratios": ratios,
            }
        return result

    @staticmethod
    def _split_values(value):
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [
            item.strip() for item in str(value or "").split(",")
            if item.strip()
        ]

    def _build_inventory(self):
        chunks = {}
        amt_footprints = set()
        for row in self.hex_sequence_table:
            footprint = str(row.get("footprint") or "").strip().upper()
            if not footprint:
                continue
            amt_footprints.add(footprint)
            chunk = {
                "source_id": str(
                    row.get("hex") or
                    f"{footprint}_CHUNK_{int(self._number(row.get('sequence'))):03d}"
                ),
                "sequence": self._number(row.get("sequence")),
                "balance": max(self._number(row.get("balance")), 0),
            }
            for grade in self.GRADES:
                chunk[f"grade_{grade}"] = self._number(
                    row.get(f"grade_{grade}")
                )
            chunk["grade_streams"] = normalise_grade_streams(
                row.get("grade_streams") or row.get("GRADE_STREAMS"), row
            )
            chunk["source_properties"] = filter_source_properties(
                source_properties_from_mapping(row),
                self.required_source_property_keys,
            )
            chunk["is_amt"] = True
            chunks.setdefault(footprint, []).append(chunk)

        for footprint in chunks:
            chunks[footprint].sort(
                key=lambda row: (row["sequence"], row["source_id"])
            )

        for name, values in self.stockpile_data.items():
            if name in amt_footprints:
                continue
            values = {
                **dict(values or {}),
                **inventory_product_property_aliases(values, self.opf),
            }
            chunk = {
                "source_id": name,
                "sequence": 1,
                "balance": max(self._number(values.get("balance")), 0),
            }
            for grade in self.GRADES:
                chunk[f"grade_{grade}"] = self._number(
                    values.get(f"grade_{grade}")
                )
            chunk["grade_streams"] = normalise_grade_streams(
                values.get("grade_streams") or values.get("GRADE_STREAMS"),
                values,
            )
            chunk["source_properties"] = filter_source_properties(
                source_properties_from_mapping(values),
                self.required_source_property_keys,
            )
            chunk["is_amt"] = False
            chunks[name] = [chunk]
        self._amt_sources = amt_footprints
        return chunks

    def _period_boundaries(self):
        values = []
        for key in (
            boundary
            for period_key in self.period_keys
            for boundary in (
                f"{period_key}_start", f"{period_key}_end"
            )
        ):
            value = self.periods.get(key)
            if value is not None:
                values.append(self._datetime(value, key))
        return sorted(set(values))

    def _period_number(self, at_time):
        for index, period_key in reversed(
            list(enumerate(self.period_keys))
        ):
            period_start = self.periods.get(f"{period_key}_start")
            if period_start is not None and at_time >= self._datetime(
                period_start, f"{period_key}_start"
            ):
                return index
        return 0

    def _crusher_rate_for(self, at_time):
        period = self.period_labels[self._period_number(at_time)]
        return self.crusher_rates.get(period, self.crusher_rate)

    @staticmethod
    def state_key(start, end, blend_id):
        return (
            f"{start.strftime('%Y-%m-%d %H:%M:%S')}|"
            f"{end.strftime('%Y-%m-%d %H:%M:%S')}|{blend_id}"
        )

    def _build_completion_distance(self, produced_tonnes):
        running = 0.0
        for setting in self.product_build_settings:
            running += max(self._number(setting.get("target_tonnes")), 0)
            if running > produced_tonnes + 1e-7:
                return running - produced_tonnes
        return None

    @staticmethod
    def _current_chunk(source_chunks):
        for chunk in source_chunks or []:
            if chunk["balance"] > 1e-7:
                return chunk
        return None

    def build_steady_states(self):
        inventory = deepcopy(self._inventory_template)
        period_boundaries = self._period_boundaries()
        states = []
        produced_tonnes = 0.0

        for sequence_row in self.sequence_rows:
            blend_id = sequence_row["Blend ID"]
            blend = self.blends.get(blend_id)
            if blend is None:
                raise ManualBlendPlanningError(
                    f"Blend ID {blend_id} has no saved blend definition."
                )
            current = sequence_row["_start"]
            blend_end = sequence_row["_end"]

            if sequence_row.get("_fixed_steady_state"):
                duration_hours = (
                    blend_end - current
                ).total_seconds() / 3600
                state_crusher_rate = self._number(
                    sequence_row.get("_crusher_rate"),
                    self._crusher_rate_for(current),
                )
                if state_crusher_rate <= 0:
                    state_crusher_rate = self._crusher_rate_for(current)
                feed_tonnes = duration_hours * state_crusher_rate
                states.append({
                    "steady_state_number": len(states) + 1,
                    "state_key": self.state_key(
                        current, blend_end, blend_id
                    ),
                    "blend_ID": blend_id,
                    "start_datetime": current,
                    "end_datetime": blend_end,
                    "steady_state_duration": duration_hours,
                    "period": self._period_number(current),
                    "trigger": "Optimised decision point",
                    "feed_capacity_tonnes": feed_tonnes,
                    "crusher_rate": state_crusher_rate,
                    "optimised_steady_state_number": sequence_row.get(
                        "_optimised_steady_state"
                    ),
                })
                produced_tonnes += feed_tonnes
                continue

            while current < blend_end - timedelta(microseconds=1):
                current_crusher_rate = self._crusher_rate_for(current)
                candidates = [(blend_end, "Blend completion")]
                for boundary in period_boundaries:
                    if current < boundary < blend_end:
                        candidates.append((boundary, "Period boundary"))

                remaining_build = self._build_completion_distance(
                    produced_tonnes
                )
                if remaining_build is not None:
                    build_end = current + timedelta(
                        hours=remaining_build / current_crusher_rate
                    )
                    if current < build_end < blend_end:
                        candidates.append(
                            (build_end, "Product build completion")
                        )

                for source, ratio in zip(
                    blend["_sources"], blend["_ratios"]
                ):
                    if ratio <= 0:
                        continue
                    chunk = self._current_chunk(inventory.get(source))
                    if chunk is None:
                        raise ManualBlendPlanningError(
                            f"{source} does not have enough inventory for "
                            f"the scheduled duration of Blend {blend_id}."
                        )
                    depletion_end = current + timedelta(
                        hours=chunk["balance"] / (
                            current_crusher_rate * ratio
                        )
                    )
                    if current < depletion_end < blend_end:
                        label = (
                            "AMT chunk turnover"
                            if source in self._amt_sources
                            else "ROM stockpile depletion"
                        )
                        candidates.append((depletion_end, label))

                state_end, trigger = min(
                    candidates, key=lambda value: value[0]
                )
                duration_hours = (
                    state_end - current
                ).total_seconds() / 3600
                feed_tonnes = duration_hours * current_crusher_rate
                state = {
                    "steady_state_number": len(states) + 1,
                    "state_key": self.state_key(
                        current, state_end, blend_id
                    ),
                    "blend_ID": blend_id,
                    "start_datetime": current,
                    "end_datetime": state_end,
                    "steady_state_duration": duration_hours,
                    "period": self._period_number(current),
                    "trigger": trigger,
                    "feed_capacity_tonnes": feed_tonnes,
                    "crusher_rate": current_crusher_rate,
                }
                states.append(state)

                for source, ratio in zip(
                    blend["_sources"], blend["_ratios"]
                ):
                    self._consume_inventory(
                        inventory, source, feed_tonnes * ratio
                    )
                produced_tonnes += feed_tonnes
                current = state_end

        self.attach_direct_tip_candidates(states)
        return states

    @staticmethod
    def _truthy(value):
        return str(value).strip().lower() in {
            "true", "1", "yes", "y", "on"
        }

    def attach_direct_tip_candidates(self, states):
        for state in states:
            state["direct_tip_candidates"] = []
        if self.payload_transactions.empty or not states:
            return states

        payloads = self.payload_transactions.copy()
        if "direct_tip_eligible" not in payloads.columns:
            return states
        payloads = payloads[
            payloads["direct_tip_eligible"].map(self._truthy)
        ].copy()
        if payloads.empty:
            return states
        payloads["_delivered"] = pd.to_datetime(
            payloads.get("delivered_datetime"), errors="coerce"
        )
        payloads["payload"] = pd.to_numeric(
            payloads.get("payload"), errors="coerce"
        ).fillna(0)

        for state in states:
            rows = payloads[
                (payloads["_delivered"] >= state["start_datetime"])
                & (payloads["_delivered"] < state["end_datetime"])
            ]
            if rows.empty:
                continue
            for source, group in rows.groupby("source", dropna=False):
                tonnes = float(group["payload"].sum())
                if tonnes <= 0:
                    continue
                candidate = {
                    "source": str(source or "Unknown grade block"),
                    "available_tonnes": tonnes,
                    "payload_count": len(group),
                    "direct_tip_ids": [
                        str(value) for value in
                        group.get(
                            "direct_tip_id",
                            pd.Series(index=group.index, dtype=object),
                        ).dropna().tolist()
                    ],
                }
                weighted_streams = None
                weighted_properties = {}
                accumulated_tonnes = 0.0
                for _, payload_row in group.iterrows():
                    row_tonnes = max(self._number(payload_row.get("payload")), 0)
                    streams = payload_row.get("grade_streams")
                    if not isinstance(streams, Mapping):
                        raw_json = payload_row.get("grade_streams_json")
                        if isinstance(raw_json, str) and raw_json.strip():
                            try:
                                streams = json.loads(raw_json)
                            except (TypeError, ValueError):
                                streams = None
                    if not isinstance(streams, Mapping):
                        streams = legacy_grade_streams({
                            f"grade_{grade}": payload_row.get(
                                f"source_grade_{grade}"
                            )
                            for grade in self.GRADES
                        })
                    incoming_properties = filter_source_properties(
                        source_properties_from_mapping(payload_row),
                        self.required_source_property_keys,
                    )
                    weighted_streams = weighted_merge_grade_streams(
                        weighted_streams,
                        accumulated_tonnes,
                        streams,
                        row_tonnes,
                        weighted_properties,
                        incoming_properties,
                        self.source_property_weights,
                    )
                    weighted_properties = merge_source_properties(
                        weighted_properties,
                        accumulated_tonnes,
                        incoming_properties,
                        row_tonnes,
                        self.source_property_kinds,
                        self.source_property_weights,
                    )
                    accumulated_tonnes += row_tonnes
                candidate["grade_streams"] = (
                    reweight_grade_streams_from_properties(
                        weighted_streams, weighted_properties
                    )
                )
                candidate["source_properties"] = weighted_properties
                for grade in self.GRADES:
                    values = pd.to_numeric(
                        group.get(
                            f"source_grade_{grade}",
                            pd.Series(0, index=group.index),
                        ),
                        errors="coerce",
                    ).fillna(0)
                    candidate[f"grade_{grade}"] = float(
                        (values * group["payload"]).sum() / tonnes
                    )
                state["direct_tip_candidates"].append(candidate)
        return states

    def _consume_inventory(self, inventory, source, requested_tonnes):
        remaining = max(self._number(requested_tonnes), 0)
        consumed = []
        for chunk in inventory.get(source, []):
            if remaining <= 1e-7:
                break
            opening = chunk["balance"]
            amount = min(opening, remaining)
            if amount > 0:
                chunk["balance"] -= amount
                consumed.append((chunk, opening, amount))
                remaining -= amount
        if remaining > 1e-5:
            raise ManualBlendPlanningError(
                f"{source} is short by {remaining:,.1f} t for the manual "
                "blend sequence."
            )
        return consumed

    @staticmethod
    def _allocation_for_state(allocations, state):
        state_values = (allocations or {}).get(state["state_key"], {}) or {}
        return {
            str(source): max(float(tonnes or 0), 0)
            for source, tonnes in state_values.items()
        }

    def validate_allocations(self, states, allocations):
        for state in states:
            selected = self._allocation_for_state(allocations, state)
            available = {
                candidate["source"]: candidate["available_tonnes"]
                for candidate in state.get("direct_tip_candidates", [])
            }
            for source, tonnes in selected.items():
                if source not in available and tonnes > 1e-7:
                    raise ManualBlendPlanningError(
                        f"{source} is not an available direct-tip source in "
                        f"steady state {state['steady_state_number']}."
                    )
                if tonnes > available.get(source, 0) + 1e-7:
                    raise ManualBlendPlanningError(
                        f"Selected direct tip for {source} exceeds its "
                        f"available {available.get(source, 0):,.1f} t."
                    )
            total = sum(selected.values())
            if total > state["feed_capacity_tonnes"] + 1e-7:
                raise ManualBlendPlanningError(
                    f"Direct tip in steady state "
                    f"{state['steady_state_number']} exceeds the total "
                    "crusher feed capacity."
                )
            blend = self.blends.get(state["blend_ID"], {})
            if (
                not blend.get("_sources")
                and abs(total - state["feed_capacity_tonnes"]) > 1e-5
            ):
                raise ManualBlendPlanningError(
                    f"Steady state {state['steady_state_number']} is a "
                    "direct-tip-only optimized state. Its accepted direct-tip "
                    "tonnes must remain at 100% of crusher feed."
                )
        return True

    def _active_build(self, produced_tonnes):
        running = 0.0
        for setting in self.product_build_settings:
            running += max(self._number(setting.get("target_tonnes")), 0)
            if produced_tonnes < running - 1e-7:
                return setting
        return {}

    def _active_brand(self, produced_tonnes, state):
        build_brand = str(
            self._active_build(produced_tonnes).get("brand") or ""
        ).strip().upper()
        if build_brand:
            return build_brand
        period = int(state.get("period", 0) or 0)
        period_name = (
            self.period_labels[period]
            if 0 <= period < len(self.period_labels)
            else "Preplan"
        )
        calendar_brands = self.calendar_inputs.get("crusher_brand", {})
        if isinstance(calendar_brands, Mapping):
            return str(calendar_brands.get(period_name) or "").strip().upper()
        return ""

    def _selected_source_fields(self, streams, brand, fallback=None):
        streams = normalise_grade_streams(streams, fallback or {})
        selected, warnings = resolve_grade_vector(
            streams,
            self.selected_data_stream,
            brand,
            fallback or {},
        )
        return {
            **{
                f"source_grade_{grade}": selected[grade]
                for grade in self.GRADES
            },
            "selected_grade_stream": self.selected_data_stream,
            "selected_grade_brand": normalise_brand(brand),
            "grade_stream_warnings": json.dumps(
                warnings, separators=(",", ":")
            ) if warnings else "",
            **grade_stream_audit_fields(
                streams, brand, prefix="source_grade_"
            ),
        }

    def _target_values(self, period):
        result = {}
        period_name = (
            self.period_labels[period]
            if 0 <= period < len(self.period_labels)
            else "Preplan"
        )
        for grade in self.GRADES:
            for bound in ("min", "max"):
                value = None
                calendar_values = self.calendar_inputs.get(
                    f"crusher_target_{grade}_{bound}", {}
                )
                if isinstance(calendar_values, Mapping):
                    value = calendar_values.get(period_name)
                result[
                    f"crusher_grade_target_{bound}_{grade}"
                ] = self._number(
                    value, 0.0 if bound == "min" else 100.0
                )
        return result

    def _custom_constraint_bounds(self, definition, period):
        period_name = (
            self.period_labels[period]
            if 0 <= period < len(self.period_labels)
            else "Preplan"
        )
        key = constraint_key(definition.get("key") or definition.get("name"))

        def bound(suffix):
            values = self.calendar_inputs.get(
                f"crusher_custom_constraint_{key}_{suffix}", {}
            )
            value = values.get(period_name) if isinstance(values, Mapping) else None
            if value in (None, ""):
                return None
            try:
                return float(value)
            except (TypeError, ValueError) as error:
                raise ManualBlendPlanningError(
                    f"{definition['name']} {suffix.title()} must be numeric or blank."
                ) from error

        return bound("min"), bound("max")

    def _custom_constraint_fields(self, source_rows, period):
        report_fields = {}
        for definition in self.custom_constraints:
            if not definition.get("enabled", True):
                continue
            numerator_expression = SafeNumericExpression(
                definition.get("numerator")
            )
            denominator_expression = SafeNumericExpression(
                definition.get("denominator") or "one"
            )
            numerator_total = 0.0
            denominator_total = 0.0
            key = constraint_key(
                definition.get("key") or definition.get("name")
            )
            for source_row in source_rows:
                try:
                    fields = mapping_constraint_fields(
                        source_row, self.source_property_kinds
                    )
                    numerator = numerator_expression.evaluate(fields)
                    denominator = denominator_expression.evaluate(fields)
                except CustomConstraintError as error:
                    raise ManualBlendPlanningError(
                        f"{definition['name']}: {source_row.get('source')}: {error}"
                    ) from error
                if denominator < 0:
                    raise ManualBlendPlanningError(
                        f"{definition['name']}: {source_row.get('source')}: "
                        "denominator expression must be non-negative."
                    )
                tonnes = self._number(source_row.get("source_actual_tonnes"))
                numerator_total += numerator * tonnes
                denominator_total += denominator * tonnes
                source_row[
                    f"custom_constraint_{key}_source_numerator_coefficient"
                ] = numerator
                source_row[
                    f"custom_constraint_{key}_source_denominator_coefficient"
                ] = denominator
                source_row[
                    f"custom_constraint_{key}_source_numerator_contribution"
                ] = numerator * tonnes
                source_row[
                    f"custom_constraint_{key}_source_denominator_contribution"
                ] = denominator * tonnes
            minimum, maximum = self._custom_constraint_bounds(
                definition, period
            )
            report_fields.update(constraint_report_fields(
                definition,
                numerator_total,
                denominator_total,
                minimum,
                maximum,
            ))
        return report_fields

    def custom_constraint_report_columns(self):
        columns = []
        for definition in self.custom_constraints:
            if not definition.get("enabled", True):
                continue
            prefix = "custom_constraint_" + constraint_key(
                definition.get("key") or definition.get("name")
            )
            columns.extend([
                f"{prefix}_name",
                f"{prefix}_numerator_expression",
                f"{prefix}_denominator_expression",
                f"{prefix}_numerator",
                f"{prefix}_denominator",
                f"{prefix}_actual_ratio",
                f"{prefix}_target_min",
                f"{prefix}_target_max",
                f"{prefix}_source_numerator_coefficient",
                f"{prefix}_source_denominator_coefficient",
                f"{prefix}_source_numerator_contribution",
                f"{prefix}_source_denominator_contribution",
            ])
        return columns

    def build_report(self, states, allocations=None):
        self.validate_allocations(states, allocations or {})
        inventory = deepcopy(self._inventory_template)
        report_rows = []
        produced_tonnes = 0.0

        for state in states:
            blend = self.blends[state["blend_ID"]]
            selected = self._allocation_for_state(allocations, state)
            direct_tip_tonnes = sum(selected.values())
            total_tonnes = state["feed_capacity_tonnes"]
            stockpile_tonnes = total_tonnes - direct_tip_tonnes
            source_rows = []
            active_brand = self._active_brand(produced_tonnes, state)

            for source, ratio in zip(
                blend["_sources"], blend["_ratios"]
            ):
                amount = stockpile_tonnes * ratio
                source_opening = sum(
                    chunk["balance"]
                    for chunk in inventory.get(source, [])
                )
                consumed = self._consume_inventory(
                    inventory, source, amount
                )
                source_closing = sum(
                    chunk["balance"]
                    for chunk in inventory.get(source, [])
                )
                source_row = {
                    "source": source,
                    "source_id": ", ".join(
                        item[0]["source_id"] for item in consumed
                    ) or source,
                    "source_type": "stockpile",
                    "source_opening_balance": source_opening,
                    "source_actual_tonnes": amount,
                    "constraint_source_balance": amount,
                    "source_closing_balance": source_closing,
                    "equipment": "RC",
                    "is_amt": source in self._amt_sources,
                }
                source_streams = None
                source_properties = {}
                accumulated_tonnes = 0.0
                for chunk, opening, consumed_tonnes in consumed:
                    consumed_properties = scale_additive_source_properties(
                        chunk.get("source_properties"),
                        consumed_tonnes / opening if opening > 0 else 0,
                        self.source_property_kinds,
                    )
                    source_streams = weighted_merge_grade_streams(
                        source_streams,
                        accumulated_tonnes,
                        chunk.get("grade_streams"),
                        consumed_tonnes,
                        source_properties,
                        consumed_properties,
                        self.source_property_weights,
                    )
                    source_properties = merge_source_properties(
                        source_properties,
                        accumulated_tonnes,
                        consumed_properties,
                        consumed_tonnes,
                        self.source_property_kinds,
                        self.source_property_weights,
                    )
                    accumulated_tonnes += consumed_tonnes
                source_streams = reweight_grade_streams_from_properties(
                    source_streams, source_properties
                )
                source_row["source_properties"] = source_properties
                legacy = {
                    f"grade_{grade}": (
                        sum(
                            item[0][f"grade_{grade}"] * item[2]
                            for item in consumed
                        ) / amount
                        if amount > 0 else 0
                    )
                    for grade in self.GRADES
                }
                source_row.update(
                    self._selected_source_fields(
                        source_streams, active_brand, legacy
                    )
                )
                source_rows.append(source_row)

            candidates = {
                row["source"]: row
                for row in state.get("direct_tip_candidates", [])
            }
            for source, amount in selected.items():
                if amount <= 0:
                    continue
                candidate = candidates[source]
                source_row = {
                    "source": source,
                    "source_id": ", ".join(
                        candidate.get("direct_tip_ids", [])
                    ) or source,
                    "source_type": "grade_block",
                    "source_opening_balance": candidate[
                        "available_tonnes"
                    ],
                    "source_actual_tonnes": amount,
                    "constraint_source_balance": amount,
                    "source_closing_balance": (
                        candidate["available_tonnes"] - amount
                    ),
                    "equipment": "EX",
                    "is_amt": False,
                    "source_properties": scale_additive_source_properties(
                        candidate.get("source_properties") or {},
                        amount / candidate["available_tonnes"]
                        if candidate["available_tonnes"] > 0 else 0,
                        self.source_property_kinds,
                    ),
                }
                source_row.update(self._selected_source_fields(
                    candidate.get("grade_streams"),
                    active_brand,
                    {
                        f"grade_{grade}": candidate[f"grade_{grade}"]
                        for grade in self.GRADES
                    },
                ))
                source_rows.append(source_row)

            # The physical source balance remains ROM WMT.  The selected
            # streams control the independently reported crusher and product
            # quantities, exactly as in the optimiser.
            for row in source_rows:
                physical = self._number(row.get("source_actual_tonnes"))
                properties = row.get("source_properties") or {}
                def mapped_tonnes(stream):
                    value = self._number(properties.get(stream), 0.0)
                    return value if value > 1e-9 else physical
                row["crusher_source_tonnes"] = mapped_tonnes(self.crusher_tonnes_stream)
                row["reclaimer_source_tonnes"] = mapped_tonnes(self.reclaimer_tonnes_stream)
                row["product_build_source_tonnes"] = mapped_tonnes(self.product_build_tonnes_stream)
                row["reclaimer_tonnes_stream"] = self.reclaimer_tonnes_stream
                row["crusher_tonnes_stream"] = self.crusher_tonnes_stream
                row["product_build_tonnes_stream"] = self.product_build_tonnes_stream
                try:
                    warnings = json.loads(row.get("grade_stream_warnings") or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    warnings = []
                for grade in self.GRADES:
                    used_stream = self.selected_data_stream
                    for warning in warnings:
                        if str(warning.get("analyte") or "").lower() == grade:
                            used_stream = str(
                                warning.get("used_stream") or used_stream
                            ).lower()
                            break
                    weight_field = self.source_property_weights.get(
                        f"{used_stream}_{grade}"
                    )
                    row[f"selected_grade_weight_{grade}_tonnes"] = (
                        mapped_tonnes(weight_field) if weight_field else physical
                    )
            crusher_tonnes = sum(
                self._number(row.get("crusher_source_tonnes"))
                for row in source_rows
            )
            crusher_grades = {}
            for grade in self.GRADES:
                grade_weight = sum(
                    self._number(row.get(f"selected_grade_weight_{grade}_tonnes"))
                    for row in source_rows
                )
                crusher_grades[grade] = (
                    sum(
                        row[f"source_grade_{grade}"]
                        * row[f"selected_grade_weight_{grade}_tonnes"]
                        for row in source_rows
                    ) / grade_weight
                    if grade_weight > 0 else 0
                )

            direct_tip_ratio = (
                direct_tip_tonnes / total_tonnes
                if total_tonnes > 0 else 0
            )
            targets = self._target_values(state["period"])
            custom_constraint_fields = self._custom_constraint_fields(
                source_rows, state["period"]
            )
            duration = state["steady_state_duration"]

            for source_row in source_rows:
                amount = source_row["source_actual_tonnes"]
                visible_source_properties = filter_source_properties(
                    source_row.get("source_properties"),
                    (self.calendar_inputs.get("solver_config") or {}).get(
                        "optimisation_source_property_fields"
                    ),
                )
                opening_source_properties = scale_additive_source_properties(
                    visible_source_properties,
                    self._number(source_row.get("source_opening_balance")) / amount
                    if amount > 0 else 0,
                    self.source_property_kinds,
                )
                report_rows.append({
                    "start_datetime": state["start_datetime"],
                    "end_datetime": state["end_datetime"],
                    "steady_state_number": state[
                        "steady_state_number"
                    ],
                    "blend_option": "Manual",
                    "blend_ID": state["blend_ID"],
                    "steady_state_duration": duration,
                    "period": state["period"],
                    "actual_direct_tip_ratio": direct_tip_ratio,
                    **source_row,
                    "source_properties": visible_source_properties,
                    **source_property_report_fields(
                        visible_source_properties,
                        property_kinds=self.source_property_kinds,
                        active_fields=(
                            self.calendar_inputs.get("solver_config") or {}
                        ).get("optimisation_source_property_fields"),
                    ),
                    **source_property_balance_report_fields(
                        opening_source_properties,
                        visible_source_properties,
                        active_fields=(
                            self.calendar_inputs.get("solver_config") or {}
                        ).get("optimisation_source_property_fields"),
                        property_kinds=self.source_property_kinds,
                    ),
                    "source_blend_ratio": (
                        source_row.get("crusher_source_tonnes", 0) / crusher_tonnes if crusher_tonnes > 0 else 0
                    ),
                    "equipment_rate_input": (
                        amount / duration if duration > 0 else 0
                    ),
                    "equipment_rate_output": (
                        self._number(source_row.get("reclaimer_source_tonnes")) / duration if duration > 0 else 0
                    ),
                    "crusher_actual_tonnes": crusher_tonnes,
                    "product_build_actual_tonnes": sum(
                        self._number(item.get("product_build_source_tonnes"))
                        for item in source_rows
                    ),
                    "crusher_rate_input": state.get(
                        "crusher_rate", self.crusher_rate
                    ),
                    "crusher_rate_output": (
                        crusher_tonnes / duration if duration > 0 else 0
                    ),
                    **{
                        f"crusher_actual_grade_{grade}": value
                        for grade, value in crusher_grades.items()
                    },
                    **targets,
                    **custom_constraint_fields,
                })
            produced_tonnes += total_tonnes

        custom_columns = self.custom_constraint_report_columns()
        source_property_columns = sorted({
            column
            for row in report_rows
            for column in row
            if str(column).startswith("source_property_")
        })
        base_columns = [
            column
            for column in self.REPORT_COLUMNS
            if column not in ProductBuildProgress.COLUMNS
        ]
        report = pd.DataFrame(
            report_rows,
            columns=[
                *base_columns,
                *source_property_columns,
                *custom_columns,
            ],
        )
        report = ProductBuildProgress.annotate(
            report, self.product_build_settings
        )
        return report.reindex(columns=[
            *base_columns,
            *source_property_columns,
            *custom_columns,
            *ProductBuildProgress.COLUMNS,
        ])

    def state_summaries(self, states, allocations=None):
        report = self.build_report(states, allocations or {})
        summaries = {}
        for state in states:
            number = state["steady_state_number"]
            rows = report[report["steady_state_number"] == number]
            selected = self._allocation_for_state(allocations, state)
            direct_tip = sum(selected.values())
            capacity = state["feed_capacity_tonnes"]
            summary = {
                **state,
                "direct_tip_tonnes": direct_tip,
                "stockpile_feed_tonnes": capacity - direct_tip,
                "direct_tip_ratio": (
                    direct_tip / capacity if capacity > 0 else 0
                ),
            }
            for grade in self.GRADES:
                summary[f"output_grade_{grade}"] = (
                    self._number(rows.iloc[0][
                        f"crusher_actual_grade_{grade}"
                    ]) if not rows.empty else 0
                )
            summaries[state["state_key"]] = summary
        return summaries
