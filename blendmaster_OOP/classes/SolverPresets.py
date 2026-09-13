"""Portable solver preferences, separate from site mappings and Calendar rates."""
from copy import deepcopy
import json

VERSION = 1
COMPOSITION_FIELDS = ('min_stockpiles', 'max_stockpiles', 'min_stockpile_contribution_ratio')
SITE_FIELDS = frozenset({'optimisation_source_property_fields', 'source_property_kinds',
    'source_property_weights', 'crusher_tonnes_stream', 'reclaimer_tonnes_stream',
    'product_build_tonnes_stream', 'byproducts_enabled', 'byproduct_quantity_fields',
    'byproduct_grade_fields', 'multi_feed_settings', 'transport_settings', 'site_context',
    'selected_data_stream', 'configured_product_brands', 'opf_profiles', 'opf_property_descriptors',
    'continuous_assay_settings', 'continuous_assay_state', 'continuous_assay_opf', 'transport_history'})


def preferences(config):
    return deepcopy({k: v for k, v in (config or {}).items()
                     if k not in SITE_FIELDS and not k.startswith('_') and not callable(v)})


def make_preset(name, config, composition):
    name = str(name or '').strip()
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError('Enter a preset name of 1–80 characters.')
    record = dict(schema_version=VERSION, name=name, solver_config=preferences(config),
                  composition={k: deepcopy(composition.get(k)) for k in COMPOSITION_FIELDS})
    # Saved/imported presets must be finite, serialisable values.
    json.dumps(record, allow_nan=False)
    return record


def preset_library(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError('Solver presets must be a list.')
    result, names = [], set()
    for item in value:
        if not isinstance(item, dict) or item.get('schema_version') != VERSION:
            raise ValueError('Unsupported solver preset version.')
        if not isinstance(item.get('solver_config'), dict) or not isinstance(item.get('composition'), dict):
            raise ValueError('A preset must contain solver preferences and blend composition.')
        record = make_preset(item.get('name'), item['solver_config'], item['composition'])
        if record['name'].casefold() in names:
            raise ValueError('Solver preset names must be unique.')
        names.add(record['name'].casefold())
        result.append(record)
    return result


def save_preset(library, record):
    result = preset_library(library)
    record = preset_library([record])[0]
    for i, old in enumerate(result):
        if old['name'].casefold() == record['name'].casefold():
            result[i] = record
            break
    else:
        result.append(record)
    return result


def matching_preset(library, config, composition):
    current = make_preset('Current', config, composition)
    for row in preset_library(library):
        if all(row[key] == current[key] for key in ('solver_config', 'composition')):
            return row['name']
    return ''
