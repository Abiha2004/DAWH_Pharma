"""GOLD LOAD: run the dimensional-model SQL steps in dependency order and log each one.

Each step is one SQL file under sql/load/ executed as one transaction. Row counts
before/after, SCD2 versions created and duration go to ops.transform_log.
Dimensions always load before facts so every surrogate-key lookup can succeed.
"""
from __future__ import annotations

import logging
import time

from src.common import db

log = logging.getLogger(__name__)

STEPS = [
    # (step name, sql file, target table, scd2-tracked?)
    ("dim_medicine_scd2", "load/20_dim_medicine_scd2.sql", "dwh.dim_medicine", True),
    ("dim_organization_scd2", "load/21_dim_organization_scd2.sql", "dwh.dim_organization", True),
    ("dims_conformed_and_junk", "load/22_dim_conformed_small.sql", "dwh.dim_batch_lot", False),
    ("fact_pharmacy_sales", "load/30_fact_pharmacy_sales.sql", "dwh.fact_pharmacy_sales", False),
    ("fact_hospital_demand", "load/31_fact_hospital_demand.sql", "dwh.fact_hospital_demand", False),
    ("fact_inventory_snapshot", "load/32_fact_inventory_snapshot.sql", "dwh.fact_inventory_snapshot", False),
]


def _count(table: str) -> int:
    return int(db.fetch_scalar(f"SELECT count(*) FROM {table}"))


def _scd_versions(table: str) -> int:
    return int(db.fetch_scalar(f"SELECT count(*) FROM {table} WHERE version_no > 1"))


def run_step(name: str, sql_file: str, table: str, scd: bool, refresh_id: str) -> dict:
    before = _count(table)
    v_before = _scd_versions(table) if scd else 0
    t0 = time.perf_counter()
    db.execute_file(sql_file)
    dur = time.perf_counter() - t0
    after = _count(table)
    v_new = (_scd_versions(table) - v_before) if scd else 0
    db.execute(
        "INSERT INTO ops.transform_log (refresh_id, step, target_table, rows_before, rows_after, rows_inserted, "
        "scd2_new_versions, duration_s) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [refresh_id, name, table, before, after, after - before, v_new, round(dur, 3)])
    out = {"step": name, "table": table, "inserted": after - before, "scd2_new_versions": v_new,
           "duration_s": round(dur, 2)}
    log.info("Transform %s", out)
    return out


def run_all(refresh_id: str, steps: list[str] | None = None) -> list[dict]:
    return [run_step(n, f, t, s, refresh_id) for n, f, t, s in STEPS if not steps or n in steps]
