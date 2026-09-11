# This is where the main workflow is defined (everything happens here)
from classes.BalanceTracker import BalanceTracker
from classes.EventPoolGenerator import EventPoolGenerator
from classes.EquipmentData import EquipmentData
from classes.StockpileData import StockpileData
from classes.GradeBlockData import GradeBlockData
from classes.Optimizer import Optimizer
from classes.ProductBuildProgress import ProductBuildProgress
from classes.ProductQualityLimits import QUALITY_FIELDS, quality_fields, with_quality_configuration, quality_label
from classes.ProductQualityReport import QUALITY_REPORT_SUFFIXES
from classes.ProductTargetModes import TARGET_MODE_FIELDS, target_mode_fields, require_supported_target_modes
from classes.ProductBuildLanes import (
    lane_kind,
    BYPRODUCT_LANES,
    PRODUCT_LANE,
    active_build_indices,
    lane_grade_column,
    lane_grade_weight_column,
    lane_source_tonnes_column,
    normalize_byproduct_grade_fields,
    normalize_byproduct_quantity_fields,
    normalized_build_lane,
)
from classes.CrusherTarget import CrusherTarget
from classes.GradeStreams import ANALYTES, STREAMS, grade_stream_audit_fields
from classes.GradeBlockIdentity import parent_grade_block_name
from classes.MaterialFlowTopology import model_sources, one_lane_topology, planning_topology
from classes.MultiFeedSettings import multi_feed_settings, period_lanes
from classes.MultiLaneOptimizer import MultiLaneOptimizer
from classes.CustomConstraints import (
    custom_constraint_property_keys,
    expand_required_property_keys,
    filter_source_properties,
    source_property_kind,
)
from database.SQLiteDatabase import DatabaseManager
from classes.PeriodManager import PeriodManager
import pandas as pd
import copy
from datetime import timedelta
from typing import Callable, List, Optional
from types import SimpleNamespace

class SolverRunAborted(Exception):
    def __init__(self, message="Optimisation run aborted by user."):
        super().__init__(message)
        self.user_message = message
        self.title = "Run Aborted"

class SteadyStateInfeasible(Exception):
    def __init__(self, steady_state, message="No feasible blend was found."):
        detail = (
            f"Optimisation stopped at steady state {steady_state}. {message} "
            "All earlier successfully solved steady states remain available."
        )
        super().__init__(detail)
        self.user_message = detail
        self.title = "Partial Plan - Infeasible Steady State"
        self.steady_state_number = steady_state

class ProductBuildCapacityComplete(Exception):
    def __init__(self):
        message = (
            "All configured product builds are complete, but the schedule still has time remaining. "
            "Create more product builds if the crusher should continue."
        )
        super().__init__(message)
        self.user_message = message
        self.title = "Product Builds Complete"

class ProductBuildRepairRequired(Exception):
    def __init__(self, build_index, candidate_steady_states, reason):
        super().__init__(reason)
        self.build_index = build_index
        self.candidate_steady_states = candidate_steady_states
        self.reason = reason

class ProductBuildRepairFailed(Exception):
    def __init__(self, build_name, reason):
        message = (
            f"No compliant repair plan was found for {build_name}. "
            f"{reason}"
        )
        super().__init__(message)
        self.user_message = message
        self.title = "No Compliant Product Build Plan"

class NoDistinctContingencyBlend(Exception):
    def __init__(self, plan_id, steady_state, acceptance_label):
        message = (
            f"{plan_id} has no feasible unused blend in steady state "
            f"{steady_state} under the contingency acceptance rule "
            f"'{acceptance_label}'."
        )
        super().__init__(message)
        self.user_message = message
        self.title = "No Distinct Contingency Blend"


class CaseModeller:
    MAX_DECISION_BLEND_OPTIONS = 12
    PRODUCT_BUILD_TONNES_TOLERANCE = 0.1
    REPAIR_CHECKPOINT_ATTRIBUTES = (
        "stockpiles", "grade_blocks", "equipment", "balance_tracker",
        "event_pool", "results", "build_report", "current_time",
        "period_tracker", "steady_state_tracker", "blend_option",
        "user_blend_choice", "blend_ID", "decision_point_results",
        "decision_point_results_to_display",
        "decision_point_results_to_display_filtered_to_current_blend_choice",
        "total_AMT_stockpile_balances", "product_build_runtime_states",
        "optimization_diagnostics", "previous_selected_stockpile_source_ids",
        "previous_selected_grade_block_pairs", "grade_block_pair_locks",
        "selected_blend_signatures", "contingency_reuse_fallbacks",
        "previous_chemical_blend_signature",
    )

    def __init__(
        self,
        stockpiles: List[StockpileData],
        grade_blocks: List[GradeBlockData],
        equipment: List[EquipmentData],
        crusher_targets,
        expit_payload_transactions,
        periods: PeriodManager,
        user_interaction_mode,
        hex_sequence_table,
        min_stockpiles: Optional[int] = None,
        max_stockpiles: Optional[int] = None,
        min_stockpile_contribution_ratio: Optional[float] = None,
        solver_config: Optional[dict] = None,
        product_build_settings: Optional[list] = None,
        abort_callback: Optional[Callable[[], bool]] = None,
        plan_id: str = "Primary",
        reserved_blend_signatures: Optional[set] = None,
        site_context: Optional[dict] = None,
    ):
        require_supported_target_modes(product_build_settings)
        self.stockpiles = stockpiles
        self.grade_blocks = grade_blocks
        self.equipment = equipment
        self.crusher_targets = crusher_targets
        self.site_context = dict(site_context or {})
        self.periods = periods
        self.current_time = periods.get_periods()["preplan_start"]
        self.start_time = periods.get_periods()["preplan_start"]
        self.period_tracker = "preplan"
        self.solver_config = solver_config or {}
        self.byproducts_enabled = bool(
            self.solver_config.get("byproducts_enabled", False)
        )
        constraint_definitions = list(
            self.solver_config.get("custom_constraints") or []
        )
        for target in (crusher_targets or {}).values():
            if isinstance(target, dict):
                constraint_definitions.extend(
                    target.get("custom_constraints") or []
                )
        required_source_property_keys = custom_constraint_property_keys(
            constraint_definitions
        )
        required_source_property_keys.update(
            self.solver_config.get("optimisation_source_property_fields") or []
        )
        selected_stream = str(
            self.solver_config.get("selected_data_stream") or ""
        ).strip().lower()
        required_source_property_keys.update(
            str(self.solver_config.get(key) or default)
            for key, default in {
                "crusher_tonnes_stream": "modelled_rom_wmt",
                "reclaimer_tonnes_stream": "modelled_rom_wmt",
                "product_build_tonnes_stream": "modelled_product_wmt",
            }.items()
        )
        if self.byproducts_enabled:
            quantities = normalize_byproduct_quantity_fields(
                self.solver_config.get("byproduct_quantity_fields")
            )
            grades = normalize_byproduct_grade_fields(
                self.solver_config.get("byproduct_grade_fields")
            )
            required_source_property_keys.update(quantities.values())
            required_source_property_keys.update(
                field for lane_fields in grades.values()
                for field in lane_fields.values()
            )
        if selected_stream in STREAMS:
            required_source_property_keys.update(
                f"{selected_stream}_{analyte}" for analyte in ANALYTES
            )
        source_property_kinds = dict(
            self.solver_config.get("source_property_kinds") or {}
        )
        source_property_weights = dict(
            self.solver_config.get("source_property_weights") or {}
        )
        required_source_property_keys = expand_required_property_keys(
            required_source_property_keys, source_property_weights
        )
        for source in [*stockpiles, *grade_blocks]:
            source.source_property_kinds = source_property_kinds
            source.source_property_weights = source_property_weights
            source.source_properties = filter_source_properties(
                getattr(source, "source_properties", None),
                required_source_property_keys,
            )
        self.balance_tracker = BalanceTracker(
            stockpiles,
            grade_blocks,
            self.period_tracker,
            hex_sequence_table,
            required_source_property_keys=required_source_property_keys,
            source_property_kinds=source_property_kinds,
            source_property_weights=source_property_weights,
        )
        self.expit_payload_transactions = expit_payload_transactions
        if (
            isinstance(expit_payload_transactions, pd.DataFrame)
            and "source_properties" in expit_payload_transactions.columns
        ):
            self.expit_payload_transactions = (
                expit_payload_transactions.copy(deep=False)
            )
            self.expit_payload_transactions["source_properties"] = (
                expit_payload_transactions["source_properties"].map(
                    lambda properties: filter_source_properties(
                        properties, required_source_property_keys
                    )
                )
            )
        self.event_pool = EventPoolGenerator(stockpiles, grade_blocks, equipment)
        self.optimizer = Optimizer()
        self.multi_feed_configuration = multi_feed_settings(self.solver_config.get("multi_feed_settings"))
        if self.multi_feed_configuration["mode"] != "single":
            self.crusher_targets = copy.deepcopy(self.crusher_targets)
            for period, target in self.crusher_targets.items():
                points = period_lanes(self.multi_feed_configuration, period, target)
                for point, configured in zip(points, self.multi_feed_configuration["tipping_points"]):
                    configured["targets_by_period"][period] = copy.deepcopy(point["target"])
                target["crusher_rate"] = sum(p["target"]["crusher_rate"] for p in points)
                target["direct_feed_ratio_min"], target["direct_feed_ratio_max"] = 0.0, 1.0
                for analyte in ("fe", "si", "al", "p", "mn"):
                    target[f"target_{analyte}_min"], target[f"target_{analyte}_max"] = 0.0, 100.0
            self.optimizer = MultiLaneOptimizer(self.multi_feed_configuration)
        self.results = pd.DataFrame()
        self.build_report = pd.DataFrame()
        self.steady_state_tracker = 0
        self.blend_option = 1
        self.user_blend_choice = None
        self.blend_ID = 1
        self.decision_point_results = pd.DataFrame()
        self.decision_point_results_to_display = pd.DataFrame()
        self.decision_point_results_to_display_filtered_to_current_blend_choice = pd.DataFrame()
        self.user_interaction_mode = user_interaction_mode
        self.database_manager = DatabaseManager()
        self.total_AMT_stockpile_balances = self.balance_tracker.return_total_AMT_stockpile_balances()
        self.min_stockpiles = min_stockpiles
        self.max_stockpiles = max_stockpiles
        self.min_stockpile_contribution_ratio = min_stockpile_contribution_ratio
        self.plan_id = str(plan_id or "Primary")
        self.reserved_blend_signatures = {
            frozenset(signature)
            for signature in (reserved_blend_signatures or set())
        }
        self.selected_blend_signatures = set()
        self.contingency_reuse_fallbacks = 0
        # Blend ID identifies one contiguous chemical blend, rather than a
        # solver steady state.  It changes only when the selected material
        # composition changes.
        self.previous_chemical_blend_signature = None
        from classes.MultiFeedSettings import scoped_builds
        self.product_build_settings = self.normalized_product_build_settings(scoped_builds(product_build_settings, self.multi_feed_configuration))
        if self.byproducts_enabled and self.product_build_settings:
            configured_lanes = {
                setting.get("byproduct") for setting in self.product_build_settings
            }
            missing = [lane for lane in BYPRODUCT_LANES if lane not in configured_lanes]
            if missing:
                raise ValueError(
                    "By-products are enabled but Product Targets have no "
                    f"{', '.join(lane.title() for lane in missing)} build."
                )
        self.product_build_runtime_states = [
            {
                "tonnes": 0.0,
                "grade_fe_metal": 0.0,
                "grade_fe_weight": 0.0,
                "grade_si_metal": 0.0,
                "grade_si_weight": 0.0,
                "grade_al_metal": 0.0,
                "grade_al_weight": 0.0,
                "grade_p_metal": 0.0,
                "grade_p_weight": 0.0,
                "grade_mn_metal": 0.0,
                "grade_mn_weight": 0.0,
            }
            for _ in self.product_build_settings
        ]
        self.product_build_repair_from_states = {}
        self.product_build_hard_repair_from_states = {}
        self.optimization_diagnostics = []
        self.previous_selected_stockpile_source_ids = set()
        self.previous_selected_grade_block_pairs = {}
        self.grade_block_pair_locks = {}
        self.abort_callback = abort_callback or (lambda: False)
        self.abort_requested = False

    @property
    def material_flow_topology(self):
        """Return a detached topology snapshot without altering planning state."""
        return planning_topology(
            multi_feed=getattr(self, "multi_feed_configuration", None),
            site_context=getattr(self, "site_context", None),
            sources=model_sources(self.stockpiles, self.grade_blocks),
            crusher_targets=self.crusher_targets,
            product_build_settings=self.product_build_settings,
            byproducts_enabled=self.byproducts_enabled,
        )

    def normalized_product_build_settings(self, product_build_settings):
        normalized = []
        configured_brands = [
            str(brand).strip().upper()
            for brand in (self.solver_config.get("configured_product_brands", []) or [])
            if str(brand).strip()
        ]
        for index, setting in enumerate(product_build_settings or []):
            if not isinstance(setting, dict):
                continue
            try:
                target_tonnes = float(setting.get("target_tonnes") or 0)
            except (TypeError, ValueError):
                target_tonnes = 0
            if target_tonnes <= 0:
                continue
            build_name = str(setting.get("build_name") or f"Build {index + 1}")
            explicit_brand = str(setting.get("brand") or "").strip().upper()
            if not explicit_brand:
                compact_build_name = "".join(character for character in build_name.upper() if character.isalnum())
                explicit_brand = next(
                    (
                        brand for brand in sorted(configured_brands, key=len, reverse=True)
                        if "".join(character for character in brand if character.isalnum()) in compact_build_name
                    ),
                    "",
                )
            normalized.append(with_quality_configuration({
                **{key: copy.deepcopy(value) for key, value in setting.items()
                   if key in {"opf", "opf_scope", "contributing_opfs", "crusher", "cbfl_campaign", "crusher_contribution_ratio"} or key.startswith("planning_")},
                **quality_fields(setting),
                **target_mode_fields(setting),
                "build_id": int(setting.get("build_id") or index + 1),
                "build_name": build_name,
                "brand": explicit_brand,
                "byproduct": (
                    str(setting.get("byproduct") or "").strip().lower()
                    if self.byproducts_enabled else ""
                ),
                "target_tonnes": target_tonnes,
                "target_fe_min": float(setting.get("target_fe_min", 0) or 0),
                "target_fe_max": float(setting.get("target_fe_max", 100) or 100),
                "target_si_min": float(setting.get("target_si_min", 0) or 0),
                "target_si_max": float(setting.get("target_si_max", 100) or 100),
                "target_al_min": float(setting.get("target_al_min", 0) or 0),
                "target_al_max": float(setting.get("target_al_max", 100) or 100),
                "target_p_min": float(setting.get("target_p_min", 0) or 0),
                "target_p_max": float(setting.get("target_p_max", 100) or 100),
                "target_mn_min": float(setting.get("target_mn_min", 0) or 0),
                "target_mn_max": float(setting.get("target_mn_max", 100) or 100),
            }))
        return normalized

    def current_product_build_indices(self):
        return active_build_indices(
            self.product_build_settings,
            self.product_build_runtime_states,
            bool(getattr(self, "byproducts_enabled", False)),
        )

    def current_product_build_index(self, lane=None):
        active = self.current_product_build_indices()
        if lane is not None:
            return active.get(str(lane).strip().lower())
        if PRODUCT_LANE in active:
            return active[PRODUCT_LANE]
        return next(iter(active.values()), None)

    def current_product_build_setting(self):
        index = self.current_product_build_index()
        if index is None:
            return None
        return self.product_build_settings[index]

    @staticmethod
    def normalize_two_wp_stockpile_name(value):
        name = str(value or "").strip()
        if name.upper().startswith("STOCKPILES/"):
            name = name.split("/", 1)[1].strip()
        return name.upper()

    def two_wp_active_blend_report_fields(self, result=None):
        """Return the active 2WP stockpile set for the current state."""
        empty_fields = {
            "two_wp_active_blend": "",
            "two_wp_active_blend_product_brand": "",
            "two_wp_active_blend_start_datetime": "",
            "two_wp_active_blend_end_datetime": "",
            "two_wp_active_blend_exact_match": None,
        }
        current_time = pd.to_datetime(
            getattr(self, "current_time", None), errors="coerce"
        )
        if pd.isna(current_time):
            return empty_fields

        current_build = self.current_product_build_setting()
        product_brand = str(
            (current_build or {}).get("brand") or ""
        ).strip()
        normalized_brand = product_brand.upper()
        active_windows = []
        for window in (
            (getattr(self, "solver_config", {}) or {}).get(
                "active_blend_guidance", []
            )
            or []
        ):
            start = pd.to_datetime(
                window.get("start_datetime"), errors="coerce"
            )
            end = pd.to_datetime(
                window.get("end_datetime"), errors="coerce"
            )
            window_brand = str(
                window.get("product_brand") or ""
            ).strip()
            if (
                pd.isna(start)
                or pd.isna(end)
                or not (start <= current_time < end)
                or (
                    normalized_brand
                    and window_brand.upper() != normalized_brand
                )
            ):
                continue
            active_windows.append((window, start, end, window_brand))

        if not active_windows:
            empty_fields["two_wp_active_blend_product_brand"] = (
                product_brand
            )
            return empty_fields
        if not normalized_brand and len(active_windows) != 1:
            return empty_fields

        window, start, end, window_brand = active_windows[0]
        stockpiles = sorted({
            str(stockpile).strip()
            for stockpile in window.get("stockpiles", [])
            if str(stockpile).strip()
        })
        expected = {
            self.normalize_two_wp_stockpile_name(stockpile)
            for stockpile in stockpiles
        }
        exact_match = None
        solution = (result or {}).get("Linprog_result_object")
        if expected and getattr(solution, "success", False):
            selected = {
                self.normalize_two_wp_stockpile_name(
                    transaction.get("parent_stockpile")
                    or transaction.get("source")
                    or transaction.get("source_id")
                )
                for transaction in (result or {}).get(
                    "transactions", []
                )
                if (
                    transaction.get("source_type") == "stockpile"
                    and float(transaction.get("actual_tonnes") or 0)
                    > Optimizer.SOLUTION_TOLERANCE
                )
            }
            exact_match = int(selected == expected)

        return {
            "two_wp_active_blend": " + ".join(stockpiles),
            "two_wp_active_blend_product_brand": (
                window_brand or product_brand
            ),
            "two_wp_active_blend_start_datetime": (
                start.strftime("%Y-%m-%d %H:%M:%S")
            ),
            "two_wp_active_blend_end_datetime": (
                end.strftime("%Y-%m-%d %H:%M:%S")
            ),
            "two_wp_active_blend_exact_match": exact_match,
        }

    def update_product_build_runtime_state(self, selected_results):
        if not self.product_build_settings or selected_results is None or selected_results.empty:
            return [] if self.byproducts_enabled else None

        completed = []
        for lane, build_index in self.current_product_build_indices().items():
            tonnes_column = lane_source_tonnes_column(lane)
            data = selected_results.copy()
            if tonnes_column not in data:
                if self.solver_config.get("strict_mapped_fields", False):
                    raise ValueError(
                        f"Optimiser result is missing {tonnes_column}; the "
                        f"{lane} product build cannot fall back to physical ROM tonnes."
                    )
                data[tonnes_column] = data.get("source_actual_tonnes", 0)
            data[tonnes_column] = pd.to_numeric(
                data[tonnes_column], errors="coerce"
            ).fillna(0)
            data = data[data[tonnes_column] > Optimizer.SOLUTION_TOLERANCE]
            if data.empty:
                continue

            product_tonnes = float(data[tonnes_column].sum() or 0)
            build_setting = self.product_build_settings[build_index]
            build_state = self.product_build_runtime_states[build_index]
            capacity = build_setting["target_tonnes"] - build_state["tonnes"]
            if capacity <= self.PRODUCT_BUILD_TONNES_TOLERANCE:
                completed.append(build_index)
                continue

            allocation_tonnes = min(product_tonnes, capacity)
            allocation_fraction = (
                allocation_tonnes / product_tonnes if product_tonnes else 0
            )
            for _, row in data.iterrows():
                for grade in ["fe", "si", "al", "p", "mn"]:
                    grade_value = float(
                        row.get(lane_grade_column(lane, grade)) or 0
                    )
                    raw_weight = row.get(lane_grade_weight_column(lane, grade))
                    grade_weight = float(
                        (row.get(tonnes_column) or 0) if raw_weight is None or pd.isna(raw_weight) else raw_weight
                    ) * allocation_fraction
                    build_state[f"grade_{grade}_metal"] += (
                        grade_weight * grade_value
                    )
                    build_state[f"grade_{grade}_weight"] = float(
                        build_state.get(
                            f"grade_{grade}_weight", build_state.get("tonnes", 0)
                        )
                    ) + grade_weight
            build_state["tonnes"] += allocation_tonnes
            if (
                build_state["tonnes"]
                >= build_setting["target_tonnes"]
                - self.PRODUCT_BUILD_TONNES_TOLERANCE
            ):
                completed.append(build_index)
        if bool(getattr(self, "byproducts_enabled", False)) or getattr(self, "multi_feed_configuration", {}).get("mode") == "combined_opf":
            return completed
        return completed[0] if completed else None

    def product_build_repair_enabled(self):
        return bool(
            self.solver_config.get("enable_product_build_repair_loop", False)
            and self.solver_config.get(
                "allow_offspec_steady_states_for_product_build", False
            )
        )

    def capture_product_build_repair_checkpoint(self):
        state = {
            attribute: getattr(self, attribute)
            for attribute in self.REPAIR_CHECKPOINT_ATTRIBUTES
        }
        return copy.deepcopy(state)

    def restore_product_build_repair_checkpoint(self, checkpoint):
        for attribute, value in checkpoint.items():
            setattr(self, attribute, value)

    def restore_last_solved_checkpoint_for_reporting(self, checkpoint):
        """Restore a consistent partial plan after repair attempts fail.

        Product-build repair deliberately rewinds the modeller.  If every
        repair path fails, leaving the modeller at that earlier checkpoint
        discards steady states that were successfully completed before the
        first repair was requested.  The caller supplies the furthest
        internally consistent checkpoint captured at the *start* of a later
        steady state, so the offending/off-spec transaction itself is not
        included.
        """
        if not checkpoint:
            return False
        results = checkpoint.get("results")
        if not isinstance(results, pd.DataFrame) or results.empty:
            return False
        self.restore_product_build_repair_checkpoint(checkpoint)
        self.partial_plan_restored_after_repair = True
        print(
            "Product-build repair was unsuccessful. Restored the last "
            f"successfully solved partial plan through steady state "
            f"{max(int(self.steady_state_tracker) - 1, 0)} for reporting."
        )
        return True

    def product_build_offspec_steady_states(self, build_index):
        candidate_states = set()
        if self.results is not None and not self.results.empty:
            report = ProductBuildProgress.annotate(
                self.results,
                self.product_build_settings,
                solver_config=getattr(self, "solver_config", {}),
            )
            required_columns = {
                "product_build_id",
                "product_build_current_on_spec",
                "steady_state_number",
            }
            if not report.empty and required_columns.issubset(report.columns):
                build_id = self.product_build_settings[build_index]["build_id"]
                build_rows = report[
                    report["product_build_id"] == build_id
                ].copy()
                if not build_rows.empty:
                    on_spec = (
                        build_rows["product_build_current_on_spec"]
                        .fillna(False)
                        .astype(bool)
                    )
                    state_numbers = pd.to_numeric(
                        build_rows.loc[~on_spec, "steady_state_number"],
                        errors="coerce",
                    ).dropna()
                    candidate_states.update(
                        int(value) for value in state_numbers
                    )

        # Final compliance is validated from the independent runtime
        # accumulator, not from ProductBuildProgress annotations. If that
        # accumulator says the build is currently off spec, the checkpoint at
        # the start of this state is necessarily a valid repair point even if
        # report annotation failed to rediscover the visible off-spec state.
        if 0 <= build_index < len(self.product_build_runtime_states):
            build_state = self.product_build_runtime_states[build_index]
            build_setting = self.product_build_settings[build_index]
            if (
                build_state["tonnes"] > Optimizer.SOLUTION_TOLERANCE
                and not self.product_build_grade_on_spec(
                    build_state, build_setting
                )
            ):
                candidate_states.add(int(self.steady_state_tracker))

        return sorted(candidate_states, reverse=True)

    def request_product_build_repair(self, build_index, reason):
        if not self.product_build_repair_enabled():
            return
        if target_mode_fields(self.product_build_settings[build_index])["target_mode"] == "soft":
            return
        candidate_states = self.product_build_offspec_steady_states(build_index)
        build_name = self.product_build_settings[build_index]["build_name"]
        if not candidate_states:
            raise ProductBuildRepairFailed(
                build_name,
                f"{reason} No saved repair checkpoint could be associated "
                "with an off-spec accumulated build state.",
            )
        raise ProductBuildRepairRequired(
            build_index,
            candidate_states,
            reason,
        )

    def product_build_repair_checkpoint_states(
        self,
        repair_checkpoints,
        build_index,
        before_state=None,
    ):
        """Return earlier checkpoints where the requested build is active.

        A terminal build may need grade buffer created in a state that was
        cumulatively on spec. Restricting retries to already-off-spec states
        prevents that recovery, so the bounded fallback can progressively
        expand through all checkpoints for the same active build.
        """
        eligible = []
        for state_number, checkpoint in repair_checkpoints.items():
            if before_state is not None and state_number >= before_state:
                continue
            runtime_states = checkpoint.get(
                "product_build_runtime_states", []
            )
            if build_index >= len(runtime_states):
                continue

            requested_lane = normalized_build_lane(self.product_build_settings[build_index], bool(getattr(self, 'byproducts_enabled', False)))
            prior_builds_complete = all(
                runtime_states[index]["tonnes"]
                >= self.product_build_settings[index]["target_tonnes"]
                - self.PRODUCT_BUILD_TONNES_TOLERANCE
                for index in range(build_index)
                if (
                    normalized_build_lane(self.product_build_settings[index], bool(getattr(self, 'byproducts_enabled', False))) == requested_lane
                )
            )
            requested_build_incomplete = (
                runtime_states[build_index]["tonnes"]
                < self.product_build_settings[build_index]["target_tonnes"]
                - self.PRODUCT_BUILD_TONNES_TOLERANCE
            )
            if prior_builds_complete and requested_build_incomplete:
                eligible.append(int(state_number))
        return sorted(set(eligible), reverse=True)

    def request_repair_for_terminal_infeasibility(
        self,
        last_solver_result,
        period_crusher_target,
        steady_state_duration,
    ):
        if not self.product_build_repair_enabled():
            return
        if (
            last_solver_result is not None
            and last_solver_result.get("Linprog_result_object") is not None
            and last_solver_result["Linprog_result_object"].success
        ):
            return

        for lane, build_index in self.current_product_build_indices().items():
            build_state = self.product_build_runtime_states[build_index]
            build_setting = self.product_build_settings[build_index]
            repair_from_state = self.product_build_repair_from_states.get(build_index)
            repair_constraint_active = (
                repair_from_state is not None
                and self.steady_state_tracker >= repair_from_state
            )
            terminal_constraint_active = (
                build_state["tonnes"] > Optimizer.SOLUTION_TOLERANCE
                and not self.product_build_grade_on_spec(build_state, build_setting)
                and Optimizer.product_build_can_complete_in_steady_state(
                    build_setting["target_tonnes"],
                    build_state["tonnes"],
                    self.product_build_capacity_rate(
                        period_crusher_target.get("crusher_rate", 0.0),
                        lane=lane,
                    ),
                    steady_state_duration,
                )
            )
            if repair_constraint_active or terminal_constraint_active:
                self.request_product_build_repair(
                    build_index,
                    "A cumulative build-grade repair constraint was infeasible.",
                )

    def validate_completed_product_build(self, build_index):
        if build_index is None or not self.product_build_repair_enabled():
            return
        if isinstance(build_index, (list, tuple, set)):
            for index in build_index:
                self.validate_completed_product_build(index)
            return
        build_state = self.product_build_runtime_states[build_index]
        build_setting = self.product_build_settings[build_index]
        if not self.product_build_grade_on_spec(build_state, build_setting):
            self.request_product_build_repair(
                build_index,
                "The independently accumulated completed build was off spec.",
            )

    def request_abort(self):
        self.abort_requested = True

    def check_abort_requested(self):
        if self.abort_requested or self.abort_callback():
            raise SolverRunAborted()

    def configured_period_keys(self):
        if hasattr(self.periods, "period_keys"):
            return list(self.periods.period_keys())
        period_data = self.periods.get_periods()
        keys = []
        index = 0
        while True:
            key = "preplan" if index == 0 else f"period_{index}"
            if f"{key}_start" not in period_data:
                break
            keys.append(key)
            index += 1
        if not keys:
            keys = [
                key[:-4]
                for key in period_data
                if key.endswith("_end")
            ]
            keys.sort(
                key=lambda key: (
                    0 if key == "preplan" else 1,
                    int(key.rsplit("_", 1)[-1])
                    if key.rsplit("_", 1)[-1].isdigit()
                    else 0,
                )
            )
        return keys

    def planning_horizon_end(self):
        if hasattr(self.periods, "horizon_end"):
            return self.periods.horizon_end()
        period_data = self.periods.get_periods()
        return period_data[f"{self.configured_period_keys()[-1]}_end"]

    def period_for_time(self, value):
        if hasattr(self.periods, "period_for_datetime"):
            return self.periods.period_for_datetime(value)
        period_data = self.periods.get_periods()
        for period_key in self.configured_period_keys():
            period_start = period_data.get(f"{period_key}_start")
            period_end = period_data.get(f"{period_key}_end")
            if (
                period_start is not None
                and period_end is not None
                and period_start <= value < period_end
            ):
                return period_key
        return None

    def run(self):
        """Runs the modeling process, coordinating optimization and time tracking."""
        if not hasattr(self, "plan_id"):
            self.plan_id = "Primary"
        if not hasattr(self, "reserved_blend_signatures"):
            self.reserved_blend_signatures = set()
        if not hasattr(self, "selected_blend_signatures"):
            self.selected_blend_signatures = set()
        if not hasattr(self, "contingency_reuse_fallbacks"):
            self.contingency_reuse_fallbacks = 0
        if not hasattr(self, "previous_chemical_blend_signature"):
            self.previous_chemical_blend_signature = None
        if not hasattr(self, "product_build_hard_repair_from_states"):
            self.product_build_hard_repair_from_states = {}
        self.partial_plan_restored_after_repair = False
        print(
            "Active solver configuration: "
            f"Min Grade Block Pair Duration = "
            f"{float(self.solver_config.get('min_grade_block_pair_duration_hours') or 0):.2f} hrs; "
            f"Min Stockpile Feed Duration = "
            f"{float(self.solver_config.get('min_feed_duration_hours') or 0):.2f} hrs; "
            f"Blend Option Timeout = "
            f"{float(self.solver_config.get('blend_option_timeout_seconds') or 0):.0f} sec; "
            f"Max Blend Options per Steady State = "
            f"{self.configured_max_decision_blend_options()}."
        )
        repair_checkpoints = {}
        repair_attempts = 0
        best_reporting_checkpoint = None
        best_reporting_state = -1
        best_reporting_progress = None
        while self.current_time < self.planning_horizon_end():
            self.check_abort_requested()
            if self.product_build_settings and self.current_product_build_index() is None:
                raise ProductBuildCapacityComplete()
            if self.product_build_repair_enabled():
                current_checkpoint = self.capture_product_build_repair_checkpoint()
                repair_checkpoints[self.steady_state_tracker] = current_checkpoint
                checkpoint_results = current_checkpoint.get("results")
                if (
                    isinstance(checkpoint_results, pd.DataFrame)
                    and not checkpoint_results.empty
                ):
                    # Keep this independently from repair_checkpoints because
                    # the bounded repair search intentionally removes later
                    # checkpoints as it rewinds farther into the schedule.
                    # Compare schedule time, not state count: repaired states
                    # can have different durations, so more state numbers do
                    # not necessarily mean a later valid plan boundary.
                    checkpoint_progress = current_checkpoint.get("current_time")
                    try:
                        farther_than_saved = (
                            best_reporting_progress is None
                            or checkpoint_progress > best_reporting_progress
                        )
                    except TypeError:
                        farther_than_saved = (
                            int(self.steady_state_tracker)
                            > best_reporting_state
                        )
                    if farther_than_saved:
                        best_reporting_checkpoint = copy.deepcopy(
                            current_checkpoint
                        )
                        best_reporting_state = int(self.steady_state_tracker)
                        best_reporting_progress = copy.deepcopy(
                            checkpoint_progress
                        )
            # Run optimization and only advance time if successful
            try:
                self.run_optimization_step()
            except ProductBuildRepairFailed:
                self.restore_last_solved_checkpoint_for_reporting(
                    best_reporting_checkpoint
                )
                raise
            except SteadyStateInfeasible as infeasible:
                # A repair can become ordinarily infeasible after it has
                # rewound and changed the source/balance history. That path
                # previously bypassed the repair-failure handler and silently
                # replaced a later pre-repair prefix with a shorter schedule.
                # Retain whichever internally consistent checkpoint reaches
                # farther through calendar time.
                restored = False
                if repair_attempts and best_reporting_checkpoint:
                    saved_progress = best_reporting_checkpoint.get(
                        "current_time"
                    )
                    try:
                        repair_ended_earlier = (
                            saved_progress is not None
                            and self.current_time < saved_progress
                        )
                    except TypeError:
                        repair_ended_earlier = (
                            int(self.steady_state_tracker)
                            < best_reporting_state
                        )
                    if repair_ended_earlier:
                        restored = self.restore_last_solved_checkpoint_for_reporting(
                            best_reporting_checkpoint
                        )
                if restored:
                    raise SteadyStateInfeasible(
                        self.steady_state_tracker,
                        (
                            "No feasible repaired blend extended as far as "
                            "the original solved prefix. The furthest "
                            "internally consistent pre-repair checkpoint "
                            "was restored."
                        ),
                    ) from infeasible
                raise
            except ProductBuildRepairRequired as repair:
                current_repair_state = self.product_build_repair_from_states.get(
                    repair.build_index
                )
                candidate_states = [
                    state
                    for state in repair.candidate_steady_states
                    if state in repair_checkpoints
                    and (
                        current_repair_state is None
                        or state < current_repair_state
                    )
                ]
                expanded_search = False
                if not candidate_states:
                    candidate_states = (
                        self.product_build_repair_checkpoint_states(
                            repair_checkpoints,
                            repair.build_index,
                            before_state=current_repair_state,
                        )
                    )
                    expanded_search = bool(candidate_states)
                build_name = self.product_build_settings[
                    repair.build_index
                ]["build_name"]
                if not candidate_states:
                    hard_repair_states = (
                        self.product_build_repair_checkpoint_states(
                            repair_checkpoints,
                            repair.build_index,
                        )
                    )
                    hard_repair_already_active = (
                        repair.build_index
                        in self.product_build_hard_repair_from_states
                    )
                    if hard_repair_states and not hard_repair_already_active:
                        repair_state = min(hard_repair_states)
                        checkpoint = repair_checkpoints[repair_state]
                        self.restore_product_build_repair_checkpoint(
                            checkpoint
                        )
                        self.product_build_repair_from_states[
                            repair.build_index
                        ] = repair_state
                        self.product_build_hard_repair_from_states[
                            repair.build_index
                        ] = repair_state
                        repair_checkpoints = {
                            state: saved_checkpoint
                            for state, saved_checkpoint
                            in repair_checkpoints.items()
                            if state < repair_state
                        }
                        repair_attempts += 1
                        print(
                            f"Cumulative repair did not produce a compliant "
                            f"{build_name}. Restarting that build from steady "
                            f"state {repair_state} with every build state "
                            "forced on spec."
                        )
                        continue
                    failure = ProductBuildRepairFailed(
                        build_name,
                        (
                            f"{repair.reason} Cumulative repair and the final "
                            "all-states-on-spec fallback both failed across "
                            "the saved checkpoints for this build."
                        ),
                    )
                    self.restore_last_solved_checkpoint_for_reporting(
                        best_reporting_checkpoint
                    )
                    raise failure

                repair_state = max(candidate_states)
                if expanded_search:
                    print(
                        f"Expanding {build_name} repair into earlier "
                        f"checkpoint steady state {repair_state} to create "
                        "additional cumulative grade buffer."
                    )
                checkpoint = repair_checkpoints[repair_state]
                self.restore_product_build_repair_checkpoint(checkpoint)
                self.product_build_repair_from_states[
                    repair.build_index
                ] = repair_state
                repair_checkpoints = {
                    state: saved_checkpoint
                    for state, saved_checkpoint in repair_checkpoints.items()
                    if state < repair_state
                }
                repair_attempts += 1
                print(
                    f"Repairing {build_name} from steady state {repair_state} "
                    f"(attempt {repair_attempts})."
                )
                continue
            self.steady_state_tracker += 1
            self.check_abort_requested()

    def run_optimization_step(self):
        """Run a single optimization step for the initial steady state duration."""
        self.check_abort_requested()
        initial_steady_state_duration = self.calculate_initial_steady_state_duration()
        steady_state_end = self.current_time + timedelta(hours=initial_steady_state_duration)
        events = self.event_pool.get_events(
            self.period_tracker,
            pd.DataFrame(),
            self.current_time,
            steady_state_end,
            self.balance_tracker,
        )
        
        # This method will be ultimately redundant as stockpile balances are updated in the is_stockpile_ready method of EventPoolGenerator and the same can be done for grade blocks (at which point this method is no longer required)
        self.event_pool.update_event_balances(events, self.balance_tracker)
        
        period_crusher_target = copy.deepcopy(
            CrusherTarget(self.crusher_targets).get_targets(
                self.period_tracker
            )
        )
        candidate_source_sets = []
        excluded_source_sets = []
        excluded_stockpile_sets = []
        candidate_source_signatures = set()
        configured_min_feed_duration = self.configured_min_feed_duration_hours()
        blend_option_timeout_seconds = self.configured_blend_option_timeout_seconds()
        max_decision_blend_options = self.configured_max_decision_blend_options()
        step_solver_config = self.solver_config_for_current_step()
        events, unavailable_grade_block_windows = (
            self.filter_grade_block_events_by_pair_duration(
                events, initial_steady_state_duration
            )
        )
        if unavailable_grade_block_windows:
            preview = "; ".join(unavailable_grade_block_windows[:8])
            remainder = len(unavailable_grade_block_windows) - 8
            if remainder > 0:
                preview += f"; and {remainder} more"
            print(
                "Excluded direct-tip parent grade block(s) that cannot meet "
                f"Min Grade Block Pair Duration: {preview}."
            )
        enumerate_stockpile_mixes_only = (
            bool(self.reserved_blend_signatures)
            and self.configured_contingency_distinctness_mode()
            == self.CONTINGENCY_STOCKPILE_MIX_ONLY
        )
        last_solver_result = None
        no_selected_blend_message = "No feasible blend found."
        stockpile_only_guardrail_fallback = False
        guardrail_rejections = []

        if not events:
            result = {
                "Linprog_result_object": SimpleNamespace(
                    success=False,
                    status="No sources available",
                    status_code=None,
                ),
                "steady_state_duration": initial_steady_state_duration,
                "diagnostics": Optimizer.build_diagnostics(
                    events,
                    period_crusher_target,
                    initial_steady_state_duration,
                    self.periods,
                    self.period_tracker,
                    self.min_stockpiles,
                    self.max_stockpiles,
                    self.min_stockpile_contribution_ratio,
                    [],
                    "No sources available",
                    0,
                ),
            }
            self.register_optimization_diagnostic(result, events, "No sources available.")
            store_blend_option = self.blend_option
            self.blend_option = "No blend found"
            self.record_results(result)
            self.blend_option = store_blend_option

        if events:
            print(
                f"Solving steady state {self.steady_state_tracker} ({self.period_tracker}) "
                f"from {self.current_time:%Y-%m-%d %H:%M} to {steady_state_end:%Y-%m-%d %H:%M}. "
                f"{len(events)} source/equipment option(s) are available."
            )

        while events and len(candidate_source_sets) < max_decision_blend_options:
            self.check_abort_requested()
            print(f"Searching feasible blend option {self.blend_option}...")
            # Run optimization with dynamic steady states
            result = self.optimizer.run_with_dynamic_steady_state(
                events,
                period_crusher_target,
                initial_steady_state_duration,
                self.periods,
                self.period_tracker,
                self.current_time,
                self.stockpiles,
                self.min_stockpiles,
                self.max_stockpiles,
                self.min_stockpile_contribution_ratio,
                step_solver_config,
                excluded_source_sets,
                excluded_stockpile_sets,
            )
            last_solver_result = result
            self.check_abort_requested()

            if not result['Linprog_result_object'].success:
                result.setdefault("diagnostics", {}).setdefault(
                    "guardrail_rejections", []
                ).extend(guardrail_rejections)
                solver_status = getattr(result.get("Linprog_result_object"), "status", "")
                if not candidate_source_sets:
                    self.register_optimization_diagnostic(result, events, "No feasible blend found.")
                    store_blend_option = self.blend_option
                    self.blend_option = "No blend found"
                    self.record_results(result)
                    self.blend_option = store_blend_option
                else:
                    if (
                        blend_option_timeout_seconds is not None
                        and str(solver_status) in {"Not Solved", "Undefined"}
                    ):
                        print(
                            f"Stopped searching additional blend options after "
                            f"{blend_option_timeout_seconds:g} seconds for blend option {self.blend_option}. "
                            f"{len(candidate_source_sets)} feasible option(s) already found; moving on."
                        )
                    else:
                        print(f"No further feasible blend options found after {len(candidate_source_sets)} option(s).")
                break
        
            elif result['Linprog_result_object'].success:
                if result['crusher_actual_tonnes'] > Optimizer.SOLUTION_TOLERANCE:
                    active_source_ids = self.active_source_ids_from_result(result)
                    active_source_signature = frozenset(active_source_ids)
                    if not active_source_ids or active_source_signature in candidate_source_signatures:
                        if not candidate_source_sets:
                            no_selected_blend_message = (
                                "No further distinct blend options remained after applying guardrails."
                            )
                        print("No further distinct blend options found.")
                        break

                    lock_violations = self.grade_block_pair_lock_violations_from_result(result)
                    if lock_violations:
                        no_selected_blend_message = (
                            "All candidate blends were rejected by the grade block lock rule."
                        )
                        excluded_source_sets.append(set(active_source_ids))
                        candidate_source_signatures.add(active_source_signature)
                        print(
                            f"Rejected blend option {self.blend_option} because grade block lock "
                            f"would be breached: {'; '.join(lock_violations)}."
                        )
                        continue

                    grade_block_duration_issues = self.grade_block_pair_duration_issues_from_result(result)
                    if grade_block_duration_issues:
                        no_selected_blend_message = (
                            "All candidate blends were rejected by Min Grade Block Pair Duration."
                        )
                        excluded_source_sets.append(set(active_source_ids))
                        candidate_source_signatures.add(active_source_signature)
                        guardrail_rejections.extend(
                            grade_block_duration_issues
                        )
                        print(
                            f"Rejected blend option {self.blend_option} because grade block pair "
                            f"duration is too short: {'; '.join(grade_block_duration_issues)}."
                        )
                        try:
                            direct_tip_minimum = float(
                                period_crusher_target.get(
                                    "direct_feed_ratio_min", 0
                                ) or 0
                            )
                            direct_tip_maximum = float(
                                period_crusher_target.get(
                                    "direct_feed_ratio_max", 0
                                ) or 0
                            )
                        except (TypeError, ValueError):
                            direct_tip_minimum = direct_tip_maximum = 0.0
                        if (
                            not candidate_source_sets
                            and not stockpile_only_guardrail_fallback
                            and direct_tip_minimum
                            <= Optimizer.SOLUTION_TOLERANCE
                            and direct_tip_maximum
                            > Optimizer.SOLUTION_TOLERANCE
                            and any(event.is_stockpile for event in events)
                        ):
                            # A voluntary direct-tip choice can be rejected by
                            # a post-solve operational guardrail even when the
                            # Calendar minimum is zero. Explicitly try the
                            # valid stockpile-only domain before declaring the
                            # steady state infeasible.
                            period_crusher_target[
                                "direct_feed_ratio_max"
                            ] = 0.0
                            stockpile_only_guardrail_fallback = True
                            print(
                                "Calendar Direct Tip Ratio Min is 0; retrying "
                                "with Direct Tip Ratio Max temporarily set to "
                                "0 after the voluntary direct-tip option was "
                                "rejected by Min Grade Block Pair Duration."
                            )
                        continue

                    potential_feed_duration = self.candidate_potential_feed_duration(result)
                    # Depletion, turnover or build completion can shorten the
                    # initial Calendar window. Apply the guardrail to this
                    # candidate's final window without changing the user input.
                    required_min_feed_duration = self.required_min_feed_duration(
                        result.get("steady_state_duration", initial_steady_state_duration)
                    )
                    if (
                        required_min_feed_duration is not None
                        and configured_min_feed_duration is not None
                        and required_min_feed_duration + Optimizer.SOLUTION_TOLERANCE
                        < configured_min_feed_duration
                    ):
                        print(
                            f"Min Stockpile Feed Duration for blend option {self.blend_option} "
                            f"is capped at {required_min_feed_duration:.2f} hours by its "
                            f"steady-state duration (configured {configured_min_feed_duration:.2f} hours)."
                        )
                    if (
                        required_min_feed_duration is not None
                        and potential_feed_duration + Optimizer.SOLUTION_TOLERANCE < required_min_feed_duration
                    ):
                        no_selected_blend_message = (
                            "All candidate blends were rejected by Min Stockpile Feed Duration."
                        )
                        excluded_source_sets.append(set(active_source_ids))
                        candidate_source_signatures.add(active_source_signature)
                        active_source_names = self.active_source_names_from_result(result)
                        print(
                            f"Rejected blend option {self.blend_option} using "
                            f"{', '.join(active_source_names)} because its stockpile blend can "
                            f"potentially feed for {potential_feed_duration:.2f} hours; minimum feed duration is "
                            f"{required_min_feed_duration:.2f} hours."
                        )
                        continue

                    self.record_results(result)
                    candidate_source_sets.append(set(active_source_ids))
                    if enumerate_stockpile_mixes_only:
                        excluded_stockpile_sets.append(
                            self.stockpile_source_ids_from_transactions(
                                result.get("transactions", [])
                            )
                        )
                    else:
                        excluded_source_sets.append(
                            set(active_source_ids)
                        )
                    candidate_source_signatures.add(active_source_signature)
                    active_source_names = self.active_source_names_from_result(result)
                    print(
                        f"Found blend option {self.blend_option} using "
                        f"{', '.join(active_source_names)}."
                    )
                    self.blend_option += 1
                    continue
                else:
                    if not candidate_source_sets:
                        self.register_optimization_diagnostic(result, events, "Solver returned zero crusher feed.")
                        store_blend_option = self.blend_option
                        self.blend_option = "No blend"
                        self.record_results(result)
                        self.blend_option = store_blend_option
                    else:
                        print(f"No further positive-feed blend options found after {len(candidate_source_sets)} option(s).")
                    break
            else:
                if not candidate_source_sets:
                    self.register_optimization_diagnostic(result, events, "Rare optimisation case.")
                    store_blend_option = self.blend_option
                    self.blend_option = "Rare case"
                    self.record_results(result)
                    self.blend_option = store_blend_option
                break

        if events and len(candidate_source_sets) >= max_decision_blend_options:
            print(f"Stopped after {max_decision_blend_options} feasible blend options.")

        if events and self.decision_point_results.empty:
            if last_solver_result is not None:
                last_solver_result.setdefault("diagnostics", {}).setdefault(
                    "guardrail_rejections", []
                ).extend(
                    issue for issue in guardrail_rejections
                    if issue not in last_solver_result.get(
                        "diagnostics", {}
                    ).get("guardrail_rejections", [])
                )
            self.record_no_selected_blend(
                last_solver_result,
                events,
                period_crusher_target,
                initial_steady_state_duration,
                no_selected_blend_message,
                step_solver_config,
            )

        # Check if there is any decision point results
        if "source_actual_tonnes" in self.decision_point_results:
            self.decision_point_results["source_actual_tonnes"] = (
                pd.to_numeric(
                    self.decision_point_results["source_actual_tonnes"],
                    errors="coerce",
                ).fillna(0)
            )
        if "crusher_actual_tonnes" in self.decision_point_results:
            self.decision_point_results["crusher_actual_tonnes"] = (
                pd.to_numeric(
                    self.decision_point_results["crusher_actual_tonnes"],
                    errors="coerce",
                ).fillna(0)
            )

        has_positive_feed = (
            "source_actual_tonnes" in self.decision_point_results
            and (self.decision_point_results["source_actual_tonnes"] > 0).any()
        )
        if not has_positive_feed:
            self.request_repair_for_terminal_infeasibility(
                last_solver_result,
                period_crusher_target,
                initial_steady_state_duration,
            )
            raise SteadyStateInfeasible(
                self.steady_state_tracker,
                no_selected_blend_message,
            )

        if (
            "source_actual_tonnes" in self.decision_point_results
            and (self.decision_point_results["source_actual_tonnes"] > 0).any()
        ):

            # Manage user interaction
            
            # Filter results to display
            self.decision_point_results_to_display = self.decision_point_results.loc[
                (
                    (self.decision_point_results["source_actual_tonnes"] != 0) & 
                    (self.decision_point_results["crusher_actual_tonnes"] != 0)
                )
            ]

            # Filter results to previous blend choice to compare results between iterations
            self.decision_point_results_to_display_filtered_to_current_blend_choice = self.decision_point_results.loc[
                (
                    (self.decision_point_results["source_actual_tonnes"] != 0) & 
                    (self.decision_point_results["crusher_actual_tonnes"] != 0) &
                    (self.decision_point_results["blend_option"] == self.user_blend_choice)

                )
            ]

            # Prompt user for interaction mode
            if self.user_interaction_mode == None:
                self.user_interaction_mode = input("\033[92mEnter 1 to automatically select the top blend option in every steady state or 2 to select manually: \033[0m")


            # Cast user choice to appropriate type
            try:
                self.user_interaction_mode = int(self.user_interaction_mode)
            except ValueError:
                print("Invalid input. Please enter a number.")
                return

            self.publish_decision_options()
            
            if self.steady_state_tracker != 0:

                # Compare the sources for a blend option between two iteration and avoid user interaction if no change
                current_filtered_sources =  self.decision_point_results_to_display_filtered_to_current_blend_choice["source"]
                previous_filtered_sources = self.results[
                    (self.results["blend_option"] == self.user_blend_choice) &
                    (self.results["steady_state_number"] == self.steady_state_tracker - 1)
                ]["source"]

            else: pass
            
            if self.user_interaction_mode == 1:

                self.user_blend_choice = (
                    self.select_automatic_blend_option()
                )


            elif self.user_interaction_mode == 2 and self.steady_state_tracker != 0:
                
                if not list(current_filtered_sources) == list(previous_filtered_sources):
                    print("Blend fully depleted.")
                    self.user_blend_choice = input("Choose new blend: ")
                    # Cast user choice to appropriate type
                    try:
                        self.user_blend_choice = int(self.user_blend_choice)
                    except ValueError:
                        print("Invalid input. Please enter a number.")
                        return
                else:
                    pass
            
            elif self.user_interaction_mode == 2 and self.steady_state_tracker == 0:
                self.user_blend_choice = input("Choose blend: ")
                # Cast user choice to appropriate type
                try:
                    self.user_blend_choice = int(self.user_blend_choice)
                except ValueError:
                    print("Invalid input. Please enter a number.")
                    return
            
            # Filter results based on user choice
            filtered_decision_point_results_to_user_choice = self.decision_point_results.loc[
                (
                    (self.decision_point_results["source_actual_tonnes"] != 0) & 
                    (self.decision_point_results["crusher_actual_tonnes"] != 0) & 
                    (self.decision_point_results["blend_option"] == self.user_blend_choice)
                ) | 
                    (self.decision_point_results["blend_option"] == "No blend found")
                    |
                    (self.decision_point_results["blend_option"] == "Rare case")
                ]

            # Candidate options are all recorded before the user/automatic
            # choice is known.  Assign the persistent Blend ID only to the
            # chosen option, using source identity and contribution ratios.
            filtered_decision_point_results_to_user_choice = (
                filtered_decision_point_results_to_user_choice.copy()
            )
            filtered_decision_point_results_to_user_choice["blend_ID"] = (
                self.assign_selected_chemical_blend_id(
                    filtered_decision_point_results_to_user_choice
                )
            )
            
            self.append_results(filtered_decision_point_results_to_user_choice)
            self.previous_selected_stockpile_source_ids = self.stockpile_source_ids_from_dataframe(
                filtered_decision_point_results_to_user_choice
            )
            self.update_grade_block_pair_memory(filtered_decision_point_results_to_user_choice)
            completed_build_index = self.update_product_build_runtime_state(
                filtered_decision_point_results_to_user_choice
            )
            self.validate_completed_product_build(completed_build_index)

            # Prepare for next cycle
            
            steady_state_start_time = self.current_time
            steady_state_duration = self.latest_result_duration(initial_steady_state_duration)
            steady_state_end_time = steady_state_start_time + timedelta(hours=steady_state_duration)
            
            try:
                self.balance_tracker.update_balances(filtered_decision_point_results_to_user_choice, 
                                                    self.expit_payload_transactions,
                                                    steady_state_start_time,
                                                    steady_state_end_time,
                                                    self.steady_state_tracker
                                                    )
            except ValueError as e:
                error_message = str(e)
                print(f"Caught Error: {error_message}")
                raise

            self.total_AMT_stockpile_balances = self.balance_tracker.return_total_AMT_stockpile_balances()
            
            self.advance_time()
            
            self.decision_point_results = pd.DataFrame()
            self.blend_option = 1
        
        else:
            self.append_results(self.decision_point_results)
            self.previous_selected_stockpile_source_ids = self.stockpile_source_ids_from_dataframe(
                self.decision_point_results
            )
            self.update_grade_block_pair_memory(self.decision_point_results)
            self.update_product_build_runtime_state(self.decision_point_results)

            # Prepare for next cycle (no results)
            
            steady_state_start_time = self.current_time
            steady_state_duration = self.latest_result_duration(initial_steady_state_duration)
            steady_state_end_time = steady_state_start_time + timedelta(hours=steady_state_duration)

            try:
                self.balance_tracker.update_balances(self.decision_point_results, 
                                                    self.expit_payload_transactions,
                                                    steady_state_start_time,
                                                    steady_state_end_time,
                                                    self.steady_state_tracker
                                                    )
            except ValueError as e:
                error_message = str(e)
                print(f"Caught Error: {error_message}")
                raise

            self.total_AMT_stockpile_balances = self.balance_tracker.return_total_AMT_stockpile_balances()

            self.advance_time()
            
            self.decision_point_results = pd.DataFrame()
            self.blend_option = 1
    
    CONTINGENCY_STOCKPILE_MIX_ONLY = "stockpile_mix_only"
    CONTINGENCY_STOCKPILE_OR_GRADE_BLOCK = (
        "stockpile_or_grade_block_pairing"
    )
    CONTINGENCY_ACCEPTANCE_LABELS = {
        CONTINGENCY_STOCKPILE_MIX_ONLY: (
            "Different Stockpile Mix Only (harder to find)"
        ),
        CONTINGENCY_STOCKPILE_OR_GRADE_BLOCK: (
            "Different Stockpile Mix or Grade Block Pairing "
            "(easier to find)"
        ),
    }

    def configured_contingency_distinctness_mode(self):
        solver_config = getattr(self, "solver_config", {}) or {}
        mode = str(
            solver_config.get(
                "contingency_distinctness_mode",
                self.CONTINGENCY_STOCKPILE_OR_GRADE_BLOCK,
            )
            or ""
        ).strip()
        if mode not in self.CONTINGENCY_ACCEPTANCE_LABELS:
            return self.CONTINGENCY_STOCKPILE_OR_GRADE_BLOCK
        return mode

    @staticmethod
    def contingency_source_type(row):
        source_type = str(
            row.get("source_type") or ""
        ).strip().lower().replace(" ", "_")
        if source_type in {"stockpile", "grade_block"}:
            return source_type
        equipment = str(row.get("equipment") or "").strip().upper()
        if equipment.startswith("RC"):
            return "stockpile"
        if equipment.startswith("EX"):
            return "grade_block"
        return source_type

    def blend_signature_from_dataframe(self, data):
        if data is None or data.empty:
            return frozenset()
        active = data.copy()
        if "source_actual_tonnes" in active:
            tonnes = pd.to_numeric(
                active["source_actual_tonnes"], errors="coerce"
            ).fillna(0)
            active = active[tonnes > Optimizer.SOLUTION_TOLERANCE]
        stockpile_signature = set()
        grade_block_signature = set()
        for _, row in active.iterrows():
            source_type = self.contingency_source_type(row)
            source = str(
                row.get("parent_stockpile")
                if source_type == "stockpile" and row.get("parent_stockpile")
                else row.get("source") or row.get("source_id") or ""
            ).strip().upper()
            if not source:
                continue
            if source_type == "stockpile":
                stockpile_signature.add(f"stockpile:{source}")
            elif source_type == "grade_block":
                grade_block_signature.add(f"grade_block:{source}")

        if not stockpile_signature:
            stockpile_signature.add("stockpile_mix:<none>")
        signature = set(stockpile_signature)
        if (
            self.configured_contingency_distinctness_mode()
            == self.CONTINGENCY_STOCKPILE_OR_GRADE_BLOCK
        ):
            signature.update(grade_block_signature)
        return frozenset(signature)

    def chemical_blend_signature_from_dataframe(self, data):
        """Return the source-and-ratio signature that defines a Blend ID.

        The contingency signature intentionally records only source identity.
        Blend IDs are more specific: a stockpile-ratio change or a direct-tip
        grade-block/rate change represents a different chemical blend even if
        the set of source IDs is unchanged.
        """
        if data is None or data.empty:
            return tuple()

        active = data.copy()
        if "source_actual_tonnes" not in active:
            return tuple()
        active["_blend_tonnes"] = pd.to_numeric(
            active["source_actual_tonnes"], errors="coerce"
        ).fillna(0.0)
        active = active[
            active["_blend_tonnes"] > Optimizer.SOLUTION_TOLERANCE
        ]
        if active.empty:
            return tuple()

        contributions = {}
        for _, row in active.iterrows():
            source_type = self.contingency_source_type(row) or "unknown"
            source_id = str(
                row.get("source_id") or row.get("source") or ""
            ).strip().upper()
            if not source_id:
                continue
            key = (source_type, source_id)
            contributions[key] = contributions.get(key, 0.0) + float(
                row["_blend_tonnes"]
            )

        total_tonnes = sum(contributions.values())
        if total_tonnes <= Optimizer.SOLUTION_TOLERANCE:
            return tuple()
        # Eight decimal places prevents insignificant solver tolerance noise
        # from producing a new blend while retaining meaningful mix changes.
        return tuple(sorted(
            (source_type, source_id, round(tonnes / total_tonnes, 8))
            for (source_type, source_id), tonnes in contributions.items()
        ))

    def assign_selected_chemical_blend_id(self, selected_rows):
        """Assign the persistent Blend ID for the selected steady state."""
        signature = self.chemical_blend_signature_from_dataframe(selected_rows)
        if not signature:
            return self.blend_ID
        if (
            self.previous_chemical_blend_signature is not None
            and signature != self.previous_chemical_blend_signature
        ):
            self.blend_ID += 1
        self.previous_chemical_blend_signature = signature
        return self.blend_ID

    def select_automatic_blend_option(self):
        options = []
        numeric_options = pd.to_numeric(
            self.decision_point_results["blend_option"],
            errors="coerce",
        ).dropna()
        for option in sorted(set(numeric_options.astype(int).tolist())):
            rows = self.decision_point_results[
                self.decision_point_results["blend_option"] == option
            ]
            signature = self.blend_signature_from_dataframe(rows)
            reuse_count = sum(
                signature == reserved
                for reserved in self.reserved_blend_signatures
            )
            options.append((reuse_count, option, signature))

        if not options:
            return 1
        reuse_count, option, signature = min(
            options, key=lambda candidate: (candidate[0], candidate[1])
        )
        if reuse_count and self.reserved_blend_signatures:
            mode = self.configured_contingency_distinctness_mode()
            self.contingency_reuse_fallbacks += 1
            print(
                f"{self.plan_id}: no unused blend satisfying "
                f"'{self.CONTINGENCY_ACCEPTANCE_LABELS[mode]}' was feasible "
                f"in steady state {self.steady_state_tracker}. Reusing the "
                f"least-reused feasible blend option {option}; later steady "
                "states will continue searching for a distinct mix."
            )
        elif self.reserved_blend_signatures:
            print(
                f"{self.plan_id}: selected the highest-ranked unused "
                f"blend option {option}."
            )
        if signature:
            self.selected_blend_signatures.add(signature)
        return option

    def selected_plan_blend_signatures(self):
        signatures = set(self.selected_blend_signatures)
        if self.results is None or self.results.empty:
            return signatures
        if "steady_state_number" not in self.results:
            return signatures
        for _, group in self.results.groupby(
            "steady_state_number", sort=False, dropna=False
        ):
            signature = self.blend_signature_from_dataframe(group)
            if signature:
                signatures.add(signature)
        return signatures

    def publish_decision_options(self):
        display_columns = [
            "tipping_point", "opf",
            "steady_state_number",
            "start_datetime",
            "end_datetime",
            "steady_state_duration",
            "blend_option",
            "solver_score",
            "source",
            "estimated_delivery_datetime",
            "source_blend_ratio",
            "source_actual_tonnes",
            "crusher_rate_output",
            "crusher_actual_grade_fe",
            "crusher_actual_grade_si",
            "crusher_actual_grade_al",
            "crusher_actual_grade_p",
            "crusher_actual_grade_mn",
        ]
        available_columns = [
            column for column in display_columns
            if column in self.decision_point_results_to_display.columns
        ]
        if available_columns:
            print(self.group_decision_results_for_display(self.decision_point_results_to_display)[available_columns])

        blend_options = sorted(
            self.decision_point_results_to_display["blend_option"].dropna().unique()
        )
        option_count = len(blend_options)
        if self.user_interaction_mode == 1:
            if self.reserved_blend_signatures:
                print(
                    f"Contingency auto mode: the highest-ranked unused "
                    f"blend will be selected from {option_count} feasible "
                    "option(s)."
                )
            else:
                print(
                    f"Auto select mode: Blend option 1 will be selected "
                    f"from {option_count} feasible option(s). Higher solver "
                    "score is better."
                )
        elif self.user_interaction_mode == 2:
            print("Manual mode: choose a blend option from the table. Higher solver score is better.")

    def configured_min_feed_duration_hours(self):
        try:
            value = float(self.solver_config.get("min_feed_duration_hours") or 0)
        except (TypeError, ValueError):
            return None
        return value if value > Optimizer.SOLUTION_TOLERANCE else None

    def configured_blend_option_timeout_seconds(self):
        try:
            value = float(self.solver_config.get("blend_option_timeout_seconds") or 0)
        except (TypeError, ValueError):
            return None
        return value if value > Optimizer.SOLUTION_TOLERANCE else None

    def configured_max_decision_blend_options(self):
        config_key = (
            "contingency_max_blend_options_per_steady_state"
            if str(getattr(self, "plan_id", "Primary")) != "Primary"
            else "max_blend_options_per_steady_state"
        )
        try:
            value = int(self.solver_config.get(
                config_key,
                self.MAX_DECISION_BLEND_OPTIONS,
            ) or self.MAX_DECISION_BLEND_OPTIONS)
        except (TypeError, ValueError):
            value = self.MAX_DECISION_BLEND_OPTIONS
        return max(1, value)

    def solver_config_for_current_step(self):
        solver_config = dict(self.solver_config or {})
        solver_config["current_steady_state_datetime"] = self.current_time
        solver_config["enforce_cumulative_product_build_grade"] = False
        current_indices = self.current_product_build_indices()
        if current_indices:
            target_builds = {
                lane: copy.deepcopy(self.product_build_settings[index])
                for lane, index in current_indices.items()
            }
            target_states = {
                lane: dict(self.product_build_runtime_states[index])
                for lane, index in current_indices.items()
            }
            completion_projection = {
                lane: self.active_product_build_completes_within_horizon(
                    index, lane=lane
                )
                for lane, index in current_indices.items()
            }
            solver_config["target_product_builds"] = target_builds
            solver_config["target_product_build_states"] = target_states
            solver_config[
                "active_product_builds_complete_within_horizon"
            ] = completion_projection
            cumulative_repairs = {}
            hard_repairs = {}
            for lane, index in current_indices.items():
                repair_state = getattr(
                    self, "product_build_repair_from_states", {}
                ).get(index)
                hard_state = getattr(
                    self, "product_build_hard_repair_from_states", {}
                ).get(index)
                cumulative_repairs[lane] = (
                    repair_state is not None
                    and self.steady_state_tracker >= repair_state
                )
                hard_repairs[lane] = (
                    hard_state is not None
                    and self.steady_state_tracker >= hard_state
                )
            solver_config[
                "enforce_cumulative_product_build_grades"
            ] = cumulative_repairs
            solver_config[
                "force_product_build_state_grades_on_spec_by_lane"
            ] = hard_repairs
            primary_lane = (
                "lump" if "lump" in current_indices
                else next(iter(current_indices))
            )
            current_product_build_index = current_indices[primary_lane]
            current_product_build = target_builds[primary_lane]
            solver_config["target_product_brand"] = current_product_build.get("brand", "")
            # Legacy aliases keep the established one-build path and reports
            # stable. The optimiser consumes the lane dictionaries above when
            # by-products are enabled.
            solver_config["target_product_build"] = dict(current_product_build)
            solver_config["target_product_build_state"] = dict(
                target_states[primary_lane]
            )
            solver_config["active_product_build_completes_within_horizon"] = (
                completion_projection[primary_lane]
            )
            solver_config["enforce_cumulative_product_build_grade"] = (
                cumulative_repairs[primary_lane]
            )
            solver_config["force_product_build_state_grades_on_spec"] = (
                hard_repairs[primary_lane]
            )
        else:
            period_target = (self.crusher_targets or {}).get(self.period_tracker, {}) or {}
            solver_config["target_product_brand"] = period_target.get("brand", "")
        solver_config["previous_blend_stockpile_source_ids"] = sorted(
            self.previous_selected_stockpile_source_ids
        )
        solver_config["previous_grade_block_pairs"] = {
            source: list(stockpiles)
            for source, stockpiles in self.previous_selected_grade_block_pairs.items()
        }
        solver_config["grade_block_pair_locks"] = {
            source: list(stockpiles)
            for source, stockpiles in self.grade_block_pair_locks.items()
        }
        return solver_config

    def active_product_build_completes_within_horizon(self, build_index, lane=None):
        """Return whether remaining crusher capacity can finish the active build.

        This deliberately uses the remaining configured planning horizon, rather
        than just the current steady state. It is a capacity projection; source
        availability and grade constraints are still checked by the optimiser.
        """
        if build_index is None or build_index >= len(self.product_build_settings):
            return False

        build = self.product_build_settings[build_index]
        state = self.product_build_runtime_states[build_index]
        remaining_tonnes = max(
            float(build.get("target_tonnes") or 0)
            - float(state.get("tonnes") or 0),
            0.0,
        )
        if remaining_tonnes <= self.PRODUCT_BUILD_TONNES_TOLERANCE:
            return True

        periods = self.periods.get_periods()
        remaining_capacity = 0.0
        for period_name in self.configured_period_keys():
            period_end = periods.get(f"{period_name}_end")
            if period_end is None or self.current_time >= period_end:
                continue
            period_start = periods.get(f"{period_name}_start", self.current_time)
            active_start = max(self.current_time, period_start)
            hours = max((period_end - active_start).total_seconds() / 3600, 0.0)
            try:
                crusher_rate = float(
                    (self.crusher_targets.get(period_name) or {}).get(
                        "crusher_rate", 0
                    ) or 0
                )
            except (TypeError, ValueError):
                crusher_rate = 0.0
            if build.get('contributing_opfs'):
                crusher_rate = sum(float(p['targets_by_period'].get(period_name, {}).get('crusher_rate') or 0)
                                   for p in self.multi_feed_configuration['tipping_points'] if p['opf'] in build['contributing_opfs'])
            remaining_capacity += (
                self.product_build_capacity_rate(crusher_rate, lane=lane) * hours
            )

        return remaining_tonnes <= (
            remaining_capacity + self.PRODUCT_BUILD_TONNES_TOLERANCE
        )

    def product_build_capacity_rate(self, crusher_rate, lane=None):
        """Convert configured crusher capacity to an optimistic build rate."""
        crusher_stream = str(
            self.solver_config.get("crusher_tonnes_stream")
            or "modelled_rom_wmt"
        )
        if bool(getattr(self, "byproducts_enabled", False)) and lane_kind(lane) in BYPRODUCT_LANES:
            product_stream = normalize_byproduct_quantity_fields(
                self.solver_config.get("byproduct_quantity_fields")
            )[lane_kind(lane)]
        else:
            product_stream = str(
                self.solver_config.get("product_build_tonnes_stream")
                or "modelled_product_wmt"
            )
        event_pool = getattr(self, "event_pool", None)
        if isinstance(event_pool, EventPoolGenerator):
            capacity_sources = [
                *(event_pool.stockpiles or []),
                *(event_pool.grade_blocks or []),
            ]
        else:
            # Retain compatibility with callers and tests that provide an
            # already-generated event list.
            capacity_sources = event_pool or []

        ratios = []
        for event in capacity_sources:
            properties = getattr(event, "source_properties", {}) or {}
            try:
                crusher_raw = properties.get(crusher_stream)
                product_raw = properties.get(product_stream)
                if crusher_raw is None or product_raw is None:
                    continue
                crusher_tonnes = float(crusher_raw)
                product_tonnes = float(product_raw)
            except (TypeError, ValueError):
                continue
            if crusher_tonnes > Optimizer.SOLUTION_TOLERANCE:
                ratios.append(max(product_tonnes, 0.0) / crusher_tonnes)
        ratio = max(ratios) if ratios else 0.0
        try:
            return max(float(crusher_rate), 0.0) * ratio
        except (TypeError, ValueError):
            return 0.0

    def configured_min_grade_block_pair_duration_hours(self):
        try:
            value = float(self.solver_config.get("min_grade_block_pair_duration_hours") or 0)
        except (TypeError, ValueError):
            return None
        return value if value > Optimizer.SOLUTION_TOLERANCE else None

    def filter_grade_block_events_by_pair_duration(
        self, events, steady_state_duration
    ):
        """Remove parent blocks whose complete delivery window is too short.

        The post-solve guardrail evaluates every payload row belonging to a
        selected parent block, including zero-selected sibling slices.  Its
        maximum possible window is therefore known before solving. Excluding
        an inevitably invalid parent here lets an optional-direct-tip period
        fall back cleanly to another parent or to stockpile-only feed.
        """
        required_duration = (
            self.configured_min_grade_block_pair_duration_hours()
        )
        try:
            steady_state_duration = float(steady_state_duration or 0.0)
        except (TypeError, ValueError):
            steady_state_duration = 0.0
        if (
            required_duration is None
            or steady_state_duration + Optimizer.SOLUTION_TOLERANCE
            < required_duration
        ):
            return list(events or []), []

        parent_deliveries = {}
        for event in events or []:
            if not getattr(event, "is_grade_block", False):
                continue
            parent_source = parent_grade_block_name(
                getattr(event, "source_name", None)
                or getattr(event, "grade_block", None)
            )
            if not parent_source:
                continue
            parent_deliveries.setdefault(parent_source, []).append(
                getattr(event, "delivered_datetime", None)
            )

        invalid_parents = {}
        for parent_source, delivered_datetimes in parent_deliveries.items():
            available_duration = (
                Optimizer.calculate_grouped_payload_depletion_duration(
                    delivered_datetimes,
                    getattr(self, "current_time", None),
                )
            )
            if (
                available_duration is None
                or available_duration + Optimizer.SOLUTION_TOLERANCE
                < required_duration
            ):
                invalid_parents[parent_source] = available_duration

        if not invalid_parents:
            return list(events or []), []

        filtered_events = [
            event
            for event in events or []
            if not (
                getattr(event, "is_grade_block", False)
                and parent_grade_block_name(
                    getattr(event, "source_name", None)
                    or getattr(event, "grade_block", None)
                ) in invalid_parents
            )
        ]
        descriptions = [
            (
                f"{parent_source} has no valid delivery window"
                if available_duration is None
                else f"{parent_source} {available_duration:.2f} hrs"
            )
            for parent_source, available_duration in sorted(
                invalid_parents.items()
            )
        ]
        return filtered_events, descriptions

    def grade_block_pair_duration_issues_from_result(self, result):
        required_duration = self.configured_min_grade_block_pair_duration_hours()
        if required_duration is None:
            return []

        try:
            result_duration = float(result.get("steady_state_duration") or 0)
        except (TypeError, ValueError):
            result_duration = 0.0

        if result_duration + Optimizer.SOLUTION_TOLERANCE < required_duration:
            return []

        transactions = list(result.get("transactions", []) or [])
        selected_parent_sources = set()
        for transaction in transactions:
            if transaction.get("source_type") != "grade_block":
                continue
            try:
                actual_tonnes = float(transaction.get("actual_tonnes") or 0)
            except (TypeError, ValueError):
                actual_tonnes = 0
            source = parent_grade_block_name(
                transaction.get("source") or transaction.get("source_id")
            )
            if (
                source
                and actual_tonnes > Optimizer.SOLUTION_TOLERANCE
            ):
                selected_parent_sources.add(source)

        source_details = {}
        for transaction in transactions:
            if transaction.get("source_type") != "grade_block":
                continue
            source = parent_grade_block_name(
                transaction.get("source") or transaction.get("source_id")
            )
            if source not in selected_parent_sources:
                continue
            try:
                actual_tonnes = max(
                    float(transaction.get("actual_tonnes") or 0), 0.0
                )
            except (TypeError, ValueError):
                actual_tonnes = 0.0
            details = source_details.setdefault(
                source,
                {
                    "actual_tonnes": 0.0,
                    "payload_tonnes": 0.0,
                    "payload_count": 0,
                    "delivered_datetimes": [],
                },
            )
            details["actual_tonnes"] += actual_tonnes
            try:
                opening_balance = float(transaction.get("opening_balance") or 0)
            except (TypeError, ValueError):
                opening_balance = 0
            details["payload_tonnes"] += max(opening_balance, 0.0)
            details["payload_count"] += 1
            delivered_datetime = pd.to_datetime(
                transaction.get("estimated_delivery_datetime"),
                errors="coerce",
            )
            if not pd.isna(delivered_datetime):
                details["delivered_datetimes"].append(delivered_datetime.to_pydatetime())

        issues = []
        steady_state_start = getattr(self, "current_time", None)
        for source, details in source_details.items():
            if steady_state_start is None:
                source_duration = result_duration
            else:
                source_duration = Optimizer.calculate_grouped_payload_depletion_duration(
                    details["delivered_datetimes"],
                    steady_state_start,
                )

            if source_duration is None:
                issues.append(
                    f"{source} has no valid payload delivery timestamp while paired in a "
                    f"{result_duration:.2f} hr steady state; minimum is {required_duration:.2f} hrs"
                )
                continue

            if source_duration + Optimizer.SOLUTION_TOLERANCE < required_duration:
                issues.append(
                    f"{source} delivery window is {source_duration:.2f} hrs from "
                    f"{details['payload_count']} payload row(s) "
                    f"({details['payload_tonnes']:.1f} t payload tonnes available, "
                    f"{details['actual_tonnes']:.1f} t selected) in a "
                    f"{result_duration:.2f} hr steady state; minimum is {required_duration:.2f} hrs"
                )
        return issues

    def grade_block_pair_lock_violations_from_result(self, result):
        if not self.solver_config.get("grade_block_lock_enabled", False):
            return []

        stockpile_ids = self.stockpile_source_ids_from_transactions(result.get("transactions", []))
        grade_block_sources = self.grade_block_sources_from_transactions(result.get("transactions", []))
        violations = []
        for source in grade_block_sources:
            locked_stockpiles = self.grade_block_pair_locks.get(source)
            if not locked_stockpiles:
                continue
            if tuple(sorted(stockpile_ids)) != tuple(locked_stockpiles):
                violations.append(
                    f"{source} is locked to {', '.join(locked_stockpiles)} "
                    f"but candidate uses {', '.join(sorted(stockpile_ids)) or 'no stockpile'}"
                )
        return violations

    def update_grade_block_pair_memory(self, data):
        pairs = self.grade_block_pair_signatures_from_dataframe(data)
        self.previous_selected_grade_block_pairs = pairs
        if not self.solver_config.get("grade_block_lock_enabled", False):
            return
        for source, stockpiles in pairs.items():
            if stockpiles and source not in self.grade_block_pair_locks:
                self.grade_block_pair_locks[source] = tuple(stockpiles)

    def required_min_feed_duration(self, available_window_duration):
        """Cap the configured minimum to the candidate's final steady-state window."""
        configured_duration = self.configured_min_feed_duration_hours()
        if configured_duration is None:
            return None
        try:
            available_window_duration = float(available_window_duration)
        except (TypeError, ValueError):
            return configured_duration
        if available_window_duration <= Optimizer.SOLUTION_TOLERANCE:
            return configured_duration
        return min(configured_duration, available_window_duration)

    def candidate_potential_feed_duration(self, result):
        """Estimate how long the selected stockpile blend could keep feeding from current balances."""
        stockpile_durations = []
        result_duration = float(result.get("steady_state_duration") or 0)
        for transaction in result.get("transactions", []):
            if transaction.get("source_type") != "stockpile":
                continue
            try:
                actual_tonnes = float(transaction.get("actual_tonnes") or 0)
                opening_balance = float(transaction.get("opening_balance") or 0)
                equipment_rate_output = float(transaction.get("equipment_rate_output") or 0)
            except (TypeError, ValueError):
                continue
            if actual_tonnes <= Optimizer.SOLUTION_TOLERANCE:
                continue
            if equipment_rate_output <= Optimizer.SOLUTION_TOLERANCE and result_duration > Optimizer.SOLUTION_TOLERANCE:
                equipment_rate_output = actual_tonnes / result_duration
            if opening_balance <= Optimizer.SOLUTION_TOLERANCE or equipment_rate_output <= Optimizer.SOLUTION_TOLERANCE:
                return 0
            stockpile_durations.append(opening_balance / equipment_rate_output)

        if stockpile_durations:
            return min(stockpile_durations)
        return result_duration

    def stockpile_source_ids_from_dataframe(self, data):
        if data is None or data.empty or "source_actual_tonnes" not in data.columns:
            return set()

        data = data.copy()
        data["source_actual_tonnes"] = pd.to_numeric(
            data["source_actual_tonnes"], errors="coerce"
        ).fillna(0)
        data = data[data["source_actual_tonnes"] > Optimizer.SOLUTION_TOLERANCE]
        if "source_type" in data.columns:
            data = data[data["source_type"] == "stockpile"]

        source_column = "source_id" if "source_id" in data.columns else "source"
        if source_column not in data.columns:
            return set()
        if "parent_stockpile" in data.columns:
            values = data["parent_stockpile"].where(
                data["parent_stockpile"].notna()
                & data["parent_stockpile"].astype(str).str.strip().ne(""),
                data[source_column],
            )
        else:
            values = data[source_column]
        return set(str(value) for value in values.dropna() if str(value))

    def stockpile_source_ids_from_transactions(self, transactions):
        stockpile_ids = set()
        for transaction in transactions or []:
            if transaction.get("source_type") != "stockpile":
                continue
            try:
                actual_tonnes = float(transaction.get("actual_tonnes") or 0)
            except (TypeError, ValueError):
                actual_tonnes = 0
            if actual_tonnes <= Optimizer.SOLUTION_TOLERANCE:
                continue
            source_id = (
                transaction.get("parent_stockpile")
                or transaction.get("source_id")
                or transaction.get("source")
            )
            if source_id:
                stockpile_ids.add(str(source_id))
        return stockpile_ids

    def grade_block_sources_from_transactions(self, transactions):
        grade_block_sources = set()
        for transaction in transactions or []:
            if transaction.get("source_type") != "grade_block":
                continue
            try:
                actual_tonnes = float(transaction.get("actual_tonnes") or 0)
            except (TypeError, ValueError):
                actual_tonnes = 0
            if actual_tonnes <= Optimizer.SOLUTION_TOLERANCE:
                continue
            source = parent_grade_block_name(
                transaction.get("source") or transaction.get("source_id")
            )
            if source:
                grade_block_sources.add(source)
        return grade_block_sources

    def grade_block_pair_signatures_from_dataframe(self, data):
        if data is None or data.empty or "source_actual_tonnes" not in data.columns:
            return {}

        data = data.copy()
        data["source_actual_tonnes"] = pd.to_numeric(
            data["source_actual_tonnes"], errors="coerce"
        ).fillna(0)
        data = data[data["source_actual_tonnes"] > Optimizer.SOLUTION_TOLERANCE]
        if data.empty or "source_type" not in data.columns:
            return {}

        stockpile_ids = self.stockpile_source_ids_from_dataframe(data)
        if not stockpile_ids:
            return {}

        grade_block_data = data[data["source_type"] == "grade_block"]
        if grade_block_data.empty or "source" not in grade_block_data.columns:
            return {}

        return {
            source: tuple(sorted(stockpile_ids))
            for source in {
                parent_grade_block_name(value)
                for value in grade_block_data["source"].dropna().unique()
            }
            if source
        }

    def active_source_ids_from_result(self, result):
        """Return source labels used for distinct-option exclusion."""
        active_source_ids = []
        for transaction in result.get("transactions", []):
            try:
                actual_tonnes = float(transaction.get("actual_tonnes") or 0)
            except (TypeError, ValueError):
                actual_tonnes = 0
            if actual_tonnes > Optimizer.SOLUTION_TOLERANCE:
                if transaction.get("source_type") == "grade_block":
                    active_source_ids.append(transaction.get("source") or transaction.get("source_id"))
                else:
                    # Optimizer source-selection binaries identify stockpiles
                    # by their physical parent. AMT reports identify the
                    # active chunk as source_id, so use the balance-tracking
                    # parent here to ensure rejected-candidate cuts actually
                    # exclude the solution that was just returned.
                    active_source_ids.append(
                        transaction.get("balance_tracker_source_id")
                        or transaction.get("parent_stockpile")
                        or transaction.get("source_id")
                        or transaction.get("source")
                    )
        return sorted(source for source in set(active_source_ids) if source)

    def active_source_names_from_result(self, result):
        """Return display source names with positive tonnes in an optimisation result."""
        active_sources = []
        for transaction in result.get("transactions", []):
            try:
                actual_tonnes = float(transaction.get("actual_tonnes") or 0)
            except (TypeError, ValueError):
                actual_tonnes = 0
            if actual_tonnes > Optimizer.SOLUTION_TOLERANCE:
                active_sources.append(transaction.get("source") or transaction.get("source_id"))
        return sorted(source for source in set(active_sources) if source)

    def group_decision_results_for_display(self, data: pd.DataFrame):
        return self.group_grade_block_rows(data)

    def group_grade_block_rows(self, data: pd.DataFrame):
        """Group selected grade-block payload rows by readable grade-block source."""
        if data is None or data.empty or "source_type" not in data.columns or "source" not in data.columns:
            return data

        data = data.copy()
        grade_block_rows = data[data["source_type"] == "grade_block"].copy()
        other_rows = data[data["source_type"] != "grade_block"].copy()
        if grade_block_rows.empty:
            return data

        # Reporting operates at the operational parent grade-block level.
        # Individual payload IDs and arrival timestamps remain available on
        # the grouped row, but slice suffixes such as _627 and _124 do not
        # create separate output rows.
        grade_block_rows["source"] = grade_block_rows["source"].map(
            parent_grade_block_name
        )

        for column in [
            "source_actual_tonnes",
            "crusher_actual_tonnes",
            "source_opening_balance",
            "source_closing_balance",
            "constraint_source_balance",
            "reclaimer_source_tonnes",
            "crusher_source_tonnes",
            "product_build_source_tonnes",
            "equipment_rate_input",
            "equipment_rate_output",
            "crusher_rate_output",
            "source_blend_ratio",
        ]:
            if column in grade_block_rows.columns:
                grade_block_rows[column] = pd.to_numeric(grade_block_rows[column], errors="coerce").fillna(0)

        group_keys = [
            column for column in [
                "start_datetime",
                "end_datetime",
                "steady_state_number",
                "blend_option",
                "blend_ID",
                "solver_score",
                "steady_state_duration",
                "period",
                "source",
                "source_type",
                "equipment",
                "tipping_point", "opf",
            ]
            if column in grade_block_rows.columns
        ]

        grouped_records = []
        for _, group in grade_block_rows.groupby(group_keys, dropna=False, sort=False):
            record = group.iloc[0].copy()
            actual_tonnes = group["source_actual_tonnes"].sum()
            crusher_actual_tonnes = group["crusher_actual_tonnes"].iloc[0] if "crusher_actual_tonnes" in group else 0
            record["source_actual_tonnes"] = actual_tonnes
            if "source_blend_ratio" in group:
                record["source_blend_ratio"] = (
                    actual_tonnes / crusher_actual_tonnes
                    if crusher_actual_tonnes not in (0, None)
                    else group["source_blend_ratio"].sum()
                )
            if "source_opening_balance" in group:
                record["source_opening_balance"] = group["source_opening_balance"].sum()
            if "source_closing_balance" in group:
                record["source_closing_balance"] = group["source_closing_balance"].sum()
            for additive_column in (
                "constraint_source_balance",
                "reclaimer_source_tonnes",
                "crusher_source_tonnes",
                "product_build_source_tonnes",
                "equipment_rate_input",
            ):
                if additive_column in group:
                    values = pd.to_numeric(
                        group[additive_column], errors="coerce"
                    )
                    record[additive_column] = (
                        values.sum(min_count=1)
                        if values.notna().any() else None
                    )
            if "equipment_rate_output" in group:
                record["equipment_rate_output"] = group["equipment_rate_output"].sum()
            if "source_id" in group:
                record["source_id"] = ", ".join(str(value) for value in group["source_id"].dropna().unique())
            if "estimated_delivery_datetime" in group:
                delivered_datetimes = pd.to_datetime(
                    group["estimated_delivery_datetime"],
                    errors="coerce",
                ).dropna().drop_duplicates().sort_values()
                if len(delivered_datetimes) == 1:
                    record["estimated_delivery_datetime"] = delivered_datetimes.iloc[0].strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                elif len(delivered_datetimes) > 1:
                    record["estimated_delivery_datetime"] = (
                        f"from {delivered_datetimes.iloc[0]:%Y-%m-%d %H:%M:%S} "
                        f"to {delivered_datetimes.iloc[-1]:%Y-%m-%d %H:%M:%S}"
                    )
                else:
                    record["estimated_delivery_datetime"] = ""

            # One readable grade block can contain several 2WP allocation
            # payloads. Preserve all exact destinations while reporting the
            # same tonnes-weighted turnover coefficient that contributed to
            # the grouped objective.
            for text_column in (
                "two_wp_planned_stockpile_destination",
                "two_wp_first_reclaim_datetime",
            ):
                if text_column in group:
                    values = [
                        str(value).strip()
                        for value in group[text_column].dropna()
                        if str(value).strip()
                    ]
                    record[text_column] = ", ".join(dict.fromkeys(values))
            weights = pd.to_numeric(
                group["source_actual_tonnes"], errors="coerce"
            ).fillna(0)
            for numeric_column in (
                "two_wp_destination_turnover_priority",
                "two_wp_destination_turnover_incentive_applied",
            ):
                if numeric_column in group:
                    values = pd.to_numeric(
                        group[numeric_column], errors="coerce"
                    )
                    valid = values.notna() & (
                        weights > Optimizer.SOLUTION_TOLERANCE
                    )
                    valid_tonnes = weights[valid].sum()
                    record[numeric_column] = (
                        (values[valid] * weights[valid]).sum()
                        / valid_tonnes
                        if valid_tonnes > Optimizer.SOLUTION_TOLERANCE
                        else None
                    )
            if "two_wp_destination_turnover_guidance_applied" in group:
                record["two_wp_destination_turnover_guidance_applied"] = any(
                    str(value).strip().lower() in {"true", "1", "yes"}
                    for value in group[
                        "two_wp_destination_turnover_guidance_applied"
                    ].dropna()
                )

            # The grouped source row remains an audit record, so raw stream
            # fields must be weighted exactly like the selected legacy grades.
            for grade_column in [
                column for column in group.columns
                if column.startswith("source_grade_")
            ]:
                grades = pd.to_numeric(group[grade_column], errors="coerce")
                weights = pd.to_numeric(
                    group["source_actual_tonnes"], errors="coerce"
                ).fillna(0)
                valid = grades.notna() & (weights > Optimizer.SOLUTION_TOLERANCE)
                valid_tonnes = weights[valid].sum()
                record[grade_column] = (
                    (grades[valid] * weights[valid]).sum() / valid_tonnes
                    if valid_tonnes > Optimizer.SOLUTION_TOLERANCE
                    else None
                )

            # Payload transactions already carry additive properties scaled
            # to the selected payload mass.  Sum those totals when several
            # payloads from one readable grade block are collapsed.  Physical
            # assays/percentages remain intensive and must be tonnes-weighted,
            # just like the grade-stream audit fields above.
            for property_column in [
                column for column in group.columns
                if str(column).startswith("source_property_")
            ]:
                values = pd.to_numeric(
                    group[property_column], errors="coerce"
                )
                property_name = str(property_column).removeprefix(
                    "source_property_"
                )
                solver_config = getattr(self, "solver_config", {}) or {}
                if property_name.endswith((
                    "_opening_balance", "_actual_depletion", "_closing_balance"
                )) or source_property_kind(
                    property_name,
                    solver_config.get("source_property_kinds"),
                ) == "additive":
                    record[property_column] = (
                        values.sum(min_count=1)
                        if values.notna().any() else None
                    )
                    continue
                weight_name = (
                    solver_config.get("source_property_weights") or {}
                ).get(property_name)
                weight_column = (
                    f"source_property_{weight_name}" if weight_name else ""
                )
                if weight_name and weight_column not in group.columns:
                    record[property_column] = None
                    continue
                weights = pd.to_numeric(
                    group[
                        weight_column
                        if weight_name else "source_actual_tonnes"
                    ],
                    errors="coerce",
                ).fillna(0)
                valid = values.notna() & (
                    weights > Optimizer.SOLUTION_TOLERANCE
                )
                valid_tonnes = weights[valid].sum()
                record[property_column] = (
                    (values[valid] * weights[valid]).sum() / valid_tonnes
                    if valid_tonnes > Optimizer.SOLUTION_TOLERANCE
                    else None
                )

            for coefficient_column in [
                column for column in group.columns
                if str(column).startswith("custom_constraint_")
                and str(column).endswith((
                    "_source_numerator_coefficient",
                    "_source_denominator_coefficient",
                ))
            ]:
                coefficients = pd.to_numeric(
                    group[coefficient_column], errors="coerce"
                )
                weights = pd.to_numeric(
                    group["source_actual_tonnes"], errors="coerce"
                ).fillna(0)
                valid = coefficients.notna() & (
                    weights > Optimizer.SOLUTION_TOLERANCE
                )
                valid_tonnes = weights[valid].sum()
                record[coefficient_column] = (
                    (coefficients[valid] * weights[valid]).sum()
                    / valid_tonnes
                    if valid_tonnes > Optimizer.SOLUTION_TOLERANCE
                    else None
                )

            for contribution_column in [
                column for column in group.columns
                if str(column).startswith("custom_constraint_")
                and str(column).endswith((
                    "_source_numerator_contribution",
                    "_source_denominator_contribution",
                ))
            ]:
                values = pd.to_numeric(
                    group[contribution_column], errors="coerce"
                )
                record[contribution_column] = (
                    values.sum(min_count=1) if values.notna().any() else None
                )

            # Product-build quantities are additive across grouped APS
            # payloads. Lane grades remain intensive and use the exact grade
            # weight emitted by the optimiser for that lane/analyte.
            for lane in dict.fromkeys([PRODUCT_LANE, *BYPRODUCT_LANES, *[normalized_build_lane(s, bool(getattr(self, 'byproducts_enabled', False))) for s in getattr(self, "product_build_settings", [])]]):
                tonnes_column = lane_source_tonnes_column(lane)
                if tonnes_column not in group.columns:
                    continue
                tonnes = pd.to_numeric(
                    group[tonnes_column], errors="coerce"
                )
                record[tonnes_column] = (
                    tonnes.sum(min_count=1)
                    if tonnes.notna().any() else None
                )
                for grade in ("fe", "si", "al", "p", "mn"):
                    grade_column = lane_grade_column(lane, grade)
                    weight_column = lane_grade_weight_column(lane, grade)
                    if weight_column in group.columns:
                        weights = pd.to_numeric(
                            group[weight_column], errors="coerce"
                        )
                        record[weight_column] = (
                            weights.sum(min_count=1)
                            if weights.notna().any() else None
                        )
                    if grade_column not in group.columns:
                        continue
                    grades = pd.to_numeric(
                        group[grade_column], errors="coerce"
                    )
                    weights = pd.to_numeric(
                        group.get(weight_column, group.get(tonnes_column)),
                        errors="coerce",
                    )
                    valid = grades.notna() & weights.notna() & (
                        weights > Optimizer.SOLUTION_TOLERANCE
                    )
                    total_weight = weights[valid].sum()
                    record[grade_column] = (
                        (grades[valid] * weights[valid]).sum()
                        / total_weight
                        if total_weight > Optimizer.SOLUTION_TOLERANCE
                        else None
                    )

            grouped_records.append(record)

        grouped_rows = pd.DataFrame(grouped_records)
        grouped_data = pd.concat([other_rows, grouped_rows], ignore_index=True)
        sort_columns = [
            column for column in ["steady_state_number", "blend_option", "source_type", "source"]
            if column in grouped_data.columns
        ]
        if sort_columns:
            grouped_data = grouped_data.sort_values(sort_columns, kind="stable").reset_index(drop=True)
        return grouped_data

    def register_optimization_diagnostic(self, result, events, message):
        diagnostics = dict(result.get("diagnostics") or {})
        steady_state_duration = diagnostics.get(
            "steady_state_duration",
            result.get("steady_state_duration", self.calculate_initial_steady_state_duration()),
        )
        diagnostics.update(
            {
                "message": message,
                "steady_state_number": self.steady_state_tracker,
                "period": self.period_tracker,
                "start_datetime": self.current_time,
                "end_datetime": self.current_time + timedelta(hours=float(steady_state_duration or 0)),
                "blend_option": self.blend_option,
                "event_count": len(events),
            }
        )
        self.optimization_diagnostics.append(diagnostics)
        print(self.format_optimization_diagnostic(diagnostics))

    @staticmethod
    def format_optimization_diagnostic(diagnostics):
        """Return a concise, actionable Decision Point diagnostic."""
        lines = [
            "Optimisation diagnostic:",
            f"  Solver status: {diagnostics.get('solver_status', 'unknown')}",
            (
                "  Sources: "
                f"{diagnostics.get('positive_source_count', 0)} positive "
                f"({diagnostics.get('positive_stockpile_count', 0)} stockpile, "
                f"{diagnostics.get('positive_grade_block_count', 0)} direct-tip)"
            ),
            (
                "  Crusher: "
                f"{float(diagnostics.get('crusher_rate') or 0):,.0f} t/h; "
                f"window target {float(diagnostics.get('target_tonnes') or 0):,.0f} t"
            ),
            (
                "  Direct Tip Ratio submitted to solver: "
                f"{float(diagnostics.get('direct_feed_ratio_min') or 0):g} to "
                f"{float(diagnostics.get('direct_feed_ratio_max') if diagnostics.get('direct_feed_ratio_max') is not None else 1):g}"
            ),
            (
                "  Stockpile selection: "
                f"minimum {diagnostics.get('min_stockpiles')}, "
                f"maximum {diagnostics.get('max_stockpiles')}, "
                f"minimum contribution "
                f"{float(diagnostics.get('min_stockpile_contribution_ratio') or 0):g}"
            ),
        ]
        for build in diagnostics.get("product_build_targets") or []:
            bounds = []
            for grade, values in (build.get("grade_targets") or {}).items():
                if build.get("target_mode") == "soft":
                    bounds.append(f"{grade} Target {values.get('target')}; {quality_label(grade, 'lql')}/{quality_label(grade, 'hql')} {values.get('lql')}/{values.get('hql')} ({values.get('limit_mode')})")
                else:
                    bounds.append(
                    f"{grade} {float(values.get('target_min') or 0):g}-"
                    f"{float(values.get('target_max') if values.get('target_max') is not None else 100):g}"
                    )
            lines.append(
                f"  Product build {build.get('name')} [{build.get('brand') or 'unbranded'}; {build.get('target_mode', 'hard')}]: "
                + ", ".join(bounds)
            )
        if diagnostics.get("product_quality"):
            lines.append(f"  Applied soft-grade penalty: {diagnostics.get('applied_soft_grade_penalty', 0):,.3f}; "
                         f"source similarity penalty minus reward: {diagnostics.get('applied_source_similarity_penalty', 0):,.3f}.")
            for row in diagnostics["product_quality"]:
                if row["target_mode"] == "soft" and row["actual_grade"] is not None:
                    lines.append(f"  {row['lane']} {row['analyte'].title()} ({row['evaluation_basis']}): "
                                 f"actual {row['actual_grade']:.5g}; Target {row['target']}; deviation {row['target_deviation']}; "
                                 f"below {quality_label(row['analyte'], 'lql')} {row['below_lql']:.5g}; above {quality_label(row['analyte'], 'hql')} {row['above_hql']:.5g}; "
                                 f"limits {row['limit_mode']}; applied penalty {row['applied_penalty']:,.3f}.")
        for custom in diagnostics.get("custom_constraint_ranges") or []:
            lines.append(
                f"  Custom constraint {custom.get('name')}: "
                f"{custom.get('target_min')} to {custom.get('target_max')}"
            )
        causes = diagnostics.get("likely_causes") or []
        if causes:
            lines.append("  Likely causes / useful changes:")
            lines.extend(f"    - {cause}" for cause in causes[:10])
        rejections = diagnostics.get("guardrail_rejections") or []
        if rejections:
            lines.append("  Post-solve guardrail rejections:")
            lines.extend(f"    - {reason}" for reason in rejections[:10])
        return "\n".join(lines)

    def record_no_selected_blend(
        self,
        result,
        events,
        period_crusher_target,
        steady_state_duration,
        message,
        solver_config,
    ):
        """Record a no-blend row when all solver candidates were rejected before reporting."""
        if result is None:
            result = {
                "steady_state_duration": steady_state_duration,
                "diagnostics": Optimizer.build_diagnostics(
                    events,
                    period_crusher_target,
                    steady_state_duration,
                    self.periods,
                    self.period_tracker,
                    self.min_stockpiles,
                    self.max_stockpiles,
                    self.min_stockpile_contribution_ratio,
                    [],
                    message,
                    0,
                    solver_config,
                ),
            }
        else:
            result = dict(result)
            result.setdefault("steady_state_duration", steady_state_duration)
            diagnostics = dict(result.get("diagnostics") or {})
            diagnostics["solver_status"] = message
            likely_causes = list(diagnostics.get("likely_causes") or [])
            if message not in likely_causes:
                likely_causes.append(message)
            diagnostics["likely_causes"] = likely_causes
            result["diagnostics"] = diagnostics

        result["Linprog_result_object"] = SimpleNamespace(
            success=False,
            status=message,
            status_code=getattr(result.get("Linprog_result_object"), "status_code", None),
        )
        self.register_optimization_diagnostic(result, events, message)

        stored_blend_option = self.blend_option
        self.blend_option = "No blend found"
        self.record_results(result)
        self.blend_option = stored_blend_option

    def latest_result_duration(self, default_duration=None):
        if (
            self.results is not None
            and not self.results.empty
            and "steady_state_duration" in self.results.columns
        ):
            durations = pd.to_numeric(
                self.results["steady_state_duration"],
                errors="coerce",
            ).dropna()
            if not durations.empty:
                return max(float(durations.iloc[-1]), 0.0)

        if default_duration is None:
            default_duration = self.calculate_initial_steady_state_duration()
        try:
            return max(float(default_duration or 0), 0.0)
        except (TypeError, ValueError):
            return 0.0

    def advance_time(self):
        """Advance current time and update period if needed."""
        steady_state_duration = self.latest_result_duration()
        if steady_state_duration <= Optimizer.SOLUTION_TOLERANCE:
            raise ValueError("Steady state duration is zero; cannot advance optimisation time.")
        self.current_time += timedelta(hours=steady_state_duration)

        # A shortened steady state can land exactly on any configured
        # period boundary, so resolve the tracker from the shared calendar.
        active_period = self.period_for_time(self.current_time)
        if active_period is not None:
            self.period_tracker = active_period

    def record_results(self, result):
        """Record results from an optimization run into the main DataFrame."""
        two_wp_active_blend_fields = (
            self.two_wp_active_blend_report_fields(result)
        )
        if not result['Linprog_result_object'].success:
            failed_blend_label = (
                self.blend_option
                if isinstance(self.blend_option, str)
                else "No blend selected"
            )
            report_data = [
                {
                    "start_datetime": self.current_time,
                    "end_datetime": self.current_time + timedelta(hours=result["steady_state_duration"]),
                    "steady_state_number": self.steady_state_tracker,
                    "blend_option": failed_blend_label,
                    "blend_ID": "No blend selected",
                    "solver_score": result.get("solver_score", ""),
                    "steady_state_duration": result["steady_state_duration"],
                    "period": self.period_tracker,
                    "actual_direct_tip_ratio": 0,
                    **two_wp_active_blend_fields,
                    "source": "",
                    "source_id": "",
                    "source_type": "",
                    "estimated_delivery_datetime": "",
                    "source_blend_ratio": "No tonnes selected",
                    "source_opening_balance": "",
                    "source_actual_tonnes": "No tonnes selected",
                    "source_closing_balance": "",
                    "source_grade_fe": "",
                    "source_grade_si": "",
                    "source_grade_al": "",
                    "source_grade_p": "",
                    "source_grade_mn": "",
                    "equipment": "",
                    "equipment_rate_input": "",
                    "equipment_rate_output": "",
                    "crusher_actual_tonnes": "No crusher feed in steady state",
                    "crusher_rate_input": "",
                    "crusher_rate_output": "",
                    "crusher_actual_grade_fe": "",
                    "crusher_actual_grade_si": "",
                    "crusher_actual_grade_al": "",
                    "crusher_actual_grade_p": "",
                    "crusher_actual_grade_mn": "",
                    "crusher_grade_target_min_fe": "",
                    "crusher_grade_target_max_fe": "",
                    "crusher_grade_target_min_si": "",
                    "crusher_grade_target_max_si": "",
                    "crusher_grade_target_min_al": "",
                    "crusher_grade_target_max_al": "",
                    "crusher_grade_target_min_p": "",
                    "crusher_grade_target_max_p": "",
                    "crusher_grade_target_min_mn": "",
                    "crusher_grade_target_max_mn": ""
                }
            ]
        else:
            crusher_actual_tonnes = float(result.get("crusher_actual_tonnes") or 0)
            direct_tip_tonnes = sum(
                float(transaction.get("actual_tonnes") or 0)
                for transaction in result["transactions"]
                if transaction.get("source_type") == "grade_block"
            )
            actual_direct_tip_ratio = (
                direct_tip_tonnes / crusher_actual_tonnes
                if crusher_actual_tonnes > Optimizer.SOLUTION_TOLERANCE
                else 0
            )
            custom_constraint_result_fields = {
                key: value
                for key, value in result.items()
                if str(key).startswith("custom_constraint_")
            }
            report_data = [
                {
                    "start_datetime": self.current_time,
                    "end_datetime": self.current_time + timedelta(hours=result["steady_state_duration"]),
                    "steady_state_number": self.steady_state_tracker,
                    "blend_option": self.blend_option,
                    "blend_ID": self.blend_ID,
                    "solver_score": result.get("solver_score", ""),
                    "steady_state_duration": result["steady_state_duration"],
                    "period": self.period_tracker,
                    "actual_direct_tip_ratio": actual_direct_tip_ratio,
                    **two_wp_active_blend_fields,
                    "source": transaction["source"],
                    "source_id": transaction.get("source_id", transaction["source"]),
                    "parent_stockpile": transaction.get("parent_stockpile", ""),
                    "source_type": transaction.get("source_type", ""),
                    "estimated_delivery_datetime": transaction.get("estimated_delivery_datetime", ""),
                    "source_blend_ratio": round(transaction["equipment_rate_output"] / result["crusher_rate_output"], 2) if result["crusher_rate_output"] != 0 else 0,
                    # AMT transactions are reported at active-chunk level.
                    # The solver opening balance is therefore the correct
                    # comparable balance for every source type.
                    "source_opening_balance": transaction["opening_balance"],
                    "source_actual_tonnes": transaction["actual_tonnes"],
                    "source_closing_balance": transaction["opening_balance"] - transaction["actual_tonnes"],
                    "source_grade_fe": transaction["grade_fe"],
                    "source_grade_si": transaction["grade_si"],
                    "source_grade_al": transaction["grade_al"],
                    "source_grade_p": transaction["grade_p"],
                    "source_grade_mn": transaction["grade_mn"],
                    **{key: value for key, value in transaction.items()
                       if str(key).startswith("selected_grade_weight_")},
                    "selected_grade_stream": transaction.get("selected_grade_stream", ""),
                    "selected_grade_brand": transaction.get("selected_grade_brand", ""),
                    "grade_stream_warnings": str(transaction.get("grade_stream_warnings") or ""),
                    **grade_stream_audit_fields(
                        transaction.get("grade_streams"),
                        transaction.get("selected_grade_brand"),
                        prefix="source_grade_",
                    ),
                    **custom_constraint_result_fields,
                    **{
                        key: value
                        for key, value in transaction.items()
                        if str(key).startswith("custom_constraint_")
                    },
                    **{
                        key: value
                        for key, value in transaction.items()
                        if str(key).startswith("source_property_")
                    },
                    # Product-build lane quantities, grades and grade weights
                    # are calculated on the optimiser transaction.  Preserve
                    # them on the decision-point rows because the selected
                    # rows are also the authoritative input to the runtime
                    # build-balance tracker.
                    **{
                        key: value
                        for key, value in transaction.items()
                        if str(key).startswith("product_build_")
                    },
                    **{
                        key: value
                        for key, value in transaction.items()
                        if str(key).startswith(
                            "two_wp_destination_turnover_"
                        )
                        or key in {
                            "two_wp_planned_stockpile_destination",
                            "two_wp_first_reclaim_datetime",
                        }
                    },
                    "equipment": transaction["equipment"],
                    "equipment_rate_input": transaction["equipment_rate_input"],
                    "equipment_rate_output": transaction["equipment_rate_output"],
                    "crusher_actual_tonnes": result["crusher_actual_tonnes"],
                    "crusher_rate_input": result["crusher_rate_input"],
                    "crusher_rate_output": result["crusher_rate_output"],
                    "crusher_actual_grade_fe": 0 if self.blend_option == "No blend" else result["crusher_actual_grade_fe"],
                    "crusher_actual_grade_si": 0 if self.blend_option == "No blend" else result["crusher_actual_grade_si"],
                    "crusher_actual_grade_al": 0 if self.blend_option == "No blend" else result["crusher_actual_grade_al"],
                    "crusher_actual_grade_p": 0 if self.blend_option == "No blend" else result["crusher_actual_grade_p"],
                    "crusher_actual_grade_mn": 0 if self.blend_option == "No blend" else result["crusher_actual_grade_mn"],
                    "crusher_grade_target_min_fe": result["crusher_grade_target_min_fe"],
                    "crusher_grade_target_max_fe": result["crusher_grade_target_max_fe"],
                    "crusher_grade_target_min_si": result["crusher_grade_target_min_si"],
                    "crusher_grade_target_max_si": result["crusher_grade_target_max_si"],
                    "crusher_grade_target_min_al": result["crusher_grade_target_min_al"],
                    "crusher_grade_target_max_al": result["crusher_grade_target_max_al"],
                    "crusher_grade_target_min_p": result["crusher_grade_target_min_p"],
                    "crusher_grade_target_max_p": result["crusher_grade_target_max_p"],
                    "crusher_grade_target_min_mn": result["crusher_grade_target_min_mn"],
                    "crusher_grade_target_max_mn": result["crusher_grade_target_max_mn"]
                }
                for transaction in result["transactions"]
            ]

        if result.get("tipping_point_results") and result["Linprog_result_object"].success:
            for row, transaction in zip(report_data, result["transactions"]):
                point = transaction["tipping_point"]
                local = result["tipping_point_results"][point]
                row.update(tipping_point=point, opf=transaction["opf"])
                row.update({key: transaction.get(key, "") for key in ("reconciliation_opf", "reconciliation_scenario")})
                row.update({key: value for key, value in local.items() if key.startswith("crusher_")})
                total = sum(float(t.get("actual_tonnes") or 0) for t in local.get("transactions", []))
                direct = sum(float(t.get("actual_tonnes") or 0) for t in local.get("transactions", []) if t.get("source_type") == "grade_block")
                row["source_blend_ratio"] = float(transaction["actual_tonnes"]) / total if total else 0.0
                row["actual_direct_tip_ratio"] = direct / total if total else 0.0
        self.decision_point_results = pd.concat([self.decision_point_results, pd.DataFrame(report_data)], ignore_index=True)

    def append_results(self, filtered_decision_point_results_to_user_choice):
        """Append filtered results to the main DataFrame."""
        self.results = pd.concat([self.results, pd.DataFrame(filtered_decision_point_results_to_user_choice)], ignore_index=True)
    
    def save_optimised_blend_report(self):
        """Save results to an Excel file."""
        # Ensure numeric columns for comparison
        if (
            "source_actual_tonnes" in self.results
            and "crusher_actual_tonnes" in self.results
        ):
            self.results["source_actual_tonnes"] = pd.to_numeric(
                self.results["source_actual_tonnes"], errors="coerce"
            )
            self.results["crusher_actual_tonnes"] = pd.to_numeric(
                self.results["crusher_actual_tonnes"], errors="coerce"
            )

            self.results = self.results.loc[
                (
                    (self.results["source_actual_tonnes"] != 0)
                    & (self.results["crusher_actual_tonnes"] != 0)
                )
                |
                (
                    (self.results["source_actual_tonnes"] == 0)
                    & (self.results["crusher_actual_tonnes"] == 0)
                )
            ]

        #self.results.to_excel(filename, index=False)
        #print(f"All results written to {filename}")

        report_results = ProductBuildProgress.annotate(
            self.results, self.product_build_settings, solver_config=getattr(self, "solver_config", {})
        )
        report_results = self.group_grade_block_rows(report_results)
        self.database_manager.write_optimised_blend_report_to_database(report_results, self.periods)

    def product_build_grade_on_spec(self, build_state, build_setting):
        if target_mode_fields(build_setting)["target_mode"] == "soft":
            return ProductBuildProgress._is_on_spec(
                build_state["tonnes"],
                {a: build_state.get(f"grade_{a}_metal", 0) for a in ANALYTES},
                build_setting,
                {a: build_state.get(f"grade_{a}_weight", build_state["tonnes"]) for a in ANALYTES},
            )
        if build_state["tonnes"] <= Optimizer.SOLUTION_TOLERANCE:
            return False
        for grade in ["fe", "si", "al", "p", "mn"]:
            grade_weight = float(
                build_state.get(f"grade_{grade}_weight", build_state["tonnes"])
            )
            if grade_weight <= Optimizer.SOLUTION_TOLERANCE:
                return False
            grade_value = build_state[f"grade_{grade}_metal"] / grade_weight
            if (
                grade_value < build_setting[f"target_{grade}_min"]
                or grade_value > build_setting[f"target_{grade}_max"]
            ):
                return False
        return True

    def build_product_build_report(self):
        columns = [
            "tipping_point", "contributing_opf",
            "product_build_id",
            "product_build_name",
            "product_build_lane",
            "brand",
            "target_tonnes",
            "build_opening_tonnes",
            "build_added_tonnes",
            "build_closing_tonnes",
            "build_complete",
            "build_on_spec",
            "build_grade_fe",
            "build_grade_si",
            "build_grade_al",
            "build_grade_p",
            "build_grade_mn",
            "steady_state_number",
            "steady_state_start_datetime",
            "steady_state_end_datetime",
            "steady_state_duration",
            "period",
            "blend_ID",
            "blend_option",
            "source",
            "source_id",
            "source_type",
            "source_blend_ratio",
            "source_actual_tonnes",
            "source_actual_tonnes_to_build",
            "source_grade_fe",
            "source_grade_si",
            "source_grade_al",
            "source_grade_p",
            "source_grade_mn",
            "crusher_actual_tonnes",
            "crusher_rate_output",
            "crusher_actual_grade_fe",
            "crusher_actual_grade_si",
            "crusher_actual_grade_al",
            "crusher_actual_grade_p",
            "crusher_actual_grade_mn",
            "target_fe_min",
            "target_fe_max",
            "target_si_min",
            "target_si_max",
            "target_al_min",
            "target_al_max",
            "target_p_min",
            "target_p_max",
            "target_mn_min",
            "target_mn_max",
            "opf",
            *QUALITY_FIELDS,
            *QUALITY_REPORT_SUFFIXES,
            *[key for key in TARGET_MODE_FIELDS if key not in QUALITY_REPORT_SUFFIXES],
        ]
        if not self.product_build_settings or self.results is None or self.results.empty:
            return pd.DataFrame(columns=columns)

        data = self.group_grade_block_rows(ProductBuildProgress.annotate(
            self.results, self.product_build_settings, solver_config=getattr(self, "solver_config", {})
        )).copy()
        data["source_actual_tonnes"] = pd.to_numeric(data.get("source_actual_tonnes"), errors="coerce").fillna(0)
        data["crusher_actual_tonnes"] = pd.to_numeric(data.get("crusher_actual_tonnes"), errors="coerce").fillna(0)
        data = data[
            (data["source_actual_tonnes"] > Optimizer.SOLUTION_TOLERANCE)
            & (data["crusher_actual_tonnes"] > Optimizer.SOLUTION_TOLERANCE)
        ].copy()
        if data.empty:
            return pd.DataFrame(columns=columns)

        data["start_datetime"] = pd.to_datetime(data["start_datetime"], errors="coerce")
        data = data.sort_values(["start_datetime", "steady_state_number", "blend_ID", "source"], kind="stable")

        records = []
        group_keys = ["steady_state_number", "blend_ID", "blend_option"]

        lanes = list(dict.fromkeys(normalized_build_lane(s, bool(getattr(self, 'byproducts_enabled', False))) for s in self.product_build_settings))
        for lane in lanes:
            lane_settings = [
                setting for setting in self.product_build_settings
                if normalized_build_lane(
                    setting,
                    bool(getattr(self, "byproducts_enabled", False)),
                ) == lane
            ]
            tonnes_column = lane_source_tonnes_column(lane)
            if not lane_settings or tonnes_column not in data.columns:
                continue

            lane_data = data.copy()
            lane_data[tonnes_column] = pd.to_numeric(
                lane_data[tonnes_column], errors="coerce"
            ).fillna(0)
            build_states = [
                {
                    "tonnes": 0.0,
                    **{
                        f"grade_{grade}_{suffix}": 0.0
                        for grade in ("fe", "si", "al", "p", "mn")
                        for suffix in ("metal", "weight")
                    },
                }
                for _ in lane_settings
            ]
            active_build_index = 0

            for _, steady_state_group in lane_data.groupby(
                group_keys, sort=False, dropna=False
            ):
                if active_build_index >= len(lane_settings):
                    break

                product_tonnes = float(
                    steady_state_group[tonnes_column].sum() or 0
                )
                if product_tonnes <= Optimizer.SOLUTION_TOLERANCE:
                    continue

                build_setting = lane_settings[active_build_index]
                build_state = build_states[active_build_index]
                build_opening = build_state["tonnes"]
                build_capacity = (
                    build_setting["target_tonnes"] - build_opening
                )
                if build_capacity <= self.PRODUCT_BUILD_TONNES_TOLERANCE:
                    active_build_index += 1
                    continue

                allocation_tonnes = min(product_tonnes, build_capacity)
                allocation_fraction = allocation_tonnes / product_tonnes
                lane_record_start = len(records)
                crusher_tonnes = float(
                    steady_state_group["crusher_actual_tonnes"].iloc[0] or 0
                )

                for _, row in steady_state_group.iterrows():
                    source_tonnes = float(
                        row.get("source_actual_tonnes") or 0
                    )
                    source_to_build = float(
                        row.get(tonnes_column) or 0
                    ) * allocation_fraction
                    if source_to_build <= Optimizer.SOLUTION_TOLERANCE:
                        continue
                    source_grades = {}
                    for grade in ("fe", "si", "al", "p", "mn"):
                        grade_column = lane_grade_column(lane, grade)
                        weight_column = lane_grade_weight_column(lane, grade)
                        grade_value = float(row.get(grade_column) or 0)
                        raw_weight = row.get(weight_column)
                        grade_weight = float(
                            row.get(tonnes_column) or 0
                            if pd.isna(raw_weight) or raw_weight is None
                            else raw_weight
                        ) * allocation_fraction
                        build_state[f"grade_{grade}_metal"] += (
                            grade_weight * grade_value
                        )
                        build_state[f"grade_{grade}_weight"] += grade_weight
                        source_grades[f"source_grade_{grade}"] = grade_value

                    records.append({
                        "tipping_point": row.get("tipping_point", ""),
                        "contributing_opf": row.get("opf", ""),
                        "product_build_id": build_setting["build_id"],
                        **{key: row.get(f"product_build_{lane + '_' if lane != PRODUCT_LANE else ''}{key}")
                           for key in QUALITY_REPORT_SUFFIXES},
                        "product_build_name": build_setting["build_name"],
                        "product_build_lane": lane,
                        "brand": build_setting["brand"],
                        "target_tonnes": build_setting["target_tonnes"],
                        "build_opening_tonnes": build_opening,
                        "build_added_tonnes": allocation_tonnes,
                        "build_closing_tonnes": min(
                            build_opening + allocation_tonnes,
                            build_setting["target_tonnes"],
                        ),
                        "build_complete": (
                            build_opening + allocation_tonnes
                            >= build_setting["target_tonnes"]
                            - self.PRODUCT_BUILD_TONNES_TOLERANCE
                        ),
                        "build_on_spec": False,
                        "opf": build_setting.get("opf", ""),
                        **quality_fields(build_setting),
                        **target_mode_fields(build_setting),
                        **{
                            f"build_grade_{grade}": 0
                            for grade in ("fe", "si", "al", "p", "mn")
                        },
                        "steady_state_number": row.get("steady_state_number"),
                        "steady_state_start_datetime": row.get("start_datetime"),
                        "steady_state_end_datetime": row.get("end_datetime"),
                        "steady_state_duration": row.get("steady_state_duration"),
                        "period": row.get("period"),
                        "blend_ID": row.get("blend_ID"),
                        "blend_option": row.get("blend_option"),
                        "source": row.get("source"),
                        "source_id": row.get("source_id", row.get("source")),
                        "source_type": row.get("source_type", ""),
                        "source_blend_ratio": row.get("source_blend_ratio"),
                        "source_actual_tonnes": source_tonnes,
                        "source_actual_tonnes_to_build": source_to_build,
                        **source_grades,
                        "crusher_actual_tonnes": crusher_tonnes,
                        "crusher_rate_output": row.get("crusher_rate_output"),
                        **{
                            f"crusher_actual_grade_{grade}": row.get(
                                f"crusher_actual_grade_{grade}"
                            )
                            for grade in ("fe", "si", "al", "p", "mn")
                        },
                        **{
                            f"target_{grade}_{bound}": build_setting[
                                f"target_{grade}_{bound}"
                            ]
                            for grade in ("fe", "si", "al", "p", "mn")
                            for bound in ("min", "max")
                        },
                    })

                build_state["tonnes"] += allocation_tonnes
                build_complete = (
                    build_state["tonnes"]
                    >= build_setting["target_tonnes"]
                    - self.PRODUCT_BUILD_TONNES_TOLERANCE
                )
                build_on_spec = (
                    self.product_build_grade_on_spec(
                        build_state, build_setting
                    )
                    if build_complete else False
                )
                build_grades = {
                    f"build_grade_{grade}": (
                        build_state[f"grade_{grade}_metal"]
                        / build_state[f"grade_{grade}_weight"]
                        if build_state[f"grade_{grade}_weight"]
                        > Optimizer.SOLUTION_TOLERANCE
                        else 0
                    )
                    for grade in ("fe", "si", "al", "p", "mn")
                }
                for record in records[lane_record_start:]:
                    record.update(build_grades)
                    record["build_on_spec"] = build_on_spec
                if build_complete:
                    active_build_index += 1

        return pd.DataFrame(records, columns=columns)

    def save_product_build_report(self):
        self.database_manager.write_product_build_report_to_database(
            self.build_product_build_report()
        )

    def save_build_report(self):
        """Save stockpile build report to an Excel file."""
        self.build_report = self.balance_tracker.get_build_transactions()

        #self.build_report.to_excel(filename, index=False)
        #print(f"All results written to {filename}")

        self.database_manager.write_build_report_to_database(self.build_report)
       
    def calculate_initial_steady_state_duration(self):
        """Calculate initial steady state duration based on the current time and periods."""
        period_key = self.period_for_time(self.current_time)
        if period_key is None:
            return 0
        return (
            self.periods.get_periods()[f"{period_key}_end"]
            - self.current_time
        ).total_seconds() / 3600
