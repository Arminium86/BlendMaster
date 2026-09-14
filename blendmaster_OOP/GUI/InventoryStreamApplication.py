"""Apply grade mappings/reconciliation to a worker-owned inventory snapshot."""
from copy import deepcopy
import inspect
from classes.PlanningPersistence import PLAN_FIELDS

FIELDS = (
    'grade_reconciliation_registry', 'grade_reconciliation_policy_revision', 'transport_opening_history',
    'stockpile_data', 'updated_stockpile_data', 'stockpile_data_use_column', 'stockpile_data_AMT_column',
    'AMT_stockpile_data', 'AMT_footprint_exclusions', 'AMT_data_request_signature', 'AMT_enrichment_signature',
    'field_definitions', 'field_mappings', 'field_mapping_schema_version', 'mine_input_choice', 'opf_input_choice',
    'hub_input_choice', 'crusher_input_choice', 'selected_site_crushers', 'start_time_choice',
    'product_brand_labels_choice', 'historical_recon_factors', 'historical_recon_warnings',
    'reconciliation_inputs', 'reconciliation_settings', 'opf_reconciliation_inputs', 'multi_feed_configuration',
    'selected_data_stream', 'crusher_tonnes_stream', 'reclaimer_tonnes_stream', 'product_build_tonnes_stream',
    'cb_lump_fines_mode', 'cb_lump_percentage', 'cb_lump_fines_settings', 'byproducts_enabled',
    'byproduct_quantity_fields', 'byproduct_grade_fields', 'file_path_24hr_choice', 'aps_grade_field_mappings',
    'product_targets', 'calendar_inputs', 'data_stream_planning_categories',
)
FIELDS = tuple(dict.fromkeys((*FIELDS, *PLAN_FIELDS)))


class InventoryContext:
    def __init__(self, implementation, values):
        self._implementation = implementation
        self.__dict__.update(values)

    def __getattr__(self, name):
        # Only pure application methods and constants are accessible. No Qt
        # object is copied into this context or available to a worker.
        value = inspect.getattr_static(self._implementation, name)
        return value.__get__(self, self._implementation) if hasattr(value, '__get__') else value


def apply(host, on_complete):
    if vars(host).get('_data_stream_application_pending'):
        return
    host._data_stream_application_pending = True
    # Freeze references while controls are locked; copy the large AMT payload
    # on the worker so even the copy does not block painting/input feedback.
    values = {name: vars(host)[name] for name in FIELDS if name in vars(host)}
    implementation = type(host)
    def work():
        context = InventoryContext(implementation, deepcopy(values))
        context._reconciliation_application_cache = None  # Never inherit manual-search permission.
        context.apply_canonical_field_mappings()
        if (vars(context).get('multi_feed_configuration') or {}).get('mode') != 'combined_opf':
            context.apply_grade_streams_to_inventory()
        result = {name: vars(context).get(name) for name in ('stockpile_data', 'updated_stockpile_data',
            'historical_recon_warnings', 'data_stream_source_warnings', '_reconciliation_application_cache',
            'aps_grade_field_mappings', 'aps_source_property_field_mappings', 'field_mappings', 'field_definitions')}
        return result
    def unlock():
        host._data_stream_application_pending = False
    def finished(result):
        for name, value in result.items():
            setattr(host, name, value)
        unlock()
        host.prune_zeroed_amt_chunks()
        on_complete()
    def failed(error):
        unlock()
        host.show_error_popup(error)
    host.run_background_task('Saving approved reconciliation inputs…', work, finished, failed, readable_results=True)
