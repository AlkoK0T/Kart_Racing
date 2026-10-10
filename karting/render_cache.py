from collections import OrderedDict


class SurfaceCache:
    def __init__(self, max_entries=256, max_bytes=8 * 1024 * 1024):
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self._values = OrderedDict()
        self.bytes = 0

    def get(self, key):
        value = self._values.get(key)
        if value is not None:
            self._values.move_to_end(key)
            return value[0]
        return None

    def put(self, key, surface):
        size = surface.get_pitch() * surface.get_height()
        old = self._values.pop(key, None)
        if old:
            self.bytes -= old[1]
        if size > self.max_bytes:
            return
        while self._values and (len(self._values) >= self.max_entries or self.bytes + size > self.max_bytes):
            _, (_, removed) = self._values.popitem(last=False)
            self.bytes -= removed
        self._values[key] = surface, size
        self.bytes += size

    def clear(self):
        self._values.clear()
        self.bytes = 0


class WindowSchedule:
    def __init__(self):
        self._next = {}

    def due(self, name, fps, now):
        if now + 1e-9 < self._next.get(name, now):
            return False
        self._next[name] = now + 1 / fps
        return True


def frame_signature(frame, background):
    # Procedural effects always move; a decoded GIF only changes with its frame.
    if background is None:
        return None
    return frame.top_lines, frame.bottom_lines, frame.effect, frame.background, id(background)
