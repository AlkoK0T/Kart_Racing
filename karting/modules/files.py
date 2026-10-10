from __future__ import annotations

import logging
from pathlib import Path
from threading import Event as ThreadEvent, Thread
from typing import Any

import yaml

from karting.core import DataStore

log = logging.getLogger(__name__)


class FileSource:
    """Loads the newest YAML file from each category directory.

    Layout: ``data/<category>/*.yaml``. For migration, YAML files directly in
    ``data`` are treated as ``current_race`` and the newest one wins.
    """

    name = "files"

    def __init__(self, store: DataStore, root: str | Path, interval: float = 1.0) -> None:
        self._store = store
        self._root = Path(root)
        self._interval = interval
        self._seen: dict[str, tuple[Path, int]] = {}
        self._stop = ThreadEvent()
        self._thread: Thread | None = None

    def start(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        self.scan()
        self._stop.clear()
        self._thread = Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()
        log.info("file source started root=%s interval=%s", self._root, self._interval)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self.scan()

    def scan(self) -> None:
        log.debug("scanning data root=%s", self._root)
        candidates: dict[str, list[Path]] = {}
        root_files = list(self._root.glob("*.yaml")) + list(self._root.glob("*.yml"))
        if root_files:
            candidates["current_race"] = root_files
        for directory in (path for path in self._root.iterdir() if path.is_dir()):
            files = list(directory.glob("*.yaml")) + list(directory.glob("*.yml"))
            if files:
                candidates[directory.name] = files

        for category, files in candidates.items():
            newest = max(files, key=lambda path: path.stat().st_mtime_ns)
            marker = (newest, newest.stat().st_mtime_ns)
            if self._seen.get(category) == marker:
                log.debug("file unchanged category=%s path=%s", category, newest)
                continue
            try:
                with newest.open(encoding="utf-8", errors="ignore") as stream:
                    value: Any = yaml.safe_load(stream)
                if category == "current_race":
                    value = normalize_race(value, newest.name)
                log.debug("file received category=%s path=%s value=%r", category, newest, value)
                self._store.set(category, value, source=f"file:{newest}")
                self._seen[category] = marker
                log.info("file category updated category=%s path=%s", category, newest)
            except (OSError, yaml.YAMLError) as exc:
                log.warning("Cannot load %s: %s", newest, exc)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self._interval + 1)
        log.info("file source stopped root=%s", self._root)


def normalize_race(data: Any, filename: str = "") -> Any:
    """Add calculated fields used by the legacy web templates."""
    if not isinstance(data, dict) or not isinstance(data.get("laps"), dict):
        return data
    laps = {kart: list(times or []) for kart, times in data["laps"].items()}
    best = {kart: min(times) if times else None for kart, times in laps.items()}
    ranked = sorted((kart for kart in laps if best[kart] is not None), key=lambda kart: best[kart])
    ranks = {kart: position for position, kart in enumerate(ranked, 1)}
    overall_best = min((value for value in best.values() if value is not None), default=None)
    # Keep unclassified participants visible, after all actual results.
    karts = ranked + [kart for kart in laps if best[kart] is None]
    current = {
        "karts": karts, "laps": laps, "best_times": best,
        "avg_times": {kart: sum(times) / len(times) if times else None for kart, times in laps.items()},
        "positions": {kart: ranks.get(kart) for kart in laps},
        "time_deltas": {kart: value - overall_best if value is not None else None for kart, value in best.items()},
        "last_laps": {kart: times[-1] if times else None for kart, times in laps.items()},
        "total_time": {kart: sum(times) if times else None for kart, times in laps.items()},
        "total_laps": {kart: len(times) for kart, times in laps.items()},
        "max_time": max((len(times) for times in laps.values()), default=0),
        "diff": {kart: times[-1] - times[-2] if len(times) > 1 else None for kart, times in laps.items()},
        "type": "current_race",
    }
    style = data.get("style")
    if style not in ("laps", "race"):
        style = "laps" if filename == "laps.yaml" else "race"
    return {**data, "raw": data, "style": style, "current_race": current}
