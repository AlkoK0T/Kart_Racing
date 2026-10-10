from __future__ import annotations

import math
import logging
import time
from dataclasses import dataclass
from contextlib import ExitStack
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from threading import Event as ThreadEvent, Thread
from typing import Any

import yaml

from karting.core import DataStore, EventBus
from karting.lifecycle import already_running, join_worker
from karting.timezones import display_timezone
from karting.render_cache import SurfaceCache, WindowSchedule, frame_signature
from karting.gif_assets import GifAssets

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScreenConfig:
    name: str
    x: int
    y: int
    width: int = 320
    height: int = 160
    record_category: str = "men"
    record_label: str = "Рекорд дня"
    messages: tuple[str, ...] = ("Время работы 12–21", "10 минут — 1000 ₽")
    top_interval: float = 5.0
    message_interval: float = 4.0
    fps: int = 15
    borderless: bool = True
    always_on_top: bool = True
    background_time_temperature: str = ""
    background_daily_record: str = "previous"

    def __post_init__(self):
        if type(self.fps) is not int or self.fps <= 0:
            raise ValueError('screen fps must be a positive integer')


@dataclass(slots=True)
class ScreenFrame:
    top_lines: tuple[str, ...]
    bottom_lines: tuple[str, ...]
    effect: str
    background: str = ""


def load_screen_configs(path: str | Path) -> list[ScreenConfig]:
    with Path(path).open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream) or {}
    defaults = document.get("defaults", {})
    result: list[ScreenConfig] = []
    for index, screen in enumerate(document.get("screens", []), 1):
        values = {**defaults, **screen}
        values["name"] = values.get("name", f"screen-{index}")
        values["messages"] = tuple(values.get("messages", ()))
        result.append(ScreenConfig(**values))
    if len(result) != 3:
        raise ValueError("Screen configuration must contain exactly 3 screens")
    log.info("screen configuration loaded path=%s screens=%d", path, len(result))
    log.debug("screen configuration values=%r", result)
    return result


class ContentPlanner:
    """Pure presentation logic, kept independent from the GUI for testing."""

    def __init__(self, store: DataStore, *, timezone: str = 'local', now=None) -> None:
        self._store = store
        self._timezone = display_timezone(timezone)
        self._now = now or (lambda: datetime.now().astimezone())

    def current_records(self, key='records_day', *, now=None):
        item = self._store.item(key)
        if item is None or item.quality != 'good':
            return {}
        now = (now or self._now()).astimezone(self._timezone)
        records = item.value
        period = 'date' if key == 'records_day' else 'month'
        pattern = '%Y-%m-%d' if period == 'date' else '%Y-%m'
        supplied = records.get(period) if isinstance(records, dict) else None
        actual = str(supplied) if supplied is not None else item.updated_at.astimezone(self._timezone).strftime(pattern)
        return records if actual == now.strftime(pattern) else {}

    def frame(self, config: ScreenConfig, elapsed: float) -> ScreenFrame:
        now = self._now().astimezone(self._timezone)
        top_page = int(elapsed / max(config.top_interval, 0.1)) % 2
        if top_page == 0:
            clock = self._store.item('current_time')
            current = clock.value if clock is not None and (now - clock.updated_at).total_seconds() <= 3 else now
            if isinstance(current, str):
                try:
                    current = datetime.fromisoformat(current.replace('Z', '+00:00'))
                except ValueError:
                    pass
            if isinstance(current, datetime):
                if current.tzinfo is not None:
                    current = current.astimezone(self._timezone)
                current_text = current.strftime("%H:%M:%S")
            else:
                current_text = str(current)
            item = self._store.item('temperature')
            temperature = item.value if item is not None and item.quality == 'good' and (now - item.updated_at).total_seconds() <= 120 else '—'
            top = (current_text, f"Температура: {format_temperature(temperature)}")
            effect = "waves"
            background = resolve_background(config, "time_temperature")
        else:
            record = find_record(self.current_records(now=now), config.record_category)
            top = (config.record_label, format_record(record))
            effect = "pulse"
            background = resolve_background(config, "daily_record")

        messages = config.messages or ("",)
        message = messages[int(elapsed / max(config.message_interval, 0.1)) % len(messages)]
        bottom = tuple(part.strip() for part in message.split("\n"))
        return ScreenFrame(top, bottom, effect, background)


def resolve_background(config: ScreenConfig, page: str) -> str:
    current = getattr(config, f"background_{page}")
    if current != "previous":
        return current
    previous = (
        config.background_daily_record
        if page == "time_temperature"
        else config.background_time_temperature
    )
    return "" if previous == "previous" else previous


def find_record(records: Any, category: str) -> Any:
    if not isinstance(records, dict):
        return records if records is not None else "—"
    aliases = {
        "children": ("children", "kids", "дети"),
        "women": ("women", "female", "женщины"),
        "men": ("men", "male", "мужчины"),
    }
    for key in aliases.get(category, (category,)):
        if key in records:
            return records[key]
    return "—"


def format_record(record: Any) -> str:
    if record is None:
        return '—'
    if isinstance(record, dict):
        name = record.get("name") or record.get("driver") or ""
        value = record.get("time", record.get("value", "—"))
        if value is None or value == '—':
            return '—'
        return f"{name}  {value}".strip()
    return str(record)


def format_temperature(value: Any) -> str:
    if value is None:
        return '—'
    if isinstance(value, dict):
        value = value.get("value", value.get("temperature", "—"))
    if isinstance(value, (int, float)):
        return f"{value:g} °C"
    text = str(value)
    return text if "°" in text else f"{text} °C"


class ScreenDisplay:
    """Three SDL2/Pygame windows driven by the shared data store."""

    name = "screens"
    _font_cache = OrderedDict()
    _text_cache = SurfaceCache()
    _bottom_cache = SurfaceCache(max_entries=32, max_bytes=4 * 1024 * 1024)

    def __init__(self, store: DataStore, bus: EventBus, config_path: str | Path,
                 *, timezone: str = 'local') -> None:
        self._store = store
        self._bus = bus
        self._configs = load_screen_configs(config_path)
        self._config_dir = Path(config_path).resolve().parent
        self._planner = ContentPlanner(store, timezone=timezone)
        self._stop = ThreadEvent()
        self._ready = ThreadEvent()
        self._error: BaseException | None = None
        self._unsubscribe = None
        self._thread: Thread | None = None
        self._cleanup_errors = []
        self.health = {'state': 'stopped', 'error': None}
        self._dirty = ThreadEvent()

    def start(self) -> None:
        if already_running(self):
            return
        self._stop.clear()
        self._ready.clear()
        self._error = None
        self._cleanup_errors.clear()
        self.health = {'state': 'starting', 'error': None}
        self._dirty.set()
        self._unsubscribe = self._bus.subscribe('data.changed', lambda event: self._dirty.set())
        self._thread = Thread(target=self._thread_main, name=self.name, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5):
            try:
                self._cleanup_failed_start()
            except Exception:
                log.exception('screen cleanup after startup timeout failed')
            raise RuntimeError("Pygame screen module did not start within 5 seconds")
        if self._error is not None:
            try:
                self._cleanup_failed_start()
            except Exception:
                log.exception('screen cleanup after startup error failed')
            raise RuntimeError("Cannot start Pygame screen module") from self._error
        log.info("Pygame screen module initialized screens=%d", len(self._configs))

    def _cleanup_failed_start(self) -> None:
        self.stop()

    def stop(self) -> None:
        self._stop.set()
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        join_worker(self)
        if self._cleanup_errors:
            raise RuntimeError('screen resource cleanup failed: ' + '; '.join(self._cleanup_errors))
        log.info("Pygame screen module stopped")

    def _thread_main(self) -> None:
        try:
            self._run()
        except BaseException as exc:
            self._error = exc
            log.exception("Pygame screen thread failed")
        finally:
            self.health = {'state': 'failed' if self._error else 'stopped',
                           'error': str(self._error) if self._error else None}
            self._ready.set()

    def _run(self) -> None:
        import pygame
        with ExitStack() as resources:
            resources.callback(self._cleanup_resource, pygame.quit)
            pygame.init()
            resources.callback(self._font_cache.clear)
            resources.callback(self._text_cache.clear)
            resources.callback(self._bottom_cache.clear)
            self._run_initialized(pygame, resources)
        if self._cleanup_errors:
            raise RuntimeError('screen resource cleanup failed: ' + '; '.join(self._cleanup_errors))

    def _cleanup_resource(self, action):
        try:
            action()
        except Exception as exc:
            log.exception('screen resource cleanup failed action=%r', action)
            self._cleanup_errors.append(str(exc))

    def _run_initialized(self, pygame, resources) -> None:
        from pygame._sdl2.video import Renderer, Texture, Window

        self._font_cache.clear()
        started = time.monotonic()
        gifs = GifAssets(self._config_dir, pygame.image.fromstring)
        resources.callback(self._cleanup_resource, gifs.close)
        displays: list[tuple[ScreenConfig, Any, Any, Any, Any]] = []
        resources.callback(displays.clear)

        for config in self._configs:
            window = Window(
                config.name, size=(config.width, config.height),
                position=(config.x, config.y), borderless=config.borderless,
                always_on_top=config.always_on_top,
            )
            resources.callback(self._cleanup_resource, window.destroy)
            renderer = Renderer(window, accelerated=True, vsync=False)
            surface = pygame.Surface((config.width, config.height), pygame.SRCALPHA, 32)
            texture = Texture(renderer, (config.width, config.height), streaming=True)
            displays.append((config, window, renderer, surface, texture))
            log.info(
                "screen window created name=%s position=%s,%s size=%sx%s fps=%s",
                config.name, config.x, config.y, config.width, config.height, config.fps,
            )
        self.health = {'state': 'running', 'error': None}
        self._ready.set()

        clock = pygame.time.Clock()
        target_fps = max(config.fps for config in self._configs)
        schedule = WindowSchedule()
        previous = {}
        previous_content = {}
        try:
            while not self._stop.is_set():
                for event in pygame.event.get():
                    if event.type in (pygame.QUIT, pygame.WINDOWCLOSE):
                        self._stop.set()
                elapsed = time.monotonic() - started
                if self._dirty.is_set():
                    self._dirty.clear()
                    previous.clear()
                for config, _, renderer, surface, texture in displays:
                    if not schedule.due(config.name, config.fps, elapsed):
                        continue
                    frame = self._planner.frame(config, elapsed)
                    if previous_content.get(config.name) != frame:
                        log.debug('screen content changed name=%s content=%r', config.name, frame)
                        previous_content[config.name] = frame
                    background = gifs.frame(
                        frame.background, config.width, config.height // 2, elapsed
                    )
                    signature = frame_signature(frame, background)
                    if signature is not None and previous.get(config.name) == signature:
                        continue
                    previous[config.name] = signature
                    self._draw(surface, config, frame, elapsed, background)
                    texture.update(surface)
                    renderer.clear()
                    texture.draw()
                    renderer.present()
                clock.tick(target_fps)
        finally:
            displays.clear()
            self._font_cache.clear()

    @staticmethod
    def _draw(surface: Any, config: ScreenConfig, frame: ScreenFrame, elapsed: float,
              background: Any | None = None) -> None:
        import pygame

        width, height, half = config.width, config.height, config.height // 2
        surface.fill("#08162f", pygame.Rect(0, 0, width, half))
        if background is not None:
            surface.blit(background, (0, 0))
        elif frame.effect == "waves":
            for index in range(7):
                x = int((elapsed * 35 + index * 58) % (width + 80) - 40)
                radius = 18 + index % 3 * 7
                pygame.draw.circle(surface, "#087eaa", (x, half // 2), radius, 2)
        else:
            radius = int(22 + 10 * (1 + math.sin(elapsed * 3)))
            pygame.draw.circle(surface, "#5a164e", (width // 2, half // 2), radius)
            pygame.draw.circle(surface, "#d02c79", (width // 2, half // 2), radius, 2)

        bottom_key = width, height - half, frame.bottom_lines
        bottom = ScreenDisplay._bottom_cache.get(bottom_key)
        if bottom is None:
            bottom = pygame.Surface((width, height - half), pygame.SRCALPHA, 32)
            bottom.fill('#090909')
            pygame.draw.line(bottom, '#e8233c', (0, 0), (width, 0), 2)
            bottom_size = max(10, int(half * 0.22))
            spacing = half / (len(frame.bottom_lines) + 1)
            for index, line in enumerate(frame.bottom_lines, 1):
                ScreenDisplay._draw_text(bottom, line, (width // 2, int(spacing * index)),
                                         bottom_size, '#ffd33d', width - 12)
            ScreenDisplay._bottom_cache.put(bottom_key, bottom)
        surface.blit(bottom, (0, half))
        top_size = max(10, int(half * 0.23))
        for index, line in enumerate(frame.top_lines):
            ScreenDisplay._draw_text(
                surface, line, (width // 2, int(half * (0.30 + index * 0.43))),
                top_size, "white", width - 10,
            )

    @staticmethod
    def _draw_text(surface: Any, text: str, center: tuple[int, int], size: int,
                   color: str, max_width: int) -> None:
        import pygame

        key = str(text), size, color, max_width
        rendered = ScreenDisplay._text_cache.get(key)
        if rendered is not None:
            surface.blit(rendered, rendered.get_rect(center=center))
            return
        font = ScreenDisplay._font_cache.get(size)
        if font is None:
            font = pygame.font.SysFont("Arial", size, bold=True)
            ScreenDisplay._font_cache[size] = font
            if len(ScreenDisplay._font_cache) > 128:
                ScreenDisplay._font_cache.popitem(last=False)
        ScreenDisplay._font_cache.move_to_end(size)
        rendered = font.render(str(text), True, color)
        if rendered.get_width() > max_width:
            scale = max_width / rendered.get_width()
            rendered = pygame.transform.smoothscale(
                rendered, (max_width, max(1, int(rendered.get_height() * scale)))
            )
        ScreenDisplay._text_cache.put(key, rendered)
        surface.blit(rendered, rendered.get_rect(center=center))
