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
