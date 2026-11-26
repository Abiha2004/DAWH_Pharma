"""DAG sdi_05_query_layer - Analytical Query Layer & Query Optimization.

Runs after every gold load (Dataset GOLD):
  apply_indexes_and_mvs -> refresh_materialized_views -> benchmark_queries
  -> capture_storage_metrics -> close_refresh
The benchmark runs every catalogue query in baseline and optimized form and
logs timings + error status to ops.query_log (Query-Layer dashboard).
"""
from __future__ import annotations

from airflow.decorators import dag, task

from common import DEFAULT_ARGS, GOLD, START, TAGS


def _refresh_id() -> str:
    from src.common import db
    rid = db.fetch_scalar("SELECT refresh_id FROM ops.refresh_log ORDER BY started_at DESC LIMIT 1")
    return str(rid or "R0")


@dag(dag_id="sdi_05_query_layer", start_date=START, schedule=[GOLD], catchup=False,
     default_args=DEFAULT_ARGS, tags=TAGS + ["query-layer", "optimization"], max_active_runs=1, doc_md=__doc__)
def sdi_05_query_layer():

    @task
    def apply_indexes_and_mvs() -> None:
        from src.common import db
        db.execute_file("optimize/40_indexes.sql")
        db.execute_file("optimize/41_materialized_views.sql")

    @task
    def refresh_materialized_views() -> float:
        from src.query.benchmark import refresh_materialized_views as r
        return r()

    @task
    def benchmark_queries() -> list[dict]:
        from src.query.benchmark import benchmark
        return benchmark(_refresh_id(), repeats=3).astype(str).to_dict("records")

    @task
    def capture_storage_metrics() -> int:
        from src.storage.storage_metrics import capture
        return len(capture(_refresh_id()))

    @task
    def close_refresh() -> str:
        from src.pipeline import finish_refresh, refresh_is_complete
        rid = _refresh_id()
        if refresh_is_complete(rid):          # small files reach gold first - wait for all of them
            finish_refresh(rid)
            return f"{rid} complete"
        return f"{rid} still in progress - will be closed by a later run"

    apply_indexes_and_mvs() >> refresh_materialized_views() >> benchmark_queries() \
        >> capture_storage_metrics() >> close_refresh()


sdi_05_query_layer()
