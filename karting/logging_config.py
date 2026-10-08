from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(
    level: str = "INFO", filename: str = "karting.log",
    max_bytes: int = 5 * 1024 * 1024, backup_count: int = 3,
) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(threadName)s %(name)s: %(message)s"
    )
    root = logging.getLogger()
    root.setLevel(numeric_level)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    if filename:
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        output = RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        output.setFormatter(formatter)
        root.addHandler(output)

    # Request details are emitted by app.routes at DEBUG. Keep INFO operational.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
