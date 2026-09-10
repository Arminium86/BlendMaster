import copy
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd

from database.DatabaseContext import get_database_path, set_database_path
from database.SQLiteDatabase import DatabaseManager
from execute.Run import Run
from GUI.InitialiseGUI import UserInputs
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

    @staticmethod
    def database_view():
        return SimpleNamespace(
            database_view_stockpile_rows=lambda: [{"source_type": "Stockpile", "tonnes": 100}],
            file_path_24hr_choice="24hr.csv", start_time_choice=pd.Timestamp("2026-08-18"),
            expit_mode_choice=2, active_site_context=lambda: {"mine": "CC"},
            run_program=Mock(), database_view_periods=lambda: SimpleNamespace(
                horizon_end=lambda: pd.Timestamp("2026-08-20")),
            database_view_grade_block_rows=lambda _: [],
            database_view_input_signature=lambda: "current-inputs",
        )

    def test_failed_aps_preparation_cannot_return_an_empty_solver_snapshot(self):
        view = self.database_view()
        message = "24HR grade block 'SO69_459': No exact 2WP destination."
        view.run_program.prepare_expit_payload_transactions.side_effect = ValueError(message)
        with self.assertRaisesRegex(ValueError, "APS 24HR payload preparation failed") as caught:
            UserInputs.prepare_database_view_data(view)
        self.assertIn(message, str(caught.exception))
        # The background task delivers the exception to this existing handler.
        view.database_view_expit_payload_transactions = pd.DataFrame()
        view.database_view_snapshot_signature = "previous-inputs"
        for name in ("database_view_refresh_button", "database_view_continue_button",
                     "database_view_table", "database_view_warning_label", "database_view_summary_label"):
            setattr(view, name, Mock())
        UserInputs.handle_database_view_error(view, str(caught.exception))
        self.assertIsNone(view.database_view_expit_payload_transactions)
        self.assertIsNone(view.database_view_snapshot_signature)
        self.assertTrue(view.database_view_refresh_pending)
        view.database_view_continue_button.setEnabled.assert_called_once_with(False)

    def test_successful_empty_aps_preparation_remains_valid(self):
        view = self.database_view()
        empty = pd.DataFrame()
        empty.attrs["source_property_warnings"] = []
        view.run_program.prepare_expit_payload_transactions.return_value = empty
        result = UserInputs.prepare_database_view_data(view)
        self.assertIs(result["transactions"], empty)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(len(result["records"]), 1)

    def test_old_database_view_failure_cache_is_not_reused(self):
        view = SimpleNamespace(
            time_mode_choice=2, expit_mode_choice=2, expit_refresh_tolerance_minutes=30,
            expit_signatures_match_except_start_time=UserInputs.expit_signatures_match_except_start_time,
        )
        signature = UserInputs.expit_input_cache_signature(
            view, start_time=pd.Timestamp("2026-08-18"), site_context={"mine": "CC"},
            schedule_path=str(self.schedule), two_wp_path=str(self.reference), selected_agents=["EX01"],
            expit_mode=2, reevaluate_direct_tip=False, selected_crusher=[],
        )
        for old_format in ("failed_import", "missing_rule_version", "older_rule_version"):
            old = json.loads(signature)
            if old_format == "failed_import":
                old["cache_version"] = 2
            elif old_format == "missing_rule_version":
                old.pop("destination_rule_version")
            else:
                old["destination_rule_version"] -= 1
            self.manager.write_expit_input_cache(pd.DataFrame(), json.dumps(old, sort_keys=True),
                                                metadata={"source": "database_view"})
            for time_mode in (1, 2):
                with self.subTest(old_format=old_format, time_mode=time_mode):
                    view.time_mode_choice = time_mode
                    cached, _ = UserInputs.read_reusable_expit_input_cache(view, signature)
                    self.assertIsNone(cached)


if __name__ == "__main__":
    unittest.main()
