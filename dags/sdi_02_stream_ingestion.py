"""DAG sdi_02_stream_ingestion - Streaming Data Ingestion Layer.

Replays the e-commerce sales event stream for a refresh as timed micro-batches
POSTed to the Ingestion API (falls back to a direct DB write if the API is down).
Per micro-batch: throughput (events/s), end-to-end latency, malformed count
-> ops.stream_metrics (Streaming dashboard).
"""
from __future__ import annotations

import os

from airflow.decorators import dag, task
from airflow.models.param import Param

from common import BRONZE, DEFAULT_ARGS, START, TAGS


@dag(dag_id="sdi_02_stream_ingestion", start_date=START, schedule=None, catchup=False,
     default_args=DEFAULT_ARGS, tags=TAGS + ["ingestion", "streaming"], max_active_runs=1,
     params={"refresh_id": Param("R1", type="string")}, doc_md=__doc__)
def sdi_02_stream_ingestion():

    @task.short_circuit
    def stream_available(params=None, dag_run=None) -> bool:
        from src.common.config import path
        rid = (dag_run.conf or {}).get("refresh_id", params["refresh_id"])
        return (path("stream") / f"sales_stream__{rid}.jsonl").exists()

    @task(outlets=[BRONZE])
    def replay_stream(params=None, dag_run=None, run_id=None) -> dict:
        import requests
        from src.ingestion.stream_ingest import replay_stream as replay
        rid = (dag_run.conf or {}).get("refresh_id", params["refresh_id"])
        api = os.getenv("INGEST_API_URL")
        try:
            if api:
                requests.get(f"{api}/health", timeout=5).raise_for_status()
        except Exception:  # noqa: BLE001
            api = None                                  # API down -> direct path, still measured
        return replay(rid, airflow_run_id=run_id, api_url=api)

    stream_available() >> replay_stream()


sdi_02_stream_ingestion()
