from behave import when, then
from test_support.odtemp_cycle import watchdog_recovery_cycle


@when('выполняется полный управляемый цикл watchdog и восстановления ODTEMP')
def cycle(context):
    context.recovery = watchdog_recovery_cycle()


@then('через 120 секунд показание становится stale и retry назначается через 60 секунд')
def stale_retry(context):
    assert (22, 'stale') in context.recovery.readings
    assert context.recovery.waits == [60]


@then('термометр повторно определяется на COM14 и инициализируется')
def reinitialized(context):
    result = context.recovery
    assert result.opened == ['COM12', 'COM14'] and result.detects == 2
    assert all(device.commands == [b'~I\n', b'~F\n'] for device in result.devices)
    assert all(device.closed for device in result.devices)


@then('первое новое измерение единожды записано как восстановление чтения')
def restored(context):
    assert (23.5, 'good') in context.recovery.readings
    assert sum('reading restored' in message for message in context.recovery.messages) == 1
