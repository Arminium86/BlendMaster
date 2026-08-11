import re


def parent_grade_block_name(value):
    """Return the operational parent name for a sliced APS grade block.

    APS slice identifiers use a final ``_<digits>`` suffix on the last path
    segment.  Payload identity and timing stay untouched; this helper is only
    for logic that deliberately operates at parent-grade-block granularity.
    """
    source = str(value or "").strip().replace("\\", "/")
    source = re.sub(r"/+", "/", source).rstrip("/")
    if not source:
        return ""
    prefix, separator, final_part = source.rpartition("/")
    parent_part = re.sub(r"_\d+$", "", final_part)
    return f"{prefix}{separator}{parent_part}" if separator else parent_part
