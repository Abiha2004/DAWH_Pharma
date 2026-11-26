"""DAG sdi_01_batch_ingestion - Batch Data Ingestion Layer (landing CSV -> bronze).

Triggered two ways:
  * by the file-watcher service (watchdog) through the Airflow REST API, with
    conf {"file": "<name>.csv"}  -> loads exactly that file
  * every 5 minutes as a safety-net scan of data/landing/ (no conf)
Each file is a dynamically-mapped task instance, so the Airflow grid shows one
box per file. Files are claimed atomically, so both triggers can never collide.
Metrics per file -> ops.batch_load_log (Batch Ingestion dashboard).
"""
from __future__ import annotations

from airflow.decorators import dag, task

from common import BRONZE, DEFAULT_ARGS, START, TAGS


@dag(dag_id="sdi_01_batch_ingestion", start_date=START, schedule="*/5 * * * *", catchup=False,
     default_args=DEFAULT_ARGS, tags=TAGS + ["ingestion", "batch"], max_active_runs=3, doc_md=__doc__)
def sdi_01_batch_ingestion():

    @task
    def detect_files(dag_run=None) -> list[str]:
        """File named in the trigger conf, otherwise everything waiting in landing."""
        from src.ingestion.batch_loader import scan_landing
        conf = (dag_run.conf or {}) if dag_run else {}
        if conf.get("file"):
            return [conf["file"]]
        return [f.name for f in scan_landing()]

    @task(max_active_tis_per_dag=2, sla=None)   # Airflow does not support SLAs on mapped tasks
    def ingest_file(file_name: str, run_id=None) -> dict:
        from src.ingestion.batch_loader import ingest_file as load
        return load(file_name, detected_by="airflow", airflow_run_id=run_id)

    @task.short_circuit
    def anything_loaded(results: list[dict]) -> bool:
        """Only signal downstream layers when new data actually arrived."""
        return any(r.get("status") == "SUCCESS" for r in results)

    @task(outlets=[BRONZE])
    def publish_bronze(results: list[dict]) -> dict:
        ok = [r for r in results if r.get("status") == "SUCCESS"]
        return {"files_loaded": len(ok), "rows": sum(r.get("rows", 0) for r in ok)}

    files = detect_files()
    results = ingest_file.expand(file_name=files)
    anything_loaded(results) >> publish_bronze(results)


sdi_01_batch_ingestion()
