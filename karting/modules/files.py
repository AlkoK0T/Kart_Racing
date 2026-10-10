from __future__ import annotations

import logging
from pathlib import Path
from threading import Event as ThreadEvent, Thread
from karting.core import DataStore
from karting.domain import ValidationError, normalize_race  # compatibility re-export
from karting.ingestion import DataIngestor, parse_yaml

log = logging.getLogger(__name__)


class FileSource:
    """Loads the newest YAML file from each category directory.

    Layout: ``data/<category>/*.yaml``. For migration, YAML files directly in
    ``data`` are treated as ``current_race`` and the newest one wins.
    """

    name = "files"

    def __init__(self, store: DataStore, root: str | Path, interval: float = 1.0,
                 *, ingestor: DataIngestor | None = None) -> None:
        self._store = store
        self._ingestor = ingestor if ingestor is not None else DataIngestor(store)
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
                limits = self._ingestor.registry.limits
                with newest.open('rb') as stream:
                    value = parse_yaml(stream.read(limits.max_bytes + 1), limits)
                log.debug("file received category=%s path=%s value=%r", category, newest, value)
                self._ingestor.ingest(category, value, source=f"file:{newest}", legacy=True, filename=newest.name)
                self._seen[category] = marker
                log.info("file category updated category=%s path=%s", category, newest)
            except (OSError, ValidationError) as exc:
                log.warning("Cannot load %s: %s", newest, exc)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self._interval + 1)
        log.info("file source stopped root=%s", self._root)
