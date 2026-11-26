"""DAG sdi_04_gold_transform - Transformation & Dimensional Modeling Layer (silver -> SDI star).

Dimensions first (SCD2 medicine, SCD2 organization, conformed + junk dimensions),
then the three fact tables in parallel. Every step is incremental (watermarks)
and idempotent (ON CONFLICT), and logs to ops.transform_log.
"""
from __future__ import annotations

from airflow.decorators import dag, task
from airflow.utils.task_group import TaskGroup

from common import DEFAULT_ARGS, GOLD, SILVER, START, TAGS


def _refresh_id() -> str:
    from src.common import db
    rid = db.fetch_scalar("SELECT refresh_id FROM ops.refresh_log ORDER BY started_at DESC LIMIT 1")
    return str(rid or "R0")


def make_step(step_name: str):
    @task(task_id=step_name)
    def _run() -> dict:
        from src.transform.run_transform import STEPS, run_step
        n, f, t, s = next(x for x in STEPS if x[0] == step_name)
        return run_step(n, f, t, s, _refresh_id())
    return _run()


@dag(dag_id="sdi_04_gold_transform", start_date=START, schedule=[SILVER], catchup=False,
     default_args=DEFAULT_ARGS, tags=TAGS + ["transformation", "dimensional-model"], max_active_runs=1,
     doc_md=__doc__)
def sdi_04_gold_transform():
    with TaskGroup("dimensions") as dims:
        med = make_step("dim_medicine_scd2")
        org = make_step("dim_organization_scd2")
        small = make_step("dims_conformed_and_junk")
        med >> org >> small

    with TaskGroup("facts") as facts:
        make_step("fact_pharmacy_sales")
        make_step("fact_hospital_demand")
        make_step("fact_inventory_snapshot")

    @task(outlets=[GOLD])
    def publish_gold() -> str:
        return "gold layer updated"

    dims >> facts >> publish_gold()


sdi_04_gold_transform()
