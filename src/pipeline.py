"""Pipeline facade: one function per Airflow task.

The DAG files in dags/ only orchestrate; all logic lives in src/. This module
is also usable without Airflow:

    python -m src.pipeline init                 # create schemas/tables
    python -m src.pipeline refresh R0           # initial historical load
    python -m src.pipeline refresh R1 --stream  # generate + load refresh 1
"""
from __future__ import annotations

import argparse
import logging

from src.common import db
from src.common.config import cfg

log = logging.getLogger(__name__)

DDL = ["ddl/00_schemas_and_functions.sql", "ddl/01_ops_observability.sql", "ddl/02_bronze_raw.sql",
       "ddl/03_silver_staging.sql", "ddl/10_gold_sdi_star.sql", "ddl/11_gold_seed.sql"]


def init_database() -> None:
    for f in DDL:
        db.execute_file(f)
    db.execute("ALTER TABLE ops.batch_load_log ADD COLUMN IF NOT EXISTS validated_at TIMESTAMPTZ")


def start_refresh(refresh_id: str) -> None:
    month = cfg()["refreshes"].get(refresh_id, {}).get("month", "history")
    db.execute("INSERT INTO ops.refresh_log (refresh_id, refresh_month) VALUES (%s, %s) "
               "ON CONFLICT (refresh_id) DO UPDATE SET started_at = now(), status = 'RUNNING'",
               [refresh_id, month])


def finish_refresh(refresh_id: str, status: str = "SUCCESS") -> None:
    db.execute("UPDATE ops.refresh_log SET finished_at = now(), status = %s WHERE refresh_id = %s",
               [status, refresh_id])


def refresh_is_complete(refresh_id: str) -> bool:
    """True only when every file of the refresh is loaded, validated and in the gold layer.

    The query-layer DAG runs after *every* gold update (small files arrive first),
    so it must not mark a refresh finished until the big files have flowed through too.
    """
    from src.common.config import path
    if any(path("landing").glob("*.csv")) or any((path("landing") / ".processing").glob("*.csv")):
        return False                                        # files still waiting / being loaded
    pending = int(db.fetch_scalar(
        "SELECT count(*) FROM ops.batch_load_log WHERE refresh_id = %s AND "
        "(status = 'RUNNING' OR (status = 'SUCCESS' AND validated_at IS NULL "
        " AND source_domain IN ('master','manufacturing','warehouse','sales','hospital')))", [refresh_id]) or 0)
    if pending:
        return False
    behind = int(db.fetch_scalar(
        "SELECT count(*) FROM ops.watermark w WHERE w.target_table LIKE 'dwh.fact%%' AND w.last_batch_id < "
        "COALESCE((SELECT max(_batch_id) FROM staging.pharmacy_sale), 0) AND w.target_table = 'dwh.fact_pharmacy_sales'") or 0)
    return behind == 0


def generate(refresh_id: str) -> list[dict]:
    from src.generator.oversample import generate as gen, stage_initial_load
    if refresh_id == "R0":
        return [{"file": f} for f in stage_initial_load()]
    return gen(refresh_id)


def ingest_batch(airflow_run_id: str | None = None) -> list[dict]:
    from src.ingestion.batch_loader import ingest_all_landed
    return ingest_all_landed("airflow_scan", airflow_run_id)


def ingest_stream(refresh_id: str, airflow_run_id: str | None = None, api_url: str | None = None) -> dict:
    from src.ingestion.stream_ingest import replay_stream
    return replay_stream(refresh_id, airflow_run_id, api_url=api_url)


def validate() -> list[dict]:
    from src.validation.validate import validate_pending
    return validate_pending()


def transform(refresh_id: str) -> list[dict]:
    from src.transform.run_transform import run_all
    return run_all(refresh_id)


def optimize_and_benchmark(refresh_id: str, repeats: int = 3):
    from src.query.benchmark import apply_optimizations, benchmark
    apply_optimizations()
    return benchmark(refresh_id, repeats)


def storage(refresh_id: str) -> list[dict]:
    from src.storage.storage_metrics import capture
    return capture(refresh_id)


def full_refresh(refresh_id: str, stream: bool = True) -> None:
    start_refresh(refresh_id)
    try:
        generate(refresh_id)
        ingest_batch()
        if stream and refresh_id != "R0":
            ingest_stream(refresh_id)
        storage(refresh_id)                 # bronze snapshot
        validate()
        transform(refresh_id)
        optimize_and_benchmark(refresh_id)
        storage(refresh_id)                 # after gold + mart
        finish_refresh(refresh_id)
    except Exception:
        finish_refresh(refresh_id, "FAILED")
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["init", "refresh"])
    ap.add_argument("refresh_id", nargs="?", default="R0")
    ap.add_argument("--stream", action="store_true")
    a = ap.parse_args()
    if a.command == "init":
        init_database()
    else:
        full_refresh(a.refresh_id, stream=a.stream)
