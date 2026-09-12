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
from classes.CombinedOPFReconciliation import opf_field_mappings
from classes.GradeStreams import amt_grade_streams, configured_brands, apply_selected_stream
from classes.ReconciliationApplication import ReconciliationApplication
from setup.InventoryBuildLineage import canonical_block, block_record, finite_number
from setup.RecentDestinationActivity import RecentDestinationActivity
from setup.AMTGradeBlockLineage import EXPIT_FEED_PROPERTY_COLUMNS, EXPIT_PRODUCT_PROPERTY_COLUMNS, GRADE_CONTROL_PROPERTY_COLUMNS, DIRECT_LINEAGE_TONNE_COLUMNS


class TransportOpeningHistory(RecentDestinationActivity):
    VERSION = 1

    def request(self, site, start, points, settings):
        settings = transport_settings(settings)
        selected = [p for p in points if settings['tipping_points'].get(p['name'], {}).get('enabled')]
        hours = {p['name']: history_lookback_hours(settings['tipping_points'][p['name']], p['opening_rate']) for p in selected}
        if any(h > self.MAX_HOURS for h in hours.values()):
            raise ValueError('Conveyor/COS opening history exceeds 744 hours. Review capacities and opening rates.')
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
            raw = self.query(connection, request)
        finally:
            connection.close()
        records, warnings, seen = [], [], set()
        for row in raw:
            identity = str(row['INTERNAL_ID'])
            if identity in seen:
                continue
            seen.add(identity)
            matches = [p for p in request['points'] if
                ExpitDataHandler.crusher_destination_matches(row.get('DESTINATION_FMS'), site, p['name'], p['opf'])]
            if len(matches) != 1:
                if len(matches) > 1:
                    raise ValueError('An actual destination matches several selected crushers; correct the crusher mapping.')
                continue
            point = matches[0]
            time = moment(row['OBSERVED_AT'])
            if not moment(start)-timedelta(hours=request['hours'][point['name']]) <= time < moment(start):
                continue
            wmt = finite_number(row.get('WMT_REPORTING'))
            if wmt is None or wmt <= 0:
                continue
            record = {k: (float(v) if hasattr(v, 'as_tuple') else v.isoformat() if hasattr(v, 'isoformat') else v)
                      for k, v in row.items()}
            record.update(tipping_point=point['name'], opf=point['opf'], time=time.isoformat(), wmt=wmt)
            records.append(record)
        return dict(request=request, records=records, warnings=warnings, data_signature=digest(records), status='fresh')

    def query(self, connection, request):
        columns = list(dict.fromkeys(['expit.INTERNAL_ID', 'expit.SOURCE', 'expit.SOURCE_FMS',
            'expit.DESTINATION_FMS', 'expit.WMT_REPORTING', 'expit.FE', 'expit.SIO2', 'expit.AL2O3', 'expit.P', 'expit.MN',
            *EXPIT_FEED_PROPERTY_COLUMNS.values(), *EXPIT_PRODUCT_PROPERTY_COLUMNS.values()]))
        start = moment(request['end'])-timedelta(hours=max(request['hours'].values()))
        sql = """SELECT """ + ', '.join(columns) + """,
          CONVERT_TIMEZONE('Australia/Perth', expit.TRANSACTION_DATETIME)::TIMESTAMP_NTZ AS OBSERVED_AT
          FROM AA_OPERATIONS_MANAGEMENT.SELFSERVICE.INVENTORY_EXPIT_REHANDLE_TRANSACTIONS expit
          WHERE expit.TRANSACTION_DATETIME >= TO_TIMESTAMP_TZ(%s)
            AND expit.TRANSACTION_DATETIME < TO_TIMESTAMP_TZ(%s)
            AND UPPER(TRIM(expit.OPERATION)) = %s AND expit.IS_DELETED = FALSE
            AND expit.DISCRIMINATOR = 'PrimaryMovement'
            AND ((expit.MOVEMENT_CLASSIFICATION = 'Rehandle Ore' AND expit.MOVEMENT_SUBCLASSIFICATION = 'Rehandle Ore Primary')
              OR (expit.MOVEMENT_TYPE = 'ExPit' AND expit.MOVEMENT_CLASSIFICATION = 'Expit Ore'
                  AND expit.MOVEMENT_SUBCLASSIFICATION = 'Expit Ore'))
          ORDER BY OBSERVED_AT, INTERNAL_ID LIMIT 50001"""
        with connection.cursor() as cursor:
            cursor.execute('ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 60')
            cursor.execute(sql, (start.isoformat()+'+08:00', request['end']+'+08:00', request['operation']))
            records = [dict(zip((c[0].upper() for c in cursor.description), row)) for row in cursor.fetchall()]
        if len(records) > 50000:
            raise ValueError('Opening history exceeds 50,000 movements. Review opening rates/capacities.')
        names = sorted({str(r.get('SOURCE_FMS') or '').strip().upper() for r in records})
        if not names:
            return records
        # Use the same explicit Grade Control masses as AMT; never substitute
        # physical ROM tonnes for a missing modelled product quantity.
        fields = list(dict.fromkeys(['gradeblock.GB_WET_TONNES', *DIRECT_LINEAGE_TONNE_COLUMNS.values(),
                                     *GRADE_CONTROL_PROPERTY_COLUMNS.values()]))
        block_sql = """WITH blocks AS (SELECT
          CONCAT(MINE_CODE, '_', LOCATION_NO, '_', PHASE, '_', BLAST_RL, '_', BLAST_NO, '_',
          CASE WHEN TRY_TO_NUMBER(BLAST_NO) BETWEEN 600 AND 699 AND TRY_TO_NUMBER(FLITCH_RL) IS NOT NULL
            THEN TO_VARCHAR(TRY_TO_NUMBER(FLITCH_RL)+1) ELSE FLITCH_RL END, '_', GB_NAME) AS FULL_NAME,
          """ + ', '.join(fields) + """
          FROM DA_OPERATIONS.STG_GRADECONTROL.GRADE_BLOCKS gradeblock WHERE UPPER(MINE_CODE) = %s)
          SELECT * FROM blocks WHERE UPPER(FULL_NAME) IN
          (SELECT VALUE::STRING FROM TABLE(FLATTEN(INPUT => PARSE_JSON(%s))))"""
        with connection.cursor() as cursor:
            cursor.execute(block_sql, (request['site'].upper(), json.dumps(names)))
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
            name = str(row.get('SOURCE_FMS') or '').strip().upper()
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
        fields = apply_field_mappings(expanded, definitions, mappings, 'amt')
        streams = amt_grade_streams(fields, fields, brands, factors, opf, strict_mappings=True)
        block = canonical_block(row.get('SOURCE')) or canonical_block(row.get('SOURCE_FMS'))
        if opf not in applications:
            applications[opf] = ReconciliationApplication(samples=(factors_bundle.get('reconciliation_inputs') or (context.get('reconciliation_inputs') if primary else {}) or {}).get('samples', []),
                standard_factors=factors, opf=opf, brands=brands, scenario_start=bundle['request']['end'],
                settings=context.get('reconciliation_settings'))
        streams, audit = applications[opf].apply(streams, source_id=str(row['INTERNAL_ID']), source_kind='inventory',
            source_wmt=row['wmt'], contributing_blocks=[block_record(block,row['wmt'])] if block else [])
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
