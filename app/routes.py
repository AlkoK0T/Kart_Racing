from __future__ import annotations

from datetime import datetime
import logging
from threading import RLock

from flask import Blueprint, current_app, jsonify, render_template, request, url_for

from karting import Event, Runtime

log = logging.getLogger(__name__)

web = Blueprint("web", __name__)
class WebState:
    def __init__(self):
        self.lock = RLock()
        self.view = 'style1'


def _web_state():
    return current_app.extensions['karting_web_state']


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
    return current_app.extensions["karting_runtime"]


@web.route("/")
def show_results():
    race_item, race_revision = _runtime().store.category_snapshot('current_race')
    web_state = _web_state()
    with web_state.lock:
        subview = web_state.view
    race_data = race_item.value if race_item is not None else None
    # Use the revision of the rendered snapshot, not a later store read.
    polling = dict(
        revision=race_revision,
        view=subview,
        intervalMs=max(100, current_app.config["UPDATE_TIME"]),
        updatesUrl=url_for("web.check_updates"), stateUrl=url_for("web.state"),
    )
    if race_data is None:
        return render_template("results.html", current_race=None, best_laps=[], top_pilots=[],
                               last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                               current_view="laps", current_subview=subview,
                               web_poll=polling,
                               error_message="Данные заездов не найдены")
    if isinstance(race_data, list):
        return render_template("laps_champ.html", tables=race_data, web_poll=polling)
    if not isinstance(race_data, dict):
        return jsonify({"current_race": race_data})

    view = race_data.get("style", "race")
    context = dict(
        current_race=race_data.get("current_race"),
        best_laps=race_data.get("best_laps", []), top_pilots=race_data.get("top_pilots", []),
        last_updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        current_view=view, current_subview=subview, data=race_data, error_message=None,
        web_poll=polling,
    )
    if view == "laps":
        template = "results_alt.html" if subview == "style2" else "results.html"
    elif view == "race":
        template = "v_race.html"
        context["header"] = "Гонка"
    else:
        template = f"v_{view}.html"
    return render_template(template, **context)


@web.route("/api/data")
def all_data():
    items, _ = _runtime().store.snapshot_with_revision()
    snapshot = {key: item.value for key, item in items.items()}
    for key in ('temperature', 'humidity'):
        item = items.get(key)
        if item is not None and item.quality != 'good':
            snapshot[key] = None
    log.debug("HTTP API snapshot=%r", snapshot)
    return jsonify(snapshot)


@web.route('/api/health')
def health():
    runtime = _runtime()
    items, _ = runtime.store.snapshot_with_revision()
    readings = {}
    for key in ('temperature', 'humidity'):
        item = items.get(key)
        if item is not None:
            readings[key] = {'last_value': item.value, 'quality': item.quality,
                             'received_at': item.updated_at.isoformat(),
                             'source': item.source, 'revision': item.revision}
    return jsonify({'sources': {key: item.value for key, item in items.items()
                                if key.startswith('source.')},
                    'readings': readings, 'subscribers': runtime.bus.subscriber_health()})


@web.route("/check-updates")
def check_updates():
    requested = request.args.get("revision", 0, type=int)
    store = _runtime().store
    category = request.args.get("category")
    if category not in (None, "current_race"):
        return jsonify({"error": "category must be current_race or omitted"}), 400

    latest = store.wait_for_revision(requested, category=category,
                                    timeout=current_app.config.get('LONG_POLL_TIMEOUT', 30))
    return jsonify({"updated": latest > requested, "revision": latest})


@web.route("/state", methods=["GET", "PUT"])
def state():
    web_state = _web_state()
    if request.method == "GET":
        with web_state.lock:
            return jsonify({"view": web_state.view})
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("view") not in ("style1", "style2"):
        return jsonify({"error": "JSON field 'view' must be style1 or style2"}), 400
    with web_state.lock:
        web_state.view = data['view']
        view = web_state.view
    log.info("web display view changed view=%s", view)
    _runtime().bus.publish(Event("display.view", view, "web"))
    return jsonify({"success": True, "view": view})


@web.route("/set_state")
def set_state():
    return render_template("set_state.html")
