from dataclasses import replace
from pathlib import Path
import tempfile
from threading import Event, get_ident
import unittest
from unittest.mock import patch

import pygame
from PIL import Image
from karting.gif_assets import Animation, GifAssets, GifLimits, decode_gif


class GifAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'test.gif'
        self.save()

    def save(self, colors=('red', 'blue')):
        images = [Image.new('RGBA', (8, 8), color) for color in colors]
        images[0].save(self.path, save_all=True, append_images=images[1:], duration=100, loop=0)

    def cache(self, **kwargs):
        cache = GifAssets(self.root, kwargs.pop('converter', pygame.image.fromstring), **kwargs)
        self.addCleanup(cache.close)
        return cache

    def ready(self, cache, filename='test.gif', now=0):
        cache.frame(filename, 8, 8, 0, now=now)
        self.assertTrue(cache.wait_ready(filename, 8, 8))

    def test_limits_reject_file_frames_pixels_and_decoded_memory(self):
        base = GifLimits()
        for limits in (replace(base, max_file_bytes=1), replace(base, max_frames=1),
                       replace(base, max_source_pixels=1), replace(base, max_animation_bytes=255)):
            with self.assertRaises(ValueError):
                decode_gif(self.path, 8, 8, Event(), limits)

    def test_binary_search_selects_frame_at_boundaries_and_wraps(self):
        animation = Animation((b'a', b'b', b'c'), (20, 40, 60), (1, 1))
        self.assertEqual([0, 1, 2, 0], [animation.index(value) for value in (0, .02, .04, .06)])

    def test_conversion_only_runs_on_requesting_gui_thread_and_is_cached(self):
        thread_ids = []
        def convert(*args):
            thread_ids.append(get_ident())
            return pygame.image.fromstring(*args)
        cache = self.cache(converter=convert)
        self.ready(cache)
        first = cache.frame('test.gif', 8, 8, 0, now=0)
        second = cache.frame('test.gif', 8, 8, 0, now=0)
        self.assertIs(first, second)
        self.assertEqual([get_ident()], thread_ids)
        self.assertNotEqual(first.get_at((0, 0)), cache.frame('test.gif', 8, 8, .1, now=0).get_at((0, 0)))

    def test_failed_file_is_reloaded_after_repair_without_restart(self):
        self.path.write_bytes(b'broken')
        cache = self.cache()
        with self.assertLogs('karting.gif_assets', level='WARNING'):
            self.ready(cache)
        self.assertIsNone(cache.frame('test.gif', 8, 8, 0, now=1))
        self.save()
        self.ready(cache, now=3)
        self.assertIsNotNone(cache.frame('test.gif', 8, 8, 0, now=3))

    def test_cache_budget_evicts_decoded_variants(self):
        cache = self.cache(limits=replace(GifLimits(), max_cache_bytes=512))
        self.ready(cache)
        second = self.root / 'second.gif'
        second.write_bytes(self.path.read_bytes())
        self.ready(cache, 'second.gif', now=3)
        self.assertLessEqual(cache.bytes, 512)
        self.assertEqual(1, len(cache._animations))

    def test_slow_decoder_does_not_block_rendering(self):
        entered, release = Event(), Event()
        original = decode_gif
        def slow(*args):
            entered.set(); release.wait(2)
            return original(*args)
        cache = self.cache()
        with patch('karting.gif_assets.decode_gif', side_effect=slow):
            try:
                self.assertIsNone(cache.frame('test.gif', 8, 8, 0))
                self.assertTrue(entered.wait(1))
                self.assertIsNone(cache.frame('test.gif', 8, 8, 0))
                self.assertFalse(release.is_set())
            finally:
                release.set()
            self.assertTrue(cache.wait_ready('test.gif', 8, 8))

    def test_close_stops_worker_and_releases_caches(self):
        cache = self.cache()
        self.ready(cache)
        cache.close(); cache.close()
        self.assertFalse(cache._worker.is_alive())
        self.assertEqual(0, cache.bytes)
        self.assertIsNone(cache.frame('test.gif', 8, 8, 0))
