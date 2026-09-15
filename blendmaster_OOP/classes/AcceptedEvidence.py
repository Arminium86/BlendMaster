"""Ownership boundary for accepted reconciliation evidence.

Published registries and their evidence records are read-only. Manual searches
use fork_registry; continuous updates also fork before editing record metadata.
History caches are replaced as a whole and copied before use by a resolver.
This lets site snapshots share accepted evidence without sharing editable state.
"""
import hashlib
import json
from copy import deepcopy


def copy_preparation_state(values):
    """Detach editable sources while retaining read-only accepted evidence."""
    shared = [values.get(key) for key in ('grade_reconciliation_registry', '_combined_opf_profile_cache', 'inventory_source_cache')]
    return deepcopy(values, {id(value): value for value in shared if isinstance(value, (dict, list, tuple))})


def fork_registry(registry):
    result = dict(registry or {})
    result['sources'] = dict(result.get('sources') or {})
    for name in ('active_inventory', 'active_chunks'):
        result[name] = {key: dict(record) for key, record in (result.get(name) or {}).items()}
    return result


def fingerprint_fields(fields, state, *, accepted=()):
    """Same canonical JSON hash as SiteWorkflow.fingerprint, with cached bytes.

    Only explicitly accepted, replacement-owned fields may cache their encoding.
    The cache retains the object itself, so recycled Python ids cannot match.
    Mutable controls and source inventories are encoded afresh on every call.
    """
    cache = state.setdefault('_accepted_evidence_encodings', {})
    digest = hashlib.sha256()
    digest.update(b'{')
    for index, key in enumerate(sorted(fields)):
        if index:
            digest.update(b',')
        digest.update(json.dumps(key).encode('utf-8'))
        digest.update(b':')
        value = fields[key]
        previous = cache.get(key) if key in accepted else None
        if previous is not None and previous[0] is value:
            encoded = previous[1]
        else:
            encoded = json.dumps(value, sort_keys=True, default=str, separators=(',', ':')).encode('utf-8')
            if key in accepted:
                cache[key] = (value, encoded)
        digest.update(encoded)
    digest.update(b'}')
    return digest.hexdigest()
