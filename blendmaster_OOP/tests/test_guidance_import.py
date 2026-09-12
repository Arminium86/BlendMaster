import tempfile
import unittest
from pathlib import Path
import pandas as pd
from classes.GuidanceImport import inspect_import, snapshot_import, retain_selection, retain_rules, current_import_revisions


class GuidanceImportTests(unittest.TestCase):
    def test_replacement_cannot_destroy_an_accepted_input(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'day.csv'
            original = 'Agent.Name,Source.Type\nEX1,Reserve\n'
            path.write_text(original)
            result = snapshot_import(inspect_import('day_plan', path), Path(folder) / 'accepted')
            path.write_text('partial')
            with self.assertRaises(ValueError):
                inspect_import('day_plan', path)
            self.assertEqual(Path(result['accepted_path']).read_text(), original)

    def test_rechecking_one_source_does_not_forget_other_input_versions(self):
        rows = [dict(kind=k, revision=k+'-v1') for k in ('two_wp','day_plan','haul_cycles','closing_balance')]
        rows += [dict(kind='day_plan', revision='day_plan-v2')] * 5
        result = current_import_revisions(rows)
        self.assertEqual(result['two_wp'], 'two_wp-v1')
        self.assertEqual(result['day_plan'], 'day_plan-v2')
    def test_retains_only_existing_choices_without_selecting_new_options(self):
        self.assertEqual(retain_selection(['A', 'B'], ['B', 'C']), (['B'], ['A']))
        self.assertEqual(retain_selection([], ['B', 'C']), ([], []))

    def test_rules_are_validated_by_both_endpoints(self):
        good = dict(grade_block_source='S', crusher_destination='B')
        bad = dict(grade_block_source='OLD', crusher_destination='B')
        self.assertEqual(retain_rules([good, bad], ['S'], ['B']), ([good], [bad]))

    def test_import_uses_whole_file_identities_and_distinguishes_product_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '2wp.csv'
            pd.DataFrame([
                {'Agent.Name': 'EX1', 'Source.Type': 'Reserve', 'Source.NamePart2': 'S',
                 'Destination.Type': 'Crusher', 'Destination.Name': 'B', 'Destination.FullName': 'Crushers/B'},
                {'Agent.Name': 'PlantAgent', 'Source.Type': 'Flow', 'Source.NamePart2': '',
                 'Destination.Type': 'Crusher', 'Destination.Name': 'P', 'Destination.FullName': 'Crushers/P'},
            ]).to_csv(path, index=False)
            result = inspect_import('two_wp', path)['catalogue']
            self.assertEqual(result['agents'], ('EX1',))
            self.assertEqual(result['product_crushers'], ('Crushers/P',))
            self.assertEqual(result['sources'], ('S',))
            path.write_text('wrong,column\n1,2\n')
            with self.assertRaises(ValueError):
                inspect_import('two_wp', path)
