import copy
import json
import os
import socket
import time

from behave import given, when, then

from karting.ingestion import DataIngestor


@given('сохранён последний валидный снимок для проверки отказа')
def save_valid_snapshot(context):
    DataIngestor(context.store).message({'category': 'temperature', 'data': 20}, source='test')
    context.before_validation = copy.deepcopy(context.store.snapshot())
    context.before_validation_revision = context.store.item('temperature').revision
    context.events.clear()


@when('отправляют проверочный пакет "{kind}"')
def validation_packet(context, kind):
    race = {'schema_version': 1, 'race_id': 'race-1', 'state': 'running',
            'participants': {'7': {'name': 'Иван', 'category': 'men'}}, 'laps': {'7': [31.2]}}
    message = {'category': 'current_race', 'data': race}
    if kind == 'missing-data':
        message = {'category': 'records_day'}
    elif kind == 'unsupported-version':
        race['schema_version'] = 2
    elif kind == 'invalid-state':
        race['state'] = 'broken'
    elif kind == 'participants-mismatch':
        race['participants'] = {}
    elif kind == 'non-numeric-lap':
        race['laps']['7'] = ['31.2']
    elif kind == 'non-finite':
        message = {'category': 'temperature', 'data': float('nan')}
    elif kind == 'incomplete-records':
        message = {'category': 'records_day', 'data': {'men': 30}}
    elif kind == 'unknown-category':
        message = {'category': 'unknown', 'data': 1}
    elif kind == 'empty-records':
        message = {'category': 'records_day', 'data': {'children': None, 'women': None, 'men': None}}
    elif kind == 'custom-category':
        message = {'category': 'custom', 'data': {'text': 'ok'}}
    elif kind != 'invalid-utf8':
        raise AssertionError(f'Unknown test packet {kind}')
    raw = b'{"category":"temperature","data":"\xff"}' if kind == 'invalid-utf8' else json.dumps(message).encode()
    with socket.create_connection(context.tcp._server.server_address, timeout=2) as connection:
        connection.sendall(raw + b'\n')
        with connection.makefile('rb') as stream:
            context.tcp_response = json.loads(stream.readline())


@then('ответ указывает поле ошибки валидации')
def validation_error_field(context):
    assert context.tcp_response.get('field')
    assert context.tcp_response.get('error')


@then('последний валидный снимок и его ревизия не изменились')
def unchanged_after_rejection(context):
    assert context.store.snapshot() == context.before_validation
    assert context.store.item('temperature').revision == context.before_validation_revision
    assert context.events == []


@given('появился семантически некорректный YAML категории "{category}"')
def bad_semantic_yaml(context, category):
    path = context.data_root / category / 'invalid.yaml'
    path.write_text('men: .nan\n', encoding='utf-8')
    os.utime(path, (time.time(), time.time()))


@when('явно регистрируют проверочную категорию')
def register_category(context):
    context.tcp._ingestor.registry.register('custom', lambda data, validation_context: data)
