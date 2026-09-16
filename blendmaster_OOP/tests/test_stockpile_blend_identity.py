import unittest
import pandas as pd
from classes.CaseModeller import CaseModeller


class StockpileBlendIdentityTests(unittest.TestCase):
    def rows(self, a=50, b=50, direct=20, block='GB1', chunk='A1'):
        return pd.DataFrame([
            dict(source_type='stockpile', parent_stockpile='A', source_id=chunk, source_actual_tonnes=a),
            dict(source_type='stockpile', parent_stockpile='B', source_id='B1', source_actual_tonnes=b),
            dict(source_type='grade_block', source_id=block, source_actual_tonnes=direct)])

    def test_only_parent_stockpile_mix_changes_blend_id(self):
        model = CaseModeller.__new__(CaseModeller)
        model.blend_ID = 1
        model.previous_chemical_blend_signature = None
        assign = model.assign_selected_chemical_blend_id
        self.assertEqual(assign(self.rows()), 1)
        self.assertEqual(assign(self.rows(a=100,b=100,direct=900,block='GB2',chunk='A2')), 1)
        self.assertEqual(assign(self.rows(a=60,b=40)), 2)
        self.assertEqual(assign(self.rows(a=0,b=0)), 3)
        self.assertEqual(assign(self.rows(a=0,b=0,block='GB3')), 3)
        self.assertEqual(assign(self.rows(a=60,b=40)), 4)

    def test_simultaneous_points_keep_independent_ids(self):
        for mode in ('multi_tipping_point', 'combined_opf'):
            with self.subTest(mode=mode):
                model = CaseModeller.__new__(CaseModeller)
                model.multi_feed_configuration = {'mode': mode}
                def rows(changed=False):
                    return pd.concat([
                        self.rows(a=60 if changed else 50, b=40 if changed else 50).assign(tipping_point='HAL', opf='OPF1'),
                        self.rows(chunk='A2' if changed else 'A1', block='GB2').assign(tipping_point='PC1', opf='OPF1'),
                        self.rows().assign(tipping_point='PC2', opf='OPF2' if mode == 'combined_opf' else 'OPF1')
                    ], ignore_index=True)
                first = model.assign_selected_point_blend_ids(rows())
                self.assertEqual(first.tolist(), [1]*9)
                second = model.assign_selected_point_blend_ids(rows(True))
                self.assertEqual(second.tolist(), [2]*3+[1]*6)
                self.assertEqual(model.assign_selected_point_blend_ids(rows(True)).tolist(), second.tolist())

    def test_point_ids_restore_with_repair_checkpoint(self):
        model = CaseModeller.__new__(CaseModeller)
        for name in model.REPAIR_CHECKPOINT_ATTRIBUTES:
            setattr(model, name, None)
        model.point_chemical_blend_state = {('OPF1', 'PC1'): (('mix',), 2)}
        checkpoint = model.capture_product_build_repair_checkpoint()
        model.point_chemical_blend_state[('OPF1', 'PC1')] = (('other',), 3)
        model.restore_product_build_repair_checkpoint(checkpoint)
        self.assertEqual(model.point_chemical_blend_state[('OPF1', 'PC1')][1], 2)

    def test_solver_noise_does_not_change_id_but_real_ratio_changes_do(self):
        for mode in ('single', 'multi_tipping_point', 'combined_opf'):
            with self.subTest(mode=mode):
                model = CaseModeller.__new__(CaseModeller)
                model.multi_feed_configuration = {'mode': mode}
                model.blend_ID = 1
                model.previous_chemical_blend_signature = None
                def assign(a):
                    rows = self.rows(a=a, b=100-a).assign(opf='OPF1', tipping_point='PC1')
                    value = model.assign_selected_point_blend_ids(rows)
                    return int(value.iloc[0]) if isinstance(value, pd.Series) else value
                self.assertEqual(assign(50), 1)
                self.assertEqual(assign(50.000001), 1)
                self.assertEqual(assign(50.00009), 1)
                # Compare against the retained mix, not the most recent noisy row.
                self.assertEqual(assign(50.00018), 2)
                self.assertEqual(assign(60), 3)

    def test_different_source_sets_never_match_on_ratio_tolerance(self):
        self.assertFalse(CaseModeller.chemical_blend_signatures_match(
            (('stockpile', 'A', 1.0),), (('stockpile', 'B', 1.0),)))
