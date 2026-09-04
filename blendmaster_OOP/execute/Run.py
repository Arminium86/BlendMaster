# This is the control centre in which user and inventory data are imported and the program is executed
from PyQt5.QtCore import QObject, pyqtSignal, QEventLoop
import builtins, pandas as pd, traceback, copy
from classes.CaseModeller import (
    CaseModeller,
    ProductBuildCapacityComplete,
    ProductBuildRepairFailed,
    SolverRunAborted,
    SteadyStateInfeasible,
)
from classes.DataLoader import DataLoader
from classes.PeriodManager import PeriodManager
from classes.ProductBuildProgress import ProductBuildProgress
from classes.ProductBuildLanes import (
    normalize_byproduct_grade_fields,
    normalize_byproduct_quantity_fields,
)
from classes.ExpitDataHandler import ExpitDataHandler
from classes.Optimizer import Optimizer
from classes.GradeStreams import configured_brands, resolve_grade_vector
from classes.ClosingROMStocksCompliance import ClosingROMStocksCompliance
from database.SQLiteDatabase import DatabaseManager
from execute.Requirements import Requirements
from pandas import DataFrame

class BlendMasterRunError(Exception):
    def __init__(self, message, title="BlendMaster"):
        super().__init__(message)
        self.user_message = message
        self.title = title


class InfeasibleRunError(BlendMasterRunError):
    def __init__(self, message):
        super().__init__(message, title="Infeasible Run")


class StockpileSelectionRunError(BlendMasterRunError):
    def __init__(self, stockpile_name):
        message = (
            f"APS Mining.csv contains transactions with destination stockpile '{stockpile_name}', "
            "but that stockpile is not selected in Stockpile Inventories.\n\n"
            "Go back to Stockpile Inventories and tick Use for this stockpile, or remove/change "
            "those destination transactions in APS Mining.csv."
        )
        super().__init__(message, title="Stockpile Selection Required")


class Run:
    
    def __init__(self, gui):
        self.gui = gui
        self.case_bridge = CaseModellerBridge()

        # Connect bridge signals to the GUI
        self.case_bridge.output_signal.connect(gui.display_decision_output)
        if hasattr(gui, "update_progress_message"):
            self.case_bridge.output_signal.connect(gui.update_progress_message)
        self.case_bridge.dataframe_signal.connect(gui.display_decision_dataframe)
        self.case_bridge.input_request_signal.connect(gui.display_decision_output)
        self.case_modeller = None
        self.manual_case_modeller = None
        self.manual_blend_dash = None
        self.abort_requested = False

    def request_abort(self):
        self.abort_requested = True
        if self.case_modeller is not None and hasattr(self.case_modeller, "request_abort"):
            self.case_modeller.request_abort()

    def is_abort_requested(self):
        return bool(self.abort_requested)

    def prepare_expit_payload_transactions(
        self,
        start_time,
        expit_mode,
        file_path,
        reevaluate_aps_direct_tip=False,
        selected_aps_crusher=None,
        site_context=None,
        two_wp_file_path=None,
        selected_24hr_agents=None,
    ):
        """Build the exact APS payload population supplied to DataLoader."""
        if not file_path:
            return DataFrame()

        site_context = site_context or {}
        reference_path = two_wp_file_path or file_path
        destination_guidance = site_context.get("aps_destination_guidance")
        if (
            not destination_guidance
            or destination_guidance.get("matching_version")
            != ExpitDataHandler.DESTINATION_GUIDANCE_VERSION
        ):
            destination_guidance = (
                ExpitDataHandler.build_2wp_destination_guidance(reference_path)
            )

        try:
            interaction_mode = int(expit_mode)
        except (TypeError, ValueError):
            interaction_mode = 1

        handler = ExpitDataHandler(
            file_path,
            include_crusher_destinations=reevaluate_aps_direct_tip,
            selected_crusher_name=selected_aps_crusher,
            operational_mine=site_context.get("mine"),
            operational_crusher=site_context.get("crusher"),
            operational_opf=site_context.get("opf"),
            direct_tip_movement_rules=site_context.get(
                "direct_tip_movement_rules", []
            ),
            destination_guidance=destination_guidance,
            selected_agent_names=selected_24hr_agents,
            grade_field_mappings=site_context.get(
                "aps_grade_field_mappings", {}
            ),
            source_property_field_mappings=site_context.get(
                "aps_source_property_field_mappings", {}
            ),
            configured_product_brands=site_context.get(
                "product_brands", []
            ),
            source_property_kinds={
                str(row.get("name")): str(row.get("kind"))
                for row in (site_context.get("field_definitions") or [])
                if isinstance(row, dict) and row.get("name")
            },
            source_property_weights={
                str(row.get("name")): str(row.get("weight_field"))
                for row in (site_context.get("field_definitions") or [])
                if isinstance(row, dict)
                and row.get("name")
                and row.get("weight_field")
            },
            preserve_source_payloads_for_reconciliation=(
                interaction_mode == 2
            ),
        )
        transactions = handler.process_transactions()
        transactions = self._ensure_direct_tip_ids(transactions)
        if interaction_mode == 2:
            transactions = handler.update_transactions(
                transactions,
                start_time,
                (site_context or {}).get(
                    "expit_completion_tolerance_pct", 10.0
                ),
            )
            reconciliation_attributes = dict(transactions.attrs)
            transactions = self._ensure_direct_tip_ids(transactions)
            transactions.attrs.update(reconciliation_attributes)
        elif "route_only_waste" in transactions:
            transactions = transactions[
                ~transactions["route_only_waste"].astype(bool)
            ].reset_index(drop=True)
        if transactions is not None:
            # DataFrame operations may drop attrs, so merge rather than
            # replacing the reconciliation audit attached above.
            transactions.attrs["source_property_warnings"] = list(
                getattr(handler, "property_warnings", []) or []
            )
        return transactions if transactions is not None else DataFrame()

    def _run_case_modeller(self, case_modeller):
        self.case_modeller = case_modeller
        original_print = builtins.print
        original_input = builtins.input
        try:
            builtins.print = self.case_bridge.print
            builtins.input = self.case_bridge.input
            case_modeller.run()
        finally:
            builtins.print = original_print
            builtins.input = original_input

    @staticmethod
    def _case_blend_report(case_modeller):
        results = case_modeller.results.copy()
        if (
            "source_actual_tonnes" in results
            and "crusher_actual_tonnes" in results
        ):
            source_tonnes = pd.to_numeric(
                results["source_actual_tonnes"], errors="coerce"
            ).fillna(0)
            crusher_tonnes = pd.to_numeric(
                results["crusher_actual_tonnes"], errors="coerce"
            ).fillna(0)
            results = results[
                ((source_tonnes != 0) & (crusher_tonnes != 0))
                | ((source_tonnes == 0) & (crusher_tonnes == 0))
            ]
        results = case_modeller.group_grade_block_rows(results)
        return ProductBuildProgress.annotate(
            results, case_modeller.product_build_settings
        )

    @staticmethod
    def _completed_offspec_product_build_names(case_modeller):
        names = []
        settings = getattr(case_modeller, "product_build_settings", []) or []
        states = getattr(
            case_modeller, "product_build_runtime_states", []
        ) or []
        for index, build_setting in enumerate(settings):
            if index >= len(states):
                continue
            build_state = states[index]
            completed = (
                float(build_state.get("tonnes", 0) or 0)
                >= float(build_setting.get("target_tonnes", 0) or 0)
                - case_modeller.PRODUCT_BUILD_TONNES_TOLERANCE
            )
            if (
                completed
                and not case_modeller.product_build_grade_on_spec(
                    build_state, build_setting
                )
            ):
                names.append(
                    str(
                        build_setting.get("build_name")
                        or build_setting.get("brand")
                        or f"Build {index + 1}"
                    )
                )
        return names

    @staticmethod
    def _closing_compliance_stockpiles(
        site_context, blend_report=None, build_report=None
    ):
        """Return stockpiles used by either 2WP guidance or BlendMaster."""
        names = set()
        guidance = (site_context or {}).get("aps_destination_guidance") or {}
        for allocations in (
            guidance.get("source_destinations", {}) or {}
        ).values():
            for allocation in allocations or []:
                names.add(
                    allocation.get("destination_name")
                    or allocation.get("destination")
                )
        names.update(
            (guidance.get("stockpile_reclaim_windows", {}) or {}).keys()
        )
        if isinstance(blend_report, pd.DataFrame) and not blend_report.empty:
            rows = blend_report
            if "source_type" in rows:
                rows = rows[
                    rows["source_type"].astype(str).str.lower().eq("stockpile")
                ]
            for column in (
                "parent_stockpile", "balance_tracker_source_id", "source",
            ):
                if column in rows:
                    names.update(rows[column].dropna().astype(str))
                    break
        if isinstance(build_report, pd.DataFrame) and "stockpile" in build_report:
            names.update(build_report["stockpile"].dropna().astype(str))
        return {
            ClosingROMStocksCompliance.normalize_stockpile_name(name)
            for name in names
            if ClosingROMStocksCompliance.normalize_stockpile_name(name)
        }

    def execute(
        self,
        start_time,
        expit_mode,
        file_path,
        blend_mode,
        stockpile_data,
        calendar_inputs,
        hex_sequence_table,
        min_stockpiles=None,
        max_stockpiles=None,
        min_stockpile_contribution_ratio=None,
        solver_config=None,
        reevaluate_aps_direct_tip=False,
        selected_aps_crusher=None,
        site_context=None,
        two_wp_file_path=None,
        selected_24hr_agents=None,
        planning_period_count=3,
        prepared_expit_payload_transactions=None,
        expit_input_cache_signature=None,
    ):
        self.abort_requested = False
        self.case_bridge.print(
            "Preparing schedules and model inputs for the first steady state..."
        )

        # Install required libraries
        #requirements = Requirements()
        #requirements.install_requirements()

        # Initialize periods
        periods = PeriodManager(planning_period_count)
        periods.calculate_periods(start_time)
        calendar_inputs = calendar_inputs or {}
        calendar_inputs["planning_period_count"] = periods.period_count

        # Database View prepares this same payload population for audit. Reuse
        # it so the inspected records and the subsequent run cannot diverge.
        if prepared_expit_payload_transactions is not None:
            self.case_bridge.print(
                "Reusing saved Expit payloads and sequence reconciliation."
            )
            expit_payload_transactions = copy.deepcopy(
                prepared_expit_payload_transactions
            )
        elif file_path:
            self.case_bridge.print(
                "Preparing Expit payloads"
                + (
                    " and refreshing actual sequence reconciliation..."
                    if str(expit_mode or 1).strip() == "2"
                    else "..."
                )
            )
            expit_payload_transactions = self.prepare_expit_payload_transactions(
                start_time,
                expit_mode,
                file_path,
                reevaluate_aps_direct_tip,
                selected_aps_crusher,
                site_context,
                two_wp_file_path,
                selected_24hr_agents,
            )
        else:
            expit_payload_transactions = DataFrame()

        # Store the exact, solver-ready frame immediately after preparation.
        # This is intentionally separate from the readable reporting table,
        # which omits internal columns and DataFrame reconciliation attrs.
        if (
            file_path
            and expit_input_cache_signature
            and prepared_expit_payload_transactions is None
        ):
            DatabaseManager().write_expit_input_cache(
                expit_payload_transactions,
                expit_input_cache_signature,
                metadata={
                    "source": "solver_preparation",
                    "scenario_start": str(start_time),
                    "expit_mode": expit_mode,
                },
            )

        # User interaction required to choose between original time and updated time methods
        user_interaction_mode = expit_mode

        # Cast user choice to appropriate type
        try:
            user_interaction_mode = int(user_interaction_mode)
        except ValueError:
            print("Invalid input. Please enter a number.")

        database_manager = DatabaseManager()
        expit_payload_transactions_to_save = None
        solver_config = dict(solver_config or {})
        solver_config["stockpile_timing_guidance"] = (
            (site_context or {}).get("aps_stockpile_timing_guidance", {}) or {}
        )
        solver_config["active_blend_guidance"] = (
            (site_context or {}).get("aps_active_blend_guidance", []) or []
        )
        solver_config["product_builds_configured"] = bool(
            (calendar_inputs or {}).get("product_build_settings")
        )
        try:
            contingency_plan_count = max(
                int(solver_config.get("contingency_plan_count", 0) or 0),
                0,
            )
        except (TypeError, ValueError):
            contingency_plan_count = 0
        contingency_plan_count = min(contingency_plan_count, 10)
        solver_config["contingency_plan_count"] = contingency_plan_count
        # Enumeration depth is independent for primary and contingency plans.
        solver_config["max_blend_options_per_steady_state"] = max(
            int(
                solver_config.get(
                    "max_blend_options_per_steady_state", 12
                )
                or 12
            ),
            1,
        )
        solver_config["selected_data_stream"] = (
            (site_context or {}).get("selected_data_stream") or "adjusted_product"
        )
        for key, default in {
            "crusher_tonnes_stream": "modelled_rom_wmt",
            "reclaimer_tonnes_stream": "modelled_rom_wmt",
            "product_build_tonnes_stream": "modelled_product_wmt",
        }.items():
            solver_config[key] = (site_context or {}).get(key) or solver_config.get(key) or default
        solver_config["byproducts_enabled"] = bool(
            (site_context or {}).get(
                "byproducts_enabled", solver_config.get("byproducts_enabled", False)
            )
        )
        solver_config["byproduct_quantity_fields"] = normalize_byproduct_quantity_fields(
            (site_context or {}).get("byproduct_quantity_fields")
            or solver_config.get("byproduct_quantity_fields")
        )
        solver_config["byproduct_grade_fields"] = normalize_byproduct_grade_fields(
            (site_context or {}).get("byproduct_grade_fields")
            or solver_config.get("byproduct_grade_fields")
        )
        solver_config["configured_product_brands"] = list(
            (site_context or {}).get("product_brands", []) or []
        )
        field_definitions = [
            row for row in (
                (site_context or {}).get("field_definitions") or []
            )
            if isinstance(row, dict) and row.get("name")
        ]
        solver_config["optimisation_source_property_fields"] = [
            str(row["name"])
            for row in field_definitions
            if row.get("use_in_optimisation")
        ]
        if solver_config["byproducts_enabled"]:
            solver_config["optimisation_source_property_fields"] = sorted(set(
                solver_config["optimisation_source_property_fields"]
                + list(solver_config["byproduct_quantity_fields"].values())
                + [
                    field
                    for lane_fields in solver_config["byproduct_grade_fields"].values()
                    for field in lane_fields.values()
                ]
            ))
        solver_config["source_property_kinds"] = {
            str(row["name"]): str(row.get("kind") or "weighted_average")
            for row in field_definitions
        }
        solver_config["source_property_weights"] = {
            str(row["name"]): str(row.get("weight_field"))
            for row in field_definitions
            if row.get("weight_field")
        }
        solver_config[
            "contingency_max_blend_options_per_steady_state"
        ] = max(
            int(
                solver_config.get(
                    "contingency_max_blend_options_per_steady_state",
                    solver_config["max_blend_options_per_steady_state"],
                )
                or 12
            ),
            1,
        )

        if user_interaction_mode == 2 and file_path:
            expit_payload_transactions_to_save = expit_payload_transactions.copy()

        elif user_interaction_mode == 1 and file_path:
            expit_payload_transactions_to_save = expit_payload_transactions.copy()

        else: 
            print("No 24HR APS schedule imported.")

        stockpile_data = self._include_aps_destination_stockpiles(
            stockpile_data,
            calendar_inputs,
            expit_payload_transactions,
        )

        # Load input data (this is combined user input and opening inventories)
        loader_calendar_inputs = dict(calendar_inputs or {})
        loader_calendar_inputs["solver_config"] = solver_config
        input_data = DataLoader(
            stockpile_data,
            loader_calendar_inputs,
            expit_payload_transactions,
            hex_sequence_table,
            periods,
        )

        stockpile_data_objects, grade_block_data_objects, equipment_data_objects, crusher_target_data = input_data.load_data()

        for warning in (site_context or {}).get("historical_recon_warnings", []) or []:
            self.case_bridge.print(f"Data stream warning: {warning}")
        expected_brands = configured_brands((site_context or {}).get("product_brands", []))
        fallback_messages = []
        for source in [*stockpile_data_objects, *grade_block_data_objects]:
            brands_to_check = expected_brands or [""]
            for brand in brands_to_check:
                _grades, fallbacks = resolve_grade_vector(
                    getattr(source, "grade_streams", None),
                    (site_context or {}).get("selected_data_stream", "adjusted_product"),
                    brand,
                    source,
                )
                if fallbacks:
                    analytes = ", ".join(item["analyte"] for item in fallbacks)
                    fallback_messages.append(
                        f"{source.name} / {brand or 'unbranded'}: {analytes}"
                    )
        if fallback_messages:
            preview = "; ".join(fallback_messages[:12])
            remainder = len(fallback_messages) - 12
            if remainder > 0:
                preview += f"; and {remainder} more"
            self.case_bridge.print(
                "Data stream warning: selected-grade fallback will be applied independently per analyte for "
                + preview
            )

        min_stockpile_contribution_ratio = self._normalize_stockpile_contribution_ratio(
            min_stockpile_contribution_ratio
        )

        # Every plan starts from an identical deep-copied model state.
        model_seed = {
            "stockpiles": copy.deepcopy(stockpile_data_objects),
            "grade_blocks": copy.deepcopy(grade_block_data_objects),
            "equipment": copy.deepcopy(equipment_data_objects),
            "crusher_targets": copy.deepcopy(crusher_target_data),
        }

        def create_case_modeller(
            plan_id,
            interaction_mode,
            reserved_blend_signatures=None,
        ):
            return CaseModeller(
                stockpiles=copy.deepcopy(model_seed["stockpiles"]),
                grade_blocks=copy.deepcopy(model_seed["grade_blocks"]),
                equipment=copy.deepcopy(model_seed["equipment"]),
                crusher_targets=copy.deepcopy(model_seed["crusher_targets"]),
                expit_payload_transactions=expit_payload_transactions.copy(),
                periods=periods,
                user_interaction_mode=interaction_mode,
                hex_sequence_table=copy.deepcopy(hex_sequence_table),
                min_stockpiles=min_stockpiles,
                max_stockpiles=max_stockpiles,
                min_stockpile_contribution_ratio=min_stockpile_contribution_ratio,
                solver_config=copy.deepcopy(solver_config),
                product_build_settings=(calendar_inputs or {}).get(
                    "product_build_settings", []
                ),
                abort_callback=self.is_abort_requested,
                plan_id=plan_id,
                reserved_blend_signatures=reserved_blend_signatures,
                site_context=site_context,
            )

        primary_case_modeller = create_case_modeller(
            "Primary", blend_mode
        )
        self.case_modellers = {"Primary": primary_case_modeller}
        primary_outcome_error = None
        try:
            self.case_bridge.print(
                "Model inputs prepared. Starting the primary optimisation..."
            )
            self._run_case_modeller(primary_case_modeller)
        except ProductBuildCapacityComplete as outcome:
            primary_outcome_error = outcome
            self.case_bridge.print(str(outcome))
        except (
            ProductBuildRepairFailed,
            SolverRunAborted,
            SteadyStateInfeasible,
        ) as outcome:
            primary_outcome_error = outcome
            if isinstance(outcome, SteadyStateInfeasible):
                diagnostic = self._first_optimization_diagnostic(
                    getattr(outcome, "steady_state_number", None)
                ) or self._first_optimization_diagnostic()
                self.case_bridge.print(self._format_infeasible_run_message(
                    f"{outcome.title}: {outcome.user_message}",
                    diagnostic,
                ))
            else:
                self.case_bridge.print(
                    f"{outcome.title}: {outcome.user_message}"
                )
        except Exception as error:
            stockpile_selection_error = self._stockpile_selection_error(error)
            if stockpile_selection_error is not None:
                raise stockpile_selection_error
            raise

        completed_offspec_builds = (
            self._completed_offspec_product_build_names(
                primary_case_modeller
            )
        )
        if completed_offspec_builds:
            offspec_message = (
                "Completed product build(s) are off specification: "
                + ", ".join(completed_offspec_builds)
                + "."
            )
            if primary_outcome_error is None or isinstance(
                primary_outcome_error, ProductBuildCapacityComplete
            ):
                primary_outcome_error = BlendMasterRunError(
                    offspec_message,
                    title="Off-Spec Product Build",
                )
            elif offspec_message not in str(primary_outcome_error):
                primary_outcome_error.user_message = (
                    str(
                        getattr(
                            primary_outcome_error,
                            "user_message",
                            primary_outcome_error,
                        )
                    )
                    + " "
                    + offspec_message
                )

        if primary_outcome_error is None:
            try:
                self._validate_optimization_results()
                self._validate_stockpile_count_constraints(
                    min_stockpiles,
                    max_stockpiles,
                    min_stockpile_contribution_ratio,
                )
            except InfeasibleRunError as outcome:
                primary_outcome_error = outcome
                self.case_bridge.print(
                    f"{outcome.title}: {outcome.user_message}"
                )

        self.case_bridge.print(
            (
                "Optimisation complete. Writing database tables..."
                if primary_outcome_error is None
                else "Optimisation stopped. Preserving solved steady states "
                "and writing database tables..."
            )
        )
        database_manager.clear_optimisation_plan_results()

        self.case_bridge.print(
            "Writing 2WP active blend schedule to database..."
        )
        database_manager.write_two_wp_active_blend_report_to_database(
            solver_config.get("active_blend_guidance", [])
        )

        if expit_payload_transactions_to_save is not None:
            self.case_bridge.print("Writing expit payload transactions to database...")
            database_manager.write_expit_payload_transactions_to_database(
                expit_payload_transactions_to_save,
                solver_config,
            )

        self.case_bridge.print("Writing build report to database...")
        self.case_modeller.save_build_report()

        self.case_bridge.print("Writing optimised blend, depletion and profile reports to database...")
        self.case_modeller.save_optimised_blend_report()

        self.case_bridge.print("Writing material destination plan to database...")
        database_manager.write_material_destination_plan_to_database(
            payload_transactions=expit_payload_transactions,
            blend_report=self.case_modeller.results,
            plan_type="optimised",
            crusher_destination=(
                selected_aps_crusher
                or (site_context or {}).get("crusher")
            ),
            direct_tip_movement_rules=(site_context or {}).get(
                "direct_tip_movement_rules", []
            ),
        )

        self.case_bridge.print("Writing product build report to database...")
        self.case_modeller.save_product_build_report()

        primary_blend_report = self._case_blend_report(
            primary_case_modeller
        )
        database_manager.write_optimisation_plan_result(
            "blend", primary_blend_report, "Primary", 0
        )
        database_manager.write_optimisation_plan_result(
            "build", primary_case_modeller.build_report, "Primary", 0
        )
        database_manager.write_optimisation_plan_result(
            "product_build",
            primary_case_modeller.build_product_build_report(),
            "Primary",
            0,
        )
        primary_status = "complete"
        primary_message = "Primary optimised plan completed."
        if primary_outcome_error is not None:
            primary_message = str(
                getattr(primary_outcome_error, "user_message", None)
                or primary_outcome_error
            )
            if isinstance(primary_outcome_error, ProductBuildCapacityComplete):
                primary_status = "complete"
            elif isinstance(primary_outcome_error, SolverRunAborted):
                primary_status = "aborted"
            elif (
                isinstance(primary_outcome_error, ProductBuildRepairFailed)
                or getattr(primary_outcome_error, "title", "")
                == "Off-Spec Product Build"
            ):
                primary_status = "off_spec"
            else:
                primary_status = "partial"
        partial_plan_restored = bool(
            getattr(
                primary_case_modeller,
                "partial_plan_restored_after_repair",
                False,
            )
        )
        if partial_plan_restored:
            primary_message += (
                " The last internally consistent solved checkpoint before "
                "repair has been retained for Results and Reports."
            )

        plan_status_rows = [{
            "plan_id": "Primary",
            "plan_rank": 0,
            "status": primary_status,
            "message": primary_message,
            "reused_blend_fallbacks": 0,
        }]
        reserved_signatures = (
            primary_case_modeller.selected_plan_blend_signatures()
        )

        contingency_runs = (
            contingency_plan_count
            if primary_status == "complete"
            else 0
        )
        for plan_rank in range(1, contingency_runs + 1):
            plan_id = f"Contingency {plan_rank}"
            self.case_bridge.print(
                f"Running {plan_id} of {contingency_plan_count} using "
                "the highest-ranked blends not used by earlier plans..."
            )
            contingency = create_case_modeller(
                plan_id,
                1,
                reserved_signatures,
            )
            self.case_modellers[plan_id] = contingency
            try:
                self._run_case_modeller(contingency)
                self._validate_optimization_results()
                self._validate_stockpile_count_constraints(
                    min_stockpiles,
                    max_stockpiles,
                    min_stockpile_contribution_ratio,
                )
                contingency.build_report = (
                    contingency.balance_tracker.get_build_transactions()
                )
                blend_report = self._case_blend_report(contingency)
                product_build_report = (
                    contingency.build_product_build_report()
                )
                database_manager.write_optimisation_plan_result(
                    "blend", blend_report, plan_id, plan_rank
                )
                database_manager.write_optimisation_plan_result(
                    "build",
                    contingency.build_report,
                    plan_id,
                    plan_rank,
                )
                database_manager.write_optimisation_plan_result(
                    "product_build",
                    product_build_report,
                    plan_id,
                    plan_rank,
                )
                database_manager.write_material_destination_plan_to_database(
                    payload_transactions=expit_payload_transactions,
                    blend_report=blend_report,
                    plan_type="optimised",
                    plan_id=plan_id,
                    crusher_destination=(
                        selected_aps_crusher
                        or (site_context or {}).get("crusher")
                    ),
                    direct_tip_movement_rules=(site_context or {}).get(
                        "direct_tip_movement_rules", []
                    ),
                )
                reserved_signatures.update(
                    contingency.selected_plan_blend_signatures()
                )
                fallback_count = contingency.contingency_reuse_fallbacks
                message = (
                    "Plan completed with reused blends where no unused "
                    "feasible option existed."
                    if fallback_count
                    else "Plan completed using unused blend choices."
                )
                plan_status_rows.append({
                    "plan_id": plan_id,
                    "plan_rank": plan_rank,
                    "status": "complete",
                    "message": message,
                    "reused_blend_fallbacks": fallback_count,
                })
            except Exception as error:
                message = str(
                    getattr(error, "user_message", None) or error
                )
                if (
                    contingency.results is not None
                    and not contingency.results.empty
                ):
                    contingency.build_report = (
                        contingency.balance_tracker.get_build_transactions()
                    )
                    partial_blend_report = self._case_blend_report(
                        contingency
                    )
                    database_manager.write_optimisation_plan_result(
                        "blend",
                        partial_blend_report,
                        plan_id,
                        plan_rank,
                    )
                    database_manager.write_optimisation_plan_result(
                        "build",
                        contingency.build_report,
                        plan_id,
                        plan_rank,
                    )
                    database_manager.write_optimisation_plan_result(
                        "product_build",
                        contingency.build_product_build_report(),
                        plan_id,
                        plan_rank,
                    )
                    database_manager.write_material_destination_plan_to_database(
                        payload_transactions=expit_payload_transactions,
                        blend_report=partial_blend_report,
                        plan_type="optimised",
                        plan_id=plan_id,
                        crusher_destination=(
                            selected_aps_crusher
                            or (site_context or {}).get("crusher")
                        ),
                        direct_tip_movement_rules=(site_context or {}).get(
                            "direct_tip_movement_rules", []
                        ),
                    )
                self.case_bridge.print(
                    f"{plan_id} could not produce a compliant feasible "
                    f"plan: {message}"
                )
                plan_status_rows.append({
                    "plan_id": plan_id,
                    "plan_rank": plan_rank,
                    "status": "infeasible",
                    "message": message,
                    "reused_blend_fallbacks": (
                        contingency.contingency_reuse_fallbacks
                    ),
                })

        closing_targets = ClosingROMStocksCompliance.normalize_target_rows(
            (site_context or {}).get("two_wp_closing_stock_balances")
        )
        if not closing_targets.empty:
            for plan_id, modeller in self.case_modellers.items():
                blend_report = self._case_blend_report(modeller)
                build_report = modeller.balance_tracker.get_build_transactions()
                compliance = ClosingROMStocksCompliance.build_report(
                    plan_id=plan_id,
                    plan_type="optimised",
                    periods=periods.get_periods(),
                    physical_balance_history=(
                        modeller.balance_tracker.get_physical_balance_history()
                    ),
                    target_rows=closing_targets,
                    used_stockpiles=self._closing_compliance_stockpiles(
                        site_context, blend_report, build_report
                    ),
                )
                database_manager.write_closing_rom_stocks_compliance(
                    compliance, plan_id, "optimised"
                )

        database_manager.write_optimisation_plan_status(plan_status_rows)
        self.case_modeller = primary_case_modeller
        primary_result_row_count = len(primary_blend_report.index)
        if primary_result_row_count:
            self.case_bridge.print(
                "Database tables written successfully. "
                f"Primary report contains {primary_result_row_count} row(s); "
                f"{len(plan_status_rows) - 1} contingency plan(s) processed."
            )
        else:
            self.case_bridge.print(
                "Database tables were written, but the primary plan contains "
                "no successfully solved steady-state rows. Results and charts "
                "will therefore be empty."
            )

        periods.run_outcome = {
            "status": primary_status,
            "title": str(
                getattr(primary_outcome_error, "title", "")
                or "Optimisation Complete"
            ),
            "message": primary_message,
            "off_spec_builds": completed_offspec_builds,
            "partial_plan_restored": partial_plan_restored,
            "result_row_count": primary_result_row_count,
        }
        return periods

    def _include_aps_destination_stockpiles(
        self, stockpile_data, calendar_inputs, expit_payload_transactions
    ):
        """Add APS build destinations as build-only stockpile model objects."""
        if not isinstance(stockpile_data, dict) or expit_payload_transactions is None:
            return stockpile_data
        if expit_payload_transactions.empty or "destination" not in expit_payload_transactions:
            return stockpile_data

        destination_names = set()
        for value in expit_payload_transactions["destination"].tolist():
            if value is None or pd.isna(value):
                continue
            name = str(value).strip()
            if name.lower().startswith("stockpiles/"):
                name = name.split("/", 1)[1].strip()
            if name:
                destination_names.add(name)
        if not destination_names:
            return stockpile_data

        opening_data = getattr(self.gui, "stockpile_data", {}) or {}
        opening_by_name = {}
        if isinstance(opening_data, dict):
            for key, value in opening_data.items():
                if not isinstance(value, dict):
                    continue
                attrs = {str(attr).lower(): item for attr, item in value.items()}
                opening_name = str(attrs.get("name") or key).strip()
                if opening_name:
                    opening_by_name[opening_name.upper()] = (opening_name, attrs)

        def ensure_build_only_calendar_defaults(stockpile_name):
            """Ensure APS build destinations always have a complete calendar record."""
            calendar_name = str(stockpile_name).strip().lower()
            period_labels = PeriodManager.period_labels_for_count(
                calendar_inputs.get("planning_period_count", 3)
            )
            calendar_inputs.setdefault(
                f"stockpiles_{calendar_name}_state",
                {period: "Build" for period in period_labels},
            )
            calendar_inputs.setdefault(
                f"stockpiles_{calendar_name}_maximum_quantity",
                {period: 100000 for period in period_labels},
            )
            calendar_inputs.setdefault(
                f"stockpiles_{calendar_name}_cash",
                {period: 0 for period in period_labels},
            )

        existing_names = {str(key).strip().upper() for key in stockpile_data}
        added = []
        for destination_name in sorted(destination_names):
            if destination_name.upper() in existing_names:
                # The destination can already be present from an earlier run
                # or a restored project. It still needs its hidden build-only
                # calendar defaults before DataLoader creates its model object.
                ensure_build_only_calendar_defaults(destination_name)
                continue
            source = opening_by_name.get(destination_name.upper())
            if source is None:
                # Some APS destination stockpiles are not returned by the
                # opening-inventory query. They still need to exist as model
                # outputs, so initialise them at zero using the first payload
                # grades rather than stopping the run.
                matching_rows = expit_payload_transactions.loc[
                    expit_payload_transactions["destination"].astype("string").str.replace(
                        "Stockpiles/", "", regex=False
                    ).str.strip().str.upper() == destination_name.upper()
                ]
                first_row = matching_rows.iloc[0] if not matching_rows.empty else None
                opening_name = destination_name
                attrs = {
                    "name": opening_name.lower(),
                    "balance": 0.0,
                    "grade_fe": float(first_row.get("source_grade_fe", 0) or 0) if first_row is not None else 0.0,
                    "grade_si": float(first_row.get("source_grade_si", 0) or 0) if first_row is not None else 0.0,
                    "grade_al": float(first_row.get("source_grade_al", 0) or 0) if first_row is not None else 0.0,
                    "grade_p": float(first_row.get("source_grade_p", 0) or 0) if first_row is not None else 0.0,
                    "grade_mn": float(first_row.get("source_grade_mn", 0) or 0) if first_row is not None else 0.0,
                    "amt": False,
                    "reclaim_threshold": 0.0,
                }
            else:
                opening_name, attrs = source
            build_only = copy.deepcopy(attrs)
            # Selected inventory rows use a lowercase nested name for the
            # calendar key, while the outer dictionary key remains the model
            # stockpile identifier.
            build_only["name"] = opening_name.lower()
            build_only["amt"] = False
            build_only["reclaim_threshold"] = float(build_only.get("reclaim_threshold") or 0)
            stockpile_data[opening_name] = build_only
            existing_names.add(opening_name.upper())
            added.append(opening_name)

            ensure_build_only_calendar_defaults(opening_name)

        if added:
            self.case_bridge.print(
                "Automatically included APS destination stockpiles as build-only outputs: "
                + ", ".join(added)
            )
        return stockpile_data

    @staticmethod
    def _ensure_direct_tip_ids(expit_payload_transactions):
        if expit_payload_transactions is None or expit_payload_transactions.empty:
            return expit_payload_transactions
        expit_payload_transactions = expit_payload_transactions.copy()
        if "direct_tip_id" not in expit_payload_transactions.columns:
            expit_payload_transactions["direct_tip_id"] = [
                f"GB_{index + 1:06d}" for index in range(len(expit_payload_transactions))
            ]
        return expit_payload_transactions

    @staticmethod
    def _stockpile_selection_error(exception):
        message = str(exception)
        marker = "Stockpile '"
        if (
            marker not in message
            or "not selected or found in Stockpile Inventories" not in message
        ):
            return None

        stockpile_name = message.split(marker, 1)[1].split("'", 1)[0]
        return StockpileSelectionRunError(stockpile_name)

    @staticmethod
    def _normalize_stockpile_contribution_ratio(min_stockpile_contribution_ratio=None):
        if min_stockpile_contribution_ratio is None:
            return Optimizer.MIN_SELECTED_STOCKPILE_BLEND_RATIO

        min_stockpile_contribution_ratio = float(min_stockpile_contribution_ratio)
        if not 0.01 <= min_stockpile_contribution_ratio <= 1:
            raise ValueError("Min Stockpile Contribution Ratio must be between 0.01 and 1.")

        return min_stockpile_contribution_ratio

    def _validate_optimization_results(self):
        results = getattr(self.case_modeller, "results", None)
        diagnostics = self._first_optimization_diagnostic()
        if results is None or results.empty:
            raise InfeasibleRunError(
                self._format_infeasible_run_message(
                    "No feasible blend was produced.",
                    diagnostics,
                )
            )

        results = results.copy()
        results["source_actual_tonnes"] = pd.to_numeric(
            results.get("source_actual_tonnes"), errors="coerce"
        ).fillna(0)
        results["crusher_actual_tonnes"] = pd.to_numeric(
            results.get("crusher_actual_tonnes"), errors="coerce"
        ).fillna(0)

        failed_blend_labels = {"no blend selected", "no blend found", "no blend", "rare case"}
        if "blend_option" in results:
            failed_rows = results[
                results["blend_option"].astype(str).str.lower().isin(failed_blend_labels)
            ]
            if not failed_rows.empty:
                failed_row = failed_rows.iloc[0]
                steady_state = failed_row.get("steady_state_number", "unknown")
                diagnostics = self._first_optimization_diagnostic(steady_state) or diagnostics
                raise InfeasibleRunError(
                    self._format_infeasible_run_message(
                        f"Steady state {steady_state} did not produce a feasible blend.",
                        diagnostics,
                    )
                )

        valid_feed = results[results["crusher_actual_tonnes"] > Optimizer.SOLUTION_TOLERANCE]
        if valid_feed.empty:
            raise InfeasibleRunError(
                self._format_infeasible_run_message(
                    "The run completed without selecting any positive crusher feed.",
                    diagnostics,
                )
            )

    def _validate_stockpile_count_constraints(self, min_stockpiles=None, max_stockpiles=None, min_stockpile_contribution_ratio=None):
        if min_stockpiles is None and max_stockpiles is None:
            return

        min_stockpile_contribution_ratio = self._normalize_stockpile_contribution_ratio(
            min_stockpile_contribution_ratio
        )

        results = getattr(self.case_modeller, "results", None)
        if results is None or results.empty:
            raise InfeasibleRunError(self._stockpile_constraint_message(
                min_stockpiles,
                max_stockpiles,
                min_stockpile_contribution_ratio,
                "No feasible blend was produced.",
                self._first_optimization_diagnostic(),
            ))

        results = results.copy()
        results["source_actual_tonnes"] = pd.to_numeric(
            results.get("source_actual_tonnes"), errors="coerce"
        ).fillna(0)
        results["crusher_actual_tonnes"] = pd.to_numeric(
            results.get("crusher_actual_tonnes"), errors="coerce"
        ).fillna(0)

        if "blend_option" in results:
            failed_blend_labels = {"no blend selected", "no blend found", "no blend", "rare case"}
            has_failed_blend = (
                results["blend_option"]
                .astype(str)
                .str.lower()
                .isin(failed_blend_labels)
                .any()
            )
            if has_failed_blend:
                raise InfeasibleRunError(self._stockpile_constraint_message(
                    min_stockpiles,
                    max_stockpiles,
                    min_stockpile_contribution_ratio,
                    "At least one steady state has no feasible blend.",
                    self._first_optimization_diagnostic(),
                ))

        valid_feed = results[results["crusher_actual_tonnes"] > Optimizer.SOLUTION_TOLERANCE]
        if valid_feed.empty:
            raise InfeasibleRunError(self._stockpile_constraint_message(
                min_stockpiles,
                max_stockpiles,
                min_stockpile_contribution_ratio,
                "No crusher feed was selected.",
                self._first_optimization_diagnostic(),
            ))

        ratio_tolerance = Optimizer.SOLUTION_TOLERANCE
        grade_block_names = {
            grade_block.name
            for grade_block in getattr(self.case_modeller, "grade_blocks", [])
        }
        for (steady_state, blend_id), blend_rows in valid_feed.groupby(
            ["steady_state_number", "blend_ID"], dropna=False
        ):
            source_ratios = (
                blend_rows["source_actual_tonnes"]
                / blend_rows["crusher_actual_tonnes"].replace(0, pd.NA)
            ).fillna(0)
            source_identifier = (
                blend_rows["source_id"] if "source_id" in blend_rows.columns else blend_rows["source"]
            )
            stockpile_source_ratios = source_ratios[
                ~source_identifier.isin(grade_block_names)
            ]
            active_stockpile_count = int(
                (stockpile_source_ratios >= min_stockpile_contribution_ratio - ratio_tolerance).sum()
            )

            if min_stockpiles is not None and active_stockpile_count < min_stockpiles:
                raise InfeasibleRunError(self._stockpile_constraint_message(
                    min_stockpiles,
                    max_stockpiles,
                    min_stockpile_contribution_ratio,
                    f"Steady state {steady_state}, blend {blend_id} only has {active_stockpile_count} stockpile(s) contributing at least {self._format_stockpile_contribution_ratio(min_stockpile_contribution_ratio)}.",
                    self._first_optimization_diagnostic(steady_state),
                ))

            if max_stockpiles is not None and active_stockpile_count > max_stockpiles:
                raise InfeasibleRunError(self._stockpile_constraint_message(
                    min_stockpiles,
                    max_stockpiles,
                    min_stockpile_contribution_ratio,
                    f"Steady state {steady_state}, blend {blend_id} has {active_stockpile_count} stockpile(s) contributing at least {self._format_stockpile_contribution_ratio(min_stockpile_contribution_ratio)}.",
                    self._first_optimization_diagnostic(steady_state),
                ))

    @staticmethod
    def _format_stockpile_contribution_ratio(min_stockpile_contribution_ratio):
        return f"{min_stockpile_contribution_ratio:g} ({min_stockpile_contribution_ratio * 100:g}%)"

    def _stockpile_constraint_message(self, min_stockpiles, max_stockpiles, min_stockpile_contribution_ratio, detail, diagnostics=None):
        constraints = []
        if min_stockpiles is not None:
            constraints.append(f"minimum {min_stockpiles}")
        if max_stockpiles is not None:
            constraints.append(f"maximum {max_stockpiles}")
        constraint_text = ", ".join(constraints)
        stockpile_message = (
            f"BlendMaster could not satisfy the stockpile-count constraints ({constraint_text}) "
            f"with each selected stockpile contributing at least "
            f"{Run._format_stockpile_contribution_ratio(min_stockpile_contribution_ratio)} of crusher feed. "
            "Review Decision Levers, Solver Configuration and the Calendar "
            "inputs, then rerun."
        )
        return self._format_infeasible_run_message(detail, diagnostics, stockpile_message)

    def _first_optimization_diagnostic(self, steady_state=None):
        diagnostics = getattr(self.case_modeller, "optimization_diagnostics", []) or []
        if steady_state is not None:
            for diagnostic in diagnostics:
                if str(diagnostic.get("steady_state_number")) == str(steady_state):
                    return diagnostic
        return diagnostics[0] if diagnostics else None

    @staticmethod
    def _format_datetime(value):
        if hasattr(value, "strftime"):
            return value.strftime("%Y-%m-%d %H:%M")
        return str(value) if value not in (None, "") else "unknown"

    @staticmethod
    def _format_infeasible_run_message(detail, diagnostics=None, extra_detail=None):
        lines = [detail]

        if diagnostics:
            lines.extend([
                "",
                "Where:",
                f"- Steady state: {diagnostics.get('steady_state_number', 'unknown')}",
                f"- Period: {diagnostics.get('period', 'unknown')}",
                f"- Time window: {Run._format_datetime(diagnostics.get('start_datetime'))} to {Run._format_datetime(diagnostics.get('end_datetime'))}",
                f"- Solver status: {diagnostics.get('solver_status', 'unknown')}",
                f"- Available sources: {diagnostics.get('available_source_count', 0)} total, {diagnostics.get('positive_source_count', 0)} with positive reclaimable tonnes",
                f"- Positive stockpiles / grade blocks: {diagnostics.get('positive_stockpile_count', 0)} / {diagnostics.get('positive_grade_block_count', 0)}",
                f"- Crusher target tonnes in window: {float(diagnostics.get('target_tonnes') or 0):,.1f}",
                f"- Selected crusher tonnes: {float(diagnostics.get('selected_tonnes') or 0):,.1f}",
                f"- Direct tip: {'enabled' if diagnostics.get('direct_tip_enabled', True) else 'disabled'}",
                f"- Direct tip ratio target: {float(diagnostics.get('direct_feed_ratio_min') or 0):g} to {float(diagnostics.get('direct_feed_ratio_max') if diagnostics.get('direct_feed_ratio_max') is not None else 1):g}",
            ])

            likely_causes = diagnostics.get("likely_causes") or []
            if likely_causes:
                lines.extend(["", "Useful checks:"])
                lines.extend(f"- {cause}" for cause in likely_causes[:6])

            guardrail_rejections = diagnostics.get(
                "guardrail_rejections"
            ) or []
            if guardrail_rejections:
                lines.extend(["", "Post-solve guardrail rejections:"])
                lines.extend(
                    f"- {reason}" for reason in guardrail_rejections[:10]
                )

            grade_ranges = diagnostics.get("grade_ranges") or {}
            if grade_ranges:
                lines.extend(["", "Grade target vs available range:"])
                for grade_name, values in grade_ranges.items():
                    lines.append(
                        f"- {grade_name}: target {values['target_min']:g} to {values['target_max']:g}; "
                        f"available {values['available_min']:g} to {values['available_max']:g}"
                    )

            product_builds = diagnostics.get("product_build_targets") or []
            if product_builds:
                lines.extend(["", "Active product-build constraints:"])
                for build in product_builds:
                    lines.append(
                        f"- {build.get('name')} [{build.get('brand') or 'unbranded'}], "
                        f"opening {float(build.get('opening_tonnes') or 0):,.1f} t / "
                        f"target {float(build.get('target_tonnes') or 0):,.1f} t"
                    )
                    for grade_name, values in (
                        build.get("grade_targets") or {}
                    ).items():
                        current = values.get("current_grade")
                        current_text = (
                            f"; current {float(current):g}"
                            if current is not None else ""
                        )
                        available_min = values.get("available_min")
                        available_max = values.get("available_max")
                        available_text = (
                            f"; available {float(available_min):g} to "
                            f"{float(available_max):g}"
                            if available_min is not None
                            and available_max is not None else ""
                        )
                        lines.append(
                            f"  - {grade_name}: target "
                            f"{float(values.get('target_min') or 0):g} to "
                            f"{float(values.get('target_max') if values.get('target_max') is not None else 100):g}"
                            f"{current_text}{available_text}"
                        )

            custom_constraints = diagnostics.get("custom_constraint_ranges") or []
            if custom_constraints:
                lines.extend(["", "Active custom constraints:"])
                for custom in custom_constraints:
                    lines.append(
                        f"- {custom.get('name')}: target "
                        f"{custom.get('target_min')} to {custom.get('target_max')}; "
                        f"available coefficient range "
                        f"{custom.get('available_min')} to {custom.get('available_max')}"
                    )

        if extra_detail:
            lines.extend(["", extra_detail])

        lines.extend([
            "",
            "Suggested next checks: Solver Configuration, Calendar grade targets, stockpile State, Max Quantity, balances and reclaim rates.",
        ])
        return "\n".join(lines)
        
class CaseModellerBridge(QObject):
    output_signal = pyqtSignal(str)
    dataframe_signal = pyqtSignal(object)
    input_request_signal = pyqtSignal(str)
    input_response_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)  # Forwards errors to the GUI

    def __init__(self):
        super().__init__()
        self._input_response = None

    def print(self, message):
        if isinstance(message, pd.DataFrame):
            self.dataframe_signal.emit(message)
        else:
            self.output_signal.emit(str(message))

    def input(self, prompt):
        self.input_request_signal.emit(prompt)
        loop = QEventLoop()
        self.input_response_signal.connect(lambda text: loop.quit())
        loop.exec_()
        return self._input_response

    def send_input(self, user_input):
        self._input_response = user_input
        self.input_response_signal.emit(user_input)

    def handle_exception(self, exception):
        """Handle exceptions raised by the Case Modeller."""
        # Extract the traceback information
        tb_lines = traceback.format_exception(type(exception), exception, exception.__traceback__)
        error_message = "".join(tb_lines)  # Combine the traceback into a single string
        self.error_signal.emit(error_message)  # Emit the full traceback to the GUI
