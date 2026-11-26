"""AUTOMATED FILE-WATCHER (runs as its own container: `file-watcher`).

Uses the `watchdog` library to receive file-system events on data/landing/.
When a new CSV appears (and its size has stopped changing, i.e. the copy is
complete) the watcher:
  1. registers the file in ops.file_registry (detected_by = 'watchdog')
  2. triggers the Airflow DAG `sdi_01_batch_ingestion` through the Airflow REST
     API, passing the file name in the DAG-run conf.
If Airflow is unreachable the file simply stays in landing; the DAG's own
scheduled scan picks it up later, so no file is ever lost.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import requests
from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver  # polling works on Docker-for-Windows bind mounts

from src.common.config import path
from src.ingestion.batch_loader import register_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s file-watcher: %(message)s")
log = logging.getLogger("file_watcher")

AIRFLOW_URL = os.getenv("AIRFLOW_API_URL", "http://airflow-webserver:8080/api/v1")
AIRFLOW_AUTH = (os.getenv("AIRFLOW_API_USER", "admin"), os.getenv("AIRFLOW_API_PASSWORD", "admin"))
TARGET_DAG = os.getenv("WATCHER_TARGET_DAG", "sdi_01_batch_ingestion")


def wait_until_stable(p: Path, checks: int = 3, pause: float = 1.0) -> bool:
    """A file is 'complete' once its size is unchanged for `checks` consecutive polls."""
    last, same = -1, 0
    for _ in range(120):
        if not p.exists():
            return False
        size = p.stat().st_size
        same = same + 1 if size == last else 0
        if same >= checks:
            return True
        last = size
        time.sleep(pause)
    return False


def trigger_airflow(file_name: str) -> None:
    run_id = f"watchdog__{file_name}__{int(time.time())}"
    try:
        resp = requests.post(f"{AIRFLOW_URL}/dags/{TARGET_DAG}/dagRuns", auth=AIRFLOW_AUTH, timeout=15,
                             json={"dag_run_id": run_id, "conf": {"file": file_name, "detected_by": "watchdog"}})
    except requests.RequestException as exc:
        log.warning("Airflow unreachable (%s) - the 5-minute scheduled scan will load %s", exc.__class__.__name__, file_name)
        return
    if resp.status_code >= 300:
        log.warning("Airflow trigger failed (%s): %s", resp.status_code, resp.text[:300])
    else:
        log.info("Triggered %s for %s", TARGET_DAG, file_name)


class LandingHandler(FileSystemEventHandler):
    def _handle(self, file_path: str) -> None:
        p = Path(file_path)
        # only complete CSVs placed directly in landing/ (ignore .part temp files and
        # the .processing/ claim folder the loader moves files into)
        if p.suffix.lower() != ".csv" or p.name.startswith(".") or p.parent.resolve() != path("landing").resolve():
            return
        log.info("Detected %s", p.name)
        if not wait_until_stable(p):
            log.warning("File %s never became stable; skipping", p.name)
            return
        try:
            if register_file(p, detected_by="watchdog") is None:
                log.info("%s already loaded earlier (same SHA-256) - ignored", p.name)
                return
            trigger_airflow(p.name)
        except Exception:  # noqa: BLE001
            log.exception("Could not process %s", p.name)

    def on_created(self, event):
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._handle(event.dest_path)


def main() -> None:
    landing = path("landing")
    observer = PollingObserver(timeout=2)
    observer.schedule(LandingHandler(), str(landing), recursive=False)
    observer.start()
    log.info("Watching %s", landing)
    try:
        while True:
            time.sleep(1)
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
