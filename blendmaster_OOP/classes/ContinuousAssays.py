"""Bounded, causal Kalman source-grade updates from attributable product assays.

The observation is a dry-product-mass weighted mixture. Full covariance is
retained: repeated observations of one fixed blend do not identify each source.
Historical/modelled streams are immutable; corrections overlay adjusted product
grades only, on the matching build/chunk and after the assay became available.
"""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import math
import numpy as np
from classes.GradeStreams import ANALYTES, normalise_grade_streams, normalise_opf, numeric
from setup.ProductAssayHistory import awst
from classes.ContinuousAssayScope import live_mode


DEFAULTS = dict(enabled=True, interval_minutes=5, lookback_hours=48,
    max_age_hours=24, min_dmt=100, mass_tolerance=0.35, innovation_sigma=4.0,
    lag_minutes={}, prior_std=dict(zip(ANALYTES, (.8, .4, .3, .008, .015))),
    assay_std=dict(zip(ANALYTES, (.25, .15, .1, .003, .005))),
    max_offset=dict(zip(ANALYTES, (2., 1., 1., .015, .03))),
    max_step=dict(zip(ANALYTES, (.3, .2, .2, .003, .005))))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()


def settings(value=None):
    result = deepcopy(DEFAULTS)
    result.update(deepcopy(value or {}))
    if not isinstance(result['enabled'], bool):
        raise ValueError('Continuous assay enabled must be true or false.')
    for name, limits in dict(interval_minutes=(1, 1440), lookback_hours=(1, 168),
            max_age_hours=(1, 168), min_dmt=(1, 1e7), mass_tolerance=(0, .5), innovation_sigma=(1, 10)).items():
        number = float(result[name])
        if not math.isfinite(number) or not limits[0] <= number <= limits[1]:
            raise ValueError(f'{name} must be between {limits[0]} and {limits[1]}.')
        result[name] = number
    for name in ('prior_std', 'assay_std', 'max_offset', 'max_step'):
        for analyte in ANALYTES:
            number = float(result[name][analyte])
            if not math.isfinite(number) or not 0 < number <= 10:
                raise ValueError(f'{name}/{analyte} must be positive and at most 10 percentage points.')
            result[name][analyte] = number
    result['lag_minutes'] = {normalise_opf(k): float(v) for k, v in result['lag_minutes'].items()}
    if any(not math.isfinite(v) or not 0 <= v <= 1440 for v in result['lag_minutes'].values()):
        raise ValueError('Assay alignment lag must be between 0 and 1,440 minutes per OPF.')
    return result


def live_enabled(state):
    return live_mode(state) and settings(state.get('continuous_assay_settings'))['enabled']


def request_basis(state):
    return digest([source_catalog(state), settings(state.get('continuous_assay_settings')),
                   state.get('time_mode_choice'), state.get('selected_optimisation_plan_id'),
                   state.get('optimisation_input_revision')])


def source_catalog(state):
    """Current physical build/chunk priors, before any continuous correction."""
    inventory = {**(state.get('stockpile_data') or {}), **(state.get('updated_stockpile_data') or {})}
    chunks = state.get('hex_sequence_table') or []
    amt = {str(r.get('footprint')).upper() for r in chunks}
    candidates = [(name, name, row) for name, row in inventory.items() if str(name).upper() not in amt]
    candidates += [(str(r.get('hex') or r.get('chunk_id') or ''), str(r.get('footprint') or ''),
                    {**r, 'build': r.get('build') or r.get('BUILD') or (inventory.get(r.get('footprint')) or {}).get('build')
                     or (inventory.get(r.get('footprint')) or {}).get('BUILD')}) for r in chunks]
    result = {}
    for identity, footprint, row in candidates:
        build = str(row.get('build') or row.get('BUILD') or '').strip().upper()
        if not identity or not build or (numeric(row.get('balance')) or 0) <= 0:
            continue
        streams = normalise_grade_streams(row.get('grade_streams') or row.get('GRADE_STREAMS'), row)
        props = {**(row.get('defined_fields') or {}), **(row.get('source_properties') or {})}
        # A mapped dry product quantity is required; ROM/WMT is not an assay weight.
        dry = numeric(props.get('modelled_product_dmt'))
        rom = numeric(props.get('modelled_rom_wmt', row.get('balance')))
        if dry is None or rom is None or dry <= 0 or rom <= 0:
            continue
        record = dict(source=identity.upper(), footprint=footprint.upper(), build=build,
            grades=streams.get('adjusted_product', {}), dry_yield=float(dry)/float(rom),
            is_amt=footprint.upper() in amt)
        record['signature'] = digest(record)
        result[identity.upper()] = record
    return result


def sample_windows(records):
    """Collapse five-minute production rows carrying the same laboratory assay.

    Sample time identifies one observation, not dozens of independent updates.
    Keep the latest revision of each production interval before DMT aggregation.
    """
    intervals, rejected = {}, []
    for row in records:
        if not row.get('sampled_at'):
            rejected.append(dict(status='withheld',reason='Product has no laboratory sample time.',assay=row.get('observed_at')))
            continue
        key = (normalise_opf(row.get('opf')), row.get('brand'), row.get('period_start'), row.get('period_end'), row.get('build'), row.get('destination'))
        old = intervals.get(key)
        if old is None or str(row.get('last_updated') or row['sampled_at']) >= str(old.get('last_updated') or old['sampled_at']):
            intervals[key] = row
    groups = {}
    for row in intervals.values():
        groups.setdefault((normalise_opf(row['opf']), row['brand'], row['sampled_at']), []).append(row)
    result = []
    for (opf,brand,sample), rows in groups.items():
        try:
            dmt = sum(float(r.get('dmt') or 0) for r in rows)
            if dmt <= 0 or any(float(r.get('dmt') or 0) <= 0 for r in rows):
                raise ValueError('Sample includes production without positive dry tonnes.')
            result.append(dict(opf=opf,brand=brand,sampled_at=sample,
                period_start=min(awst(r['period_start']) for r in rows).isoformat(),
                period_end=max(awst(r['period_end']) for r in rows).isoformat(),
                last_updated=max((r.get('last_updated') or sample for r in rows)), dmt=dmt,
                grades={a: sum(float(r['grades'][a])*float(r['dmt']) for r in rows)/dmt
                        for a in ANALYTES if all(r.get('grades',{}).get(a) is not None for r in rows)}))
        except (TypeError, ValueError, KeyError) as exc:
            rejected.append(dict(status='withheld',reason=str(exc),sampled_at=sample))
    return result, rejected


def observations(assays, movements, catalog, plan_rows, config, now, *, transport=False):
    """Require complete exact-build attribution and nonoverlapping assay windows.

    An AMT chunk is attributed only where a single saved-plan chunk covers the
    whole feed window and actual movements confirm its physical inventory build.
    A direct tip, ambiguous chunk, missing dry yield, or unknown residence delay
    withholds the observation instead of assigning its error to known sources.
    """
    config, now = settings(config), awst(now)
    from bisect import bisect_left
    indexed, by_build = {}, {}
    for row in movements:
        indexed.setdefault(normalise_opf(row['opf']), []).append((awst(row['time']), row))
    for opf, values in indexed.items():
        values.sort(key=lambda item: item[0])
        indexed[opf] = ([when for when,_ in values], [row for _,row in values])
    for row in catalog.values():
        by_build.setdefault(row['build'], []).append(row)
    assays, rejected = sample_windows(assays)
    grouped = {}
    for assay in assays:
        try:
            start, end = awst(assay['period_start']), awst(assay['period_end'])
            key = (normalise_opf(assay['opf']), assay['brand'], start.isoformat(), end.isoformat())
            if end <= start or end > now or now-end > timedelta(hours=config['max_age_hours']):
                raise ValueError('Assay production window is incomplete or outside the accepted age.')
            known = max(end, *[awst(assay[k]) for k in ('sampled_at', 'last_updated') if assay.get(k)])
            if known > now:
                raise ValueError('Assay availability is in the future.')
            # Several records can represent one window. Only the latest assay
            # revision is used; equal-revision movements are DMT aggregated.
            revision = known.isoformat()
            if key not in grouped or revision > grouped[key][0]:
                grouped[key] = (revision, [assay])
            elif revision == grouped[key][0]:
                grouped[key][1].append(assay)
        except (TypeError, ValueError, KeyError) as exc:
            rejected.append(dict(status='withheld', reason=str(exc), assay=assay.get('observed_at')))
    result, used_until = [], {}
    for key, (known, rows) in sorted(grouped.items(), key=lambda item: item[0][2]):
        opf, brand, start_text, end_text = key
        try:
            available_brands = {b for r in catalog.values() for b in r['grades'] if b != '*'}
            matches = [brand] if brand in available_brands else [b for b in available_brands if brand.endswith(b)]
            if len(matches) != 1:
                raise ValueError(f'{brand}: the assay brand does not identify one configured product brand.')
            brand = matches[0]
            start, end = awst(start_text), awst(end_text)
            if start < used_until.get(opf, start):
                raise ValueError('Overlapping product windows would reuse the same actual feed.')
            if transport and opf not in config['lag_minutes']:
                raise ValueError(f'{opf}: Support must configure a validated feed-to-assay alignment lag for transport.')
            lag = timedelta(minutes=config['lag_minutes'].get(opf, 0))
            feed_start, feed_end = start-lag, end-lag
            times, population = indexed.get(opf, ([],[]))
            actual = population[bisect_left(times,feed_start):bisect_left(times,feed_end)]
            build_totals = {}
            for movement in actual:
                build = str(movement.get('SOURCE') or '').strip().upper()
                build_totals[build] = build_totals.get(build,0) + float(movement['wmt'])
            masses = {}
            for build, wmt in build_totals.items():
                candidates = by_build.get(build, [])
                if candidates and candidates[0]['is_amt']:
                    active = {str(r.get('source_id') or '').upper() for r in plan_rows
                        if normalise_opf(r.get('opf') or opf) == opf
                        and str(r.get('parent_stockpile') or r.get('source') or '').upper() == candidates[0]['footprint']
                        and awst(r['start_datetime']) <= feed_start and awst(r['end_datetime']) >= feed_end
                        and float(r.get('source_actual_tonnes') or 0) > 0}
                    candidates = [r for r in candidates if r['source'] in active]
                if len(candidates) != 1:
                    raise ValueError(f'{build or "Unknown actual source"}: exact active build/chunk attribution is unavailable.')
                source = candidates[0]
                quantity = wmt * source['dry_yield']
                masses[source['source']] = masses.get(source['source'], 0) + quantity
            dmt = sum(float(r.get('dmt') or 0) for r in rows)
            total = sum(masses.values())
            if min(dmt, total) < config['min_dmt'] or any(float(r.get('dmt') or 0) <= 0 for r in rows):
                raise ValueError('Insufficient attributable dry product tonnes.')
            if abs(total-dmt)/dmt > config['mass_tolerance']:
                raise ValueError('Actual feed dry-product yield and assayed production mass do not reconcile within bounds.')
            values = {a: sum(float(r['grades'][a])*float(r['dmt']) for r in rows)/dmt
                      for a in ANALYTES if all(r.get('grades', {}).get(a) is not None for r in rows)}
            observation = dict(id=digest((opf,brand,rows[0]['sampled_at'])), opf=opf, brand=brand, start=start_text, end=end_text,
                available_at=known, weights={s: q/total for s,q in masses.items()}, grades=values,
                dmt=dmt, attribution='actual_exact_build_and_plan_chunk' if any(catalog[s]['is_amt'] for s in masses) else 'actual_exact_build')
            result.append(observation)
            used_until[opf] = end
        except (TypeError, ValueError, KeyError) as exc:
            rejected.append(dict(status='withheld', reason=str(exc), window=list(key)))
    return result, rejected


def estimate(catalog, evidence, config=None):
    """Replay the current observation revisions from fixed priors, idempotently."""
    config = settings(config)
    identities = sorted({s for observation in evidence for s in observation.get('weights', {}) if s in catalog})
    index = {s: i for i, s in enumerate(identities)}
    states, timeline, audit = {}, [], []
    for observation in sorted(evidence, key=lambda o: (o['available_at'], o['id'])):
        if not observation.get('weights') or set(observation['weights']) - index.keys():
            continue
        opf, brand = observation['opf'], observation['brand']
        h = np.array([observation['weights'].get(s, 0) for s in identities], dtype=float)
        if not np.isfinite(h).all() or (h < 0).any() or abs(h.sum()-1) > 1e-8:
            continue
        accepted = {}
        for a, y in observation['grades'].items():
            if a not in ANALYTES or not math.isfinite(y) or not 0 <= y <= 100:
                continue
            priors = [(catalog[s]['grades'].get(brand) or catalog[s]['grades'].get('*') or {}).get(a) for s in identities]
            if any(priors[i] is None for i in np.flatnonzero(h)):
                continue
            prior = np.array([v or 0 for v in priors], dtype=float)
            state_key = (opf, brand, a)
            mean, covariance = states.setdefault(state_key, (np.zeros(len(h)), np.eye(len(h))*config['prior_std'][a]**2))
            error = y-float(h @ (prior+mean))
            variance = float(h @ covariance @ h) + config['assay_std'][a]**2
            gain = covariance @ h / variance
            delta = gain*error
            proposed = mean+delta
            reason = ''
            if abs(error) > config['innovation_sigma']*math.sqrt(variance):
                reason = 'Innovation exceeds the configured uncertainty gate.'
            elif np.max(np.abs(delta)) > config['max_step'][a] or np.max(np.abs(proposed)) > config['max_offset'][a]:
                reason = 'Correction exceeds Support step or total-offset bounds.'
            elif ((prior+proposed < 0) | (prior+proposed > 100)).any():
                reason = 'Correction would leave physical grade bounds.'
            if reason:
                audit.append(dict(id=observation['id'], analyte=a, status='withheld', reason=reason, innovation=error))
                continue
            # Joseph form retains a positive covariance under rounding.
            residual = np.eye(len(h))-np.outer(gain, h)
            covariance = residual @ covariance @ residual.T + np.outer(gain, gain)*config['assay_std'][a]**2
            states[state_key] = proposed, covariance
            accepted[a] = {s: float(proposed[index[s]]) for s in identities}
            audit.append(dict(id=observation['id'], analyte=a, status='applied', innovation=error,
                posterior_std={s: math.sqrt(max(0, covariance[index[s],index[s]])) for s in observation['weights']}))
        if accepted:
            timeline.append(dict(available_at=observation['available_at'], opf=opf, brand=brand, offsets=accepted))
    return dict(version=1, catalog=deepcopy(catalog), evidence=deepcopy(evidence), timeline=timeline, policy_signature=digest(config),
                audit=audit, revision=digest(dict(catalog=catalog, timeline=timeline, config=config)))


def corrected_streams(streams, source, config, when, opf, brand):
    if not live_mode(config or {}):
        return streams
    bundle = (config or {}).get('continuous_assay_state') or {}
    bundle = bundle.get('profiles', {}).get(opf, {}) if 'profiles' in bundle else bundle
    policy = settings((config or {}).get('continuous_assay_settings'))
    source = str(source or '').upper()
    reference = bundle.get('catalog', {}).get(source)
    if not policy['enabled'] or not reference or not when or bundle.get('policy_signature') != digest(policy):
        return streams
    time = awst(when)
    offsets = {}
    for update in bundle.get('timeline', []):
        available = awst(update['available_at'])
        if normalise_opf(update['opf']) == normalise_opf(opf) and update['brand'] == brand and available <= time and time-available <= timedelta(hours=policy['max_age_hours']):
            offsets.update({a: v[source] for a,v in update['offsets'].items() if source in v})
    if not offsets:
        return streams
    result = deepcopy(streams or {})
    normalized = normalise_grade_streams(streams)
    vector = normalized.get('adjusted_product', {}).get(brand) or normalized.get('adjusted_product', {}).get('*') or {}
    prior = reference['grades'].get(brand) or reference['grades'].get('*') or {}
    # A new chunk, fresh historical baseline, or inbound blend must not inherit
    # an offset fitted to another chemistry. Also prevents applying twice.
    if any(vector.get(a) is None or prior.get(a) is None or abs(vector[a]-prior[a]) > 1e-8 for a in offsets):
        return streams
    result.setdefault('adjusted_product', {})[brand] = {**vector, **{a: prior[a]+v for a,v in offsets.items()}}
    return result


def boundaries(config):
    if not live_enabled(config):
        return []
    bundles = [config.get('continuous_assay_state') or {}]
    bundles += [p.get('continuous_assay_state') or {} for p in (config.get('opf_profiles') or {}).values()]
    for bundle in list(bundles):
        bundles.extend(bundle.get('profiles', {}).values())
    return sorted({awst(update['available_at']) for bundle in bundles for update in bundle.get('timeline', [])})


def apply_event(event, config, brand):
    if getattr(event, 'is_grade_block', False) or not getattr(event, 'is_stockpile', True):
        return
    if not getattr(event, '_continuous_assay_eligible', True):
        return
    opf = getattr(event, '_multi_opf', None) or config.get('continuous_assay_opf')
    profile = (config.get('opf_profiles') or {}).get(opf, {})
    effective = {**config, **{k: profile[k] for k in ('continuous_assay_state', 'continuous_assay_settings') if k in profile}}
    previous = event.grade_streams
    event.grade_streams = corrected_streams(previous,
        event.source_name or event.stockpile, effective, config.get('current_steady_state_datetime'), opf, brand)
    if event.grade_streams is not previous:
        event.source_properties = corrected_properties(event.source_properties, event.grade_streams, brand)


def corrected_properties(properties, streams, brand):
    """Keep declared adjusted-product aliases consistent in the calculation copy."""
    result = dict(properties or {})
    vector = streams.get('adjusted_product', {}).get(brand) or {}
    for a,value in vector.items():
        if value is not None and 'adjusted_product_'+a in result:
            result['adjusted_product_'+a] = value
    return result


def for_calculation(bundle, inventory, chunks, *, time_mode=None, active_sources=()):
    """Use only verified current feeds with the same physical build and prior."""
    if not bundle or not live_mode({'time_mode_choice': time_mode}):
        return {}
    current = source_catalog(dict(stockpile_data=inventory, hex_sequence_table=chunks))
    catalog = bundle.get('catalog') or {}
    active = {str(source).upper() for source in active_sources}
    # Never apply a pre-scope legacy registry or an inactive source's offset.
    if not catalog or set(catalog) != set(bundle.get('active_sources') or ()) or not set(catalog) <= active:
        return {}
    return deepcopy(bundle) if all(current.get(source) == row for source, row in catalog.items()) else {}


def write_audit(database, bundle):
    from contextlib import closing
    import sqlite3
    import pandas as pd
    from classes.SavedPlanStore import replace_plan
    profiles = bundle.get('profiles') or {'':bundle}
    with closing(sqlite3.connect(database)) as connection, connection:
        for table, key in (('continuous_assay_observations','evidence'), ('continuous_assay_updates','timeline'),
                           ('continuous_assay_audit','audit')):
            frame = pd.DataFrame([{**row, 'opf': row.get('opf', opf)} for opf,b in profiles.items() for row in b.get(key, [])])
            replace_plan(connection, table, frame, '', named=False)
