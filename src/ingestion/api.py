"""DATA INGESTION API (FastAPI, container `ingest-api`, port 8000).

Endpoints
  GET  /health                     liveness probe
  POST /ingest/file                upload a source CSV -> saved into data/landing/
                                   (the file-watcher then triggers the batch DAG)
  POST /ingest/stream/sales        push a micro-batch of e-commerce sales events
                                   (validated + inserted into bronze, metrics logged)
  GET  /ingest/status              last 20 batch loads and stream micro-batches
Interactive docs are served at http://localhost:8000/docs
"""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

from src.common import db
from src.common.config import path, source_for_file
from src.ingestion.stream_ingest import ingest_events

app = FastAPI(title="Pharma Cold-Chain DWH - Ingestion API", version="1.0")


class StreamBatch(BaseModel):
    batch_id: int
    refresh_id: str
    micro_batch_no: int
    window_s: float | None = None
    events: list


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "db": bool(db.fetch_scalar("SELECT 1"))}


@app.post("/ingest/file", status_code=202)
def ingest_file(file: UploadFile = File(...)) -> dict:
    name = Path(file.filename or "").name
    if not name.lower().endswith(".csv") or source_for_file(name) is None:
        raise HTTPException(400, f"{name!r} is not one of the 7 recognised source files")
    tmp = path("landing") / f".{name}.part"
    with open(tmp, "wb") as out:
        shutil.copyfileobj(file.file, out)
    tmp.rename(path("landing") / name)          # atomic rename -> watcher sees a complete file
    return {"accepted": name, "next": "file-watcher will trigger sdi_01_batch_ingestion"}


@app.post("/ingest/stream/sales")
def ingest_stream(batch: StreamBatch) -> dict:
    return ingest_events(batch.events, batch.batch_id, batch.refresh_id, batch.micro_batch_no,
                         window_s=batch.window_s)


@app.get("/ingest/status")
def status() -> dict:
    loads = db.fetch_df("SELECT batch_id, refresh_id, source_domain, file_name, rows_loaded, duration_s, status "
                        "FROM ops.batch_load_log ORDER BY batch_id DESC LIMIT 20")
    stream = db.fetch_df("SELECT refresh_id, micro_batch_no, events_loaded, events_malformed, throughput_eps, "
                         "avg_latency_s FROM ops.stream_metrics ORDER BY id DESC LIMIT 20")
    return {"batch_loads": loads.astype(str).to_dict("records"), "stream": stream.astype(str).to_dict("records")}
