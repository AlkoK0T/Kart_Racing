import json
import re
from unittest.mock import patch

from behave import given, when, then


@given('запомнена ревизия показанной гонки')
def remember_race_revision(context):
    context.shown_race_revision = context.runtime.store.item('current_race').revision


@when('обновлены только часы и температура')
def update_sensors(context):
    context.runtime.store.set('current_time', '15:00:00', source='clock')
    context.runtime.store.set('temperature', 24, source='sensor')


@when('поступает новый снимок гонки')
def update_race(context):
    value = context.runtime.store.get('current_race')
    context.runtime.store.set('current_race', value, source='tcp:test')


@when('проверяют обновления только показанной гонки')
def check_race_updates(context):
    with patch('app.routes._update.wait', return_value=False):
        context.response = context.client.get('/check-updates', query_string={
            'category': 'current_race', 'revision': context.shown_race_revision})


@then('обновление гонки не обнаружено')
def unchanged_race(context):
    assert context.response.json == {'updated': False, 'revision': context.shown_race_revision}


@then('обновление гонки обнаружено')
def changed_race(context):
    assert context.response.json['updated'] is True
    assert context.response.json['revision'] > context.shown_race_revision


@then('подключён один общий контроллер обновлений')
def shared_controller(context):
    html = context.response.get_data(as_text=True)
    assert html.count('/static/js/polling.js') == 1
    assert 'сurrent_race.UPD_TIME' not in html
    assert 'data.timestamp' not in html


@then('начальная ревизия соответствует показанной гонке')
def bootstrap_revision(context):
    html = context.response.get_data(as_text=True)
    match = re.search(r'<script id="karting-poll-config" type="application/json">(.*?)</script>', html, re.S)
    assert match is not None
    assert json.loads(match.group(1))['revision'] == context.runtime.store.item('current_race').revision
