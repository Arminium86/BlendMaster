"""Connect embedded charts after their local HTTP service is accepting requests."""
import time
import uuid
import requests
from PyQt5 import sip
from PyQt5.QtCore import QUrl, QUrlQuery
from database.DatabaseContext import get_database_path


def connect_view(host, view, url, timeout_seconds=30):
    token = (url, get_database_path(), getattr(host, 'active_scenario_id', None))
    if getattr(view, '_chart_connection_pending', None) == token:
        return
    view._chart_connection_pending = token
    def probe():
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                with requests.get(url, timeout=1, stream=True) as response:
                    if response.ok:
                        return True
            except requests.RequestException:
                pass
            time.sleep(.25)
        return False
    def done(ready):
        if (not sip.isdeleted(view) and view._chart_connection_pending == token
                and token[1:] == (get_database_path(), getattr(host, 'active_scenario_id', None))):
            view._chart_connection_pending = None
            if ready:
                target = QUrl(url)
                query = QUrlQuery(target)
                query.addQueryItem('refresh', uuid.uuid4().hex)
                target.setQuery(query)
                view.setUrl(target)
            else:
                view.setHtml('<p style="font:16px Segoe UI;padding:24px">The chart service is still unavailable. '
                             'Reopen this page to try again.</p>')
    host.run_background_task('Connecting chart…', probe, done, show_progress=False)
