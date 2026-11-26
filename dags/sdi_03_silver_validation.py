"""DAG sdi_03_silver_validation - Storage & Staging Layer (bronze -> silver ODS).

Runs automatically whenever new bronze data is published (Dataset BRONZE).
  validate_pending_batches : oldest batch first (master data before the facts that
                             reference it); safe casts, DQ rules, duplicate checks,
                             rejects -> quarantine, clean rows -> staging
  quality_gate             : fails (no retry) if any batch exceeds the reject-rate
                             threshold, which stops promotion to the gold layer
  publish_silver           : emits the SILVER dataset -> triggers the gold DAG
"""
from __future__ import annotations

from airflow.decorators import dag, task

from common import BRONZE, DEFAULT_ARGS, SILVER, START, TAGS


@dag(dag_id="sdi_03_silver_validation", start_date=START, schedule=[BRONZE], catchup=False,
     default_args=DEFAULT_ARGS, tags=TAGS + ["staging", "data-quality"], max_active_runs=1, doc_md=__doc__)
def sdi_03_silver_validation():

    @task
    def validate_pending_batches() -> list[dict]:
        from src.validation.validate import pending_batches, validate_batch
        return [validate_batch(b, s, r, enforce_gate=False) for b, s, r in pending_batches()]

    @task(retries=0)
    def quality_gate(results: list[dict]) -> list[dict]:
        from src.common.config import cfg
        limit = float(cfg()["validation"]["max_reject_rate_pct"])
        bad = [r for r in results if r["reject_rate_pct"] > limit]
        if bad:
            raise ValueError(f"Quality gate failed (> {limit}% rejected): {bad}")
        return results

    @task.short_circuit
    def anything_new(results: list[dict]) -> bool:
        return len(results) > 0

    @task(outlets=[SILVER])
    def publish_silver(results: list[dict]) -> dict:
        return {"batches": len(results), "rejected": sum(r["rejected"] for r in results)}

    checked = quality_gate(validate_pending_batches())
    anything_new(checked) >> publish_silver(checked)


sdi_03_silver_validation()
