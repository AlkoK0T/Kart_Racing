import unittest
from unittest.mock import patch
import smoke_test


class SmokeEntrypointTests(unittest.TestCase):
    def test_requires_explicit_opt_in(self):
        with patch('sys.stderr'), patch.object(smoke_test, 'native_smoke') as native:
            with self.assertRaises(SystemExit) as error:
                smoke_test.main([])
        self.assertEqual(2, error.exception.code)
        native.assert_not_called()

    def test_hardware_requires_serial_and_duration_is_bounded(self):
        for options in (['--odtemp'], ['--sdl', '--seconds', '0'], ['--sdl', '--seconds', '3601']):
            with self.subTest(options=options), patch('sys.stderr'), self.assertRaises(SystemExit):
                smoke_test.main(options)

    def test_browser_flag_does_not_touch_usb_or_sdl(self):
        with patch('karting.logging_config.configure_logging'), \
             patch.object(smoke_test, 'native_smoke') as native, \
             patch.object(smoke_test, 'browser_smoke') as browser:
            self.assertEqual(0, smoke_test.main(['--browser']))
        native.assert_not_called()
        browser.assert_called_once()

    def test_smoke_error_has_nonzero_exit_code(self):
        with patch('karting.logging_config.configure_logging'), \
             patch.object(smoke_test, 'native_smoke', side_effect=RuntimeError('SDL failed')), \
             self.assertLogs('smoke_test', level='ERROR'):
            self.assertEqual(1, smoke_test.main(['--sdl']))
