import unittest
from unittest.mock import MagicMock, patch

import pygame

from karting.core import Runtime
from karting.modules.screens import ScreenConfig, ScreenDisplay


class ScreenCleanupTests(unittest.TestCase):
    def setUp(self):
        runtime = Runtime()
        configs = [ScreenConfig(str(number), number * 320, 0) for number in range(3)]
        with patch('karting.modules.screens.load_screen_configs', return_value=configs):
            self.source = ScreenDisplay(runtime.store, runtime.bus, 'screens.yaml')
        self.windows = [MagicMock() for _ in range(3)]

    def run_with(self, *, windows=None, renderer=None, texture=None):
        with patch('pygame.init'), patch('pygame.quit') as quit, \
             patch('pygame._sdl2.video.Window', side_effect=windows or self.windows), \
             patch('pygame._sdl2.video.Renderer', side_effect=renderer), \
             patch('pygame._sdl2.video.Texture', side_effect=texture):
            self.quit = quit
            self.source._run()
        return quit

    def test_second_window_failure_frees_first_and_quits(self):
        with self.assertRaises(OSError):
            self.run_with(windows=[self.windows[0], OSError('second window failed')])
        self.windows[0].destroy.assert_called_once()
        self.quit.assert_called_once()

    def test_renderer_failure_frees_just_created_window(self):
        with self.assertRaises(OSError):
            self.run_with(renderer=OSError('renderer failed'))
        self.windows[0].destroy.assert_called_once()

    def test_texture_failure_frees_window(self):
        with self.assertRaises(OSError):
            self.run_with(texture=OSError('texture failed'))
        self.windows[0].destroy.assert_called_once()

    def test_destroy_failure_does_not_skip_other_windows_or_quit(self):
        self.windows[1].destroy.side_effect = OSError('destroy failed')
        self.source._ready = MagicMock()
        self.source._ready.set.side_effect = self.source._stop.set
        with patch('pygame.init'), patch('pygame.quit') as quit, \
             patch('pygame._sdl2.video.Window', side_effect=self.windows), \
             patch('pygame._sdl2.video.Renderer'), patch('pygame._sdl2.video.Texture'), \
             self.assertLogs('karting.modules.screens', level='ERROR'), \
             self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
            self.source._run()
        for window in self.windows:
            window.destroy.assert_called_once()
        quit.assert_called_once()

    def test_init_failure_still_quits_pygame(self):
        with patch('pygame.init', side_effect=OSError('init failed')), patch('pygame.quit') as quit:
            with self.assertRaises(OSError):
                self.source._run()
        quit.assert_called_once()

    def test_worker_error_is_visible_and_failed_start_joins(self):
        with patch.object(self.source, '_run', side_effect=OSError('window failed')), \
             self.assertLogs('karting.modules.screens', level='ERROR'), \
             self.assertRaisesRegex(RuntimeError, 'Cannot start'):
            self.source.start()
        self.assertIsNone(self.source._thread)
        self.assertIsNone(self.source._unsubscribe)
        self.assertEqual('failed', self.source.health['state'])
