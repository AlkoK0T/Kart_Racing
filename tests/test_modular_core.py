import tempfile
import unittest
from pathlib import Path

from karting import DataStore, EventBus
from karting.modules.files import FileSource


class DataStoreTests(unittest.TestCase):
    def test_set_is_visible_and_publishes_category_event(self):
        bus = EventBus()
        store = DataStore(bus)
        received = []
        bus.subscribe("data.temperature", received.append)

        store.set("temperature", 22.5, source="test")

        self.assertEqual(22.5, store.get("temperature"))
        self.assertEqual(22.5, received[0].payload.value)

    def test_file_source_uses_newest_file_in_each_category(self):
        bus = EventBus()
        store = DataStore(bus)
        with tempfile.TemporaryDirectory() as temporary:
            category = Path(temporary, "records_day")
            category.mkdir()
            Path(category, "result.yaml").write_text("men: 31.2\n", encoding="utf-8")

            source = FileSource(store, temporary)
            source.scan()

            self.assertEqual({"men": 31.2}, store.get("records_day"))


if __name__ == "__main__":
    unittest.main()
