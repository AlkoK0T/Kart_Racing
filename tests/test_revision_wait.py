import json
import re
import unittest
from threading import Event as Signal, Thread
from unittest.mock import patch

from app import create_app
from karting.core import Runtime


class RevisionWaitTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()
        self.store = self.runtime.store

    def client(self):
        app = create_app(self.runtime)
        app.config['LONG_POLL_TIMEOUT'] = 0
        return app.test_client()

    def test_deletion_increases_global_revision_and_emits_change(self):
        self.store.set('first', 1)
        second = self.store.set('second', 2)
        seen = []
        self.runtime.bus.subscribe('data.changed', seen.append)
        self.store.remove('second')
        self.assertEqual(second.revision + 1, self.store.revision)
        self.assertEqual('remove', seen[0].payload['operation'])
        self.assertEqual(self.store.revision, seen[0].payload['revision'])
        response = self.client().get('/check-updates?revision=2').json
        self.assertEqual({'updated': True, 'revision': 3}, response)
        self.store.remove('second')
        self.assertEqual(3, self.store.revision)

    def test_category_tombstone_is_bootstrapped_and_does_not_trigger_reload_forever(self):
        item = self.store.set('current_race', [])
        self.store.remove('current_race')
        client = self.client()
        self.assertTrue(client.get(f'/check-updates?category=current_race&revision={item.revision}').json['updated'])
        html = client.get('/').get_data(as_text=True)
        match = re.search(r'id="karting-poll-config" type="application/json">(.*?)</script>', html, re.S)
        config = json.loads(match.group(1))
        self.assertEqual(2, config['revision'])
        self.assertFalse(client.get('/check-updates?category=current_race&revision=2').json['updated'])

    def test_change_just_before_wait_is_not_lost(self):
        self.store.set('temperature', 20)
        entering = Signal()
        original_wait = self.store._changed.wait
        def wait(timeout):
            entering.set()
            return original_wait(timeout)
        def write():
            if entering.wait(1):
                self.store.set('temperature', 21)
        writer = Thread(target=write)
        writer.start()
        try:
            with patch.object(self.store._changed, 'wait', side_effect=wait):
                self.assertEqual(2, self.store.wait_for_revision(1, timeout=1))
        finally:
            writer.join(2)
        self.assertFalse(writer.is_alive())

    def test_spurious_notifications_recheck_predicate_and_deadline(self):
        with patch.object(self.store._changed, 'wait', return_value=True) as wait, \
             patch('threading._time', side_effect=[0, 0.5, 1.5]):
            self.assertEqual(0, self.store.wait_for_revision(0, timeout=1))
        self.assertEqual(2, wait.call_count)

    def test_category_wait_ignores_sensor_changes(self):
        race = self.store.set('current_race', [])
        self.store.set('temperature', 20)
        self.assertEqual(race.revision, self.store.wait_for_revision(race.revision, category='current_race', timeout=0))

    def test_apps_have_independent_views_and_no_lazy_subscription_leak(self):
        first, second = self.client(), self.client()
        first.put('/state', json={'view': 'style2'})
        self.assertEqual('style2', first.get('/state').json['view'])
        self.assertEqual('style1', second.get('/state').json['view'])
        for _ in range(10):
            self.client().get('/api/data')
        self.assertEqual([], self.runtime.bus.subscriber_health())

    def test_distinct_runtimes_do_not_share_revision(self):
        self.store.set('temperature', 20)
        other = Runtime()
        app = create_app(other); app.config['LONG_POLL_TIMEOUT'] = 0
        self.assertEqual({'updated': False, 'revision': 0}, app.test_client().get('/check-updates').json)
