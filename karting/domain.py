"""Bounded domain validation and presentation calculations shared by sources."""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime
import math
import re
from typing import Any


class ValidationError(ValueError):
    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        super().__init__(f'{field}: {reason}')


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 1024 * 1024
    max_depth: int = 32
    max_nodes: int = 100000
    max_collection: int = 10000
    max_string: int = 4096
    max_participants: int = 128
    max_laps: int = 2000
    max_total_laps: int = 20000

    def __post_init__(self):
        for item in fields(self):
            value = getattr(self, item.name)
            if type(value) is not int or value < 1:
                raise ValueError(f'{item.name} must be a positive integer')


DEFAULT_LIMITS = Limits()
RACE_STATES = ('waiting', 'running', 'paused', 'finished')
RECORD_CATEGORIES = ('children', 'women', 'men')


@dataclass(frozen=True)
class ValidationContext:
    legacy: bool = False
    filename: str = ''
    limits: Limits = DEFAULT_LIMITS


def fail(field: str, reason: str):
    raise ValidationError(field, reason)


def text(value: Any, field: str, *, maximum: int = 256, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        fail(field, f'must be a {"possibly empty " if empty else "non-empty "}string of at most {maximum} characters')
    return value


def number(value: Any, field: str, *, positive: bool = False):
    if type(value) not in (int, float):
        fail(field, 'must be a number, not a string or boolean')
    try:
        finite = math.isfinite(value)
    except (OverflowError, ValueError):
        finite = False
    if not finite or (positive and value <= 0):
        fail(field, 'must be a finite positive number' if positive else 'must be a finite number')
    return value


def json_value(value: Any, context: ValidationContext) -> Any:
    """Copy a bounded tree, reject cycles/non-JSON types and non-finite numbers."""
    active = set()
    count = 0
    byte_count = 0
    limits = context.limits

    def charge(size, field):
        nonlocal byte_count
        byte_count += size
        if byte_count > limits.max_bytes:
            fail(field, 'expanded snapshot exceeds byte limit')

    def visit(node, field, depth):
        nonlocal count
        count += 1
        if count > limits.max_nodes or depth > limits.max_depth:
            fail(field, 'snapshot exceeds node/depth limit')
        if context.legacy and isinstance(node, (datetime, date)):
            node = node.isoformat()
        if node is None or type(node) is bool:
            charge(5, field)
            return node
        if type(node) in (int, float):
            number(node, field)
            charge(len(str(node)) + 1, field)
            return node
        if isinstance(node, str):
            text(node, field, maximum=limits.max_string, empty=True)
            try:
                charge(len(node.encode('utf-8')) + 3, field)
            except UnicodeEncodeError:
                fail(field, 'contains an invalid Unicode surrogate')
            return node
        if not isinstance(node, (dict, list)):
            fail(field, 'must contain only JSON-compatible values')
        if id(node) in active:
            fail(field, 'cyclic snapshots are not supported')
        if len(node) > limits.max_collection:
            fail(field, 'collection exceeds item limit')
        charge(2, field)
        active.add(id(node))
        try:
            if isinstance(node, list):
                return [visit(item, f'{field}[{index}]', depth + 1) for index, item in enumerate(node)]
            result = {}
            for key, item in node.items():
                if not isinstance(key, str) and not (context.legacy and type(key) is int):
                    fail(field, 'object keys must be strings (integer keys also allowed in legacy YAML)')
                visit(str(key), field + '.<key>', depth + 1)
                result[key] = visit(item, f'{field}.{key}', depth + 1)
            return result
        finally:
            active.remove(id(node))

    return visit(value, 'data', 0)


def version(data: dict):
    if 'schema_version' in data and (type(data['schema_version']) is not int or data['schema_version'] != 1):
        fail('data.schema_version', 'only integer version 1 is supported')


def timestamp(value: Any, field: str, *, nullable: bool = False):
    if nullable and value is None:
        return
    text(value, field)
    try:
        # Python 3.10 also supports Z after explicit conversion.
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        fail(field, 'must be an ISO 8601 timestamp with timezone')
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        fail(field, 'must include a timezone')


def metadata(data: dict):
    version(data)
    if 'updated_at' in data:
        timestamp(data['updated_at'], 'data.updated_at')


def lap_arrays(data: dict, context: ValidationContext):
    laps = data.get('laps')
    if not isinstance(laps, dict):
        fail('data.laps', 'must be an object of lap arrays')
    if len(laps) > context.limits.max_participants:
        fail('data.laps', 'too many participants')
    total = 0
    for kart, times in laps.items():
        field = f'data.laps.{kart}'
        if not (context.legacy and type(kart) is int):
            text(kart, field, maximum=64)
        if not isinstance(times, list):
            fail(field, 'must be an array; use [] for no completed laps')
        total += len(times)
        if len(times) > context.limits.max_laps or total > context.limits.max_total_laps:
            fail(field, 'lap count exceeds limit')
        for index, value in enumerate(times):
            number(value, f'{field}[{index}]', positive=True)
        if times:
            number(sum(times), field + '.total', positive=True)
    return laps


def table(value: Any, field: str, *, template: bool = False):
    if not isinstance(value, dict):
        fail(field, 'must be a table object')
    if template and value.get('template') not in ('tmpl_1', 'tmpl_sub_table1'):
        fail(field + '.template', 'must be a registered legacy table template')
    headers = value.get('headers')
    rows = value.get('rows')
    if not isinstance(headers, list) or not headers:
        fail(field + '.headers', 'must be a non-empty array of labels')
    for index, header in enumerate(headers):
        text(header, f'{field}.headers[{index}]')
    if not isinstance(rows, dict):
        fail(field + '.rows', 'must be an object of rows')
    for key, row in rows.items():
        if not isinstance(row, list) or len(row) != len(headers):
            fail(f'{field}.rows.{key}', 'must have one cell per header')
        if any(isinstance(cell, (dict, list)) for cell in row):
            fail(f'{field}.rows.{key}', 'cells must be scalar values')
    result = dict(value)
    result.setdefault('title', '')
    text(result['title'], field + '.title', empty=True)
    return result


def race_snapshot(data: Any, context: ValidationContext) -> Any:
    if context.legacy and isinstance(data, list):
        return [table(value, f'data[{index}]', template=True) for index, value in enumerate(data)]
    if not isinstance(data, dict):
        fail('data', 'current_race must be an object')
    metadata(data)
    if 'style' in data and data['style'] not in ('laps', 'race'):
        fail('data.style', 'must be laps or race')
    contract = not context.legacy or any(key in data for key in ('schema_version', 'race_id', 'participants'))
    if contract:
        text(data.get('race_id'), 'data.race_id')
        if data.get('state') not in RACE_STATES:
            fail('data.state', 'must be waiting, running, paused or finished')
        participants = data.get('participants')
        if not isinstance(participants, dict):
            fail('data.participants', 'must be an object')
        if len(participants) > context.limits.max_participants:
            fail('data.participants', 'too many participants')
        for kart, participant in participants.items():
            if not isinstance(participant, dict):
                fail(f'data.participants.{kart}', 'must be an object')
            text(participant.get('name'), f'data.participants.{kart}.name')
            if participant.get('category') not in RECORD_CATEGORIES:
                fail(f'data.participants.{kart}.category', 'must be children, women or men')
        laps = lap_arrays(data, context)
        if participants.keys() != laps.keys():
            fail('data.participants', 'keys must match data.laps')
    elif 'laps' in data:
        laps = lap_arrays(data, context)
    else:
        if not any(key in data for key in ('next', 'race')):
            fail('data', 'legacy race must contain laps, next or race')
        if 'race' in data and not isinstance(data['race'], dict):
            fail('data.race', 'legacy race section must be an object')
        laps = None
    if 'state' in data and data['state'] not in RACE_STATES:
        fail('data.state', 'must be waiting, running, paused or finished')
    if 'started_at' in data:
        timestamp(data['started_at'], 'data.started_at', nullable=True)
    if 'next' in data:
        data['next'] = table(data['next'], 'data.next')
    return calculate_race(data, laps, context.filename)


def calculate_race(data: dict, source_laps: dict | None, filename: str = '') -> dict:
    """Pure presentation calculation; absence never becomes a zero-second lap."""
    style = data.get('style', 'laps' if filename == 'laps.yaml' else 'race')
    result = {**data, 'raw': data, 'style': style}
    if source_laps is None:
        result['current_race'] = None
        return result
    laps = {kart: list(times) for kart, times in source_laps.items()}
    best = {kart: min(times) if times else None for kart, times in laps.items()}
    ranked = sorted((kart for kart in laps if best[kart] is not None), key=lambda kart: best[kart])
    ranks = {kart: position for position, kart in enumerate(ranked, 1)}
    overall_best = min((value for value in best.values() if value is not None), default=None)
    karts = ranked + [kart for kart in laps if best[kart] is None]
    result['current_race'] = {
        'karts': karts, 'laps': laps, 'best_times': best,
        'avg_times': {kart: sum(times) / len(times) if times else None for kart, times in laps.items()},
        'positions': {kart: ranks.get(kart) for kart in laps},
        'time_deltas': {kart: value - overall_best if value is not None else None for kart, value in best.items()},
        'last_laps': {kart: times[-1] if times else None for kart, times in laps.items()},
        'total_time': {kart: sum(times) if times else None for kart, times in laps.items()},
        'total_laps': {kart: len(times) for kart, times in laps.items()},
        'max_time': max((len(times) for times in laps.values()), default=0),
        'diff': {kart: times[-1] - times[-2] if len(times) > 1 else None for kart, times in laps.items()},
        'type': 'current_race',
    }
    return result


def normalize_race(data: Any, filename: str = '') -> Any:
    """Compatibility entry point for legacy YAML and existing presentation tests."""
    context = ValidationContext(legacy=True, filename=filename)
    return race_snapshot(json_value(data, context), context)


def record(value: Any, field: str, *, legacy: bool):
    if value is None:
        return
    if legacy and type(value) in (int, float):
        number(value, field, positive=True)
        return
    if not isinstance(value, dict):
        fail(field, 'must be a record object or null')
    if legacy:
        if 'name' in value or 'driver' in value:
            text(value.get('name', value.get('driver')), field + '.name')
        number(value.get('time', value.get('value')), field + '.time', positive=True)
    else:
        text(value.get('name'), field + '.name')
        number(value.get('time'), field + '.time', positive=True)
    for key in ('kart', 'race_id'):
        if key in value:
            text(value[key], field + '.' + key)
    if 'achieved_at' in value:
        timestamp(value['achieved_at'], field + '.achieved_at')


def records_snapshot(data: Any, context: ValidationContext, period: str):
    if not isinstance(data, dict):
        fail('data', 'records snapshot must be an object')
    metadata(data)
    opposite = 'month' if period == 'date' else 'date'
    if opposite in data:
        fail('data.' + opposite, f'use {period} for this records category')
    if period in data:
        value = text(data[period], 'data.' + period)
        try:
            if period == 'date':
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                    raise ValueError()
                date.fromisoformat(value)
            else:
                if not re.fullmatch(r'\d{4}-\d{2}', value):
                    raise ValueError()
                date(int(value[:4]), int(value[5:]), 1)
        except ValueError:
            fail('data.' + period, 'must be a valid YYYY-MM-DD date' if period == 'date' else 'must be a valid YYYY-MM month')
    legacy = context.legacy and 'schema_version' not in data
    if not legacy:
        for key in RECORD_CATEGORIES:
            if key not in data:
                fail('data.' + key, 'is required; use null for an absent record')
            record(data[key], 'data.' + key, legacy=False)
    else:
        aliases = (('children', 'kids', 'дети'), ('women', 'female', 'женщины'), ('men', 'male', 'мужчины'))
        found = False
        for group in aliases:
            keys = [key for key in group if key in data]
            if len(keys) > 1:
                fail('data.' + group[0], 'duplicate category aliases')
            for key in keys:
                found = True
                record(data[key], 'data.' + key, legacy=True)
        if not found:
            fail('data', 'legacy records must contain at least one record category')
    return data


def sensor_snapshot(data: Any, context: ValidationContext, category: str):
    value = data
    if isinstance(data, dict):
        if category in data and 'value' in data:
            fail('data', 'specify either value or the sensor name, not both')
        value = data.get(category, data.get('value'))
    number(value, 'data.' + category)
    if category == 'humidity' and not 0 <= value <= 100:
        fail('data.humidity', 'must be between 0 and 100 percent')
    return data


def forecast_snapshot(data: Any, context: ValidationContext):
    if not isinstance(data, (str, dict)):
        fail('data', 'forecast must be a string or object')
    return data


def clock_snapshot(data: Any, context: ValidationContext):
    timestamp(data, 'data.current_time')
    return data
