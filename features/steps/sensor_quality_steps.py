from behave import when, then
from app import create_app
from karting.modules.odtemp import ODTempSource
from karting.modules.screens import ContentPlanner, ScreenConfig


@when('показания ODTEMP отмечены устаревшими')
def mark_stale(context):
    ODTempSource(context.store)._invalidate_readings('stale')


@then('температура скрыта на экране и в текущем API')
def hidden_temperature(context):
    frame = ContentPlanner(context.store).frame(ScreenConfig('test', 0, 0), 0)
    assert '22.5' not in frame.top_lines[1]
    context.quality_client = create_app(RuntimeAdapter(context)).test_client()
    assert context.quality_client.get('/api/data').json['temperature'] is None


class RuntimeAdapter:
    def __init__(self, context):
        self.store, self.bus = context.store, context.bus


@then('последнее измерение доступно в диагностике')
def diagnostic_last_value(context):
    item = context.quality_client.get('/api/health').json['readings']['temperature']
    assert item['last_value'] == 22.5
    assert item['quality'] == 'stale'
