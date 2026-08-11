from __future__ import annotations

import math

import pandas as pd

from classes.GradeBlockIdentity import parent_grade_block_name


class ManualBlendSummary:
    """Build one selected-stream summary for each manual Gantt bar."""

    GRADES = ("fe", "si", "al", "p", "mn")
    COLUMNS = [
        "Bar",
        "Blend ID",
        "Start Datetime",
        "End Datetime",
        "Optimiser Grade Stream",
        "Grade Fe",
        "Grade Si",
        "Grade Al",
        "Grade P",
        "Grade Mn",
        "Sources and Ratios",
        "Direct Tip Grade Blocks",
    ]

    @staticmethod
    def _text(value):
        if value is None:
            return ""
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
        return str(value).strip()

    @staticmethod
    def _number(value, default=None):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    @classmethod
    def _blend_key(cls, value):
        text = cls._text(value)
        try:
            number = float(text)
            if number.is_integer():
                return str(int(number))
        except (TypeError, ValueError):
            pass
        return text

    @classmethod
    def _split(cls, value):
        return [item.strip() for item in cls._text(value).split(",") if item.strip()]

    @classmethod
    def stockpile_mix_key(cls, sources):
        """Return a ratio-insensitive key for manual Gantt shading."""
        normalized = sorted({item.upper() for item in cls._split(sources)})
        return "|".join(normalized) if normalized else "NO_STOCKPILE_SOURCE"

    @classmethod
    def _matching_report_rows(cls, report, sequence_row):
        if report.empty:
            return report
        start = pd.to_datetime(
            sequence_row.get("_exact_start", sequence_row.get("Start Datetime")),
            errors="coerce",
        )
        end = pd.to_datetime(
            sequence_row.get("_exact_end", sequence_row.get("End Datetime")),
            errors="coerce",
        )
        if pd.isna(start) or pd.isna(end):
            return report.iloc[0:0]
        blend_key = cls._blend_key(sequence_row.get("Blend ID"))
        blend_mask = report["_blend_key"].eq(blend_key)
        contained = report[
            blend_mask
            & (report["_start"] >= start)
            & (report["_end"] <= end)
        ]
        if not contained.empty:
            return contained
        return report[
            blend_mask
            & (report["_end"] > start)
            & (report["_start"] < end)
        ]

    @classmethod
    def _selected_grade(cls, rows, grade):
        grade_column = f"source_grade_{grade}"
        weight_column = f"selected_grade_weight_{grade}_tonnes"
        if grade_column in rows and weight_column in rows:
            grades = pd.to_numeric(rows[grade_column], errors="coerce")
            weights = pd.to_numeric(rows[weight_column], errors="coerce")
            valid = grades.notna() & weights.notna() & (weights > 0)
            total_weight = weights[valid].sum()
            if total_weight > 0:
                return float((grades[valid] * weights[valid]).sum() / total_weight)

        crusher_grade = f"crusher_actual_grade_{grade}"
        if crusher_grade not in rows:
            return None
        state_columns = [
            column for column in ("steady_state_number", "start_datetime")
            if column in rows
        ]
        states = (
            rows.drop_duplicates(state_columns, keep="first")
            if state_columns else rows.iloc[:1]
        )
        grades = pd.to_numeric(states[crusher_grade], errors="coerce")
        weights = pd.to_numeric(
            states.get("crusher_actual_tonnes", pd.Series(1.0, index=states.index)),
            errors="coerce",
        ).fillna(0.0)
        valid = grades.notna() & (weights > 0)
        total_weight = weights[valid].sum()
        return (
            float((grades[valid] * weights[valid]).sum() / total_weight)
            if total_weight > 0 else None
        )

    @classmethod
    def _source_details(cls, rows, fallback):
        stockpiles = rows[
            rows["_source_type"].eq("stockpile")
        ] if not rows.empty else rows
        if not stockpiles.empty:
            physical_tonnes = pd.to_numeric(
                stockpiles.get("source_actual_tonnes"), errors="coerce"
            ).fillna(0.0)
            if {
                "source_blend_ratio", "crusher_actual_tonnes"
            }.issubset(stockpiles.columns):
                contributions = (
                    pd.to_numeric(
                        stockpiles["source_blend_ratio"], errors="coerce"
                    ).fillna(0.0)
                    * pd.to_numeric(
                        stockpiles["crusher_actual_tonnes"], errors="coerce"
                    ).fillna(0.0)
                )
            else:
                contributions = physical_tonnes
            source_names = stockpiles.get(
                "parent_stockpile", pd.Series("", index=stockpiles.index)
            ).fillna("").astype(str).str.strip()
            raw_names = stockpiles.get(
                "source", pd.Series("", index=stockpiles.index)
            ).fillna("").astype(str).str.strip()
            source_names = source_names.where(source_names.ne(""), raw_names)
            totals = contributions.groupby(source_names).sum()
            totals = totals[totals.index.astype(str).str.strip() != ""]
            crusher_total = cls._unique_state_crusher_tonnes(rows)
            if not totals.empty:
                sources = totals.index.tolist()
                ratios = [
                    float(totals[source] / crusher_total)
                    if crusher_total > 0 else 0.0
                    for source in sources
                ]
                return sources, ratios

        sources = cls._split(fallback.get("Sources"))
        ratios = [cls._number(value, 0.0) for value in cls._split(
            fallback.get("Source Ratios")
        )]
        return sources, ratios

    @classmethod
    def _direct_tip_details(cls, rows, fallback):
        grade_blocks = rows[
            rows["_source_type"].eq("grade_block")
        ] if not rows.empty else rows
        if not grade_blocks.empty:
            tonnes = pd.to_numeric(
                grade_blocks.get("source_actual_tonnes"), errors="coerce"
            ).fillna(0.0)
            names = grade_blocks.get(
                "source", pd.Series("", index=grade_blocks.index)
            ).fillna("").map(parent_grade_block_name)
            totals = tonnes.groupby(names).sum()
            totals = totals[totals.index.astype(str).str.strip() != ""]
            crusher_total = cls._unique_state_crusher_tonnes(rows)
            if not totals.empty:
                return [
                    (
                        source,
                        float(totals[source]),
                        float(totals[source] / crusher_total)
                        if crusher_total > 0 else 0.0,
                    )
                    for source in totals.index
                ]

        sources = cls._split(fallback.get("Direct Tip Sources"))
        ratios = cls._split(fallback.get("Direct Tip Ratios"))
        tonnes = cls._split(fallback.get("Direct Tip Tonnes"))
        combined = {}
        for index, source in enumerate(sources):
            parent = parent_grade_block_name(source)
            if not parent:
                continue
            values = combined.setdefault(parent, [0.0, 0.0])
            values[0] += cls._number(
                tonnes[index] if index < len(tonnes) else None, 0.0
            )
            values[1] += cls._number(
                ratios[index] if index < len(ratios) else None, 0.0
            )
        return [
            (source, values[0], values[1])
            for source, values in combined.items()
        ]

    @classmethod
    def _unique_state_crusher_tonnes(cls, rows):
        if rows.empty or "crusher_actual_tonnes" not in rows:
            return 0.0
        state_columns = [
            column for column in ("steady_state_number", "start_datetime")
            if column in rows
        ]
        unique = (
            rows.drop_duplicates(state_columns, keep="first")
            if state_columns else rows.iloc[:1]
        )
        return float(pd.to_numeric(
            unique["crusher_actual_tonnes"], errors="coerce"
        ).fillna(0.0).sum())

    @classmethod
    def build(cls, sequence_rows, manual_report, legend_rows=None):
        sequence = [dict(row or {}) for row in (sequence_rows or [])]
        report = (
            manual_report.copy()
            if isinstance(manual_report, pd.DataFrame)
            else pd.DataFrame(manual_report or [])
        )
        if not report.empty:
            report["_start"] = pd.to_datetime(
                report.get("start_datetime"), errors="coerce"
            )
            report["_end"] = pd.to_datetime(
                report.get("end_datetime"), errors="coerce"
            )
            report["_blend_key"] = report.get(
                "blend_ID", pd.Series("", index=report.index)
            ).map(cls._blend_key)
            source_type = report.get(
                "source_type", pd.Series("", index=report.index)
            ).fillna("").astype(str).str.strip().str.lower()
            equipment = report.get(
                "equipment", pd.Series("", index=report.index)
            ).fillna("").astype(str).str.strip().str.upper()
            report["_source_type"] = source_type.where(
                source_type.ne(""),
                equipment.map(
                    lambda value: "grade_block" if value.startswith("EX")
                    else "stockpile" if value.startswith("RC") else ""
                ),
            )

        legend_by_blend = {
            cls._blend_key(row.get("Blend ID")): dict(row)
            for row in (legend_rows or []) if isinstance(row, dict)
        }
        summaries = []
        for index, sequence_row in enumerate(sequence):
            blend_id = cls._blend_key(sequence_row.get("Blend ID"))
            fallback = legend_by_blend.get(blend_id, {})
            rows = cls._matching_report_rows(report, sequence_row)
            stream_values = (
                [cls._text(value) for value in rows.get(
                    "selected_grade_stream", pd.Series(dtype=object)
                ).dropna() if cls._text(value)]
                if not rows.empty else []
            )
            stream = stream_values[0] if stream_values else cls._text(
                sequence_row.get("Optimiser Grade Stream")
            )
            sources, source_ratios = cls._source_details(rows, fallback)
            direct_tips = cls._direct_tip_details(rows, fallback)
            summary = {
                "Bar": index + 1,
                "Blend ID": blend_id,
                "Start Datetime": cls._text(sequence_row.get("Start Datetime")),
                "End Datetime": cls._text(sequence_row.get("End Datetime")),
                "Optimiser Grade Stream": stream,
                **{
                    f"Grade {grade.title() if grade != 'si' else 'Si'}": (
                        cls._selected_grade(rows, grade)
                        if not rows.empty else None
                    )
                    for grade in cls.GRADES
                },
                "Sources": ", ".join(sources),
                "Source Ratios": ", ".join(
                    f"{ratio:.6f}" for ratio in source_ratios
                ),
                "Sources and Ratios": "; ".join(
                    f"{source} @ {ratio * 100:.2f}%"
                    for source, ratio in zip(sources, source_ratios)
                ),
                "Direct Tip Sources": ", ".join(
                    source for source, _tonnes, _ratio in direct_tips
                ),
                "Direct Tip Ratios": ", ".join(
                    f"{ratio:.6f}" for _source, _tonnes, ratio in direct_tips
                ),
                "Direct Tip Tonnes": ", ".join(
                    f"{tonnes:.2f}" for _source, tonnes, _ratio in direct_tips
                ),
                "Direct Tip Grade Blocks": "; ".join(
                    f"{source} @ {ratio * 100:.2f}% ({tonnes:,.0f} t)"
                    for source, tonnes, ratio in direct_tips
                ),
                "_stockpile_mix_key": cls.stockpile_mix_key(", ".join(sources)),
            }
            summaries.append(summary)
        return summaries
