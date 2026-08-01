# This is where the main workflow is defined (everything happens here)
from classes.BalanceTracker import BalanceTracker
from classes.EventPoolGenerator import EventPoolGenerator
from classes.EquipmentData import EquipmentData
from classes.StockpileData import StockpileData
from classes.GradeBlockData import GradeBlockData
from classes.Optimizer import Optimizer
from classes.ProductBuildProgress import ProductBuildProgress
from classes.CrusherTarget import CrusherTarget
from classes.GradeStreams import grade_stream_audit_fields
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
    ):
        self.stockpiles = stockpiles
        self.grade_blocks = grade_blocks
        self.equipment = equipment
        self.crusher_targets = crusher_targets
        self.periods = periods
        self.current_time = periods.get_periods()["preplan_start"]
        self.start_time = periods.get_periods()["preplan_start"]
        self.period_tracker = "preplan"
        self.balance_tracker = BalanceTracker(stockpiles, grade_blocks, self.period_tracker, hex_sequence_table)
        self.expit_payload_transactions = expit_payload_transactions
        self.event_pool = EventPoolGenerator(stockpiles, grade_blocks, equipment)
        self.optimizer = Optimizer()
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
        self.solver_config = solver_config or {}
        self.plan_id = str(plan_id or "Primary")
        self.reserved_blend_signatures = {
            frozenset(signature)
            for signature in (reserved_blend_signatures or set())
        }
        self.selected_blend_signatures = set()
        self.contingency_reuse_fallbacks = 0
        self.product_build_settings = self.normalized_product_build_settings(product_build_settings)
        self.product_build_runtime_states = [
            {
                "tonnes": 0.0,
                "grade_fe_metal": 0.0,
                "grade_si_metal": 0.0,
                "grade_al_metal": 0.0,
                "grade_p_metal": 0.0,
                "grade_mn_metal": 0.0,
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
            normalized.append({
                "build_id": int(setting.get("build_id") or index + 1),
                "build_name": build_name,
                "brand": explicit_brand,
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
            })
        return normalized

    def current_product_build_index(self):
        for index, setting in enumerate(self.product_build_settings):
            state = self.product_build_runtime_states[index]
            if state["tonnes"] < setting["target_tonnes"] - self.PRODUCT_BUILD_TONNES_TOLERANCE:
                return index
        return None

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
                    transaction.get("source")
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
            return None

        data = selected_results.copy()
        if "source_actual_tonnes" not in data or "crusher_actual_tonnes" not in data:
            return None
        data["source_actual_tonnes"] = pd.to_numeric(data["source_actual_tonnes"], errors="coerce").fillna(0)
        data["crusher_actual_tonnes"] = pd.to_numeric(data["crusher_actual_tonnes"], errors="coerce").fillna(0)
        data = data[
            (data["source_actual_tonnes"] > Optimizer.SOLUTION_TOLERANCE)
            & (data["crusher_actual_tonnes"] > Optimizer.SOLUTION_TOLERANCE)
        ]
        if data.empty:
            return None

        crusher_tonnes = float(data["crusher_actual_tonnes"].iloc[0] or 0)
        build_index = self.current_product_build_index()
        if build_index is None:
            return None

        build_setting = self.product_build_settings[build_index]
        build_state = self.product_build_runtime_states[build_index]
        capacity = build_setting["target_tonnes"] - build_state["tonnes"]
        if capacity <= self.PRODUCT_BUILD_TONNES_TOLERANCE:
            return build_index

        allocation_tonnes = min(crusher_tonnes, capacity)
        allocation_fraction = allocation_tonnes / crusher_tonnes if crusher_tonnes else 0
        for _, row in data.iterrows():
            source_to_build = float(row.get("source_actual_tonnes") or 0) * allocation_fraction
            for grade in ["fe", "si", "al", "p", "mn"]:
                grade_value = float(row.get(f"source_grade_{grade}") or 0)
                build_state[f"grade_{grade}_metal"] += source_to_build * grade_value
        build_state["tonnes"] += allocation_tonnes
        if (
            build_state["tonnes"]
            >= build_setting["target_tonnes"] - self.PRODUCT_BUILD_TONNES_TOLERANCE
        ):
            return build_index
        return None

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

    def product_build_offspec_steady_states(self, build_index):
        candidate_states = set()
        if self.results is not None and not self.results.empty:
            report = ProductBuildProgress.annotate(
                self.group_grade_block_rows(self.results),
                self.product_build_settings,
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

            prior_builds_complete = all(
                runtime_states[index]["tonnes"]
                >= self.product_build_settings[index]["target_tonnes"]
                - self.PRODUCT_BUILD_TONNES_TOLERANCE
                for index in range(build_index)
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

        build_index = self.current_product_build_index()
        if build_index is None:
            return
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
                period_crusher_target.get("crusher_rate", 0.0),
                steady_state_duration,
            )
        )
        if not repair_constraint_active and not terminal_constraint_active:
            return

        self.request_product_build_repair(
            build_index,
            "A cumulative build-grade repair constraint was infeasible.",
        )

    def validate_completed_product_build(self, build_index):
        if build_index is None or not self.product_build_repair_enabled():
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
        if not hasattr(self, "product_build_hard_repair_from_states"):
            self.product_build_hard_repair_from_states = {}
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
        while self.current_time < self.planning_horizon_end():
            self.check_abort_requested()
            if self.product_build_settings and self.current_product_build_index() is None:
                raise ProductBuildCapacityComplete()
            if self.product_build_repair_enabled():
                repair_checkpoints[self.steady_state_tracker] = (
                    self.capture_product_build_repair_checkpoint()
                )
            # Run optimization and only advance time if successful
            try:
                self.run_optimization_step()
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
                    raise ProductBuildRepairFailed(
                        build_name,
                        (
                            f"{repair.reason} Cumulative repair and the final "
                            "all-states-on-spec fallback both failed across "
                            "the saved checkpoints for this build."
                        ),
                    )

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
        
        period_crusher_target = CrusherTarget(self.crusher_targets).get_targets(self.period_tracker)
        candidate_source_sets = []
        excluded_source_sets = []
        excluded_stockpile_sets = []
        candidate_source_signatures = set()
        required_min_feed_duration = self.configured_min_feed_duration_hours()
        blend_option_timeout_seconds = self.configured_blend_option_timeout_seconds()
        max_decision_blend_options = self.configured_max_decision_blend_options()
        step_solver_config = self.solver_config_for_current_step()
        enumerate_stockpile_mixes_only = (
            bool(self.reserved_blend_signatures)
            and self.configured_contingency_distinctness_mode()
            == self.CONTINGENCY_STOCKPILE_MIX_ONLY
        )
        last_solver_result = None
        no_selected_blend_message = "No feasible blend found."

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
                        print(
                            f"Rejected blend option {self.blend_option} because grade block pair "
                            f"duration is too short: {'; '.join(grade_block_duration_issues)}."
                        )
                        continue

                    potential_feed_duration = self.candidate_potential_feed_duration(result)
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

                if self.steady_state_tracker != 0:
                    if not list(current_filtered_sources) == list(previous_filtered_sources):
                        self.results.loc[self.results['blend_ID'] == self.blend_ID, 'blend_ID'] -= 1
                        self.blend_ID += 1
                    else: pass
                else: pass


            elif self.user_interaction_mode == 2 and self.steady_state_tracker != 0:
                
                if not list(current_filtered_sources) == list(previous_filtered_sources):
                    print("Blend fully depleted.")
                    self.user_blend_choice = input("Choose new blend: ")
                    self.results.loc[self.results['blend_ID'] == self.blend_ID, 'blend_ID'] -= 1
                    self.blend_ID += 1
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
                row.get("source")
                or row.get("source_id")
                or ""
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
        current_product_build_index = self.current_product_build_index()
        if current_product_build_index is not None:
            current_product_build = self.product_build_settings[current_product_build_index]
            solver_config["target_product_brand"] = current_product_build.get("brand", "")
            solver_config["target_product_build"] = dict(current_product_build)
            solver_config["target_product_build_state"] = dict(
                self.product_build_runtime_states[current_product_build_index]
            )
            solver_config["active_product_build_completes_within_horizon"] = (
                self.active_product_build_completes_within_horizon(
                    current_product_build_index
                )
            )
            repair_from_state = getattr(
                self, "product_build_repair_from_states", {}
            ).get(
                current_product_build_index
            )
            solver_config["enforce_cumulative_product_build_grade"] = (
                repair_from_state is not None
                and self.steady_state_tracker >= repair_from_state
            )
            hard_repair_from_state = getattr(
                self, "product_build_hard_repair_from_states", {}
            ).get(current_product_build_index)
            solver_config["force_product_build_state_grades_on_spec"] = (
                hard_repair_from_state is not None
                and self.steady_state_tracker >= hard_repair_from_state
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

    def active_product_build_completes_within_horizon(self, build_index):
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
            remaining_capacity += crusher_rate * hours

        return remaining_tonnes <= (
            remaining_capacity + self.PRODUCT_BUILD_TONNES_TOLERANCE
        )

    def configured_min_grade_block_pair_duration_hours(self):
        try:
            value = float(self.solver_config.get("min_grade_block_pair_duration_hours") or 0)
        except (TypeError, ValueError):
            return None
        return value if value > Optimizer.SOLUTION_TOLERANCE else None

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

        source_details = {}
        for transaction in result.get("transactions", []):
            if transaction.get("source_type") != "grade_block":
                continue
            try:
                actual_tonnes = float(transaction.get("actual_tonnes") or 0)
            except (TypeError, ValueError):
                actual_tonnes = 0
            if actual_tonnes <= Optimizer.SOLUTION_TOLERANCE:
                continue

            source = str(transaction.get("source") or transaction.get("source_id") or "")
            if not source:
                continue
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
        return set(str(value) for value in data[source_column].dropna() if str(value))

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
            source_id = transaction.get("source_id") or transaction.get("source")
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
            source = transaction.get("source") or transaction.get("source_id")
            if source:
                grade_block_sources.add(str(source))
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
            str(source): tuple(sorted(stockpile_ids))
            for source in grade_block_data["source"].dropna().unique()
            if str(source)
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
                    active_source_ids.append(transaction.get("source_id") or transaction.get("source"))
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

        for column in [
            "source_actual_tonnes",
            "crusher_actual_tonnes",
            "source_opening_balance",
            "source_closing_balance",
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
                    "source_type": transaction.get("source_type", ""),
                    "estimated_delivery_datetime": transaction.get("estimated_delivery_datetime", ""),
                    "source_blend_ratio": round(transaction["equipment_rate_output"] / result["crusher_rate_output"], 2) if result["crusher_rate_output"] != 0 else 0,
                    "source_opening_balance": self.total_AMT_stockpile_balances[transaction["source_id"]] if transaction.get("source_id") in self.total_AMT_stockpile_balances else transaction["opening_balance"],
                    "source_actual_tonnes": transaction["actual_tonnes"],
                    "source_closing_balance": (self.total_AMT_stockpile_balances[transaction["source_id"]] if transaction.get("source_id") in self.total_AMT_stockpile_balances else transaction["opening_balance"]) - transaction["actual_tonnes"],
                    "source_grade_fe": transaction["grade_fe"],
                    "source_grade_si": transaction["grade_si"],
                    "source_grade_al": transaction["grade_al"],
                    "source_grade_p": transaction["grade_p"],
                    "source_grade_mn": transaction["grade_mn"],
                    "selected_grade_stream": transaction.get("selected_grade_stream", ""),
                    "selected_grade_brand": transaction.get("selected_grade_brand", ""),
                    "grade_stream_warnings": str(transaction.get("grade_stream_warnings") or ""),
                    **grade_stream_audit_fields(
                        transaction.get("grade_streams"),
                        transaction.get("selected_grade_brand"),
                        prefix="source_grade_",
                    ),
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

        report_results = self.group_grade_block_rows(self.results)
        report_results = ProductBuildProgress.annotate(
            report_results, self.product_build_settings
        )
        self.database_manager.write_optimised_blend_report_to_database(report_results, self.periods)

    def product_build_grade_on_spec(self, build_state, build_setting):
        if build_state["tonnes"] <= Optimizer.SOLUTION_TOLERANCE:
            return False
        for grade in ["fe", "si", "al", "p", "mn"]:
            grade_value = build_state[f"grade_{grade}_metal"] / build_state["tonnes"]
            if (
                grade_value < build_setting[f"target_{grade}_min"]
                or grade_value > build_setting[f"target_{grade}_max"]
            ):
                return False
        return True

    def build_product_build_report(self):
        columns = [
            "product_build_id",
            "product_build_name",
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
        ]
        if not self.product_build_settings or self.results is None or self.results.empty:
            return pd.DataFrame(columns=columns)

        data = self.group_grade_block_rows(self.results).copy()
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

        build_states = [
            {
                "tonnes": 0.0,
                "grade_fe_metal": 0.0,
                "grade_si_metal": 0.0,
                "grade_al_metal": 0.0,
                "grade_p_metal": 0.0,
                "grade_mn_metal": 0.0,
            }
            for _ in self.product_build_settings
        ]
        active_build_index = 0
        records = []
        group_keys = ["steady_state_number", "blend_ID", "blend_option"]

        for _, steady_state_group in data.groupby(group_keys, sort=False, dropna=False):
            if active_build_index >= len(self.product_build_settings):
                break

            crusher_tonnes = float(steady_state_group["crusher_actual_tonnes"].iloc[0] or 0)
            if crusher_tonnes <= Optimizer.SOLUTION_TOLERANCE:
                continue

            build_setting = self.product_build_settings[active_build_index]
            build_state = build_states[active_build_index]
            build_opening = build_state["tonnes"]
            build_capacity = build_setting["target_tonnes"] - build_opening
            if build_capacity <= self.PRODUCT_BUILD_TONNES_TOLERANCE:
                active_build_index += 1
                continue

            allocation_tonnes = min(crusher_tonnes, build_capacity)
            allocation_fraction = allocation_tonnes / crusher_tonnes if crusher_tonnes else 0

            for _, row in steady_state_group.iterrows():
                source_tonnes = float(row.get("source_actual_tonnes") or 0)
                source_to_build = source_tonnes * allocation_fraction
                for grade in ["fe", "si", "al", "p", "mn"]:
                    grade_value = float(row.get(f"source_grade_{grade}") or 0)
                    build_state[f"grade_{grade}_metal"] += source_to_build * grade_value

                records.append({
                    "product_build_id": build_setting["build_id"],
                    "product_build_name": build_setting["build_name"],
                    "brand": build_setting["brand"],
                    "target_tonnes": build_setting["target_tonnes"],
                    "build_opening_tonnes": build_opening,
                    "build_added_tonnes": allocation_tonnes,
                    "build_closing_tonnes": min(build_opening + allocation_tonnes, build_setting["target_tonnes"]),
                    "build_complete": build_opening + allocation_tonnes >= build_setting["target_tonnes"] - self.PRODUCT_BUILD_TONNES_TOLERANCE,
                    "build_on_spec": False,
                    "build_grade_fe": 0,
                    "build_grade_si": 0,
                    "build_grade_al": 0,
                    "build_grade_p": 0,
                    "build_grade_mn": 0,
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
                    "source_grade_fe": row.get("source_grade_fe"),
                    "source_grade_si": row.get("source_grade_si"),
                    "source_grade_al": row.get("source_grade_al"),
                    "source_grade_p": row.get("source_grade_p"),
                    "source_grade_mn": row.get("source_grade_mn"),
                    "crusher_actual_tonnes": crusher_tonnes,
                    "crusher_rate_output": row.get("crusher_rate_output"),
                    "crusher_actual_grade_fe": row.get("crusher_actual_grade_fe"),
                    "crusher_actual_grade_si": row.get("crusher_actual_grade_si"),
                    "crusher_actual_grade_al": row.get("crusher_actual_grade_al"),
                    "crusher_actual_grade_p": row.get("crusher_actual_grade_p"),
                    "crusher_actual_grade_mn": row.get("crusher_actual_grade_mn"),
                    "target_fe_min": build_setting["target_fe_min"],
                    "target_fe_max": build_setting["target_fe_max"],
                    "target_si_min": build_setting["target_si_min"],
                    "target_si_max": build_setting["target_si_max"],
                    "target_al_min": build_setting["target_al_min"],
                    "target_al_max": build_setting["target_al_max"],
                    "target_p_min": build_setting["target_p_min"],
                    "target_p_max": build_setting["target_p_max"],
                    "target_mn_min": build_setting["target_mn_min"],
                    "target_mn_max": build_setting["target_mn_max"],
                })

            build_state["tonnes"] += allocation_tonnes
            build_complete = build_state["tonnes"] >= build_setting["target_tonnes"] - self.PRODUCT_BUILD_TONNES_TOLERANCE
            build_on_spec = self.product_build_grade_on_spec(build_state, build_setting) if build_complete else False
            build_grades = {
                f"build_grade_{grade}": (
                    build_state[f"grade_{grade}_metal"] / build_state["tonnes"]
                    if build_state["tonnes"] > Optimizer.SOLUTION_TOLERANCE
                    else 0
                )
                for grade in ["fe", "si", "al", "p", "mn"]
            }
            for record in records[-len(steady_state_group):]:
                if record["product_build_id"] == build_setting["build_id"]:
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
