"""Validate source identities before atomically accepting a replacement file."""
from functools import lru_cache
from pathlib import Path
import hashlib
import os
import shutil
import tempfile
import pandas as pd
from classes.HaulCycleDataHandler import HaulCycleDataHandler


def file_revision(path):
    resolved = Path(path).resolve(strict=True)
    stat = resolved.stat()
    return (str(resolved), stat.st_size, stat.st_mtime_ns)


def retain_selection(selected, available):
    allowed = set(available)
    selected = list(dict.fromkeys(str(value).strip() for value in selected or []))
    return ([v for v in selected if v in allowed], [v for v in selected if v not in allowed])


def retain_rules(rules, sources, destinations):
    sources, destinations = set(sources), set(destinations)
    retained, removed = [], []
    for rule in rules or []:
        target = retained if (rule.get('grade_block_source') in sources and
                              rule.get('crusher_destination') in destinations) else removed
        target.append(dict(rule))
    return retained, removed


@lru_cache(maxsize=8)
def _catalogue(kind, revision):
    path = revision[0]
    if kind == 'closing_balance':
        from classes.ClosingROMStocksCompliance import ClosingROMStocksCompliance
        frame = ClosingROMStocksCompliance.read_workbook(path)
        if frame.empty:
            raise ValueError('The closing-balance workbook has no usable rows.')
        return {'rows': len(frame)}
    if kind == 'haul_cycles':
        return {'crushers': tuple(HaulCycleDataHandler.get_distinct_crusher_names(path))}
    columns = {'Agent.Name', 'Source.Type', 'Source.NamePart2', 'Destination.Type',
               'Destination.Name', 'Destination.FullName'}
    headers = set(pd.read_csv(path, nrows=0).columns)
    required = {'Agent.Name', 'Source.Type'}
    if kind == 'two_wp':
        required |= {'Destination.Type'}
        if not headers.intersection({'Destination.Name', 'Destination.FullName'}):
            raise ValueError('The 2WP file has no destination identity column.')
    if required - headers:
        raise ValueError('Missing required columns: ' + ', '.join(sorted(required - headers)))
    result = {key: set() for key in ('agents', 'crushers', 'product_crushers', 'sources')}
    def text(frame, column):
        return frame.get(column, pd.Series('', index=frame.index)).astype('string').fillna('').str.strip()
    for frame in pd.read_csv(path, usecols=lambda name: name in columns, chunksize=100_000):
        source_type = text(frame, 'Source.Type').str.lower()
        agents = text(frame, 'Agent.Name')
        destinations = text(frame, 'Destination.Name')
        full = text(frame, 'Destination.FullName')
        products = full.mask(full.eq(''), destinations)
        # Match the existing transaction selector (Name preferred), while
        # product guidance uses the full path. Both scan the entire file.
        if 'Destination.Name' not in frame:
            destinations = full
        crusher = text(frame, 'Destination.Type').str.lower().eq('crusher')
        result['agents'].update(agents[source_type.eq('reserve')])
        result['sources'].update(text(frame, 'Source.NamePart2')[source_type.eq('reserve')])
        result['crushers'].update(destinations[crusher])
        product = crusher & source_type.eq('flow') & agents.str.lower().eq('plantagent')
        result['product_crushers'].update(products[product])
    result['has_source_identity'] = 'Source.NamePart2' in headers
    return {key: tuple(sorted(value - {''})) if isinstance(value, set) else value
            for key, value in result.items()}


def inspect_import(kind, path):
    if kind not in ('two_wp', 'day_plan', 'haul_cycles', 'closing_balance'):
        raise ValueError('Unknown guidance input type: ' + str(kind))
    revision = file_revision(path)
    catalogue = dict(_catalogue(kind, revision))
    if file_revision(path) != revision:
        raise ValueError('The file changed while it was being read. Wait for the export to finish and retry.')
    return dict(kind=kind, revision=revision, catalogue=catalogue)


def snapshot_import(result, directory):
    """Retain an accepted input version even if its delivery file is overwritten."""
    revision = tuple(result['revision'])
    source = Path(revision[0])
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(repr(revision).encode()).hexdigest()[:16]
    accepted = directory / (source.stem + '-' + digest + source.suffix)
    if not accepted.exists():
        fd, temporary = tempfile.mkstemp(prefix='incoming-', dir=directory)
        os.close(fd)
        try:
            shutil.copy2(source, temporary)
            if file_revision(source) != revision:
                raise ValueError('Delivery changed while its accepted version was being saved.')
            os.replace(temporary, accepted)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {**result, 'accepted_path': str(accepted)}


def current_import_revisions(audits):
    """Stable source versions, independent of how often a file was inspected."""
    return {row['kind']: row['revision'] for row in audits or []}
