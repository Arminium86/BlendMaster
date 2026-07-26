from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

import pandas as pd

from classes.ManualBlendPlanner import ManualBlendPlanningError


class OptimisedToManualPlan:
    """Convert a selected optimized result into editable manual-plan inputs."""

    TOLERANCE = 1e-7
    GRADES = ("fe", "si", "al", "p", "mn")

    def __init__(self, optimised_report, stockpile_data=None):
        self.report = (
            optimised_report.copy()
            if isinstance(optimised_report, pd.DataFrame)
            else pd.DataFrame(optimised_report or [])
        )
        self.stockpile_data = {
            str(name).strip().upper(): deepcopy(values or {})
            for name, values in (stockpile_data or {}).items()
        }

    @staticmethod
    def _number(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _source_type(row):
        value = str(row.get("source_type") or "").strip().lower()
        equipment = str(row.get("equipment") or "").strip().upper()
        if value in {"grade_block", "grade block", "gradeblock"}:
            return "grade_block"
        if value == "stockpile":
            return "stockpile"
        if equipment.startswith("EX"):
            return "grade_block"
        if equipment.startswith("RC"):
            return "stockpile"
        return value

    @staticmethod
    def _identifier_values(value):
        if value is None:
            return []
        try:
            if pd.isna(value):
                return []
        except (TypeError, ValueError):
            pass
        return [
            part.strip()
            for part in str(value).split(",")
            if part.strip()
        ]

    def _prepared_report(self):
        required = {
            "start_datetime", "end_datetime", "steady_state_number",
            "source", "source_actual_tonnes",
        }
        missing = required.difference(self.report.columns)
        if missing:
            raise ManualBlendPlanningError(
                "optimised_blend_report is missing: "
                + ", ".join(sorted(missing))
            )

        data = self.report.copy()
        data["_start"] = pd.to_datetime(
            data["start_datetime"], errors="coerce"
        )
        data["_end"] = pd.to_datetime(
            data["end_datetime"], errors="coerce"
        )
        data["_tonnes"] = pd.to_numeric(
            data["source_actual_tonnes"], errors="coerce"
        ).fillna(0)
        data["_source_type"] = data.apply(self._source_type, axis=1)
        data = data[
            data["_start"].notna()
            & data["_end"].notna()
            & (data["_end"] > data["_start"])
            & (data["_tonnes"] > self.TOLERANCE)
        ].copy()
        if data.empty:
            raise ManualBlendPlanningError(
                "The optimized result does not contain any positive crusher "
                "feed to prepopulate."
            )
        return data.sort_values(
            ["_start", "_end", "steady_state_number"],
            kind="stable",
        ).reset_index(drop=True)

    @staticmethod
    def _state_group_columns(data):
        columns = [
            "steady_state_number", "_start", "_end"
        ]
        if "blend_option" in data.columns:
            columns.append("blend_option")
        return columns

    def _stockpile_pattern(self, stockpile_rows):
        tonnes_by_source = (
            stockpile_rows.groupby("_source_name", sort=True)["_tonnes"]
            .sum()
        )
        total = float(tonnes_by_source.sum())
        if total <= self.TOLERANCE:
            return None
        sources = tuple(tonnes_by_source.index.tolist())
        ratios = tuple(
            float(tonnes_by_source[source] / total)
            for source in sources
        )
        signature = tuple(
            (source, round(ratio, 6))
            for source, ratio in zip(sources, ratios)
        )
        return {
            "sources": sources,
            "ratios": ratios,
            "signature": signature,
        }

    def _patterns_by_optimised_blend(self, data):
        result = defaultdict(list)
        if "blend_ID" not in data.columns:
            return result
        stockpiles = data[data["_source_type"] == "stockpile"]
        for blend_id, group in stockpiles.groupby(
            "blend_ID", sort=False, dropna=False
        ):
            pattern = self._stockpile_pattern(group)
            if pattern:
                result[str(blend_id)].append(pattern)
        return result

    @staticmethod
    def _period_name(value):
        text = str(value or "").strip().lower().replace(" ", "_")
        if text in {"0", "preplan", "pre_plan"}:
            return "Preplan"
        if text in {"1", "period_1", "period1"}:
            return "Period_1"
        if text in {"2", "period_2", "period2"}:
            return "Period_2"
        return "Preplan"

    def build(self):
        data = self._prepared_report()
        data["_source_name"] = (
            data["source"].astype(str).str.strip().str.upper()
        )
        states = []
        pattern_ids = {}
        direct_tip_rows = []
        blend_groups = defaultdict(list)
        period_totals = defaultdict(
            lambda: {"tonnes": 0.0, "hours": 0.0}
        )

        grouped = data.groupby(
            self._state_group_columns(data),
            sort=False,
            dropna=False,
        )
        for group_key, group in grouped:
            first = group.iloc[0]
            start = first["_start"].to_pydatetime()
            end = first["_end"].to_pydatetime()
            duration = (end - start).total_seconds() / 3600
            crusher_tonnes = self._number(
                first.get("crusher_actual_tonnes")
            )
            state_crusher_rate = self._number(
                first.get("crusher_rate_output")
            )
            if state_crusher_rate <= self.TOLERANCE:
                state_crusher_rate = (
                    crusher_tonnes / duration
                    if duration > self.TOLERANCE
                    and crusher_tonnes > self.TOLERANCE
                    else 0
                )
            stockpile_rows = group[
                group["_source_type"] == "stockpile"
            ].copy()
            pattern = self._stockpile_pattern(stockpile_rows)
            if pattern is None:
                pattern = {
                    "sources": (),
                    "ratios": (),
                    "signature": (),
                }

            grade_blocks = group[
                group["_source_type"] == "grade_block"
            ]
            direct_tip_state_tonnes = float(
                grade_blocks["_tonnes"].sum()
            )
            original_blend_id = str(
                first.get("blend_ID") or ""
            ).strip()
            if original_blend_id:
                blend_key = ("optimised_blend", original_blend_id)
                manual_blend_id = original_blend_id
            else:
                direct_tip_sources = tuple(sorted(
                    grade_blocks["_source_name"].unique().tolist()
                ))
                blend_key = (
                    "derived",
                    pattern["signature"],
                    direct_tip_sources,
                )
                if blend_key not in pattern_ids:
                    pattern_ids[blend_key] = str(len(pattern_ids) + 1)
                manual_blend_id = pattern_ids[blend_key]

            if crusher_tonnes <= self.TOLERANCE:
                crusher_tonnes = float(group["_tonnes"].sum())
            if state_crusher_rate <= self.TOLERANCE:
                state_crusher_rate = (
                    crusher_tonnes / duration
                    if duration > self.TOLERANCE else 0
                )
            stockpile_rows["_state_duration"] = duration
            if "equipment_rate_output" in stockpile_rows:
                stockpile_rows["_reported_reclaim_rate"] = pd.to_numeric(
                    stockpile_rows["equipment_rate_output"],
                    errors="coerce",
                ).fillna(0)
            else:
                stockpile_rows["_reported_reclaim_rate"] = (
                    stockpile_rows["_tonnes"] / duration
                    if duration > self.TOLERANCE else 0
                )

            period_name = self._period_name(first.get("period"))
            period_totals[period_name]["tonnes"] += (
                state_crusher_rate * duration
            )
            period_totals[period_name]["hours"] += duration
            blend_groups[manual_blend_id].append({
                "duration": duration,
                "crusher_tonnes": crusher_tonnes,
                "crusher_rate": state_crusher_rate,
                "period": period_name,
                "stockpile_rows": stockpile_rows,
            })

            states.append({
                "Blend ID": manual_blend_id,
                "Origin": "Optimised",
                "Start Datetime": start.strftime("%Y-%m-%d %H:%M"),
                "Duration (hrs)": round(duration, 1),
                "End Datetime": end.strftime("%Y-%m-%d %H:%M"),
                "Early Start Flag": "On Time",
                "Remaining Hrs": 0,
                "_optimised_steady_state": first[
                    "steady_state_number"
                ],
                "_fixed_steady_state": True,
                "_crusher_rate": state_crusher_rate,
                "_period_name": period_name,
                "_exact_start": start.strftime("%Y-%m-%d %H:%M:%S"),
                "_exact_end": end.strftime("%Y-%m-%d %H:%M:%S"),
                "Direct Tip Tonnes": direct_tip_state_tonnes,
                "Direct Tip Ratio": (
                    direct_tip_state_tonnes / crusher_tonnes
                    if crusher_tonnes > self.TOLERANCE else 0
                ),
            })

            for _, row in grade_blocks.iterrows():
                direct_tip_rows.append({
                    "optimised_steady_state": row[
                        "steady_state_number"
                    ],
                    "start_datetime": start,
                    "end_datetime": end,
                    "source": str(row.get("source") or "").strip(),
                    "source_ids": self._identifier_values(
                        row.get("source_id")
                    ),
                    "selected_tonnes": float(row["_tonnes"]),
                })

        definitions = []
        config_inputs = {}

        def blend_sort_key(value):
            try:
                return (0, int(value))
            except (TypeError, ValueError):
                return (1, str(value))

        for blend_id in sorted(blend_groups, key=blend_sort_key):
            state_groups = blend_groups[blend_id]
            total_duration = sum(
                item["duration"] for item in state_groups
            )
            source_rows = pd.concat(
                [item["stockpile_rows"] for item in state_groups],
                ignore_index=True,
            )
            tonnes_by_source = (
                source_rows.groupby("_source_name", sort=True)["_tonnes"]
                .sum()
            )
            sources = tonnes_by_source.index.tolist()
            blend_crusher_rate = (
                sum(
                    item["crusher_rate"] * item["duration"]
                    for item in state_groups
                ) / total_duration
                if total_duration > self.TOLERANCE else 0
            )
            reclaim_rates = []
            for source in sources:
                rows = source_rows[
                    source_rows["_source_name"] == source
                ]
                source_hours = float(rows["_state_duration"].sum())
                reclaim_rates.append(
                    float(
                        (
                            rows["_reported_reclaim_rate"]
                            * rows["_state_duration"]
                        ).sum() / source_hours
                    )
                    if source_hours > self.TOLERANCE else 0
                )
            source_ratios = [
                (
                    rate / blend_crusher_rate
                    if blend_crusher_rate > self.TOLERANCE else 0
                )
                for rate in reclaim_rates
            ]
            stockpile_rate = sum(reclaim_rates)
            stockpile_mix = [
                (
                    rate / stockpile_rate
                    if stockpile_rate > self.TOLERANCE else 0
                )
                for rate in reclaim_rates
            ]
            grades = {}
            for grade in self.GRADES:
                grade_column = f"source_grade_{grade}"
                if grade_column not in source_rows:
                    grades[grade] = 0.0
                    continue
                values = pd.to_numeric(
                    source_rows[grade_column], errors="coerce"
                ).fillna(0)
                tonnes = source_rows["_tonnes"]
                grades[grade] = (
                    float((values * tonnes).sum() / tonnes.sum())
                    if tonnes.sum() > self.TOLERANCE else 0.0
                )

            balances = [
                self._number(
                    self.stockpile_data.get(source, {}).get("balance")
                )
                for source in sources
            ]
            has_amt_source = any(
                bool(self.stockpile_data.get(source, {}).get("amt"))
                for source in sources
            )
            definitions.append({
                "Blend ID": blend_id,
                "Grade Fe": (
                    "AMT" if has_amt_source else f"{grades['fe']:.2f}"
                ),
                "Grade Si": (
                    "AMT" if has_amt_source else f"{grades['si']:.2f}"
                ),
                "Grade Al": (
                    "AMT" if has_amt_source else f"{grades['al']:.2f}"
                ),
                "Grade P": (
                    "AMT" if has_amt_source else f"{grades['p']:.2f}"
                ),
                "Grade Mn": (
                    "AMT" if has_amt_source else f"{grades['mn']:.2f}"
                ),
                "Balance (WMT)": f"{min(balances or [0]):.1f}",
                "Max Duration (hrs)": f"{total_duration:.6f}",
                "Available": "Now",
                "Sources": ", ".join(sources),
                "Source Ratios": ", ".join(
                    f"{ratio:.6f}" for ratio in source_ratios
                ),
            })
            config_inputs[blend_id] = {
                "weights": list(stockpile_mix),
                "sources": list(sources),
                "source_ratios": list(source_ratios),
                "reclaim_rates": list(reclaim_rates),
                "crusher_rate": blend_crusher_rate,
                "max_duration": total_duration,
                "periods": sorted({
                    item["period"] for item in state_groups
                }),
                "rate_mode": "optimised",
            }

        period_crusher_rates = {
            period: (
                values["tonnes"] / values["hours"]
                if values["hours"] > self.TOLERANCE else 0
            )
            for period, values in period_totals.items()
        }
        return {
            "blend_definitions": definitions,
            "blend_config_table_inputs": config_inputs,
            "sequence_rows": states,
            "direct_tip_rows": direct_tip_rows,
            "blend_count": len(definitions),
            "steady_state_count": len(states),
            "direct_tip_tonnes": sum(
                row["selected_tonnes"] for row in direct_tip_rows
            ),
            "period_crusher_rates": period_crusher_rates,
        }

    @classmethod
    def direct_tip_allocations(
        cls, manual_states, direct_tip_rows
    ):
        """
        Place optimized direct-tip tonnes into regenerated manual states.

        Payload IDs are used first. Source and overlapping optimized state
        timing provide compatibility for older reports without source IDs.
        """
        allocations = {}
        allocated_by_candidate = defaultdict(float)

        for selected in direct_tip_rows or []:
            requested = max(
                cls._number(selected.get("selected_tonnes")), 0
            )
            if requested <= cls.TOLERANCE:
                continue
            selected_source = str(
                selected.get("source") or ""
            ).strip().upper()
            selected_ids = {
                str(value).strip()
                for value in selected.get("source_ids", [])
                if str(value).strip()
            }
            selected_start = pd.to_datetime(
                selected.get("start_datetime"), errors="coerce"
            )
            selected_end = pd.to_datetime(
                selected.get("end_datetime"), errors="coerce"
            )

            candidates = []
            for state in manual_states or []:
                state_start = pd.to_datetime(
                    state.get("start_datetime"), errors="coerce"
                )
                state_end = pd.to_datetime(
                    state.get("end_datetime"), errors="coerce"
                )
                if (
                    pd.isna(state_start) or pd.isna(state_end)
                    or pd.isna(selected_start) or pd.isna(selected_end)
                    or state_end <= selected_start
                    or state_start >= selected_end
                ):
                    continue
                for candidate in state.get(
                    "direct_tip_candidates", []
                ):
                    if (
                        str(candidate.get("source") or "")
                        .strip().upper() != selected_source
                    ):
                        continue
                    candidate_ids = {
                        str(value).strip()
                        for value in candidate.get(
                            "direct_tip_ids", []
                        )
                        if str(value).strip()
                    }
                    id_match = bool(
                        selected_ids and candidate_ids
                        and selected_ids.intersection(candidate_ids)
                    )
                    candidates.append((
                        0 if id_match else 1,
                        state_start,
                        state,
                        candidate,
                    ))

            if selected_ids and any(item[0] == 0 for item in candidates):
                candidates = [
                    item for item in candidates if item[0] == 0
                ]
            candidates.sort(key=lambda item: (item[0], item[1]))

            remaining = requested
            for _, _start, state, candidate in candidates:
                candidate_key = (
                    state["state_key"], candidate["source"]
                )
                available = max(
                    cls._number(candidate.get("available_tonnes"))
                    - allocated_by_candidate[candidate_key],
                    0,
                )
                accepted = min(remaining, available)
                if accepted <= cls.TOLERANCE:
                    continue
                allocations.setdefault(
                    state["state_key"], {}
                )[candidate["source"]] = (
                    allocations.get(
                        state["state_key"], {}
                    ).get(candidate["source"], 0)
                    + accepted
                )
                allocated_by_candidate[candidate_key] += accepted
                remaining -= accepted
                if remaining <= cls.TOLERANCE:
                    break

            if remaining > 1e-5:
                label = selected.get(
                    "optimised_steady_state", "?"
                )
                raise ManualBlendPlanningError(
                    f"Could not transfer {remaining:,.1f} t of optimized "
                    f"direct tip for {selected.get('source') or 'source'} "
                    f"in steady state {label}. Check that the same 24HR "
                    "schedule and direct-tip rules are still selected."
                )
        return allocations
