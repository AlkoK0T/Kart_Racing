import copy
import json
import socket
import tempfile
import unittest
from pathlib import Path

from jinja2 import StrictUndefined

from app import create_app
from karting import Runtime
from karting.modules.files import FileSource, normalize_race
from karting.modules.tcp import TcpJsonSource


class EmptyLapsTests(unittest.TestCase):
    def current(self, laps):
        return normalize_race({'laps': laps}, 'laps.yaml')['current_race']

    def test_empty_laps_do_not_create_a_result(self):
        race = self.current({'7': []})
        self.assertEqual([], race['laps']['7'])
        self.assertEqual(0, race['total_laps']['7'])
        self.assertEqual(0, race['max_time'])
        for field in ('best_times', 'avg_times', 'last_laps', 'total_time', 'time_deltas', 'positions', 'diff'):
            with self.subTest(field=field):
                self.assertIsNone(race[field]['7'])

    def test_all_empty_participants_remain_unclassified(self):
        race = self.current({'7': [], '12': []})
        self.assertEqual(['7', '12'], race['karts'])
        self.assertEqual({'7': None, '12': None}, race['positions'])
        self.assertEqual({'7': 0, '12': 0}, race['total_laps'])

    def test_no_participants_is_valid(self):
        race = self.current({})
        self.assertEqual([], race['karts'])
        self.assertEqual({}, race['best_times'])
        self.assertEqual({}, race['positions'])
        self.assertEqual(0, race['max_time'])

    def test_only_real_results_determine_rank_and_gap(self):
        race = self.current({'7': [], '12': [32.5], '3': [31.2], '9': []})
        self.assertEqual(['3', '12', '7', '9'], race['karts'])
        self.assertEqual({'7': None, '12': 2, '3': 1, '9': None}, race['positions'])
        self.assertAlmostEqual(1.3, race['time_deltas']['12'])
        self.assertEqual(0, race['time_deltas']['3'])
        self.assertIsNone(race['time_deltas']['7'])

    def test_first_lap_changes_zero_count_to_one_without_an_invented_lap(self):
        data = {'laps': {'7': [], '12': [31.2]}}
        before = normalize_race(data)['current_race']
        data['laps']['7'].append(30.5)
        after = normalize_race(data)['current_race']
        self.assertEqual([], before['laps']['7'])
        self.assertEqual([30.5], after['laps']['7'])
        self.assertEqual(1, after['total_laps']['7'])
        self.assertEqual(30.5, after['best_times']['7'])
        self.assertEqual(30.5, after['total_time']['7'])
        self.assertEqual(1, after['positions']['7'])
        self.assertIsNone(after['diff']['7'])

    def test_difference_requires_two_laps_but_real_zero_is_kept(self):
        race = self.current({'7': [32, 31], '12': [31, 31], '3': [30]})
        self.assertEqual(-1, race['diff']['7'])
        self.assertEqual(0, race['diff']['12'])
        self.assertIsNone(race['diff']['3'])
        self.assertEqual(63, race['total_time']['7'])
        self.assertEqual(31.5, race['avg_times']['7'])

    def test_normalization_does_not_change_input_or_alias_lap_lists(self):
        data = {'laps': {7: [], 12: [31.2]}, 'state': 'waiting'}
        original = copy.deepcopy(data)
        normalized = normalize_race(data, 'laps.yaml')
        self.assertEqual(original, data)
        self.assertIsNot(data['laps'][12], normalized['current_race']['laps'][12])
        self.assertEqual('waiting', normalized['state'])

    def test_yaml_source_keeps_empty_laps(self):
        runtime = Runtime()
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'laps.yaml').write_text('laps:\n  7: []\n  12: [31.2]\n', encoding='utf-8')
            FileSource(runtime.store, directory).scan()
        race = runtime.store.get('current_race')['current_race']
        self.assertEqual([], race['laps'][7])
        self.assertIsNone(race['best_times'][7])
        self.assertEqual(1, race['positions'][12])

    def test_tcp_accepts_empty_laps_and_api_exposes_null_not_zero(self):
        runtime = Runtime()
        source = TcpJsonSource(runtime.store, port=0)
        source.start()
        try:
            with socket.create_connection(source._server.server_address, timeout=2) as connection:
                data = {'schema_version': 1, 'race_id': 'next', 'state': 'waiting',
                        'participants': {'7': {'name': 'Иван', 'category': 'men'}}, 'laps': {'7': []}}
                connection.sendall((json.dumps({'category': 'current_race', 'data': data}) + '\n').encode())
                with connection.makefile('rb') as stream:
                    self.assertEqual({'ok': True}, json.loads(stream.readline()))
        finally:
            source.stop()
        race = create_app(runtime).test_client().get('/api/data').json['current_race']['current_race']
        self.assertEqual([], race['laps']['7'])
        self.assertEqual(0, race['total_laps']['7'])
        self.assertIsNone(race['best_times']['7'])
        self.assertIsNone(race['positions']['7'])

    def test_missing_values_and_real_zero_have_distinct_display(self):
        macros = create_app().jinja_env.get_template('race_values.html').module
        self.assertEqual('—', macros.race_time(None))
        self.assertEqual('—', macros.race_position(None))
        self.assertEqual('1', macros.race_position(1))
        self.assertEqual('0.000', macros.race_time(0))
        self.assertEqual('+0.000', macros.race_time(0, signed=True))
        self.assertEqual('31.23', macros.race_time(31.234, precision=2))
        self.assertEqual('delta-neutral', macros.race_delta_class(None))

    def test_every_web_view_renders_empty_mixed_and_first_lap_snapshots(self):
        runtime = Runtime()
        app = create_app(runtime)
        app.config.update(TESTING=True)
        app.jinja_env.undefined = StrictUndefined
        client = app.test_client()
        previous_view = client.get('/state').json['view']
        self.addCleanup(client.put, '/state', json={'view': previous_view})
        for style, view in (('laps', 'style1'), ('laps', 'style2'), ('race', 'style1')):
            for laps in ({'7': [], '12': []}, {'7': [], '12': [31.2, 31.1]}, {'7': [30.5]}, {}):
                with self.subTest(style=style, view=view, laps=laps):
                    client.put('/state', json={'view': view})
                    runtime.store.set('current_race', normalize_race({'style': style, 'laps': laps}))
                    response = client.get('/')
                    self.assertEqual(200, response.status_code)
                    html = response.get_data(as_text=True)
                    self.assertNotIn('None', html)
                    if laps and not any(laps.values()):
                        self.assertIn('—', html)
                        self.assertNotIn('0.000', html)
                        self.assertNotIn('class="grid-row leader"', html)


if __name__ == '__main__':
    unittest.main()
