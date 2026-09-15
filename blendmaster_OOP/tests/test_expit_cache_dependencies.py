import json
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import pandas as pd
from GUI.InitialiseGUI import UserInputs
from database.DatabaseContext import database_scope
from database.SQLiteDatabase import DatabaseManager
from classes.ExpitDataHandler import ExpitDataHandler
from execute.Run import Run
from tests.test_dual_schedule_ingestion import reserve_row


class ExpitCacheDependenciesTests(unittest.TestCase):
    def signature(self, context, **state):
        return UserInputs.expit_input_cache_signature(SimpleNamespace(**state),
            start_time='2026-01-01', site_context=context, schedule_path='', two_wp_path='',
            selected_agents=['EX1'], expit_mode=2, reevaluate_direct_tip=True, selected_crusher=['PC'])

    def test_calendar_solver_and_transport_changes_do_not_invalidate_payloads(self):
        context = dict(mine='CC', multi_feed_settings=dict(mode='combined_opf', tipping_points=[
            dict(name='PC', opf='OPF', direct_tip_enabled=True, targets_by_period={'preplan': {'crusher_rate':100}})]))
        original = self.signature(context)
        changed = deepcopy(context)
        changed['multi_feed_settings']['tipping_points'][0]['targets_by_period']['preplan']['crusher_rate'] = 200
        self.assertEqual(original, self.signature(changed, solver_config={'incentive': 100},
            transport_settings={'capacity': 500}, AMT_chunk_settings={'A': {'count':4}}))
        changed['multi_feed_settings']['tipping_points'][0]['direct_tip_enabled'] = False
        self.assertNotEqual(original, self.signature(changed))
        for field, value in [('aps_grade_field_mappings', {'Fe':'new'}),
                             ('direct_tip_movement_rules', ['new']), ('destination_rules', {'haul_routes': 'new'})]:
            self.assertNotEqual(original, self.signature({**context, field:value}))

    def test_anchor_is_shared_by_both_time_modes_and_does_not_roll_forward(self):
        with TemporaryDirectory() as directory, database_scope(str(Path(directory)/'cache.db')):
            signature = dict(cache_version=5, start_time='2026-01-01T00:00:00', site={'mine':'CC'})
            manager = DatabaseManager()
            manager.write_expit_input_cache(pd.DataFrame([{'payload':100}]), json.dumps(signature))
            for mode in (1,2):
                host = SimpleNamespace(time_mode_choice=mode, expit_mode_choice=2, expit_refresh_tolerance_minutes=30)
                for minutes in (0, 20, 30, 31):
                    current = {**signature, 'start_time':str(pd.Timestamp(signature['start_time'])+pd.Timedelta(minutes=minutes))}
                    frame, metadata = UserInputs.read_reusable_expit_input_cache(host, json.dumps(current))
                    self.assertEqual(frame is not None, minutes <= 30)
                host.expit_refresh_tolerance_minutes = 0
                self.assertIsNone(UserInputs.read_reusable_expit_input_cache(host, json.dumps(signature))[0])
            self.assertEqual(json.loads(manager.read_latest_expit_input_cache()[1]), signature)

    def test_sequence_refresh_reuses_parsed_payloads_and_keeps_them_unreconciled(self):
        with TemporaryDirectory() as directory, database_scope(str(Path(directory)/'cache.db')):
            path = Path(directory)/'schedule.csv'
            pd.DataFrame([reserve_row('Reserves/CC/PIT_A/BLOCK_1','PIT_A','Stockpiles/SP_A',100,
                '01/01/2026 00:00','01/01/2026 01:00')]).to_csv(path,index=False)
            runner = Run.__new__(Run)
            seen=[]
            def reconcile(handler, frame, *args):
                seen.append(frame.payload.sum())
                frame['payload'] = 5
                return frame
            with patch.object(ExpitDataHandler, 'update_transactions', reconcile):
                runner.prepare_expit_payload_transactions(pd.Timestamp('2026-01-01'),2,path,two_wp_file_path=path)
                with patch.object(ExpitDataHandler, 'process_transactions', side_effect=AssertionError('parsed twice')):
                    runner.prepare_expit_payload_transactions(pd.Timestamp('2026-01-01 01:00'),2,path,two_wp_file_path=path)
            self.assertEqual(seen,[100,100])
