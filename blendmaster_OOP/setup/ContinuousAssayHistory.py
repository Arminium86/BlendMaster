"""Use the OPF Production Report assay source and exact actual crusher feeds."""
from datetime import timedelta
from copy import deepcopy
from classes.ContinuousAssays import settings, source_catalog, observations, estimate, live_enabled
from classes.ContinuousAssayScope import active_sources, plan_rows as read_plan_rows
from classes.ExpitDataHandler import ExpitDataHandler
from classes.TransportSettings import transport_enabled
from setup.ProductAssayHistory import ProductAssayHistory, awst
from setup.RecentDestinationActivity import RecentDestinationActivity
from setup.ActualCrusherFeed import destination_matches, FEED_PREDICATE
from classes.GradeStreams import normalise_grade_streams


def request_snapshot(state, profiles=None):
    """Detach only estimator inputs, excluding large reconciliation/geometry audits.

    Polling captures this on the UI thread. Copying the entire OPF profile can
    otherwise freeze the window despite the warehouse query running in a worker.
    Keep the same inputs consumed by source_catalog, including chunk build fallback.
    """
    keys = ('continuous_assay_settings', 'continuous_assay_state', 'multi_feed_configuration',
            'time_mode_choice', 'optimisation_input_revision',
            'mine_input_choice', 'opf_input_choice', 'crusher_input_choice',
            'transport_settings', 'selected_optimisation_plan_id')
    snapshot = deepcopy({key: state.get(key) for key in keys})

    def source(row):
        result = {key: deepcopy(row.get(key)) for key in
                  ('build', 'BUILD', 'balance', 'footprint', 'hex', 'chunk_id') if key in row}
        result['grade_streams'] = normalise_grade_streams(row.get('grade_streams') or row.get('GRADE_STREAMS'), row)
        for name in ('defined_fields', 'source_properties'):
            result[name] = {key: deepcopy(value) for key, value in (row.get(name) or {}).items()
                            if key in ('modelled_product_dmt', 'modelled_rom_wmt')}
        return result

    def inventory(rows):
        return {name: source(row) for name, row in (rows or {}).items()}

    for name in ('stockpile_data', 'updated_stockpile_data'):
        snapshot[name] = inventory(state.get(name))
    snapshot['hex_sequence_table'] = [source(row) for row in state.get('hex_sequence_table') or []]
    snapshot['_continuous_opf_profiles'] = {
        opf: dict(inventory=inventory(profile.get('inventory')), chunks=inventory(profile.get('chunks')))
        for opf, profile in (profiles or {}).items()}
    return snapshot


class ContinuousAssayHistory:
    def __init__(self, assay_service=None):
        self.assays = assay_service or ProductAssayHistory()

    def actual_feed(self, site, start, end, points):
        connection = self.assays.inventory_loader.connect_snowflake_with_service_account()
        if connection is None:
            raise ConnectionError('Actual crusher feed is unavailable; accepted corrections have been retained.')
        try:
            with connection.cursor() as cursor:
                cursor.execute('ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 60')
                cursor.execute("""SELECT INTERNAL_ID, SOURCE, DESTINATION, DESTINATION_FMS, WMT_REPORTING,
                    CONVERT_TIMEZONE('Australia/Perth', TRANSACTION_DATETIME)::TIMESTAMP_NTZ AS OBSERVED_AT
                    FROM AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_EXPIT_REHANDLE_TRANSACTIONS
                    WHERE TRANSACTION_DATETIME >= TO_TIMESTAMP_TZ(%s) AND TRANSACTION_DATETIME < TO_TIMESTAMP_TZ(%s)
                      AND UPPER(TRIM(OPERATION)) = %s AND IS_DELETED = FALSE
                      AND """ + FEED_PREDICATE + """
                    ORDER BY OBSERVED_AT, INTERNAL_ID LIMIT 50001""",
                    (awst(start).isoformat()+'+08:00', awst(end).isoformat()+'+08:00', RecentDestinationActivity.warehouse_operation(site)))
                rows = [dict(zip((c[0].upper() for c in cursor.description), row)) for row in cursor.fetchall()]
            if len(rows) > 50000:
                raise ValueError('Continuous reconciliation exceeds 50,000 actual movements; shorten its lookback.')
            result, seen = [], set()
            for row in rows:
                identity = str(row['INTERNAL_ID'])
                if identity in seen:
                    continue
                seen.add(identity)
                matches = [p for p in points if destination_matches(row, site, p)]
                if len(matches) > 1:
                    raise ValueError('Ambiguous crusher mapping in actual assay contributions.')
                if len(matches) == 1 and float(row['WMT_REPORTING'] or 0) > 0:
                    result.append(dict(SOURCE=row['SOURCE'], opf=matches[0]['opf'], tipping_point=matches[0]['name'],
                        time=awst(row['OBSERVED_AT']).isoformat(), wmt=float(row['WMT_REPORTING'])))
            return result
        finally:
            connection.close()

    def refresh(self, state, now, database=None):
        if not live_enabled(state):
            return dict(version=1, catalog={}, evidence=[], timeline=[], audit=[], revision='',
                        status='Continuous assays require Now mode and an enabled policy.')
        # Establish the active blend before accessing either warehouse service.
        plan_rows = state.get('_continuous_plan_rows')
        if plan_rows is None:
            plan_rows = read_plan_rows(database, state.get('selected_optimisation_plan_id') or 'Primary')
        scope = active_sources(plan_rows, now, state.get('opf_input_choice'))
        if not scope:
            return dict(version=1, catalog={}, evidence=[], timeline=[], audit=[], revision='',
                        status='No active saved-plan inventory stockpile or AMT chunk feeds at the current time.')
        if (state.get('multi_feed_configuration') or {}).get('mode') == 'combined_opf':
            from classes.ContinuousAssays import digest
            profiles = state.get('_continuous_opf_profiles') or {}
            required = {p['opf'] for p in state['multi_feed_configuration']['tipping_points']}
            if required - profiles.keys():
                raise ValueError('Prepare the independent OPF source profiles before continuous reconciliation.')
            results = {}
            for opf in sorted(required):
                context = {**state, 'opf_input_choice': opf, 'stockpile_data': profiles[opf]['inventory'],
                    '_continuous_plan_rows': plan_rows,
                    'updated_stockpile_data': {}, 'hex_sequence_table': list(profiles[opf]['chunks'].values()),
                    'multi_feed_configuration': {**state['multi_feed_configuration'], 'mode': 'multi_tipping_point'},
                    'continuous_assay_state': (state.get('continuous_assay_state') or {}).get('profiles', {}).get(opf, {})}
                results[opf] = self.refresh(context, now, database)
            return dict(version=1, profiles=results, revision=digest({k:v['revision'] for k,v in results.items()}),
                checked_at=awst(now).isoformat(), status='fresh',
                audit=[{**a, 'opf':opf} for opf,v in results.items() for a in v.get('audit', [])])
        policy = settings(state.get('continuous_assay_settings'))
        catalog = source_catalog(state)
        from classes.GradeStreams import normalise_opf
        active = set(scope.get(normalise_opf(state.get('opf_input_choice')), []))
        catalog = {source: row for source, row in catalog.items() if source in active}
        if not policy['enabled'] or not catalog:
            return dict(version=1, catalog=catalog, evidence=[], timeline=[], audit=[], revision='',
                status='disabled' if not policy['enabled'] else 'No current builds with mapped dry-product quantities.')
        end = awst(now).replace(second=0, microsecond=0)
        start = end-timedelta(hours=policy['lookback_hours'])
        feed = state.get('multi_feed_configuration') or {}
        points = feed.get('tipping_points') or [dict(name=state.get('crusher_input_choice'), opf=state.get('opf_input_choice'))]
        # This profile owns independent OPF chemistry, even in a combined solve.
        opf = state.get('opf_input_choice')
        points = [p for p in points if p['opf'] == opf]
        snapshot = self.assays.fetch(start, end, [opf], force_refresh=True)
        if snapshot.get('status') not in ('fresh', 'cached'):
            raise ConnectionError('New assays are unavailable; accepted corrections have been retained.')
        actual = self.actual_feed(state['mine_input_choice'], start-timedelta(days=1), end, points)
        available_now = max(awst(now), awst(snapshot.get('fetched_at') or now))
        evidence, withheld = observations(snapshot['records'], actual, catalog, plan_rows, policy, available_now,
            transport=transport_enabled(state.get('transport_settings')))
        previous = state.get('continuous_assay_state') or {}
        # Catalog revisions restart the estimator; never stack new historical
        # factors on corrections learned against an old build or prior.
        ledger = {r['id']: r for r in previous.get('evidence', [])} if previous.get('catalog') == catalog else {}
        for row in evidence:
            old = ledger.get(row['id'])
            if old and {k:v for k,v in row.items() if k!='available_at'} == {k:v for k,v in old.items() if k!='available_at'}:
                # A warehouse refresh watermark is not another lab measurement
                # and must not move a previously known correction into the future.
                row['available_at'] = old['available_at']
        # Records absent in this freshly fetched window are withdrawn, including
        # revisions that no longer pass attribution or validation.
        ledger = {k: r for k,r in ledger.items() if awst(r['end']) < start}
        ledger.update({r['id']: r for r in evidence})
        if len(ledger) > 5000:
            raise ValueError('Continuous assay ledger exceeds 5,000 windows; refresh the historical source priors.')
        result = estimate(catalog, list(ledger.values()), policy)
        result['audit'] += withheld
        result.update(checked_at=end.isoformat(), status='fresh', source_request=deepcopy(snapshot['request']),
                      active_sources=sorted(catalog), active_at=awst(now).isoformat())
        return result
