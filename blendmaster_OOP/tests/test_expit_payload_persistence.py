import copy
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from database.DatabaseContext import get_database_path, set_database_path
from database.SQLiteDatabase import DatabaseManager
from execute.Run import Run
from tests.test_dual_schedule_ingestion import reserve_row


class ExpitPayloadPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(set_database_path, get_database_path())
        self.database = str(Path(self.directory.name) / "payloads.db")
        set_database_path(self.database)
        self.schedule = Path(self.directory.name) / "24hr.csv"
        self.reference = Path(self.directory.name) / "2wp.csv"
        pd.DataFrame([reserve_row(
            "Reserves/CC/PIT_A/BLOCK_1", "PIT_A", "Stockpiles/SP_A", 100,
            "01/01/2026 00:00", "01/01/2026 01:00",
        )]).to_csv(self.reference, index=False)
        self.runner = Run.__new__(Run)
        self.manager = DatabaseManager()

    def prepare(self, mode=1, agents=None, tonnes=100):
        pd.DataFrame([reserve_row(
            "Reserves/CC/PIT_A/BLOCK_1", "PIT_A", "Stockpiles/SP_A", tonnes,
            "01/01/2026 00:00", "01/01/2026 01:00",
        )]).to_csv(self.schedule, index=False)
        return self.runner.prepare_expit_payload_transactions(
            pd.Timestamp("2026-01-01").to_pydatetime(), mode, self.schedule,
            selected_24hr_agents=agents, two_wp_file_path=self.reference,
        )

    def test_empty_schedule_payloads_clear_previous_payloads_and_turnover_audit(self):
        for mode, tonnes, agents in ((1, 100, ["EX99"]), (2, 100, ["EX99"]),
                                     (1, 0, None), (2, 0, None)):
            with self.subTest(mode=mode, tonnes=tonnes, agents=agents):
                populated = self.prepare()
                original = populated.copy(deep=True)
                self.manager.write_expit_payload_transactions_to_database(populated)
                pd.testing.assert_frame_equal(populated, original)
                with closing(sqlite3.connect(self.database)) as connection:
                    self.assertEqual(connection.execute(
                        "SELECT start_datetime FROM expit_payload_transactions"
                    ).fetchone()[0], "2026-01-01 00:00:00")
                    self.assertGreater(connection.execute(
                        "SELECT COUNT(*) FROM two_wp_grade_block_turnover_audit"
                    ).fetchone()[0], 0)
                empty = self.prepare(mode=mode, agents=agents, tonnes=tonnes)
                self.assertEqual(empty.shape, (0, 0))
                self.manager.write_expit_payload_transactions_to_database(empty)
                self.assertEqual(empty.shape, (0, 0))
                with closing(sqlite3.connect(self.database)) as connection:
                    for table in ("expit_payload_transactions", "two_wp_grade_block_turnover_audit"):
                        self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
                    columns = {row[1] for row in connection.execute("PRAGMA table_info(expit_payload_transactions)")}
                self.assertTrue({"start_datetime", "delivered_datetime", "payload"}.issubset(columns))

    def test_empty_payloads_still_persist_sequence_reconciliation_evidence(self):
        for columns in ([], ["start_datetime"], ["start_datetime", "delivered_datetime"]):
            with self.subTest(columns=columns):
                empty = pd.DataFrame(columns=columns)
                empty.attrs["expit_sequence_audit"] = pd.DataFrame([{
                    "agent": "EX01", "record_type": "complete", "remaining_wmt": 0,
                }])
                original = copy.deepcopy(empty)
                self.manager.write_expit_payload_transactions_to_database(empty)
                pd.testing.assert_frame_equal(empty, original)
                pd.testing.assert_frame_equal(empty.attrs["expit_sequence_audit"], original.attrs["expit_sequence_audit"])
                with closing(sqlite3.connect(self.database)) as connection:
                    self.assertEqual(connection.execute(
                        "SELECT agent, record_type, remaining_wmt FROM expit_sequence_reconciliation_audit"
                    ).fetchall(), [("EX01", "complete", 0)])
                    self.assertEqual(connection.execute(
                        "SELECT COUNT(*) FROM expit_payload_transactions"
                    ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
