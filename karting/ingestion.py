"""One validation boundary for external files, TCP and serial JSON lines."""
from __future__ import annotations

import json
from threading import RLock
from typing import Any, Callable

import yaml

from karting.core import DataStore
from karting.domain import (
    DEFAULT_LIMITS, Limits, ValidationContext, ValidationError, clock_snapshot,
    fail, forecast_snapshot, json_value, race_snapshot, records_snapshot,
    sensor_snapshot, text,
)


Validator = Callable[[Any, ValidationContext], Any]


class CategoryRegistry:
    """Explicit, extendable registry; no arbitrary new network categories."""
    def __init__(self, limits: Limits = DEFAULT_LIMITS):
        self.limits = limits
        self._validators: dict[str, Validator] = {}
        self._lock = RLock()

    def register(self, category: str, validator: Validator):
        text(category, 'category', maximum=64)
        if not callable(validator):
            raise TypeError('validator must be callable')
        with self._lock:
            if category in self._validators:
                raise ValueError(f'category {category} is already registered')
            if len(self._validators) >= 64:
                raise ValueError('category registry is full')
            self._validators[category] = validator

    def prepare(self, category: str, data: Any, *, legacy: bool = False, filename: str = ''):
        text(category, 'category', maximum=64)
        with self._lock:
            validator = self._validators.get(category)
        if validator is None:
            fail('category', f'unknown category {category!r}; register it explicitly')
        context = ValidationContext(legacy, filename, self.limits)
        value = json_value(data, context)
        return validator(value, context)


def default_registry(limits: Limits = DEFAULT_LIMITS):
    registry = CategoryRegistry(limits)
    registry.register('current_race', race_snapshot)
    registry.register('records_day', lambda data, context: records_snapshot(data, context, 'date'))
    registry.register('records_month', lambda data, context: records_snapshot(data, context, 'month'))
    registry.register('temperature', lambda data, context: sensor_snapshot(data, context, 'temperature'))
    registry.register('humidity', lambda data, context: sensor_snapshot(data, context, 'humidity'))
    registry.register('forecast_tomorrow', forecast_snapshot)
    registry.register('current_time', clock_snapshot)
    return registry


def utf8_message(raw: bytes | str, limits: Limits = DEFAULT_LIMITS) -> str:
    if not isinstance(raw, (bytes, str)):
        fail('message', 'must be UTF-8 bytes or text')
    try:
        encoded = raw if isinstance(raw, bytes) else raw.encode('utf-8')
        if len(encoded) > limits.max_bytes:
            fail('message', 'message too large')
        return encoded.decode('utf-8')
    except UnicodeError as exc:
        raise ValidationError('message', 'must be valid UTF-8') from exc


def parse_json(raw: bytes | str, limits: Limits = DEFAULT_LIMITS):
    def reject_constant(value):
        fail('message', f'non-finite JSON constant {value} is not allowed')

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                fail('message', 'duplicate JSON object keys are not allowed')
            result[key] = value
        return result

    try:
        return json.loads(utf8_message(raw, limits), parse_constant=reject_constant, object_pairs_hook=unique_object)
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError('message', f'invalid JSON: {exc}') from exc


class _UniqueSafeLoader(yaml.SafeLoader):
    def __init__(self, stream, limits=DEFAULT_LIMITS):
        super().__init__(stream)
        self._limits = limits
        self._merge_sizes = {}
        self._merge_active = set()
        self._merge_nodes = 0

    def _merge_size(self, node, depth=0):
        """Bound merge expansion before SafeLoader allocates flattened pairs."""
        identity = id(node)
        if identity in self._merge_active:
            fail('message', 'cyclic YAML merges are not supported')
        if depth > self._limits.max_depth:
            fail('message', 'YAML merge exceeds depth limit')
        if identity in self._merge_sizes:
            return self._merge_sizes[identity]
        self._merge_active.add(identity)
        try:
            size = 0
            for key, value in node.value:
                if key.tag != 'tag:yaml.org,2002:merge':
                    size += 1
                elif isinstance(value, yaml.MappingNode):
                    size += self._merge_size(value, depth + 1)
                elif isinstance(value, yaml.SequenceNode):
                    if len(value.value) > self._limits.max_collection:
                        fail('message', 'YAML merge sequence exceeds item limit')
                    for child in value.value:
                        if isinstance(child, yaml.MappingNode):
                            size += self._merge_size(child, depth + 1)
                            if size > self._limits.max_collection:
                                fail('message', 'expanded YAML mapping exceeds item limit')
                # Invalid merge node types are rejected by SafeLoader itself.
                if size > self._limits.max_collection:
                    fail('message', 'expanded YAML mapping exceeds item limit')
            self._merge_nodes += size
            if self._merge_nodes > self._limits.max_nodes:
                fail('message', 'expanded YAML merges exceed node limit')
            self._merge_sizes[identity] = size
            return size
        finally:
            self._merge_active.remove(identity)

    def flatten_mapping(self, node):
        self._merge_size(node)
        super().flatten_mapping(node)

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            # Standard YAML merges may legitimately override inherited keys.
            if key_node.tag == 'tag:yaml.org,2002:merge':
                continue
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    fail('message', 'duplicate YAML object keys are not allowed')
                seen.add(key)
            except TypeError as exc:
                raise ValidationError('message', 'YAML object keys must be scalar values') from exc
        return super().construct_mapping(node, deep=deep)


def parse_yaml(raw: bytes, limits: Limits = DEFAULT_LIMITS):
    try:
        return yaml.load(utf8_message(raw, limits), Loader=lambda stream: _UniqueSafeLoader(stream, limits))
    except (yaml.YAMLError, RecursionError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError('message', f'invalid YAML: {exc}') from exc


class DataIngestor:
    def __init__(self, store: DataStore, registry: CategoryRegistry | None = None):
        self.store = store
        self.registry = registry if registry is not None else default_registry()

    def ingest(self, category: str, data: Any, *, source: str, legacy: bool = False, filename: str = ''):
        value = self.registry.prepare(category, data, legacy=legacy, filename=filename)
        return self.store.set(category, value, source=source)

    def message(self, value: Any, *, source: str):
        value = json_value(value, ValidationContext(limits=self.registry.limits))
        if not isinstance(value, dict):
            fail('message', 'envelope must be a JSON object')
        if 'category' not in value:
            fail('category', 'is required')
        if 'data' not in value:
            fail('data', 'is required')
        return self.ingest(value['category'], value['data'], source=source, filename='laps.yaml')
