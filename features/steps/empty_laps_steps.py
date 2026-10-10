from behave import given, when, then

from karting.modules.files import normalize_race


@given('гонка содержит только участников без кругов')
def empty_participants(context):
    context.laps_input = {'laps': {'7': [], '12': []}}


@given('гонка содержит участника без кругов и участника с результатом')
def mixed_participants(context):
    context.laps_input = {'laps': {'7': [], '12': [31.2]}}


@when('рассчитывают результаты кругов')
def calculate_laps(context):
    context.calculated_race = normalize_race(context.laps_input, 'laps.yaml')['current_race']


@then('число кругов равно нулю и времена отсутствуют')
def no_invented_results(context):
    race = context.calculated_race
    for kart in ('7', '12'):
        assert race['laps'][kart] == []
        assert race['total_laps'][kart] == 0
        for field in ('best_times', 'avg_times', 'last_laps', 'total_time', 'time_deltas', 'positions', 'diff'):
            assert race[field][kart] is None, (field, race[field][kart])


@then('участник без кругов не получает место и отображается после результата')
def unclassified_after_result(context):
    race = context.calculated_race
    assert race['positions']['7'] is None
    assert race['positions']['12'] == 1
    assert race['karts'] == ['12', '7']
    assert race['time_deltas']['12'] == 0
    assert race['best_times']['7'] is None


@when('участник "{kart}" завершает первый круг за {seconds:g} секунд')
def complete_first_lap(context, kart, seconds):
    context.laps_input['laps'][kart].append(seconds)


@then('первый круг учитывается ровно один раз')
def first_lap_once(context):
    race = context.calculated_race
    assert race['laps']['7'] == [30.5]
    assert race['total_laps']['7'] == 1
    assert race['best_times']['7'] == 30.5
    assert race['total_time']['7'] == 30.5
    assert race['positions']['7'] == 1
    assert race['diff']['7'] is None


@when('показывают начало гонки в стиле "{style}" и виде "{view}"')
def render_start(context, style, view):
    context.client.put('/state', json={'view': view})
    context.laps_input['style'] = style
    context.runtime.store.set('current_race', normalize_race(context.laps_input, 'laps.yaml'))
    context.response = context.client.get('/')


@then('пустые результаты показаны прочерками без фиктивных времён')
def empty_display(context):
    html = context.response.get_data(as_text=True)
    assert '—' in html
    assert 'None' not in html
    assert '0.000' not in html
