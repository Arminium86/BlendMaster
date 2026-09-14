from copy import deepcopy
import unittest
from classes.ContinuousAssays import settings, estimate, corrected_streams, observations, source_catalog


class ContinuousAssayTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {s: dict(source=s, footprint=s, build=s+'-BUILD', is_amt=False,
            dry_yield=.8, grades={'B': dict(fe=60 if s == 'A' else 62, si=4, al=2, p=.08, mn=.1)}) for s in ('A','B')}
        self.observation = dict(id='sample', opf='OPF1', brand='B', available_at='2026-09-13T09:00:00',
            start='2026-09-13T06:00:00', end='2026-09-13T07:00:00', weights={'A':.5,'B':.5}, grades={'fe':61.2})

    def test_fixed_mixture_retains_unidentified_source_uncertainty(self):
        observations = [{**self.observation, 'id': str(i)} for i in range(100)]
        result = estimate(self.catalog, observations)
        last = result['audit'][-1]['posterior_std']
        self.assertGreater(last['A'], .55)  # independent source prior is .8, not driven to zero
        self.assertAlmostEqual(result['timeline'][-1]['offsets']['fe']['A'], .2, places=3)

    def test_bounds_outliers_and_physical_limits_are_withheld(self):
        for y in (61.8, 95):
            result = estimate(self.catalog, [{**self.observation, 'grades':{'fe':y}}])
            self.assertFalse(result['timeline'])
            self.assertEqual(result['audit'][0]['status'], 'withheld')

    def test_causal_idempotent_overlay_preserves_other_streams_and_opf(self):
        result = estimate(self.catalog, [self.observation])
        config = dict(continuous_assay_state=result, time_mode_choice=1)
        streams = {'adjusted_product':self.catalog['A']['grades'], 'adjusted_rom':{'B':{'fe':58}}}
        original = deepcopy(streams)
        self.assertEqual(corrected_streams(streams, 'A', config, '2026-09-13 08:59', 'OPF1','B'), streams)
        self.assertEqual(corrected_streams(streams, 'A', config, '2026-09-13 09:01', 'OPF2','B'), streams)
        applied = corrected_streams(streams, 'A', config, '2026-09-13 09:01', 'OPF1','B')
        self.assertGreater(applied['adjusted_product']['B']['fe'], 60)
        self.assertEqual(applied['adjusted_rom'], original['adjusted_rom'])
        self.assertEqual(streams, original)
        self.assertEqual(corrected_streams(applied, 'A', config, '2026-09-13 09:01', 'OPF1','B'), applied)
        self.assertEqual(corrected_streams(streams, 'A', config, '2026-09-15 09:01', 'OPF1','B'), streams)

    def test_new_prior_is_not_contaminated_by_old_correction(self):
        config = dict(continuous_assay_state=estimate(self.catalog, [self.observation]), time_mode_choice=1)
        streams = {'adjusted_product':{'B':{'fe':63}}}
        self.assertEqual(corrected_streams(streams, 'A', config, '2026-09-13 09:01', 'OPF1','B'), streams)

    def evidence(self):
        assay = dict(opf='OPF1', brand='B', period_start='2026-09-13 06:00', period_end='2026-09-13 07:00',
            sampled_at='2026-09-13 08:00', last_updated='2026-09-13 08:30', dmt=800, grades={'fe':61.2})
        feed = [dict(SOURCE=s+'-BUILD', opf='OPF1', time='2026-09-13 06:30', wmt=500) for s in ('A','B')]
        return assay, feed

    def test_actual_dry_mass_attribution_and_late_assay(self):
        assay, feed = self.evidence()
        values, rejected = observations([assay], feed, self.catalog, [], None, '2026-09-13 09:00')
        self.assertFalse(rejected)
        self.assertEqual(values[0]['weights'], {'A':.5,'B':.5})
        self.assertEqual(values[0]['available_at'], '2026-09-13T08:30:00')
        future, rejected = observations([assay], feed, self.catalog, [], None, '2026-09-13 08:00')
        self.assertFalse(future)

    def test_unknown_source_does_not_allocate_its_error_to_known_sources(self):
        assay, feed = self.evidence()
        feed[0]['SOURCE'] = 'DIRECT-TIP'
        values, rejected = observations([assay], feed, self.catalog, [], None, '2026-09-13 09:00')
        self.assertFalse(values)
        self.assertIn('attribution', rejected[0]['reason'])

    def test_transport_requires_explicit_validated_alignment(self):
        assay, feed = self.evidence()
        self.assertFalse(observations([assay], feed, self.catalog, [], None, '2026-09-13 09:00', transport=True)[0])
        self.assertEqual(len(observations([assay], feed, self.catalog, [], dict(lag_minutes={'OPF1':0}),
            '2026-09-13 09:00', transport=True)[0]), 1)

    def test_ambiguous_amt_chunk_is_withheld(self):
        assay, feed = self.evidence()
        catalog = deepcopy(self.catalog)
        catalog['A']['is_amt'] = True
        catalog['A2'] = {**catalog['A'], 'source':'A2'}
        self.assertFalse(observations([assay], feed, catalog, [], None, '2026-09-13 09:00')[0])
        plan = [dict(source='A',source_id='A',start_datetime='2026-09-13 06:00',end_datetime='2026-09-13 07:00', source_actual_tonnes=500)]
        self.assertTrue(observations([assay], feed, catalog, plan, None, '2026-09-13 09:00')[0])

    def test_revised_window_replaces_old_sample_and_overlaps_are_not_reused(self):
        assay, feed = self.evidence()
        revised = {**assay, 'last_updated':'2026-09-13 08:45', 'grades':{'fe':61.1}}
        overlap = {**assay, 'sampled_at':'2026-09-13 08:05', 'period_start':'2026-09-13 06:15', 'period_end':'2026-09-13 07:15'}
        values, rejected = observations([assay,revised,overlap],feed,self.catalog,[],None,'2026-09-13 09:00')
        self.assertEqual(len(values),1)
        self.assertEqual(values[0]['grades']['fe'],61.1)
        self.assertIn('Overlapping', rejected[0]['reason'])

    def test_warehouse_five_minute_rows_are_one_sample_with_site_brand_alias(self):
        assay, feed = self.evidence()
        catalog = deepcopy(self.catalog)
        for r in catalog.values():
            r['grades']['SS'] = r['grades'].pop('B')
        rows = [{**assay,'opf':'CC_OPF02','brand':'CCSS','dmt':400,'period_end':'2026-09-13 06:30'},
                {**assay,'opf':'CC_OPF02','brand':'CCSS','dmt':400,'period_start':'2026-09-13 06:30'}]
        feed = [{**r,'opf':'CC OPF02'} for r in feed]
        values, rejected = observations(rows,feed,catalog,[],None,'2026-09-13 09:00')
        self.assertFalse(rejected)
        self.assertEqual(len(values),1)
        self.assertEqual(values[0]['brand'],'SS')
        self.assertEqual(values[0]['dmt'],800)

    def test_catalog_requires_build_and_mapped_product_dry_mass(self):
        row = dict(build='BUILD1', balance=1000, grade_streams={'adjusted_product':{'B':{'fe':60}}})
        self.assertFalse(source_catalog(dict(stockpile_data={'A':row})))
        row['defined_fields'] = dict(modelled_product_dmt=800,modelled_rom_wmt=1000)
        self.assertAlmostEqual(source_catalog(dict(stockpile_data={'A':row}))['A']['dry_yield'],.8)
        with self.assertRaises(ValueError):
            settings(dict(lag_minutes={'OPF1':float('nan')}))

    def test_refresh_watermark_is_not_a_new_measurement_and_revisions_replay_once(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from setup.ContinuousAssayHistory import ContinuousAssayHistory
        assay, feed = self.evidence()
        state = dict(time_mode_choice=1, mine_input_choice='MINE',opf_input_choice='OPF1',crusher_input_choice='C1',
            _continuous_plan_rows=[dict(source_id=s, opf='OPF1', start_datetime='2026-09-13 06:00',
                end_datetime='2026-09-13 12:00', source_actual_tonnes=500) for s in self.catalog],
            stockpile_data={s:dict(build=s+'-BUILD',balance=1000,grade_streams={'adjusted_product':r['grades']},
                defined_fields=dict(modelled_rom_wmt=1000,modelled_product_dmt=800)) for s,r in self.catalog.items()})
        snapshot = dict(status='fresh',records=[assay],request={})
        service = ContinuousAssayHistory(SimpleNamespace(fetch=Mock(return_value=snapshot)))
        service.actual_feed = Mock(return_value=feed)
        first = service.refresh(state,'2026-09-13 09:00')
        state['continuous_assay_state'] = first
        assay['last_updated']='2026-09-13 09:01'
        same = service.refresh(state,'2026-09-13 09:02')
        self.assertEqual(first['revision'],same['revision'])
        assay['grades']={'fe':61.1}
        revised = service.refresh(state,'2026-09-13 09:03')
        self.assertNotEqual(first['revision'],revised['revision'])
        self.assertEqual(len(revised['evidence']),1)
        self.assertEqual(revised['evidence'][0]['available_at'],'2026-09-13T09:01:00')
        self.assertEqual(first, state['continuous_assay_state'])

    def test_tighter_policy_does_not_use_previously_accepted_wider_bounds(self):
        result = estimate(self.catalog,[self.observation])
        config = dict(time_mode_choice=1,continuous_assay_state=result,continuous_assay_settings=dict(max_offset={a:.001 for a in ('fe','si','al','p','mn')}))
        streams = {'adjusted_product':self.catalog['A']['grades']}
        self.assertEqual(corrected_streams(streams,'A',config,'2026-09-13 09:01','OPF1','B'),streams)

    def test_calculation_copies_update_declared_grade_aliases_without_mutating_prior(self):
        from types import SimpleNamespace
        from classes.ContinuousAssays import apply_event
        bundle=estimate(self.catalog,[self.observation])
        prior={'adjusted_product':self.catalog['A']['grades']}
        properties={'adjusted_product_fe':60,'modelled_product_fe':59,'modelled_product_dmt':100}
        event=SimpleNamespace(grade_streams=prior,source_name='A',stockpile='A',source_properties=properties)
        apply_event(event,dict(time_mode_choice=1,continuous_assay_state=bundle,continuous_assay_opf='OPF1',current_steady_state_datetime='2026-09-13 09:01'),'B')
        self.assertGreater(event.source_properties['adjusted_product_fe'],60)
        self.assertEqual(properties['adjusted_product_fe'],60)
        self.assertEqual(event.source_properties['modelled_product_dmt'],100)
        self.assertEqual(prior['adjusted_product']['B']['fe'],60)

    def test_new_build_with_identical_grades_cannot_inherit_previous_corrections(self):
        from classes.ContinuousAssays import for_calculation
        row=dict(build='BUILD1',balance=1000,grade_streams={'adjusted_product':self.catalog['A']['grades']},
            defined_fields=dict(modelled_rom_wmt=1000,modelled_product_dmt=800))
        catalog=source_catalog(dict(stockpile_data={'A':row}))
        bundle=estimate(catalog,[{**self.observation,'weights':{'A':1},'grades':{'fe':60.2}}])
        bundle['active_sources'] = ['A']
        self.assertTrue(for_calculation(bundle,{'A':row},[],time_mode=1,active_sources=['A']))
        self.assertFalse(for_calculation(bundle,{'A':{**row,'build':'BUILD2'}},[],time_mode=1,active_sources=['A']))


if __name__ == '__main__':
    unittest.main()
