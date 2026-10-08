from __future__ import annotations

import logging
from flask import Flask

from karting.core import Runtime

log = logging.getLogger(__name__)


def create_app(runtime: Runtime | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object("config.Config")
    app.extensions["karting_runtime"] = runtime or Runtime()

    from .routes import web
    app.register_blueprint(web)
    log.info("Flask application initialized")
    return app
