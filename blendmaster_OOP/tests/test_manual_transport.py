from copy import deepcopy
import unittest
import pandas as pd
from classes.ManualTransport import replay
from classes.SavedPlanStore import write_manual_snapshot
from classes.MaterialFlowReview import saved_flow_data
from classes.SavedResultViews import read_report
from tempfile import TemporaryDirectory
from pathlib import Path


class ManualTransportTests(unittest.TestCase):
    def fixture(self):
        transport = dict(tipping_points={'C1':dict(enabled=True, conveyor_capacity_wmt=100,
            cos_capacity_wmt=0,cos_chunks=1,rehandle_payload_wmt=100)})
        config = dict(product_build_tonnes_stream='modelled_product_wmt',
            source_property_kinds={'modelled_product_wmt':'additive','modelled_product_dmt':'additive'},
            source_property_weights={'adjusted_product_fe':'modelled_product_dmt'}, transport_history=[])
        calendar = dict(crusher_rate={'Period_1':100}, solver_config=config,
            site_context=dict(crusher='C1',opf='OPF1',transport_settings=transport))
        periods = dict(period_1_start=pd.Timestamp('2026-09-13 06:00'),period_1_end=pd.Timestamp('2026-09-13 08:00'))
        row = dict(start_datetime='2026-09-13 06:00',end_datetime='2026-09-13 08:00',steady_state_number=1,
            steady_state_duration=2,period=1,blend_ID='M1',source='A',source_id='A',source_type='stockpile',equipment='RC',
            source_actual_tonnes=200,source_opening_balance=1000,source_closing_balance=800,
            selected_grade_stream='adjusted_product',selected_grade_brand='B',product_build_source_tonnes=180,
            source_property_modelled_product_wmt=180,source_property_modelled_product_dmt=160,
            **{f'source_grade_{a}':v for a,v in dict(fe=60,si=4,al=2,p=.08,mn=.1).items()},
            **{f'selected_grade_weight_{a}_tonnes':160 for a in ('fe','si','al','p','mn')})
        return pd.DataFrame([row]),calendar,periods

    def test_arrivals_are_delayed_and_product_mass_not_counted_at_tip(self):
        frame, calendar, periods = self.fixture()
        result = replay(frame,calendar,periods,[],{'nodes':[], 'edges':[]})
        arrivals = result.attrs['transport_frames']['transport_product_arrivals']
        self.assertAlmostEqual(arrivals.source_actual_tonnes.sum(),100)
        self.assertAlmostEqual(arrivals.product_build_source_tonnes.sum(),90)
        self.assertGreaterEqual(pd.to_datetime(arrivals.start_datetime).min(),pd.Timestamp('2026-09-13 07:00'))
        self.assertEqual(result.source_actual_tonnes.sum(),200)
        contents = result.attrs['transport_frames']['transport_contents']
        self.assertAlmostEqual(contents.loc[contents['snapshot'].eq('closing')].physical_rom_wmt.sum(),100)
        self.assertNotIn('tipping_point',frame)

    def test_manual_snapshot_keeps_transport_separate_from_optimised(self):
        frame, calendar, periods = self.fixture()
        result = replay(frame,calendar,periods,[],{'nodes':[], 'edges':[]})
        class Audit:
            def __deepcopy__(self, memo):
                raise AssertionError('Snapshot writing must not copy attached audits for every column')
        result.attrs['audit_sentinel'] = Audit()
        with TemporaryDirectory() as directory:
            database = str(Path(directory)/'manual.db')
            write_manual_snapshot(database, 'Primary', result)
            self.assertEqual(len(read_report(database,'manual')),1)
            self.assertTrue(read_report(database,'optimised').empty)
            data = saved_flow_data('Primary',database,plan_type='manual')
            self.assertFalse(data['frames']['transport_movements'].empty)

    def test_stale_opening_history_is_rejected_before_publication(self):
        frame, calendar, periods = self.fixture()
        calendar['solver_config'].pop('transport_history')
        with self.assertRaisesRegex(ValueError,'opening contents'):
            replay(frame,calendar,periods,[],{})

    def test_late_direct_tip_is_not_fed_before_its_arrival(self):
        frame, calendar, periods = self.fixture()
        frame['source_type']='grade_block'
        frame['source_actual_tonnes']=50
        frame['manual_feed_available_at']='2026-09-13 07:00'
        result = replay(frame,calendar,periods,[],{})
        movements=result.attrs['transport_frames']['transport_movements']
        tips=movements.loc[movements.movement.eq('tip')]
        self.assertGreaterEqual(pd.to_datetime(tips.start_datetime).min(),pd.Timestamp('2026-09-13 07:00'))
        self.assertTrue(result.attrs['transport_frames']['transport_product_arrivals'].empty)
        frame['source_actual_tonnes']=150
        with self.assertRaisesRegex(ValueError,'after actual direct-tip arrivals'):
            replay(frame,calendar,periods,[],{})

    def test_manual_rehandle_capacity_matches_transport_service_settings(self):
        frame, calendar, periods = self.fixture()
        calendar['site_context']['transport_settings']['tipping_points']['C1'].update(spot_seconds=2400,dump_seconds=2400)
        with self.assertRaisesRegex(ValueError,'spotting and dumping capacity'):
            replay(frame,calendar,periods,[],{})


if __name__ == '__main__':
    unittest.main()
