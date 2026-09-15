"""Opening transport uses the latest active Grade Control revision."""
from copy import deepcopy
from datetime import timedelta
import json
from unittest.mock import MagicMock, Mock
import unittest

from setup.TransportOpeningHistory import TransportOpeningHistory
from tests.test_conveyor_cos import START, configuration


BLOCK = 'CC_LEF08_01_0426_123_0432_SO70'


class TransportOpeningModelTests(unittest.TestCase):
    @staticmethod
    def cursor(rows):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        keys = list(rows[0]) if rows else []
        cursor.description = [(key,) for key in keys]
        cursor.fetchall.return_value = [tuple(row.get(key) for key in keys) for row in rows]
        return cursor

    def query(self, models, historical=None):
        movement = dict(INTERNAL_ID=1, SOURCE=BLOCK, SOURCE_FMS=BLOCK,
                        DESTINATION_FMS='OP2_HOP01', OBSERVED_AT=START-timedelta(minutes=30),
                        WMT_REPORTING=100)
        movement_cursor = self.cursor([movement])
        model_cursor = self.cursor(models)
        history_cursor = self.cursor(historical or [])
        connection = Mock()
        connection.cursor.side_effect = [movement_cursor, model_cursor, history_cursor]
        service = TransportOpeningHistory(inventory_loader=Mock())
        request = dict(site='CC', operation='CHRISTMAS CREEK', end=START.isoformat(),
                       hours={'OPF02_PC': 1}, points=[dict(name='OPF02_PC', opf='CC OPF02')])
        return service.query(connection, request), model_cursor

    @staticmethod
    def model(**overrides):
        return dict(FULL_NAME=BLOCK, RECORD_CREATED_DT=START, GB_WET_TONNES=1000,
                    GB_DRY_TONNES=900, PROD1_TONNES_WET=800, PROD1_TONNES_DRY=700,
                    PROD1_FINES_FE=62, **overrides)

    def test_query_selects_latest_active_revision_and_scales_once(self):
        rows, cursor = self.query([self.model()])
        sql, parameters = cursor.execute.call_args.args
        self.assertIn("gradeblock.RECORD_ACTIVE_FLAG = 'Y'", sql)
        self.assertIn('QUALIFY DENSE_RANK() OVER (PARTITION BY UPPER(FULL_NAME)', sql)
        self.assertIn('ORDER BY RECORD_CREATED_DT DESC NULLS LAST) = 1', sql)
        self.assertNotIn(BLOCK, sql)
        self.assertEqual(json.loads(parameters[0]), [BLOCK])
        self.assertEqual(json.loads(parameters[1]), [BLOCK])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['WMT_REPORTING'], 100)
        self.assertEqual(rows[0]['FEED_DMT'], 90)
        self.assertEqual(rows[0]['PROD1_WMT'], 80)
        self.assertEqual(rows[0]['PROD1_DMT'], 70)
        self.assertEqual(rows[0]['PROD1_FINES_FE'], 62)

    def test_identical_latest_rows_do_not_duplicate_movement_tonnes(self):
        model = self.model()
        rows, _ = self.query([model, deepcopy(model)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['PROD1_WMT'], 80)

    def test_conflicting_latest_rows_remain_an_error(self):
        model = self.model()
        changed = dict(model, PROD1_TONNES_WET=850)
        with self.assertRaisesRegex(ValueError, BLOCK + ': ambiguous Grade Control model'):
            self.query([model, changed])

    def test_missing_active_model_keeps_explicit_inventory_mass_fallback(self):
        rows, _ = self.query([], [
            dict(FULL_NAME=BLOCK, STREAM='rom', DESIGNED_WMT=1000, STREAM_DMT=900),
            dict(FULL_NAME=BLOCK, STREAM='prod1', DESIGNED_WMT=800, STREAM_DMT=700),
        ])
        self.assertEqual(rows[0]['FEED_DMT'], 90)
        self.assertEqual(rows[0]['PROD1_WMT'], 80)
        self.assertEqual(rows[0]['PROD1_DMT'], 70)
        self.assertIsNone(rows[0]['PROD1_FINES_FE'])

    def test_cache_before_active_revision_fix_is_refetched(self):
        service = TransportOpeningHistory(inventory_loader=Mock())
        service.query = Mock(return_value=[])
        points = [dict(name='OPF02_PC', opf='CC OPF02', opening_rate=100)]
        settings = dict(tipping_points={'OPF02_PC': configuration()['tipping_points']['A']})
        current = service.fetch('CC', START, points, settings)
        first_calls = service.query.call_count
        old = deepcopy(current)
        old['request']['version'] = 2
        refreshed = service.fetch('CC', START, points, settings, cached=old)
        self.assertEqual(refreshed['status'], 'fresh')
        self.assertEqual(service.query.call_count, first_calls * 2)
        self.assertEqual(service.fetch('CC', START, points, settings, cached=refreshed)['status'], 'cached')
        self.assertEqual(service.query.call_count, first_calls * 2)

    def test_history_expands_until_opening_tonnes_are_covered(self):
        service = TransportOpeningHistory(inventory_loader=Mock())
        points = [dict(name='OPF02_PC', opf='CC OPF02', opening_rate=100)]
        settings = dict(tipping_points={'OPF02_PC': configuration(100,200)['tipping_points']['A']})
        requests = []
        def query(connection, request):
            requests.append(deepcopy(request))
            rows = [dict(INTERNAL_ID=1,DESTINATION_FMS='OP2_HOP01',OBSERVED_AT=START-timedelta(hours=1),WMT_REPORTING=100)]
            if request['hours']['OPF02_PC'] >= 6:
                rows.append(dict(INTERNAL_ID=2,DESTINATION_FMS='OP2_HOP01',OBSERVED_AT=START-timedelta(hours=5),WMT_REPORTING=250))
            return rows
        service.query = query
        result = service.fetch('CC',START,points,settings)
        self.assertEqual([r['hours']['OPF02_PC'] for r in requests],[3,6])
        self.assertEqual(sum(r['wmt'] for r in result['records']),350)
        self.assertFalse(result['warnings'])
        self.assertEqual(service.fetch('CC',START,points,settings,cached=result)['status'],'cached')
        self.assertEqual(len(requests),2)


if __name__ == '__main__':
    unittest.main()
