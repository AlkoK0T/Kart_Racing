import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest

from app import create_app
from karting import Runtime
from karting.domain import DEFAULT_LIMITS, Limits, ValidationError, normalize_race
from karting.ingestion import DataIngestor, default_registry, parse_json, parse_yaml
from karting.modules.files import FileSource
from karting.modules.serial import SerialSource
from karting.modules.tcp import TcpJsonSource


def valid_race():
    return {'schema_version': 1, 'race_id': 'race-42', 'state': 'running',
            'participants': {'7': {'name': 'Иван', 'category': 'men'}}, 'laps': {'7': [31.2, 30.5]}}


def valid_records():
    return {'schema_version': 1, 'date': '2026-10-10', 'children': None, 'women': None,
            'men': {'name': 'Иван', 'time': 30.5, 'kart': '7'}}


class DomainValidationTests(unittest.TestCase):
    def setUp(self):
        self.registry = default_registry()

    def rejects(self, category, data, **options):
        with self.assertRaises(ValidationError):
            self.registry.prepare(category, data, **options)

    def test_valid_race_preserves_metadata_and_calculates_results(self):
        data = valid_race()
        original = copy.deepcopy(data)
        value = self.registry.prepare('current_race', data, filename='laps.yaml')
        self.assertEqual(original, data)
        self.assertEqual('race-42', value['race_id'])
        self.assertEqual('Иван', value['participants']['7']['name'])
        self.assertEqual('laps', value['style'])
        self.assertEqual(30.5, value['current_race']['best_times']['7'])

    def test_required_race_fields_cannot_be_omitted(self):
        for field in ('race_id', 'state', 'participants', 'laps'):
            data = valid_race(); del data[field]
            with self.subTest(field=field):
                self.rejects('current_race', data)

    def test_race_states_and_styles_are_checked(self):
        for field, value in (('state', 'broken'), ('state', None), ('style', 'missing'), ('style', [])):
            data = valid_race(); data[field] = value
            self.rejects('current_race', data)
        for state in ('waiting', 'running', 'paused', 'finished'):
            data = valid_race(); data['state'] = state
            self.registry.prepare('current_race', data)

    def test_participant_keys_and_categories_are_checked(self):
        cases = [None, {}, {'12': {'name': 'Анна', 'category': 'women'}},
                 {'7': None}, {'7': {'name': '', 'category': 'men'}},
                 {'7': {'name': 'Иван', 'category': 'unknown'}}]
        for participants in cases:
            data = valid_race(); data['participants'] = participants
            self.rejects('current_race', data)

    def test_lap_values_must_be_positive_finite_numbers(self):
        for value in ('31.2', True, False, 0, -1, None, float('nan'), float('inf'), float('-inf')):
            data = valid_race(); data['laps']['7'] = [value]
            with self.subTest(value=value):
                self.rejects('current_race', data)
        for value in (None, 31.2, '31.2', {}):
            data = valid_race(); data['laps']['7'] = value
            self.rejects('current_race', data)

    def test_aggregate_overflow_is_rejected(self):
        data = valid_race(); data['laps']['7'] = [1e308, 1e308]
        self.rejects('current_race', data)

    def test_empty_race_and_empty_laps_remain_valid(self):
        data = valid_race(); data['laps']['7'] = []
        value = self.registry.prepare('current_race', data)
        self.assertIsNone(value['current_race']['best_times']['7'])
        data['participants'] = {}; data['laps'] = {}
        self.assertEqual([], self.registry.prepare('current_race', data)['current_race']['karts'])

    def test_versions_are_checked_even_in_legacy_files(self):
        for category, data in (('current_race', valid_race()), ('records_day', valid_records())):
            for version in (2, '1', True, None):
                data['schema_version'] = version
                self.rejects(category, data)
                self.rejects(category, data, legacy=True)

    def test_records_require_three_keys_and_allow_null(self):
        value = self.registry.prepare('records_day', valid_records())
        self.assertIsNone(value['children'])
        for field in ('children', 'women', 'men'):
            data = valid_records(); del data[field]
            self.rejects('records_day', data)
        self.registry.prepare('records_day', {'children': None, 'women': None, 'men': None})

    def test_record_shapes_and_values_are_checked(self):
        for value in (30, '30', {}, {'name': '', 'time': 30}, {'name': 'Иван', 'time': 0},
                      {'name': 'Иван', 'time': True}, {'name': 'Иван', 'time': '30'},
                      {'name': 'Иван', 'time': 30, 'kart': 7}):
            data = valid_records(); data['men'] = value
            self.rejects('records_day', data)

    def test_dates_months_and_timestamps_are_validated_if_present(self):
        for value in ('2026-02-30', '10.10.2026', None):
            data = valid_records(); data['date'] = value
            self.rejects('records_day', data)
        monthly = valid_records(); del monthly['date']; monthly['month'] = '2026-10'
        self.registry.prepare('records_month', monthly)
        for value in ('2026-13', '2026-1', None):
            monthly['month'] = value
            self.rejects('records_month', monthly)
        for value in ('2026-10-10T10:00:00', 'invalid', None):
            data = valid_race(); data['updated_at'] = value
            self.rejects('current_race', data)
        data = valid_race(); data['started_at'] = None; data['updated_at'] = '2026-10-10T10:00:00Z'
        self.registry.prepare('current_race', data)

    def test_temperature_and_humidity_do_not_accept_arbitrary_json(self):
        for value in (None, True, '20', [20], {'other': 20}, float('nan'), float('inf')):
            self.rejects('temperature', value)
        for value in (-20, 0, 23.5, {'value': 23.5}):
            self.registry.prepare('temperature', value)
        self.rejects('humidity', 101)
        self.registry.prepare('humidity', 0)

    def test_legacy_yaml_keeps_integer_karts_and_short_records(self):
        value = self.registry.prepare('current_race', {'laps': {7: [], 12: [31.2]}}, legacy=True, filename='laps.yaml')
        self.assertEqual([], value['current_race']['laps'][7])
        self.assertEqual('laps', value['style'])
        self.assertEqual({'men': 31.2}, self.registry.prepare('records_day', {'men': 31.2}, legacy=True))
        self.rejects('records_day', {'men': 31.2})
        value = self.registry.prepare('current_race', {'laps': {7: [31.2]}}, legacy=True, filename='race.yaml')
        self.assertEqual('race', value['style'])

    def test_legacy_does_not_hide_invalid_laps_or_style(self):
        for data in ({'laps': {7: ['bad']}}, {'laps': {7: None}}, {'laps': {7: [0]}},
                     {'laps': {}, 'style': 'nonexistent'}, {}):
            self.rejects('current_race', data, legacy=True)

    def test_legacy_tables_and_example_files_remain_supported(self):
        table = {'template': 'tmpl_1', 'headers': ['Карт'], 'rows': {1: [7]}}
        self.assertEqual('', self.registry.prepare('current_race', [table], legacy=True)[0]['title'])
        self.rejects('current_race', [{**table, 'template': '../results'}], legacy=True)
        root = Path(__file__).resolve().parents[1]
        for filename in ('race.yaml', 'laps.yaml'):
            data = parse_yaml((root / 'testdata' / filename).read_bytes())
            value = self.registry.prepare('current_race', data, legacy=True, filename=filename)
            app = create_app()
            app.extensions['karting_runtime'].store.set('current_race', value)
            self.assertEqual(200, app.test_client().get('/').status_code)

    def test_participant_and_lap_limits_are_enforced(self):
        data = valid_race()
        small = default_registry(replace(DEFAULT_LIMITS, max_laps=1))
        with self.assertRaises(ValidationError):
            small.prepare('current_race', data)
        data['participants']['12'] = {'name': 'Анна', 'category': 'women'}
        data['laps']['12'] = [32]
        for limits in (replace(DEFAULT_LIMITS, max_participants=1), replace(DEFAULT_LIMITS, max_total_laps=2)):
            with self.assertRaises(ValidationError):
                default_registry(limits).prepare('current_race', data)


class ParsingAndRegistryTests(unittest.TestCase):
    def test_utf8_only_even_if_json_library_can_autodetect_utf16(self):
        for raw in (b'{"category":"temperature","data":"\xff"}', '{"data":20}'.encode('utf-16')):
            with self.assertRaises(ValidationError):
                parse_json(raw)

    def test_json_constants_duplicate_keys_and_depth_are_rejected(self):
        for raw in (b'NaN', b'Infinity', b'-Infinity', b'{"data":1,"data":2}', b'[' * 2000 + b']' * 2000):
            with self.assertRaises(ValidationError):
                parse_json(raw)

    def test_numeric_overflow_and_surrogates_are_rejected_after_parsing(self):
        registry = default_registry()
        for value in (parse_json(b'1e999'), parse_json(b'"\ud800"')):
            with self.assertRaises(ValidationError):
                registry.prepare('temperature', value)

    def test_yaml_safe_loader_rejects_duplicates_cycles_and_unsafe_tags(self):
        for raw in (b'men: 31\nmen: 32\n', b'!!python/object/apply:os.system [echo unsafe]'):
            with self.assertRaises(ValidationError):
                parse_yaml(raw)
        value = parse_yaml(b'&root {loop: *root}')
        with self.assertRaises(ValidationError):
            default_registry().prepare('forecast_tomorrow', value, legacy=True)

    def test_yaml_merge_and_dates_are_supported(self):
        value = parse_yaml(b'base: &base {value: 20}\nmerged: {<<: *base, value: 21}\n')
        self.assertEqual({'value': 21}, value['merged'])
        value = parse_yaml(b'date: 2026-10-10\nmen: 31.2\n')
        value = default_registry().prepare('records_day', value, legacy=True)
        self.assertEqual('2026-10-10', value['date'])

    def test_yaml_merge_expansion_is_bounded_before_construction(self):
        raw = b'a: &a {x: 1}\nb: &b {<<: [*a, *a]}\nc: &c {<<: [*b, *b]}\n'
        with self.assertRaises(ValidationError):
            parse_yaml(raw, replace(DEFAULT_LIMITS, max_collection=3))
        with self.assertRaises(ValidationError):
            parse_yaml(b'&root {<<: *root}')

    def test_tree_limits_and_expanded_yaml_budget_are_enforced(self):
        cases = [(replace(DEFAULT_LIMITS, max_depth=2), {'x': {'y': {'z': 1}}}),
                 (replace(DEFAULT_LIMITS, max_nodes=3), {'a': 1, 'b': 2}),
                 (replace(DEFAULT_LIMITS, max_collection=1), {'a': 1, 'b': 2}),
                 (replace(DEFAULT_LIMITS, max_string=4), {'note': 'too long'}),
                 (replace(DEFAULT_LIMITS, max_bytes=40), {'x': 'x' * 40})]
        for limits, value in cases:
            with self.subTest(limits=limits), self.assertRaises(ValidationError):
                default_registry(limits).prepare('forecast_tomorrow', value, legacy=True)
        with self.assertRaises(ValidationError):
            parse_json(b'"123456789"', replace(DEFAULT_LIMITS, max_bytes=4))

    def test_unknown_categories_require_explicit_registration(self):
        registry = default_registry()
        with self.assertRaises(ValidationError):
            registry.prepare('custom', {'text': 'ok'})
        registry.register('custom', lambda data, context: data)
        self.assertEqual({'text': 'ok'}, registry.prepare('custom', {'text': 'ok'}))
        with self.assertRaises(ValueError):
            registry.register('custom', lambda data, context: data)

    def test_envelope_errors_do_not_write_to_store(self):
        runtime = Runtime(); ingestion = DataIngestor(runtime.store)
        for message in ([], None, {}, {'data': 20}, {'category': 'temperature'},
                        {'category': None, 'data': 20}, {'category': [], 'data': 20},
                        {'category': 'custom', 'data': 20}):
            with self.subTest(message=message), self.assertRaises(ValidationError):
                ingestion.message(message, source='test')
        self.assertEqual({}, runtime.store.snapshot())


class TransportValidationTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()
        self.ingestion = DataIngestor(self.runtime.store)
        self.ingestion.message({'category': 'current_race', 'data': valid_race()}, source='test')
        self.ingestion.message({'category': 'records_day', 'data': valid_records()}, source='test')
        self.ingestion.message({'category': 'temperature', 'data': 20}, source='test')
        self.previous = copy.deepcopy(self.runtime.store.snapshot())
        self.events = []
        self.runtime.bus.subscribe('*', self.events.append)
        self.tcp = TcpJsonSource(self.runtime.store, port=0, ingestor=self.ingestion)
        self.tcp.start()
        self.addCleanup(self.tcp.stop)

    def exchange(self, connection, stream, value):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode('utf-8')
        connection.sendall(raw + b'\n')
        return json.loads(stream.readline())

    def test_tcp_rejects_invalid_snapshots_without_writes_then_accepts_valid_one(self):
        bad_race = valid_race(); bad_race['laps']['7'] = ['bad']
        cases = [None, [], {}, {'category': 'records_day'},
                 {'category': 'temperature', 'data': None},
                 {'category': 'temperature', 'data': float('nan')},
                 {'category': 'temperature', 'data': True},
                 {'category': 'temperature', 'data': 1e999},
                 {'category': 'records_day', 'data': {'men': 30}},
                 {'category': 'current_race', 'data': {'laps': {'7': [31]}}},
                 {'category': 'current_race', 'data': bad_race},
                 {'category': 'unknown', 'data': 1},
                 b'{"category":"temperature","data":"\xff"}',
                 b'{"category":"temperature","data":20,"data":30}']
        with socket.create_connection(self.tcp._server.server_address, timeout=2) as connection:
            with connection.makefile('rb') as stream:
                for value in cases:
                    with self.subTest(value=value):
                        result = self.exchange(connection, stream, value)
                        self.assertIs(result['ok'], False)
                        self.assertIn('field', result)
                        self.assertEqual(self.previous, self.runtime.store.snapshot())
                        self.assertEqual([], self.events)
                self.assertEqual({'ok': True}, self.exchange(connection, stream,
                                 {'category': 'temperature', 'data': 23.75}))
        self.assertEqual(23.75, self.runtime.store.get('temperature'))

    def test_tcp_size_limit_rejects_before_changing_store(self):
        ingestion = DataIngestor(self.runtime.store, default_registry(replace(DEFAULT_LIMITS, max_bytes=128)))
        source = TcpJsonSource(self.runtime.store, port=0, ingestor=ingestion)
        source.start(); self.addCleanup(source.stop)
        with socket.create_connection(source._server.server_address, timeout=2) as connection:
            with connection.makefile('rb') as stream:
                result = self.exchange(connection, stream, b'x' * 200)
        self.assertFalse(result['ok'])
        self.assertEqual(self.previous, self.runtime.store.snapshot())

    def test_tcp_serial_and_versioned_yaml_produce_the_same_race(self):
        message = {'category': 'current_race', 'data': valid_race()}
        with socket.create_connection(self.tcp._server.server_address, timeout=2) as connection:
            with connection.makefile('rb') as stream:
                self.assertEqual({'ok': True}, self.exchange(connection, stream, message))
        expected = self.runtime.store.get('current_race')
        serial_runtime = Runtime()
        SerialSource(serial_runtime.store, 'TEST').consume_line(json.dumps(message).encode())
        self.assertEqual(expected, serial_runtime.store.get('current_race'))
        file_runtime = Runtime()
        with tempfile.TemporaryDirectory() as directory:
            category = Path(directory, 'current_race'); category.mkdir()
            (category / 'laps.yaml').write_text(json.dumps(valid_race()), encoding='utf-8')
            FileSource(file_runtime.store, directory).scan()
        self.assertEqual(expected, file_runtime.store.get('current_race'))

    def test_invalid_yaml_or_utf8_preserves_last_valid_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            category = Path(directory, 'records_day'); category.mkdir()
            path = category / 'valid.yaml'
            path.write_text(json.dumps(valid_records()), encoding='utf-8'); os.utime(path, (1, 1))
            source = FileSource(self.runtime.store, directory, ingestor=self.ingestion)
            source.scan(); self.events.clear()
            revision = self.runtime.store.item('records_day').revision
            cases = (b'men: .nan\n', b'men: [broken]\n', b'men: "\xff"\n',
                     b'schema_version: 2\nmen: 30\n')
            for index, raw in enumerate(cases, 2):
                (category / 'bad.yaml').write_bytes(raw); os.utime(category / 'bad.yaml', (index, index))
                with self.assertLogs('karting.modules.files', level='WARNING'):
                    source.scan()
                self.assertEqual(self.previous, self.runtime.store.snapshot())
                self.assertEqual(revision, self.runtime.store.item('records_day').revision)
                self.assertEqual([], self.events)

    def test_serial_worker_ignores_invalid_lines_and_continues_with_valid_data(self):
        source = SerialSource(self.runtime.store, 'TEST', ingestor=self.ingestion)
        lines = iter((b'NaN\n', b'{"category":"records_day"}\n', b'"bad"\n', b'23,75\n'))
        class Device:
            def readline(self, size=-1):
                value = next(lines, None)
                if value is None:
                    source._stop.set()
                    return b''
                return value
        source._serial = Device()
        with self.assertLogs('karting.modules.serial', level='WARNING') as captured:
            source._run()
        self.assertEqual(3, len(captured.output))
        self.assertEqual(23.75, self.runtime.store.get('temperature'))
        self.assertEqual(self.previous['records_day'], self.runtime.store.get('records_day'))

    def test_one_registered_extension_is_available_to_all_sources(self):
        self.ingestion.registry.register('custom', lambda data, context: data)
        message = {'category': 'custom', 'data': {'text': 'ok'}}
        with socket.create_connection(self.tcp._server.server_address, timeout=2) as connection:
            with connection.makefile('rb') as stream:
                self.assertEqual({'ok': True}, self.exchange(connection, stream, message))
        self.runtime.store.remove('custom')
        SerialSource(self.runtime.store, 'TEST', ingestor=self.ingestion).consume_line(json.dumps(message))
        self.assertEqual({'text': 'ok'}, self.runtime.store.get('custom'))
        self.runtime.store.remove('custom')
        with tempfile.TemporaryDirectory() as directory:
            category = Path(directory, 'custom'); category.mkdir()
            (category / 'data.yaml').write_text('text: ok\n', encoding='utf-8')
            FileSource(self.runtime.store, directory, ingestor=self.ingestion).scan()
        self.assertEqual({'text': 'ok'}, self.runtime.store.get('custom'))

    def test_serial_timeout_fragments_are_not_separate_measurements(self):
        source = SerialSource(self.runtime.store, 'TEST', ingestor=self.ingestion)
        lines = iter((b'2', b'', b'3.75\n'))
        runtime = self.runtime
        class Device:
            def readline(self, size=-1):
                value = next(lines, None)
                if value is None:
                    source._stop.set()
                    return b''
                self_check = runtime.store.get('temperature')
                if value in (b'', b'3.75\n'):
                    assert self_check == 20
                return value
        source._serial = Device(); source._run()
        self.assertEqual(23.75, self.runtime.store.get('temperature'))

    def test_serial_discards_the_entire_oversized_line(self):
        ingestion = DataIngestor(self.runtime.store, default_registry(replace(DEFAULT_LIMITS, max_bytes=64)))
        source = SerialSource(self.runtime.store, 'TEST', ingestor=ingestion)
        lines = iter((b'x' * 65, b'99\n', b'23.5\n'))
        class Device:
            def readline(self, size=-1):
                value = next(lines, None)
                if value is None:
                    source._stop.set()
                    return b''
                return value
        source._serial = Device()
        with self.assertLogs('karting.modules.serial', level='WARNING'):
            source._run()
        values = [event.payload.value for event in self.events if event.topic == 'data.temperature']
        self.assertEqual([23.5], values)
