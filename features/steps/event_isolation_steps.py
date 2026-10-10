from behave import given, then


@given('подписан падающий наблюдатель температуры')
def subscribe_broken(context):
    def broken(event):
        raise RuntimeError('test observer failure')
    context.bus.subscribe('data.temperature', broken)


@then('здоровье падающего наблюдателя отражает ошибку')
def subscriber_failed(context):
    health = [item for item in context.bus.subscriber_health() if item['topic'] == 'data.temperature']
    assert len(health) == 1
    assert health[0]['status'] == 'degraded'
    assert health[0]['failures'] == 1
    assert 'test observer failure' in health[0]['last_error']
