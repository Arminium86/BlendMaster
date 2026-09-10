import sqlite3
from contextlib import closing
import json
import pickle
import zlib
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from classes.MaterialDestinationPlan import MaterialDestinationPlan
from classes.PrimaryDestinationAllocator import AUDIT_COLUMNS, allocate_final_plan, write_allocation_audit
from classes.DestinationPlanReport import PUBLICATION_COLUMNS, build_publication, write_publication, ensure_schema
from classes.PeriodManager import PeriodManager
from classes.ProductBuildProgress import ProductBuildProgress
from classes.GradeStreams import ANALYTES, STREAMS
from classes.ReportColumns import order_balance_triplets
from database.DatabaseContext import get_database_path

class DatabaseManager:
    EXPIT_INPUT_CACHE_TABLE = "expit_input_cache"

    def write_expit_input_cache(
        self,
        transactions,
        signature,
        metadata=None,
        database_name=None,
    ):
        """Persist an exact solver-ready Expit payload snapshot.

        The ordinary ``expit_payload_transactions`` table is deliberately a
        readable report and therefore does not retain every internal column
        or DataFrame attribute required by DataLoader.  This cache is a
        lossless project-local snapshot used only when its complete input
        signature matches.
        """
        if not signature or transactions is None:
            return False
        try:
            payload = zlib.compress(
                pickle.dumps(
                    transactions, protocol=pickle.HIGHEST_PROTOCOL
                ),
                level=6,
            )
        except (pickle.PickleError, TypeError, AttributeError):
            return False
        metadata_json = json.dumps(
            metadata or {}, sort_keys=True, default=str
        )
        database_name = database_name or get_database_path()
        connection = sqlite3.connect(database_name)
        try:
            try:
                connection.execute(
                    f'''CREATE TABLE IF NOT EXISTS "{self.EXPIT_INPUT_CACHE_TABLE}" (
                        cache_id INTEGER PRIMARY KEY CHECK (cache_id = 1),
                        signature TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        payload_blob BLOB NOT NULL,
                        metadata_json TEXT NOT NULL
                    )'''
                )
                connection.execute(
                    f'''INSERT OR REPLACE INTO "{self.EXPIT_INPUT_CACHE_TABLE}"
                        (cache_id, signature, created_at, payload_blob, metadata_json)
                        VALUES (1, ?, ?, ?, ?)''',
                    (
                        str(signature),
                        datetime.now().isoformat(timespec="seconds"),
                        sqlite3.Binary(payload),
                        metadata_json,
                    ),
                )
                connection.commit()
            except sqlite3.Error:
                return False
        finally:
            connection.close()
        return True

    def read_expit_input_cache(self, signature, database_name=None):
        """Return ``(transactions, metadata)`` for an exact signature hit."""
        if not signature:
            return None, {}
        database_name = database_name or get_database_path()
        connection = sqlite3.connect(database_name)
        try:
            try:
                table_exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (self.EXPIT_INPUT_CACHE_TABLE,),
                ).fetchone()
                if not table_exists:
                    return None, {}
                row = connection.execute(
                    f'''SELECT payload_blob, metadata_json, created_at
                        FROM "{self.EXPIT_INPUT_CACHE_TABLE}"
                        WHERE cache_id = 1 AND signature = ?''',
                    (str(signature),),
                ).fetchone()
            except sqlite3.Error:
                return None, {}
        finally:
            connection.close()
        if row is None:
            return None, {}
        try:
            transactions = pickle.loads(zlib.decompress(bytes(row[0])))
            metadata = json.loads(row[1] or "{}")
            metadata["cache_created_at"] = row[2]
        except (pickle.PickleError, zlib.error, EOFError, ValueError, TypeError):
            return None, {}
        if not isinstance(transactions, pd.DataFrame):
            return None, {}
        return transactions, metadata

    def read_latest_expit_input_cache(self, database_name=None):
        """Return the latest project-local Expit snapshot and its signature."""
        database_name = database_name or get_database_path()
        connection = sqlite3.connect(database_name)
        try:
            try:
                row = connection.execute(
                    f'''SELECT signature, payload_blob, metadata_json, created_at
                        FROM "{self.EXPIT_INPUT_CACHE_TABLE}"
                        WHERE cache_id = 1'''
                ).fetchone()
            except sqlite3.Error:
                return None, "", {}
        finally:
            connection.close()
        if row is None:
            return None, "", {}
        try:
            transactions = pickle.loads(zlib.decompress(bytes(row[1])))
            metadata = json.loads(row[2] or "{}")
            metadata["cache_created_at"] = row[3]
        except (pickle.PickleError, zlib.error, EOFError, ValueError, TypeError):
            return None, "", {}
        if not isinstance(transactions, pd.DataFrame):
            return None, "", {}
        return transactions, str(row[0] or ""), metadata

    @staticmethod
    def clear_all_tables(database_name=None):
        database_name = database_name or get_database_path()
        conn = sqlite3.connect(database_name)
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name NOT LIKE 'sqlite_%'
                """
            )
            table_names = [row[0] for row in cursor.fetchall()]

            for table_name in table_names:
                safe_table_name = table_name.replace('"', '""')
                cursor.execute(f'DELETE FROM "{safe_table_name}"')

            conn.commit()
        finally:
            conn.close()

    PLAN_RESULT_TABLES = {
        "blend": "optimisation_plan_blend_report",
        "build": "optimisation_plan_build_report",
        "product_build": "optimisation_plan_product_build_report",
        "manual": "manual_plan_blend_report",
    }

    @staticmethod
    def clear_scheduling_reports(database_name=None):
        """Invalidate derived reports after the participating source set changes."""
        connection = sqlite3.connect(database_name or get_database_path())
        try:
            names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
            derived = {
                "optimised_blend_report", "manual_blend_report", "build_report", "product_build_report",
                "optimised_stockpile_depletion_report", "optimised_stockpile_profile_report",
                "optimisation_plan_blend_report", "optimisation_plan_build_report",
                "optimisation_plan_product_build_report", "manual_plan_blend_report",
                "two_wp_active_blend_report", "optimisation_plan_status", "closing_rom_stocks_compliance",
            }
            derived.update(AUDIT_COLUMNS)
            derived.update(PUBLICATION_COLUMNS)
            for name in names:
                if name in derived:
                    escaped = name.replace('"', '""')
                    connection.execute(f'DELETE FROM "{escaped}"')
            connection.commit()
        finally:
            connection.close()

    def clear_optimisation_plan_results(self, database_name=None):
        database_name = database_name or get_database_path()
        connection = sqlite3.connect(database_name)
        try:
            for table_name in (
                self.PLAN_RESULT_TABLES["blend"],
                self.PLAN_RESULT_TABLES["build"],
                self.PLAN_RESULT_TABLES["product_build"],
                "optimisation_plan_status",
                "two_wp_active_blend_report",
                "two_wp_grade_block_turnover_audit",
                "closing_rom_stocks_compliance",
            ):
                connection.execute(
                    f'DROP TABLE IF EXISTS "{table_name}"'
                )
            for table_name in [*AUDIT_COLUMNS, *PUBLICATION_COLUMNS]:
                if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone():
                    connection.execute(f'DELETE FROM "{table_name}" WHERE plan_type = ?', ("optimised",))
            connection.commit()
        finally:
            connection.close()

    def write_two_wp_closing_rom_stocks(
        self, rows, database_name=None
    ):
        """Persist the normalized four-column 2WP closing-stock input."""
        database_name = database_name or get_database_path()
        frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows or [])
        for column in frame.columns:
            if pd.api.types.is_datetime64_any_dtype(frame[column]):
                frame[column] = pd.to_datetime(
                    frame[column], errors="coerce"
                ).dt.strftime("%Y-%m-%d %H:%M:%S")
        connection = sqlite3.connect(database_name)
        try:
            frame.to_sql(
                "two_wp_closing_rom_stocks",
                connection,
                if_exists="replace",
                index=False,
            )
            connection.commit()
        finally:
            connection.close()

    def write_closing_rom_stocks_compliance(
        self, rows, plan_id, plan_type, database_name=None
    ):
        """Replace one plan's closing-ROM compliance rows."""
        database_name = database_name or get_database_path()
        frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows or [])
        if "plan_id" not in frame:
            frame.insert(0, "plan_id", str(plan_id))
        if "plan_type" not in frame:
            frame.insert(1, "plan_type", str(plan_type))
        for column in frame.columns:
            if pd.api.types.is_datetime64_any_dtype(frame[column]):
                frame[column] = pd.to_datetime(
                    frame[column], errors="coerce"
                ).dt.strftime("%Y-%m-%d %H:%M:%S")
        connection = sqlite3.connect(database_name)
        try:
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                ("closing_rom_stocks_compliance",),
            ).fetchone()
            existing = (
                pd.read_sql(
                    'SELECT * FROM "closing_rom_stocks_compliance"',
                    connection,
                )
                if table_exists else pd.DataFrame()
            )
            if not existing.empty and "plan_id" in existing:
                existing = existing[
                    existing["plan_id"].astype(str) != str(plan_id)
                ]
            columns = list(dict.fromkeys([
                *existing.columns.tolist(), *frame.columns.tolist()
            ]))
            combined = pd.concat([
                existing.reindex(columns=columns),
                frame.reindex(columns=columns),
            ], ignore_index=True)
            combined.to_sql(
                "closing_rom_stocks_compliance",
                connection,
                if_exists="replace",
                index=False,
            )
            connection.commit()
        finally:
            connection.close()

    def write_two_wp_active_blend_report_to_database(
        self, active_blend_guidance, database_name=None
    ):
        """Write every derived 2WP active-blend time window."""
        database_name = database_name or get_database_path()
        records = []
        for window in active_blend_guidance or []:
            stockpiles = sorted({
                str(stockpile).strip()
                for stockpile in window.get("stockpiles", [])
                if str(stockpile).strip()
            })
            start = pd.to_datetime(
                window.get("start_datetime"), errors="coerce"
            )
            end = pd.to_datetime(
                window.get("end_datetime"), errors="coerce"
            )
            records.append({
                "product_brand": str(
                    window.get("product_brand") or ""
                ).strip(),
                "destination_full_name": str(
                    window.get("destination_full_name") or ""
                ).strip(),
                "two_wp_active_blend": " + ".join(stockpiles),
                "stockpiles": ", ".join(stockpiles),
                "stockpile_count": len(stockpiles),
                "start_datetime": (
                    start.strftime("%Y-%m-%d %H:%M:%S")
                    if not pd.isna(start) else ""
                ),
                "end_datetime": (
                    end.strftime("%Y-%m-%d %H:%M:%S")
                    if not pd.isna(end) else ""
                ),
                "duration_hours": float(
                    window.get("duration_hours") or 0
                ),
            })
        columns = [
            "product_brand", "destination_full_name",
            "two_wp_active_blend", "stockpiles",
            "stockpile_count", "start_datetime", "end_datetime",
            "duration_hours",
        ]
        report = pd.DataFrame(records, columns=columns)
        connection = sqlite3.connect(database_name)
        try:
            report.to_sql(
                "two_wp_active_blend_report",
                connection,
                if_exists="replace",
                index=False,
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _plan_result_frame(results, plan_id, plan_rank):
        frame = (
            results.copy()
            if isinstance(results, pd.DataFrame)
            else pd.DataFrame(results or [])
        )
        frame.insert(0, "plan_rank", int(plan_rank))
        frame.insert(0, "plan_id", str(plan_id))
        for column in frame.columns:
            if pd.api.types.is_datetime64_any_dtype(frame[column]):
                frame[column] = pd.to_datetime(
                    frame[column], errors="coerce"
                ).dt.strftime("%Y-%m-%d %H:%M:%S")
            elif frame[column].dtype == "object":
                # Plan reports can include structured audit values such as
                # grade_streams. SQLite cannot bind a Python dict/list, so
                # retain the value as deterministic JSON rather than failing
                # after an otherwise successful optimisation run.
                frame[column] = frame[column].map(
                    DatabaseManager._sqlite_plan_result_value
                )
        return order_balance_triplets(frame)

    @staticmethod
    def _sqlite_plan_result_value(value):
        if isinstance(value, dict):
            return json.dumps(value, sort_keys=True, default=str)
        if isinstance(value, (list, tuple)):
            return json.dumps(value, default=str)
        if isinstance(value, set):
            return json.dumps(sorted(value, key=str), default=str)
        return value

    def write_optimisation_plan_result(
        self,
        result_type,
        results,
        plan_id,
        plan_rank,
        database_name=None,
    ):
        table_name = self.PLAN_RESULT_TABLES[result_type]
        database_name = database_name or get_database_path()
        frame = self._plan_result_frame(results, plan_id, plan_rank)
        connection = sqlite3.connect(database_name)
        try:
            table_exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = ?
                """,
                (table_name,),
            ).fetchone()
            if frame.empty:
                # Do not manufacture a metadata-only plan table.  Readers of
                # blend results legitimately sort by steady_state_number and
                # source; a zero-row table containing only plan_id/plan_rank
                # makes those otherwise valid reads fail with "no such
                # column".  If this plan previously existed, remove only its
                # rows and preserve the established schema for other plans.
                if table_exists:
                    table_columns = {
                        row[1]
                        for row in connection.execute(
                            f'PRAGMA table_info("{table_name}")'
                        ).fetchall()
                    }
                    if "plan_id" in table_columns:
                        connection.execute(
                            f'DELETE FROM "{table_name}" WHERE plan_id = ?',
                            (str(plan_id),),
                        )
                        connection.commit()
                return
            existing = (
                pd.read_sql(f'SELECT * FROM "{table_name}"', connection)
                if table_exists
                else pd.DataFrame()
            )
            if not existing.empty and "plan_id" in existing:
                existing = existing[
                    existing["plan_id"].astype(str) != str(plan_id)
                ]
            columns = list(dict.fromkeys([
                *existing.columns.tolist(),
                *frame.columns.tolist(),
            ]))
            combined = pd.concat(
                [
                    existing.reindex(columns=columns),
                    frame.reindex(columns=columns),
                ],
                ignore_index=True,
            )
            combined = order_balance_triplets(combined)
            combined.to_sql(
                table_name,
                connection,
                if_exists="replace",
                index=False,
            )
            connection.commit()
        finally:
            connection.close()

    def write_optimisation_plan_status(
        self, status_rows, database_name=None
    ):
        database_name = database_name or get_database_path()
        status = pd.DataFrame(status_rows or [], columns=[
            "plan_id", "plan_rank", "status", "message",
            "reused_blend_fallbacks",
        ])
        connection = sqlite3.connect(database_name)
        try:
            status.to_sql(
                "optimisation_plan_status",
                connection,
                if_exists="replace",
                index=False,
            )
            connection.commit()
        finally:
            connection.close()

    def add_product_build_progress_to_existing_reports(
        self, product_build_settings, database_name=None
    ):
        """Upgrade legacy blend-report tables without requiring a rerun."""
        database_name = database_name or get_database_path()
        connection = sqlite3.connect(database_name)
        try:
            table_names = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                    """
                ).fetchall()
            }
            for table_name in (
                "optimised_blend_report",
                "manual_blend_report",
            ):
                if table_name not in table_names:
                    continue
                report = pd.read_sql(
                    f'SELECT * FROM "{table_name}"',
                    connection,
                )
                report = ProductBuildProgress.annotate(
                    report, product_build_settings
                )
                for column in ("start_datetime", "end_datetime"):
                    if column in report.columns:
                        report[column] = pd.to_datetime(
                            report[column], errors="coerce"
                        ).dt.strftime("%Y-%m-%d %H:%M:%S")
                report.to_sql(
                    table_name,
                    connection,
                    if_exists="replace",
                    index=False,
                )
            connection.commit()
        finally:
            connection.close()

    def write_optimised_blend_report_to_database (self, results: pd.DataFrame, periods: PeriodManager):
        # Connect to the SQLite database or create it
        database_name = get_database_path()
        conn = sqlite3.connect(database_name)
        cursor = conn.cursor()

        # Create the table or use if it already exists
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS optimised_blend_report (
            start_datetime TEXT,
            end_datetime TEXT,
            steady_state_number INTEGER,
            blend_option TEXT,
            blend_ID TEXT,
            steady_state_duration INTEGER,
            period INTEGER,
            actual_direct_tip_ratio REAL,
            source TEXT,
            source_id TEXT,
            source_type TEXT,
            source_blend_ratio REAL,
            source_opening_balance REAL,
            source_actual_tonnes REAL,
            source_closing_balance REAL,
            source_grade_fe REAL,
            source_grade_si REAL,
            source_grade_al REAL,
            source_grade_p REAL,
            source_grade_mn REAL,
            selected_grade_stream TEXT,
            selected_grade_brand TEXT,
            grade_stream_warnings TEXT,
            equipment TEXT,
            equipment_rate_input REAL,
            equipment_rate_output REAL,
            crusher_actual_tonnes REAL,
            crusher_rate_input REAL,
            crusher_rate_output REAL,
            crusher_actual_grade_fe REAL,
            crusher_actual_grade_si REAL,
            crusher_actual_grade_al REAL,
            crusher_actual_grade_p REAL,
            crusher_actual_grade_mn REAL,
            crusher_grade_target_min_fe REAL,
            crusher_grade_target_max_fe REAL,
            crusher_grade_target_min_si REAL,
            crusher_grade_target_max_si REAL,
            crusher_grade_target_min_al REAL,
            crusher_grade_target_max_al REAL,
            crusher_grade_target_min_p REAL,
            crusher_grade_target_max_p REAL,
            crusher_grade_target_min_mn REAL,
            crusher_grade_target_max_mn REAL
        )
        ''')

        cursor.execute("PRAGMA table_info(optimised_blend_report)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "source_id" not in existing_columns:
            cursor.execute("ALTER TABLE optimised_blend_report ADD COLUMN source_id TEXT")
        if "source_type" not in existing_columns:
            cursor.execute("ALTER TABLE optimised_blend_report ADD COLUMN source_type TEXT")
        if "actual_direct_tip_ratio" not in existing_columns:
            cursor.execute("ALTER TABLE optimised_blend_report ADD COLUMN actual_direct_tip_ratio REAL")
        for column in ("selected_grade_stream", "selected_grade_brand", "grade_stream_warnings"):
            if column not in existing_columns:
                cursor.execute(f"ALTER TABLE optimised_blend_report ADD COLUMN {column} TEXT")
        for stream in STREAMS:
            for analyte in ANALYTES:
                column = f"source_grade_{stream}_{analyte}"
                if column not in existing_columns:
                    cursor.execute(
                        f'ALTER TABLE optimised_blend_report '
                        f'ADD COLUMN "{column}" REAL'
                    )
        two_wp_report_columns = {
            "two_wp_active_blend": "TEXT",
            "two_wp_active_blend_product_brand": "TEXT",
            "two_wp_active_blend_start_datetime": "TEXT",
            "two_wp_active_blend_end_datetime": "TEXT",
            "two_wp_active_blend_exact_match": "INTEGER",
        }
        for column, column_type in two_wp_report_columns.items():
            if column not in existing_columns:
                cursor.execute(
                    f'ALTER TABLE optimised_blend_report '
                    f'ADD COLUMN "{column}" {column_type}'
                )
        text_progress_columns = {
            "product_build_id",
            "product_build_name",
            "product_build_brand",
        }
        boolean_progress_columns = {
            "product_build_complete",
            "product_build_current_on_spec",
            "product_build_complete_on_spec",
        }
        for column in ProductBuildProgress.COLUMNS:
            if column in existing_columns:
                continue
            column_type = (
                "TEXT" if column in text_progress_columns
                else "INTEGER" if column in boolean_progress_columns
                else "REAL"
            )
            cursor.execute(
                f'ALTER TABLE optimised_blend_report '
                f'ADD COLUMN "{column}" {column_type}'
            )

        for column in results.columns:
            column_name = str(column)
            is_custom_constraint = column_name.startswith(
                "custom_constraint_"
            )
            is_source_property = column_name.startswith(
                "source_property_"
            )
            if (
                not (is_custom_constraint or is_source_property)
                or column in existing_columns
            ):
                continue
            column_type = (
                "TEXT"
                if is_custom_constraint and column_name.endswith((
                    "_name",
                    "_numerator_expression",
                    "_denominator_expression",
                ))
                else "REAL"
            )
            cursor.execute(
                f'ALTER TABLE optimised_blend_report '
                f'ADD COLUMN "{column}" {column_type}'
            )

        cursor.execute('DELETE FROM optimised_blend_report')

        if results.empty:
            conn.commit()
            conn.close()
            print(f"Optimised blend report saved to database {database_name}")
            return

        if "source_id" not in results.columns:
            results["source_id"] = results["source"]
        if "source_type" not in results.columns:
            results["source_type"] = ""
        if "actual_direct_tip_ratio" not in results.columns:
            results["actual_direct_tip_ratio"] = 0
        results = ProductBuildProgress.ensure_columns(results)

        results['start_datetime'] = pd.to_datetime(results['start_datetime']).dt.strftime('%Y-%m-%d %H:%M:%S')
        results['end_datetime'] = pd.to_datetime(results['end_datetime']).dt.strftime('%Y-%m-%d %H:%M:%S')

        # The result frame is authoritative. Keeping only columns present in
        # the legacy CREATE TABLE schema silently dropped newer audit fields
        # such as parent_stockpile. Replacing from the complete ordered frame
        # preserves every runtime property and its stable report order.
        results_to_write = order_balance_triplets(results)
        # Replace here so SQLite's physical schema follows the same stable
        # opening/quantity/closing order exposed by every report UI.
        results_to_write.to_sql(
            "optimised_blend_report",
            conn,
            if_exists="replace",
            index=False,
        )

        # Commit and close the connection
        conn.commit()
        conn.close()

        print(f"Optimised blend report saved to database {database_name}")
        
        self.write_optimised_stockpile_depletion_report_to_database(results)
        StockpileProfileReport.write_optimised_stockpile_profile_report_to_database(periods)

    def write_manual_blend_report_to_database(self, results: pd.DataFrame):
        """
        Replace the manual blend report with the current submitted manual plan.

        The manual report intentionally uses the same source- and
        crusher-level columns as ``optimised_blend_report`` so Results queries
        and downstream comparisons can treat the two plans consistently.
        """
        database_name = get_database_path()
        results = (
            results.copy()
            if isinstance(results, pd.DataFrame)
            else pd.DataFrame()
        )
        for column in ("start_datetime", "end_datetime"):
            if column in results.columns:
                results[column] = pd.to_datetime(
                    results[column], errors="coerce"
                ).dt.strftime("%Y-%m-%d %H:%M:%S")

        connection = sqlite3.connect(database_name)
        try:
            results = order_balance_triplets(results)
            results.to_sql(
                "manual_blend_report",
                connection,
                if_exists="replace",
                index=False,
            )
            connection.commit()
        finally:
            connection.close()

        print(f"Manual blend report saved to database {database_name}")

    def write_product_build_report_to_database(self, results: pd.DataFrame):
        database_name = get_database_path()
        conn = sqlite3.connect(database_name)
        try:
            if results is None:
                results = pd.DataFrame()
            results = results.copy()
            for column in results.columns:
                if pd.api.types.is_datetime64_any_dtype(results[column]):
                    results[column] = pd.to_datetime(results[column]).dt.strftime("%Y-%m-%d %H:%M:%S")
            results = order_balance_triplets(results)
            results.to_sql("product_build_report", conn, if_exists="replace", index=False)
            conn.commit()
        finally:
            conn.close()
        print(f"Product build report saved to database {database_name}")

    def write_material_destination_plan_to_database(
        self,
        payload_transactions,
        blend_report,
        plan_type,
        plan_id="Primary",
        crusher_destination=None,
        direct_tip_movement_rules=None,
        database_name=None,
        destination_reconciliation=None,
    ):
        """Replace one plan's MDP rows while preserving other plan types."""
        database_name = database_name or get_database_path()
        allocation = allocate_final_plan(
            payload_transactions, blend_report, destination_reconciliation,
            plan_type=plan_type, plan_id=plan_id, crusher_destination=crusher_destination,
            direct_tip_movement_rules=direct_tip_movement_rules,
        )
        publication = build_publication(
            payload_transactions, blend_report, allocation, destination_reconciliation,
            plan_type=str(plan_type).strip().lower(), plan_id=str(plan_id or "Primary"),
            crusher_destination=crusher_destination, direct_tip_movement_rules=direct_tip_movement_rules)
        write_publication(publication, database_name, allocation["run"], allocation)
        return publication["material_destination_plan"]

    def write_material_destination_plan_from_database(
        self,
        blend_report,
        plan_type,
        plan_id="Primary",
        crusher_destination=None,
        direct_tip_movement_rules=None,
        database_name=None,
    ):
        """Build MDP rows from the persisted payload population."""
        database_name = database_name or get_database_path()
        connection = sqlite3.connect(database_name)
        try:
            try:
                payload_transactions = pd.read_sql(
                    "SELECT * FROM expit_payload_transactions",
                    connection,
                )
            except (sqlite3.Error, pd.errors.DatabaseError):
                payload_transactions = pd.DataFrame()
        finally:
            connection.close()

        return self.write_material_destination_plan_to_database(
            payload_transactions=payload_transactions,
            blend_report=blend_report,
            plan_type=plan_type,
            plan_id=plan_id,
            crusher_destination=crusher_destination,
            direct_tip_movement_rules=direct_tip_movement_rules,
            database_name=database_name,
        )

    def ensure_material_destination_plan_reports(
        self, database_name=None, crusher_destination=None, direct_tip_movement_rules=None,
    ):
        """Migrate saved schemas without recomputing or overwriting final plan evidence."""
        database_name = database_name or get_database_path()
        with closing(sqlite3.connect(database_name)) as connection, connection:
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            ensure_schema(connection)
            if "material_destination_plan" in names or "expit_payload_transactions" not in names:
                return
            payloads = pd.read_sql_query("SELECT * FROM expit_payload_transactions", connection)
            reports = {kind: pd.read_sql_query(f'SELECT * FROM "{table}"', connection)
                       for kind, table in (("optimised", "optimised_blend_report"), ("manual", "manual_blend_report")) if table in names}
        for kind, frame in reports.items():
            plans = frame.groupby("plan_id", dropna=False) if "plan_id" in frame else [("Primary", frame)]
            for plan_id, report in plans:
                plan_id = str(plan_id) if pd.notna(plan_id) else "Primary"
                legacy = MaterialDestinationPlan.build(payloads, report, kind, plan_id, crusher_destination, direct_tip_movement_rules)
                legacy["status"] = "Legacy snapshot — recalculate"
                write_publication({"material_destination_plan": legacy}, database_name, {"plan_type": kind, "plan_id": plan_id})

    def write_build_report_to_database (self, results: pd.DataFrame):
        database_name = get_database_path()
        conn = sqlite3.connect(database_name)
        try:
            data = results.copy()
            if len(data.columns) == 0:
                data = pd.DataFrame(columns=[
                    "steady_state_number", "steady_state_start_datetime",
                    "steady_state_end_datetime", "agent",
                    "mining_start_datetime", "source", "stockpile",
                    "payload", "delivered_datetime", "closing_balance",
                    "grade_fe", "grade_si", "grade_al", "grade_p", "grade_mn",
                ])
            for column in data.columns:
                if pd.api.types.is_datetime64_any_dtype(data[column]):
                    data[column] = pd.to_datetime(data[column]).dt.strftime('%Y-%m-%d %H:%M:%S')
                elif data[column].dtype == object:
                    data[column] = data[column].map(
                        lambda value: json.dumps(value, default=str)
                        if isinstance(value, (dict, list, tuple, set)) else value
                    )
            data = order_balance_triplets(data)
            data.to_sql("build_report", conn, if_exists="replace", index=False)
            conn.commit()
        finally:
            conn.close()

        print(f"Build report (if used) saved to database {database_name}")
        print(f"Expit payload transactions (if used) saved to database {database_name}")

    @staticmethod
    def _joined_unique_text(values):
        return ", ".join(dict.fromkeys(
            str(value).strip()
            for value in values.dropna()
            if str(value).strip()
        ))

    def write_two_wp_grade_block_turnover_audit(
        self,
        results,
        solver_config=None,
        database_name=None,
    ):
        """Write one audit row per grade block and derived 2WP outcome.

        This is deliberately based on the complete prepared APS payload set,
        not optimiser selections, so unused grade blocks remain visible.
        """
        database_name = database_name or get_database_path()
        source = (
            results.copy()
            if isinstance(results, pd.DataFrame)
            else pd.DataFrame(results or [])
        )
        output_columns = [
            "grade_block",
            "agents",
            "available_payload_wmt",
            "payload_count",
            "first_source_start_datetime",
            "last_source_start_datetime",
            "first_estimated_delivery_datetime",
            "last_estimated_delivery_datetime",
            "aps_schedule_destinations",
            "two_wp_planned_stockpile_destination",
            "two_wp_first_reclaim_datetime",
            "two_wp_destination_turnover_priority",
            "two_wp_destination_resolution",
            "two_wp_turnover_guidance_applicable",
            "two_wp_destination_turnover_guidance_applied",
            "two_wp_destination_turnover_incentive_applied",
        ]
        if source.empty:
            audit = pd.DataFrame(columns=output_columns)
        else:
            defaults = {
                "source": "",
                "agent": "",
                "payload": 0.0,
                "start_datetime": pd.NaT,
                "delivered_datetime": pd.NaT,
                "destination": "",
                "planned_destination": "",
                "two_wp_first_reclaim_datetime": "",
                "two_wp_destination_turnover_priority": None,
                "two_wp_destination_resolution": "",
                "two_wp_turnover_guidance_applicable": False,
            }
            for column, default in defaults.items():
                if column not in source:
                    source[column] = default

            source["grade_block"] = source["source"].astype("string").fillna("")
            source["available_payload_wmt"] = pd.to_numeric(
                source["payload"], errors="coerce"
            ).fillna(0.0)
            for column in ("start_datetime", "delivered_datetime"):
                source[column] = pd.to_datetime(
                    source[column], errors="coerce"
                )
            source["two_wp_destination_resolution"] = (
                source["two_wp_destination_resolution"]
                .astype("string").fillna("").str.strip()
            )
            exact_match = source[
                "two_wp_destination_resolution"
            ].str.lower().eq("exact_2wp")
            source["two_wp_planned_stockpile_destination"] = (
                source["planned_destination"].where(exact_match, "")
                .astype("string").fillna("").str.strip()
            )
            source["two_wp_first_reclaim_datetime"] = (
                source["two_wp_first_reclaim_datetime"].where(
                    exact_match, ""
                ).astype("string").fillna("").str.strip()
            )
            source["two_wp_destination_turnover_priority"] = pd.to_numeric(
                source["two_wp_destination_turnover_priority"],
                errors="coerce",
            ).where(exact_match)
            source["two_wp_turnover_guidance_applicable"] = (
                exact_match
                & source["two_wp_turnover_guidance_applicable"].map(
                    lambda value: str(value).strip().lower()
                    in {"true", "1", "yes"}
                )
            )
            config = dict(solver_config or {})
            guidance_enabled = bool(config.get(
                "two_wp_destination_turnover_guidance_enabled", False
            ))
            try:
                incentive = float(config.get(
                    "two_wp_destination_turnover_incentive", 10.0
                ) or 0.0)
            except (TypeError, ValueError):
                incentive = 10.0
            source[
                "two_wp_destination_turnover_guidance_applied"
            ] = (
                guidance_enabled
                & source["two_wp_turnover_guidance_applicable"]
            )
            priority = source[
                "two_wp_destination_turnover_priority"
            ].fillna(0.0).clip(0.0, 1.0)
            source[
                "two_wp_destination_turnover_incentive_applied"
            ] = (
                priority * incentive
                if incentive >= 0
                else -(1.0 - priority) * abs(incentive)
            )
            source.loc[
                ~source["two_wp_destination_turnover_guidance_applied"],
                "two_wp_destination_turnover_incentive_applied",
            ] = 0.0

            group_keys = [
                "grade_block",
                "two_wp_planned_stockpile_destination",
                "two_wp_first_reclaim_datetime",
                "two_wp_destination_turnover_priority",
                "two_wp_destination_resolution",
                "two_wp_turnover_guidance_applicable",
                "two_wp_destination_turnover_guidance_applied",
                "two_wp_destination_turnover_incentive_applied",
            ]
            audit = source.groupby(
                group_keys, as_index=False, dropna=False, sort=False
            ).agg(
                agents=("agent", self._joined_unique_text),
                available_payload_wmt=("available_payload_wmt", "sum"),
                payload_count=("available_payload_wmt", "size"),
                first_source_start_datetime=("start_datetime", "min"),
                last_source_start_datetime=("start_datetime", "max"),
                first_estimated_delivery_datetime=(
                    "delivered_datetime", "min"
                ),
                last_estimated_delivery_datetime=(
                    "delivered_datetime", "max"
                ),
                aps_schedule_destinations=(
                    "destination", self._joined_unique_text
                ),
            )
            audit = audit[output_columns]
            for column in (
                "first_source_start_datetime",
                "last_source_start_datetime",
                "first_estimated_delivery_datetime",
                "last_estimated_delivery_datetime",
            ):
                audit[column] = pd.to_datetime(
                    audit[column], errors="coerce"
                ).dt.strftime("%Y-%m-%d %H:%M:%S")
            for column in (
                "two_wp_turnover_guidance_applicable",
                "two_wp_destination_turnover_guidance_applied",
            ):
                audit[column] = audit[column].astype(int)

        connection = sqlite3.connect(database_name)
        try:
            audit.to_sql(
                "two_wp_grade_block_turnover_audit",
                connection,
                if_exists="replace",
                index=False,
            )
            connection.commit()
        finally:
            connection.close()
        return audit

    def ensure_two_wp_grade_block_turnover_audit(
        self, solver_config=None, database_name=None
    ):
        """Reconstruct the audit for an older project when payloads exist."""
        database_name = database_name or get_database_path()
        connection = sqlite3.connect(database_name)
        try:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "two_wp_grade_block_turnover_audit" in tables:
                return
            payloads = (
                pd.read_sql(
                    "SELECT * FROM expit_payload_transactions", connection
                )
                if "expit_payload_transactions" in tables
                else pd.DataFrame()
            )
        finally:
            connection.close()
        if "expit_payload_transactions" in tables:
            self.write_two_wp_grade_block_turnover_audit(
                payloads, solver_config, database_name
            )

    def write_expit_payload_transactions_to_database (
        self, results: pd.DataFrame, solver_config=None
    ):
        # Connect to the SQLite database or create it
        database_name = get_database_path()
        reconciliation_attributes = dict(getattr(results, "attrs", {}) or {})
        results = results.copy()
        conn = sqlite3.connect(database_name)
        cursor = conn.cursor()

        # Create the table or use if it already exists
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS expit_payload_transactions (
            agent TEXT,
            source TEXT,
            start_datetime TEXT,
            payload REAL,
            source_grade_fe REAL,
            source_grade_si REAL,
            source_grade_al REAL,
            source_grade_mn REAL,
            source_grade_p REAL,
            destination TEXT,
            delivered_datetime TEXT,
            direct_tip_id TEXT,
            destination_type TEXT,
            planned_destination TEXT,
            fallback_destination TEXT,
            alternate_destination_1 TEXT,
            alternate_destination_2 TEXT,
            aps_direct_tip_candidate INTEGER,
            two_wp_destination_resolution TEXT,
            two_wp_destination_ratio REAL,
            two_wp_turnover_guidance_applicable INTEGER,
            two_wp_first_reclaim_datetime TEXT,
            two_wp_destination_turnover_priority REAL,
            grade_streams_json TEXT,
            source_properties_json TEXT
        )
        ''')

        cursor.execute("PRAGMA table_info(expit_payload_transactions)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        optional_columns = {
            "direct_tip_id": "TEXT",
            "destination_type": "TEXT",
            "planned_destination": "TEXT",
            "fallback_destination": "TEXT",
            "alternate_destination_1": "TEXT",
            "alternate_destination_2": "TEXT",
            "aps_direct_tip_candidate": "INTEGER",
            "two_wp_destination_resolution": "TEXT",
            "two_wp_destination_ratio": "REAL",
            "two_wp_turnover_guidance_applicable": "INTEGER",
            "two_wp_first_reclaim_datetime": "TEXT",
            "two_wp_destination_turnover_priority": "REAL",
            "grade_streams_json": "TEXT",
            "source_properties_json": "TEXT",
        }
        for column_name, column_type in optional_columns.items():
            if column_name not in existing_columns:
                cursor.execute(
                    f"ALTER TABLE expit_payload_transactions ADD COLUMN {column_name} {column_type}"
                )

        cursor.execute('DELETE FROM expit_payload_transactions')

        if "direct_tip_id" not in results.columns:
            results["direct_tip_id"] = [
                f"GB_{index + 1:06d}" for index in range(len(results))
            ]
        for column_name in optional_columns:
            if column_name not in results.columns:
                results[column_name] = (
                    0
                    if column_name == "aps_direct_tip_candidate"
                    else 1.0
                    if column_name == "two_wp_destination_ratio"
                    else ""
                )
        if "grade_streams" in results.columns:
            results["grade_streams_json"] = results["grade_streams"].map(
                lambda value: json.dumps(value or {})
            )
        elif "grade_streams_json" in results.columns:
            results["grade_streams_json"] = results["grade_streams_json"].map(
                lambda value: value if isinstance(value, str) else json.dumps(value or {})
            )
        if "source_properties" in results.columns:
            results["source_properties_json"] = results[
                "source_properties"
            ].map(lambda value: json.dumps(value or {}, sort_keys=True))
        elif "source_properties_json" in results.columns:
            results["source_properties_json"] = results[
                "source_properties_json"
            ].map(
                lambda value: value
                if isinstance(value, str)
                else json.dumps(value or {}, sort_keys=True)
            )
        results["aps_direct_tip_candidate"] = results["aps_direct_tip_candidate"].map(
            lambda value: str(value).strip().lower() in {"true", "1", "yes"}
        ).astype(int)
        results["two_wp_turnover_guidance_applicable"] = results[
            "two_wp_turnover_guidance_applicable"
        ].map(
            lambda value: str(value).strip().lower() in {"true", "1", "yes"}
        ).astype(int)

        # No eligible APS payloads can produce a frame with no columns. Still
        # clear the report and write its audits, but only format actual rows.
        if not results.empty:
            results['start_datetime'] = pd.to_datetime(results['start_datetime']).dt.strftime('%Y-%m-%d %H:%M:%S')
            results['delivered_datetime'] = pd.to_datetime(results['delivered_datetime']).dt.strftime('%Y-%m-%d %H:%M:%S')

        # Insert each row from the DataFrame into the database
        for _, row in results.iterrows():
            cursor.execute('''
            INSERT INTO expit_payload_transactions (
                agent,
                source,
                start_datetime,
                payload,
                source_grade_fe,
                source_grade_si,
                source_grade_al,
                source_grade_mn,
                source_grade_p,
                destination,
                delivered_datetime,
                direct_tip_id,
                destination_type,
                planned_destination,
                fallback_destination,
                alternate_destination_1,
                alternate_destination_2,
                aps_direct_tip_candidate,
                two_wp_destination_resolution,
                two_wp_destination_ratio,
                two_wp_turnover_guidance_applicable,
                two_wp_first_reclaim_datetime,
                two_wp_destination_turnover_priority,
                grade_streams_json,
                source_properties_json
            ) VALUES (
                :agent, 
                :source, 
                :start_datetime, 
                :payload, 
                :source_grade_fe, 
                :source_grade_si, 
                :source_grade_al, 
                :source_grade_mn, 
                :source_grade_p, 
                :destination, 
                :delivered_datetime,
                :direct_tip_id,
                :destination_type,
                :planned_destination,
                :fallback_destination,
                :alternate_destination_1,
                :alternate_destination_2,
                :aps_direct_tip_candidate,
                :two_wp_destination_resolution,
                :two_wp_destination_ratio,
                :two_wp_turnover_guidance_applicable,
                :two_wp_first_reclaim_datetime,
                :two_wp_destination_turnover_priority,
                :grade_streams_json,
                :source_properties_json
            )
            ''', row.to_dict())

        # Commit and close the connection
        conn.commit()
        conn.close()

        self.write_two_wp_grade_block_turnover_audit(
            results, solver_config, database_name
        )
        self.write_expit_sequence_reconciliation(
            reconciliation_attributes, database_name
        )

        print(f"Expit payload transactions saved to database {database_name}")

    def write_expit_sequence_reconciliation(
        self, attributes, database_name=None
    ):
        """Persist the parent-block route audit and live-map source data."""
        attributes = dict(attributes or {})
        planned_transactions = attributes.get(
            "expit_sequence_planned_transactions"
        )
        audit = attributes.get("expit_sequence_audit")
        geometry = attributes.get("expit_sequence_geometry")
        geological_blocks = attributes.get(
            "expit_sequence_geological_blocks"
        )
        actual = attributes.get("expit_sequence_actual_movements")
        summary = attributes.get("expit_sequence_summary") or {}
        if not any(
            isinstance(frame, pd.DataFrame)
            for frame in (
                planned_transactions, audit, geometry,
                geological_blocks, actual,
            )
        ):
            return

        database_name = database_name or get_database_path()
        connection = sqlite3.connect(database_name)
        try:
            for table_name, frame in (
                (
                    "expit_sequence_planned_transactions",
                    planned_transactions,
                ),
                ("expit_sequence_reconciliation_audit", audit),
                ("expit_sequence_geometry", geometry),
                ("expit_sequence_geological_blocks", geological_blocks),
                ("expit_sequence_actual_movements", actual),
            ):
                if isinstance(frame, pd.DataFrame):
                    writable = frame.copy()
                    for column in writable.columns:
                        if pd.api.types.is_datetime64_any_dtype(writable[column]):
                            writable[column] = writable[column].astype(str)
                        elif writable[column].dtype == object:
                            writable[column] = writable[column].map(
                                lambda value: json.dumps(value, default=str)
                                if isinstance(value, (dict, list, tuple, set))
                                else value
                            )
                    writable.to_sql(
                        table_name, connection, if_exists="replace", index=False
                    )

            agent_rows = []
            common = {
                "as_of": str(summary.get("as_of") or ""),
                "schedule_start": str(summary.get("schedule_start") or ""),
                "context_start": str(summary.get("context_start") or ""),
                "completion_tolerance_pct": summary.get(
                    "completion_tolerance_pct"
                ),
            }
            for agent, values in (summary.get("agents") or {}).items():
                agent_rows.append({"agent": agent, **common, **dict(values or {})})
            summary_columns = [
                "agent", "as_of", "schedule_start", "context_start",
                "completion_tolerance_pct",
            ]
            summary_frame = pd.DataFrame(agent_rows)
            if summary_frame.empty:
                summary_frame = pd.DataFrame(columns=summary_columns)
            for column in summary_frame.columns:
                if summary_frame[column].dtype == object:
                    summary_frame[column] = summary_frame[column].map(
                        lambda value: json.dumps(value, default=str)
                        if isinstance(value, (dict, list, tuple, set))
                        else value
                    )
            summary_frame.to_sql(
                "expit_sequence_reconciliation_summary",
                connection,
                if_exists="replace",
                index=False,
            )
            connection.commit()
        finally:
            connection.close()

    def write_optimised_stockpile_depletion_report_to_database(self, blend_report: pd.DataFrame):
        # Connect to the SQLite database or create it
        database_name = get_database_path()
        conn = sqlite3.connect(database_name)
        cursor = conn.cursor()

        # Create the table for granular transactions if it doesn't exist
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS optimised_stockpile_depletion_report (
            start_datetime TEXT,
            end_datetime TEXT,
            steady_state_number INTEGER,
            period INTEGER,
            source TEXT,
            source_opening_balance REAL,
            source_actual_tonnes REAL,
            source_closing_balance REAL,
            source_grade_fe REAL,
            source_grade_si REAL,
            source_grade_al REAL,
            source_grade_p REAL,
            source_grade_mn REAL,
            source_type TEXT
        )
        ''')

        cursor.execute("PRAGMA table_info(optimised_stockpile_depletion_report)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "source_type" not in existing_columns:
            cursor.execute(
                "ALTER TABLE optimised_stockpile_depletion_report ADD COLUMN source_type TEXT"
            )

        # Clear the table
        cursor.execute('DELETE FROM optimised_stockpile_depletion_report')

        # Placeholder for second-level transactions
        second_transactions = []

        def finite_number(value, default=0.0):
            try:
                number = float(value)
            except (TypeError, ValueError):
                return float(default)
            return number if np.isfinite(number) else float(default)

        def report_datetime(value):
            timestamp = pd.to_datetime(value, errors="coerce")
            if pd.isna(timestamp):
                raise ValueError(
                    f"Invalid depletion-report datetime: {value!r}"
                )
            return timestamp.to_pydatetime()

        def datetime_text(value):
            # Preserve fractional steady-state boundaries while retaining the
            # legacy whole-second representation for ordinary rows.
            return value.strftime('%Y-%m-%d %H:%M:%S.%f').rstrip('0').rstrip('.')

        for _, steady_state in blend_report.iterrows():
            steady_state_number = steady_state['steady_state_number']
            period = steady_state['period']
            source = steady_state['source']
            duration_seconds = max(
                finite_number(steady_state['steady_state_duration']) * 3600.0,
                0.0,
            )
            grades = {
                "fe": steady_state['source_grade_fe'],
                "si": steady_state['source_grade_si'],
                "al": steady_state['source_grade_al'],
                "p": steady_state['source_grade_p'],
                "mn": steady_state['source_grade_mn'],
            }
            source_opening_balance = finite_number(
                steady_state['source_opening_balance']
            )
            source_actual_tonnes_total = max(
                finite_number(steady_state['source_actual_tonnes']), 0.0
            )
            expected_source_closing_balance = finite_number(
                steady_state['source_closing_balance']
            )
            source_type = steady_state.get('source_type', 'stockpile')

            # Initialize start_datetime for this steady state
            start_datetime = report_datetime(steady_state['start_datetime'])

            derived_source_closing_balance = (
                source_opening_balance - source_actual_tonnes_total
            )
            if not np.isclose(
                derived_source_closing_balance,
                expected_source_closing_balance,
                rtol=1e-9,
                atol=1e-6,
            ):
                conn.rollback()
                conn.close()
                raise ValueError(
                    "Optimised balance mismatch before depletion expansion for "
                    f"steady state {steady_state_number} and source {source}: "
                    f"opening {source_opening_balance:.9f} - actual "
                    f"{source_actual_tonnes_total:.9f} = "
                    f"{derived_source_closing_balance:.9f}, but the optimiser "
                    f"reported {expected_source_closing_balance:.9f}."
                )

            if duration_seconds <= 0.0:
                if source_actual_tonnes_total > 1e-9:
                    conn.rollback()
                    conn.close()
                    raise ValueError(
                        "Positive depletion has zero duration for steady state "
                        f"{steady_state_number} and source {source}."
                    )
                continue

            whole_seconds = int(duration_seconds)
            fractional_second = duration_seconds - whole_seconds
            interval_durations = [1.0] * whole_seconds
            if fractional_second > 1e-9:
                interval_durations.append(fractional_second)
            elif not interval_durations:
                interval_durations.append(duration_seconds)

            current_balance = source_opening_balance
            remaining_actual_tonnes = source_actual_tonnes_total
            for interval_index, interval_duration in enumerate(interval_durations):
                end_datetime = start_datetime + timedelta(
                    seconds=interval_duration
                )

                # The optimiser's explicit source tonnes are authoritative.
                # Apportion them over the whole-second rows and preserve the
                # final fractional interval so sub-second decision points do
                # not disappear from the depletion report.
                if interval_index == len(interval_durations) - 1:
                    source_actual_tonnes = remaining_actual_tonnes
                else:
                    source_actual_tonnes = (
                        source_actual_tonnes_total
                        * interval_duration
                        / duration_seconds
                    )
                    remaining_actual_tonnes -= source_actual_tonnes
                source_closing_balance = (
                    current_balance - source_actual_tonnes
                )

                # Append to transactions
                second_transactions.append({
                    "start_datetime": datetime_text(start_datetime),
                    "end_datetime": datetime_text(end_datetime),
                    "steady_state_number": steady_state_number,
                    "period": period,
                    "source": source,
                    "source_opening_balance": current_balance,
                    "source_actual_tonnes": source_actual_tonnes,
                    "source_closing_balance": source_closing_balance,
                    "source_type": source_type,
                    **{f"source_grade_{k}": v for k, v in grades.items()}
                })

                # Update for next second
                start_datetime = end_datetime
                current_balance = source_closing_balance

            if not np.isclose(
                current_balance,
                expected_source_closing_balance,
                rtol=1e-9,
                atol=1e-6,
            ):
                conn.rollback()
                conn.close()
                raise ValueError(
                    "Balance mismatch after depletion expansion for steady "
                    f"state {steady_state_number} and source {source}: "
                    f"expanded closing {current_balance:.9f}, expected "
                    f"{expected_source_closing_balance:.9f}."
                )

        # Convert transactions to DataFrame
        transactions_df = pd.DataFrame(second_transactions)

        # Insert second transactions into the database
        for _, row in transactions_df.iterrows():
            cursor.execute('''
            INSERT INTO optimised_stockpile_depletion_report (
                start_datetime,
                end_datetime,
                steady_state_number,
                period,
                source,
                source_opening_balance,
                source_actual_tonnes,
                source_closing_balance,
                source_grade_fe,
                source_grade_si,
                source_grade_al,
                source_grade_p,
                source_grade_mn,
                source_type
            ) VALUES (
                :start_datetime,
                :end_datetime,
                :steady_state_number,
                :period,
                :source,
                :source_opening_balance,
                :source_actual_tonnes,
                :source_closing_balance,
                :source_grade_fe,
                :source_grade_si,
                :source_grade_al,
                :source_grade_p,
                :source_grade_mn,
                :source_type
            )
            ''', row.to_dict())

        # Commit and close the connection
        conn.commit()
        conn.close()

        print(f"Optimised stockpile depletion report saved to database {database_name}")

class StockpileProfileReport:
    @staticmethod
    def read_table_or_empty(conn, table_name, columns):
        try:
            data = pd.read_sql(f'SELECT * FROM {table_name}', conn)
        except (sqlite3.Error, pd.errors.DatabaseError) as error:
            print(f"Warning: could not read {table_name}: {error}")
            data = pd.DataFrame(columns=columns)

        for column in columns:
            if column not in data.columns:
                data[column] = pd.NA
        return data

    @staticmethod
    def weighted_average(group, value_column, weight_column):
        values = pd.to_numeric(group.get(value_column), errors="coerce")
        weights = pd.to_numeric(group.get(weight_column), errors="coerce").fillna(0)
        valid = values.notna() & (weights > 0)
        if valid.any() and weights[valid].sum() > 0:
            return float((values[valid] * weights[valid]).sum() / weights[valid].sum())
        return float(values.dropna().mean()) if values.dropna().any() else 0

    @staticmethod
    def assign_steady_state_window(data, time_column, steady_states):
        data = data.copy()
        data[time_column] = pd.to_datetime(data[time_column], errors="coerce")
        data["_steady_state_start"] = pd.NaT
        data["_steady_state_end"] = pd.NaT

        if data.empty or steady_states.empty:
            return data

        for _, state in steady_states.iterrows():
            start_time = state["start_datetime"]
            end_time = state["end_datetime"]
            mask = (data[time_column] >= start_time) & (data[time_column] < end_time)
            data.loc[mask, "steady_state_number"] = state["steady_state_number"]
            data.loc[mask, "_steady_state_start"] = start_time
            data.loc[mask, "_steady_state_end"] = end_time

        return data

    @staticmethod
    def aggregate_grade_block_movements(data, tonnes_column, grade_prefix, extra_columns=None):
        if data.empty:
            return pd.DataFrame()

        extra_columns = extra_columns or []
        data = data.copy()
        data[tonnes_column] = pd.to_numeric(data[tonnes_column], errors="coerce").fillna(0)
        data = data[
            data["source"].notna()
            & data["steady_state_number"].notna()
            & (data[tonnes_column] > 0)
        ]
        if data.empty:
            return pd.DataFrame()

        records = []
        for (source, steady_state_number), group in data.groupby(
            ["source", "steady_state_number"], dropna=False, sort=False
        ):
            record = {
                "source": source,
                "steady_state_number": steady_state_number,
                "_steady_state_start": group["_steady_state_start"].dropna().iloc[0]
                if not group["_steady_state_start"].dropna().empty
                else pd.NaT,
                "_steady_state_end": group["_steady_state_end"].dropna().iloc[0]
                if not group["_steady_state_end"].dropna().empty
                else pd.NaT,
                tonnes_column: float(group[tonnes_column].sum()),
            }
            for grade in ["fe", "si", "al", "p", "mn"]:
                record[f"grade_{grade}"] = StockpileProfileReport.weighted_average(
                    group,
                    f"{grade_prefix}{grade}",
                    tonnes_column,
                )
            for column in extra_columns:
                values = [
                    str(value)
                    for value in group[column].dropna().unique()
                    if str(value).strip()
                ]
                record[column] = ", ".join(values)
            if "delivered_datetime" in group:
                delivered = pd.to_datetime(group["delivered_datetime"], errors="coerce").dropna()
                if not delivered.empty:
                    record["first_delivery_datetime"] = delivered.min()
                    record["last_delivery_datetime"] = delivered.max()
            records.append(record)

        return pd.DataFrame(records)

    @staticmethod
    def create_grade_block_profile_rows(
        expit_payload_transactions,
        build_report,
        optimised_blend_report,
        steady_states,
        profile_start_datetime,
    ):
        profile_columns = [
            "steady_state_number", "agent", "time", "stockpile", "balance",
            "grade_fe", "grade_si", "grade_al", "grade_p", "grade_mn",
            "source_or_destination", "source_type", "arrival_tonnes",
            "tonnes_to_crusher", "tonnes_to_stockpile", "movement_tonnes",
            "movement_destination", "steady_state_start_datetime",
            "steady_state_end_datetime"
        ]
        if steady_states.empty:
            return pd.DataFrame(columns=profile_columns)

        expit_payload_transactions = expit_payload_transactions.copy()
        build_report = build_report.copy()
        optimised_blend_report = optimised_blend_report.copy()

        arrival_groups = pd.DataFrame()
        if not expit_payload_transactions.empty:
            for column in ["payload", "source_grade_fe", "source_grade_si", "source_grade_al", "source_grade_p", "source_grade_mn"]:
                if column in expit_payload_transactions:
                    expit_payload_transactions[column] = pd.to_numeric(
                        expit_payload_transactions[column], errors="coerce"
                    ).fillna(0)
            expit_payload_transactions = StockpileProfileReport.assign_steady_state_window(
                expit_payload_transactions,
                "delivered_datetime",
                steady_states,
            )
            arrival_groups = StockpileProfileReport.aggregate_grade_block_movements(
                expit_payload_transactions,
                "payload",
                "source_grade_",
            ).rename(columns={"payload": "arrival_tonnes"})

        crusher_groups = pd.DataFrame()
        if not optimised_blend_report.empty and "source_type" in optimised_blend_report:
            direct_tip_rows = optimised_blend_report[
                optimised_blend_report["source_type"].astype(str).str.lower().eq("grade_block")
            ].copy()
            if not direct_tip_rows.empty:
                direct_tip_rows["source_actual_tonnes"] = pd.to_numeric(
                    direct_tip_rows["source_actual_tonnes"], errors="coerce"
                ).fillna(0)
                direct_tip_rows["_steady_state_start"] = pd.to_datetime(
                    direct_tip_rows["start_datetime"], errors="coerce"
                )
                direct_tip_rows["_steady_state_end"] = pd.to_datetime(
                    direct_tip_rows["end_datetime"], errors="coerce"
                )
                crusher_groups = StockpileProfileReport.aggregate_grade_block_movements(
                    direct_tip_rows,
                    "source_actual_tonnes",
                    "source_grade_",
                ).rename(columns={"source_actual_tonnes": "tonnes_to_crusher"})

        stockpile_groups = pd.DataFrame()
        if not build_report.empty:
            build_report["payload"] = pd.to_numeric(build_report["payload"], errors="coerce").fillna(0)
            build_report["delivered_datetime"] = pd.to_datetime(
                build_report["delivered_datetime"], errors="coerce"
            )
            build_report["_steady_state_start"] = pd.to_datetime(
                build_report.get("steady_state_start_datetime"), errors="coerce"
            )
            build_report["_steady_state_end"] = pd.to_datetime(
                build_report.get("steady_state_end_datetime"), errors="coerce"
            )

            if not expit_payload_transactions.empty:
                grade_lookup = expit_payload_transactions[[
                    column for column in [
                        "source", "delivered_datetime", "source_grade_fe",
                        "source_grade_si", "source_grade_al", "source_grade_p",
                        "source_grade_mn"
                    ]
                    if column in expit_payload_transactions.columns
                ]].copy()
                grade_lookup["delivered_datetime"] = pd.to_datetime(
                    grade_lookup["delivered_datetime"], errors="coerce"
                )
                build_report = build_report.merge(
                    grade_lookup.drop_duplicates(subset=["source", "delivered_datetime"]),
                    how="left",
                    on=["source", "delivered_datetime"],
                    suffixes=("", "_expit"),
                )

            for grade in ["fe", "si", "al", "p", "mn"]:
                source_grade_column = f"source_grade_{grade}"
                if source_grade_column not in build_report:
                    build_report[source_grade_column] = build_report.get(f"grade_{grade}", 0)
                build_report[source_grade_column] = pd.to_numeric(
                    build_report[source_grade_column], errors="coerce"
                ).fillna(pd.to_numeric(build_report.get(f"grade_{grade}", 0), errors="coerce"))

            if build_report["steady_state_number"].isna().any():
                build_report = StockpileProfileReport.assign_steady_state_window(
                    build_report,
                    "delivered_datetime",
                    steady_states,
                )

            stockpile_groups = StockpileProfileReport.aggregate_grade_block_movements(
                build_report,
                "payload",
                "source_grade_",
                extra_columns=["stockpile"],
            ).rename(columns={"payload": "tonnes_to_stockpile"})

        movement_groups = arrival_groups
        for frame in [crusher_groups, stockpile_groups]:
            if frame.empty:
                continue
            if movement_groups.empty:
                movement_groups = frame
            else:
                movement_groups = movement_groups.merge(
                    frame,
                    how="outer",
                    on=["source", "steady_state_number"],
                    suffixes=("", "_movement"),
                )
                for column in ["_steady_state_start", "_steady_state_end"]:
                    movement_column = f"{column}_movement"
                    if movement_column in movement_groups:
                        movement_groups[column] = movement_groups[column].combine_first(
                            movement_groups[movement_column]
                        )
                        movement_groups = movement_groups.drop(columns=[movement_column])
                for grade in ["fe", "si", "al", "p", "mn"]:
                    movement_column = f"grade_{grade}_movement"
                    if movement_column in movement_groups:
                        movement_groups[f"grade_{grade}"] = movement_groups[f"grade_{grade}"].combine_first(
                            movement_groups[movement_column]
                        )
                        movement_groups = movement_groups.drop(columns=[movement_column])

        if movement_groups.empty:
            return pd.DataFrame(columns=profile_columns)

        for column in ["arrival_tonnes", "tonnes_to_crusher", "tonnes_to_stockpile"]:
            if column not in movement_groups:
                movement_groups[column] = 0
            movement_groups[column] = pd.to_numeric(movement_groups[column], errors="coerce").fillna(0)

        movement_groups["effective_arrival_tonnes"] = movement_groups[[
            "arrival_tonnes", "tonnes_to_crusher", "tonnes_to_stockpile"
        ]].assign(
            total_depleted=movement_groups["tonnes_to_crusher"] + movement_groups["tonnes_to_stockpile"]
        )[["arrival_tonnes", "total_depleted"]].max(axis=1)

        records = []
        for source, group in movement_groups.sort_values(
            ["source", "_steady_state_start"]
        ).groupby("source", sort=False):
            running_balance = 0.0
            records.append({
                "steady_state_number": None,
                "agent": "EX",
                "time": profile_start_datetime,
                "stockpile": source,
                "balance": running_balance,
                "grade_fe": group["grade_fe"].dropna().iloc[0] if group["grade_fe"].dropna().any() else 0,
                "grade_si": group["grade_si"].dropna().iloc[0] if group["grade_si"].dropna().any() else 0,
                "grade_al": group["grade_al"].dropna().iloc[0] if group["grade_al"].dropna().any() else 0,
                "grade_p": group["grade_p"].dropna().iloc[0] if group["grade_p"].dropna().any() else 0,
                "grade_mn": group["grade_mn"].dropna().iloc[0] if group["grade_mn"].dropna().any() else 0,
                "source_or_destination": "Not yet delivered",
                "source_type": "grade_block",
                "arrival_tonnes": 0,
                    "tonnes_to_crusher": 0,
                    "tonnes_to_stockpile": 0,
                    "movement_tonnes": 0,
                    "movement_destination": "",
                    "steady_state_start_datetime": pd.NaT,
                    "steady_state_end_datetime": pd.NaT,
                })

            for _, row in group.sort_values("_steady_state_start").iterrows():
                start_time = row["_steady_state_start"]
                end_time = row["_steady_state_end"]
                if pd.isna(start_time) or pd.isna(end_time):
                    continue

                arrived = float(row["effective_arrival_tonnes"] or 0)
                to_crusher = float(row["tonnes_to_crusher"] or 0)
                to_stockpile = float(row["tonnes_to_stockpile"] or 0)
                total_depleted = to_crusher + to_stockpile
                opening_balance = running_balance + arrived
                closing_balance = max(opening_balance - total_depleted, 0)

                destinations = []
                if to_crusher > 0:
                    destinations.append("Crusher")
                if to_stockpile > 0:
                    stockpile_text = row.get("stockpile", "")
                    destinations.append(f"Stockpile ({stockpile_text})" if stockpile_text else "Stockpile")
                movement_destination = " + ".join(destinations)

                records.append({
                    "steady_state_number": row["steady_state_number"],
                    "agent": "EX",
                    "time": start_time,
                    "stockpile": source,
                    "balance": opening_balance,
                    "grade_fe": row.get("grade_fe", 0),
                    "grade_si": row.get("grade_si", 0),
                    "grade_al": row.get("grade_al", 0),
                    "grade_p": row.get("grade_p", 0),
                    "grade_mn": row.get("grade_mn", 0),
                    "source_or_destination": "Payloads arrived at ROM / Crusher Area",
                    "source_type": "grade_block",
                    "arrival_tonnes": arrived,
                    "tonnes_to_crusher": 0,
                    "tonnes_to_stockpile": 0,
                    "movement_tonnes": 0,
                    "movement_destination": "Arrived at ROM / Crusher Area",
                    "steady_state_start_datetime": start_time,
                    "steady_state_end_datetime": end_time,
                })
                records.append({
                    "steady_state_number": row["steady_state_number"],
                    "agent": "EX",
                    "time": start_time,
                    "stockpile": source,
                    "balance": closing_balance,
                    "grade_fe": row.get("grade_fe", 0),
                    "grade_si": row.get("grade_si", 0),
                    "grade_al": row.get("grade_al", 0),
                    "grade_p": row.get("grade_p", 0),
                    "grade_mn": row.get("grade_mn", 0),
                    "source_or_destination": movement_destination or "No depletion",
                    "source_type": "grade_block",
                    "arrival_tonnes": 0,
                    "tonnes_to_crusher": to_crusher,
                    "tonnes_to_stockpile": to_stockpile,
                    "movement_tonnes": total_depleted,
                    "movement_destination": movement_destination,
                    "steady_state_start_datetime": start_time,
                    "steady_state_end_datetime": end_time,
                })
                running_balance = closing_balance

        return pd.DataFrame(records, columns=profile_columns)

    @staticmethod
    def write_optimised_stockpile_profile_report_to_database(periods: PeriodManager):
        
        start_datetime = periods.get_periods()["preplan_start"]
        end_datetime = periods.horizon_end()
        
        # Connect to the SQLite database
        database_name = get_database_path()
        conn = sqlite3.connect(database_name)

        try:
            build_report = StockpileProfileReport.read_table_or_empty(
                conn,
                "build_report",
                [
                    "steady_state_number", "agent", "delivered_datetime", "stockpile",
                    "closing_balance", "grade_fe", "grade_si", "grade_al", "grade_p",
                    "grade_mn", "source"
                ],
            )
            optimised_stockpile_depletion_report = StockpileProfileReport.read_table_or_empty(
                conn,
                "optimised_stockpile_depletion_report",
                [
                    "start_datetime", "steady_state_number", "period", "source",
                    "source_opening_balance", "source_grade_fe", "source_grade_si",
                    "source_grade_al", "source_grade_p", "source_grade_mn",
                    "source_type"
                ],
            )
            opening_stockpile_inventories = StockpileProfileReport.read_table_or_empty(
                conn,
                "opening_stockpile_inventories",
                [
                    "name", "balance", "grade_fe", "grade_si", "grade_al",
                    "grade_p", "grade_mn"
                ],
            )
            opening_amt_stockpile_inventories = StockpileProfileReport.read_table_or_empty(
                conn,
                "opening_AMT_stockpile_inventories",
                [
                    "footprint", "balance", "grade_fe", "grade_si", "grade_al",
                    "grade_p", "grade_mn"
                ],
            )
            optimised_blend_report = StockpileProfileReport.read_table_or_empty(
                conn,
                "optimised_blend_report",
                [
                    "start_datetime", "end_datetime", "steady_state_number",
                    "source", "source_id", "source_type", "source_actual_tonnes",
                    "source_grade_fe", "source_grade_si", "source_grade_al",
                    "source_grade_p", "source_grade_mn", "crusher_actual_tonnes",
                    "actual_direct_tip_ratio"
                ],
            )
            expit_payload_transactions = StockpileProfileReport.read_table_or_empty(
                conn,
                "expit_payload_transactions",
                [
                    "agent", "source", "start_datetime", "payload",
                    "source_grade_fe", "source_grade_si", "source_grade_al",
                    "source_grade_mn", "source_grade_p", "destination",
                    "delivered_datetime", "direct_tip_id", "destination_type",
                    "planned_destination", "fallback_destination",
                    "aps_direct_tip_candidate"
                ],
            )

            steady_states = optimised_blend_report[[
                "steady_state_number", "start_datetime", "end_datetime"
            ]].dropna(subset=["steady_state_number", "start_datetime", "end_datetime"]).drop_duplicates()
            if not steady_states.empty:
                steady_states = steady_states.copy()
                steady_states["start_datetime"] = pd.to_datetime(
                    steady_states["start_datetime"], errors="coerce"
                )
                steady_states["end_datetime"] = pd.to_datetime(
                    steady_states["end_datetime"], errors="coerce"
                )
                steady_states = steady_states.dropna(
                    subset=["start_datetime", "end_datetime"]
                ).sort_values("start_datetime")

            # Prepare data from build_report (table 1)
            build_report_prepared = build_report.rename(columns={
                'delivered_datetime': 'time',
                'closing_balance': 'balance',
                'source': 'source_or_destination',
                'agent': 'agent',
            })
            build_report_prepared['source_type'] = 'stockpile'
            build_report_prepared = build_report_prepared[[
                'steady_state_number', 'agent', 'time', 'stockpile', 'balance',
                'grade_fe', 'grade_si', 'grade_al', 'grade_p', 'grade_mn',
                'source_or_destination', 'source_type'
            ]]
            build_report_prepared["source_or_destination"] = (
                "From " + build_report_prepared["source_or_destination"].astype(str)
            )

            # Prepare data from optimised_stockpile_depletion_report (table 2)
            stockpile_depletion_report = optimised_stockpile_depletion_report[
                ~optimised_stockpile_depletion_report["source_type"].astype(str).str.lower().eq("grade_block")
            ].copy()
            depletion_report_prepared = stockpile_depletion_report.rename(columns={
                'start_datetime': 'time',
                'source_opening_balance': 'balance',
                'source': 'stockpile',
                'source_grade_fe': 'grade_fe',
                'source_grade_si': 'grade_si',
                'source_grade_al': 'grade_al',
                'source_grade_p': 'grade_p',
                'source_grade_mn': 'grade_mn',
            })
            depletion_report_prepared['agent'] = "RC"
            depletion_report_prepared['source_or_destination'] = 'Crusher'
            depletion_report_prepared['source_type'] = (
                depletion_report_prepared['source_type']
                .fillna('')
                .replace('', 'stockpile')
            )
            depletion_report_prepared = depletion_report_prepared[[
                'steady_state_number', 'agent', 'time', 'stockpile', 'balance',
                'grade_fe', 'grade_si', 'grade_al', 'grade_p', 'grade_mn',
                'source_or_destination', 'source_type'
            ]]

            grade_block_profile_prepared = StockpileProfileReport.create_grade_block_profile_rows(
                expit_payload_transactions,
                build_report,
                optimised_blend_report,
                steady_states,
                start_datetime,
            )

            movement_columns = [
                "arrival_tonnes", "tonnes_to_crusher", "tonnes_to_stockpile",
                "movement_tonnes", "movement_destination"
            ]
            for frame in [build_report_prepared, depletion_report_prepared]:
                for column in movement_columns:
                    frame[column] = 0 if column != "movement_destination" else ""

            # Combine the two reports
            combined_report = pd.concat(
                [build_report_prepared, depletion_report_prepared, grade_block_profile_prepared],
                ignore_index=True
            )

            # Backfill baseline rows only for stockpiles that are actually used as
            # crusher feed sources or build destinations. AMT openings are stored
            # at hex level, so aggregate them by footprint before matching.
            used_stockpiles = set()

            def add_names(values):
                for value in values:
                    if pd.isna(value):
                        continue
                    text = str(value).strip()
                    if text:
                        used_stockpiles.add(text)

            add_names(build_report["stockpile"].dropna().unique())

            if not optimised_stockpile_depletion_report.empty:
                source_types = (
                    optimised_stockpile_depletion_report["source_type"]
                    .fillna("")
                    .astype(str)
                    .str.lower()
                )
                add_names(
                    optimised_stockpile_depletion_report.loc[
                        ~source_types.isin({"grade_block", "grade block", "gradeblock"}),
                        "source",
                    ].dropna().unique()
                )

            if not optimised_blend_report.empty:
                source_types = (
                    optimised_blend_report["source_type"]
                    .fillna("")
                    .astype(str)
                    .str.lower()
                )
                add_names(
                    optimised_blend_report.loc[
                        ~source_types.isin({"grade_block", "grade block", "gradeblock"}),
                        "source",
                    ].dropna().unique()
                )

            weighted_opening_rows = opening_stockpile_inventories.rename(columns={
                'name': 'stockpile',
                'balance': 'balance',
                'grade_fe': 'grade_fe',
                'grade_si': 'grade_si',
                'grade_al': 'grade_al',
                'grade_p': 'grade_p',
                'grade_mn': 'grade_mn'
            }).copy()
            weighted_opening_rows["opening_source_priority"] = 1

            amt_opening_rows = pd.DataFrame(columns=[
                "stockpile", "balance", "grade_fe", "grade_si", "grade_al",
                "grade_p", "grade_mn", "opening_source_priority"
            ])
            if not opening_amt_stockpile_inventories.empty:
                amt_openings = opening_amt_stockpile_inventories.copy()
                amt_openings["balance"] = (
                    pd.to_numeric(amt_openings["balance"], errors="coerce")
                    .fillna(0)
                    .clip(lower=0)
                )
                amt_openings = amt_openings[
                    amt_openings["footprint"].notna()
                    & (amt_openings["footprint"].astype(str).str.strip() != "")
                    & (amt_openings["balance"] > 0)
                ]
                amt_records = []
                for footprint, group in amt_openings.groupby("footprint", sort=False):
                    record = {
                        "stockpile": str(footprint).strip(),
                        "balance": float(group["balance"].sum()),
                        "opening_source_priority": 0,
                    }
                    for grade in ["fe", "si", "al", "p", "mn"]:
                        record[f"grade_{grade}"] = StockpileProfileReport.weighted_average(
                            group,
                            f"grade_{grade}",
                            "balance",
                        )
                    amt_records.append(record)
                if amt_records:
                    amt_opening_rows = pd.DataFrame(amt_records)

            opening_sources = pd.concat(
                [weighted_opening_rows, amt_opening_rows],
                ignore_index=True,
                sort=False,
            )
            expected_opening_columns = [
                "stockpile", "balance", "grade_fe", "grade_si", "grade_al",
                "grade_p", "grade_mn", "opening_source_priority"
            ]
            for column in expected_opening_columns:
                if column not in opening_sources:
                    opening_sources[column] = pd.NA
            opening_sources["stockpile"] = opening_sources["stockpile"].astype(str).str.strip()
            opening_sources = opening_sources[
                opening_sources["stockpile"].isin(used_stockpiles)
            ].copy()
            opening_sources = (
                opening_sources
                .sort_values(["opening_source_priority", "stockpile"])
                .drop_duplicates(subset=["stockpile"], keep="first")
            )

            combined_times = combined_report[["stockpile", "time"]].copy()
            combined_times["stockpile"] = combined_times["stockpile"].astype(str).str.strip()
            combined_times["time"] = pd.to_datetime(combined_times["time"], errors="coerce")
            profile_start = pd.to_datetime(start_datetime, errors="coerce")
            stockpiles_with_start_profile_row = {
                str(value).strip()
                for value in combined_times.loc[
                    combined_times["time"].notna()
                    & (combined_times["time"] <= profile_start),
                    "stockpile",
                ].dropna().unique()
                if str(value).strip()
            }
            opening_stockpiles_not_in_combined = opening_sources[
                ~opening_sources['stockpile'].isin(stockpiles_with_start_profile_row)
            ].copy()
            opening_stockpiles_not_in_combined = opening_stockpiles_not_in_combined[
                [
                    "stockpile", "balance", "grade_fe", "grade_si",
                    "grade_al", "grade_p", "grade_mn"
                ]
            ]
            opening_stockpiles_not_in_combined['time'] = start_datetime
            opening_stockpiles_not_in_combined['steady_state_number'] = None
            opening_stockpiles_not_in_combined['agent'] = None
            opening_stockpiles_not_in_combined['source_or_destination'] = None
            opening_stockpiles_not_in_combined['source_type'] = 'stockpile'
            for column in movement_columns:
                opening_stockpiles_not_in_combined[column] = 0 if column != "movement_destination" else ""

            # Combine all reports
            report_frames = [combined_report]
            if not opening_stockpiles_not_in_combined.empty:
                report_frames.append(opening_stockpiles_not_in_combined)
            final_combined_report = pd.concat(report_frames, ignore_index=True)
            for column in movement_columns:
                if column not in final_combined_report:
                    final_combined_report[column] = 0 if column != "movement_destination" else ""

            # Extend the transactions to minute-level granularity
            extended_report = []
            movement_quantity_columns = [
                "arrival_tonnes", "tonnes_to_crusher", "tonnes_to_stockpile",
                "movement_tonnes"
            ]
            movement_text_columns = ["movement_destination"]
            for stockpile, group in final_combined_report.groupby('stockpile'):
                group = group.copy()
                # Parse time column
                group['time'] = pd.to_datetime(group['time'], errors='coerce')
                group = group.dropna(subset=['time'])
                if group.empty:
                    continue

                # Create minute range
                all_times = pd.date_range(start=start_datetime, end=end_datetime, freq='min')
                extended_group = pd.DataFrame({'time': all_times})

                # Truncate seconds in both DataFrames to only honor hours and minutes
                extended_group['time'] = pd.to_datetime(extended_group['time']).dt.floor('min')
                group['time'] = pd.to_datetime(group['time']).dt.floor('min')

                # Perform the merge on the truncated time
                extended_group = extended_group.merge(group, how='left', on='time')
                movement_quantity_values = (
                    extended_group[movement_quantity_columns]
                    .apply(pd.to_numeric, errors="coerce")
                    .fillna(0)
                )
                movement_text_values = extended_group[movement_text_columns].fillna("")

                # Fill forward and backward with the first and last transaction values
                extended_group = extended_group.infer_objects(copy=False).ffill().bfill()
                extended_group[movement_quantity_columns] = movement_quantity_values
                extended_group[movement_text_columns] = movement_text_values

                # Append to the extended report list
                extended_report.append(extended_group)

            # Filter each DataFrame in the list to exclude rows where 'stockpile' is null
            extended_report = [df[df['stockpile'].notna()] for df in extended_report]

            profile_columns = [
                "time", "steady_state_number", "agent", "stockpile", "balance",
                "grade_fe", "grade_si", "grade_al", "grade_p", "grade_mn",
                "source_or_destination", "source_type", "arrival_tonnes",
                "tonnes_to_crusher", "tonnes_to_stockpile", "movement_tonnes",
                "movement_destination"
            ]
            if extended_report:
                extended_combined_report = pd.concat(extended_report, ignore_index=True)
            else:
                extended_combined_report = pd.DataFrame(columns=profile_columns)

            # Reset steady_state_number based on optimised_blend_report
            optimised_blend_report['start_datetime'] = pd.to_datetime(optimised_blend_report['start_datetime'])
            optimised_blend_report['end_datetime'] = pd.to_datetime(optimised_blend_report['end_datetime'])

            # Truncate to minutes for comparison with extended_combined_report
            optimised_blend_report['start_datetime'] = optimised_blend_report['start_datetime'].dt.floor('min')
            optimised_blend_report['end_datetime'] = optimised_blend_report['end_datetime'].dt.floor('min')

            # Convert columns to NumPy arrays for faster operations
            start_times = optimised_blend_report['start_datetime'].to_numpy()
            end_times = optimised_blend_report['end_datetime'].to_numpy()
            steady_states = optimised_blend_report['steady_state_number'].to_numpy()
            times = extended_combined_report['time'].to_numpy()

            # Create an empty array to store results
            steady_state_numbers = np.full(len(times), np.nan)

            # Vectorized interval checks
            for i, time in enumerate(times):
                mask = (start_times <= time) & (end_times > time)
                if np.any(mask):
                    steady_state_numbers[i] = steady_states[np.argmax(mask)]  # Get the first match

            # Assign results back to the DataFrame
            extended_combined_report['steady_state_number'] = steady_state_numbers
            
            def map_steady_state_number(time):
                match = optimised_blend_report[
                    (optimised_blend_report['start_datetime'] <= time) &
                    (optimised_blend_report['end_datetime'] > time)
                ]
                return match['steady_state_number'].iloc[0] if not match.empty else None

            #extended_combined_report['steady_state_number'] = extended_combined_report['time'].apply(map_steady_state_number)

            # Save the extended report to the database
            extended_combined_report.to_sql('optimised_stockpile_profile_report', conn, if_exists='replace', index=False)

            print(f"Optimised stockpile profile report saved to database {database_name}")

        finally:
            # Close the database connection
            conn.close()

