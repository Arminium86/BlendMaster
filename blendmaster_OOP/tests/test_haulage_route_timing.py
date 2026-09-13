from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import pandas as pd

from classes.HaulageRouteTiming import RouteTiming, arrival, reroute
from classes.ExpitDataHandler import ExpitDataHandler
from database.DatabaseContext import database_scope
from database.SQLiteDatabase import DatabaseManager
from tests.test_dual_schedule_ingestion import reserve_row


SOURCE = 'Reserves/CC1/PIT/01/100/20/105/HG01_1'


def route(source=SOURCE, destination='Stockpiles/SP1', truck='T1', travel=20, **extra):
    return {'Source.FullName': source, 'Source.Type': 'Reserve', 'Destination.FullName': destination,
            'Haulage.Truck': truck, 'HaulageResult.Times.LoadedTravel': travel,
            'HaulageResult.Times.SpotAtDump': 2, 'HaulageResult.Times.Dumping': 3, **extra}


class HaulageTimingTests(unittest.TestCase):
    def test_spatial_order_precedes_truck_and_other_material(self):
        index = RouteTiming([
            route(SOURCE.replace('HG01_1', 'LG09_1'), truck='T1', travel=2),
            route(SOURCE.replace('/20/105/HG01_1', '/99/999/HG02_1'), truck='T1', travel=3),
            route(SOURCE.replace('/105/HG01_1', '/106/HG02_1'), truck='T2', travel=4),
        ])
        chosen = index.lookup(SOURCE, 'SP1', 'T1')
        self.assertEqual(chosen['basis'], '2WP same material / blast')
        self.assertEqual(chosen['truck'], 'T2')
        self.assertEqual(chosen['LoadedTravel'], 4)

    def test_exact_parent_then_truck_then_first_row(self):
        index = RouteTiming([route(truck='T2', travel=12), route(truck='T1', travel=8), route(truck='T1', travel=9)])
        self.assertEqual(index.lookup(SOURCE.replace('_1', '_9'), 'SP1', 'T1')['LoadedTravel'], 8)
        self.assertEqual(index.lookup(SOURCE, 'SP1', 'T3')['LoadedTravel'], 12)

    def test_other_ore_hierarchy_cannot_cross_destination_or_mine_or_use_waste(self):
        index = RouteTiming([route(SOURCE.replace('/CC1/', '/CC2/')),
                             route(SOURCE.replace('HG01', 'WA01')),
                             route(destination='SP2'),
                             route(SOURCE.replace('HG01', 'LG09'), travel=7)])
        self.assertEqual(index.lookup(SOURCE, 'SP1')['basis'], '2WP other ore / flitch')
        self.assertIsNone(index.lookup(SOURCE, 'UNKNOWN'))
        self.assertIsNone(RouteTiming([route(SOURCE.replace('/CC1/', '/CC2/')),
                                      route(SOURCE.replace('HG01', 'WA01'))]).lookup(SOURCE, 'SP1'))

    def test_rerouting_uses_loading_start_and_explicit_24hr_fallback(self):
        index = RouteTiming([route(travel=20), route(destination='SP2', travel=40)])
        payload = dict(source=SOURCE, start_datetime=pd.Timestamp('2026-09-13 06:00'),
                       haulage=index.payload_context(route(travel=60), .1))
        first = reroute(payload, 'SP1')
        second = reroute(first, 'SP2')
        self.assertEqual(first['delivered_datetime'], pd.Timestamp('2026-09-13 06:31'))
        self.assertEqual(second['delivered_datetime'], pd.Timestamp('2026-09-13 06:51'))
        self.assertEqual(reroute(second, 'SP1')['delivered_datetime'], first['delivered_datetime'])
        missing = reroute(payload, 'SP3')
        self.assertEqual(missing['delivered_datetime'], pd.Timestamp('2026-09-13 07:11'))
        self.assertIn('24HR fallback', missing['haulage_basis'])

    def test_missing_or_invalid_2wp_components_skip_entire_row(self):
        index = RouteTiming([route(**{'HaulageResult.Times.Dumping': None}), route(travel=9)])
        self.assertEqual(index.lookup(SOURCE, 'SP1')['row'], 2)
        with self.assertRaises(ValueError):
            index.payload_context(route(travel=float('nan')), .1)

    def test_ingestion_and_database_retain_routes_for_later_assignment(self):
        with tempfile.TemporaryDirectory() as directory:
            daily, weekly = Path(directory)/'daily.csv', Path(directory)/'weekly.csv'
            row = reserve_row(SOURCE, 'PIT', 'Stockpiles/SP1', 100,
                              '13/09/2026 06:00', '13/09/2026 07:00')
            row.update({'Haulage.Truck': 'T1', 'HaulageResult.Times.LoadedTravel': 60})
            pd.DataFrame([row]).to_csv(daily, index=False)
            pd.DataFrame([route(travel=20), route(destination='SP2', travel=40)]).to_csv(weekly, index=False)
            output = ExpitDataHandler(daily, two_wp_path=weekly).process_transactions()
            first = output.iloc[0].to_dict()
            self.assertEqual(first['haulage_truck'], 'T1')
            self.assertEqual(first['haulage_loaded_travel_minutes'], 20)
            database = str(Path(directory)/'test.db')
            with database_scope(database):
                DatabaseManager().write_expit_payload_transactions_to_database(output)
            with closing(sqlite3.connect(database)) as connection:
                saved = pd.read_sql_query('SELECT * FROM expit_payload_transactions', connection).iloc[0].to_dict()
            self.assertEqual(json.loads(saved['haulage_json']), first['haulage'])
            later = arrival(saved, 'SP2')
            self.assertEqual((later['delivered_datetime']-pd.Timestamp(saved['delivered_datetime'])).total_seconds(), 1200)


if __name__ == '__main__':
    unittest.main()
