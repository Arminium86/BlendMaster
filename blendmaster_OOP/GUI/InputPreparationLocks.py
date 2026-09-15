"""Lock mutable inputs while retaining navigation through saved plan results."""
from PyQt5 import sip
from PyQt5.QtCore import Qt


RESULT_PAGES = frozenset({
    'optimised_blend_sequence', 'setup_blends', 'blend_sequence', 'blend_plan',
    'material_destination_plan', 'material_flow_results', 'build_depletion_profiles',
    'grade_profiles', 'optimised_grade_profiles', 'manual_grade_profiles',
    'closing_rom_stocks_compliance', 'opf_production_report', 'database_reports',
})


def acquire(host, readable_results=False):
    pages = vars(host).get('page_widgets') or {}
    if readable_results and pages:
        widgets = [widget for name, widget in pages.items() if name not in RESULT_PAGES]
        widgets.append(vars(host).get('scenario_toolbar'))
    else:
        widgets = [vars(host).get(name) for name in ('tabs', 'scenario_toolbar')]
    records = vars(host).setdefault('_preparation_widget_locks', {})
    held = []
    for widget in dict.fromkeys(widgets):
        if widget is None or sip.isdeleted(widget):
            continue
        if widget not in records:
            # Preserve explicit disabled state, including underneath a locked parent.
            records[widget] = [0, not widget.testAttribute(Qt.WA_ForceDisabled)]
        records[widget][0] += 1
        widget.setEnabled(False)
        held.append(widget)
    return held


def page_state_changed(host, page, enabled):
    """Track navigation's latest intent without unlocking an active worker.

    QTabWidget.setTabEnabled also enables/disables its page widget. Override
    that side effect while locked, and restore the new intent on final release.
    """
    widget = (vars(host).get('page_widgets') or {}).get(page)
    record = (vars(host).get('_preparation_widget_locks') or {}).get(widget)
    if record is not None:
        record[1] = bool(enabled)
        if widget is not None and not sip.isdeleted(widget):
            widget.setEnabled(False)


def release(host, held):
    records = vars(host).get('_preparation_widget_locks', {})
    for widget in held:
        record = records.get(widget)
        if record is None:
            continue
        record[0] -= 1
        if not record[0]:
            records.pop(widget)
            if not sip.isdeleted(widget):
                widget.setEnabled(record[1])
