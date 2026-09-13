import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from PyQt5.QtCore import QObject
from PyQt5.QtWidgets import QApplication, QWidget, QPushButton
from classes.SiteWorkflow import default_contract
from GUI.ScheduledSiteBatch import ScheduledSiteBatch


class ScheduledBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def batch(self):
        contracts = {s:{**default_contract(s),'enabled':True} for s in ('A','B')}
        h = SimpleNamespace(active_scenario_id='A', site_scenarios={s:{'name':s} for s in contracts},
            tabs=QWidget(),scenario_selector=QWidget(),prepare_inputs_button=QPushButton(),cancel_preparation_button=QPushButton(),
            background_tasks=[],access_role='support',snapshot_database=Mock(return_value=b'db'),
            save_active_scenario_state=Mock(),restore_site_scenario=Mock(),refresh_scenario_selector=Mock(),
            scenario_display_name=lambda state,**kwargs:state['name'])
        def run(message, work, done, failed):
            try: done(work())
            except Exception as exc: failed({'message':str(exc)})
        h.run_background_task=run
        c=QObject()
        c.host=h; c.active=False; c.last_attempt={}; c.status=Mock(); c.unlock=Mock(); c.start=Mock(return_value=True)
        c.batch=ScheduledSiteBatch(c,contracts)
        c.batch.timer.stop()
        self.addCleanup(c.deleteLater)
        return c,c.batch

    def complete(self,c,batch,status='inputs_ready'):
        batch.poll(); batch.poll()
        c.run=SimpleNamespace(status=status)
        batch.site_finished()

    def test_all_enabled_sites_finish_before_one_stable_publication(self):
        c,batch=self.batch()
        with patch('GUI.ScheduledSiteBatch.write_checkpoint',return_value='shared.prj') as write:
            self.complete(c,batch)
            write.assert_not_called()
            self.complete(c,batch)
            batch.poll()
            write.assert_called_once()
            self.assertEqual(set(write.call_args.args[0]),{'A','B'})
            self.assertEqual(write.call_args.args[2].name,'A.prj')
            self.assertEqual(c.start.call_count,2)
            self.assertEqual(c.host.active_scenario_id,'A')
            batch.poll()
            self.assertIsNone(c.batch)
            c.unlock.assert_called_once()

    def test_failed_site_retries_and_never_replaces_shared_project(self):
        c,batch=self.batch()
        with patch('GUI.ScheduledSiteBatch.write_checkpoint') as write:
            self.complete(c,batch,'failed')
            self.assertEqual(batch.queue[0],'A')
            self.complete(c,batch,'failed')
            self.complete(c,batch)
            batch.poll()
            write.assert_not_called()
            self.assertEqual(c.start.call_count,3)
            self.assertEqual(c.host.active_scenario_id,'A')

    def test_cancel_stops_future_sites_and_retains_previous_publication(self):
        c,batch=self.batch()
        with patch('GUI.ScheduledSiteBatch.write_checkpoint') as write:
            self.complete(c,batch)
            batch.cancelled=True
            batch.poll()
            write.assert_not_called()
            self.assertEqual(c.start.call_count,1)


if __name__ == '__main__':
    unittest.main()
