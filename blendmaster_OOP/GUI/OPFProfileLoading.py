"""Prepare independent OPF source chemistry without blocking the Qt event loop."""
from copy import deepcopy
from PyQt5.QtCore import QObject
from classes.CombinedOPFReconciliation import (
    SOURCE_FIELDS, build_profiles, evidence_signature, profile_signature, reusable_cache,
)


def ensure(host, on_complete, *, on_error=None):
    """Return True while preparing; otherwise the caller can continue now."""
    if not isinstance(host, QObject):
        return False
    if vars(host).get('_defer_opf_profile_preparation'):
        return False  # Restore sources/chunks before preparing their chemistry.
    config = vars(host).get('multi_feed_configuration') or {}
    if config.get('mode') != 'combined_opf':
        return False
    if vars(host).get('_opf_profile_preparation_pending'):
        return True
    opfs = sorted({point['opf'] for point in config.get('tipping_points', [])})
    bundles = vars(host).get('opf_reconciliation_inputs') or {}
    builds = host.reconciliation_inventory_builds()
    if not opfs or any(opf not in bundles or bundles[opf].get('signature') !=
                      evidence_signature(vars(host), opf, builds) for opf in opfs):
        return False  # The existing reconciliation workflow must fetch evidence first.
    if reusable_cache(vars(host), opfs):
        return False
    values = {key: vars(host)[key] for key in (*SOURCE_FIELDS, 'active_scenario_id') if key in vars(host)}
    implementation = type(host)
    host._opf_profile_preparation_pending = True

    def work():
        state = deepcopy(values)
        return profile_signature(state, opfs), build_profiles(state, opfs, implementation)

    def done(result):
        host._opf_profile_preparation_pending = False
        if result[0] != profile_signature(vars(host), opfs):
            # AMT map delivery may complete while the controls are locked.
            # Rebuild from that newer snapshot instead of publishing stale grades.
            if not ensure(host, on_complete, on_error=on_error):
                on_complete()
            return
        host._combined_opf_profile_cache = result
        on_complete()

    def failed(error):
        host._opf_profile_preparation_pending = False
        (on_error or host.show_error_popup)(error)

    host.run_background_task('Preparing independent OPF source grades…', work, done, failed)
    return True
