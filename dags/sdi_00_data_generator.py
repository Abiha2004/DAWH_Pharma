"""DAG sdi_00_data_generator - Data Oversampling Layer.

Trigger with {"refresh_id": "R0"} for the initial historical load, then "R1", "R2" ...
  init_database -> start_refresh -> generate_files -> trigger_stream_ingestion
The generated CSVs land in data/landing/, where the file-watcher detects them and
triggers sdi_01_batch_ingestion. The e-commerce event file is replayed by
sdi_02_stream_ingestion, which this DAG triggers at the end.
"""
from __future__ import annotations

from airflow.decorators import dag, task
from airflow.models.param import Param
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

from common import DEFAULT_ARGS, START, TAGS


@dag(dag_id="sdi_00_data_generator", start_date=START, schedule=None, catchup=False,
     default_args=DEFAULT_ARGS, tags=TAGS + ["oversampling"], max_active_runs=1,
     params={"refresh_id": Param("R1", type="string", enum=["R0", "R1", "R2", "R3", "R4", "R5", "R6"],
                                 description="R0 = initial historical load; R1/R2 = Phase 1 refreshes")},
     doc_md=__doc__)
def sdi_00_data_generator():

    @task
    def init_database() -> None:
        from src.pipeline import init_database as run
        run()                                   # idempotent: CREATE ... IF NOT EXISTS

    @task
    def start_refresh(params=None) -> str:
        from src.pipeline import start_refresh as run
        rid = params["refresh_id"]
        run(rid)
        return rid

    @task
    def generate_files(refresh_id: str) -> list[dict]:
        from src.pipeline import generate
        stats = generate(refresh_id)            # files written atomically into data/landing/
        return stats

    rid = start_refresh()
    init_database() >> rid
    files = generate_files(rid)

    stream = TriggerDagRunOperator(
        task_id="trigger_stream_ingestion",
        trigger_dag_id="sdi_02_stream_ingestion",
        conf={"refresh_id": "{{ params.refresh_id }}"},
        wait_for_completion=False,
        trigger_rule="all_success",
    )
    files >> stream


sdi_00_data_generator()
