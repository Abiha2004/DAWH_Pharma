"""BATCH INGESTION: landing-folder CSV file  ->  bronze (raw.*) table.

Measured for every file (written to ops.batch_load_log):
  * Batch Load Duration (s)  = end_ts - start_ts  (read + parse + COPY insert)
  * Rows Processed per Batch = len(df) after parsing
  * Malformed rows           = data lines in the file that pandas could not parse
  * Status                   = SUCCESS / FAILED  -> Batch Success Rate (%)
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.common import db
from src.common.config import path, source_for_file

log = logging.getLogger(__name__)
REFRESH_RE = re.compile(r"__(R\d+)")
CHUNK_ROWS = 50_000          # rows per COPY - keeps the worker's memory flat


def refresh_of(file_name: str) -> str:
    """'05_pharmacy_sales_data__R1.csv' -> 'R1'; original files -> 'R0' (initial load)."""
    m = REFRESH_RE.search(file_name)
    return m.group(1) if m else "R0"


def file_hash(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def register_file(p: Path, detected_by: str) -> tuple[int, str] | None:
    """Insert into ops.file_registry. Returns (file_id, status) or None if already loaded."""
    src = source_for_file(p.name)
    digest = file_hash(p)
    existing = db.fetch_df("SELECT file_id, status FROM ops.file_registry WHERE file_hash = %s", [digest])
    if not existing.empty:
        if existing.iat[0, 1] == "LOADED":
            return None
        return int(existing.iat[0, 0]), str(existing.iat[0, 1])
    db.execute(
        "INSERT INTO ops.file_registry (file_name, file_hash, source_domain, refresh_id, size_bytes, detected_by) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        [p.name, digest, src[0] if src else None, refresh_of(p.name), p.stat().st_size, detected_by],
    )
    fid = db.fetch_scalar("SELECT file_id FROM ops.file_registry WHERE file_hash = %s", [digest])
    return int(fid), "DETECTED"


def _count_data_lines(p: Path) -> int:
    with open(p, "rb") as fh:
        n = sum(chunk.count(b"\n") for chunk in iter(lambda: fh.read(1 << 20), b""))
    if p.stat().st_size:
        with open(p, "rb") as fh:      # file may not end with a newline
            fh.seek(-1, 2)
            if fh.read(1) != b"\n":
                n += 1
    return max(n - 1, 0)               # minus header


def ingest_file(file_path: str | Path, detected_by: str = "airflow_scan", airflow_run_id: str | None = None) -> dict:
    """Load one landed CSV into its bronze table.

    Concurrency-safe: the file is first *claimed* with an atomic rename into
    landing/.processing/, so the watcher-triggered run and the scheduled scan can
    never load the same file twice. Idempotent on content (SHA-256 registry).
    """
    p = Path(file_path)
    if not p.is_absolute():
        p = path("landing") / p.name
    src = source_for_file(p.name)
    if src is None:
        raise ValueError(f"Unrecognised source file: {p.name}")
    source_name, meta = src
    claim_dir = path("landing") / ".processing"
    claim_dir.mkdir(exist_ok=True)
    try:
        p = p.rename(claim_dir / p.name)          # atomic claim
    except FileNotFoundError:
        return {"file": p.name, "status": "ALREADY_CLAIMED"}
    reg = register_file(p, detected_by)
    if reg is None:
        log.info("Skipping %s - identical file already loaded", p.name)
        db.execute(
            "INSERT INTO ops.batch_load_log (refresh_id, source_domain, file_name, file_size_mb, start_ts, end_ts, "
            "duration_s, rows_read, rows_loaded, status, error_message, airflow_run_id) "
            "VALUES (%s, %s, %s, %s, now(), now(), 0, 0, 0, 'SKIPPED_DUPLICATE', "
            "'identical SHA-256 already loaded - idempotent skip', %s)",
            [refresh_of(p.name), source_name, p.name, round(p.stat().st_size / 1_048_576, 3), airflow_run_id])
        shutil.move(str(p), path("archive") / p.name)
        return {"file": p.name, "status": "SKIPPED_DUPLICATE"}
    file_id, _ = reg
    refresh_id = refresh_of(p.name)
    size_mb = round(p.stat().st_size / 1_048_576, 3)

    start = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    db.execute(
        "INSERT INTO ops.batch_load_log (refresh_id, source_domain, file_name, file_size_mb, start_ts, status, airflow_run_id) "
        "VALUES (%s, %s, %s, %s, %s, 'RUNNING', %s)",
        [refresh_id, source_name, p.name, size_mb, start, airflow_run_id],
    )
    batch_id = int(db.fetch_scalar(
        "SELECT max(batch_id) FROM ops.batch_load_log WHERE file_name = %s AND status = 'RUNNING'", [p.name]))

    try:
        data_lines = _count_data_lines(p)
        # every column as text: bronze keeps the data exactly as received
        # Stream the file in chunks so memory stays small even for the 500k-row / 220 MB sales file
        rows_read = 0
        for chunk in pd.read_csv(p, dtype=str, keep_default_na=False, on_bad_lines="skip", engine="c",
                                 chunksize=CHUNK_ROWS):
            n = len(chunk)
            chunk["_batch_id"] = batch_id
            chunk["_source_file"] = p.name
            chunk["_row_num"] = range(rows_read + 1, rows_read + n + 1)
            chunk["_ingest_mode"] = "batch"
            buf = io.StringIO()
            chunk.to_csv(buf, index=False)
            db.copy_csv(meta["table"], chunk.columns, buf.getvalue())
            rows_read += n
            del chunk, buf
        malformed = max(data_lines - rows_read, 0)

        dur = time.perf_counter() - t0
        db.execute(
            "UPDATE ops.batch_load_log SET end_ts = %s, duration_s = %s, rows_read = %s, rows_loaded = %s, "
            "rows_malformed = %s, rows_per_sec = %s, status = 'SUCCESS' WHERE batch_id = %s",
            [datetime.now(timezone.utc), round(dur, 3), rows_read + malformed, rows_read, malformed,
             round(rows_read / dur, 1) if dur else None, batch_id],
        )
        db.execute("UPDATE ops.file_registry SET status = 'LOADED', batch_id = %s WHERE file_id = %s", [batch_id, file_id])
        shutil.move(str(p), path("archive") / p.name)
        log.info("Loaded %s: %s rows (%s malformed) in %.2fs", p.name, rows_read, malformed, dur)
        return {"file": p.name, "batch_id": batch_id, "rows": rows_read, "malformed": malformed,
                "duration_s": round(dur, 3), "status": "SUCCESS", "source": source_name}
    except Exception as exc:  # noqa: BLE001 - every failure must be recorded
        dur = time.perf_counter() - t0
        db.execute(f"DELETE FROM {meta['table']} WHERE _batch_id = %s", [batch_id])   # no half-loaded batches
        db.execute(
            "UPDATE ops.batch_load_log SET end_ts = %s, duration_s = %s, status = 'FAILED', error_message = %s "
            "WHERE batch_id = %s",
            [datetime.now(timezone.utc), round(dur, 3), str(exc)[:1000], batch_id],
        )
        db.execute("UPDATE ops.file_registry SET status = 'FAILED' WHERE file_id = %s", [file_id])
        shutil.move(str(p), path("rejected") / p.name)
        log.exception("Failed to ingest %s", p.name)
        raise


def scan_landing() -> list[Path]:
    """Files currently waiting in the landing folder, ordered so master data loads first."""
    return sorted(path("landing").glob("*.csv"), key=lambda f: f.name)


def ingest_all_landed(detected_by: str = "airflow_scan", airflow_run_id: str | None = None) -> list[dict]:
    results = []
    for f in scan_landing():
        try:
            results.append(ingest_file(f, detected_by, airflow_run_id))
        except Exception as exc:  # keep going; failure is logged in ops.batch_load_log
            results.append({"file": f.name, "status": "FAILED", "error": str(exc)})
    return results
