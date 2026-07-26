from datetime import datetime, timedelta


class ManualBlendRules:
    """Pure validation helpers shared by the PyQt and manual Gantt UIs."""

    DATETIME_FORMATS = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    )

    @staticmethod
    def normalized_blend_id(value):
        return str(value or "").strip()

    @staticmethod
    def split_sources(value):
        if isinstance(value, (list, tuple, set)):
            values = value
        else:
            values = str(value or "").split(",")
        return {
            str(source).strip().upper()
            for source in values
            if str(source).strip()
        }

    @classmethod
    def blend_source_sets(cls, saved_blends):
        source_sets = {}
        for blend in saved_blends or []:
            blend_id = cls.normalized_blend_id(blend.get("Blend ID"))
            if not blend_id:
                continue
            sources = cls.split_sources(blend.get("Sources"))
            if sources:
                source_sets.setdefault(blend_id, set()).update(sources)
        return source_sets

    @classmethod
    def parse_datetime(cls, value):
        if isinstance(value, datetime):
            return value
        text = str(value or "").strip()
        for date_format in cls.DATETIME_FORMATS:
            try:
                return datetime.strptime(text, date_format)
            except ValueError:
                continue
        return None

    @classmethod
    def sequence_interval(cls, row):
        start = cls.parse_datetime(row.get("Start Datetime"))
        if start is None:
            return None

        end = cls.parse_datetime(row.get("End Datetime"))
        if end is None:
            try:
                duration = float(row.get("Duration (hrs)") or 0)
            except (TypeError, ValueError):
                return None
            end = start + timedelta(hours=duration)
        if end <= start:
            return None
        return start, end

    @classmethod
    def overlapping_blend_bar_conflicts(cls, sequence_rows):
        """Return every pair of scheduled bars whose time intervals overlap."""
        scheduled = []
        for row_index, row in enumerate(sequence_rows or []):
            blend_id = cls.normalized_blend_id(row.get("Blend ID"))
            interval = cls.sequence_interval(row)
            if blend_id and interval:
                scheduled.append(
                    {
                        "row_index": row_index,
                        "blend_id": blend_id,
                        "start": interval[0],
                        "end": interval[1],
                    }
                )

        conflicts = []
        for left_index, left in enumerate(scheduled):
            for right in scheduled[left_index + 1:]:
                intervals_overlap = (
                    left["start"] < right["end"]
                    and right["start"] < left["end"]
                )
                if intervals_overlap:
                    conflicts.append(
                        {
                            "first_row": left["row_index"],
                            "second_row": right["row_index"],
                            "first_blend_id": left["blend_id"],
                            "second_blend_id": right["blend_id"],
                            "overlap_start": max(left["start"], right["start"]),
                            "overlap_end": min(left["end"], right["end"]),
                        }
                    )
        return conflicts

    @classmethod
    def overlapping_stockpile_conflicts(cls, sequence_rows, saved_blends=None):
        """Compatibility alias for projects created during Task 7."""
        return cls.overlapping_blend_bar_conflicts(sequence_rows)

    @staticmethod
    def conflict_message(conflict):
        return (
            f"Blend {conflict['first_blend_id']} and Blend "
            f"{conflict['second_blend_id']} overlap from "
            f"{conflict['overlap_start']:%Y-%m-%d %H:%M} to "
            f"{conflict['overlap_end']:%Y-%m-%d %H:%M}."
        )
