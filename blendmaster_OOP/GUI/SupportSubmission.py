"""Apply saved Support configuration at planner submission boundaries."""
from classes.ReconciliationApplication import reconciliation_fingerprint

FIELDS = ('active_scenario_id', 'mine_input_choice', 'opf_input_choice',
          'field_definitions', 'field_mappings', 'field_mapping_schema_version',
          'product_brand_labels_choice', 'selected_data_stream', 'crusher_tonnes_stream',
          'reclaimer_tonnes_stream', 'product_build_tonnes_stream', 'data_stream_planning_categories')


def prepare(host, task):
    state = vars(host)
    if state.get('access_role') != 'planner' or task not in ('grade_reconciliation', 'amt_stockpiles', 'calendar', 'blend_sequence'):
        return
    if state.get('field_definitions') is None or state.get('field_mappings') is None:
        return  # Legacy model migration remains owned by source preparation.
    signature = reconciliation_fingerprint({key: state.get(key) for key in FIELDS})
    if state.get('_submitted_support_signature') == signature:
        return
    from classes.FieldDefinitions import normalize_field_definitions, normalize_field_mappings, legacy_aps_mappings
    from classes.GradeStreams import (normalise_planning_categories, normalise_aps_grade_field_mappings,
                                      DEFAULT_STREAM)
    from classes.SourcePropertyMappings import normalise_aps_source_property_mappings
    definitions = normalize_field_definitions(state['field_definitions'])
    targets = {row['name'] for row in definitions}
    mappings = [row for row in normalize_field_mappings(state['field_mappings']) if row['target_field'] in targets]
    grades, properties = legacy_aps_mappings(definitions, mappings, state.get('product_brand_labels_choice'))
    state.update(field_definitions=definitions, field_mappings=mappings,
        data_stream_planning_categories=normalise_planning_categories(state.get('data_stream_planning_categories')),
        aps_grade_field_mappings=normalise_aps_grade_field_mappings(grades, state.get('product_brand_labels_choice')),
        aps_source_property_field_mappings=normalise_aps_source_property_mappings(properties))
    for name, default in (('selected_data_stream', DEFAULT_STREAM), ('crusher_tonnes_stream', 'modelled_rom_wmt'),
                          ('reclaimer_tonnes_stream', 'modelled_rom_wmt'), ('product_build_tonnes_stream', 'modelled_product_wmt')):
        state[name] = state.get(name) or default
    # Source application stays in the existing AMT/Calendar profile worker.
    state['_submitted_support_signature'] = reconciliation_fingerprint({key: state.get(key) for key in FIELDS})
