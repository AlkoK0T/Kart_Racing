from __future__ import annotations

from dataclasses import dataclass, field, replace
from copy import deepcopy
import logging
from datetime import datetime, timezone
from queue import Empty, Full, Queue
from threading import Condition, Event as ThreadEvent, RLock, Thread, current_thread
from typing import Any, Callable, Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Event:
    topic: str
    payload: Any = None
    source: str = "unknown"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


EventHandler = Callable[[Event], None]


class _Subscription:
    def __init__(self, topic, handler, queued, queue_size):
        self.topic, self.handler = topic, handler
        self._lock = RLock()
        self._stop = ThreadEvent()
        self.failures = self.dropped = self.delivered = 0
        self.last_error = None
        self.last_source = None
        self._queue = Queue(queue_size) if queued else None
        self._thread = None
        if queued:
            self._thread = Thread(target=self._run, name=f'event:{topic}', daemon=True)
            self._thread.start()

    def invoke(self, event):
        try:
            self.handler(deepcopy(event))
        except Exception as exc:
            with self._lock:
                self.failures += 1
                self.last_error = f'{type(exc).__name__}: {exc}'
                self.last_source = event.source
            log.exception('event handler failed topic=%s source=%s handler=%r',
                          event.topic, event.source, self.handler)
        else:
            with self._lock:
                self.delivered += 1

    def dispatch(self, event):
        if self._stop.is_set():
            return
        if self._queue is None:
            self.invoke(event)
            return
        try:
            self._queue.put_nowait(event)
        except Full:
            with self._lock:
                self.dropped += 1
                self.last_source = event.source
            log.warning('event queue full topic=%s source=%s handler=%r; notification dropped',
                        event.topic, event.source, self.handler)

    def _run(self):
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                if not self._stop.is_set():
                    self.invoke(event)
            finally:
                self._queue.task_done()

    def close(self):
        self._stop.set()
        if self._thread and self._thread is not current_thread():
            self._thread.join(timeout=1)
        if self._queue is not None:
            while True:
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except Empty:
                    break

    def health(self):
        with self._lock:
            return {'topic': self.topic, 'handler': repr(self.handler),
                    'status': 'degraded' if self.failures or self.dropped else 'ok',
                    'failures': self.failures, 'dropped': self.dropped,
                    'delivered': self.delivered, 'last_error': self.last_error,
                    'last_source': self.last_source,
                    'queued': self._queue is not None}


class EventBus:
    """Isolated notifications; queued=True moves expensive callbacks off publishers.

    Handlers are copied before dispatch, so handlers may safely subscribe while an
    event is being published. A slow handler only delays its publisher; expensive
    output modules should maintain their own worker/queue.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[_Subscription]] = {}
        self._lock = RLock()

    def subscribe(self, topic: str, handler: EventHandler, *, queued: bool = False,
                  queue_size: int = 64) -> Callable[[], None]:
        if not callable(handler):
            raise TypeError('handler must be callable')
        if type(queue_size) is not int or queue_size < 1:
            raise ValueError('queue_size must be a positive integer')
        subscription = _Subscription(topic, handler, queued, queue_size)
        with self._lock:
            self._handlers.setdefault(topic, []).append(subscription)
        log.debug("event subscription topic=%s handler=%r", topic, handler)

        def unsubscribe() -> None:
            with self._lock:
                handlers = self._handlers.get(topic, [])
                if subscription in handlers:
                    handlers.remove(subscription)
            subscription.close()
            log.debug("event unsubscribe topic=%s handler=%r", topic, handler)

        return unsubscribe

    def subscriber_health(self) -> list[dict[str, Any]]:
        with self._lock:
            subscriptions = [item for values in self._handlers.values() for item in values]
        return [item.health() for item in subscriptions]

    def close(self) -> None:
        with self._lock:
            subscriptions = [item for values in self._handlers.values() for item in values]
            self._handlers.clear()
        for item in subscriptions:
            item.close()

    def publish(self, event: Event) -> None:
        with self._lock:
            handlers = [
                *self._handlers.get(event.topic, ()),
                *(self._handlers.get("*", ()) if event.topic != '*' else ()),
            ]
        log.debug(
            "event publish topic=%s source=%s payload=%r handlers=%d",
            event.topic, event.source, event.payload, len(handlers),
        )
        for handler in handlers:
            handler.dispatch(event)


@dataclass(frozen=True, slots=True)
class DataValue:
    value: Any
    updated_at: datetime
    source: str
    revision: int
    quality: str = 'good'


class DataStore:
    """Thread-safe in-memory store containing only the current value per key."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._values: dict[str, DataValue] = {}
        self._revision = 0
        self._lock = RLock()
        self._changed = Condition(self._lock)
        self._category_revisions: dict[str, int] = {}

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def category_snapshot(self, key: str) -> tuple[DataValue | None, int]:
        with self._lock:
            return deepcopy(self._values.get(key)), self._category_revisions.get(key, 0)

    def wait_for_revision(self, requested: int, *, category: str | None = None,
                          timeout: float = 30) -> int:
        def latest():
            return self._revision if category is None else self._category_revisions.get(category, 0)
        with self._changed:
            self._changed.wait_for(lambda: latest() > requested, timeout=max(0, timeout))
            return latest()

    def set(self, key: str, value: Any, *, source: str = "unknown") -> DataValue:
        value = deepcopy(value)
        with self._lock:
            self._revision += 1
            item = DataValue(value, datetime.now(timezone.utc), source, self._revision)
            self._values[key] = item
            self._category_revisions[key] = item.revision
            self._changed.notify_all()
        log.debug("store set key=%s source=%s revision=%d value=%r", key, source, item.revision, value)
        self._bus.publish(Event(f"data.{key}", item, source))
        self._bus.publish(Event("data.changed", {"key": key, "item": item,
                                'operation': 'set', 'revision': item.revision}, source))
        return deepcopy(item)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            item = self._values.get(key)
            return deepcopy(default if item is None else item.value)

    def mark_quality(self, key: str, quality: str, *, owner: str) -> bool:
        """Change quality only while this producer still owns the reading."""
        if quality not in ('good', 'stale', 'unavailable'):
            raise ValueError('unsupported data quality')
        with self._lock:
            old = self._values.get(key)
            if old is None or old.source != owner or old.quality == quality:
                return False
            self._revision += 1
            item = replace(old, quality=quality, revision=self._revision)
            self._values[key] = item
            self._category_revisions[key] = item.revision
            self._changed.notify_all()
        log.info('store quality changed key=%s source=%s quality=%s revision=%s', key, owner, quality, item.revision)
        self._bus.publish(Event(f'data.{key}', item, owner))
        self._bus.publish(Event('data.changed', {'key': key, 'item': item,
                                'operation': 'quality', 'revision': item.revision}, owner))
        return True

    def item(self, key: str) -> DataValue | None:
        with self._lock:
            return deepcopy(self._values.get(key))

    def snapshot_with_revision(self) -> tuple[dict[str, DataValue], int]:
        """One atomic, detached snapshot of values and metadata."""
        with self._lock:
            return deepcopy(self._values), self._revision

    def remove(self, key: str, *, source: str = "system") -> None:
        with self._lock:
            existed = self._values.pop(key, None)
            if existed is not None:
                self._revision += 1
                revision = self._revision
                self._category_revisions[key] = revision
                self._changed.notify_all()
        if existed is not None:
            log.debug("store remove key=%s source=%s previous=%r", key, source, existed.value)
            self._bus.publish(Event("data.removed", key, source))
            self._bus.publish(Event('data.changed', {'key': key, 'item': None,
                                    'operation': 'remove', 'revision': revision}, source))

    def snapshot(self) -> dict[str, Any]:
        values, _ = self.snapshot_with_revision()
        return {key: item.value for key, item in values.items()}


class Module(Protocol):
    name: str

    def start(self) -> None: ...
    def stop(self) -> None: ...


class Runtime:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.store = DataStore(self.bus)
        self._modules: list[Module] = []
        self._required: dict[str, bool] = {}
        self._active: list[Module] = []
        self._states: dict[str, dict] = {}
        self._lifecycle_lock = RLock()
        self._health_lock = RLock()
        self._state = 'stopped'
        self._supervisor_stop = ThreadEvent()
        self._supervisor_thread = None

    def add(self, module: Module, *, required: bool = True) -> Module:
        with self._lifecycle_lock:
            if self._active or self._state not in ('stopped', 'failed'):
                raise RuntimeError('cannot register modules while runtime is active')
            if module.name in self._required:
                raise ValueError(f'duplicate module name: {module.name}')
            self._modules.append(module)
            self._required[module.name] = required
            self._module_state(module, 'stopped')
        log.debug("runtime module registered name=%s class=%s", module.name, type(module).__name__)
        return module

    def _module_state(self, module, state, error=None):
        with self._health_lock:
            self._states[module.name] = {'state': state, 'error': error,
                                         'required': self._required[module.name]}

    def health_snapshot(self):
        with self._health_lock:
            return {'state': self._state, 'modules': deepcopy(self._states)}

    def close(self):
        try:
            self.stop()
        finally:
            self.bus.close()

    def _check_workers(self):
        changed = False
        with self._health_lock:
            for module in tuple(self._active):
                state = self._states[module.name]['state']
                if state not in ('running', 'degraded'):
                    continue
                thread = getattr(module, '_thread', None)
                module_health = getattr(module, 'health', {})
                if isinstance(module_health, dict) and module_health.get('state') == 'failed':
                    error = module_health.get('error', 'worker failed')
                elif thread is not None and not thread.is_alive():
                    error = str(getattr(module, '_error', None) or 'worker terminated unexpectedly')
                else:
                    source_health = self.store.get('source.' + module.name, {})
                    local_state = module_health.get('state') if isinstance(module_health, dict) else None
                    if isinstance(source_health, dict):
                        local_state = source_health.get('state', local_state)
                    desired = 'degraded' if local_state in ('degraded', 'reconnecting', 'waiting_measurement', 'failed') else 'running'
                    if desired != state:
                        self._module_state(module, desired)
                        changed = True
                    continue
                self._module_state(module, 'failed', error)
                self._state = 'degraded'
                log.error('module worker failed name=%s error=%s', module.name, error)
                changed = True
            if self._state in ('running', 'degraded'):
                self._state = 'degraded' if any(item['state'] in ('failed', 'degraded') for item in self._states.values()) else 'running'
        if changed:
            self.bus.publish(Event('module.health', self.health_snapshot(), 'runtime'))

    def _supervise(self):
        while not self._supervisor_stop.wait(0.25):
            self._check_workers()

    def _cleanup(self, modules):
        errors = []
        for module in reversed(tuple(modules)):
            self._module_state(module, 'stopping')
            try:
                module.stop()
                thread = getattr(module, '_thread', None)
                if thread is not None and thread.is_alive():
                    raise RuntimeError('worker remains alive after stop')
            except Exception as exc:
                log.exception('module cleanup failed name=%s', module.name)
                self._module_state(module, 'failed', str(exc))
                errors.append(f'{module.name}: {exc}')
            else:
                self._module_state(module, 'stopped')
                if module in self._active:
                    self._active.remove(module)
        return errors

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._state in ('running', 'degraded'):
                return
            if self._active:
                raise RuntimeError('cleanup failed modules before restarting')
            self._state = 'starting'
            optional_failed = False
            try:
                for module in self._modules:
                    self._active.append(module)  # Includes resources from partial start.
                    self._module_state(module, 'starting')
                    try:
                        log.info('starting module %s', module.name)
                        module.start()
                    except Exception as exc:
                        self._module_state(module, 'failed', str(exc))
                        if self._required[module.name]:
                            raise
                        log.exception('optional module start failed name=%s', module.name)
                        self._cleanup([module])
                        self._module_state(module, 'failed', str(exc))
                        optional_failed = True
                    else:
                        self._module_state(module, 'running')
                        log.info('module %s started', module.name)
            except BaseException:
                log.exception('runtime startup failed')
                self._cleanup(self._active)
                self._state = 'failed'
                raise
            self._state = 'degraded' if optional_failed else 'running'
            self._supervisor_stop.clear()
            self._supervisor_thread = Thread(target=self._supervise, name='runtime-supervisor', daemon=True)
            self._supervisor_thread.start()
            self._check_workers()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._supervisor_stop.set()
            if self._supervisor_thread:
                self._supervisor_thread.join(timeout=2)
                if self._supervisor_thread.is_alive():
                    self._state = 'failed'
                    raise RuntimeError('runtime supervisor did not stop')
                self._supervisor_thread = None
            self._state = 'stopping'
            errors = self._cleanup(self._active)
            self._state = 'failed' if errors else 'stopped'
            if errors:
                raise RuntimeError('module shutdown errors: ' + '; '.join(errors))
