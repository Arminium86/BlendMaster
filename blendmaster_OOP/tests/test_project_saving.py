"""Save As keeps the source project intact and remembers successful destinations."""
import pickle
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PyQt5.QtWidgets import QDialog, QMessageBox
from GUI.InitialiseGUI import UserInputs
from GUI.ProjectSaving import begin, choose_destination, default_destination, restore_destination


class ProjectSavingTests(unittest.TestCase):
    def host(self, original):
        return SimpleNamespace(
            active_scenario_id='site', current_project_path=str(original),
            site_scenarios={
                'site': {'database_path': 'one.db', 'solver_config': {'mode': 'soft'},
                         '_combined_opf_profile_cache': ('verified inputs', {'OPF1': {'inventory': {'A': 123}}})},
                'second': {'database_path': 'two.db', 'product_targets': []},
            },
            snapshot_database=lambda database: database.encode(),
            shared_project_settings={'model_name': 'Support Model'},
            shared_project_subscription={'path': 'upstream.prj', 'revision': [1, 2, 3]},
            access_role='planner', run_background_task=Mock(),
        )

    def test_save_as_preserves_original_and_all_sites_then_save_uses_new_file(self):
        with TemporaryDirectory() as directory:
            original = Path(directory)/'original.prj'
            original.write_bytes(b'original project remains untouched')
            copy = Path(directory)/'elsewhere'/'experiment.prj'
            host = self.host(original)
            begin(host, {'agent_story_text': 'working model'}, destination=copy, show_success=False)
            self.assertEqual(host.current_project_path, str(original))
            self.assertFalse(copy.exists())
            _, work, completed, _ = host.run_background_task.call_args.args
            completed(work())
            self.assertEqual(original.read_bytes(), b'original project remains untouched')
            self.assertEqual(Path(host.current_project_path), copy.resolve())
            with copy.open('rb') as stream:
                saved = pickle.load(stream)
            self.assertEqual(set(saved['site_scenarios']), {'site', 'second'})
            self.assertEqual(saved['site_scenarios']['second']['database_snapshot'], b'two.db')
            self.assertEqual(saved['_combined_opf_profile_cache'], host.site_scenarios['site']['_combined_opf_profile_cache'])
            self.assertEqual(saved['shared_project_subscription'], host.shared_project_subscription)
            self.assertEqual(saved['agent_story_text'], 'working model')
            host.site_scenarios['site']['solver_config']['mode'] = 'changed'
            begin(host, {}, show_success=False)
            _, work, completed, _ = host.run_background_task.call_args.args
            completed(work())
            with copy.open('rb') as stream:
                self.assertEqual(pickle.load(stream)['solver_config']['mode'], 'changed')
            self.assertEqual(original.read_bytes(), b'original project remains untouched')

    def test_failed_save_as_keeps_both_existing_files_and_current_destination(self):
        with TemporaryDirectory() as directory:
            original, target = Path(directory)/'original.prj', Path(directory)/'copy.prj'
            original.write_bytes(b'original')
            target.write_bytes(b'previous copy')
            host = self.host(original)
            begin(host, {}, destination=target, show_success=False)
            _, work, _, failed = host.run_background_task.call_args.args
            with patch('classes.WorkflowCheckpoint.pickle.dump', side_effect=OSError('Disk full')):
                with self.assertRaises(OSError):
                    work()
            with patch('GUI.ProjectSaving.QMessageBox.critical'):
                failed({'message': 'Disk full'})
            self.assertEqual(original.read_bytes(), b'original')
            self.assertEqual(target.read_bytes(), b'previous copy')
            self.assertEqual(host.current_project_path, str(original))
            self.assertFalse(host._project_save_pending)
            self.assertEqual(set(Path(directory).iterdir()), {original, target})

    def test_cancel_and_pending_save_do_not_capture_or_write_the_model(self):
        host = SimpleNamespace(current_project_path='original.prj', save_state=Mock())
        with patch('GUI.ProjectSaving.choose_destination', return_value=None) as choose:
            self.assertFalse(UserInputs.save_state_as(host))
            choose.assert_called_once_with(host)
            host._project_save_pending = True
            self.assertFalse(UserInputs.save_state_as(host))
            choose.assert_called_once()
        host.save_state.assert_not_called()
        self.assertEqual(host.current_project_path, 'original.prj')

    def test_selected_destination_uses_the_existing_save_pipeline(self):
        host = SimpleNamespace(save_state=Mock(return_value=True))
        selected = Path('experiment.prj')
        with patch('GUI.ProjectSaving.choose_destination', return_value=selected):
            self.assertTrue(UserInputs.save_state_as(host))
        host.save_state.assert_called_once_with(destination=selected)

    def test_reopening_a_renamed_copy_saves_back_to_that_copy(self):
        with TemporaryDirectory() as directory:
            first, second = Path(directory)/'first.prj', Path(directory)/'renamed.prj'
            first.touch()
            second.touch()
            host = self.host(first)
            restore_destination(host, second)
            self.assertEqual(default_destination(host), second.resolve())
            restore_destination(host, first)
            self.assertEqual(default_destination(host), first.resolve())
            restore_destination(host, 'embedded project state')
            self.assertIsNone(host.current_project_path)

    def test_loading_published_inputs_retains_a_local_working_save(self):
        with TemporaryDirectory() as directory:
            published = Path(directory)/'shared'/'model.prj'
            published.parent.mkdir()
            published.touch()
            host = self.host(published)
            shared = {'folder': str(published.parent), 'model_name': 'Support Model'}
            host.shared_project_settings = shared
            restore_destination(host, published, shared)
            with patch('GUI.ProjectSaving.Path.cwd', return_value=Path(directory)/'local'):
                self.assertEqual(default_destination(host), Path(directory)/'local'/'Support Model.prj')

    def test_dialog_cancellation_and_final_extension_replacement_are_safe(self):
        with TemporaryDirectory() as directory:
            host = self.host(Path(directory)/'original.prj')
            with patch('GUI.ProjectSaving.QFileDialog') as dialog_type:
                dialog = dialog_type.return_value
                dialog.exec_.return_value = QDialog.Rejected
                self.assertIsNone(choose_destination(host))
                dialog.exec_.return_value = QDialog.Accepted
                selected = Path(directory)/'plan.v2'
                destination = Path(str(selected) + '.prj')
                dialog.selectedFiles.return_value = [str(selected)]
                self.assertEqual(choose_destination(host), destination)
                destination.write_bytes(b'existing project')
                with patch('GUI.ProjectSaving.QMessageBox.question', return_value=QMessageBox.No) as question:
                    self.assertIsNone(choose_destination(host))
                self.assertIn(str(destination), question.call_args.args[2])
                self.assertEqual(destination.read_bytes(), b'existing project')
                with patch('GUI.ProjectSaving.QMessageBox.question', return_value=QMessageBox.Yes):
                    self.assertEqual(choose_destination(host), destination)
