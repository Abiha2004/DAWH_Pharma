"""STORAGE & STAGING metrics -> ops.storage_snapshot / ops.database_size_log.

Per table and layer:
  * Table Row Count        SELECT count(*)
  * Storage Footprint (MB) pg_total_relation_size (heap + indexes + TOAST);
                           whole database via pg_database_size, plus an OS-level
                           os.path.getsize walk of the Postgres data directory
                           when it is mounted into the Airflow container
  * Date Coverage Span     min/max business date + number of distinct months
"""
from __future__ import annotations

import os
from pathlib import Path

from src.common import db

# (layer, table, source domain, business date expression)
TABLES = [
    ("bronze", "raw.master_data", "master", "ops.try_date(\"EffectiveFromDate\")"),
    ("bronze", "raw.manufacturing_data", "manufacturing", "ops.try_date(\"OrderDate\")"),
    ("bronze", "raw.manufacturer_delivery", "delivery", "ops.try_date(\"DispatchDate\")"),
    ("bronze", "raw.warehouse_data", "warehouse", "ops.try_date(\"SnapshotDate\")"),
    ("bronze", "raw.pharmacy_sales", "sales", "ops.try_date(\"SalesDate\")"),
    ("bronze", "raw.hospital_demand", "hospital", "ops.try_date(\"DemandMonth\")"),
    ("bronze", "raw.iot_readings", "iot", "ops.try_ts(\"EventTimestampUTC\")::date"),
    ("silver", "staging.medicine", "master", "effective_from_date"),
    ("silver", "staging.manufacturing_batch", "manufacturing", "order_date"),
    ("silver", "staging.warehouse_snapshot", "warehouse", "snapshot_date"),
    ("silver", "staging.pharmacy_sale", "sales", "sales_date"),
    ("silver", "staging.hospital_demand", "hospital", "demand_month"),
    ("quarantine", "quarantine.rejected_rows", "all", "rejected_at::date"),
    ("gold", "dwh.fact_pharmacy_sales", "sales", "to_date(sale_date_key::text, 'YYYYMMDD')"),
    ("gold", "dwh.fact_hospital_demand", "hospital", "to_date(demand_month_key::text, 'YYYYMMDD')"),
    ("gold", "dwh.fact_inventory_snapshot", "warehouse", "to_date(snapshot_date_key::text, 'YYYYMMDD')"),
    ("gold", "dwh.dim_medicine", "master", None),
    ("gold", "dwh.dim_organization", "sales", None),
    ("gold", "dwh.dim_batch_lot", "warehouse", None),
    ("gold", "dwh.dim_date", "calendar", None),
    ("mart", "mart.mv_sales_month_med_channel", "sales", "month_start_date"),
    ("mart", "mart.mv_sales_commercial", "sales", "month_start_date"),
    ("mart", "mart.mv_demand_month_hospital", "hospital", "month_start_date"),
    ("mart", "mart.mv_sales_vs_demand", "sales", "month_start_date"),
    ("mart", "mart.mv_inventory_month_wh_med", "warehouse", "month_start_date"),
]


def _size_mb(table: str) -> float:
    # partitioned tables: sum partitions
    return float(db.fetch_scalar(
        "SELECT COALESCE(sum(pg_total_relation_size(c.oid)), 0) / 1048576.0 "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE (n.nspname || '.' || c.relname) = %s "
        "   OR c.oid IN (SELECT inhrelid FROM pg_inherits WHERE inhparent = %s::regclass)",
        [table, table]) or 0.0)


def os_dir_size_mb(p: str | None) -> float | None:
    if not p or not Path(p).exists():
        return None
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return round(total / 1_048_576, 3)


def capture(refresh_id: str) -> list[dict]:
    out = []
    for layer, table, domain, date_expr in TABLES:
        exists = db.fetch_scalar("SELECT to_regclass(%s) IS NOT NULL", [table])
        if not exists or str(exists).lower() in ("f", "false"):
            continue
        if date_expr:
            r = db.fetch_df(f"SELECT count(*) AS n, min({date_expr}) AS mn, max({date_expr}) AS mx, "
                            f"count(DISTINCT date_trunc('month', {date_expr})) AS months FROM {table}")
            n, mn, mx, months = int(r.iat[0, 0]), r.iat[0, 1], r.iat[0, 2], int(r.iat[0, 3] or 0)
        else:
            n, mn, mx, months = int(db.fetch_scalar(f"SELECT count(*) FROM {table}")), None, None, None
        size = round(_size_mb(table), 3)
        mn = None if mn is None or str(mn) in ("nan", "NaT", "None") else str(mn)[:10]
        mx = None if mx is None or str(mx) in ("nan", "NaT", "None") else str(mx)[:10]
        db.execute(
            "INSERT INTO ops.storage_snapshot (refresh_id, layer, schema_name, table_name, source_domain, row_count, "
            "size_mb, min_date, max_date, distinct_months) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [refresh_id, layer, table.split(".")[0], table.split(".")[1], domain, n, size, mn, mx, months])
        out.append({"layer": layer, "table": table, "rows": n, "size_mb": size, "min": mn, "max": mx})
    db_mb = float(db.fetch_scalar("SELECT pg_database_size(current_database()) / 1048576.0"))
    data_dir = os_dir_size_mb(os.getenv("PG_DATA_DIR_MOUNT"))
    db.execute("INSERT INTO ops.database_size_log (refresh_id, db_size_mb, data_dir_mb) VALUES (%s, %s, %s)",
               [refresh_id, round(db_mb, 3), data_dir])
    return out
