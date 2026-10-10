from __future__ import annotations

import json
import logging
import os
import socket
import sys
import tempfile
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from behave import given, then, when

from app import create_app
from com_port_inventory import PortInfo, compare, stable_device_id, update_registry
from karting import DataStore, EventBus, Runtime
from karting.modules.clock import ClockSource
from karting.modules.files import FileSource, normalize_race
from karting.modules.odtemp import (
    ODTempSource, find_odtemp_port, is_measurement_stale, parse_odtemp_line,
)
from karting.modules.serial import SerialSource
from karting.modules.screens import (
    ContentPlanner, ScreenConfig, ScreenDisplay, ScreenFrame, load_screen_configs,
)
from karting.modules.tcp import TcpJsonSource


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@given('обнаружено USB-устройство на "{port}" с VID "{vid}" PID "{pid}" и серийным номером "{serial_number}"')
def step_usb_device(context, port, vid, pid, serial_number):
    context.raw_port = SimpleNamespace(
        device=port, vid=int(vid, 16), pid=int(pid, 16),
        serial_number=serial_number, hwid=f"USB VID:PID={vid}:{pid} SER={serial_number}",
    )
    context.inventory_port = PortInfo(
        port, f"USB:{vid}:{pid}:{serial_number}", "USB device", context.raw_port.hwid,
        vid, pid, serial_number, "Vendor", "Product", "1-1",
    )


@when("вычисляется стабильный ID устройства")
def step_stable_id(context):
    context.calculated_device_id = stable_device_id(context.raw_port)


@then('ID равен "{device_id}"')
def step_id_equals(context, device_id):
    assert context.calculated_device_id == device_id


@given('в CSV устройство "{device_id}" ожидается на "{port}"')
def step_registry_device(context, device_id, port):
    context.registry = {device_id: {
        "device_id": device_id, "name": "Test device", "expected_port": port,
        "notes": "", "vid": "0483", "pid": "A26A", "serial_number": "ABC123",
        "manufacturer": "", "product": "", "last_seen": "",
    }}


@given('это устройство обнаружено на "{port}"')
def step_device_on_port(context, port):
    device_id = next(iter(context.registry))
    context.inventory_ports = [PortInfo(
        port, device_id, "USB device", "hwid", "0483", "A26A", "ABC123",
        "Vendor", "Product", "1-1",
    )]


@given("подключённых COM-устройств нет")
def step_no_ports(context):
    context.inventory_ports = []


@when("список портов сравнивается с CSV")
def step_compare_ports(context):
    context.inventory_result = compare(context.inventory_ports, context.registry)


@then('состояние устройства равно "{status}"')
def step_inventory_status(context, status):
    assert context.inventory_result[0][0] == status


@given("создан пустой CSV-реестр устройств")
def step_empty_registry(context):
    context.registry = {}


@when("новое устройство добавляют в реестр")
def step_add_inventory(context):
    update_registry(context.registry, [context.inventory_port])


@then('CSV содержит устройство "{device_id}" на порту "{port}"')
def step_registry_contains(context, device_id, port):
    assert context.registry[device_id]["expected_port"] == port


def _value(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _core(context):
    context.bus = EventBus()
    context.store = DataStore(context.bus)
    context.events = []
    context.bus.subscribe("*", context.events.append)
    context.resources = []


@given("создано пустое ядро")
def step_empty_core(context):
    _core(context)


@given("включён перехват DEBUG-журнала ядра")
def step_capture_core_log(context):
    context.log_handler = _ListHandler()
    context.core_logger = logging.getLogger("karting.core")
    context.previous_log_level = context.core_logger.level
    context.core_logger.setLevel(logging.DEBUG)
    context.core_logger.addHandler(context.log_handler)


@then('журнал содержит источник "{source}" и категорию "{key}"')
def step_log_value(context, source, key):
    text = "\n".join(context.log_handler.messages)
    assert f"key={key}" in text and f"source={source}" in text, text


@then('журнал содержит событие "{topic}"')
def step_log_event(context, topic):
    text = "\n".join(context.log_handler.messages)
    assert f"topic={topic}" in text, text
    context.core_logger.removeHandler(context.log_handler)
    context.core_logger.setLevel(context.previous_log_level)


@when('источник "{source}" записывает в "{key}" значение {value}')
def step_write(context, source, key, value):
    context.store.set(key, _value(value), source=source)


@given('источник "{source}" записал в "{key}" значение "{value}"')
def step_given_write(context, source, key, value):
    context.store.set(key, value, source=source)


@then('в "{key}" хранится значение {value}')
def step_value(context, key, value):
    assert context.store.get(key) == _value(value)


@then('ревизия "{key}" равна {revision:d}')
def step_revision(context, key, revision):
    assert context.store.item(key).revision == revision


@then('опубликованы события "{first}" и "{second}"')
def step_events(context, first, second):
    topics = [event.topic for event in context.events]
    assert first in topics and second in topics, topics


@then('опубликовано событие "{topic}"')
def step_event(context, topic):
    assert topic in [event.topic for event in context.events]


@then("снимок содержит {count:d} категорию")
def step_snapshot_size(context, count):
    assert len(context.store.snapshot()) == count


@when('удаляют категорию "{key}"')
def step_remove(context, key):
    context.store.remove(key)


@then('категория "{key}" отсутствует')
def step_absent(context, key):
    assert context.store.item(key) is None


@given("обработчик подписан на все события")
def step_subscribe(context):
    context.private_events = []
    context.unsubscribe = context.bus.subscribe("*", context.private_events.append)


@when("обработчик отписывается")
def step_unsubscribe(context):
    context.unsubscribe()


@then("отписанный обработчик не получил событий")
def step_no_events(context):
    assert context.private_events == []


class _Module:
    def __init__(self, name, calls, fail=False):
        self.name, self.calls, self.fail = name, calls, fail

    def start(self):
        self.calls.append(f"start:{self.name}")
        if self.fail:
            raise RuntimeError("expected")

    def stop(self):
        self.calls.append(f"stop:{self.name}")


@given("создан runtime с двумя модулями")
def step_runtime_two(context):
    context.calls = []
    context.runtime = Runtime()
    context.runtime.add(_Module("first", context.calls))
    context.runtime.add(_Module("second", context.calls))


@when("runtime запускают и останавливают")
def step_runtime_cycle(context):
    context.runtime.start()
    context.runtime.stop()


@then('порядок вызовов равен "{calls}"')
def step_calls(context, calls):
    assert context.calls == calls.split(",")


@given("создан runtime с модулем, падающим при запуске")
def step_runtime_failing(context):
    context.calls = []
    context.runtime = Runtime()
    context.runtime.add(_Module("first", context.calls))
    context.runtime.add(_Module("broken", context.calls, True))


@when("runtime пытаются запустить")
def step_runtime_try(context):
    try:
        context.runtime.start()
        context.start_failed = False
    except RuntimeError:
        context.start_failed = True


@then("ошибка запуска перехвачена")
def step_failed(context):
    assert context.start_failed


@then("первый модуль остановлен при откате")
def step_rollback(context):
    assert context.calls == ["start:first", "start:broken", "stop:broken", "stop:first"]


@given("создан временный каталог данных")
def step_temp_data(context):
    _core(context)
    context.temporary = tempfile.TemporaryDirectory()
    context.data_root = Path(context.temporary.name)
    context.file_source = FileSource(context.store, context.data_root)


def _write_category(context, category, name, content, timestamp):
    directory = context.data_root / category
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text(content, encoding="utf-8")
    os.utime(path, (timestamp, timestamp))
    return path


@given('в категории "{category}" есть старый и новый YAML')
def step_old_new(context, category):
    now = time.time()
    _write_category(context, category, "old.yaml", "men: 40\n", now - 10)
    _write_category(context, category, "new.yaml", "men: 30\n", now)


@when("файловый источник сканирует каталог")
def step_scan(context):
    context.file_source.scan()


@then('в "{category}" хранится новый YAML')
def step_new_yaml(context, category):
    assert context.store.get(category) == {"men": 30}


@given('в корне есть файл "{filename}" с кругами')
def step_legacy_laps(context, filename):
    (context.data_root / filename).write_text("laps:\n  7: [32.5, 31.0]\n", encoding="utf-8")


@then("текущая гонка нормализована для старого веб-шаблона")
def step_normalized(context):
    race = context.store.get("current_race")
    assert race["style"] == "laps"
    assert race["current_race"]["best_times"][7] == 31.0
    assert race["current_race"]["total_laps"][7] == 2


@given('в категории "{category}" есть один YAML')
def step_one_yaml(context, category):
    _write_category(context, category, "one.yaml", "value: 10\n", time.time())


@when("файловый источник сканирует каталог дважды")
def step_scan_twice(context):
    context.file_source.scan()
    context.file_source.scan()


@given('в категории "{category}" есть корректный YAML')
def step_valid_yaml(context, category):
    context.valid_path = _write_category(context, category, "valid.yaml", "men: 29.5\n", time.time() - 10)


@given("файловый источник уже прочитал данные")
def step_already_scanned(context):
    context.file_source.scan()


@given('появился ошибочный новый YAML категории "{category}"')
def step_bad_yaml(context, category):
    _write_category(context, category, "bad.yaml", "men: [\n", time.time())


@then('сохранены прежние данные "{category}"')
def step_preserved(context, category):
    assert context.store.get(category) == {"men": 29.5}


@when("модуль часов кратковременно запущен")
def step_clock(context):
    source = ClockSource(context.store, 0.01)
    source.start()
    time.sleep(0.03)
    source.stop()


@then('категория "{key}" содержит дату и время')
def step_datetime(context, key):
    assert isinstance(context.store.get(key), datetime)


@given("запущен TCP источник на свободном порту")
def step_tcp(context):
    _core(context)
    context.tcp = TcpJsonSource(context.store, "127.0.0.1", 0)
    context.tcp.start()
    context.resources.append(context.tcp)


def _tcp_send(context, payload):
    address = context.tcp._server.server_address
    with socket.create_connection(address, timeout=2) as connection:
        connection.sendall(payload + b"\n")
        context.tcp_response = json.loads(connection.makefile("rb").readline())


@when('TCP клиент отправляет категорию "{category}" и значение {value}')
def step_tcp_value(context, category, value):
    _tcp_send(context, json.dumps({"category": category, "data": _value(value)}).encode())


@when('TCP клиент отправляет строку "{line}"')
def step_tcp_bad(context, line):
    _tcp_send(context, line.encode())


@then("TCP отвечает успешно")
def step_tcp_ok(context):
    assert context.tcp_response == {"ok": True}


@then("TCP отвечает с ошибкой")
def step_tcp_error(context):
    assert context.tcp_response["ok"] is False


class _FakeSerial:
    lines = deque()

    def __init__(self, *args, **kwargs):
        self.closed = False

    def readline(self, size=-1):
        if self.lines:
            return self.lines.popleft()
        time.sleep(0.005)
        return b""

    def close(self):
        self.closed = True


def _fake_serial(context, line):
    _core(context)
    _FakeSerial.lines = deque([line.encode() + b"\n"])
    context.serial_module_previous = sys.modules.get("serial")
    sys.modules["serial"] = SimpleNamespace(Serial=_FakeSerial)
    context.serial_source = SerialSource(context.store, "TEST")


@given('подменённый RS-232 выдаёт строку "{line}"')
def step_serial_number(context, line):
    _fake_serial(context, line)


@given('подменённый RS-232 выдаёт JSON категории "{category}"')
def step_serial_json(context, category):
    _fake_serial(context, json.dumps({"category": category, "data": "sunny"}))


@when("запускается модуль RS-232")
def step_start_serial(context):
    context.serial_source.start()
    deadline = time.time() + 1
    while not context.store.snapshot() and time.time() < deadline:
        time.sleep(0.01)
    context.serial_source.stop()


@then('в "{key}" хранится текст "{value}"')
def step_text_value(context, key, value):
    assert context.store.get(key) == value


@given('в списке портов есть ODTEMP на "{port}"')
def step_odtemp_port(context, port):
    context.serial_ports = [SimpleNamespace(device=port, vid=0x0483, pid=0xA26A)]


@when("выполняется автоопределение ODTEMP")
def step_odtemp_detect(context):
    context.selected_port = find_odtemp_port(context.serial_ports)


@when('для ODTEMP вручную задан порт "{port}"')
def step_odtemp_explicit(context, port):
    context.selected_port = find_odtemp_port(context.serial_ports, explicit_port=port)


@then('выбран порт "{port}"')
def step_selected_port(context, port):
    assert context.selected_port == port


@when('ODTEMP возвращает строку "{line}"')
def step_odtemp_line(context, line):
    context.odtemp_measurement = parse_odtemp_line(line)


@then("получена температура {temperature:g} без влажности")
def step_odtemp_temperature(context, temperature):
    assert context.odtemp_measurement == (temperature, None)


@then("получены температура {temperature:g} и влажность {humidity:g}")
def step_odtemp_humidity(context, temperature, humidity):
    assert context.odtemp_measurement == (temperature, humidity)


class _ODTempDevice:
    def __init__(self, answers):
        self.answers = deque(answer.encode() + b"\n" for answer in answers)
        self.writes = []

    def write(self, value):
        self.writes.append(value.decode().strip())

    def flush(self):
        pass

    def readline(self):
        return self.answers.popleft() if self.answers else b""


@given("ODTEMP сообщает период автоматической выдачи {period:d} мс")
def step_odtemp_stream_on(context, period):
    _core(context)
    context.odtemp = ODTempSource(context.store, stream_interval_ms=2000)
    context.odtemp_device = _ODTempDevice([f"~F{period}"])


@given("автоматическая выдача ODTEMP выключена")
def step_odtemp_stream_off(context):
    _core(context)
    context.odtemp = ODTempSource(context.store, stream_interval_ms=2000)
    context.odtemp_device = _ODTempDevice(["~F0", "~F2000"])


@when("источник проверяет автоматическую выдачу")
def step_odtemp_ensure_stream(context):
    context.odtemp_period = context.odtemp._ensure_stream(context.odtemp_device)


@then("используется период {period:d} мс")
def step_odtemp_period(context, period):
    assert context.odtemp_period == period


@then("команда включения потока не отправлялась")
def step_odtemp_no_enable(context):
    assert context.odtemp_device.writes == ["~F"]


@then('отправлена команда "{command}"')
def step_odtemp_command(context, command):
    assert command in context.odtemp_device.writes


@when("с последнего измерения прошло {seconds:d} секунд")
def step_odtemp_elapsed(context, seconds):
    context.odtemp_stale = is_measurement_stale(100.0, 100.0 + seconds)


@then("измерение ODTEMP считается устаревшим")
def step_odtemp_stale(context):
    assert context.odtemp_stale is True


@given("создано тестовое веб-приложение")
def step_web(context):
    context.runtime = Runtime()
    context.app = create_app(context.runtime)
    context.app.config.update(TESTING=True)
    context.client = context.app.test_client()


@when("запрашивают главную страницу")
def step_get_root(context):
    context.response = context.client.get("/")


@then("HTTP статус равен {status:d}")
def step_status(context, status):
    assert context.response.status_code == status


@then("страница сообщает об отсутствии данных")
def step_no_data_message(context):
    assert "Данные заездов не найдены" in context.response.get_data(as_text=True)


@given("в хранилище есть гонка с кругами")
def step_web_race(context):
    data = normalize_race({"laps": {7: [32.0, 31.0]}}, "laps.yaml")
    context.runtime.store.set("current_race", data, source="test")


@given("в хранилище температура равна {value:d}")
def step_web_temp(context, value):
    context.runtime.store.set("temperature", value, source="test")


@when("запрашивают API данных")
def step_api(context):
    context.response = context.client.get("/api/data")


@then("API содержит температуру {value:d}")
def step_api_temp(context, value):
    assert context.response.get_json()["temperature"] == value


@when('устанавливают вид "{view}"')
def step_set_view(context, view):
    context.response = context.client.put("/state", json={"view": view})


@then('текущий вид равен "{view}"')
def step_current_view(context, view):
    assert context.client.get("/state").get_json()["view"] == view


@when("отправляют некорректный запрос вида")
def step_bad_view(context):
    context.response = context.client.put("/state", json={"view": 12})


@when("проверяют обновления после ревизии {revision:d}")
def step_updates(context, revision):
    context.response = context.client.get(f"/check-updates?revision={revision}")


@then("обновление обнаружено")
def step_updated(context):
    body = context.response.get_json()
    assert body["updated"] is True and body["revision"] > 0


@when("запрашивают страницу управления")
def step_control(context):
    context.response = context.client.get("/set_state")


@when("загружают конфигурацию экранов")
def step_load_screens(context):
    context.screen_configs = load_screen_configs("screens.yaml")


@then("описано {count:d} экранных окна")
def step_screen_count(context, count):
    assert len(context.screen_configs) == count


@then("каждое окно имеет размер {width:d} на {height:d}")
def step_screen_sizes(context, width, height):
    assert all(item.width == width and item.height == height for item in context.screen_configs)


@then('координаты окон равны "{coordinates}"')
def step_screen_coordinates(context, coordinates):
    actual = ",".join(f"{item.x}:{item.y}" for item in context.screen_configs)
    assert actual == coordinates


@given("создан планировщик содержимого экрана")
def step_planner(context):
    _core(context)
    context.screen_config = ScreenConfig(
        "test", 0, 0, record_category="men",
        messages=("Время работы\n12–21", "10 минут\n1000 ₽"),
        top_interval=5, message_interval=4,
    )
    context.planner = ContentPlanner(context.store)


@given('установлены время "{clock}" и температура {temperature:g}')
def step_screen_values(context, clock, temperature):
    moment = datetime.strptime(clock, "%H:%M:%S")
    context.store.set("current_time", moment, source="test")
    context.store.set("temperature", temperature, source="test")


@when("формируется первая верхняя страница")
def step_first_top(context):
    context.frame = context.planner.frame(context.screen_config, 0)


@then('верх содержит "{text}"')
def step_top_contains(context, text):
    assert text in context.frame.top_lines, context.frame.top_lines


@given("установлены дневные рекорды всех категорий")
def step_records(context):
    context.store.set("records_day", {
        "children": {"name": "Маша", "time": 31.2},
        "women": {"name": "Анна", "time": 29.8},
        "men": {"name": "Иван", "time": 28.4},
    }, source="test")


@when('формируется страница рекорда для категории "{category}"')
def step_record_page(context, category):
    config = ScreenConfig("test", 0, 0, record_category=category, top_interval=5)
    context.frame = context.planner.frame(config, 5)


@when("формируются две последовательные нижние страницы")
def step_bottom_pages(context):
    context.first_bottom = context.planner.frame(context.screen_config, 0).bottom_lines
    context.second_bottom = context.planner.frame(context.screen_config, 4).bottom_lines


@then("нижние тексты различаются")
def step_bottom_different(context):
    assert context.first_bottom != context.second_bottom


@given("создан планировщик с наследуемым GIF-фоном")
def step_inherited_gif(context):
    _core(context)
    context.screen_config = ScreenConfig(
        "test", 0, 0, background_time_temperature="clock.gif",
        background_daily_record="previous",
    )
    context.planner = ContentPlanner(context.store)


@given("создан планировщик с двумя GIF-фонами")
def step_two_gifs(context):
    _core(context)
    context.screen_config = ScreenConfig(
        "test", 0, 0, background_time_temperature="clock.gif",
        background_daily_record="record.gif",
    )
    context.planner = ContentPlanner(context.store)


@when("формируется верхняя страница рекорда")
def step_top_record(context):
    context.frame = context.planner.frame(context.screen_config, 5)


@then('выбран GIF "{filename}"')
def step_selected_gif(context, filename):
    assert context.frame.background == filename


@given("подготовлена экранная страница для Pygame")
def step_pygame_frame(context):
    context.screen_config = ScreenConfig("test", 0, 0)
    context.frame = ScreenFrame(
        ("12:00:00", "Температура: 20 °C"), ("10 минут", "1000 ₽"), "waves"
    )


@when("Pygame отрисовывает кадр {width:d} на {height:d}")
def step_pygame_render(context, width, height):
    import pygame

    pygame.init()
    context.pygame_surface = pygame.Surface((width, height))
    ScreenDisplay._draw(
        context.pygame_surface, context.screen_config, context.frame, 1.0
    )
    pygame.quit()


@then("создана поверхность Pygame размером {width:d} на {height:d}")
def step_pygame_size(context, width, height):
    assert context.pygame_surface.get_size() == (width, height)


@when("создают основной runtime приложения")
def step_main_runtime(context):
    from run import build_runtime

    context.main_runtime = build_runtime()


@then("runtime содержит Pygame экранный модуль")
def step_runtime_has_screens(context):
    assert any(isinstance(module, ScreenDisplay) for module in context.main_runtime._modules)
