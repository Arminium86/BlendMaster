from copy import deepcopy
import unittest

from classes.CombinedOPFReconciliation import profile_signature, reusable_cache
from classes.SharedProjects import merge_inputs
from tests.test_combined_opf_reconciliation import source_state, OPFS


class ProfileReuseTests(unittest.TestCase):
    def state(self):
        state = source_state()
        state['multi_feed_configuration'] = dict(mode='combined_opf', tipping_points=[dict(name=opf + '_PC', opf=opf) for opf in OPFS])
        return state

    def test_support_cache_transfers_only_when_it_matches_all_merged_inputs(self):
        local = self.state()
        incoming = deepcopy(local)
        incoming['updated_stockpile_data']['SP']['balance'] = 200
        incoming['_combined_opf_profile_cache'] = (profile_signature(incoming, OPFS), {'prepared': 'Support'})
        merged = merge_inputs(local, incoming, ['Stockpile inventories'])
        self.assertEqual(reusable_cache(merged)[1], {'prepared': 'Support'})
        # Planner retains different AMT settings: Support's whole-profile cache
        # cannot be used for that partially merged model.
        local['AMT_chunk_settings'] = {'SP': {'chunk_count': 3}}
        partial = merge_inputs(local, incoming, ['Stockpile inventories'])
        self.assertIsNone(reusable_cache(partial))

    def test_all_chemistry_dependencies_invalidate_but_live_offsets_do_not(self):
        state = self.state()
        state['_combined_opf_profile_cache'] = (profile_signature(state, OPFS), {'prepared': True})
        for key, value in [('start_time_choice', '2026-09-02'), ('field_mappings', []),
                           ('reconciliation_settings', {'method': 'auto_max_confidence'}),
                           ('hex_sequence_table', [{'hex': 'new'}]), ('opf_reconciliation_inputs', {})]:
            changed = {**state, key: value}
            self.assertIsNone(reusable_cache(changed), key)
        state['continuous_assay_state'] = {'revision': 'new laboratory observation'}
        self.assertIsNotNone(reusable_cache(state))
        state['_combined_opf_profile_cache'] = ('legacy-signature', {'prepared': True})
        self.assertIsNone(reusable_cache(state))

    def test_checkpoint_preserves_cache_through_project_migration(self):
        import pickle
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from classes.PlanningPersistence import migrate_project_state
        from classes.WorkflowCheckpoint import write_checkpoint
        state = migrate_project_state(self.state())
        state['_combined_opf_profile_cache'] = (profile_signature(state, OPFS), {'prepared': True})
        with TemporaryDirectory() as directory:
            path = write_checkpoint({'site': state}, 'site', Path(directory)/'cached.prj', lambda _: None)
            with open(path, 'rb') as stream:
                restored = pickle.load(stream)['site_scenarios']['site']
        self.assertIsNotNone(reusable_cache(restored))
        self.assertEqual(reusable_cache(restored)[1], {'prepared': True})
