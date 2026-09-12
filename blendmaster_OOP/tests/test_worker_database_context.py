import os
import tempfile
import threading
import unittest
from database.DatabaseContext import database_scope, get_database_path, set_database_path


class WorkerDatabaseContextTests(unittest.TestCase):
    def test_worker_stays_on_original_database_when_ui_switches_sites(self):
        original = get_database_path()
        try:
            with tempfile.TemporaryDirectory() as directory:
                a, b = [os.path.join(directory, name) for name in ('a.db', 'b.db')]
                entered, switched = threading.Event(), threading.Event()
                observed = []
                def work():
                    with database_scope(a):
                        entered.set()
                        switched.wait(5)
                        observed.append(get_database_path())
                worker = threading.Thread(target=work)
                worker.start()
                self.assertTrue(entered.wait(5))
                set_database_path(b)
                switched.set()
                worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(observed, [a])
                self.assertEqual(get_database_path(), b)
        finally:
            set_database_path(original)
