"""Keep independently prepared OPF grades on the shared physical inventory ledger.

Namespaced canonical properties use the existing additive/weighted property
ledger, so inbound mixing and reclaim depletion preserve each OPF's chemistry.
"""
from copy import deepcopy
from hashlib import sha256

from classes.GradeStreams import STREAMS, ANALYTES, normalise_grade_streams
from classes.CustomConstraints import source_properties_from_mapping, source_property_kind
from classes.MultiFeedSettings import route_allowed


def prefix(opf):
    return 'opf_' + sha256(opf.encode()).hexdigest()[:12] + '_'


def profile_from_state(state, scenario_id):
    inventory = {**(state.get('stockpile_data') or {}), **(state.get('updated_stockpile_data') or {})}
    chunks = {str(r.get('hex') or r.get('chunk_id') or ''): r for r in state.get('hex_sequence_table') or []}
    return dict(scenario_id=scenario_id, opf=state.get('opf_input_choice'),
                start=str(state.get('start_time_choice') or ''), mine=state.get('mine_input_choice'),
                inventory=inventory, chunks=chunks,
                fields=deepcopy(state.get('field_definitions') or []),
                aps_grade_field_mappings=deepcopy(state.get('aps_grade_field_mappings') or {}),
                aps_source_property_field_mappings=deepcopy(state.get('aps_source_property_field_mappings') or {}),
                brands=deepcopy(state.get('product_brand_labels_choice') or []))


def register_profiles(config, profiles):
    config['opf_profiles'] = profiles
    fields = set(config.get('optimisation_source_property_fields') or [])
    kinds = config.setdefault('source_property_kinds', {})
    weights = config.setdefault('source_property_weights', {})
    descriptors = {}
    for opf, profile in profiles.items():
        aliases = {r['name']: r for r in profile['fields'] if r.get('name')}
        brands = profile.get('brands') or ['*']
        if isinstance(brands, str):
            brands = [b.strip() for b in brands.split(',') if b.strip()]
        grades = {}
        for stream in STREAMS:
            for brand in dict.fromkeys(['*', *brands]):
                for a in ANALYTES:
                    key = prefix(opf) + 'grade_' + sha256(f'{stream}/{brand}/{a}'.encode()).hexdigest()[:16]
                    grades[key] = (stream, brand, a)
                    kinds[key] = 'intensive'
                    weight = (aliases.get(f'{stream}_{a}') or {}).get('weight_field')
                    weights[key] = prefix(opf) + weight if weight else 'modelled_rom_wmt'
                    fields.add(key)
        for name, field in aliases.items():
            key = prefix(opf) + name
            kinds[key] = field.get('kind') or source_property_kind(name)
            if field.get('weight_field'):
                weights[key] = prefix(opf) + field['weight_field']
            fields.add(key)
        descriptors[opf] = dict(grades=grades, aliases=list(aliases))
    config['opf_property_descriptors'] = descriptors
    config['optimisation_source_property_fields'] = sorted(fields)


def namespace_record(row, opf, config):
    descriptor = config['opf_property_descriptors'][opf]
    properties = source_properties_from_mapping({'source_properties': {**(row.get('defined_fields') or {}), **(row.get('source_properties') or {})}})
    streams = normalise_grade_streams(row.get('grade_streams') or row.get('GRADE_STREAMS'), row)
    result = {prefix(opf) + key: properties[key] for key in descriptor['aliases'] if key in properties}
    for key, (stream, brand, a) in descriptor['grades'].items():
        value = (streams.get(stream, {}).get(brand) or streams.get(stream, {}).get('*') or {}).get(a)
        if value is not None:
            result[key] = value
    return result


def prepare_inventory_profiles(stockpiles, chunks, config):
    settings = config['multi_feed_settings']
    profiles = config.get('opf_profiles') or {}
    required = {p['opf'] for p in settings['tipping_points']}
    if required - profiles.keys():
        raise ValueError('Prepare Data Streams for all selected OPFs in this scenario: ' + ', '.join(sorted(required - profiles.keys())))
    for row, identity, footprint, amt in [*( (r, n, n, False) for n, r in stockpiles.items() if not r.get('amt')),
                                       *((r, str(r.get('hex') or r.get('chunk_id')), r.get('footprint'), True) for r in chunks)]:
        for opf, profile in profiles.items():
            if not any(p['opf'] == opf and route_allowed(settings, footprint, p['name']) for p in settings['tipping_points']):
                continue
            source = profile['chunks' if amt else 'inventory'].get(identity)
            if not source or not (source.get('grade_streams') or source.get('GRADE_STREAMS')):
                raise ValueError(f'{opf}: {identity} has no prepared grade streams. Submit Data Streams and AMT chunks in this scenario.')
            if str(source.get('build') or source.get('BUILD') or '') != str(row.get('build') or row.get('BUILD') or ''):
                raise ValueError(f'{opf}: {identity} reconciliation refers to a different inventory build. Refresh Data Streams in this scenario.')
            if abs(float(source.get('balance') or 0) - float(row.get('balance') or 0)) > 0.1:
                raise ValueError(f'{opf}: {identity} reconciliation opening tonnes differ from the physical inventory. Refresh Data Streams and rebuild AMT chunks in this scenario.')
            row.setdefault('source_properties', {}).update(namespace_record(source, opf, config))


def apply_opf_profile(event, opf, config):
    descriptor = (config.get('opf_property_descriptors') or {}).get(opf)
    if descriptor is None:
        raise ValueError(f'{opf}: independent reconciliation inputs have not been prepared.')
    properties = dict(event.source_properties or {})
    streams = {stream: {} for stream in STREAMS}
    found = False
    for key, (stream, brand, a) in descriptor['grades'].items():
        if key in properties:
            streams[stream].setdefault(brand, {})[a] = properties[key]
            found = True
    if not found and event.balance > 0:
        raise ValueError(f'{opf}: missing reconciled grades for {event.source_name}; refresh its source data.')
    event.grade_streams = streams
    for name in descriptor['aliases']:
        if prefix(opf) + name in properties:
            properties[name] = properties[prefix(opf) + name]
        else:
            properties.pop(name, None)
    event.source_properties = properties
    event._reconciliation_scenario = config['opf_profiles'][opf]['scenario_id']
