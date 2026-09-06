import math


DEFAULT_AMT_RECLAIM_RATE_TPH = 2000.0
DEFAULT_AMT_TARGET_CHUNK_HOURS = 72.0


def _positive_float(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(number):
        return float(default)
    return max(number, 0.0)


def calculate_amt_chunk_plan(
    total_wmt,
    reclaim_rate_tph=DEFAULT_AMT_RECLAIM_RATE_TPH,
    target_hours=DEFAULT_AMT_TARGET_CHUNK_HOURS,
):
    """Return the whole chunk count closest to the requested reclaim hours."""
    total_wmt = _positive_float(total_wmt)
    reclaim_rate_tph = _positive_float(reclaim_rate_tph)
    target_hours = _positive_float(target_hours)
    target_chunk_wmt = reclaim_rate_tph * target_hours

    if total_wmt <= 0:
        return {
            "chunk_count": 0,
            "chunk_size": 0.0,
            "target_chunk_wmt": target_chunk_wmt,
            "resulting_chunk_hours": 0.0,
        }

    if reclaim_rate_tph <= 0 or target_hours <= 0:
        return {
            "chunk_count": 1,
            "chunk_size": total_wmt,
            "target_chunk_wmt": target_chunk_wmt,
            "resulting_chunk_hours": (
                total_wmt / reclaim_rate_tph
                if reclaim_rate_tph > 0 else 0.0
            ),
        }

    ideal_count = total_wmt / target_chunk_wmt
    candidates = {
        max(1, int(math.floor(ideal_count))),
        max(1, int(math.ceil(ideal_count))),
    }

    def candidate_score(chunk_count):
        resulting_hours = total_wmt / chunk_count / reclaim_rate_tph
        # Prefer fewer chunks when both neighbouring counts are equally close.
        return (round(abs(resulting_hours - target_hours), 12), chunk_count)

    chunk_count = min(candidates, key=candidate_score)
    chunk_size = total_wmt / chunk_count
    return {
        "chunk_count": chunk_count,
        "chunk_size": chunk_size,
        "target_chunk_wmt": target_chunk_wmt,
        "resulting_chunk_hours": chunk_size / reclaim_rate_tph,
    }
