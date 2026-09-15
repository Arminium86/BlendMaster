"""Shared display-only formatting and stable blend colours."""
import hashlib
import math
from PyQt5.QtGui import QColor


def blend_id(value):
    text = str(value)
    try:
        number = float(text)
        if math.isfinite(number) and number.is_integer(): return str(int(number))
    except (ValueError, TypeError): pass
    return text


def blend_color(value):
    hue = int(hashlib.sha1(blend_id(value).encode()).hexdigest()[:4], 16) % 360
    return QColor.fromHsv(hue, 135, 190)


def number(value, decimals=0):
    try:
        numeric = float(value)
        return f'{numeric:,.{decimals}f}' if math.isfinite(numeric) else '—'
    except (ValueError, TypeError):
        return str(value) if value not in (None, '') else '—'
