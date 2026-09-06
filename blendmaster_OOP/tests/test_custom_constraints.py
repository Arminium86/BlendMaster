import os
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd


PACKAGE_ROOT = os.path.dirname(os.path.dirname(__file__))
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

from classes.BalanceTracker import BalanceTracker  # noqa: E402
from classes.CustomConstraints import (  # noqa: E402
    CustomConstraintError,
    SafeNumericExpression,
    constraint_property_fields,
    custom_constraint_property_keys,
    filter_source_properties,
    merge_source_properties,
    normalize_custom_constraints,
    scale_additive_source_properties,
    source_property_balance_report_fields,
    source_properties_from_mapping,
    source_property_report_fields,
)
from classes.DataLoader import DataLoader  # noqa: E402
from classes.EventData import EventData  # noqa: E402
from classes.EventPoolGenerator import EventPoolGenerator  # noqa: E402
from classes.ExpitDataHandler import ExpitDataHandler  # noqa: E402
from classes.GradeBlockData import GradeBlockData  # noqa: E402
from classes.ManualBlendPlanner import ManualBlendPlanner  # noqa: E402
from classes.Optimizer import Optimizer  # noqa: E402
from classes.PeriodManager import PeriodManager  # noqa: E402
from classes.StockpileData import StockpileData  # noqa: E402
from classes.SourcePropertyMappings import (  # noqa: E402
    APS_SOURCE_PROPERTY_FIELDS,
    normalise_aps_source_property_mappings,
)
from database.DatabaseContext import get_database_path, set_database_path  # noqa: E402
from database.SQLiteDatabase import (  # noqa: E402
    DatabaseManager,
    StockpileProfileReport,
)
from GUI.InitialiseGUI import UserInputs  # noqa: E402


PERIOD_LABELS = ("Preplan", "Period_1", "Period_2")
ANALYTES = ("fe", "si", "al", "p", "mn")


def period_values(value):
    return {label: value for label in PERIOD_LABELS}


def calendar_inputs(custom_constraints=None):
    result = {
        "planning_period_count": 3,
        "solver_config": {
            "direct_tip_enabled": True,
            "custom_constraints": list(custom_constraints or []),
        },
        "reclaim_equipment_max_reclaim_rate": period_values(1000.0),
        "crusher_rate": period_values(100.0),
        "crusher_brand": period_values("CCFB"),
        "crusher_direct_tip_ratio_min": period_values(0.0),
        "crusher_direct_tip_ratio_max": period_values(1.0),
    }
    for analyte in ANALYTES:
        result[f"crusher_target_{analyte}_min"] = period_values(0.0)
        result[f"crusher_target_{analyte}_max"] = period_values(100.0)
    return result


def stockpile_record(*, amt=False):
    streams = {
        "insitu": {
            "*": {"fe": 40.0, "si": 6.0, "al": 3.0, "p": 0.10, "mn": 0.20}
        },
        "modelled_rom": {
            "*": {"fe": 40.0, "si": 6.0, "al": 3.0, "p": 0.10, "mn": 0.20}
        },
        "adjusted_rom": {
            "CCFB": {"fe": 44.0, "si": 5.5, "al": 2.8, "p": 0.09, "mn": 0.18}
        },
        "modelled_product": {
            "CCFB": {"fe": 60.0, "si": 3.5, "al": 1.8, "p": 0.07, "mn": 0.10}
        },
        "adjusted_product": {
            "CCFB": {"fe": 63.0, "si": 3.0, "al": 1.5, "p": 0.06, "mn": 0.08}
        },
    }
    return {
        "SP1": {
            "name": "sp1",
            "balance": 1000.0,
            "reclaim_threshold": 0.0,
            "grade_fe": 40.0,
            "grade_si": 6.0,
            "grade_al": 3.0,
            "grade_p": 0.10,
            "grade_mn": 0.20,
            "amt": amt,
            "grade_streams": streams,
            "minus_1mm_pct": 12.5,
            "fines_wmt": 400.0,
        }
    }


def add_stockpile_calendar_rows(calendar):
    calendar["stockpiles_sp1_state"] = period_values("Reclaim")
    calendar["stockpiles_sp1_maximum_quantity"] = period_values(1000.0)
    return calendar


def make_event(
    name,
    *,
    event_type="stockpile",
    properties=None,
    grade_fe=60.0,
    property_kinds=None,
    property_weights=None,
):
    is_stockpile = event_type == "stockpile"
    return EventData(
        stockpile=name if is_stockpile else None,
        grade_block=None if is_stockpile else name,
        event_type=event_type,
        equipment="RC" if is_stockpile else "EX",
        cost=0.0,
        cash=0.0,
        rate=1000.0,
        grade_fe=grade_fe,
        grade_si=4.0,
        grade_al=2.0,
        grade_p=0.08,
        grade_mn=0.10,
        balance=1000.0,
        max_quantity=1000.0,
        reclaim_threshold=0.0,
        state="Reclaim",
        auto_turnover_datetime=None,
        source_name=name,
        delivered_datetime=(
            datetime(2026, 1, 1, 0, 30) if not is_stockpile else None
        ),
        source_properties=properties or {},
        source_property_kinds=property_kinds or {},
        source_property_weights=property_weights or {},
    )


def optimizer_target(**overrides):
    target = {
        "crusher_rate": 100.0,
        "direct_feed_ratio_min": 0.0,
        "direct_feed_ratio_max": 1.0,
        "custom_constraints": [],
    }
    for analyte in ANALYTES:
        target[f"target_{analyte}_min"] = 0.0
        target[f"target_{analyte}_max"] = 100.0
    target.update(overrides)
    return target


def optimize(events, target, solver_config=None):
    periods = PeriodManager()
    periods.calculate_periods(datetime(2026, 1, 1, 0, 0))
    return Optimizer.run_blending_optimization(
        events,
        target,
        1.0,
        None,
        None,
        periods,
        "preplan",
        solver_config={
            "throughput_incentive_per_tonne": 100.0,
            **(solver_config or {}),
        },
        excluded_source_sets=[],
    )


class SafeNumericExpressionTests(unittest.TestCase):
    def test_arithmetic_is_evaluated_without_eval_features(self):
        expression = SafeNumericExpression(
            "(field_a * field_b + 2) / field_c"
        )

        self.assertEqual(
            expression.evaluate({"FIELD_A": 3, "field_b": 4, "field_c": 2}),
            7.0,
        )
        self.assertEqual(expression.names, {"field_a", "field_b", "field_c"})

    def test_calls_attributes_and_power_are_rejected(self):
        for expression in (
            "__import__('os').system('whoami')",
            "field.real",
            "field ** 2",
        ):
            with self.subTest(expression=expression):
                with self.assertRaises(CustomConstraintError):
                    SafeNumericExpression(expression)

    def test_missing_fields_and_division_by_zero_are_explicit(self):
        with self.assertRaisesRegex(CustomConstraintError, "unavailable"):
            SafeNumericExpression("missing + 1").evaluate({})
        with self.assertRaisesRegex(CustomConstraintError, "divides by zero"):
            SafeNumericExpression("field / zero").evaluate(
                {"field": 2.0, "zero": 0.0}
            )


class IndependentDataStreamTests(unittest.TestCase):
    def test_product_crusher_capacity_converts_back_to_physical_rom(self):
        event = make_event("SP1", properties={
            "modelled_rom_wmt": 1000.0,
            "modelled_product_wmt": 500.0,
        })
        event.max_quantity = 6000.0
        result = optimize(
            [event],
            optimizer_target(crusher_rate=100.0),
            {
                "crusher_tonnes_stream": "modelled_product_wmt",
                "reclaimer_tonnes_stream": "modelled_rom_wmt",
            },
        )

        transaction = result["transactions"][0]
        self.assertAlmostEqual(200.0, transaction["actual_tonnes"])
        self.assertAlmostEqual(100.0, transaction["crusher_source_tonnes"])
        self.assertAlmostEqual(100.0, result["crusher_actual_tonnes"])

    def test_reclaimer_capacity_uses_selected_stream(self):
        event = make_event("SP1", properties={
            "modelled_rom_wmt": 1000.0,
            "modelled_product_wmt": 500.0,
        })
        event.rate = 100.0
        event.max_quantity = 6000.0
        result = optimize(
            [event],
            optimizer_target(crusher_rate=1000.0),
            {
                "crusher_tonnes_stream": "modelled_rom_wmt",
                "reclaimer_tonnes_stream": "modelled_product_wmt",
            },
        )

        transaction = result["transactions"][0]
        self.assertAlmostEqual(200.0, transaction["actual_tonnes"])
        self.assertAlmostEqual(100.0, transaction["reclaimer_source_tonnes"])
        self.assertAlmostEqual(100.0, transaction["equipment_rate_output"])

    def test_grade_target_uses_define_fields_weight_not_crusher_stream(self):
        low = make_event("LOW", grade_fe=40.0, properties={
            "modelled_rom_wmt": 1000.0,
            "modelled_product_wmt": 200.0,
            "grade_dmt": 1000.0,
        })
        high = make_event("HIGH", grade_fe=60.0, properties={
            "modelled_rom_wmt": 1000.0,
            "modelled_product_wmt": 1000.0,
            "grade_dmt": 500.0,
        })
        result = optimize(
            [low, high],
            optimizer_target(
                crusher_rate=100.0,
                target_fe_min=50.0,
                target_fe_max=50.0,
            ),
            {
                "selected_data_stream": "insitu",
                "crusher_tonnes_stream": "modelled_product_wmt",
                "source_property_weights": {"insitu_fe": "grade_dmt"},
                "stockpile_feasibility_mode": "combined_feed_feasible",
            },
        )

        self.assertAlmostEqual(50.0, result["crusher_actual_grade_fe"], places=6)
        self.assertNotAlmostEqual(
            sum(
                transaction["grade_fe"] * transaction["crusher_source_tonnes"]
                for transaction in result["transactions"]
            ) / result["crusher_actual_tonnes"],
            result["crusher_actual_grade_fe"],
        )

    def test_saved_constraint_keys_are_unique_and_deterministic(self):
        normalized = normalize_custom_constraints([
            {"name": "Fine ratio", "numerator": "grade_fe"},
            {"name": "Fine ratio", "numerator": "grade_si"},
        ])

        self.assertEqual(
            [item["key"] for item in normalized],
            ["fine_ratio", "fine_ratio_2"],
        )
        self.assertEqual(normalized[0]["denominator"], "1")

    def test_invalid_saved_constraint_is_rejected_instead_of_discarded(self):
        host = SimpleNamespace(solver_config={})

        with self.assertRaisesRegex(
            ValueError,
            "Invalid saved custom constraint",
        ):
            UserInputs.normalized_solver_config(host, {
                "custom_constraints": [{
                    "name": "Unsafe saved expression",
                    "numerator": "__import__('os')",
                    "denominator": "one",
                }],
            })

        self.assertTrue(host.custom_constraint_normalization_warning)


class RuntimePropertyProjectionTests(unittest.TestCase):
    @staticmethod
    def definitions():
        return [{
            "name": "Composite recovery",
            "numerator": "field_a * field_b / field_c",
            "denominator": "fines_wmt_per_source_wmt",
        }, {
            "name": "Builtins only",
            "numerator": "selected_fe * is_inventory",
            "denominator": "one",
        }]

    @staticmethod
    def full_properties():
        return {
            "field_a": 2.0,
            "field_b": 3.0,
            "field_c": 4.0,
            "fines_wmt": 50.0,
            "unused_metric": 999.0,
        }

    def test_expression_dependencies_project_raw_fields_and_additive_alias(self):
        required = custom_constraint_property_keys(self.definitions())

        self.assertEqual(
            required,
            {"field_a", "field_b", "field_c", "fines_wmt"},
        )
        projected = filter_source_properties(
            {
                "Field-A": 2.0,
                "field_b": 3.0,
                "field_c": 4.0,
                "fines_wmt": 50.0,
                "unused_metric": 999.0,
            },
            required,
        )
        self.assertEqual(projected, {
            "field_a": 2.0,
            "field_b": 3.0,
            "field_c": 4.0,
            # The expression-visible alias resolves back to its raw additive
            # property so balance-aware conversion still happens at runtime.
            "fines_wmt": 50.0,
        })
        fields = constraint_property_fields(projected, source_balance=100.0)
        self.assertAlmostEqual(
            SafeNumericExpression("field_a * field_b / field_c").evaluate(fields),
            1.5,
        )
        self.assertAlmostEqual(fields["fines_wmt_per_source_wmt"], 0.5)

    def test_data_loader_projects_inventory_amt_and_aps_solver_objects(self):
        expected = {
            key: value
            for key, value in self.full_properties().items()
            if key != "unused_metric"
        }
        calendar = add_stockpile_calendar_rows(
            calendar_inputs(self.definitions())
        )

        inventory_data = stockpile_record(amt=False)
        inventory_data["SP1"].update(self.full_properties())
        inventory_data["SP1"]["source_properties"] = self.full_properties()
        inventory = DataLoader(
            inventory_data, calendar, pd.DataFrame(), []
        ).load_data()[0][0]

        amt_data = stockpile_record(amt=True)
        amt_data["SP1"].update(self.full_properties())
        amt_data["SP1"]["source_properties"] = self.full_properties()
        amt_hex = {
            "footprint": "SP1",
            "hex": "HEX_1",
            "sequence": 1,
            "balance": 100.0,
            "grade_fe": 40.0,
            "grade_si": 6.0,
            "grade_al": 3.0,
            "grade_p": 0.10,
            "grade_mn": 0.20,
            "source_properties": self.full_properties(),
        }
        amt = DataLoader(
            amt_data, calendar, pd.DataFrame(), [amt_hex]
        ).load_data()[0][0]

        aps_row = {
            "direct_tip_id": "GB1",
            "direct_tip_eligible": True,
            "source": "BLOCK_1",
            "payload": 100.0,
            "delivered_datetime": datetime(2026, 1, 1, 0, 10),
            "start_datetime": datetime(2026, 1, 1, 0, 0),
            "destination": "Stockpiles/SP1",
            "agent": "EX01",
            "source_grade_fe": 60.0,
            "source_grade_si": 4.0,
            "source_grade_al": 2.0,
            "source_grade_p": 0.08,
            "source_grade_mn": 0.10,
            "source_properties": self.full_properties(),
        }
        aps = DataLoader(
            {}, calendar, pd.DataFrame([aps_row]), []
        ).load_data()[1][0]

        self.assertEqual(inventory.source_properties, expected)
        self.assertEqual(amt.source_properties, expected)
        self.assertEqual(aps.source_properties, expected)
        self.assertFalse(inventory.is_AMT)
        self.assertTrue(amt.is_AMT)

    def test_no_constraints_empty_solver_payload_but_keep_audit_and_sqlite_data(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "24hr.csv"
            row = ApsNumericPropertyIntegrationTests.aps_row(
                "Reserves/CC/PIT_A/BLOCK_1",
                100.0,
                "01/01/2026 00:00",
                "01/01/2026 01:00",
                12.0,
                40.0,
            )
            row["Mining.ClayIndex"] = 7.5
            pd.DataFrame([row]).to_csv(csv_path, index=False)
            transactions = ExpitDataHandler(
                csv_path,
                source_property_field_mappings={
                    "mining_clayindex": "Mining.ClayIndex",
                    "mining_fines_wmt": "Mining.fines_wmt",
                },
                source_property_kinds={
                    "mining_clayindex": "weighted_average",
                },
            ).process_transactions()

            full_properties = dict(
                transactions.iloc[0]["source_properties"]
            )
            self.assertEqual(full_properties["mining_clayindex"], 7.5)
            self.assertEqual(full_properties["mining_fines_wmt"], 40.0)

            solver_transactions = transactions.copy()
            solver_transactions["direct_tip_eligible"] = True
            solver_transactions["direct_tip_id"] = "GB1"
            grade_blocks = DataLoader(
                {}, calendar_inputs(), solver_transactions, []
            ).load_data()[1]
            self.assertEqual(len(grade_blocks), 1)
            self.assertEqual(grade_blocks[0].source_properties, {})

            view = UserInputs.__new__(UserInputs)
            view.selected_data_stream = "adjusted_product"
            view.product_brand_labels_choice = []
            audit_row = view.database_view_grade_block_rows(
                transactions
            )[0]
            self.assertEqual(audit_row["mining_clayindex"], 7.5)
            self.assertEqual(audit_row["mining_fines_wmt"], 40.0)

            previous_path = get_database_path()
            database_path = os.path.join(directory, "full-audit.db")
            set_database_path(database_path)
            try:
                DatabaseManager().write_expit_payload_transactions_to_database(
                    transactions.copy()
                )
                connection = sqlite3.connect(database_path)
                try:
                    stored = connection.execute(
                        "SELECT source_properties_json "
                        "FROM expit_payload_transactions"
                    ).fetchone()[0]
                finally:
                    connection.close()
            finally:
                set_database_path(previous_path)

        self.assertEqual(json.loads(stored), full_properties)

    def test_modelled_properties_expose_shared_raw_and_explicit_audit_aliases(self):
        properties = source_properties_from_mapping({
            "source_properties": {"prod1_wmt": 111.0},
            "modelled_properties": {
                "values": {
                    "PROD1 WMT": 99.0,
                    "oretype_bid_wmt": 30.0,
                },
                "coverage": {"oretype_bid_wmt": 0.75},
            },
        })

        # A directly imported inventory/APS value remains authoritative for
        # the shared alias while AMT's explicit modelled alias remains auditable.
        self.assertEqual(properties["prod1_wmt"], 111.0)
        self.assertEqual(properties["modelled_prod1_wmt"], 99.0)
        self.assertEqual(properties["oretype_bid_wmt"], 30.0)
        self.assertEqual(properties["modelled_oretype_bid_wmt"], 30.0)
        self.assertEqual(
            properties["modelled_oretype_bid_wmt_coverage_pct"], 75.0
        )

    def test_report_flattening_keeps_only_finite_non_runtime_properties(self):
        fields = source_property_report_fields({
            "Fines WMT": 40.0,
            "minus-1mm pct": 12.5,
            "balance": 100.0,
            "not_numeric": "n/a",
            "infinite_metric": float("inf"),
        })

        self.assertEqual(fields, {
            "source_property_fines_wmt": 40.0,
            "source_property_minus_1mm_pct": 12.5,
        })

    def test_optimised_sqlite_report_retains_dynamic_property_columns(self):
        previous_path = get_database_path()
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "report.db")
            set_database_path(database_path)
            manager = DatabaseManager()
            report = pd.DataFrame([{
                "start_datetime": datetime(2026, 1, 1, 0, 0),
                "end_datetime": datetime(2026, 1, 1, 1, 0),
                "source": "SP1",
                "source_property_fines_wmt": 40.0,
                "source_property_minus_1mm_pct": 12.5,
            }])
            try:
                with patch.object(
                    manager,
                    "write_optimised_stockpile_depletion_report_to_database",
                ), patch.object(
                    StockpileProfileReport,
                    "write_optimised_stockpile_profile_report_to_database",
                ):
                    manager.write_optimised_blend_report_to_database(
                        report, SimpleNamespace()
                    )
                connection = sqlite3.connect(database_path)
                try:
                    stored = connection.execute(
                        "SELECT source_property_fines_wmt, "
                        "source_property_minus_1mm_pct "
                        "FROM optimised_blend_report"
                    ).fetchone()
                finally:
                    connection.close()
            finally:
                set_database_path(previous_path)

        self.assertEqual(stored, (40.0, 12.5))

    def test_custom_constraint_field_discovery_uses_checked_definitions_only(self):
        host = SimpleNamespace(
            field_definitions=[
                {
                    "name": "oretype_bid_wmt", "kind": "additive",
                    "use_in_optimisation": True,
                },
                {
                    "name": "prod1_minus_1mm_pct",
                    "kind": "weighted_average",
                    "weight_field": "oretype_bid_wmt",
                    "use_in_optimisation": True,
                },
                {
                    "name": "oretype_hc_wmt", "kind": "additive",
                    "use_in_optimisation": False,
                },
            ],
        )

        fields = UserInputs.custom_constraint_available_fields(host)

        self.assertIn("oretype_bid_wmt", fields)
        self.assertIn("prod1_minus_1mm_pct", fields)
        self.assertNotIn("oretype_hc_wmt", fields)
        self.assertNotIn("selected_fe", fields)
        self.assertNotIn("grade_fe", fields)
        self.assertNotIn("is_stockpile", fields)


class SourcePropertyBalanceTests(unittest.TestCase):
    def test_intensive_fields_weight_and_additive_fields_sum(self):
        merged = merge_source_properties(
            {"minus_1mm_pct": 10.0, "fines_wmt": 40.0},
            100.0,
            {"minus_1mm_pct": 20.0, "fines_wmt": 30.0},
            50.0,
        )

        self.assertAlmostEqual(merged["minus_1mm_pct"], 40.0 / 3.0)
        self.assertEqual(merged["fines_wmt"], 70.0)
        fields = constraint_property_fields(merged, 150.0)
        self.assertAlmostEqual(fields["fines_wmt_per_source_wmt"], 70.0 / 150.0)

    def test_missing_intensive_coverage_is_not_silently_carried(self):
        merged = merge_source_properties(
            {"minus_1mm_pct": 10.0},
            100.0,
            {},
            50.0,
        )

        self.assertNotIn("minus_1mm_pct", merged)

    def test_depletion_scales_additive_but_not_intensive_fields(self):
        scaled = scale_additive_source_properties(
            {"minus_1mm_pct": 12.0, "fines_wmt": 400.0},
            0.25,
        )

        self.assertEqual(scaled["minus_1mm_pct"], 12.0)
        self.assertEqual(scaled["fines_wmt"], 100.0)

    def test_additive_report_exposes_opening_depletion_and_closing(self):
        fields = source_property_balance_report_fields(
            {"modelled_rom_wmt": 1000.0, "modelled_rom_dmt": 900.0},
            {"modelled_rom_wmt": 100.0, "modelled_rom_dmt": 90.0},
            active_fields=[
                "modelled_rom_wmt", "modelled_rom_dmt", "prod1_wmt"
            ],
        )

        self.assertEqual(
            fields["source_property_modelled_rom_wmt_opening_balance"],
            1000.0,
        )
        self.assertEqual(
            fields["source_property_modelled_rom_wmt_closing_balance"],
            900.0,
        )
        self.assertIsNone(
            fields["source_property_prod1_wmt_opening_balance"]
        )

    def test_optimizer_transaction_flattens_only_the_selected_additive_mass(self):
        result = optimize(
            [make_event(
                "SP1",
                properties={"minus_1mm_pct": 12.0, "fines_wmt": 400.0},
            )],
            optimizer_target(),
            solver_config={
                "optimisation_source_property_fields": [
                    "minus_1mm_pct", "fines_wmt", "modelled_rom_dmt"
                ]
            },
        )

        self.assertTrue(result["Linprog_result_object"].success)
        transaction = result["transactions"][0]
        self.assertAlmostEqual(transaction["actual_tonnes"], 100.0)
        self.assertEqual(
            transaction["source_property_minus_1mm_pct"], 12.0
        )
        # The source holds 400 t fines in 1,000 t. The one-hour solve selects
        # 100 t, so reports should carry only the selected 40 t fines mass.
        self.assertEqual(transaction["source_property_fines_wmt"], 40.0)
        self.assertEqual(transaction["source_properties"]["fines_wmt"], 40.0)
        self.assertEqual(
            transaction["source_property_fines_wmt_opening_balance"], 400.0
        )
        self.assertEqual(
            transaction["source_property_fines_wmt_closing_balance"], 360.0
        )
        self.assertIn("source_property_modelled_rom_dmt", transaction)
        self.assertIsNone(transaction["source_property_modelled_rom_dmt"])

    def test_partial_direct_tip_prorates_additive_properties_before_build(self):
        stockpile = StockpileData(
            name="SP1",
            balance=0.0,
            state_preplan="Build",
            state_period_1="Build",
            state_period_2="Build",
            max_quantity_preplan=1000.0,
            max_quantity_period_1=1000.0,
            max_quantity_period_2=1000.0,
            cost_preplan=0.0,
            cost_period_1=0.0,
            cost_period_2=0.0,
            cash_preplan=0.0,
            cash_period_1=0.0,
            cash_period_2=0.0,
            equipment="RC",
            reclaim_threshold=0.0,
            grade_fe=0.0,
            grade_si=0.0,
            grade_al=0.0,
            grade_p=0.0,
            grade_mn=0.0,
            auto_turnover_datetime=None,
            is_ready=False,
            is_AMT=False,
            source_properties={},
        )
        grade_block = GradeBlockData(
            name="GB1",
            balance=100.0,
            max_quantity_preplan=100.0,
            max_quantity_period_1=100.0,
            max_quantity_period_2=100.0,
            cost_preplan=0.0,
            cost_period_1=0.0,
            cost_period_2=0.0,
            cash_preplan=0.0,
            cash_period_1=0.0,
            cash_period_2=0.0,
            equipment="EX",
            grade_fe=60.0,
            grade_si=4.0,
            grade_al=2.0,
            grade_p=0.08,
            grade_mn=0.10,
            source_properties={
                "minus_1mm_pct": 20.0,
                "fines_wmt": 50.0,
            },
        )
        tracker = BalanceTracker(
            [stockpile], [grade_block], "preplan", []
        )
        start = datetime(2026, 1, 1, 0, 0)
        payloads = pd.DataFrame([{
            "direct_tip_id": "GB1",
            "source": "BLOCK_1",
            "destination": "Stockpiles/SP1",
            "payload": 100.0,
            "agent": "EX01",
            "start_datetime": start,
            "delivered_datetime": start + timedelta(minutes=10),
            "source_grade_fe": 60.0,
            "source_grade_si": 4.0,
            "source_grade_al": 2.0,
            "source_grade_p": 0.08,
            "source_grade_mn": 0.10,
            "source_properties": {
                "minus_1mm_pct": 20.0,
                "fines_wmt": 50.0,
            },
        }])
        direct_tip_selection = pd.DataFrame([{
            "source": "BLOCK_1",
            "source_id": "GB1",
            "source_actual_tonnes": 40.0,
        }])

        tracker.update_balances(
            direct_tip_selection,
            payloads,
            start,
            start + timedelta(hours=1),
            1,
        )

        self.assertEqual(tracker.balance_copy["GB1"], 60.0)
        self.assertEqual(tracker.balance_copy["SP1"], 60.0)
        # Forty percent of the payload was direct-tipped, so the inventory
        # receives 60% of the additive fines mass while retaining the same
        # intensive -1 mm percentage.
        self.assertEqual(
            tracker.source_properties["SP1"]["fines_wmt"], 30.0
        )
        self.assertEqual(
            tracker.source_properties["SP1"]["minus_1mm_pct"], 20.0
        )
        self.assertEqual(
            tracker.source_properties["GB1"]["fines_wmt"], 30.0
        )
        build = tracker.get_build_transactions().iloc[0]
        self.assertEqual(build["payload"], 60.0)
        self.assertEqual(build["source_properties"]["fines_wmt"], 30.0)

    def test_successive_partial_amt_reclaims_keep_additive_mass_proportional(self):
        stockpile = StockpileData(
            name="AMT_SP1",
            balance=100.0,
            state_preplan="Reclaim",
            state_period_1="Reclaim",
            state_period_2="Reclaim",
            max_quantity_preplan=1000.0,
            max_quantity_period_1=1000.0,
            max_quantity_period_2=1000.0,
            cost_preplan=0.0,
            cost_period_1=0.0,
            cost_period_2=0.0,
            cash_preplan=0.0,
            cash_period_1=0.0,
            cash_period_2=0.0,
            equipment="RC",
            reclaim_threshold=0.0,
            grade_fe=60.0,
            grade_si=4.0,
            grade_al=2.0,
            grade_p=0.08,
            grade_mn=0.10,
            auto_turnover_datetime=None,
            is_ready=True,
            is_AMT=True,
            source_properties={
                "minus_1mm_pct": 10.0,
                # Deliberately differs from the chunk value below. The active
                # chunk must win rather than inheriting/scaling this parent.
                "fines_wmt": 150.0,
                "prod1_fines_wmt": 25.0,
                "source_wmt": 300.0,
                "modelled_rom_wmt": 300.0,
                "modelled_rom_dmt": 270.0,
                "modelled_product_wmt": 240.0,
                "modelled_product_dmt": 216.0,
            },
        )
        hexes = [{
            "footprint": "AMT_SP1",
            "hex": "HEX_1",
            "sequence": 1,
            "balance": 100.0,
            "grade_fe": 60.0,
            "grade_si": 4.0,
            "grade_al": 2.0,
            "grade_p": 0.08,
            "grade_mn": 0.10,
            "grade_block_count": 4,
            "grade_stream_warnings": ["lineage audit warning"],
            "minus_1mm_pct": 10.0,
            "fines_wmt": 40.0,
            "modelled_properties": {
                "values": {"prod1_fines_wmt": 0.0},
                "coverage": {"prod1_fines_wmt": 1.0},
            },
        }]
        tracker = BalanceTracker([stockpile], [], "preplan", hexes)

        self.assertEqual(tracker.source_properties["AMT_SP1"]["source_wmt"], 100.0)
        self.assertEqual(
            tracker.source_properties["AMT_SP1"]["modelled_rom_wmt"], 100.0
        )
        self.assertEqual(
            tracker.source_properties["AMT_SP1"]["modelled_product_wmt"], 80.0
        )
        self.assertEqual(
            tracker.source_properties["AMT_SP1"]["modelled_product_dmt"], 72.0
        )
        self.assertEqual(
            tracker.source_properties["AMT_SP1"]["prod1_fines_wmt"], 0.0
        )

        with patch("builtins.print") as print_mock:
            tracker.process_hex_sequence("AMT_SP1", 25.0)
        print_mock.assert_not_called()
        self.assertEqual(tracker.balance_copy["AMT_SP1"], 75.0)
        self.assertEqual(
            tracker.source_properties["AMT_SP1"]["fines_wmt"], 30.0
        )
        self.assertEqual(
            tracker.source_properties["AMT_SP1"]["modelled_rom_dmt"], 67.5
        )
        self.assertEqual(
            tracker.source_properties["AMT_SP1"]["modelled_product_wmt"], 60.0
        )

        tracker.process_hex_sequence("AMT_SP1", 25.0)

        self.assertEqual(tracker.balance_copy["AMT_SP1"], 50.0)
        self.assertEqual(tracker.hex_sequence_table[0]["balance"], 50.0)
        self.assertEqual(
            tracker.source_properties["AMT_SP1"]["fines_wmt"], 20.0
        )
        self.assertEqual(
            tracker.source_properties["AMT_SP1"]["minus_1mm_pct"], 10.0
        )
        self.assertEqual(
            tracker.source_properties["AMT_SP1"]["modelled_rom_wmt"], 50.0
        )
        self.assertEqual(
            tracker.source_properties["AMT_SP1"]["modelled_rom_dmt"], 45.0
        )
        self.assertEqual(
            tracker.source_properties["AMT_SP1"]["modelled_product_wmt"], 40.0
        )
        self.assertEqual(
            tracker.return_total_AMT_stockpile_balances()["AMT_SP1"],
            50.0,
        )


class ApsNumericPropertyIntegrationTests(unittest.TestCase):
    @staticmethod
    def aps_row(source, tonnes, start, end, minus_1mm, fines_wmt):
        return {
            "Agent.Name": "EX01",
            "Source.Type": "Reserve",
            "Source.FullName": source,
            "Source.Pit": "PIT_A",
            "Destination.Type": "Stockpile",
            "Destination.Name": "SP_A",
            "Destination.FullName": "Stockpiles/SP_A",
            "Time.StartTime": start,
            "Time.EndTime": end,
            "Mining.wetTonnes": tonnes,
            "Mining.grades_fe": 60.0,
            "Mining.grades_si": 4.0,
            "Mining.grades_al": 2.0,
            "Mining.grades_mn": 0.1,
            "Mining.grades_p": 0.08,
            "HaulageResult.Times.Dumping": 1.0,
            "HaulageResult.Times.LoadedTravel": 10.0,
            "HaulageResult.LoaderProductionRate.Wtph": 1000.0,
            "HaulageResult.Times.SpotAtDump": 1.0,
            "HaulageResult.Times.SpotAtLoader": 1.0,
            "HaulageResult.TruckPayload": 100.0,
            "HaulageResult.NumberOfTrips": tonnes / 100.0,
            "Mining.minus_1mm_pct": minus_1mm,
            "Mining.fines_wmt": fines_wmt,
        }

    def test_grouping_weights_intensive_properties_and_conserves_additive_mass(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "24hr.csv"
            source = "Reserves/CC/PIT_A/BLOCK_1"
            pd.DataFrame([
                self.aps_row(
                    source, 100.0,
                    "01/01/2026 00:00", "01/01/2026 01:00",
                    10.0, 40.0,
                ),
                self.aps_row(
                    source, 300.0,
                    "01/01/2026 01:00", "01/01/2026 02:00",
                    20.0, 90.0,
                ),
            ]).to_csv(path, index=False)

            handler = ExpitDataHandler(
                path,
                source_property_field_mappings={
                    "mining_minus_1mm_pct": "Mining.minus_1mm_pct",
                    "mining_fines_wmt": "Mining.fines_wmt",
                },
            )
            transactions = handler.process_transactions()

        self.assertEqual(len(handler.data), 1)
        self.assertAlmostEqual(
            handler.data.iloc[0]["Mining.minus_1mm_pct"], 17.5
        )
        self.assertAlmostEqual(
            handler.data.iloc[0]["Mining.fines_wmt"], 130.0
        )
        self.assertEqual(len(transactions), 4)
        self.assertTrue(all(
            abs(properties["mining_minus_1mm_pct"] - 17.5) < 1e-9
            for properties in transactions["source_properties"]
        ))
        self.assertAlmostEqual(
            sum(
                properties["mining_fines_wmt"]
                for properties in transactions["source_properties"]
            ),
            130.0,
        )

    def test_configured_property_mappings_weight_and_conserve_canonical_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "24hr.csv"
            source = "Reserves/CB/PIT_A/BLOCK_1"
            first = self.aps_row(
                source, 100.0,
                "01/01/2026 00:00", "01/01/2026 01:00",
                1.0, 1.0,
            )
            second = self.aps_row(
                source, 300.0,
                "01/01/2026 01:00", "01/01/2026 02:00",
                1.0, 1.0,
            )
            first.update({
                "CB Fines Tonnes": 40.0,
                "CB Minus 1mm": 10.0,
                "CB Fines Fe": 55.0,
                "CB BID Tonnes": 60.0,
            })
            second.update({
                "CB Fines Tonnes": 90.0,
                "CB Minus 1mm": 20.0,
                "CB Fines Fe": 57.0,
                "CB BID Tonnes": 150.0,
            })
            pd.DataFrame([first, second]).to_csv(path, index=False)

            handler = ExpitDataHandler(
                path,
                source_property_field_mappings={
                    "prod1_fines_wmt": "CB Fines Tonnes",
                    "prod1_minus_1mm_pct": "CB Minus 1mm",
                    "prod1_fines_fe": "CB Fines Fe",
                    "oretype_bid_wmt": "CB BID Tonnes",
                },
                source_property_weights={
                    "prod1_fines_fe": "prod1_fines_wmt",
                },
            )
            transactions = handler.process_transactions()

        self.assertEqual(len(transactions), 4)
        properties = list(transactions["source_properties"])
        self.assertAlmostEqual(
            sum(item["prod1_fines_wmt"] for item in properties), 130.0
        )
        self.assertAlmostEqual(
            sum(item["oretype_bid_wmt"] for item in properties), 210.0
        )
        self.assertTrue(all(
            abs(item["prod1_minus_1mm_pct"] - 17.5) < 1e-9
            for item in properties
        ))
        self.assertTrue(all(
            abs(item["prod1_fines_fe"] - ((55.0 * 40.0 + 57.0 * 90.0) / 130.0)) < 1e-9
            for item in properties
        ))
        self.assertTrue(all(
            "cb_fines_tonnes" not in item
            for item in properties
        ))

    def test_legacy_destination_split_does_not_duplicate_mapped_additive_mass(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "24hr.csv"
            source = "Reserves/CB/PIT_A/BLOCK_1"
            row = self.aps_row(
                source, 400.0,
                "01/01/2026 00:00", "01/01/2026 01:00",
                1.0, 1.0,
            )
            row["CB Fines Tonnes"] = 100.0
            pd.DataFrame([row]).to_csv(path, index=False)
            source_key = ExpitDataHandler.destination_guidance_source_key(
                source
            )
            guidance = {
                "source_destinations": {
                    source_key: [{
                        "destination": "Stockpiles/SP_A",
                        "destination_name": "SP_A",
                        "ratio": 0.25,
                    }, {
                        "destination": "Stockpiles/SP_B",
                        "destination_name": "SP_B",
                        "ratio": 0.75,
                    }],
                },
                "pit_destinations": {},
                "last_destination": {},
            }

            transactions = ExpitDataHandler(
                path,
                destination_guidance=guidance,
                source_property_field_mappings={
                    "prod1_fines_wmt": "CB Fines Tonnes",
                },
            ).process_transactions()

        fines_by_destination = {
            destination: sum(
                properties["prod1_fines_wmt"]
                for properties in group["source_properties"]
            )
            for destination, group in transactions.groupby("destination")
        }
        self.assertEqual(fines_by_destination, {
            "Stockpiles/SP_A": 25.0,
            "Stockpiles/SP_B": 75.0,
        })
        self.assertEqual(sum(fines_by_destination.values()), 100.0)

    def test_missing_configured_property_header_is_rejected_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "24hr.csv"
            row = self.aps_row(
                "Reserves/CB/PIT_A/BLOCK_1", 100.0,
                "01/01/2026 00:00", "01/01/2026 01:00",
                10.0, 40.0,
            )
            pd.DataFrame([row]).to_csv(path, index=False)

            with self.assertRaisesRegex(
                ValueError,
                "source-property mapping column.*CB Missing Field",
            ):
                ExpitDataHandler(
                    path,
                    source_property_field_mappings={
                        "prod1_fines_wmt": "CB Missing Field",
                    },
                )

    def test_mapping_catalogue_covers_requested_product_and_ore_properties(self):
        required = {
            "feed_dmt",
            "oretype_bid_wmt",
            "prod1_minus_1mm_wmt",
            "prod1_fines_wmt",
            "prod1_lump_wmt",
            "prod1_fines_fe",
            "prod1_lump_fe",
        }
        self.assertTrue(required.issubset(APS_SOURCE_PROPERTY_FIELDS))

        mappings = normalise_aps_source_property_mappings({
            "PROD1 Fines WMT": "  Site-specific fines field  ",
            "Future Property": "Future.Header",
        })
        self.assertEqual(
            mappings["prod1_fines_wmt"], "Site-specific fines field"
        )
        self.assertEqual(mappings["future_property"], "Future.Header")
        self.assertEqual(mappings["prod1_lump_wmt"], "")

    def test_unmapped_numeric_header_collisions_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "24hr.csv"
            row = self.aps_row(
                "Reserves/CC/PIT_A/BLOCK_1", 100.0,
                "01/01/2026 00:00", "01/01/2026 01:00",
                10.0, 40.0,
            )
            row["Mining.Minus-1mm %"] = 11.0
            row["Mining.Minus 1mm %"] = 12.0
            pd.DataFrame([row]).to_csv(path, index=False)

            handler = ExpitDataHandler(path)

        self.assertNotIn("Mining.Minus-1mm %", handler.data.columns)
        self.assertNotIn("Mining.Minus 1mm %", handler.data.columns)
        self.assertEqual(handler.property_warnings, [])

    def test_explicitly_mapped_numeric_header_is_weighted_and_constraint_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "24hr.csv"
            source = "Reserves/CC/PIT_A/BLOCK_1"
            first = self.aps_row(
                source, 100.0,
                "01/01/2026 00:00", "01/01/2026 01:00",
                10.0, 40.0,
            )
            second = self.aps_row(
                source, 300.0,
                "01/01/2026 01:00", "01/01/2026 02:00",
                20.0, 90.0,
            )
            first["Mining.ClayIndex"] = 2.0
            second["Mining.ClayIndex"] = 6.0
            pd.DataFrame([first, second]).to_csv(path, index=False)

            handler = ExpitDataHandler(
                path,
                source_property_field_mappings={
                    "mining_clayindex": "Mining.ClayIndex",
                },
                source_property_kinds={
                    "mining_clayindex": "weighted_average",
                },
            )
            transactions = handler.process_transactions()

        self.assertAlmostEqual(
            handler.data.iloc[0]["Mining.ClayIndex"], 5.0
        )
        self.assertEqual(handler.property_warnings, [])
        self.assertTrue(all(
            properties["mining_clayindex"] == 5.0
            for properties in transactions["source_properties"]
        ))

        definition = {
            "name": "Clay index",
            "numerator": "mining_clayindex",
            "denominator": "one",
            "minimum": 5.0,
            "maximum": 5.0,
        }
        result = optimize(
            [make_event(
                "APS_BLOCK",
                properties=transactions.iloc[0]["source_properties"],
            )],
            optimizer_target(custom_constraints=[definition]),
        )

        self.assertTrue(result["Linprog_result_object"].success)
        self.assertAlmostEqual(
            result["custom_constraint_clay_index_actual_ratio"], 5.0
        )

    def test_source_properties_round_trip_as_json_in_payload_database(self):
        previous_path = get_database_path()
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "payloads.db")
            set_database_path(database_path)
            try:
                row = {
                    "agent": "EX01",
                    "source": "BLOCK_1",
                    "start_datetime": datetime(2026, 1, 1, 0, 0),
                    "payload": 100.0,
                    "source_grade_fe": 60.0,
                    "source_grade_si": 4.0,
                    "source_grade_al": 2.0,
                    "source_grade_mn": 0.1,
                    "source_grade_p": 0.08,
                    "destination": "Stockpiles/SP_A",
                    "delivered_datetime": datetime(2026, 1, 1, 0, 10),
                    "source_properties": {
                        "mining_minus_1mm_pct": 17.5,
                        "mining_fines_wmt": 32.5,
                    },
                }
                DatabaseManager().write_expit_payload_transactions_to_database(
                    pd.DataFrame([row])
                )
                connection = sqlite3.connect(database_path)
                try:
                    stored = connection.execute(
                        "SELECT source_properties_json "
                        "FROM expit_payload_transactions"
                    ).fetchone()[0]
                finally:
                    connection.close()
            finally:
                set_database_path(previous_path)

        self.assertEqual(json.loads(stored), row["source_properties"])

    def test_turnover_audit_includes_unselected_grade_blocks(self):
        previous_path = get_database_path()
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "turnover-audit.db")
            set_database_path(database_path)
            try:
                rows = pd.DataFrame([
                    {
                        "agent": "EX01",
                        "source": "BLOCK_1",
                        "start_datetime": datetime(2026, 1, 1, 0, 0),
                        "payload": 40.0,
                        "source_grade_fe": 60.0,
                        "source_grade_si": 4.0,
                        "source_grade_al": 2.0,
                        "source_grade_mn": 0.1,
                        "source_grade_p": 0.08,
                        "destination": "Stockpiles/SP_A",
                        "delivered_datetime": datetime(2026, 1, 1, 1, 0),
                        "planned_destination": "Stockpiles/SP_A",
                        "two_wp_destination_resolution": "exact_2wp",
                        "two_wp_turnover_guidance_applicable": True,
                        "two_wp_first_reclaim_datetime": (
                            "2026-01-10T06:00:00"
                        ),
                        "two_wp_destination_turnover_priority": 0.5,
                    },
                    {
                        "agent": "EX01",
                        "source": "BLOCK_1",
                        "start_datetime": datetime(2026, 1, 1, 2, 0),
                        "payload": 60.0,
                        "source_grade_fe": 60.0,
                        "source_grade_si": 4.0,
                        "source_grade_al": 2.0,
                        "source_grade_mn": 0.1,
                        "source_grade_p": 0.08,
                        "destination": "Stockpiles/SP_A",
                        "delivered_datetime": datetime(2026, 1, 1, 3, 0),
                        "planned_destination": "Stockpiles/SP_A",
                        "two_wp_destination_resolution": "exact_2wp",
                        "two_wp_turnover_guidance_applicable": True,
                        "two_wp_first_reclaim_datetime": (
                            "2026-01-10T06:00:00"
                        ),
                        "two_wp_destination_turnover_priority": 0.5,
                    },
                    {
                        "agent": "EX02",
                        "source": "BLOCK_2",
                        "start_datetime": datetime(2026, 1, 1, 0, 0),
                        "payload": 75.0,
                        "source_grade_fe": 59.0,
                        "source_grade_si": 5.0,
                        "source_grade_al": 3.0,
                        "source_grade_mn": 0.2,
                        "source_grade_p": 0.09,
                        "destination": "Stockpiles/SP_B",
                        "delivered_datetime": datetime(2026, 1, 1, 1, 0),
                        "planned_destination": "Stockpiles/SP_B",
                        "two_wp_destination_resolution": "pit_fallback",
                        "two_wp_turnover_guidance_applicable": False,
                    },
                ])
                DatabaseManager().write_expit_payload_transactions_to_database(
                    rows,
                    {
                        "two_wp_destination_turnover_guidance_enabled": True,
                        "two_wp_destination_turnover_incentive": 10.0,
                    },
                )
                connection = sqlite3.connect(database_path)
                try:
                    audit = pd.read_sql(
                        "SELECT * FROM two_wp_grade_block_turnover_audit "
                        "ORDER BY grade_block",
                        connection,
                    )
                finally:
                    connection.close()
            finally:
                set_database_path(previous_path)

        self.assertEqual(audit["grade_block"].tolist(), [
            "BLOCK_1", "BLOCK_2"
        ])
        self.assertEqual(audit.iloc[0]["available_payload_wmt"], 100.0)
        self.assertEqual(audit.iloc[0]["payload_count"], 2)
        self.assertEqual(
            audit.iloc[0]["two_wp_planned_stockpile_destination"],
            "Stockpiles/SP_A",
        )
        self.assertEqual(
            audit.iloc[0][
                "two_wp_destination_turnover_guidance_applied"
            ],
            1,
        )
        self.assertEqual(
            audit.iloc[0][
                "two_wp_destination_turnover_incentive_applied"
            ],
            5.0,
        )
        self.assertEqual(
            audit.iloc[1]["two_wp_planned_stockpile_destination"], ""
        )
        self.assertEqual(
            audit.iloc[1][
                "two_wp_destination_turnover_guidance_applied"
            ],
            0,
        )


class ManualConstraintPropertyTests(unittest.TestCase):
    def test_partial_direct_tip_prorates_additive_property_for_ratio_report(self):
        start = datetime(2026, 1, 1, 5, 0)
        periods = {
            "preplan_start": start,
            "preplan_end": start + timedelta(hours=1),
            "period_1_start": start + timedelta(hours=1),
            "period_1_end": start + timedelta(hours=13),
            "period_2_start": start + timedelta(hours=13),
            "period_2_end": start + timedelta(hours=25),
        }
        payloads = pd.DataFrame([{
            "source": "GB1",
            "direct_tip_id": "DT1",
            "payload": 100.0,
            "delivered_datetime": start + timedelta(minutes=10),
            "direct_tip_eligible": True,
            "source_grade_fe": 64.0,
            "source_grade_si": 2.0,
            "source_grade_al": 1.0,
            "source_grade_p": 0.05,
            "source_grade_mn": 0.05,
            "source_properties": {"fines_wmt": 50.0},
        }])
        planner = ManualBlendPlanner(
            [{
                "Blend ID": "1",
                "Start Datetime": start,
                "End Datetime": start + timedelta(hours=1),
            }],
            [{
                "Blend ID": "1",
                "Sources": "SP1",
                "Source Ratios": "1",
            }],
            {"SP1": {
                "balance": 1000.0,
                "grade_fe": 60.0,
                "grade_si": 4.0,
                "grade_al": 2.0,
                "grade_p": 0.08,
                "grade_mn": 0.10,
                "fines_wmt": 0.0,
            }},
            [],
            payloads,
            periods,
            [],
            crusher_rate=100.0,
            calendar_inputs={
                "solver_config": {"custom_constraints": [{
                    "name": "Fines",
                    "numerator": "fines_wmt_per_source_wmt",
                    "denominator": "one",
                }]},
            },
        )
        states = planner.build_steady_states()
        report = planner.build_report(
            states,
            {states[0]["state_key"]: {"GB1": 20.0}},
        )

        direct_tip = report.loc[report["source_type"] == "grade_block"].iloc[0]
        # The 20 t selection receives 20% of the candidate's 50 t additive
        # fines mass: 10 t / 20 t = a 0.5 per-source coefficient.
        self.assertAlmostEqual(
            direct_tip[
                "custom_constraint_fines_source_numerator_coefficient"
            ], 0.5
        )
        self.assertIsNone(
            direct_tip[
                "custom_constraint_fines_source_denominator_coefficient"
            ]
        )
        self.assertAlmostEqual(
            direct_tip[
                "custom_constraint_fines_source_numerator_contribution"
            ],
            10.0,
        )
        self.assertEqual(
            direct_tip[
                "custom_constraint_fines_source_denominator_contribution"
            ],
            0.0,
        )
        # The legacy name ends in _wmt, so it is treated as an additive
        # expression: 0.5 per source WMT * 20 selected WMT = 10 / scalar 1.
        self.assertAlmostEqual(
            direct_tip["custom_constraint_fines_numerator"], 10.0
        )
        self.assertAlmostEqual(
            direct_tip["custom_constraint_fines_denominator"], 1.0
        )
        self.assertAlmostEqual(
            direct_tip["custom_constraint_fines_actual_ratio"], 10.0
        )
        self.assertAlmostEqual(
            direct_tip["source_property_fines_wmt"], 10.0
        )


class CustomConstraintIntegrationTests(unittest.TestCase):
    def test_data_loader_places_period_bounds_on_normalized_definition(self):
        definition = {
            "name": "Ultrafines",
            "numerator": "minus_1mm_pct",
            "denominator": "one",
        }
        calendar = calendar_inputs([definition])
        calendar["crusher_custom_constraint_ultrafines_min"] = {
            "Preplan": "12.5",
            "Period_1": "",
            "Period_2": None,
        }
        calendar["crusher_custom_constraint_ultrafines_max"] = {
            "Preplan": "15",
            "Period_1": "18.5",
            "Period_2": "",
        }

        _, _, _, targets = DataLoader(
            {}, calendar, pd.DataFrame(), []
        ).load_data()

        preplan = targets["preplan"]["custom_constraints"][0]
        period_1 = targets["period_1"]["custom_constraints"][0]
        self.assertEqual((preplan["minimum"], preplan["maximum"]), (12.5, 15.0))
        self.assertEqual((period_1["minimum"], period_1["maximum"]), (None, 18.5))

    def test_data_loader_rejects_inverted_custom_bounds(self):
        definition = {
            "name": "Ultrafines",
            "numerator": "minus_1mm_pct",
            "denominator": "one",
        }
        calendar = calendar_inputs([definition])
        calendar["crusher_custom_constraint_ultrafines_min"] = period_values(20)
        calendar["crusher_custom_constraint_ultrafines_max"] = period_values(10)

        with self.assertRaisesRegex(ValueError, "Min cannot be greater than Max"):
            DataLoader({}, calendar, pd.DataFrame(), []).load_data()

    def test_optimizer_enforces_ratio_and_reports_aggregate_and_source_values(self):
        definition = {
            "name": "Ultrafines",
            "key": "ultrafines",
            "numerator": "minus_1mm_pct",
            "denominator": "one",
            "minimum": 25.0,
            "maximum": 25.0,
        }
        result = optimize(
            [
                make_event("LOW", properties={"minus_1mm_pct": 10.0}),
                make_event("HIGH", properties={"minus_1mm_pct": 30.0}),
            ],
            optimizer_target(custom_constraints=[definition]),
        )

        self.assertTrue(result["Linprog_result_object"].success)
        self.assertAlmostEqual(
            result["custom_constraint_ultrafines_actual_ratio"], 25.0
        )
        self.assertEqual(result["custom_constraint_ultrafines_target_min"], 25.0)
        self.assertEqual(result["custom_constraint_ultrafines_target_max"], 25.0)
        self.assertEqual(
            result["custom_constraint_ultrafines_numerator_expression"],
            "minus_1mm_pct",
        )
        source_coefficients = {
            row["source"]: (
                row["custom_constraint_ultrafines_source_numerator_coefficient"],
                row["custom_constraint_ultrafines_source_denominator_coefficient"],
            )
            for row in result["transactions"]
        }
        self.assertEqual(source_coefficients["LOW"], (10.0, None))
        self.assertEqual(source_coefficients["HIGH"], (30.0, None))
        for row in result["transactions"]:
            self.assertAlmostEqual(
                row["custom_constraint_ultrafines_source_numerator_contribution"],
                row["custom_constraint_ultrafines_source_numerator_coefficient"]
                * row["actual_tonnes"],
            )
        self.assertAlmostEqual(
            sum(
                row["custom_constraint_ultrafines_source_numerator_contribution"]
                for row in result["transactions"]
            ) / sum(row["actual_tonnes"] for row in result["transactions"]),
            result["custom_constraint_ultrafines_numerator"],
        )

    def test_additive_over_literal_one_constrains_selected_total(self):
        definition = {
            "name": "Fines tonnes",
            "numerator": "fines_wmt",
            "denominator": "1",
            "maximum": 30.0,
        }
        result = optimize(
            [
                make_event(
                    "LOW",
                    properties={"fines_wmt": 100.0},
                    property_kinds={"fines_wmt": "additive"},
                ),
                make_event(
                    "HIGH",
                    properties={"fines_wmt": 500.0},
                    property_kinds={"fines_wmt": "additive"},
                ),
            ],
            optimizer_target(custom_constraints=[definition]),
        )

        self.assertTrue(result["Linprog_result_object"].success)
        self.assertLessEqual(
            result["custom_constraint_fines_tonnes_numerator"], 30.0 + 1e-6
        )
        self.assertEqual(
            result["custom_constraint_fines_tonnes_denominator"], 1.0
        )

    def test_additive_ratio_uses_sums_across_selected_sources(self):
        definition = {
            "name": "Fine ratio",
            "numerator": "fine_wmt",
            "denominator": "product_wmt",
            "minimum": 0.5,
            "maximum": 0.5,
        }
        kinds = {"fine_wmt": "additive", "product_wmt": "additive"}
        result = optimize(
            [
                make_event(
                    "LOW",
                    properties={"fine_wmt": 100.0, "product_wmt": 500.0},
                    property_kinds=kinds,
                ),
                make_event(
                    "HIGH",
                    properties={"fine_wmt": 500.0, "product_wmt": 100.0},
                    property_kinds=kinds,
                ),
            ],
            optimizer_target(custom_constraints=[definition]),
        )

        self.assertTrue(result["Linprog_result_object"].success)
        self.assertAlmostEqual(
            result["custom_constraint_fine_ratio_numerator"]
            / result["custom_constraint_fine_ratio_denominator"],
            0.5,
        )
        self.assertAlmostEqual(
            result["custom_constraint_fine_ratio_actual_ratio"], 0.5
        )

    def test_missing_custom_mapping_on_any_candidate_source_fails(self):
        definition = {
            "name": "Fine tonnes",
            "numerator": "fine_wmt",
            "denominator": "1",
            "maximum": 50.0,
        }
        with self.assertRaisesRegex(
            CustomConstraintError, "MISSING.*unavailable"
        ):
            optimize(
                [
                    make_event(
                        "MAPPED",
                        properties={"fine_wmt": 100.0},
                        property_kinds={"fine_wmt": "additive"},
                    ),
                    make_event(
                        "MISSING",
                        properties={},
                        property_kinds={"fine_wmt": "additive"},
                    ),
                ],
                optimizer_target(custom_constraints=[definition]),
            )

    def test_strict_selected_grade_stream_does_not_fall_back(self):
        event = make_event("SP1")
        event.grade_streams = {
            "modelled_product": {
                "*": {
                    "fe": 60.0, "si": 4.0, "al": 2.0,
                    "p": 0.08, "mn": 0.10,
                }
            }
        }
        with self.assertRaisesRegex(
            ValueError, "stream fallback is disabled"
        ):
            optimize(
                [event],
                optimizer_target(),
                {
                    "selected_data_stream": "adjusted_product",
                    "strict_mapped_fields": True,
                },
            )

    def test_weighted_average_uses_declared_additive_weight(self):
        definition = {
            "name": "Product Fe",
            "numerator": "product_fe",
            "denominator": "1",
            "minimum": 55.0,
            "maximum": 55.0,
        }
        kinds = {
            "product_fe": "weighted_average",
            "product_dmt": "additive",
        }
        weights = {"product_fe": "product_dmt"}
        result = optimize(
            [
                make_event(
                    "LOW",
                    properties={"product_fe": 50.0, "product_dmt": 200.0},
                    property_kinds=kinds,
                    property_weights=weights,
                ),
                make_event(
                    "HIGH",
                    properties={"product_fe": 60.0, "product_dmt": 800.0},
                    property_kinds=kinds,
                    property_weights=weights,
                ),
            ],
            optimizer_target(custom_constraints=[definition]),
        )

        self.assertTrue(result["Linprog_result_object"].success)
        self.assertAlmostEqual(
            result["custom_constraint_product_fe_actual_ratio"], 55.0
        )

    def test_weighted_average_missing_declared_weight_fails(self):
        definition = {
            "name": "Product Fe",
            "numerator": "product_fe",
            "denominator": "1",
            "maximum": 60.0,
        }
        with self.assertRaisesRegex(
            CustomConstraintError, "no declared additive weight field"
        ):
            optimize(
                [make_event(
                    "SP1",
                    properties={"product_fe": 55.0},
                    property_kinds={"product_fe": "weighted_average"},
                )],
                optimizer_target(custom_constraints=[definition]),
            )

    def test_optimizer_supports_field_operations_inside_an_expression(self):
        definition = {
            "name": "Recovered fines",
            "numerator": "minus_1mm_pct * mass_recovery_pct",
            "denominator": "one",
            "minimum": 8.0,
            "maximum": 8.0,
        }
        result = optimize(
            [make_event(
                "SP1",
                properties={"minus_1mm_pct": 10.0, "mass_recovery_pct": 0.8},
            )],
            optimizer_target(custom_constraints=[definition]),
        )

        self.assertTrue(result["Linprog_result_object"].success)
        self.assertAlmostEqual(
            result["custom_constraint_recovered_fines_actual_ratio"], 8.0
        )

    def test_positive_direct_tip_minimum_is_infeasible_without_grade_blocks(self):
        result = optimize(
            [make_event("SP1")],
            optimizer_target(direct_feed_ratio_min=0.1),
        )

        self.assertFalse(result["Linprog_result_object"].success)

    def test_diagnostics_identify_impossible_custom_coefficient_range(self):
        definition = {
            "name": "Clay index",
            "numerator": "clay_index",
            "denominator": "one",
            "minimum": 30.0,
        }
        result = optimize(
            [
                make_event("LOW", properties={"clay_index": 10.0}),
                make_event("HIGH", properties={"clay_index": 20.0}),
            ],
            optimizer_target(custom_constraints=[definition]),
        )

        self.assertFalse(result["Linprog_result_object"].success)
        diagnostic = result["diagnostics"]["custom_constraint_ranges"][0]
        self.assertEqual(diagnostic["name"], "Clay index")
        self.assertEqual(diagnostic["available_min"], 10.0)
        self.assertEqual(diagnostic["available_max"], 20.0)
        self.assertTrue(any(
            "Clay index" in cause
            and "minimum 30" in cause
            and "10 to 20" in cause
            for cause in result["diagnostics"]["likely_causes"]
        ))


class InventoryOnlyDataStreamIntegrationTests(unittest.TestCase):
    def load_inventory_or_amt(self, amt):
        calendar = add_stockpile_calendar_rows(calendar_inputs([{
            "name": "Inventory properties",
            "numerator": "fines_wmt * minus_1mm_pct",
            "denominator": "1",
        }]))
        selected = stockpile_record(amt=amt)
        # AMT tonnes come from a positive chunk, never the inventory fallback.
        chunks = [{**selected["SP1"], "footprint": "SP1", "sequence": 1}] if amt else []
        loader = DataLoader(
            selected,
            calendar,
            pd.DataFrame(),
            chunks,
        )
        stockpiles, grade_blocks, equipment, targets = loader.load_data()
        tracker = BalanceTracker(
            stockpiles, grade_blocks, "preplan", []
        )
        start = datetime(2026, 1, 1, 0, 0)
        events = EventPoolGenerator(
            stockpiles, grade_blocks, equipment
        ).get_events(
            "preplan",
            pd.DataFrame(),
            start,
            start + timedelta(hours=1),
            tracker,
        )
        result = optimize(
            events,
            targets["preplan"],
            {
                "selected_data_stream": "adjusted_product",
                "target_product_brand": "CCFB",
            },
        )
        return stockpiles[0], events[0], result

    def test_inventory_only_source_preserves_properties_and_selected_product(self):
        stockpile, event, result = self.load_inventory_or_amt(False)

        self.assertFalse(stockpile.is_AMT)
        self.assertFalse(event.is_amt)
        self.assertEqual(event.source_properties["minus_1mm_pct"], 12.5)
        self.assertEqual(event.source_properties["fines_wmt"], 400.0)
        self.assertTrue(result["Linprog_result_object"].success)
        transaction = result["transactions"][0]
        self.assertEqual(transaction["selected_grade_stream"], "adjusted_product")
        self.assertEqual(transaction["selected_grade_brand"], "CCFB")
        self.assertAlmostEqual(transaction["grade_fe"], 63.0)
        self.assertAlmostEqual(result["crusher_actual_grade_fe"], 63.0)

    def test_inventory_and_amt_sources_use_identical_stream_selection_logic(self):
        _, inventory_event, inventory_result = self.load_inventory_or_amt(False)
        _, amt_event, amt_result = self.load_inventory_or_amt(True)

        self.assertFalse(inventory_event.is_amt)
        self.assertTrue(amt_event.is_amt)
        inventory_transaction = inventory_result["transactions"][0]
        amt_transaction = amt_result["transactions"][0]
        for analyte in ANALYTES:
            self.assertAlmostEqual(
                inventory_transaction[f"grade_{analyte}"],
                amt_transaction[f"grade_{analyte}"],
            )
        self.assertEqual(
            inventory_transaction["selected_grade_stream"],
            amt_transaction["selected_grade_stream"],
        )
        self.assertEqual(
            inventory_transaction["selected_grade_brand"],
            amt_transaction["selected_grade_brand"],
        )


if __name__ == "__main__":
    unittest.main()
