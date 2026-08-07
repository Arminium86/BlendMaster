"""Stable column ordering helpers shared by blend reports and report UIs."""


def balance_triplet_columns(columns):
    """Keep opening, movement/quantity, and closing fields beside each other."""
    columns = [] if columns is None else list(columns)
    available = set(columns)
    groups = []

    explicit_groups = (
        ("source_opening_balance", "source_actual_tonnes", "source_closing_balance"),
        ("product_build_opening_tonnes", "product_build_added_tonnes", "product_build_closing_tonnes"),
        ("build_opening_tonnes", "build_added_tonnes", "build_closing_tonnes"),
    )
    for group in explicit_groups:
        present = [column for column in group if column in available]
        if present:
            groups.append(present)

    for opening in columns:
        if not str(opening).endswith("_opening_balance"):
            continue
        base = str(opening)[: -len("_opening_balance")]
        closing = f"{base}_closing_balance"
        quantity_candidates = (
            base,
            f"{base}_actual_depletion",
            f"{base}_actual_tonnes",
            f"{base}_quantity",
            f"{base}_added",
            f"{base}_built",
        )
        group = [opening]
        group.extend(
            candidate for candidate in quantity_candidates
            if candidate in available and candidate not in group
        )
        if closing in available:
            group.append(closing)
        if len(group) > 1:
            groups.append(group)

    member_to_group = {}
    for group in groups:
        for member in group:
            member_to_group.setdefault(member, group)

    ordered = []
    emitted = set()
    for column in columns:
        group = member_to_group.get(column)
        if group:
            for member in group:
                if member not in emitted:
                    ordered.append(member)
                    emitted.add(member)
        elif column not in emitted:
            ordered.append(column)
            emitted.add(column)
    return ordered


def order_balance_triplets(frame):
    """Return a frame with stable balance triplets without changing its data."""
    if frame is None or not hasattr(frame, "columns"):
        return frame
    return frame.reindex(columns=balance_triplet_columns(frame.columns))
