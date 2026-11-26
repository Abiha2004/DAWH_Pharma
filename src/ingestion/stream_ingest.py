"""STREAMING INGESTION: e-commerce sales events -> bronze raw.pharmacy_sales (mode = 'stream').

A simulator replays the generated event file (data/stream/*.jsonl) as a live
stream of micro-batches. Each event is stamped with the time it was produced
(_event_created_at); the database stamps clock_timestamp() on insert, so

    End-to-End Latency (s) = _ingested_at - _event_created_at
    Throughput (events/s)   = events loaded / micro-batch wall time
    Malformed / dropped     = events failing schema or null-key checks (try/except counter)

All three are written per micro-batch to ops.stream_metrics.
The same `ingest_events` function is used by the REST API (services/ingest_api)
and by the Airflow streaming DAG, so both paths are measured identically.
"""
from __future__ import annotations

import io
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.common import db
from src.common.config import cfg, path

log = logging.getLogger(__name__)
STREAM_NAME = "ecommerce_sales_events"
REQUIRED_KEYS = ("SalesTransactionID", "SalesDate", "MedicineID", "QuantitySoldPacks", "UnitRetailPriceUSD")


def _raw_columns() -> list[str]:
    df = db.fetch_df(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'raw' AND table_name = 'pharmacy_sales' AND column_name NOT LIKE '\\_%%' "
        "ORDER BY ordinal_position")
    return df["column_name"].tolist()


def open_stream_batch(refresh_id: str, airflow_run_id: str | None = None) -> int:
    """Every stream run gets its own batch id so silver/gold can load it incrementally."""
    db.execute(
        "INSERT INTO ops.batch_load_log (refresh_id, source_domain, file_name, start_ts, status, ingest_mode, airflow_run_id) "
        "VALUES (%s, 'sales', %s, now(), 'RUNNING', 'stream', %s)",
        [refresh_id, f"stream:{STREAM_NAME}:{refresh_id}", airflow_run_id])
    return int(db.fetch_scalar(
        "SELECT max(batch_id) FROM ops.batch_load_log WHERE ingest_mode = 'stream' AND refresh_id = %s", [refresh_id]))


def close_stream_batch(batch_id: int, loaded: int, malformed: int, status: str = "SUCCESS") -> None:
    db.execute(
        "UPDATE ops.batch_load_log SET end_ts = now(), duration_s = extract(epoch FROM now() - start_ts), "
        "rows_read = %s, rows_loaded = %s, rows_malformed = %s, status = %s WHERE batch_id = %s",
        [loaded + malformed, loaded, malformed, status, batch_id])


def validate_event(evt) -> str | None:
    """Return a rejection reason, or None if the event is well-formed."""
    if not isinstance(evt, dict):
        return "not_a_json_object"
    for k in REQUIRED_KEYS:
        if k not in evt:
            return f"missing_field:{k}"
        if evt[k] in (None, ""):
            return f"null_key:{k}"
    return None


def ingest_events(events: list, batch_id: int, refresh_id: str, micro_batch_no: int,
                  columns: list[str] | None = None, airflow_run_id: str | None = None,
                  window_s: float | None = None) -> dict:
    """Validate + insert one micro-batch. `events` items carry '_event_created_at' (ISO time).

    window_s = how long the producer took to emit this micro-batch; throughput is
    measured over the full window (production + insert), not just the insert.
    """
    t0 = time.perf_counter()
    start = datetime.now(timezone.utc)
    columns = columns or _raw_columns()
    good, dead = [], []
    for evt in events:
        try:
            if isinstance(evt, (str, bytes)):
                evt = json.loads(evt)
            reason = validate_event(evt)
            if reason:
                raise ValueError(reason)
            good.append(evt)
        except Exception as exc:  # malformed / dropped counter
            dead.append((str(exc)[:200], json.dumps(evt, default=str)[:2000] if not isinstance(evt, (str, bytes)) else str(evt)[:2000]))

    if good:
        df = pd.DataFrame(good)
        for c in columns:
            if c not in df.columns:
                df[c] = ""
        out = df[columns].astype(str).replace({"nan": "", "None": ""})
        out["_batch_id"] = batch_id
        out["_source_file"] = f"stream:{STREAM_NAME}"
        out["_row_num"] = range(micro_batch_no * 100000, micro_batch_no * 100000 + len(out))
        out["_ingest_mode"] = "stream"
        out["_event_created_at"] = df["_event_created_at"].values
        buf = io.StringIO()
        out.to_csv(buf, index=False)
        db.copy_csv("raw.pharmacy_sales", out.columns, buf.getvalue())

    if dead:
        payload = "\n".join(
            "INSERT INTO ops.stream_dead_letter (refresh_id, stream_name, reason, payload) VALUES ("
            + ", ".join(db._literal(v) for v in (refresh_id, STREAM_NAME, r, p)) + ");" for r, p in dead)
        db.execute(payload)

    elapsed = max(time.perf_counter() - t0 + (window_s or 0.0), 1e-6)
    # latency measured by the database itself
    lat = db.fetch_df(
        "SELECT avg(extract(epoch FROM _ingested_at - _event_created_at)) AS avg_s, "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM _ingested_at - _event_created_at)) AS p95_s, "
        "max(extract(epoch FROM _ingested_at - _event_created_at)) AS max_s "
        "FROM raw.pharmacy_sales WHERE _batch_id = %s AND _row_num >= %s AND _row_num < %s",
        [batch_id, micro_batch_no * 100000, (micro_batch_no + 1) * 100000])
    a, p95, mx = (float(lat.iat[0, i]) if not lat.empty and pd.notna(lat.iat[0, i]) else None for i in range(3))
    metrics = {
        "micro_batch_no": micro_batch_no, "events_received": len(events), "events_loaded": len(good),
        "events_malformed": len(dead), "throughput_eps": round(len(good) / elapsed, 2),
        "avg_latency_s": a, "p95_latency_s": p95, "max_latency_s": mx,
    }
    db.execute(
        "INSERT INTO ops.stream_metrics (refresh_id, stream_name, micro_batch_no, batch_start_ts, batch_end_ts, "
        "events_received, events_loaded, events_malformed, throughput_eps, avg_latency_s, p95_latency_s, max_latency_s, airflow_run_id) "
        "VALUES (%s, %s, %s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s)",
        [refresh_id, STREAM_NAME, micro_batch_no, start, len(events), len(good), len(dead),
         metrics["throughput_eps"], a, p95, mx, airflow_run_id])
    return metrics


def replay_stream(refresh_id: str, airflow_run_id: str | None = None, max_seconds: float | None = None,
                  api_url: str | None = None) -> dict:
    """Replay data/stream/sales_stream__<R>.jsonl as timed micro-batches.

    Events inside a micro-batch are produced one by one over the batch interval
    (each gets its own creation timestamp) and flushed together - that is how
    a real micro-batch consumer behaves, so latency = buffering + insert time.
    """
    scfg = cfg()["streaming"]
    f = path("stream") / f"sales_stream__{refresh_id}.jsonl"
    if not f.exists():
        raise FileNotFoundError(f)
    lines = f.read_text(encoding="utf-8").splitlines()
    size, interval = int(scfg["micro_batch_size"]), float(scfg["micro_batch_interval_s"])
    batch_id = open_stream_batch(refresh_id, airflow_run_id)
    columns = _raw_columns()
    rng = np.random.default_rng(int(cfg()["generator"]["random_seed"]))
    t_start = time.perf_counter()
    loaded = malformed = 0
    n_batches = int(np.ceil(len(lines) / size))
    for b in range(n_batches):
        chunk = lines[b * size:(b + 1) * size]
        # bursty traffic: each window lasts 60-140% of the nominal interval
        window = interval * float(rng.uniform(0.6, 1.4))
        produced, gap = [], window / max(len(chunk), 1)
        w0 = time.perf_counter()
        for line in chunk:
            try:
                evt = json.loads(line)
                if isinstance(evt, dict):
                    evt["_event_created_at"] = datetime.now(timezone.utc).isoformat()
                produced.append(evt)
            except json.JSONDecodeError:
                produced.append(line)            # stays malformed -> dead letter
            time.sleep(gap)
        if api_url:
            import requests
            r = requests.post(f"{api_url}/ingest/stream/sales",
                              json={"batch_id": batch_id, "refresh_id": refresh_id, "micro_batch_no": b,
                                    "window_s": time.perf_counter() - w0, "events": produced}, timeout=60)
            r.raise_for_status()
            m = r.json()
        else:
            m = ingest_events(produced, batch_id, refresh_id, b, columns, airflow_run_id,
                              window_s=time.perf_counter() - w0)
        loaded += m["events_loaded"]
        malformed += m["events_malformed"]
        if max_seconds and time.perf_counter() - t_start > max_seconds:
            log.warning("Stream replay stopped after max_seconds=%s", max_seconds)
            break
    close_stream_batch(batch_id, loaded, malformed)
    total = time.perf_counter() - t_start
    return {"batch_id": batch_id, "events_loaded": loaded, "events_malformed": malformed,
            "micro_batches": b + 1, "duration_s": round(total, 2),
            "overall_throughput_eps": round(loaded / total, 2) if total else None}
