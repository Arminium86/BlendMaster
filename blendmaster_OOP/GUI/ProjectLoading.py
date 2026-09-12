"""Prepare large saved projects without blocking the Qt event loop."""
from copy import deepcopy
from types import MethodType
import inspect
from pathlib import Path
from classes.PlanningPersistence import migrate_project_state
from classes.GuidanceImport import file_revision
from classes.HaulCycleDataHandler import HaulCycleDataHandler
from database.DatabaseContext import set_database_path, database_scope


class RestoreContext:
    """Only plain state and pure model helpers are available to the worker."""
    def __init__(self, host):
        self._host_type = type(host)
        self.active_scenario_id = host.active_scenario_id
        self.scenario_session_directory = host.scenario_session_directory

    def __getattr__(self, name):
        value = inspect.getattr_static(self._host_type, name)
        if isinstance(value, staticmethod):
            return value.__func__
        if isinstance(value, classmethod):
            return value.__get__(None, self._host_type)
        if callable(value):
            return MethodType(value, self)
        raise AttributeError(name)


def prepare(context, state):
    state = migrate_project_state(state)
    scenarios = state.get('site_scenarios') or {context.active_scenario_id: state}
    restored = {}
    for site, raw in scenarios.items():
        if not isinstance(raw, dict):
            raise ValueError('Each saved site must contain a model.')
        # Each site's independent evidence is copied once, in this worker.
        row = deepcopy({k: v for k, v in raw.items()
                        if k not in ('site_scenarios', 'database_snapshot')})
        row = context.normalized_agent_project_state(row)
        row['scenario_id'] = site
        row['database_path'] = context.scenario_database_path(site)
        guidance_path = row.get('file_path_choice')
        if guidance_path and Path(guidance_path).is_file():
            from GUI.GuidancePreparation import prepare as prepare_guidance, FIELDS
            row.update(prepare_guidance(context._host_type, {key: row.get(key) for key in FIELDS}))
        path = row.get('haul_cycle_file_path_choice')
        selected = row.get('selected_haul_cycle_crushers') or []
        if path and Path(path).is_file():
            row['haul_cycle_routes'] = HaulCycleDataHandler.build_nearest_crusher_routes(path, selected)
            row['destination_haul_routes'] = HaulCycleDataHandler.build_destination_routes(path)
            row['_haul_cycle_routes_revision'] = (file_revision(path), tuple(sorted(selected)))
        restored[site] = row
    context.normalize_loaded_scenario_ratio_groups(restored)
    active = state.get('active_scenario_id', context.active_scenario_id)
    if active not in restored:
        active = next(iter(restored))
    # Validate all models before restoring any database.
    for site, row in restored.items():
        context.restore_database_snapshot(row['database_path'], scenarios[site].get('database_snapshot'))
        if row.get('AMT_stockpile_data'):
            from setup.AMTSpatialReconciliation import guard_amt_snapshot
            from classes.AMTFootprintExclusions import included_footprints
            from setup.OpeningStockpileInventories import OpeningStockpileInventories
            row['AMT_stockpile_data'] = guard_amt_snapshot(
                included_footprints(row['AMT_stockpile_data'], row.get('AMT_footprint_exclusions')),
                copy_unchanged=False)
        if row.get('stockpile_data') and not row.get('project_load_refresh_current_time'):
            from GUI.InventoryStreamApplication import InventoryContext
            model = InventoryContext(context._host_type, row)
            for name, default in (('field_definitions', None), ('field_mappings', []),
                                  ('historical_recon_factors', {}), ('historical_recon_warnings', []),
                                  ('updated_stockpile_data', {})):
                if vars(model).get(name) is None:
                    setattr(model, name, default)
            model.apply_canonical_field_mappings()
            model.apply_grade_streams_to_inventory(allow_pending=True)
            row.update({key: value for key, value in vars(model).items() if key != '_implementation'})
            row['_project_load_fields_prepared'] = True
        if row.get('AMT_stockpile_data'):
            with database_scope(row['database_path']):
                OpeningStockpileInventories().save_AMT_to_database(row['AMT_stockpile_data'])
            row['_prepared_amt_database_path'] = row['database_path']
            from types import SimpleNamespace
            display = SimpleNamespace(opf_input_choice=row.get('opf_input_choice'))
            summary = context._host_type.amt_lineage_summary
            row['_amt_lineage_display'] = {name: (id(rows), display.opf_input_choice, summary(display, rows))
                                           for name, rows in row['AMT_stockpile_data'].items()}
    result = {k: v for k, v in state.items()
              if k not in ('site_scenarios', 'database_snapshot')}
    result.update(restored[active])
    return active, restored, result


def begin(host, state, source_label, *, show_success=True, ready=None, resolve_time=True):
    state = migrate_project_state(state)
    if resolve_time and not host.prompt_loaded_project_start_time(state):
        host.is_project_loaded = False
        return
    if not host.resolve_missing_aps_mining_csv_paths(state):
        host.is_project_loaded = False
        return
    context = RestoreContext(host)
    def done(result):
        active, scenarios, prepared = result
        host.active_scenario_id, host.site_scenarios = active, scenarios
        set_database_path(prepared['database_path'])
        host.restore_loaded_state(prepared, source_label=source_label,
                                  show_success=show_success, prepared=True)
        if ready:
            ready()
    host.run_background_task('Restoring saved site models…',
                             lambda: prepare(context, state), done,
                             host.handle_site_config_error)
