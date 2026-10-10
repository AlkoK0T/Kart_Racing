"""Bounded asynchronous Pillow decoding; Pygame conversion stays on GUI thread."""
from bisect import bisect_right
from collections import OrderedDict
from dataclasses import dataclass
import logging
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
import time

from PIL import Image, ImageSequence
from karting.render_cache import SurfaceCache

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GifLimits:
    max_file_bytes: int = 8 * 1024 * 1024
    max_source_pixels: int = 4 * 1024 * 1024
    max_frames: int = 256
    max_animation_bytes: int = 8 * 1024 * 1024
    max_cache_bytes: int = 32 * 1024 * 1024
    max_variants: int = 6
    retry_interval: float = 2


@dataclass(frozen=True)
class Animation:
    frames: tuple[bytes, ...]
    boundaries: tuple[int, ...]
    size: tuple[int, int]

    @property
    def bytes(self):
        return sum(map(len, self.frames))

    def index(self, elapsed):
        return bisect_right(self.boundaries, int(elapsed * 1000) % self.boundaries[-1])


def decode_gif(path, width, height, stop, limits):
    frames, boundaries = [], []
    total = 0
    if Path(path).stat().st_size > limits.max_file_bytes:
        raise ValueError('GIF file exceeds byte limit')
    with Image.open(path) as image:
        if image.format != 'GIF':
            raise ValueError('background must be a GIF')
        for index, source in enumerate(ImageSequence.Iterator(image)):
            if stop.is_set():
                raise InterruptedError('GIF decoding stopped')
            if index >= limits.max_frames or source.width * source.height > limits.max_source_pixels:
                raise ValueError('GIF exceeds frame/source pixel limit')
            if (index + 1) * width * height * 4 > limits.max_animation_bytes:
                raise ValueError('decoded GIF exceeds animation memory limit')
            resized = source.convert('RGBA').resize((width, height), Image.Resampling.LANCZOS)
            frames.append(resized.tobytes())
            total += max(20, int(source.info.get('duration', 100)))
            boundaries.append(total)
    if not frames:
        raise ValueError('GIF has no frames')
    return Animation(tuple(frames), tuple(boundaries), (width, height))


class GifAssets:
    def __init__(self, root, converter, limits=GifLimits()):
        self.root, self.converter, self.limits = Path(root), converter, limits
        self._lock = RLock()
        self._stop = Event()
        self._requests = Queue(limits.max_variants)
        self._states = OrderedDict()
        self._animations = OrderedDict()
        self._surfaces = SurfaceCache()
        self.bytes = 0
        self._generation = 0
        self._worker = Thread(target=self._run, name='gif-decoder', daemon=True)
        self._worker.start()

    def _key(self, filename, width, height):
        path = Path(filename)
        if not path.is_absolute():
            path = self.root / path
        return str(path), width, height

    def frame(self, filename, width, height, elapsed, *, now=None):
        if not filename or self._stop.is_set():
            return None
        now = time.monotonic() if now is None else now
        key = self._key(filename, width, height)
        with self._lock:
            state = self._states.get(key)
            check = state is None or now - state['checked'] >= self.limits.retry_interval
        if check:
            try:
                stat = Path(key[0]).stat()
                fingerprint = stat.st_mtime_ns, stat.st_size
            except OSError:
                fingerprint = None
            with self._lock:
                state = self._states.get(key)
                if state is None or (state['done'].is_set() and
                    (state['fingerprint'] != fingerprint or key not in self._animations)):
                    self._generation += 1
                    new_state = {'checked': now, 'fingerprint': fingerprint,
                                 'generation': self._generation, 'done': Event()}
                    try:
                        self._requests.put_nowait((key, new_state['generation'], fingerprint))
                    except Full:
                        pass  # Retry at the next check; GUI never waits for decoder.
                    else:
                        self._states[key] = new_state
                        self._states.move_to_end(key)
                        while len(self._states) > self.limits.max_variants:
                            old, _ = self._states.popitem(last=False)
                            self._drop(old)
                elif state is not None:
                    state['checked'] = now
        with self._lock:
            cached = self._animations.get(key)
            if cached is None:
                return None
            self._animations.move_to_end(key)
            generation, animation = cached
        index = animation.index(elapsed)
        surface_key = key, generation, index
        surface = self._surfaces.get(surface_key)
        if surface is None:
            surface = self.converter(animation.frames[index], animation.size, 'RGBA')
            self._surfaces.put(surface_key, surface)
        return surface

    def _drop(self, key):
        old = self._animations.pop(key, None)
        if old:
            self.bytes -= old[1].bytes

    def _run(self):
        while not self._stop.is_set():
            try:
                key, generation, fingerprint = self._requests.get(timeout=0.1)
            except Empty:
                continue
            try:
                animation = decode_gif(*key, self._stop, self.limits)
                stat = Path(key[0]).stat()
                if fingerprint != (stat.st_mtime_ns, stat.st_size):
                    raise OSError('GIF changed while decoding')
                with self._lock:
                    state = self._states.get(key)
                    if self._stop.is_set() or state is None or state['generation'] != generation:
                        continue
                    if animation.bytes > self.limits.max_cache_bytes:
                        raise ValueError('GIF exceeds total cache budget')
                    self._drop(key)
                    while self._animations and self.bytes + animation.bytes > self.limits.max_cache_bytes:
                        self._drop(next(iter(self._animations)))
                    self._animations[key] = generation, animation
                    self.bytes += animation.bytes
                log.info('GIF loaded path=%s frames=%s bytes=%s', key[0], len(animation.frames), animation.bytes)
            except (OSError, ValueError, EOFError, Image.DecompressionBombError) as exc:
                if not self._stop.is_set():
                    log.warning('GIF unavailable path=%s error=%s; procedural/last valid background retained', key[0], exc)
            except Exception:
                log.exception('GIF decoder failed path=%s', key[0])
            finally:
                with self._lock:
                    state = self._states.get(key)
                    if state is not None and state['generation'] == generation:
                        state['done'].set()
                self._requests.task_done()

    def wait_ready(self, filename, width, height, timeout=2):
        """Warm-up/test helper, never called by GUI rendering."""
        with self._lock:
            state = self._states.get(self._key(filename, width, height))
        return state is not None and state['done'].wait(timeout)

    def close(self):
        self._stop.set()
        self._worker.join(3)
        if self._worker.is_alive():
            raise RuntimeError('GIF decoder did not stop')
        with self._lock:
            self._animations.clear(); self._states.clear(); self.bytes = 0
        self._surfaces.clear()
        while True:
            try:
                self._requests.get_nowait(); self._requests.task_done()
            except Empty:
                break
