"""Composition root: participation is controlled explicitly in this file."""
from __future__ import annotations

import atexit
import logging
import sys

from app import create_app
from config import Config
from karting import Runtime
from karting.ingestion import DataIngestor
from karting.logging_config import configure_logging
from karting.modules import (
    ClockSource, FileSource, ODTempSource, ScreenDisplay, SerialSource, TcpJsonSource,
)

log = logging.getLogger(__name__)


def build_runtime(config=None) -> Runtime:
    config = config or Config.from_env()
    runtime = Runtime()
    ingestion = DataIngestor(runtime.store)

    # Data acquisition modules. Comment/remove a line to exclude a module.
    runtime.add(FileSource(runtime.store, config.DATA_FOLDER, config.FILE_POLL_INTERVAL, ingestor=ingestion))
    runtime.add(ClockSource(runtime.store))
    if config.TCP_ENABLED:
        runtime.add(TcpJsonSource(runtime.store, config.TCP_HOST, config.TCP_PORT, ingestor=ingestion,
                                 max_clients=config.TCP_MAX_CLIENTS, client_timeout=config.TCP_CLIENT_TIMEOUT,
                                 frame_timeout=config.TCP_FRAME_TIMEOUT))
    if config.SERIAL_PORT or config.SERIAL_VID is not None:
        runtime.add(SerialSource(runtime.store, config.SERIAL_PORT, config.SERIAL_BAUDRATE, ingestor=ingestion,
                                 reconnect_interval=config.SERIAL_RECONNECT_INTERVAL,
                                 vid=config.SERIAL_VID, pid=config.SERIAL_PID, serial_number=config.SERIAL_NUMBER))
    if config.ODTEMP_ENABLED:
        runtime.add(ODTempSource(
            runtime.store, config.ODTEMP_PORT, config.ODTEMP_VID, config.ODTEMP_PID,
            stream_interval_ms=config.ODTEMP_STREAM_INTERVAL_MS,
            watchdog_timeout=config.ODTEMP_WATCHDOG_TIMEOUT,
            reconnect_interval=config.ODTEMP_RECONNECT_INTERVAL,
            serial_number=config.ODTEMP_SERIAL_NUMBER,
        ))

    # Display modules consume the same store and events as the web application.
    if config.DISPLAY_ENABLED:
        runtime.add(ScreenDisplay(runtime.store, runtime.bus, config.DISPLAY_CONFIG, timezone=config.CENTRE_TIMEZONE))

    # Flask is the first display. Screen compositors subscribe to the same bus.
    return runtime


def create_application(config=None):
    config = config or Config.from_env()
    runtime = build_runtime(config)
    return runtime, create_app(runtime, config=config)


def create_web_app():
    return create_application()[1]


def run_server(runtime, app):
    try:
        runtime.start()
        app.run(host='0.0.0.0', port=app.config['PORT'], debug=app.config['DEBUG'], use_reloader=False)
    except BaseException:
        try:
            runtime.close()
        except Exception:
            log.exception('cleanup failed while handling application error')
        raise
    else:
        runtime.close()


def main():
    try:
        config = Config.from_env()
        runtime, app = create_application(config)
    except (ValueError, OSError) as exc:
        print(f'Configuration error: {exc}', file=sys.stderr)
        return 2
    configure_logging(config.LOG_LEVEL, config.LOG_FILE, config.LOG_MAX_BYTES, config.LOG_BACKUP_COUNT)
    log.info(
        "Kart Racing starting web=0.0.0.0:%d data=%s displays=%s tcp=%s odtemp=%s",
        config.PORT, config.DATA_FOLDER, config.DISPLAY_ENABLED,
        config.TCP_ENABLED, config.ODTEMP_ENABLED,
    )
    atexit.register(runtime.close)
    log.info("web application listening address=0.0.0.0:%d", config.PORT)
    run_server(runtime, app)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
