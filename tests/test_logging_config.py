import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from karting.logging_config import configure_logging


class LoggingConfigTests(unittest.TestCase):
    def setUp(self):
        self.root = logging.getLogger()
        self.original = self.root.handlers[:]
        self.level = self.root.level
        self.werkzeug_level = logging.getLogger('werkzeug').level
        for handler in self.original:
            self.root.removeHandler(handler)
        self.addCleanup(self.restore)

    def restore(self):
        for handler in self.root.handlers[:]:
            self.root.removeHandler(handler)
            handler.close()
        for handler in self.original:
            self.root.addHandler(handler)
        self.root.setLevel(self.level)
        logging.getLogger('werkzeug').setLevel(self.werkzeug_level)

    def test_foreign_handlers_are_preserved_and_not_closed(self):
        foreign = logging.NullHandler()
        self.root.addHandler(foreign)
        with patch.object(foreign, 'close') as close:
            configure_logging(filename='')
            configure_logging(filename='')
            self.assertIn(foreign, self.root.handlers)
            close.assert_not_called()
        self.assertEqual(2, len(self.root.handlers))

    def test_reconfiguration_closes_owned_file_and_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'journal.log'
            configure_logging(filename=str(path))
            previous = self.root.handlers[:]
            file_handler = previous[-1]
            configure_logging(filename='')
            self.assertIsNone(file_handler.stream)
            self.assertTrue(all(handler._closed for handler in previous))
            self.assertEqual(1, len(self.root.handlers))
            path.unlink()  # Windows would reject an open FileHandler.

    def test_failed_setup_leaves_existing_handlers_and_level(self):
        configure_logging(level='WARNING', filename='')
        previous = self.root.handlers[:]
        with patch('karting.logging_config.RotatingFileHandler', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                configure_logging(level='DEBUG', filename='unused.log')
        self.assertEqual(previous, self.root.handlers)
        self.assertEqual(logging.WARNING, self.root.level)
        self.assertFalse(previous[0]._closed)

    def test_invalid_parameters_do_not_change_handlers(self):
        for options in ({'level': 'bad'}, {'max_bytes': 0}, {'backup_count': -1}, {'max_bytes': True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                configure_logging(filename='', **options)
        self.assertEqual([], self.root.handlers)

    def test_utf8_and_level_filtering(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'journal.log'
            configure_logging('INFO', str(path))
            logger = logging.getLogger('karting.logging_test')
            with patch('sys.stderr'):
                logger.debug('secret raw payload')
                logger.info('модуль восстановлен')
            for handler in self.root.handlers:
                handler.flush()
            content = path.read_text(encoding='utf-8')
            self.assertIn('модуль восстановлен', content)
            self.assertNotIn('secret', content)
            configure_logging(filename='')

    def test_entrypoint_configures_before_composition(self):
        import run
        calls = []
        with patch.object(run.Config, 'from_env') as config, \
             patch.object(run, 'configure_logging', side_effect=lambda *args: calls.append('logging')), \
             patch.object(run, 'create_application', side_effect=ValueError('bad screens')):
            config.return_value.LOG_LEVEL = 'INFO'
            with patch('sys.stderr'):
                self.assertEqual(2, run.main())
        self.assertEqual(['logging'], calls)
