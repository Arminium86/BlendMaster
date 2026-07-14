import os
import threading


_lock = threading.RLock()
_database_path = os.path.abspath("blendmaster.db")


def set_database_path(path):
    """Set the SQLite database used by the active BlendMaster scenario."""
    global _database_path
    resolved = os.path.abspath(os.fspath(path or "blendmaster.db"))
    parent = os.path.dirname(resolved)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with _lock:
        _database_path = resolved
    return resolved


def get_database_path():
    with _lock:
        return _database_path
