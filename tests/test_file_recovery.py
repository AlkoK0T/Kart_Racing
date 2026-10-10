import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from karting.core import Runtime
from karting.modules.files import FileSource


class FileRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = Runtime()
        self.source = FileSource(self.runtime.store, self.root)
        self.category = self.root / 'temperature'
        self.category.mkdir()
        self.file = self.category / 'value.yaml'
        self.file.write_text('22', encoding='utf-8')

    def test_disappearing_candidate_does_not_prevent_other_category(self):
        records = self.root / 'records_day'; records.mkdir()
        (records / 'records.yaml').write_text('men: 31.2', encoding='utf-8')
        stat = Path.stat
        def disappearing(path, *args, **kwargs):
            if path == self.file:
                raise FileNotFoundError('disappeared')
            return stat(path, *args, **kwargs)
        with patch.object(Path, 'stat', disappearing), self.assertLogs('karting.modules.files', level='WARNING'):
            self.source.scan()
        self.assertEqual({'men': 31.2}, self.runtime.store.get('records_day'))
        self.source.scan()
        self.assertEqual(22, self.runtime.store.get('temperature'))

    def test_root_error_does_not_kill_worker_and_next_scan_recovers(self):
        iterdir = Path.iterdir
        failed = False
        def intermittent(path):
            nonlocal failed
            if path == self.root and not failed:
                failed = True
                raise PermissionError('temporarily unavailable')
            return iterdir(path)
        self.source._stop = MagicMock()
        self.source._stop.wait.side_effect = [False, False, True]
        with patch.object(Path, 'iterdir', intermittent), self.assertLogs('karting.modules.files', level='WARNING'):
            self.source._run()
        self.assertEqual(22, self.runtime.store.get('temperature'))
        self.assertEqual('running', self.source.health['state'])

    def test_bad_newest_keeps_previous_until_same_file_is_fixed(self):
        self.source.scan()
        old = self.runtime.store.item('temperature')
        newer = self.category / 'new.yaml'
        newer.write_text('broken: [', encoding='utf-8')
        os.utime(newer, ns=(self.file.stat().st_mtime_ns + 1000000,) * 2)
        with self.assertLogs('karting.modules.files', level='WARNING'):
            self.source.scan()
        self.assertEqual(old, self.runtime.store.item('temperature'))
        newer.write_text('23', encoding='utf-8')
        self.source.scan()
        self.assertEqual(23, self.runtime.store.get('temperature'))

    def test_modified_during_read_is_not_committed(self):
        stat = Path.stat
        calls = 0
        def changed(path, *args, **kwargs):
            nonlocal calls
            result = stat(path, *args, **kwargs)
            if path == self.file:
                calls += 1
                if calls == 2:
                    return MagicMock(st_mtime_ns=result.st_mtime_ns + 1, st_size=result.st_size)
            return result
        with patch.object(Path, 'stat', changed), self.assertLogs('karting.modules.files', level='WARNING'):
            self.source.scan()
        self.assertIsNone(self.runtime.store.get('temperature'))
        self.source.scan()
        self.assertEqual(22, self.runtime.store.get('temperature'))
