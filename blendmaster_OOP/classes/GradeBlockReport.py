from __future__ import annotations

from types import SimpleNamespace


def consolidate_parent_grade_block_rows(data, solver_config=None):
    """Apply the optimiser's parent-grade-block report aggregation.

    The optimiser owns the established aggregation semantics for additive,
    weighted-average, product-build, and custom-constraint fields.  Manual
    reporting calls the same implementation through this deliberately small
    adapter so the two report paths cannot disagree about slice handling.
    The import is local to avoid coupling module initialisation order.
    """
    from classes.CaseModeller import CaseModeller

    context = SimpleNamespace(solver_config=dict(solver_config or {}))
    return CaseModeller.group_grade_block_rows(context, data)
