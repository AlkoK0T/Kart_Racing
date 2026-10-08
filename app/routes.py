from __future__ import annotations

from datetime import datetime
import logging
from threading import Condition, RLock

from flask import Blueprint, current_app, jsonify, render_template, request

from karting import Event, Runtime

log = logging.getLogger(__name__)

web = Blueprint("web", __name__)
_view_lock = RLock()
_current_subview = "style1"
_update = Condition()
_observed_runtimes: set[int] = set()


@web.before_request
def log_request() -> None:
    log.debug(
        "HTTP request method=%s path=%s remote=%s args=%r",
        request.method, request.path, request.remote_addr, dict(request.args),
    )


@web.after_request
def log_response(response):
    log.debug("HTTP response method=%s path=%s status=%s", request.method, request.path, response.status_code)
    return response


def _runtime() -> Runtime:
    runtime: Runtime = current_app.extensions["karting_runtime"]
    identity = id(runtime)
    if identity not in _observed_runtimes:
        runtime.bus.subscribe("data.changed", lambda event: _notify_update())
        _observed_runtimes.add(identity)
    return runtime


def _notify_update() -> None:
    with _update:
        _update.notify_all()


@web.route("/")
def show_results():
    race_data = _runtime().store.get("current_race")
    if race_data is None:
        return render_template("results.html", current_race=None, best_laps=[], top_pilots=[],
                               last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                               current_view="laps", current_subview=_current_subview,
                               error_message="Данные заездов не найдены")
    if isinstance(race_data, list):
        return render_template("laps_champ.html", tables=race_data)
    if not isinstance(race_data, dict):
        return jsonify({"current_race": race_data})

    view = race_data.get("style", "race")
    context = dict(
        current_race=race_data.get("current_race"),
        best_laps=race_data.get("best_laps", []), top_pilots=race_data.get("top_pilots", []),
        last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        current_view=view, current_subview=_current_subview, data=race_data, error_message=None,
    )
    if view == "laps":
        template = "results_alt.html" if _current_subview == "style2" else "results.html"
    elif view == "race":
        template = "v_race.html"
        context["header"] = "Гонка"
    else:
        template = f"v_{view}.html"
    return render_template(template, **context)


@web.route("/api/data")
def all_data():
    snapshot = _runtime().store.snapshot()
    log.debug("HTTP API snapshot=%r", snapshot)
    return jsonify(snapshot)


@web.route("/check-updates")
def check_updates():
    requested = request.args.get("revision", 0, type=int)
    store = _runtime().store
    latest = max((item.revision for key in store.snapshot() if (item := store.item(key))), default=0)
    if latest <= requested:
        with _update:
            _update.wait(timeout=30)
        latest = max((item.revision for key in store.snapshot() if (item := store.item(key))), default=0)
    return jsonify({"updated": latest > requested, "revision": latest})


@web.route("/state", methods=["GET", "PUT"])
def state():
    global _current_subview
    if request.method == "GET":
        with _view_lock:
            return jsonify({"view": _current_subview})
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("view"), str):
        return jsonify({"error": "JSON field 'view' must be a string"}), 400
    with _view_lock:
        _current_subview = data["view"]
    log.info("web display view changed view=%s", _current_subview)
    _runtime().bus.publish(Event("display.view", _current_subview, "web"))
    return jsonify({"success": True, "view": _current_subview})


@web.route("/set_state")
def set_state():
    return render_template("set_state.html")
