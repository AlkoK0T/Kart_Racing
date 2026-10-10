import unittest
from unittest.mock import MagicMock, patch
import pygame

from karting.modules.screens import ScreenConfig, ScreenDisplay, ScreenFrame
from karting.render_cache import SurfaceCache, WindowSchedule, frame_signature


class RenderCacheTests(unittest.TestCase):
    def setUp(self):
        for cache in (ScreenDisplay._text_cache, ScreenDisplay._bottom_cache, ScreenDisplay._font_cache):
            cache.clear()

    def test_each_window_respects_its_own_fps(self):
        schedule = WindowSchedule()
        counts = {fps: 0 for fps in (5, 15, 30)}
        for tick in range(30):
            for fps in counts:
                counts[fps] += schedule.due(str(fps), fps, tick / 30)
        self.assertEqual({5: 5, 15: 15, 30: 30}, counts)

    def test_long_pause_does_not_trigger_catch_up_burst(self):
        schedule = WindowSchedule()
        self.assertTrue(schedule.due('one', 5, 0))
        self.assertTrue(schedule.due('one', 5, 100))
        self.assertFalse(schedule.due('one', 5, 100))

    def test_same_text_and_bottom_are_not_rendered_again(self):
        font = MagicMock(); font.render.return_value = pygame.Surface((50, 20))
        frame = ScreenFrame(('12:00', '22 °C'), ('Работаем',), 'waves')
        surface = pygame.Surface((320, 160))
        with patch('pygame.font.SysFont', return_value=font):
            for tick in range(10):
                ScreenDisplay._draw(surface, ScreenConfig('test', 0, 0), frame, tick)
        self.assertEqual(3, font.render.call_count)

    def test_surface_cache_is_bounded_by_entries_and_bytes(self):
        surface = pygame.Surface((10, 10), pygame.SRCALPHA, 32)
        cache = SurfaceCache(max_entries=2, max_bytes=800)
        for index in range(5):
            cache.put(index, surface)
        self.assertLessEqual(cache.bytes, 800)
        self.assertIsNone(cache.get(2))
        self.assertIs(surface, cache.get(4))
        cache.clear(); self.assertEqual(0, cache.bytes)

    def test_gif_signature_changes_only_for_content_or_frame(self):
        frame = ScreenFrame(('time',), ('message',), 'waves', 'clock.gif')
        first, second = object(), object()
        self.assertEqual(frame_signature(frame, first), frame_signature(frame, first))
        self.assertNotEqual(frame_signature(frame, first), frame_signature(frame, second))
        self.assertIsNone(frame_signature(frame, None))

    def test_invalid_fps_is_rejected(self):
        for fps in (0, -1, True, '15'):
            with self.assertRaises(ValueError):
                ScreenConfig('test', 0, 0, fps=fps)
