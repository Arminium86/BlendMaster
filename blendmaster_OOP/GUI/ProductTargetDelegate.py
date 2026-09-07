"""Presentation-only precision for grades on imported 2WP target rows."""

import math

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QStyledItemDelegate


IMPORTED_GRADE_ROLE = Qt.UserRole + 1
PRECISION_TOOLTIP = (
    "2WP grades display three decimal places. Calculations, editing and copying "
    "use the full stored value."
)


class ProductTargetDelegate(QStyledItemDelegate):
    """Keep the item's editable text exact; round only the painted cell text.

    The standard editor and clipboard therefore use the same unrounded value
    as table validation and persistence. No second numeric cache can become
    stale when a user edits or pastes over an imported value.
    """

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if not index.data(IMPORTED_GRADE_ROLE):
            return
        try:
            value = float(option.text.replace(",", ""))
        except (TypeError, ValueError):
            return  # Blanks and invalid edits remain visible for validation.
        if math.isfinite(value):
            option.text = f"{value:.3f}"
