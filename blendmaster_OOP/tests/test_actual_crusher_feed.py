"""Actual hopper records must not inherit the looser APS destination matching."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
import unittest

from setup.ActualCrusherFeed import destination_matches, FEED_PREDICATE
from setup.ContinuousAssayHistory import ContinuousAssayHistory
from setup.TransportOpeningHistory import TransportOpeningHistory, opening_history_events
from classes.FieldDefinitions import default_field_definitions
from tests.test_conveyor_cos import START


class ActualCrusherFeedTests(unittest.TestCase):
    points = [dict(name=n, opf=o) for n, o in (
        ('OPF01_PC','CC OPF01'), ('HAL_PC','CC OPF01'), ('OPF02_PC','CC OPF02'))]

    def test_physical_hoppers_stay_distinct_and_stockpile_prefixes_do_not_match(self):
        for name, expected in [('OP1_HOP01','OPF01_PC'), ('OP1_HOP02','HAL_PC'),
                               ('OP2_HOP01','OPF02_PC'), ('OPF02_RP01_0221',None), ('HAL01_RP01_0305',None)]:
            with self.subTest(destination=name):
                row = dict(DESTINATION_FMS=name, DESTINATION='OP1_HOP01')
                self.assertEqual([p['name'] for p in self.points if destination_matches(row,'CC',p)],
                                 [expected] if expected else [])
        self.assertTrue(destination_matches({'DESTINATION_FMS':'Crushers/RCH:In'},'CC',self.points[2]))

    def test_continuous_feed_uses_rehandle_and_direct_feed_rows_without_duplicate_tonnes(self):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        keys = ['INTERNAL_ID','SOURCE','DESTINATION_FMS','WMT_REPORTING','OBSERVED_AT']
        cursor.description = [(k,) for k in keys]
        cursor.fetchall.return_value = [
            (1,'A-BUILD','OP1_HOP01',100,START), (1,'A-BUILD','OP1_HOP01',100,START),
            (2,'B-BUILD','OP1_HOP02',200,START), (3,'C-BUILD','OP2_HOP01',300,START),
            (4,'GB','OPF02_RP01_0221',400,START)]
        connection = Mock(); connection.cursor.return_value=cursor
        loader=Mock(); loader.connect_snowflake_with_service_account.return_value=connection
        rows=ContinuousAssayHistory(SimpleNamespace(inventory_loader=loader)).actual_feed('CC',START,START+timedelta(hours=1),self.points)
        self.assertEqual([(r['tipping_point'],r['wmt']) for r in rows], [('OPF01_PC',100),('HAL_PC',200),('OPF02_PC',300)])
        query=cursor.execute.call_args_list[-1].args[0]
        self.assertIn(FEED_PREDICATE,query)
        self.assertNotIn("DISCRIMINATOR = 'PrimaryMovement'",query)
        connection.close.assert_called_once()

    def test_rehandle_quantities_use_inventory_mapping_and_scale_the_exact_build_snapshot(self):
        snapshot=dict(BASIS_WMT=1000, FEED_WMT=1000, FEED_DMT=900, PROD3_WMT=800, PROD3_DMT=700)
        mappings={'inventory':dict(modelled_rom_wmt='feed_wmt',modelled_rom_dmt='feed_dmt',
                                   modelled_product_wmt='prod3_wmt',modelled_product_dmt='prod3_dmt')}
        for a in ('fe','si','al','p','mn'):
            snapshot['GRADE_'+a.upper()]=60 if a=='fe' else 1
            snapshot[a.upper()+'_PROD3']=62 if a=='fe' else 1
            for stream in ('insitu','modelled_rom'):
                mappings['inventory'][stream+'_'+a]='GRADE_'+a.upper()
            mappings['inventory']['modelled_product_'+a]=a.upper()+'_PROD3'
        context=dict(opf='CC OPF02',field_definitions=default_field_definitions(),field_mappings=mappings)
        record=dict(INTERNAL_ID=1,SOURCE='SP-BUILD',SOURCE_FMS='SP',opf='CC OPF02',tipping_point='OPF02_PC',
                    time=START.isoformat(),wmt=200,OPENING_INVENTORY_FIELDS=snapshot)
        # Opening inventory now needs a manual approval for this exact build and OPF.
        from classes.ReconciliationApplication import ReconciliationApplication
        from classes.GradeStreams import inventory_grade_streams
        factors = {'FB': {stream: {a: {'effective': 1.0} for a in ('fe','si','al','p','mn')}
                          for stream in ('blend', 'regression')}}
        registry = {}
        ReconciliationApplication(samples=[], standard_factors=factors, opf=context['opf'], brands=['FB'],
            scenario_start=START, settings={'method': 'standard'}, registry=registry, mine='CC', allow_search=True).apply(
                inventory_grade_streams({}, ['FB'], factors, context['opf']), source_id='SP',
                source_instance='SP-BUILD', source_kind='inventory', source_wmt=1000, contributing_blocks=[])
        context.update(mine='CC', product_brands=['FB'], historical_recon_factors=factors,
                       grade_reconciliation_registry=registry, reconciliation_settings={'method': 'standard'})
        rows=opening_history_events(dict(request={'end':START.isoformat()},records=[record]),context,{})
        event=rows[0]['material']['event']
        self.assertAlmostEqual(event.source_properties['modelled_product_wmt'],.8)
        self.assertAlmostEqual(event.source_properties['modelled_product_dmt'],.7)
        self.assertEqual(event.grade_fe,62)
        self.assertEqual(snapshot['PROD3_WMT'],800)
        context['field_mappings']['inventory']['modelled_product_fe']=''
        with self.assertRaisesRegex(ValueError,'missing'):
            opening_history_events(dict(request={'end':START.isoformat()},records=[record]),context,{})

    def test_stockpile_lookup_is_bound_to_exact_build_and_movement_time(self):
        cursor=MagicMock(); cursor.__enter__.return_value=cursor
        cursor.description=[('MOVEMENT_ID',),('BASIS_WMT',),('PROD3_WMT',)]
        cursor.fetchall.return_value=[('1',1000,800)]
        connection=Mock(); connection.cursor.return_value=cursor
        rows=[dict(INTERNAL_ID=1,SOURCE='SP-BUILD-1',OBSERVED_AT=START)]
        TransportOpeningHistory.enrich_stockpile_movements(connection,rows)
        query,params=cursor.execute.call_args.args
        self.assertIn('LT.STOCKPILEBUILDNAME',query)
        self.assertIn('LT.TRANSACTIONDATETIME <= movements.OBSERVED_AT',query)
        self.assertNotIn('SP-BUILD-1',query)
        self.assertIn('SP-BUILD-1',params[0])
        self.assertEqual(rows[0]['OPENING_INVENTORY_FIELDS']['PROD3_WMT'],800)
