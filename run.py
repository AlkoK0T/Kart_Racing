"""Composition root: participation is controlled explicitly in this file."""
from __future__ import annotations

import atexit
import logging

from app import create_app
from config import Config
from karting import Runtime
from karting.ingestion import DataIngestor
from karting.logging_config import configure_logging
from karting.modules import (
    ClockSource, FileSource, ODTempSource, ScreenDisplay, SerialSource, TcpJsonSource,
)

configure_logging(
    Config.LOG_LEVEL, Config.LOG_FILE, Config.LOG_MAX_BYTES, Config.LOG_BACKUP_COUNT
)
log = logging.getLogger(__name__)


def build_runtime() -> Runtime:
    runtime = Runtime()
    ingestion = DataIngestor(runtime.store)

    # Data acquisition modules. Comment/remove a line to exclude a module.
    runtime.add(FileSource(runtime.store, Config.DATA_FOLDER, Config.FILE_POLL_INTERVAL, ingestor=ingestion))
    runtime.add(ClockSource(runtime.store))
    if Config.TCP_ENABLED:
        runtime.add(TcpJsonSource(runtime.store, Config.TCP_HOST, Config.TCP_PORT, ingestor=ingestion))
    if Config.SERIAL_PORT:
        runtime.add(SerialSource(runtime.store, Config.SERIAL_PORT, Config.SERIAL_BAUDRATE, ingestor=ingestion))
    if Config.ODTEMP_ENABLED:
        runtime.add(ODTempSource(
            runtime.store, Config.ODTEMP_PORT, Config.ODTEMP_VID, Config.ODTEMP_PID,
            stream_interval_ms=Config.ODTEMP_STREAM_INTERVAL_MS,
            watchdog_timeout=Config.ODTEMP_WATCHDOG_TIMEOUT,
            reconnect_interval=Config.ODTEMP_RECONNECT_INTERVAL,
        ))

    # Display modules consume the same store and events as the web application.
    if Config.DISPLAY_ENABLED:
        runtime.add(ScreenDisplay(runtime.store, runtime.bus, Config.DISPLAY_CONFIG))

    # Flask is the first display. Screen compositors subscribe to the same bus.
    return runtime


runtime = build_runtime()
app = create_app(runtime)


def run_server(runtime, app):
    try:
        runtime.start()
        app.run(host='0.0.0.0', port=Config.PORT, debug=Config.DEBUG, use_reloader=False)
    except BaseException:
        try:
            runtime.close()
        except Exception:
            log.exception('cleanup failed while handling application error')
        raise
    else:
        runtime.close()


if __name__ == "__main__":
    log.info(
        "Kart Racing starting web=0.0.0.0:%d data=%s displays=%s tcp=%s odtemp=%s",
        Config.PORT, Config.DATA_FOLDER, Config.DISPLAY_ENABLED,
        Config.TCP_ENABLED, Config.ODTEMP_ENABLED,
    )
    atexit.register(runtime.close)
    log.info("web application listening address=0.0.0.0:%d", Config.PORT)
    run_server(runtime, app)
