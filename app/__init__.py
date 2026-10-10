from __future__ import annotations

import logging
from flask import Flask

from karting.core import Runtime

log = logging.getLogger(__name__)


def create_app(runtime: Runtime | None = None, *, config=None) -> Flask:
    app = Flask(__name__)
    from config import Config
    app.config.from_object(config or Config.from_env())
    app.extensions["karting_runtime"] = runtime or Runtime()

    from .routes import web, WebState
    app.extensions['karting_web_state'] = WebState()
    app.register_blueprint(web)
    log.info("Flask application initialized")
    return app
