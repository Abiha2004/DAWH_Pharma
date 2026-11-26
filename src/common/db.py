"""Thin database access layer used by every pipeline task.

Two interchangeable back-ends:
  * psycopg2  (default; used inside Docker/Airflow)
  * psql CLI  (set DWH_DB_BACKEND=psql; useful where no Python driver is installed)

All SQL in the project goes through these few functions, so query timing,
error counting and logging are applied consistently.
"""
from __future__ import annotations

import io
import os
import subprocess
import time
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from .config import DB, SQL_DIR

BACKEND = os.getenv("DWH_DB_BACKEND", "psycopg2")


# --------------------------------------------------------------------------- helpers
def _literal(v) -> str:
    """SQL literal for the psql back-end (psycopg2 does its own quoting)."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float, Decimal)):
        return repr(v) if not isinstance(v, Decimal) else str(v)
    if isinstance(v, (date, datetime)):
        return "'" + v.isoformat() + "'"
    return "'" + str(v).replace("'", "''") + "'"


def _bind(sql: str, params: Sequence | None) -> str:
    if not params:
        return sql
    parts = sql.split("%s")
    if len(parts) - 1 != len(params):
        raise ValueError("parameter count mismatch")
    out = parts[0]
    for p, rest in zip(params, parts[1:]):
        out += _literal(p) + rest
    return out


def _psql(args: list[str], stdin: bytes | None = None) -> str:
    env = dict(os.environ, PGPASSWORD=DB["password"])
    cmd = ["psql", "-h", DB["host"], "-p", str(DB["port"]), "-U", DB["user"],
           "-d", DB["dbname"], "-v", "ON_ERROR_STOP=1", "-q", *args]
    res = subprocess.run(cmd, input=stdin, capture_output=True, env=env)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.decode(errors="replace").strip())
    return res.stdout.decode(errors="replace")


@contextmanager
def connection():
    """psycopg2 connection with autocommit off; commits on success."""
    import psycopg2  # imported lazily so the psql back-end needs no driver

    conn = psycopg2.connect(**DB)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --------------------------------------------------------------------------- API
def execute(sql: str, params: Sequence | None = None, autocommit: bool = False) -> None:
    """Run one or more statements as ONE transaction (no result).

    autocommit=True is needed for statements that cannot run inside a
    transaction block, e.g. REFRESH MATERIALIZED VIEW CONCURRENTLY, VACUUM.
    """
    if BACKEND == "psql":
        _psql((["-1"] if not autocommit else []) + ["-f", "-"], stdin=_bind(sql, params).encode())
        return
    if autocommit:
        import psycopg2
        conn = psycopg2.connect(**DB)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
        finally:
            conn.close()
        return
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)


def execute_file(path: str | Path) -> None:
    p = Path(path)
    if not p.is_absolute():
        p = SQL_DIR / p
    execute(p.read_text(encoding="utf-8"))


def fetch_df(sql: str, params: Sequence | None = None) -> pd.DataFrame:
    if BACKEND == "psql":
        out = _psql(["--csv", "-c", _bind(sql, params)])
        return pd.read_csv(io.StringIO(out)) if out.strip() else pd.DataFrame()
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def fetch_scalar(sql: str, params: Sequence | None = None):
    df = fetch_df(sql, params)
    return None if df.empty else df.iat[0, 0]


def copy_csv(table: str, columns: Iterable[str], csv_text: str) -> None:
    """Bulk-load CSV text (with header) into table via COPY."""
    cols = ", ".join(f'"{c}"' for c in columns)
    copy_sql = f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true)"
    if BACKEND == "psql":
        _psql(["-c", copy_sql], stdin=csv_text.encode())
        return
    with connection() as conn, conn.cursor() as cur:
        cur.copy_expert(copy_sql, io.StringIO(csv_text))


def copy_df(table: str, df: pd.DataFrame) -> int:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    copy_csv(table, df.columns, buf.getvalue())
    return len(df)


def timed_fetch(sql: str) -> tuple[pd.DataFrame, float]:
    """Return (result, elapsed milliseconds)."""
    t0 = time.perf_counter()
    df = fetch_df(sql)
    return df, (time.perf_counter() - t0) * 1000.0
