"""Operational display projections; full source and report records stay intact."""
import re
from classes.GradeStreams import STREAMS, DEFAULT_STREAM, normalise_grade_streams, grade_stream_vector, numeric


def stream_column(column):
    name = re.sub(r'[^a-z0-9]+', '_', str(column).lower()).strip('_')
    for stream in STREAMS:
        if re.search(r'(?:^|_)' + stream + r'(?:_|$)', name):
            if 'grade' in name or re.search(r'_(fe|si|sio2|al|al2o3|p|mn)$', name):
                return stream
    return None


def visible_columns(columns, selected_stream=DEFAULT_STREAM):
    return [column for column in columns
            if stream_column(column) in (None, selected_stream)
            and not str(column).lower().endswith('_json')
            and str(column).lower() not in {'grade_streams', 'source_properties', 'modelled_properties',
                'defined_fields', 'grade_stream_warnings', 'reconciliation', 'input_revision',
                'optimisation_input_revision', 'manual_input_revision', 'manual_feed_available_at'}]


CHUNK_COLUMNS = ('footprint', 'hex', 'sequence', 'selected_grades', 'rom_tonnes', 'product_tonnes')
CHUNK_LABELS = dict(footprint='Footprint', hex='Chunk', sequence='Sequence',
                    selected_grades='Optimiser grades', rom_tonnes='ROM Tonnes', product_tonnes='Product Tonnes')


def chunk_display(row, selected_stream=DEFAULT_STREAM, crusher_field='modelled_rom_wmt',
                  product_field='modelled_product_wmt'):
    streams = normalise_grade_streams(row.get('grade_streams') or row.get('GRADE_STREAMS'))
    brands = list((streams.get(selected_stream) or {}).keys()) or ['*']
    labels = dict(fe='Fe', si='SiO₂', al='Al₂O₃', p='P', mn='Mn')
    vectors = []
    has_grades = False
    for brand in brands:
        values = grade_stream_vector(streams, selected_stream, brand)
        has_grades |= any(values.get(a) is not None for a in labels)
        text = ' · '.join(f'{labels[a]} {values[a]:.4f}' if values.get(a) is not None else f'{labels[a]} —'
                          for a in labels)
        vectors.append((brand + ': ' if brand != '*' else '') + text)
    grade_text = '; '.join(vectors) if has_grades else (
        'Pending application at AMT / Calendar submission'
        if selected_stream.startswith('adjusted_') and not row.get('prepared_grade_opf')
        else 'Grades unavailable — check field mappings')
    if has_grades and row.get('prepared_grade_opf'):
        grade_text = f"{row['prepared_grade_opf']}: {grade_text}"
    properties = {**(row.get('modelled_properties') or {}), **(row.get('source_properties') or {}),
                  **(row.get('defined_fields') or {})}
    def quantity(field):
        if field in properties:
            return properties[field]
        if field in row:
            return row[field]
        # Physical balance is valid only when that exact physical field is selected.
        return row.get('balance') if field in ('balance', 'rom_wmt', 'wmt') else None
    return dict(footprint=row.get('footprint', ''), hex=row.get('hex', ''), sequence=row.get('sequence', ''),
                selected_grades=grade_text, rom_tonnes=quantity(crusher_field),
                product_tonnes=quantity(product_field))


def hex_grade_display(row):
    """Hex chemistry remains a baseline; adjusted predictions belong to chunks."""
    streams = normalise_grade_streams(row.get('grade_streams') or row.get('GRADE_STREAMS'))
    for stream, label in (('modelled_rom', 'Hex modelled ROM'), ('insitu', 'Hex in situ')):
        for brand in streams.get(stream) or {}:
            values = grade_stream_vector(streams, stream, brand)
            if any(value is not None for value in values.values()):
                return label, values
    return 'Hex in situ', {a: numeric(row.get(f'grade_{a}')) for a in ('fe', 'si', 'al', 'p', 'mn')}
