"""Whole-plan backup instructions; no changes to physical destination allocation."""
from classes.DestinationRules import stockpile, text


def backup_choices(tipping_points, destination_rows, areas, matches=None):
    matches = matches or (lambda area, point: area.upper() == point.upper())
    result = {str(point): set() for point in tipping_points if text(point)}
    for row in destination_rows:
        for key in ("fallback_1_destination", "fallback_2_destination"):
            name = stockpile(row.get(key))
            area = text(areas.get(name))
            if not name or not area:
                continue
            for point in result:
                if matches(area, point):
                    result[point].add(name)
    return {point: sorted(names) for point, names in result.items()}


def backup_publication(choices, selections):
    rows = []
    for point, options in choices.items():
        selected = stockpile(selections.get(point))
        if selected and selected not in options:
            raise ValueError(f"{point}: saved backup {selected} is no longer an eligible fallback. Choose a current backup or clear the selection.")
        if options and not selected:
            raise ValueError(f"Choose a backup destination for {point} on the Blend Plan tab before exporting.")
        rows.append({"Tipping point": point, "Backup destination": selected or "No eligible fallback",
                     "Applies to": "All trucks, including direct tip; whole plan"})
    return rows


def backup_unavailable_reason(choices, destination_rows, areas):
    """Distinguish missing reconciliation from resolved fallbacks in another area."""
    if any(choices.values()):
        return ""
    names = {stockpile(row.get(key)) for row in destination_rows
             for key in ("fallback_1_destination", "fallback_2_destination")
             if stockpile(row.get(key))}
    if names:
        resolved_areas = sorted({text(areas.get(name)) for name in names if text(areas.get(name))})
        points = ", ".join(choices) or "the selected tipping point"
        if resolved_areas:
            return (f"Resolved fallbacks belong to ROM areas {', '.join(resolved_areas)}, "
                    f"which do not match {points}. Check the selected tipping point and "
                    "Stockpile Inventories > Nearest Crusher mapping.")
        return ("Fallback destinations are resolved, but their Stockpile Inventories > "
                "Nearest Crusher mapping is unavailable. Review the inventory mapping.")
    return " ".join(dict.fromkeys(text(row.get("reason")) for row in destination_rows
                                  if text(row.get("status")).lower() == "unavailable"
                                  and text(row.get("reason"))))
