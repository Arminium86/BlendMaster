"""Explain missing preparation before launching the existing optimisation."""
from classes.PlanningPersistence import settings_signature
from classes.SiteWorkflow import fingerprint
from classes.GuidanceImport import current_import_revisions


def input_revision(host):
    state = vars(host)
    # site_context contains derived report evidence, cached destination views
    # and UI-hydrated snapshots. Reopening a project changes those containers
    # without changing the actual planning inputs.
    calendar = {key: value for key, value in (state.get('calendar_inputs') or {}).items()
                if key != 'site_context'}
    # Manual rounding belongs to the independently generated manual plan.
    # Editing that policy must not invalidate a completed optimised plan.
    planning_settings = {**state, 'manual_ratio_rounding': None}
    return fingerprint(dict(schema=3, settings=settings_signature(planning_settings),
        site=state.get('active_scenario_id'), start=state.get('start_time_choice'),
        site_model={key: state.get(key) for key in ('hub_input_choice', 'mine_input_choice',
            'opf_input_choice', 'crusher_input_choice', 'selected_site_crushers')},
        periods=state.get('planning_period_count_choice'), calendar=calendar,
        inventory=state.get('inventory_data_request_signature'), amt=state.get('AMT_data_request_signature'),
        enrichment=state.get('AMT_enrichment_signature'),
        sources=state.get('stockpile_data_use_column'), agents=state.get('selected_24hr_expit_agents'),
        amt_sources=state.get('stockpile_data_AMT_column'),
        reconciliation=state.get('reconciliation_applied_revision'), factors=state.get('historical_recon_factors'),
        expit={key: state.get(key) for key in ('expit_mode_choice', 'expit_completion_tolerance_pct',
            'expit_refresh_tolerance_minutes', 'reevaluate_aps_direct_tip_choice', 'aps_direct_tip_crusher_choice',
            'selected_two_wp_product_crushers', 'selected_haul_cycle_crushers', 'haul_cycle_crusher_mapping_choice')},
        imports=current_import_revisions(state.get('guidance_import_audit'))))


def reconciliation_input_revision(host):
    """Track reconciliation inputs without treating generated chunks as edits."""
    state = vars(host)
    return fingerprint(dict(settings=settings_signature(state, evidence=True),
        reconciliation=state.get('reconciliation_settings'), factors=state.get('historical_recon_factors'),
        history=state.get('data_stream_input_cache_signature'), site=state.get('active_scenario_id'),
        start=state.get('start_time_choice'), opf=state.get('opf_input_choice'),
        inventory=state.get('inventory_data_request_signature'), amt=state.get('AMT_data_request_signature'),
        sources=state.get('stockpile_data_use_column'), amt_sources=state.get('stockpile_data_AMT_column'),
        exclusions=state.get('AMT_footprint_exclusions')))


def preparation_issues(host):
    issues = []
    if not host.included_stockpile_data():
        issues.append('Select and submit stockpile inventories.')
    if vars(host).get('reconciliation_applied_revision') != reconciliation_input_revision(host):
        issues.append('Refresh and review Grade Reconciliation for the current inputs.')
    if getattr(host, 'file_path_choice', '') and host.destination_allocation_context() is None:
        issues.append('Refresh Destination Reconciliation for the current site, start and guidance files.')
    try:
        host.validate_AMT_participation()
    except ValueError as exc:
        issues.append(str(exc))
    return issues


def manual_recipe_inputs(host):
    """Use the same current recipe for freshness and project persistence."""
    state = vars(host)
    current = state.get('blend_data_from_config_table_inputs')
    if (state.get('blend_config_table') is not None and isinstance(current, dict)
            and not state.get('project_load_restore_in_progress')
            and not state.get('scenario_switch_in_progress')):
        return current
    return state.get('blend_config_table_inputs')


def manual_revision(host):
    state = vars(host)
    sequence = [{key: value for key, value in row.items() if key != 'Blend Summary'}
                for row in state.get('stored_blend_sequence_table_for_gantt') or []]
    return fingerprint(dict(inputs=input_revision(host), sequence=sequence,
        blends=state.get('saved_blends_for_schedule'), allocations=state.get('manual_direct_tip_allocations'),
        recipes=manual_recipe_inputs(host), rates=state.get('crusher_rate_input_values'),
        rounding=state.get('manual_ratio_rounding')))


def manual_rate_issues(host):
    """Check configured manual crusher values before storing any blend recipes."""
    from classes.EquipmentLimits import number
    manual = host.manual_crusher_rate_values()
    calendar = (host.calendar_inputs or {}).get('crusher_rate') or {}
    errors = []
    for period, value in manual.items():
        limit = number(calendar.get(period))
        rate = number(value)
        if limit is None or rate is None or rate < 0 or rate > limit + max(1e-6, abs(limit) * 1e-7):
            errors.append(f'{period}: manual crusher rate {value} exceeds or lacks a valid Calendar limit ({calendar.get(period)}).')
    return errors
