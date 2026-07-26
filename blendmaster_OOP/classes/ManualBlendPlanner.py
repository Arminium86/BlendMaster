from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Mapping, Optional

import pandas as pd


class ManualBlendPlanningError(ValueError):
    """A user-correctable error in a manual blend plan."""


class ManualBlendPlanner:
    """
    Build manual steady states and a source-level manual blend report.

    Payload delivery times only determine which direct-tip candidates are
    visible inside a steady state. They never create a steady-state boundary.
    """

    GRADES = ("fe", "si", "al", "p", "mn")
    REPORT_COLUMNS = [
        "start_datetime", "end_datetime", "steady_state_number",
        "blend_option", "blend_ID", "steady_state_duration", "period",
        "actual_direct_tip_ratio", "source", "source_id", "source_type",
        "source_blend_ratio", "source_opening_balance",
        "source_actual_tonnes", "source_closing_balance",
        "source_grade_fe", "source_grade_si", "source_grade_al",
        "source_grade_p", "source_grade_mn", "equipment",
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
    ]

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
        self.crusher_rate = self._positive_number(
            crusher_rate, "Crusher rate"
        )
        self._inventory_template = self._build_inventory()

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
            chunks.setdefault(footprint, []).append(chunk)

        for footprint in chunks:
            chunks[footprint].sort(
                key=lambda row: (row["sequence"], row["source_id"])
            )

        for name, values in self.stockpile_data.items():
            if name in amt_footprints:
                continue
            chunk = {
                "source_id": name,
                "sequence": 1,
                "balance": max(self._number(values.get("balance")), 0),
            }
            for grade in self.GRADES:
                chunk[f"grade_{grade}"] = self._number(
                    values.get(f"grade_{grade}")
                )
            chunks[name] = [chunk]
        self._amt_sources = amt_footprints
        return chunks

    def _period_boundaries(self):
        values = []
        for key in (
            "preplan_end", "period_1_start", "period_1_end",
            "period_2_start", "period_2_end",
        ):
            value = self.periods.get(key)
            if value is not None:
                values.append(self._datetime(value, key))
        return sorted(set(values))

    def _period_number(self, at_time):
        period_1_start = self.periods.get("period_1_start")
        period_2_start = self.periods.get("period_2_start")
        if period_2_start is not None and at_time >= self._datetime(
            period_2_start, "period_2_start"
        ):
            return 2
        if period_1_start is not None and at_time >= self._datetime(
            period_1_start, "period_1_start"
        ):
            return 1
        return 0

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
                    self.crusher_rate,
                )
                if state_crusher_rate <= 0:
                    state_crusher_rate = self.crusher_rate
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
                candidates = [(blend_end, "Blend completion")]
                for boundary in period_boundaries:
                    if current < boundary < blend_end:
                        candidates.append((boundary, "Period boundary"))

                remaining_build = self._build_completion_distance(
                    produced_tonnes
                )
                if remaining_build is not None:
                    build_end = current + timedelta(
                        hours=remaining_build / self.crusher_rate
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
                            self.crusher_rate * ratio
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
                feed_tonnes = duration_hours * self.crusher_rate
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
                    "crusher_rate": self.crusher_rate,
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

    def _target_values(self, build, period):
        result = {}
        period_name = {
            0: "Preplan", 1: "Period_1", 2: "Period_2"
        }.get(period, "Preplan")
        for grade in self.GRADES:
            for bound in ("min", "max"):
                value = build.get(f"target_{grade}_{bound}")
                if value is None:
                    calendar_values = self.calendar_inputs.get(
                        f"crusher_target_{grade}_{bound}", {}
                    )
                    if isinstance(calendar_values, Mapping):
                        value = calendar_values.get(period_name)
                result[
                    f"crusher_grade_target_{bound}_{grade}"
                ] = self._number(value)
        return result

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
                    "source_closing_balance": source_closing,
                    "equipment": "RC",
                }
                for grade in self.GRADES:
                    source_row[f"source_grade_{grade}"] = (
                        sum(
                            item[0][f"grade_{grade}"] * item[2]
                            for item in consumed
                        ) / amount
                        if amount > 0 else 0
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
                    "source_closing_balance": (
                        candidate["available_tonnes"] - amount
                    ),
                    "equipment": "EX",
                }
                for grade in self.GRADES:
                    source_row[f"source_grade_{grade}"] = candidate[
                        f"grade_{grade}"
                    ]
                source_rows.append(source_row)

            crusher_grades = {}
            for grade in self.GRADES:
                crusher_grades[grade] = (
                    sum(
                        row[f"source_grade_{grade}"]
                        * row["source_actual_tonnes"]
                        for row in source_rows
                    ) / total_tonnes
                    if total_tonnes > 0 else 0
                )

            direct_tip_ratio = (
                direct_tip_tonnes / total_tonnes
                if total_tonnes > 0 else 0
            )
            build = self._active_build(produced_tonnes)
            targets = self._target_values(build, state["period"])
            duration = state["steady_state_duration"]

            for source_row in source_rows:
                amount = source_row["source_actual_tonnes"]
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
                    "source_blend_ratio": (
                        amount / total_tonnes if total_tonnes > 0 else 0
                    ),
                    "equipment_rate_input": (
                        amount / duration if duration > 0 else 0
                    ),
                    "equipment_rate_output": (
                        amount / duration if duration > 0 else 0
                    ),
                    "crusher_actual_tonnes": total_tonnes,
                    "crusher_rate_input": state.get(
                        "crusher_rate", self.crusher_rate
                    ),
                    "crusher_rate_output": state.get(
                        "crusher_rate", self.crusher_rate
                    ),
                    **{
                        f"crusher_actual_grade_{grade}": value
                        for grade, value in crusher_grades.items()
                    },
                    **targets,
                })
            produced_tonnes += total_tonnes

        return pd.DataFrame(report_rows, columns=self.REPORT_COLUMNS)

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
