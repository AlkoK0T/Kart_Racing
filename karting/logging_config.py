from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import RLock

_configuration_lock = RLock()


def configure_logging(
    level: str = "INFO", filename: str = "karting.log",
    max_bytes: int = 5 * 1024 * 1024, backup_count: int = 3,
) -> None:
    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        raise ValueError('Unknown logging level: ' + level)
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError('max_bytes must be a positive integer')
    if type(backup_count) is not int or backup_count < 0:
        raise ValueError('backup_count must be a nonnegative integer')
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(threadName)s %(name)s: %(message)s"
    )
    with _configuration_lock:
        # Prepare first: a failed file open must leave the working journal intact.
        handlers = [logging.StreamHandler()]
        try:
            if filename:
                path = Path(filename)
                path.parent.mkdir(parents=True, exist_ok=True)
                handlers.append(RotatingFileHandler(
                    path, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8'))
            for handler in handlers:
                handler.setFormatter(formatter)
                handler._karting_handler = True
        except BaseException:
            for handler in handlers:
                handler.close()
            raise

        root = logging.getLogger()
        previous = [handler for handler in root.handlers
                    if getattr(handler, '_karting_handler', False)]
        for handler in previous:
            root.removeHandler(handler)
        for handler in handlers:
            root.addHandler(handler)
        root.setLevel(numeric_level)
        for handler in previous:
            handler.close()
        # Request details are emitted by app.routes at DEBUG. Keep INFO operational.
        logging.getLogger('werkzeug').setLevel(logging.WARNING)
