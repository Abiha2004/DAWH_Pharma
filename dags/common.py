"""Shared DAG settings: retries, SLA, failure logging and the Datasets that chain the layers.

Data-aware scheduling (Airflow Datasets) links the pipeline:
    batch / stream ingestion  --BRONZE-->  silver validation  --SILVER-->
    gold transform  --GOLD-->  query layer (MV refresh + benchmark + storage metrics)
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta

from airflow.datasets import Dataset

PROJECT_ROOT = os.getenv("PROJECT_ROOT", "/opt/airflow/project")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

BRONZE = Dataset("postgres://pharma_dwh/raw")
SILVER = Dataset("postgres://pharma_dwh/staging")
GOLD = Dataset("postgres://pharma_dwh/dwh")

START = datetime(2026, 11, 1)
TAGS = ["pharma-dwh", "phase-1", "SDI"]


def on_failure(context) -> None:
    ti = context.get("task_instance")
    logging.getLogger("pharma_dwh").error(
        "Task failed: dag=%s task=%s run=%s try=%s", ti.dag_id, ti.task_id, ti.run_id, ti.try_number)


DEFAULT_ARGS = {
    "owner": "dwh-team",
    "retries": 2,
    "retry_delay": timedelta(seconds=30),
    "retry_exponential_backoff": True,
    "execution_timeout": timedelta(minutes=30),
    "sla": timedelta(minutes=20),
    "on_failure_callback": on_failure,
}
