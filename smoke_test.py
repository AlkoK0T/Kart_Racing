"""Explicit opt-in native SDL, physical ODTEMP, or real Chromium smoke tests."""
from __future__ import annotations
import argparse
import json
import logging
from pathlib import Path
import sys
from threading import Event, Thread
import time
import tracemalloc

ROOT = Path(__file__).resolve().parent


def native_smoke(args):
    from karting import Runtime
    from karting.modules import ClockSource, ODTempSource, ScreenDisplay
    runtime = Runtime()
    screen = sensor = None
    if args.sdl:
        runtime.add(ClockSource(runtime.store))
        screen = ScreenDisplay(runtime.store, runtime.bus, args.screens)
        runtime.add(screen)
    if args.odtemp:
        sensor = ODTempSource(runtime.store, serial_number=args.serial_number)
        runtime.add(sensor)
    start, cpu_start = time.monotonic(), time.process_time()
    tracemalloc.start()
    received = Event()
    unsubscribe = runtime.bus.subscribe('data.temperature',
        lambda event: received.set() if event.payload.quality == 'good' else None)
    try:
        runtime.start()
        while time.monotonic() - start < args.seconds:
            if screen and screen.health['state'] != 'running':
                raise RuntimeError('SDL worker stopped: ' + str(screen.health))
            if sensor and runtime.store.get('source.odtemp', {}).get('state') == 'failed':
                raise RuntimeError('ODTEMP worker failed')
            Event().wait(0.1)
        if sensor and (not received.is_set() or runtime.store.item('temperature').quality != 'good'):
            raise RuntimeError('No current physical ODTEMP measurement by end of smoke test')
        elapsed = time.monotonic() - start
        print(json.dumps({'elapsed_seconds': elapsed, 'cpu_seconds': time.process_time() - cpu_start,
                          'python_traced_peak_bytes': tracemalloc.get_traced_memory()[1],
                          'health': runtime.health_snapshot()}, ensure_ascii=False, default=str))
    finally:
        try:
            unsubscribe()
            runtime.close()
        finally:
            tracemalloc.stop()
    if screen and screen._thread is not None:
        raise RuntimeError('SDL worker not joined')
    if sensor and sensor._thread is not None:
        raise RuntimeError('ODTEMP worker not joined')
    print('Native cleanup complete')


def browser_smoke():
    # Optional dependency: intentionally never installed by the ordinary test runner.
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    from app import create_app
    from config import Config
    from karting import Runtime
    from karting.modules.files import normalize_race
    runtime = Runtime()
    race = normalize_race({'laps': {'7': [32, 31]}}, 'laps.yaml')
    runtime.store.set('current_race', race)
    app = create_app(runtime, config=Config(DISPLAY_ENABLED=False, ODTEMP_ENABLED=False, TCP_ENABLED=False))
    app.config.update(UPDATE_TIME=100, LONG_POLL_TIMEOUT=0.05)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    worker = Thread(target=server.serve_forever, name='browser-smoke-server', daemon=True)
    worker.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                errors, navigations = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('framenavigated', lambda frame: navigations.append(frame.url)
                        if frame == page.main_frame else None)
                base = f'http://127.0.0.1:{server.server_port}'
                page.goto(base, wait_until='domcontentloaded')
                page.wait_for_function('!!window.kartingPolling')
                initial = len(navigations)
                runtime.store.set('temperature', 23)
                page.wait_for_timeout(600)
                assert len(navigations) == initial, 'Sensor update reloaded race page'
                with page.expect_navigation(wait_until='domcontentloaded', timeout=10000):
                    response = page.request.put(base + '/state', data={'view': 'style2'})
                    assert response.ok
                page.wait_for_timeout(500)
                assert len(navigations) == initial + 1, 'Duplicate view reload'
                with page.expect_navigation(wait_until='domcontentloaded', timeout=10000):
                    runtime.store.set('current_race', race)
                page.wait_for_timeout(500)
                assert len(navigations) == initial + 2, 'Duplicate race reload'
                assert not errors, errors
                print('Real browser smoke passed: polling, sensor isolation, view/race reload, no JS errors')
            finally:
                browser.close()
    finally:
        server.shutdown(); server.server_close(); worker.join(3); runtime.close()
    if worker.is_alive():
        raise RuntimeError('Browser smoke web server not joined')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdl', action='store_true', help='open three actual configured windows')
    parser.add_argument('--odtemp', action='store_true', help='initialize and read actual USB thermometer')
    parser.add_argument('--browser', action='store_true', help='launch installed Playwright Chromium')
    parser.add_argument('--serial-number', default='')
    parser.add_argument('--seconds', type=int, default=10)
    parser.add_argument('--screens', type=Path, default=ROOT / 'screens.yaml')
    parser.add_argument('--log-level', choices=('INFO', 'DEBUG'), default='INFO')
    args = parser.parse_args(argv)
    if not (args.sdl or args.odtemp or args.browser):
        parser.error('select --sdl, --odtemp or --browser explicitly')
    if args.odtemp and not args.serial_number:
        parser.error('--odtemp requires --serial-number to target a specific device')
    if not 1 <= args.seconds <= 3600:
        parser.error('--seconds must be between 1 and 3600')
    from karting.logging_config import configure_logging
    configure_logging(args.log_level, filename='')
    try:
        if args.sdl or args.odtemp:
            native_smoke(args)
        if args.browser:
            browser_smoke()
    except (Exception, KeyboardInterrupt) as exc:
        logging.getLogger(__name__).exception('Smoke failed: %s', exc)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
