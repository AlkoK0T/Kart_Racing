import unittest
from threading import Event as Signal, Thread

from karting.core import Runtime


class StoreIsolationTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()
        self.store = self.runtime.store
        self.value = {'laps': [31], 'nested': {'name': 'Иван'}}

    def test_input_and_set_result_do_not_alias_store(self):
        item = self.store.set('race', self.value)
        self.value['laps'].append(0)
        item.value['nested']['name'] = 'changed'
        self.assertEqual({'laps': [31], 'nested': {'name': 'Иван'}}, self.store.get('race'))
        self.assertEqual(1, self.store.item('race').revision)

    def test_all_read_apis_return_detached_trees(self):
        self.store.set('race', self.value)
        for value in (self.store.get('race'), self.store.item('race').value,
                      self.store.snapshot()['race'], self.store.snapshot_with_revision()[0]['race'].value):
            value['laps'].append(0)
        self.assertEqual([31], self.store.get('race')['laps'])
        self.assertEqual(1, self.store.item('race').revision)

    def test_event_mutation_does_not_change_store_or_other_observers(self):
        seen = []
        def mutate(event):
            event.payload.value['laps'].append(0)
        self.runtime.bus.subscribe('data.race', mutate)
        self.runtime.bus.subscribe('data.race', lambda event: seen.append(event.payload.value))
        self.store.set('race', self.value)
        self.assertEqual([31], self.store.get('race')['laps'])
        self.assertEqual([31], seen[0]['laps'])

    def test_metadata_snapshot_matches_revision_during_concurrent_writes(self):
        started = Signal()
        def write():
            started.wait(1)
            for number in range(100):
                self.store.set('counter', {'n': number})
        thread = Thread(target=write)
        thread.start(); started.set()
        try:
            for _ in range(100):
                values, revision = self.store.snapshot_with_revision()
                if values:
                    self.assertEqual(revision, values['counter'].revision)
                    self.assertEqual(revision - 1, values['counter'].value['n'])
        finally:
            thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_uncopyable_input_fails_before_write_or_notification(self):
        class Uncopyable:
            def __deepcopy__(self, memo):
                raise ValueError('cannot copy')
        seen = []
        self.runtime.bus.subscribe('*', seen.append)
        with self.assertRaises(ValueError):
            self.store.set('bad', Uncopyable())
        self.assertEqual(({}, 0), self.store.snapshot_with_revision())
        self.assertEqual([], seen)
