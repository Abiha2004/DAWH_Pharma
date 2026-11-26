"""Central configuration: project root, YAML config and database settings."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

# Repo root = two levels above this file (src/common/config.py)
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", Path(__file__).resolve().parents[2]))
SQL_DIR = PROJECT_ROOT / "sql"


@lru_cache(maxsize=1)
def cfg() -> dict:
    with open(PROJECT_ROOT / "config" / "pipeline.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def path(key: str) -> Path:
    """Absolute path of a configured data folder (created if missing)."""
    p = PROJECT_ROOT / cfg()["paths"][key]
    p.mkdir(parents=True, exist_ok=True)
    return p


def source_for_file(filename: str) -> tuple[str, dict] | None:
    """Map a landed file name (e.g. 05_pharmacy_sales_data_R1.csv) to its source domain."""
    for name, meta in cfg()["sources"].items():
        if filename.startswith(meta["file"]):
            return name, meta
    return None


DB = {
    "host": os.getenv("DWH_DB_HOST", "localhost"),
    "port": int(os.getenv("DWH_DB_PORT", "5432")),
    "dbname": os.getenv("DWH_DB_NAME", "pharma_dwh"),
    "user": os.getenv("DWH_DB_USER", "postgres"),
    "password": os.getenv("DWH_DB_PASSWORD", "postgres"),
}
