"""Conservative grade-block composition inferred from exact-build inbound ore.

Bulk inventories do not measure selective reclamation. Proportional composition
is therefore an explicit inference. Unmatched sources retain an unknown share;
they are never redistributed across the known grade blocks.
"""

import math
import re
from collections import defaultdict

import pandas as pd

from classes.ExpitSequenceReconciler import grade_block_key
from classes.PhaseSchemas import contributing_block


LINEAGE_BASIS = "proportional_exact_build_inbound"


def clean_text(value):
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def finite_number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def canonical_block(value):
    """Reject stockpile/build names instead of inventing spatial identities."""
    name = clean_text(value)
    key = grade_block_key(name.replace("|", "/"))
    parts = key.split("|")
    if len(parts) != 6 or not all(parts):
        return ""
    if not re.match(r"^[A-Z]", parts[-1]):
        return ""
    return key


def block_record(key, tonnes):
    material = re.match(r"[A-Z]+", key.split("|")[-1]).group()
    return contributing_block(key, feed_wmt=tonnes, material_type=material)


class InventoryBuildLineage:
    """Index a bulk query once, then resolve many build/period compositions."""

    def __init__(self, frame):
        self.rows = defaultdict(list)
        self.inventory = {}
        self._cache = {}
        if frame is None or frame.empty:
            return
        required = {"BUILD", "GRADE_BLOCK_NAME", "AVAILABLE_AT", "INBOUND_WMT"}
        if not required.issubset(frame.columns):
            raise ValueError(f"Lineage history missing columns: {sorted(required - set(frame.columns))}")
        for row in frame.to_dict("records"):
            build = clean_text(row["BUILD"]).upper()
            if not build:
                raise ValueError("Lineage history contains an unnamed build.")
            self.inventory.setdefault(build, {
                "footprint": clean_text(row.get("FOOTPRINT")),
                "inventory_wmt": finite_number(row.get("INVENTORY_WMT")),
            })
            when = pd.to_datetime(row["AVAILABLE_AT"], errors="coerce")
            if pd.isna(when):
                # A LEFT JOIN with no inbound history is an expected empty row.
                if finite_number(row["INBOUND_WMT"]) not in (None, 0.0):
                    raise ValueError(f"{build}: inbound tonnes have no valid timestamp.")
                continue
            if when.tzinfo is not None:
                when = when.tz_convert("Australia/Perth").tz_localize(None)
            name = clean_text(row["GRADE_BLOCK_NAME"])
            key = canonical_block(name) or f"unknown:{name}"
            self.rows[build].append((when, key, finite_number(row["INBOUND_WMT"])))

    def composition(self, build, as_of):
        """Return fractions of all inbound tonnes, plus reasons for any gaps."""
        build = clean_text(build).upper()
        cutoff = pd.Timestamp(as_of)
        if cutoff.tzinfo is not None:
            cutoff = cutoff.tz_convert("Australia/Perth").tz_localize(None)
        cache_key = (build, cutoff)
        if cache_key in self._cache:
            return self._cache[cache_key]
        totals = defaultdict(float)
        invalid = False
        for when, key, tonnes in self.rows.get(build, []):
            if when > cutoff:
                continue
            if tonnes is None:
                invalid = True
                continue
            # Aggregate aliases and corrections before assessing positivity.
            totals[key] += tonnes
        if invalid or any(tonnes < 0.0 for tonnes in totals.values()):
            result = ({}, [f"{build}: invalid or negative net lineage; composition unavailable."])
        else:
            total = math.fsum(totals.values())
            if total <= 0.0:
                result = ({}, [f"{build}: no positive inbound lineage before this period ends."])
            else:
                fractions = {
                    key: tonnes / total for key, tonnes in sorted(totals.items())
                    if not key.startswith("unknown:") and tonnes > 0.0
                }
                warnings = []
                if any(key.startswith("unknown:") and tonnes > 0 for key, tonnes in totals.items()):
                    warnings.append(f"{build}: some inbound sources have no grade-block identity.")
                result = (fractions, warnings)
        self._cache[cache_key] = result
        return result

    def opening_records(self, builds, as_of):
        """Scale known composition to opening inventory without inventing coverage."""
        records = []
        for build in builds:
            build = clean_text(build).upper()
            fractions, warnings = self.composition(build, as_of)
            inventory = self.inventory.get(build, {})
            balance = inventory.get("inventory_wmt")
            usable_balance = max(balance or 0.0, 0.0)
            if balance is None:
                warnings = [*warnings, f"{build}: opening inventory balance unavailable."]
            attributed = usable_balance * math.fsum(fractions.values())
            records.append({
                "build": build,
                "footprint": inventory.get("footprint", ""),
                "inventory_wmt": balance,
                "lineage_basis": LINEAGE_BASIS,
                "contributing_blocks": [block_record(key, usable_balance * share)
                                        for key, share in fractions.items() if usable_balance > 0],
                "attributed_wmt": attributed,
                "unattributed_wmt": max(usable_balance - attributed, 0.0),
                "warnings": list(warnings),
            })
        return records
