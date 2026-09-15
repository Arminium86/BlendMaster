"""Plan-scoped backup choices shared by both operational report pages."""
from copy import deepcopy
from contextlib import closing
import sqlite3
import pandas as pd
from classes.BlendPlanBackups import backup_choices
from classes.DestinationBuildOrder import inventory_areas
from classes.MultiFeedSettings import route_area_key
from database.DatabaseContext import get_database_path


def callbacks(host, plan_type):
    def context(data, sheets):
        plan_id = data.get('plan_id', 'Primary')
        points = list(dict.fromkeys(data.get('frames', {}).get('feed', pd.DataFrame()).get('tipping_point', [])))
        feed = host.current_multi_feed_configuration()
        if not points:
            points = [p['name'] for p in feed['tipping_points']] or [host.crusher_input_choice]
        from GUI.PlanReadiness import read_table
        with closing(sqlite3.connect(get_database_path())) as connection:
            rows = read_table(connection, 'material_destination_plan', plan_id, plan_type)
        areas = inventory_areas(getattr(host, 'stockpile_data', {}) or {})
        def matches(area, point):
            configured = next((p for p in feed['tipping_points'] if p['name'] == point), {})
            return route_area_key(area) == route_area_key(configured.get('rom_area', point))
        choices = backup_choices(points, rows.to_dict('records'), areas, matches)
        if plan_type == 'manual':
            state = (getattr(host, 'manual_plan_states', {}) or {}).get(plan_id) or {}
            selections = (getattr(host, 'blend_plan_backup_destinations', {}) if plan_id == getattr(host, 'active_manual_plan_id', 'Primary')
                          else state.get('blend_plan_backup_destinations')) or {}
        else:
            selections = (getattr(host, 'operational_plan_backups', {}) or {}).get(plan_id, {})
        return choices, deepcopy(selections)

    def save(plan_id, selections):
        if plan_type == 'manual':
            host.manual_plan_states = getattr(host, 'manual_plan_states', {}) or {}
            host.manual_plan_states.setdefault(plan_id, {})['blend_plan_backup_destinations'] = deepcopy(selections)
            if plan_id == getattr(host, 'active_manual_plan_id', 'Primary'):
                host.blend_plan_backup_destinations = deepcopy(selections)
        else:
            host.operational_plan_backups = getattr(host, 'operational_plan_backups', {}) or {}
            host.operational_plan_backups[plan_id] = deepcopy(selections)
        host.save_active_scenario_state()
    return context, save
