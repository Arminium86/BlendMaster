"""Prepare independent OPF views of one scenario's physical sources."""
from copy import deepcopy
import inspect
import re
from types import SimpleNamespace

from classes.FieldDefinitions import normalize_field_mappings, field_weight_map
from classes.GradeStreams import internal_product_slot, amt_modelled_product_slot, normalise_grade_streams, amt_grade_streams
from classes.OPFSourceProfiles import profile_from_state
from classes.ReconciliationApplication import reconciliation_fingerprint
from classes.ReconciliationControls import normalise_reconciliation_settings, required_history_days
from classes.GradeStreams import configured_brands


SOURCE_FIELDS = ('stockpile_data', 'updated_stockpile_data', 'AMT_stockpile_data', 'hex_sequence_table',
                 'hex_sequence_table_argument', 'AMT_footprint_exclusions', 'AMT_chunk_settings',
                 'field_definitions', 'field_mappings', 'field_mapping_schema_version',
                 'product_brand_labels_choice', 'mine_input_choice', 'opf_input_choice', 'start_time_choice',
                 'reconciliation_settings', 'historical_recon_factors', 'reconciliation_inputs',
                 'opf_reconciliation_inputs', 'aps_grade_field_mappings', 'aps_source_property_field_mappings',
                 'cb_lump_fines_mode', 'cb_lump_percentage', 'byproducts_enabled',
                 'byproduct_quantity_fields', 'byproduct_grade_fields')


def profile_signature(state, opfs):
    return reconciliation_fingerprint({'version': 2, 'opfs': opfs,
        **{key: state.get(key) for key in SOURCE_FIELDS}})


def evidence_signature(state, opf, builds):
    settings = normalise_reconciliation_settings(state.get('reconciliation_settings'))
    brands = configured_brands(state.get('product_brand_labels_choice'))
    return reconciliation_fingerprint(dict(mine=state.get('mine_input_choice'), start=str(state.get('start_time_choice')),
        opf=opf, brands=brands, builds=builds, advanced=settings['method'] != 'standard',
        days=required_history_days(settings, opf, brands)))


def opf_field_mappings(mappings, primary, opf):
    """Redirect known product-slot mappings; custom unrelated mappings stay exact."""
    result = normalize_field_mappings(mappings)
    if primary == opf:
        return result
    for row in result:
        if not row['target_field'].startswith(('modelled_product_', 'adjusted_product_')):
            continue
        slot = internal_product_slot if row['source_family'] == 'inventory' else amt_modelled_product_slot
        old, new = slot(primary), slot(opf)
        if old and new and old != new:
            row['source_field'] = re.sub(r'(?<![a-z0-9])' + old + r'(?![a-z0-9])',
                lambda m: new.upper() if m[0].isupper() else new, row['source_field'], flags=re.IGNORECASE)
            # Canonical Product2 aliases resolve CC OPF02 inventory PROD3.
            old_alias, new_alias = amt_modelled_product_slot(primary), amt_modelled_product_slot(opf)
            if old_alias and new_alias:
                row['source_field'] = re.sub(r'(?<![a-z0-9])' + old_alias.replace('prod', 'product') + r'(?![a-z0-9])',
                    lambda m: new_alias.replace('prod', 'product').upper() if m[0].isupper() else new_alias.replace('prod', 'product'), row['source_field'], flags=re.IGNORECASE)
    return result


class SourceContext(SimpleNamespace):
    """Reuse existing source calculations without changing a live Qt window."""
    def __getattr__(self, name):
        descriptor = inspect.getattr_static(self._ui_class, name, None)
        if isinstance(descriptor, staticmethod):
            return descriptor.__func__
        if inspect.isfunction(descriptor):
            return descriptor.__get__(self, type(self))
        raise AttributeError(name)


def build_profiles(state, opfs, ui_class):
    from GUI.DrawCharts import DrawAMTStockpile
    result = {}
    for opf in opfs:
        context = SourceContext(**deepcopy({key: state.get(key) for key in SOURCE_FIELDS if key in state}))
        context._ui_class = ui_class
        context.opf_input_choice = opf
        context.field_mappings = opf_field_mappings(state.get('field_mappings'), state.get('opf_input_choice'), opf)
        bundle = state['opf_reconciliation_inputs'][opf]
        context.historical_recon_factors = deepcopy(state.get('historical_recon_factors') if opf == state.get('opf_input_choice') else bundle['factors'])
        context.reconciliation_inputs = deepcopy(bundle.get('reconciliation_inputs') or {})
        context.historical_recon_warnings = list(bundle.get('warnings') or [])
        context.stockpile_data = vars(context).get('stockpile_data') or {}
        context.updated_stockpile_data = vars(context).get('updated_stockpile_data') or {}
        context.AMT_stockpile_data = vars(context).get('AMT_stockpile_data') or {}
        context.ensure_field_mapping_migration = lambda: None  # The owning scenario already migrated its schema.
        context.apply_canonical_field_mappings()
        context.apply_grade_streams_to_inventory()
        enriched = context.enrich_AMT_grade_streams({}, context.AMT_stockpile_data) if context.AMT_stockpile_data else {}
        members = {(str(footprint), str(row.get('HEX') or row.get('hex'))): row for footprint, rows in enriched.items() for row in rows}
        builder = DrawAMTStockpile.__new__(DrawAMTStockpile)
        builder.source_property_kinds = {r['name']: r['kind'] for r in vars(context).get('field_definitions') or []}
        builder.source_property_weights = field_weight_map(vars(context).get('field_definitions'))
        builder.excluded_hex_summary = lambda _: {'count': 0, 'wmt': 0, 'hexes': []}
        builder.get_chunk_setting = lambda _footprint, _key, default=None: default
        builder.get_chunk_plan = lambda _: {'chunk_count': 1}
        chunks = []
        for original in vars(context).get('hex_sequence_table') or vars(context).get('hex_sequence_table_argument') or []:
            chunk = deepcopy(original)
            ids = builder.member_hexes_from_entry(chunk)
            rows = [members.get((str(chunk.get('footprint')), str(identity))) for identity in ids]
            if rows and all(row is not None for row in rows):
                prepared = []
                for row in rows:
                    row = deepcopy(row)
                    row['hex'] = row.get('HEX', row.get('hex'))
                    row['_positive_balance'] = max(float(row.get('FINAL_WMT', row.get('balance', 0)) or 0), 0)
                    row['balance'] = row['_positive_balance']
                    for analyte in ('fe', 'si', 'al', 'p', 'mn'):
                        row[f'grade_{analyte}'] = float(row.get(f'grade_{analyte}', row.get(f'GRADE_{analyte.upper()}', 0)) or 0)
                    prepared.append(row)
                rebuilt = builder.build_chunk_row(chunk['footprint'], int(chunk.get('sequence') or 1), prepared, float(chunk.get('chunk_size') or chunk.get('balance') or 0))
                if abs(float(rebuilt['balance']) - float(chunk['balance'])) > .1:
                    raise ValueError(f'{opf}: AMT chunk {chunk.get("hex")} no longer matches its member hexes. Rebuild AMT chunks in this scenario.')
                # Preserve the selected chunk's identity, sequence, rates and geometry.
                for key in ('grade_streams', 'reconciliation', 'defined_fields', 'source_properties', 'grade_stream_warnings', 'data_quality'):
                    chunk[key] = rebuilt[key]
                context.sync_canonical_grade_fields(chunk, chunk['grade_streams'])
            else:
                if opf != state.get('opf_input_choice'):
                    raise ValueError(f'{opf}: AMT chunk {chunk.get("hex")} needs its original member hexes to prepare independent product grades. Refresh AMT data and rebuild chunks in this scenario.')
                # Legacy chunks without member rows can only receive the established global fallback.
                streams = normalise_grade_streams(chunk.get('grade_streams'))
                for stream in ('insitu', 'modelled_rom', 'modelled_product'):
                    vector = next(iter(streams.get(stream, {}).values()), {})
                    chunk.update({f'{stream}_{a}': grade for a, grade in vector.items()})
                streams = amt_grade_streams(chunk, chunk, context.product_brand_labels_choice, context.historical_recon_factors, opf, strict_mappings=True)
                application = context.reconciliation_application()
                if application:
                    streams, audit = application.apply(streams, source_id=str(chunk.get('hex') or ''), source_kind='amt', source_wmt=float(chunk.get('balance') or 0), contributing_blocks=[], warnings=['Member hexes unavailable; using global factors.'])
                    chunk['reconciliation'] = audit
                chunk['grade_streams'] = streams
                context.sync_canonical_grade_fields(chunk, streams)
            chunks.append(chunk)
        context.hex_sequence_table = chunks
        result[opf] = profile_from_state(vars(context), str(state.get('active_scenario_id') or 'active'))
        result[opf]['reconciliation_audits'] = [deepcopy(r['reconciliation']) for r in members.values() if r.get('reconciliation')]
    return result
