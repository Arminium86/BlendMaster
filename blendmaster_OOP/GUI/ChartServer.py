"""Bind each desktop chart to its own local endpoint before showing its view."""
import threading
from werkzeug.serving import make_server


def start(chart):
    # Binding port zero atomically reserves an available port. Support and
    # Planner sessions must never attach to another process's chart service.
    server = make_server('127.0.0.1', 0, chart.app.server, threaded=True)
    chart.port = server.server_port
    chart._http_server = server
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread
