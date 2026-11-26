"""QUERY LAYER benchmark + optimisation management.

For every catalogue query and variant (baseline / optimized):
  * client_ms : time.perf_counter() wrapper around the database call
  * server_ms : PostgreSQL's own 'Execution Time' from EXPLAIN (ANALYZE)
  * rows_returned, SUCCESS/FAILED (-> Query Error Rate %)
  * checksum  : md5 of the result set - baseline and optimized must match
Results go to ops.query_log (Query-Layer dashboard) and are returned for the report.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

import pandas as pd

from src.common import db
from src.query.catalog import QUERIES

log = logging.getLogger(__name__)


def apply_optimizations() -> None:
    """Indexes + materialized views (idempotent), then refresh MVs concurrently."""
    db.execute_file("optimize/40_indexes.sql")
    db.execute_file("optimize/41_materialized_views.sql")
    refresh_materialized_views()


def refresh_materialized_views() -> float:
    t0 = time.perf_counter()
    db.execute((db.SQL_DIR / "optimize/42_refresh_materialized_views.sql").read_text(), autocommit=True)
    return round(time.perf_counter() - t0, 3)


def _server_ms(sql: str) -> float | None:
    try:
        plan = db.fetch_df("EXPLAIN (ANALYZE, FORMAT JSON) " + sql)
        raw = plan.iat[0, 0]
        doc = json.loads(raw) if isinstance(raw, str) else raw
        return float(doc[0]["Execution Time"])
    except Exception:  # noqa: BLE001
        return None


def _checksum(df: pd.DataFrame) -> str:
    norm = df.copy()
    for c in norm.columns:
        if pd.api.types.is_numeric_dtype(norm[c]):
            norm[c] = norm[c].astype(float).round(2)
    norm = norm.astype(str)
    return hashlib.md5(norm.to_csv(index=False).encode()).hexdigest()


def run_query(q: dict, variant: str, refresh_id: str, triggered_by: str = "airflow_benchmark") -> dict:
    sql = q[variant].strip()
    status, err, rows, checksum = "SUCCESS", None, 0, None
    t0 = time.perf_counter()
    try:
        df = db.fetch_df(sql)
        rows, checksum = len(df), _checksum(df)
    except Exception as exc:  # noqa: BLE001 - failures feed the error-rate KPI
        status, err = "FAILED", str(exc)[:500]
    client_ms = (time.perf_counter() - t0) * 1000
    server_ms = _server_ms(sql) if status == "SUCCESS" else None
    db.execute(
        "INSERT INTO ops.query_log (refresh_id, query_id, query_name, widget, variant, client_ms, server_ms, "
        "rows_returned, status, error_message, triggered_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        [refresh_id, q["id"], q["name"], q["widget"], variant, round(client_ms, 3),
         round(server_ms, 3) if server_ms is not None else None, rows, status, err, triggered_by])
    return {"query_id": q["id"], "variant": variant, "client_ms": round(client_ms, 2),
            "server_ms": round(server_ms, 2) if server_ms is not None else None,
            "rows": rows, "status": status, "checksum": checksum}


def benchmark(refresh_id: str, repeats: int = 3) -> pd.DataFrame:
    """Run every query `repeats` times per variant (first run warms the cache)."""
    out = []
    for q in QUERIES:
        for variant in ("baseline", "optimized"):
            for _ in range(repeats):
                out.append(run_query(q, variant, refresh_id))
    df = pd.DataFrame(out)
    summary = (df.groupby(["query_id", "variant"])
                 .agg(server_ms=("server_ms", "median"), client_ms=("client_ms", "median"),
                      rows=("rows", "max"), checksum=("checksum", "last")).reset_index())
    piv = summary.pivot(index="query_id", columns="variant")
    res = pd.DataFrame({
        "baseline_ms": piv[("server_ms", "baseline")],
        "optimized_ms": piv[("server_ms", "optimized")],
        "rows": piv[("rows", "optimized")],
        "results_identical": piv[("checksum", "baseline")] == piv[("checksum", "optimized")],
    })
    res["speedup_x"] = (res["baseline_ms"] / res["optimized_ms"]).round(1)
    log.info("Benchmark %s\n%s", refresh_id, res)
    return res.reset_index()
