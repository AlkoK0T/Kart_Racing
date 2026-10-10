from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from karting.core import Runtime
from karting.modules.screens import ContentPlanner, ScreenConfig, format_record
from karting.timezones import display_timezone


class RecordFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()
        self.zone = timezone(timedelta(hours=3))
        self.now = datetime(2026, 10, 10, 23, 59, tzinfo=self.zone)
        self.planner = ContentPlanner(self.runtime.store, timezone='+03:00', now=lambda: self.now)

    def write(self, key, value):
        with patch('karting.core.datetime') as clock:
            clock.now.return_value = self.now.astimezone(timezone.utc)
            return self.runtime.store.set(key, value)

    def test_null_record_never_renders_python_none(self):
        self.assertEqual('—', format_record(None))
        self.assertEqual('—', format_record({'name': 'Иван', 'time': None}))
        self.assertEqual('Иван  31.2', format_record({'name': 'Иван', 'time': 31.2}))

    def test_day_record_expires_at_centre_midnight_without_new_messages(self):
        self.write('records_day', {'date': '2026-10-10', 'men': 31.2})
        config = ScreenConfig('test', 0, 0)
        self.assertEqual('31.2', self.planner.frame(config, 5).top_lines[1])
        self.now += timedelta(minutes=2)
        self.assertEqual('—', self.planner.frame(config, 5).top_lines[1])
        self.assertEqual(31.2, self.runtime.store.get('records_day')['men'])

    def test_legacy_without_date_uses_acquisition_period(self):
        self.write('records_day', {'men': 31.2})
        self.assertEqual({'men': 31.2}, self.planner.current_records())
        self.now += timedelta(days=1)
        self.assertEqual({}, self.planner.current_records())

    def test_month_record_expires_on_month_boundary(self):
        self.now = datetime(2026, 10, 31, 23, 59, tzinfo=self.zone)
        self.write('records_month', {'month': '2026-10', 'men': 31.2})
        self.assertEqual(31.2, self.planner.current_records('records_month')['men'])
        self.now += timedelta(minutes=2)
        self.assertEqual({}, self.planner.current_records('records_month'))

    def test_future_or_wrong_period_is_not_current(self):
        self.write('records_day', {'date': '2026-10-11', 'men': 31.2})
        self.assertEqual({}, self.planner.current_records())

    def test_temperature_has_separate_ttl_and_advertisement_has_none(self):
        self.write('temperature', 22)
        config = ScreenConfig('test', 0, 0, messages=('Постоянный текст',))
        self.now += timedelta(seconds=121)
        frame = self.planner.frame(config, 0)
        self.assertNotIn('22', frame.top_lines[1])
        self.assertEqual(('Постоянный текст',), frame.bottom_lines)

    def test_timezone_offsets_are_checked(self):
        self.assertEqual(self.zone, display_timezone('+03:00'))
        for value in ('+24:00', '+03:60', 'not/a/zone'):
            with self.assertRaises(ValueError):
                display_timezone(value)
