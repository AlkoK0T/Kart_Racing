from __future__ import annotations

from dataclasses import dataclass, field
import logging
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Event:
    topic: str
    payload: Any = None
    source: str = "unknown"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


EventHandler = Callable[[Event], None]


class EventBus:
    """Small synchronous event bus.

    Handlers are copied before dispatch, so handlers may safely subscribe while an
    event is being published. A slow handler only delays its publisher; expensive
    output modules should maintain their own worker/queue.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}
        self._lock = RLock()

    def subscribe(self, topic: str, handler: EventHandler) -> Callable[[], None]:
        with self._lock:
            self._handlers.setdefault(topic, []).append(handler)
        log.debug("event subscription topic=%s handler=%r", topic, handler)

        def unsubscribe() -> None:
            with self._lock:
                handlers = self._handlers.get(topic, [])
                if handler in handlers:
                    handlers.remove(handler)
            log.debug("event unsubscribe topic=%s handler=%r", topic, handler)

        return unsubscribe

    def publish(self, event: Event) -> None:
        with self._lock:
            handlers = [
                *self._handlers.get(event.topic, ()),
                *self._handlers.get("*", ()),
            ]
        log.debug(
            "event publish topic=%s source=%s payload=%r handlers=%d",
            event.topic, event.source, event.payload, len(handlers),
        )
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                log.exception("event handler failed topic=%s handler=%r", event.topic, handler)
                raise


@dataclass(frozen=True, slots=True)
class DataValue:
    value: Any
    updated_at: datetime
    source: str
    revision: int


class DataStore:
    """Thread-safe in-memory store containing only the current value per key."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._values: dict[str, DataValue] = {}
        self._revision = 0
        self._lock = RLock()

    def set(self, key: str, value: Any, *, source: str = "unknown") -> DataValue:
        with self._lock:
            self._revision += 1
            item = DataValue(value, datetime.now(timezone.utc), source, self._revision)
            self._values[key] = item
        log.debug("store set key=%s source=%s revision=%d value=%r", key, source, item.revision, value)
        self._bus.publish(Event(f"data.{key}", item, source))
        self._bus.publish(Event("data.changed", {"key": key, "item": item}, source))
        return item

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            item = self._values.get(key)
            return default if item is None else item.value

    def item(self, key: str) -> DataValue | None:
        with self._lock:
            return self._values.get(key)

    def remove(self, key: str, *, source: str = "system") -> None:
        with self._lock:
            existed = self._values.pop(key, None)
        if existed is not None:
            log.debug("store remove key=%s source=%s previous=%r", key, source, existed.value)
            self._bus.publish(Event("data.removed", key, source))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {key: item.value for key, item in self._values.items()}


class Module(Protocol):
    name: str

    def start(self) -> None: ...
    def stop(self) -> None: ...


class Runtime:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.store = DataStore(self.bus)
        self._modules: list[Module] = []

    def add(self, module: Module) -> Module:
        self._modules.append(module)
        log.debug("runtime module registered name=%s class=%s", module.name, type(module).__name__)
        return module

    def start(self) -> None:
        started: list[Module] = []
        try:
            for module in self._modules:
                log.info("starting module %s", module.name)
                module.start()
                started.append(module)
                log.info("module %s started", module.name)
        except Exception:
            log.exception("runtime startup failed")
            for module in reversed(started):
                log.info("rolling back module %s", module.name)
                module.stop()
            raise

    def stop(self) -> None:
        for module in reversed(self._modules):
            log.info("stopping module %s", module.name)
            module.stop()
            log.info("module %s stopped", module.name)
