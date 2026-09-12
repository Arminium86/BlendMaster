"""Transport edge cases, history identity, persistence and native setup."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import timedelta
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import pandas as pd
from PyQt5.QtWidgets import QApplication
from classes.ConveyorCOS import ConveyorCOS
from classes.TransportPlanning import initialise_transport
from classes.TransportReports import write_transport_reports, read_transport_reports
from classes.TransportSettings import reference_rate
from GUI.TransportSetup import TransportSetup
from setup.TransportOpeningHistory import TransportOpeningHistory, opening_history_events
from tests.test_conveyor_cos import START, mat, configuration
from tests.test_multi_feed_integration import make_multi_case

class TransportIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_belt_pauses_and_changes_speed_without_changing_mass(self):
        flow = ConveyorCOS(configuration(100), START, {'A':100})
        flow.add_feed('A',mat(),100,START,START+timedelta(hours=1),100)
        flow.advance(START+timedelta(hours=1),{'A':100})
        self.assertEqual(flow.advance(START+timedelta(hours=2),{'A':0}),[])
        self.assertEqual(flow.balance('A'),100)
        output = flow.advance(START+timedelta(hours=2.25),{'A':200})
        self.assertAlmostEqual(sum(r['wmt'] for r in output),50)
        flow.assert_balance()

    def test_product_arrives_after_sources_are_exhausted(self):
        with redirect_stdout(io.StringIO()):
            case = make_multi_case(builds=[dict(build_name='Product',target_tonnes=2000)])
            case.solver_config['transport_settings'] = configuration(100)
            initialise_transport(case)
            case.run_optimization_step()
            first = case.product_build_runtime_states[0]['tonnes']
            case.steady_state_tracker += 1
            case.event_pool.get_events = Mock(return_value=[])
            case.run_optimization_step()
        self.assertAlmostEqual(case.product_build_runtime_states[0]['tonnes']-first,100)
        self.assertAlmostEqual(case.results.source_actual_tonnes.sum(),200)
        self.assertAlmostEqual(case.transport.balance('A'),0)

    def test_checkpoint_restores_fifo_and_product_together(self):
        with redirect_stdout(io.StringIO()):
            case = make_multi_case(builds=[dict(build_name='Product',target_tonnes=2000)])
            case.solver_config['transport_settings'] = configuration(100)
            initialise_transport(case)
            saved = case.capture_product_build_repair_checkpoint()
            case.run_optimization_step()
            self.assertGreater(case.transport.balance('A'),0)
            case.restore_product_build_repair_checkpoint(saved)
        self.assertEqual(case.transport.balance('A'),0)
        self.assertTrue(case.product_arrival_results.empty)
        self.assertEqual(case.current_time,case.start_time)

    def test_reports_replace_only_selected_plan_and_disable_clears_it(self):
        flow = ConveyorCOS(configuration(100),START,{'A':100})
        flow.add_feed('A',mat(),100,START,START+timedelta(hours=1),100)
        flow.advance(START+timedelta(hours=1.5),{'A':100})
        case = SimpleNamespace(transport=flow,product_arrival_results=pd.DataFrame([dict(source='SP1',source_arrival_wmt=50)]),plan_id='Primary')
        with TemporaryDirectory() as folder:
            path = Path(folder)/'reports.db'
            write_transport_reports(case,path)
            case.plan_id='Backup 1'
            write_transport_reports(case,path)
            case.plan_id='Primary'; case.transport=None
            write_transport_reports(case,path)
            self.assertTrue(read_transport_reports(path)['transport_movements'].empty)
            saved = read_transport_reports(path,'Backup 1')
            self.assertEqual(saved['transport_product_arrivals'].source_arrival_wmt.sum(),50)
            self.assertEqual(saved['transport_movements'].query("movement == 'tip'").physical_rom_wmt.sum(),100)

    def test_history_is_exactly_scoped_and_changed_request_cannot_use_saved_rows(self):
        service = TransportOpeningHistory()
        service.inventory_loader = Mock()
        service.query = Mock(return_value=[
            dict(INTERNAL_ID=1,DESTINATION_FMS='OPF01',SOURCE_FMS='GB',OBSERVED_AT=START-timedelta(minutes=30),WMT_REPORTING=100),
            dict(INTERNAL_ID=2,DESTINATION_FMS='OPF01',SOURCE_FMS='GB',OBSERVED_AT=START,WMT_REPORTING=100)])
        points = [dict(name='OPF01',opf='CB OPF',opening_rate=100)]
        cfg = dict(tipping_points={'OPF01':configuration()['tipping_points']['A']})
        saved = service.fetch('CB',START,points,cfg)
        self.assertEqual(len(saved['records']),1)
        self.assertEqual(service.fetch('CB',START,points,cfg,cached=saved)['status'],'cached')
        service.query.assert_called_once()
        service.fetch('CB',START+timedelta(hours=1),points,cfg,cached=saved)
        self.assertEqual(service.query.call_count,2)

    def test_opening_uses_explicit_fields_and_keeps_actual_provenance(self):
        from classes.FieldDefinitions import default_field_definitions
        mappings = {'modelled_rom_wmt':'feed_wmt','modelled_rom_dmt':'feed_dmt',
                    'modelled_product_wmt':'prod1_wmt','modelled_product_dmt':'prod1_dmt'}
        row = dict(INTERNAL_ID=1,SOURCE='CB_PIT_01_300_101_301_HG1',SOURCE_FMS='GB',
                   time=(START-timedelta(minutes=30)).isoformat(),wmt=100,opf='CB OPF',tipping_point='OPF01',
                   FEED_DMT=90,PROD1_WMT=80,PROD1_DMT=70)
        for analyte,raw in [('fe','FE'),('si','SIO2'),('al','AL2O3'),('p','P'),('mn','MN')]:
            row[raw] = 60 if analyte=='fe' else 1
            row['PROD1_'+raw] = 62 if analyte=='fe' else 1
            for stream in ('insitu','modelled_rom'):
                mappings[stream+'_'+analyte] = 'grade_block_'+raw.lower()
            mappings['modelled_product_'+analyte] = 'prod1_'+raw.lower()
        context = dict(opf='CB OPF',field_definitions=default_field_definitions(),field_mappings={'amt':mappings})
        bundle = dict(request={'end':START.isoformat()},records=[row])
        rows = opening_history_events(bundle,context,{})
        self.assertEqual(rows[0]['material']['provenance'],'actual_movement')
        self.assertEqual(rows[0]['material']['reconciliation']['source_kind'],'opening_crusher_movement')
        self.assertEqual(rows[0]['material']['event'].grade_fe,62)
        self.assertAlmostEqual(rows[0]['material']['event'].source_properties['modelled_product_wmt'],.8)
        context['field_mappings']['amt']['modelled_product_fe']=''
        with self.assertRaisesRegex(ValueError,'missing'):
            opening_history_events(bundle,context,{})

    def test_native_setup_roundtrip_and_reference_rate(self):
        view = TransportSetup()
        self.addCleanup(view.deleteLater)
        cfg = configuration(100,200)
        view.set_context([dict(name='A',opf='OPF1',opening_rate=100)],cfg)
        self.assertEqual(view.settings()['tipping_points']['A']['cos_capacity_wmt'],200)
        self.assertTrue(view.table.cellWidget(0,5).isEnabled())
        view.table.cellWidget(0,2).setValue(0)
        self.assertFalse(view.table.cellWidget(0,5).isEnabled())
        self.assertEqual(reference_rate({'preplan':{'crusher_rate':0},'day_1':{'crusher_rate':120}}),120)
