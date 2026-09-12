import unittest
from copy import deepcopy
from classes.TargetRefresh import merge_refreshed_targets


class TargetRefreshTests(unittest.TestCase):
    def row(self, **kw):
        return dict(opf='OPF2', crusher='PC2', brand='SS', byproduct='',
                    build_name='SS Build 1', **kw)

    def test_legacy_soft_policy_and_explicit_values_survive_refresh(self):
        original = self.row(target_mode='soft', target_tonnes=170000,
                            target_p_max=.046, target_evaluation_basis='cumulative_build')
        before = deepcopy(original)
        rows, changes = merge_refreshed_targets([original], [self.row(
            target_mode='hard', target_tonnes=180000, target_p_max=.05,
            target_evaluation_basis='steady_state', planning_scenario='new')])
        for key in ('target_mode', 'target_tonnes', 'target_p_max', 'target_evaluation_basis'):
            self.assertEqual(rows[0][key], original[key])
        self.assertEqual(rows[0]['planning_scenario'], 'new')
        self.assertTrue(all(c['action'] == 'preserved' for c in changes))
        self.assertEqual(original, before)

    def test_unedited_import_values_refresh_but_manual_overrides_survive(self):
        rows, _ = merge_refreshed_targets([], [self.row(target_mode='hard', target_tonnes=100, target_p_max=.05)])
        rows[0]['target_p_max'] = .046
        updated, _ = merge_refreshed_targets(rows, [self.row(target_mode='hard', target_tonnes=120, target_p_max=.06)])
        self.assertEqual(updated[0]['target_tonnes'], 120)
        self.assertEqual(updated[0]['target_p_max'], .046)

    def test_other_points_and_missing_builds_are_not_silently_replaced(self):
        original = self.row(target_mode='soft')
        other = {**self.row(target_mode='hard'), 'crusher': 'PC3'}
        rows, changes = merge_refreshed_targets([original], [other])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['target_mode'], 'soft')
        self.assertEqual(changes[-1]['action'], 'review')
