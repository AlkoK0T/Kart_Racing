import unittest
from unittest.mock import MagicMock, patch

from app import create_app
from karting.core import Runtime
from karting.modules.odtemp import ODTempSource, parse_odtemp_line
from karting.modules.screens import ContentPlanner, ScreenConfig


class SensorQualityTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()
        self.store = self.runtime.store
        self.source = ODTempSource(self.store)

    def test_invalidating_keeps_last_value_and_acquisition_time(self):
        old = self.store.set('temperature', 22, source='odtemp')
        events = []
        self.runtime.bus.subscribe('data.changed', events.append)
        self.source._invalidate_readings('stale')
        item = self.store.item('temperature')
        self.assertEqual(22, item.value)
        self.assertEqual(old.updated_at, item.updated_at)
        self.assertGreater(item.revision, old.revision)
        self.assertEqual('stale', item.quality)
        self.assertEqual(1, len(events))
        self.source._invalidate_readings('stale')
        self.assertEqual(1, len(events))

    def test_other_producer_keeps_its_fresh_value(self):
        self.store.set('temperature', 22, source='odtemp')
        fresh = self.store.set('temperature', 23, source='tcp:sender')
        self.source._invalidate_readings('unavailable')
        self.assertEqual(fresh, self.store.item('temperature'))

    def test_screen_hides_stale_value_and_shows_recovered_value(self):
        self.store.set('temperature', 22, source='odtemp')
        planner = ContentPlanner(self.store)
        config = ScreenConfig('test', 0, 0)
        self.assertIn('22', planner.frame(config, 0).top_lines[1])
        self.source._invalidate_readings('stale')
        self.assertNotIn('22', planner.frame(config, 0).top_lines[1])
        self.assertIn('—', planner.frame(config, 0).top_lines[1])
        self.store.set('temperature', 23.5, source='odtemp')
        self.assertIn('23.5', planner.frame(config, 0).top_lines[1])

    def test_api_separates_unavailable_current_value_from_last_measurement(self):
        old = self.store.set('temperature', 22, source='odtemp')
        self.source._invalidate_readings('unavailable')
        client = create_app(self.runtime).test_client()
        self.assertIsNone(client.get('/api/data').json['temperature'])
        data = client.get('/api/health').json['readings']['temperature']
        self.assertEqual(22, data['last_value'])
        self.assertEqual('unavailable', data['quality'])
        self.assertEqual(old.updated_at.isoformat(), data['received_at'])

    def test_missing_humidity_invalidates_only_owned_previous_reading(self):
        self.store.set('humidity', 60, source='odtemp')
        self.store.mark_quality('humidity', 'unavailable', owner='odtemp')
        self.assertEqual('unavailable', self.store.item('humidity').quality)
        self.store.set('humidity', 50, source='serial:other')
        self.assertFalse(self.store.mark_quality('humidity', 'unavailable', owner='odtemp'))
        self.assertEqual('good', self.store.item('humidity').quality)

    def test_invalid_measurements_do_not_reset_watchdog(self):
        for line in ('~G' + '9' * 400, '~G22;101', '~G22;-1', '~GNaN'):
            self.assertIsNone(parse_odtemp_line(line))
        self.assertEqual((22, 0), parse_odtemp_line('~G22;0'))

    def test_full_watchdog_marks_stale_at_120_seconds_and_waits_for_retry(self):
        self.store.set('temperature', 22, source='odtemp')
        observed = []
        self.runtime.bus.subscribe('data.source.odtemp',
            lambda event: observed.append((event.payload.value['state'], self.store.item('temperature').quality)))
        self.source._port = 'TEST'
        self.source._stop = MagicMock()
        self.source._stop.is_set.return_value = False
        self.source._stop.wait.side_effect = lambda interval: setattr(self.source._stop.is_set, 'return_value', True)
        device = MagicMock()
        device.__enter__.return_value = device
        device.readline.return_value = b''
        with patch('serial.Serial', return_value=device), \
             patch.object(self.source, '_identify', return_value='ODTEMP-10'), \
             patch.object(self.source, '_ensure_stream', return_value=2000), \
             patch('karting.modules.odtemp.time.monotonic', side_effect=[0, 120]), \
             self.assertLogs('karting.modules.odtemp', level='WARNING'):
            self.source._run()
        self.assertIn(('reconnecting', 'stale'), observed)
        self.source._stop.wait.assert_called_once_with(60)
