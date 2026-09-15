"""Bounded actual crusher movements and their modelled chemistry for FIFO opening contents."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

from classes.DestinationBuildOrder import digest
from classes.TransportSettings import transport_settings, history_lookback_hours
from classes.ExpitDataHandler import ExpitDataHandler
from classes.ConveyorCOS import moment, material
from classes.EventData import EventData
from classes.FieldDefinitions import apply_field_mappings, field_weight_map
from classes.CustomConstraints import source_property_kind
from classes.CombinedOPFReconciliation import opf_field_mappings
from classes.GradeStreams import amt_grade_streams, inventory_grade_streams, configured_brands, apply_selected_stream
from classes.ReconciliationApplication import ReconciliationApplication
from setup.InventoryBuildLineage import canonical_block, block_record, finite_number
from setup.RecentDestinationActivity import RecentDestinationActivity
from setup.ActualCrusherFeed import destination_matches, FEED_PREDICATE
from setup.AMTGradeBlockLineage import EXPIT_FEED_PROPERTY_COLUMNS, EXPIT_PRODUCT_PROPERTY_COLUMNS, GRADE_CONTROL_PROPERTY_COLUMNS, DIRECT_LINEAGE_TONNE_COLUMNS


class TransportOpeningHistory(RecentDestinationActivity):
    VERSION = 4

    def request(self, site, start, points, settings):
        settings = transport_settings(settings)
        selected = [p for p in points if settings['tipping_points'].get(p['name'], {}).get('enabled')]
        hours = {p['name']: min(self.MAX_HOURS, history_lookback_hours(
            settings['tipping_points'][p['name']], p['opening_rate'])) for p in selected}
        return dict(version=self.VERSION, site=site, operation=self.warehouse_operation(site),
                    end=moment(start).isoformat(), points=selected, hours=hours, settings=settings)

    def fetch(self, site, start, points, settings, *, cached=None):
        request = self.request(site, start, points, settings)
        if cached and cached.get('request') == request and cached.get('data_signature') == digest(cached.get('records', [])):
            return {**deepcopy(cached), 'status': 'cached'}
        if not request['points']:
            return dict(request=request, records=[], warnings=[], data_signature=digest([]), status='disabled')
        connection = self.inventory_loader.connect_snowflake_with_service_account()
        if connection is None:
            raise ConnectionError('Opening transport history is unavailable. Refresh with a warehouse connection before enabling transport.')
        try:
            query_request = deepcopy(request)
            targets = {p['name']: sum(request['settings']['tipping_points'][p['name']][key]
                for key in ('conveyor_capacity_wmt', 'cos_capacity_wmt')) for p in request['points']}
            while True:
                raw = self.query(connection, query_request)
                totals, seen_ids = dict.fromkeys(targets, 0.0), set()
                for row in raw:
                    identity = str(row['INTERNAL_ID'])
                    if identity in seen_ids or moment(row['OBSERVED_AT']) >= moment(start):
                        continue
                    seen_ids.add(identity)
                    for point in request['points']:
                        if (destination_matches(row, site, point) and moment(row['OBSERVED_AT']) >=
                                moment(start)-timedelta(hours=query_request['hours'][point['name']])):
                            totals[point['name']] += max(0.0, finite_number(row.get('WMT_REPORTING')) or 0.0)
                expand = [name for name, target in targets.items()
                          if totals[name] < target and query_request['hours'][name] < self.MAX_HOURS]
                if not expand:
                    break
                for name in expand:
                    query_request['hours'][name] = min(self.MAX_HOURS, max(1.0, query_request['hours'][name]*2))
        finally:
            connection.close()
        records, warnings, seen = [], [], set()
        for row in raw:
            identity = str(row['INTERNAL_ID'])
            if identity in seen:
                continue
            seen.add(identity)
            matches = [p for p in request['points'] if destination_matches(row, site, p)]
            if len(matches) != 1:
                if len(matches) > 1:
                    raise ValueError('An actual destination matches several selected crushers; correct the crusher mapping.')
                continue
            point = matches[0]
            time = moment(row['OBSERVED_AT'])
            if not moment(start)-timedelta(hours=query_request['hours'][point['name']]) <= time < moment(start):
                continue
            wmt = finite_number(row.get('WMT_REPORTING'))
            if wmt is None or wmt <= 0:
                continue
            record = {k: (float(v) if hasattr(v, 'as_tuple') else v.isoformat() if hasattr(v, 'isoformat') else v)
                      for k, v in row.items()}
            record.update(tipping_point=point['name'], opf=point['opf'], time=time.isoformat(), wmt=wmt)
            records.append(record)
        # Retain the boundary movement intact: the conveyor/COS initializer
        # splits its physical tonnes without changing its source-grade basis.
        selected, amounts = [], dict.fromkeys(targets, 0.0)
        for row in sorted(records, key=lambda r:(r['time'], str(r['INTERNAL_ID'])), reverse=True):
            name = row['tipping_point']
            if amounts[name] < targets[name]:
                selected.append(row)
                amounts[name] += row['wmt']
        records = list(reversed(selected))
        for name, target in targets.items():
            if amounts[name] < target:
                warnings.append(f'{name}: only {amounts[name]:,.1f} of {target:,.1f} opening WMT found within {self.MAX_HOURS} hours.')
        return dict(request=request, records=records, warnings=warnings, queried_hours=query_request['hours'],
                    data_signature=digest(records), status='fresh')

    def query(self, connection, request):
        columns = list(dict.fromkeys(['expit.INTERNAL_ID', 'expit.SOURCE', 'expit.SOURCE_FMS',
            'expit.DESTINATION', 'expit.DESTINATION_FMS', 'expit.WMT_REPORTING', 'expit.FE', 'expit.SIO2', 'expit.AL2O3', 'expit.P', 'expit.MN',
            *EXPIT_FEED_PROPERTY_COLUMNS.values(), *EXPIT_PRODUCT_PROPERTY_COLUMNS.values()]))
        start = moment(request['end'])-timedelta(hours=max(request['hours'].values()))
        sql = """SELECT """ + ', '.join(columns) + """,
          CONVERT_TIMEZONE('Australia/Perth', expit.TRANSACTION_DATETIME)::TIMESTAMP_NTZ AS OBSERVED_AT
          FROM AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_EXPIT_REHANDLE_TRANSACTIONS expit
          WHERE expit.TRANSACTION_DATETIME >= TO_TIMESTAMP_TZ(%s)
            AND expit.TRANSACTION_DATETIME < TO_TIMESTAMP_TZ(%s)
            AND UPPER(TRIM(expit.OPERATION)) = %s AND expit.IS_DELETED = FALSE
            AND """ + FEED_PREDICATE + """
          ORDER BY OBSERVED_AT, INTERNAL_ID LIMIT 50001"""
        with connection.cursor() as cursor:
            cursor.execute('ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 60')
            cursor.execute(sql, (start.isoformat()+'+08:00', request['end']+'+08:00', request['operation']))
            records = [dict(zip((c[0].upper() for c in cursor.description), row)) for row in cursor.fetchall()]
        if len(records) > 50000:
            raise ValueError('Opening history exceeds 50,000 movements. Review opening rates/capacities.')
        records = [r for r in records if any(destination_matches(r, request['site'], p) for p in request['points'])]
        self.enrich_stockpile_movements(connection, records)
        names = sorted({str(name).strip().upper() for r in records for name in (r.get('SOURCE'), r.get('SOURCE_FMS'))
                        if canonical_block(name)})
        if not names:
            return records
        # Use the same explicit Grade Control masses as AMT; never substitute
        # physical ROM tonnes for a missing modelled product quantity.
        fields = list(dict.fromkeys(['gradeblock.RECORD_CREATED_DT', 'gradeblock.GB_WET_TONNES', *DIRECT_LINEAGE_TONNE_COLUMNS.values(),
                                     *GRADE_CONTROL_PROPERTY_COLUMNS.values()]))
        block_sql = """WITH blocks AS (SELECT
          CONCAT(MINE_CODE, '_', LOCATION_NO, '_', PHASE, '_', BLAST_RL, '_', BLAST_NO, '_',
          CASE WHEN TRY_TO_NUMBER(BLAST_NO) BETWEEN 600 AND 699 AND TRY_TO_NUMBER(FLITCH_RL) IS NOT NULL
            THEN TO_VARCHAR(TRY_TO_NUMBER(FLITCH_RL)+1) ELSE FLITCH_RL END, '_', GB_NAME) AS FULL_NAME,
          """ + ', '.join(fields) + """
          FROM DA_OPERATIONS.STG_GRADECONTROL.GRADE_BLOCKS gradeblock
          WHERE gradeblock.RECORD_ACTIVE_FLAG = 'Y' AND UPPER(MINE_CODE) IN
          (SELECT SPLIT_PART(VALUE::STRING,'_',1) FROM TABLE(FLATTEN(INPUT => PARSE_JSON(%s)))))
          SELECT * FROM blocks WHERE UPPER(FULL_NAME) IN
          (SELECT VALUE::STRING FROM TABLE(FLATTEN(INPUT => PARSE_JSON(%s))))
          QUALIFY DENSE_RANK() OVER (PARTITION BY UPPER(FULL_NAME)
            ORDER BY RECORD_CREATED_DT DESC NULLS LAST) = 1"""
        # Grade Control retains inactive revisions with the same name and
        # different tonnes. Use its latest active model, as the nominal-block
        # lookup does. Keep ties so genuinely conflicting active rows still
        # reach the ambiguity check rather than choosing one arbitrarily.
        with connection.cursor() as cursor:
            cursor.execute(block_sql, (json.dumps(names), json.dumps(names)))
            models = [dict(zip((c[0].upper() for c in cursor.description), row)) for row in cursor.fetchall()]
        by_name = {}
        for row in models:
            key = row['FULL_NAME'].upper()
            if key in by_name and by_name[key] != row:
                raise ValueError(f'{key}: ambiguous Grade Control model for opening transport contents.')
            by_name[key] = row
        history_sql = """SELECT UPPER(TRIM(GRADEBLOCK)) AS FULL_NAME, LOWER(TRIM(STREAM)) AS STREAM,
            DESIGNED_WMT, IFF(LEFT(LOWER(TRIM(STREAM)),4)='prod',DESIGNED_DMT*DRY_YIELD,DESIGNED_DMT) AS STREAM_DMT
            FROM AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_GRADE_BLOCKS
            WHERE UPPER(TRIM(GRADEBLOCK)) IN (SELECT VALUE::STRING FROM TABLE(FLATTEN(INPUT => PARSE_JSON(%s))))
            AND LOWER(TRIM(STREAM)) IN ('i','rom','prod1','prod2','prod3')
            QUALIFY ROW_NUMBER() OVER (PARTITION BY UPPER(TRIM(GRADEBLOCK)),LOWER(TRIM(STREAM))
                ORDER BY MODIFIED_ON DESC NULLS LAST)=1"""
        with connection.cursor() as cursor:
            cursor.execute(history_sql,(json.dumps(names),))
            historical = [dict(zip((c[0].upper() for c in cursor.description),row)) for row in cursor.fetchall()]
        masses = {}
        for row in historical:
            masses.setdefault(row['FULL_NAME'],{})[row['STREAM']] = row
        for row in records:
            candidates = [str(row.get(key) or '').strip().upper() for key in ('SOURCE', 'SOURCE_FMS')]
            name = next((n for n in candidates if n in by_name or n in masses), candidates[0])
            model = dict(by_name.get(name, {}))
            historical = masses.get(name,{})
            feed = historical.get('rom') or historical.get('i') or {}
            model['GB_WET_TONNES'] = model.get('GB_WET_TONNES') or feed.get('DESIGNED_WMT')
            model['GB_DRY_TONNES'] = model.get('GB_DRY_TONNES') if model.get('GB_DRY_TONNES') is not None else feed.get('STREAM_DMT')
            for product in (1,2,3):
                stream = historical.get(f'prod{product}',{})
                for basis,field in [('WET','DESIGNED_WMT'),('DRY','STREAM_DMT')]:
                    key = f'PROD{product}_TONNES_{basis}'
                    if model.get(key) is None:
                        model[key] = stream.get(field)
            denominator = finite_number(model.get('GB_WET_TONNES'))
            if denominator and denominator > 0:
                for alias, field in DIRECT_LINEAGE_TONNE_COLUMNS.items():
                    value = finite_number(model.get(field.split('.')[-1]))
                    row[alias] = value * float(row['WMT_REPORTING'])/denominator if value is not None else None
            for alias, field in GRADE_CONTROL_PROPERTY_COLUMNS.items():
                row[alias] = model.get(field.split('.')[-1])
        return records

    @staticmethod
    def enrich_stockpile_movements(connection, records):
        """Read the exact source build as it existed when the movement tipped."""
        from setup.OpeningStockpileInventories import _INVENTORY_EXTRA_SELECTS
        movements = [dict(id=str(r['INTERNAL_ID']), source=r['SOURCE'], time=str(r['OBSERVED_AT']))
                     for r in records if r.get('SOURCE') and not canonical_block(r['SOURCE'])]
        if not movements:
            return
        extra = [(alias, expr) for alias, expr in _INVENTORY_EXTRA_SELECTS if 'M.' not in expr and 'SR.' not in expr]
        core = [(f'{a}_{stream.lower()}', f'LT.{sql}_{stream}_WTAVG')
                for stream in ('INSITU', 'ROM', 'PROD1', 'PROD2', 'PROD3')
                for a, sql in (('fe','FE'),('si','SIO2'),('al','AL2O3'),('p','P'),('mn','MN'))]
        core += [(f'grade_{a}', f'LT.{sql}_INSITU_WTAVG')
                 for a, sql in (('fe','FE'),('si','SIO2'),('al','AL2O3'),('p','P'),('mn','MN'))]
        columns = ', '.join(f'{expr} AS {alias}' for alias, expr in [*extra, *core])
        sql = """WITH movements AS (SELECT VALUE:id::STRING AS ID, VALUE:source::STRING AS SOURCE,
            TRY_TO_TIMESTAMP_NTZ(VALUE:time::STRING) AS OBSERVED_AT FROM TABLE(FLATTEN(INPUT => PARSE_JSON(%s))))
            SELECT movements.ID AS MOVEMENT_ID, LT.BALANCEWMT AS BASIS_WMT, LT.TRANSACTIONDATETIME AS SOURCE_AS_OF,
            """ + columns + """ FROM movements JOIN AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_STOCKPILE_TRANSACTIONS LT
            ON UPPER(LT.STOCKPILEBUILDNAME) = UPPER(movements.SOURCE) AND LT.TRANSACTIONDATETIME <= movements.OBSERVED_AT
            QUALIFY ROW_NUMBER() OVER (PARTITION BY movements.ID ORDER BY LT.TRANSACTIONDATETIME DESC) = 1"""
        with connection.cursor() as cursor:
            cursor.execute(sql, (json.dumps(movements),))
            snapshots = {str(row[0]): dict(zip((c[0].upper() for c in cursor.description), row)) for row in cursor.fetchall()}
        for row in records:
            snapshot = snapshots.get(str(row['INTERNAL_ID']))
            if snapshot:
                row['OPENING_INVENTORY_FIELDS'] = snapshot


def opening_history_events(bundle, context, config):
    result = []
    brands = configured_brands(context.get('product_brands')) or ['*']
    definitions = context.get('field_definitions') or []
    kinds = {r['name']: r.get('kind') for r in definitions}
    weights = field_weight_map(definitions)
    applications = {}
    for original in bundle.get('records', []):
        row = deepcopy(original)
        opf = row['opf']
        factors_bundle = (context.get('opf_reconciliation_inputs') or {}).get(opf) or {}
        primary = opf == context.get('opf')
        factors = factors_bundle.get('factors', context.get('historical_recon_factors', {}) if primary else {})
        raw = {str(k).lower(): v for k, v in row.items()}
        raw['feed_wmt'] = row['wmt']
        for alias, field in EXPIT_FEED_PROPERTY_COLUMNS.items():
            raw[alias.lower()] = row.get(field.split('.')[-1])
        for analyte, sql in [('fe','FE'),('si','SIO2'),('al','AL2O3'),('p','P'),('mn','MN')]:
            raw['grade_block_'+sql.lower()] = row.get(sql)
        expanded = {**row, 'FINAL_WMT': row['wmt'], 'MODELLED_PROPERTIES_JSON': raw,
                    **{'MODELLED_'+k.upper(): v for k, v in raw.items()}}
        mappings = opf_field_mappings(context.get('field_mappings'), context.get('opf'), opf)
        inventory = row.get('OPENING_INVENTORY_FIELDS')
        if inventory:
            fields = apply_field_mappings(inventory, definitions, mappings, 'inventory')
            basis = finite_number(inventory.get('BASIS_WMT'))
            if basis is None or basis <= 0:
                raise ValueError(f'{row["SOURCE"]}: opening source build has no positive physical basis at the movement time.')
            fields = {k: v*row['wmt']/basis if v is not None and source_property_kind(k, kinds) == 'additive' else v
                      for k, v in fields.items()}
            streams = inventory_grade_streams(fields, brands, factors, opf, strict_mappings=True)
        else:
            fields = apply_field_mappings(expanded, definitions, mappings, 'amt')
            streams = amt_grade_streams(fields, fields, brands, factors, opf, strict_mappings=True)
        block = canonical_block(row.get('SOURCE')) or canonical_block(row.get('SOURCE_FMS'))
        audit = {}
        if inventory:
            from classes.ApprovedReconciliation import ReconciliationRequired, continuous_inventory, policy_signature
            from classes.GradeStreams import normalise_opf
            registry = context.get('grade_reconciliation_registry') or {}
            opening_state = dict(grade_reconciliation_registry=registry, mine_input_choice=context.get('mine'), product_brand_labels_choice=brands)
            policy = policy_signature(context.get('reconciliation_settings'), context.get('grade_reconciliation_policy_revision'))
            identities = {}
            for record in registry.get('sources', {}).values():
                identity = tuple(record.get('detail', {}).get('source_identity') or [])
                if len(identity) == 6 and (record.get('policy') == policy or continuous_inventory(opening_state, opf, identity[3], {'build': identity[4]})):
                    identities[identity] = max(identities.get(identity, ''), record.get('detail', {}).get('calculated_at') or '')
            matches = [identity for identity in identities if len(identity) == 6 and identity[0] == str(context.get('mine') or '').strip().upper() and identity[1] == normalise_opf(opf)
                       and identity[2] == 'inventory' and identity[4] == str(row.get('SOURCE') or '').strip().upper()]
            if not matches:
                raise ReconciliationRequired(f"{row.get('SOURCE')}: opening transport requires approved source factors. Opening actuals must be current before reviewing Grade Reconciliation.", workflow_page='material_flow')
            identity = max(matches, key=lambda key: identities[key])
            managed = continuous_inventory(opening_state, opf, identity[3], {'build': identity[4]})
            if managed:
                for stream in ('adjusted_rom', 'adjusted_product'):
                    streams[stream] = deepcopy(managed['grade_streams'].get(stream) or {})
                audit = deepcopy(managed['reconciliation'])
                audit.update(source_wmt=row['wmt'], prediction_scope='inventory',
                             last_adjusted=managed.get('last_adjusted') or audit.get('last_adjusted'))
            else:
                if opf not in applications:
                    applications[opf] = ReconciliationApplication(samples=[], standard_factors=factors, opf=opf, brands=brands,
                        scenario_start=bundle['request']['end'], settings=context.get('reconciliation_settings'),
                        registry=registry, mine=identity[0], policy_revision=context.get('grade_reconciliation_policy_revision'),
                        accepted_movement=True)  # Physical movements retain their already accepted source factors.
                streams, audit = applications[opf].apply(streams, source_id=identity[3], source_instance=identity[4],
                    source_kind='inventory', source_wmt=row['wmt'], contributing_blocks=[])
        # Direct-tip grade blocks already use the configured APS/global stream;
        # opening movements must not launch an inventory/hex history search.
        event = EventData(None, str(row['INTERNAL_ID']), 'grade_block', 'Opening actual movement', 0, 0, 1,
            0,0,0,0,0,row['wmt'],row['wmt'],0,'Reclaim',None,
            source_name=block or str(row.get('SOURCE_FMS') or row['SOURCE']), grade_streams=streams,
            source_properties={k:v for k,v in fields.items() if v is not None},
            source_property_kinds=kinds, source_property_weights=weights)
        event._multi_opf = opf
        event._multi_point = row['tipping_point']
        warnings = apply_selected_stream(event, config.get('selected_data_stream','adjusted_product'), brands[0])
        if any(w.get('used_stream') != config.get('selected_data_stream','adjusted_product') for w in warnings):
            raise ValueError(f'{event.source_name}: opening transport grades are missing. Review the actual history and AMT field mappings.')
        entry = material(event, point=row['tipping_point'], opf=opf, provenance='actual_movement', payload_id=row['INTERNAL_ID'])
        entry['reconciliation'] = {**audit,'source_kind':'opening_crusher_movement'}
        result.append(dict(time=row['time'], wmt=row['wmt'],material=entry))
    return result
