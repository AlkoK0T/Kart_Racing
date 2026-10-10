import json
import socket
import unittest

from karting import DataStore, EventBus
from karting.modules.tcp import TcpJsonSource


class TcpRaceContractTests(unittest.TestCase):
    def test_raw_laps_are_normalized_and_metadata_is_preserved(self):
        store = DataStore(EventBus())
        source = TcpJsonSource(store, "127.0.0.1", 0)
        source.start()
        try:
            message = {
                "category": "current_race",
                "data": {
                    "schema_version": 1,
                    "race_id": "race-42",
                    "state": "running",
                    "participants": {"7": {"name": "Иван", "category": "men"}},
                    "laps": {"7": [31.2, 30.5]},
                },
            }
            with socket.create_connection(source._server.server_address, timeout=2) as client:
                client.sendall(json.dumps(message).encode("utf-8") + b"\n")
                with client.makefile('rb') as reader:
                    response = json.loads(reader.readline())
            self.assertEqual({"ok": True}, response)
            race = store.get("current_race")
            self.assertEqual("race-42", race["race_id"])
            self.assertEqual("running", race["state"])
            self.assertEqual("Иван", race["participants"]["7"]["name"])
            self.assertEqual(30.5, race["current_race"]["best_times"]["7"])
            self.assertEqual(1, race["current_race"]["positions"]["7"])
        finally:
            source.stop()


if __name__ == "__main__":
    unittest.main()
