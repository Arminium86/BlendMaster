"""2WP closing ROM stock inputs and BlendMaster balance comparisons."""

from __future__ import annotations

import re

import pandas as pd


class ClosingROMStocksCompliance:
    REQUIRED_COLUMNS = [
        "Source.Name",
        "Period.Start DateTime",
        "Period.End DateTime",
        "Mining.wetTonnes",
    ]
    NORMALIZED_COLUMNS = [
        "stockpile",
        "two_wp_period_start_datetime",
        "two_wp_period_end_datetime",
        "two_wp_closing_rom_wmt",
    ]
    REPORT_COLUMNS = [
        "plan_id",
        "plan_type",
        "period",
        "period_start_datetime",
        "period_end_datetime",
        "stockpile",
        "blendmaster_balance_datetime",
        "blendmaster_closing_rom_wmt",
        "two_wp_closing_rom_wmt",
        "variance_wmt",
        "variance_pct",
        "two_wp_period_start_datetime",
        "two_wp_period_end_datetime",
        "comparison_status",
    ]

    @staticmethod
    def normalize_stockpile_name(value):
        value = str(value or "").strip().replace("\\", "/")
        value = re.sub(r"/+", "/", value).strip(" /")
        value = re.sub(r"^stockpiles/", "", value, flags=re.IGNORECASE)
        return value.rsplit("/", 1)[-1].strip().upper()

    @staticmethod
    def _naive_timestamp(value):
        timestamp = pd.to_datetime(value, errors="coerce")
        if pd.isna(timestamp):
            return pd.NaT
        timestamp = pd.Timestamp(timestamp)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_localize(None)
        return timestamp

    @classmethod
    def read_workbook(cls, path):
        if not str(path or "").strip():
            return pd.DataFrame(columns=cls.NORMALIZED_COLUMNS)
        if not str(path).strip().lower().endswith(".xlsx"):
            raise ValueError(
                "2WP Closing ROM Stocks must be supplied as an .xlsx workbook."
            )
        data = pd.read_excel(path)
        missing = [
            column for column in cls.REQUIRED_COLUMNS
            if column not in data.columns
        ]
        if missing:
            raise ValueError(
                "2WP Closing ROM Stocks workbook is missing column(s): "
                + ", ".join(missing)
            )
        unexpected = [
            column for column in data.columns
            if column not in cls.REQUIRED_COLUMNS
        ]
        if unexpected:
            raise ValueError(
                "2WP Closing ROM Stocks workbook must contain exactly the four "
                "documented columns. Unexpected column(s): "
                + ", ".join(str(column) for column in unexpected)
            )
        data = data[cls.REQUIRED_COLUMNS].copy()
        data["stockpile"] = data["Source.Name"].map(
            cls.normalize_stockpile_name
        )
        data["two_wp_period_start_datetime"] = data[
            "Period.Start DateTime"
        ].map(cls._naive_timestamp)
        data["two_wp_period_end_datetime"] = data[
            "Period.End DateTime"
        ].map(cls._naive_timestamp)
        data["two_wp_closing_rom_wmt"] = pd.to_numeric(
            data["Mining.wetTonnes"], errors="coerce"
        )

        invalid = data[
            data["stockpile"].eq("")
            | data["two_wp_period_start_datetime"].isna()
            | data["two_wp_period_end_datetime"].isna()
            | data["two_wp_closing_rom_wmt"].isna()
            | (data["two_wp_closing_rom_wmt"] < 0)
            | (
                data["two_wp_period_end_datetime"]
                <= data["two_wp_period_start_datetime"]
            )
        ]
        if not invalid.empty:
            rows = ", ".join(str(index + 2) for index in invalid.index[:10])
            raise ValueError(
                "2WP Closing ROM Stocks workbook contains invalid source, "
                "datetime or closing-tonnes values on Excel row(s): " + rows
            )

        duplicates = data.duplicated(
            ["stockpile", "two_wp_period_start_datetime", "two_wp_period_end_datetime"],
            keep=False,
        )
        if duplicates.any():
            names = ", ".join(sorted(data.loc[duplicates, "stockpile"].unique())[:10])
            raise ValueError(
                "2WP Closing ROM Stocks workbook contains duplicate closing "
                f"periods for: {names}."
            )
        return data[cls.NORMALIZED_COLUMNS].sort_values(
            ["stockpile", "two_wp_period_end_datetime"]
        ).reset_index(drop=True)

    @classmethod
    def normalize_target_rows(cls, rows):
        if isinstance(rows, pd.DataFrame):
            data = rows.copy()
        else:
            data = pd.DataFrame(rows or [])
        if data.empty:
            return pd.DataFrame(columns=cls.NORMALIZED_COLUMNS)
        if set(cls.NORMALIZED_COLUMNS).issubset(data.columns):
            data = data[cls.NORMALIZED_COLUMNS].copy()
            data["stockpile"] = data["stockpile"].map(
                cls.normalize_stockpile_name
            )
            for column in (
                "two_wp_period_start_datetime",
                "two_wp_period_end_datetime",
            ):
                data[column] = data[column].map(cls._naive_timestamp)
            data["two_wp_closing_rom_wmt"] = pd.to_numeric(
                data["two_wp_closing_rom_wmt"], errors="coerce"
            )
            return data.dropna(subset=[
                "two_wp_period_start_datetime",
                "two_wp_period_end_datetime",
                "two_wp_closing_rom_wmt",
            ])
        return pd.DataFrame(columns=cls.NORMALIZED_COLUMNS)

    @classmethod
    def build_report(
        cls,
        plan_id,
        plan_type,
        periods,
        physical_balance_history,
        target_rows,
        used_stockpiles=None,
    ):
        """Compare physical BlendMaster balances to completed 2WP periods."""
        targets = cls.normalize_target_rows(target_rows)
        history = []
        for snapshot in physical_balance_history or []:
            timestamp = cls._naive_timestamp(
                snapshot.get("snapshot_datetime")
            )
            if pd.isna(timestamp):
                continue
            history.append((timestamp, dict(snapshot.get("balances") or {})))
        history.sort(key=lambda item: item[0])

        explicit_scope = used_stockpiles is not None
        used = {
            cls.normalize_stockpile_name(value)
            for value in (used_stockpiles or [])
            if cls.normalize_stockpile_name(value)
        }
        if not explicit_scope:
            used.update(
                targets.get("stockpile", pd.Series(dtype=str)).dropna()
            )
            for _, balances in history:
                used.update(
                    cls.normalize_stockpile_name(value)
                    for value in balances
                    if cls.normalize_stockpile_name(value)
                )

        normalized_history = [
            (
                timestamp,
                {
                    cls.normalize_stockpile_name(name): value
                    for name, value in balances.items()
                },
            )
            for timestamp, balances in history
        ]
        records = []
        period_keys = [
            key[:-4] for key in periods if key.endswith("_end")
        ]
        period_keys.sort(
            key=lambda key: cls._naive_timestamp(periods.get(f"{key}_start"))
        )
        for period_key in period_keys:
            period_start = cls._naive_timestamp(
                periods.get(f"{period_key}_start")
            )
            period_end = cls._naive_timestamp(periods.get(f"{period_key}_end"))
            if pd.isna(period_start) or pd.isna(period_end):
                continue
            eligible_snapshots = [
                (timestamp, balances)
                for timestamp, balances in normalized_history
                if timestamp <= period_end
            ]
            balance_timestamp, balances = (
                eligible_snapshots[-1]
                if eligible_snapshots else (pd.NaT, {})
            )
            # A partial/infeasible plan must not present its last earlier
            # balance as the closing balance of every later BlendMaster period.
            # Normal plan execution creates a steady-state boundary exactly at
            # each period end; allow one second for serialization precision.
            if (
                pd.isna(balance_timestamp)
                or period_end - balance_timestamp > pd.Timedelta(seconds=1)
            ):
                balance_timestamp = pd.NaT
                balances = {}
            for stockpile in sorted(used):
                target_options = targets[
                    (targets["stockpile"] == stockpile)
                    & (
                        targets["two_wp_period_end_datetime"]
                        <= period_end
                    )
                ]
                target = (
                    target_options.sort_values(
                        "two_wp_period_end_datetime"
                    ).iloc[-1]
                    if not target_options.empty else None
                )
                blendmaster_value = pd.to_numeric(
                    pd.Series([balances.get(stockpile)]), errors="coerce"
                ).iloc[0]
                target_value = (
                    float(target["two_wp_closing_rom_wmt"])
                    if target is not None else None
                )
                variance = (
                    float(blendmaster_value) - target_value
                    if pd.notna(blendmaster_value) and target_value is not None
                    else None
                )
                variance_pct = (
                    variance / target_value * 100.0
                    if variance is not None and target_value > 0 else None
                )
                records.append({
                    "plan_id": str(plan_id),
                    "plan_type": str(plan_type),
                    "period": period_key,
                    "period_start_datetime": period_start,
                    "period_end_datetime": period_end,
                    "stockpile": stockpile,
                    "blendmaster_balance_datetime": (
                        balance_timestamp
                        if pd.notna(balance_timestamp) else None
                    ),
                    "blendmaster_closing_rom_wmt": (
                        float(blendmaster_value)
                        if pd.notna(blendmaster_value) else None
                    ),
                    "two_wp_closing_rom_wmt": target_value,
                    "variance_wmt": variance,
                    "variance_pct": variance_pct,
                    "two_wp_period_start_datetime": (
                        target["two_wp_period_start_datetime"]
                        if target is not None else None
                    ),
                    "two_wp_period_end_datetime": (
                        target["two_wp_period_end_datetime"]
                        if target is not None else None
                    ),
                    "comparison_status": (
                        "Compared"
                        if pd.notna(blendmaster_value) and target is not None
                        else "Missing BlendMaster balance"
                        if target is not None
                        else "Missing 2WP closing balance"
                    ),
                })
        return pd.DataFrame(records, columns=cls.REPORT_COLUMNS)
