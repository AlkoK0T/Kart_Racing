from behave import when, then
from app import create_app


@when('удаляют гонку тестового веб-приложения')
def remove_web_race(context):
    context.runtime.store.remove('current_race')


@then('второе веб-приложение сохраняет исходный подвид')
def independent_web_view(context):
    other = create_app(context.runtime).test_client()
    assert other.get('/state').json['view'] == 'style1'
    assert context.client.get('/state').json['view'] == 'style2'
