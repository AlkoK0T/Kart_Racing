import json
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from config import Config, PROJECT_ROOT
from karting.modules.screens import ScreenConfig, load_screen_configs


class ConfigurationTests(unittest.TestCase):
    def test_env_types_and_ranges_have_named_errors(self):
        for name, value in (('PORT', 'bad'), ('TCP_PORT', '65536'), ('DEBUG', 'maybe'),
                            ('FILE_POLL_INTERVAL', 'NaN'), ('TCP_FRAME_TIMEOUT', '0'),
                            ('SERIAL_VID', '1'), ('ODTEMP_WATCHDOG_TIMEOUT', '1'),
                            ('LOG_LEVEL', 'unknown')):
            with self.subTest(name=name), self.assertRaises(ValueError) as error:
                Config.from_env({name: value})
            self.assertIn(name, str(error.exception))

    def test_paths_are_project_relative_from_any_cwd(self):
        config = Config.from_env({'DATA_FOLDER': 'custom/data', 'DISPLAY_CONFIG': 'screens.yaml', 'LOG_FILE': ''})
        self.assertEqual(str(PROJECT_ROOT / 'custom/data'), config.DATA_FOLDER)
        self.assertEqual(str(PROJECT_ROOT / 'screens.yaml'), config.DISPLAY_CONFIG)
        self.assertEqual('', config.LOG_FILE)

    def test_project_debug_overrides_legacy_system_debug(self):
        self.assertFalse(Config.from_env({'DEBUG': 'release'}).DEBUG)
        self.assertTrue(Config.from_env({'DEBUG': 'release', 'KART_DEBUG': 'True'}).DEBUG)

    def test_conflicting_explicit_com_ports_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'same port'):
            Config(SERIAL_PORT='com12', ODTEMP_PORT='COM12')

    def test_import_run_does_not_configure_logging_or_create_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            code = ('import logging,sys,json; sys.path.insert(0,sys.argv[1]); '
                    'h=logging.NullHandler(); logging.getLogger().addHandler(h); '
                    'import run; print(json.dumps([h in logging.getLogger().handlers, '
                    'hasattr(run,"runtime"),hasattr(run,"app")]))')
            result = subprocess.run([sys.executable, '-c', code, str(PROJECT_ROOT)], cwd=directory,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual([True, False, False], json.loads(result.stdout))
            self.assertEqual([], list(Path(directory).iterdir()))

    def test_factory_from_other_cwd_uses_project_screen_configuration(self):
        from run import build_runtime
        config = Config(DISPLAY_ENABLED=True, ODTEMP_ENABLED=False, TCP_ENABLED=False, LOG_FILE='')
        with patch('karting.modules.screens.load_screen_configs', wraps=load_screen_configs) as loader:
            runtime = build_runtime(config)
        self.assertEqual(str(PROJECT_ROOT / 'screens.yaml'), loader.call_args.args[0])
        self.assertEqual('stopped', runtime.health_snapshot()['state'])
        self.assertTrue(all(getattr(module, '_thread', None) is None for module in runtime._modules))

    def test_screen_values_are_checked_before_workers(self):
        for options in ({'width': 0}, {'height': True}, {'top_interval': float('inf')},
                        {'messages': 'text'}, {'record_category': 'all'}, {'borderless': 'yes'}):
            with self.assertRaises(ValueError):
                ScreenConfig('test', 0, 0, **options)

    def test_yaml_messages_string_and_duplicate_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'screens.yaml')
            for raw in ('screens: [{name: a, x: 0, y: 0, messages: text}]',
                        'screens: [{name: a, x: 0, y: 0}, {name: a, x: 1, y: 0}, {name: c, x: 2, y: 0}]'):
                path.write_text(raw, encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_screen_configs(path)

    def test_web_error_still_cleans_runtime_and_preserves_primary_exception(self):
        from run import run_server
        runtime, app = MagicMock(), MagicMock()
        app.config = {'PORT': 5000, 'DEBUG': False}
        app.run.side_effect = ValueError('primary web error')
        runtime.close.side_effect = RuntimeError('cleanup error')
        with self.assertLogs('run', level='ERROR'), self.assertRaisesRegex(ValueError, 'primary web'):
            run_server(runtime, app)
        runtime.close.assert_called_once()
