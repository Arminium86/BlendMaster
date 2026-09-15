"""Apply the operational noise policy without changing reports or editable data."""
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QTableView, QTableWidget, QTabWidget, QPushButton
from PyQt5 import sip
from classes.PlannerPresentation import visible_columns

RICH_PAGES = frozenset({'database_view', 'database_reports'})
AUDIT_TABS = frozenset({'Plan Audits', '2WP row audit'})
ENRICH_BUTTONS = frozenset({'Choose Columns', 'Select Columns', 'Configure Columns', 'Columns…',
                          'Columns...', 'Enrich View', 'Export review CSV…'})


def page_for(host, widget):
    pages = {id(value): key for key, value in vars(host).get('page_widgets', {}).items()}
    current = widget
    while current is not None:
        if id(current) in pages:
            return pages[id(current)]
        current = current.parentWidget()
    return None


def apply_table(table):
    if sip.isdeleted(table):
        return
    host = table.window()
    if not vars(host).get('page_widgets') or not vars(host).get('selected_data_stream'):
        return
    page = page_for(host, table)
    if page is None or page in RICH_PAGES:
        return
    model = table.model()
    if model is None:
        return
    from PyQt5.QtCore import Qt
    columns = [str(model.headerData(i, Qt.Horizontal) or '') for i in range(model.columnCount())]
    allowed = set(visible_columns(columns, host.selected_data_stream))
    previous = vars(table).get('_noise_hidden_columns', set())
    hidden = {i for i, label in enumerate(columns) if label not in allowed}
    for i in previous - hidden:
        if i < model.columnCount():
            table.setColumnHidden(i, False)
    for i in hidden:
        table.setColumnHidden(i, True)
    table._noise_hidden_columns = hidden


def refresh(host, page=None):
    # Explicit context changes still refresh all pages. Navigation needs only
    # its destination; hidden pages are refreshed when they are entered.
    root = (vars(host).get('page_widgets') or {}).get(page) if page is not None else host
    if root is None:
        return
    tables = root.findChildren(QTableView)
    if isinstance(root, QTableView):
        tables.insert(0, root)
    for table in tables:
        if not vars(table).get('_noise_connected'):
            table._noise_connected = True
            table.horizontalHeader().sectionCountChanged.connect(lambda _a, _b, t=table: queue_table(t))
        apply_table(table)
    tab_widgets = root.findChildren(QTabWidget)
    if isinstance(root, QTabWidget):
        tab_widgets.insert(0, root)
    for tabs in tab_widgets:
        if page_for(host, tabs) in RICH_PAGES:
            continue
        for i in range(tabs.count()):
            if tabs.tabText(i) in AUDIT_TABS:
                tabs.setTabVisible(i, False)
    for button in root.findChildren(QPushButton):
        if page_for(host, button) not in RICH_PAGES and button.text() in ENRICH_BUTTONS:
            button.hide()


def queue_table(table):
    if vars(table).get('_noise_pending'):
        return
    table._noise_pending = True
    def done():
        if not sip.isdeleted(table):
            table._noise_pending = False
            apply_table(table)
    QTimer.singleShot(0, done)
