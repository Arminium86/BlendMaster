"""Prepare independent OPF source chemistry without blocking the Qt event loop."""
from copy import deepcopy
from PyQt5.QtCore import QObject
from classes.CombinedOPFReconciliation import (
    SOURCE_FIELDS, build_profiles, evidence_signature, profile_signature, reusable_cache,
)


def ensure(host, on_complete, *, on_error=None, _previous_profiles=None):
    """Return True while preparing; otherwise the caller can continue now."""
    if not isinstance(host, QObject):
        return False
    if vars(host).get('_defer_opf_profile_preparation'):
        return False  # Restore sources/chunks before preparing their chemistry.
    config = vars(host).get('multi_feed_configuration') or {}
    from classes.AMTReconciliation import chunks_ready
    from classes.ApprovedReconciliation import missing_sources
    if not chunks_ready(vars(host)) or missing_sources(vars(host), planning=True):
        return False
    if vars(host).get('_opf_profile_preparation_pending'):
        vars(host).setdefault('_opf_profile_waiters', []).append((on_complete, on_error))
        return True
    opfs = sorted({point['opf'] for point in config.get('tipping_points', [])}) if config.get('mode', 'single') != 'single' else [host.opf_input_choice]
    bundles = vars(host).get('opf_reconciliation_inputs') or {}
    builds = host.reconciliation_inventory_builds()
    if not opfs or (not vars(host).get('grade_reconciliation_registry') and any(opf not in bundles or bundles[opf].get('signature') !=
                      evidence_signature(vars(host), opf, builds) for opf in opfs)):
        return False  # The existing reconciliation workflow must fetch evidence first.
    cached = reusable_cache(vars(host), opfs)
    if cached:
        from GUI.OPFProfilePublication import publish
        publish(host, cached[1])
        host._combined_opf_profile_cache = (profile_signature(vars(host), opfs), cached[1])
        return False
    values = {key: vars(host)[key] for key in (*SOURCE_FIELDS, 'active_scenario_id') if key in vars(host)}
    previous_profiles = _previous_profiles if _previous_profiles is not None else vars(host).get('_combined_opf_profile_cache')
    implementation = type(host)
    host._opf_profile_preparation_pending = True
    host._opf_profile_waiters = [(on_complete, on_error)]

    def work():
        state = deepcopy(values)
        # Read old audits without copying the entire old profile. Reuse is
        # checked per source even when chunking invalidated the profile itself.
        state['_combined_opf_profile_cache'] = previous_profiles
        return profile_signature(state, opfs), build_profiles(state, opfs, implementation)

    def done(result):
        host._opf_profile_preparation_pending = False
        waiters = vars(host).pop('_opf_profile_waiters', [])
        def complete_all():
            for complete, error in waiters:
                try:
                    complete()
                except Exception as exc:
                    (error or host.show_error_popup)(str(exc))
        def fail_all(error):
            for _, failure in waiters:
                (failure or host.show_error_popup)(error)
        if result[0] != profile_signature(vars(host), opfs):
            # AMT map delivery may complete while the controls are locked.
            # Rebuild from that newer snapshot instead of publishing stale grades.
            if not ensure(host, complete_all, on_error=fail_all, _previous_profiles=result):
                complete_all()
            return
        from GUI.OPFProfilePublication import publish
        publish(host, result[1])
        host._combined_opf_profile_cache = (profile_signature(vars(host), opfs), result[1])
        complete_all()

    def failed(error):
        host._opf_profile_preparation_pending = False
        for _, failure in vars(host).pop('_opf_profile_waiters', []):
            (failure or host.show_error_popup)(error)

    host.run_background_task('Preparing independent OPF source grades…', work, done, failed, readable_results=True)
    return True
