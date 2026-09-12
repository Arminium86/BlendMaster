import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar


_lock = threading.RLock()
_database_path = os.path.abspath("blendmaster.db")
_task_database = ContextVar('blendmaster_task_database', default=None)


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
    scoped = _task_database.get()
    if scoped is not None:
        return scoped
    with _lock:
        return _database_path


@contextmanager
def database_scope(path):
    """Pin worker I/O to its originating scenario without changing the UI's DB."""
    token = _task_database.set(os.path.abspath(os.fspath(path)))
    try:
        yield get_database_path()
    finally:
        _task_database.reset(token)
