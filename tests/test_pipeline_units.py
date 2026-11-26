"""Unit tests that need no database: config, generator logic, DQ SQL generation, query catalogue."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.config import cfg, source_for_file  # noqa: E402
from src.ingestion.batch_loader import refresh_of  # noqa: E402
from src.query.catalog import QUERIES  # noqa: E402
from src.validation.rules import SOURCES  # noqa: E402
from src.validation.validate import build_sql  # noqa: E402


def test_source_files_are_recognised():
    assert source_for_file("05_pharmacy_sales_data__R1.csv")[0] == "sales"
    assert source_for_file("07_iot_streaming_data.csv")[0] == "iot"
    assert source_for_file("random.csv") is None


def test_refresh_id_parsing():
    assert refresh_of("05_pharmacy_sales_data__R2.csv") == "R2"
    assert refresh_of("05_pharmacy_sales_data.csv") == "R0"


def test_refresh_calendar_gives_two_refreshes_per_phase():
    phases = [r["phase"] for r in cfg()["refreshes"].values()]
    assert all(phases.count(p) == 2 for p in (1, 2, 3))


def test_every_dq_rule_is_compiled_into_the_sql():
    for src, spec in SOURCES.items():
        sql = build_sql(src, 999, "RT")
        for name, _, _ in spec["rules"]:
            assert f"f_{name}" in sql
        assert "duplicate_in_batch" in sql
        assert "INSERT INTO quarantine.rejected_rows" in sql


def test_query_catalogue_is_complete():
    ids = [q["id"] for q in QUERIES]
    assert len(ids) == len(set(ids)) == 13
    for q in QUERIES:
        assert q["baseline"].strip() and q["optimized"].strip()
        assert "mart." in q["optimized"]          # optimized variant must use a summary table


def test_scd_changes_apply_cumulatively():
    import pytest
    pytest.importorskip("pandas")
    from src.generator.oversample import current_master
    try:
        m1, m2 = current_master("R1"), current_master("R2")
    except FileNotFoundError:
        pytest.skip("source CSVs not present")
    p1 = float(m1.loc[m1.MedicineID == "MED001", "RecommendedRetailPriceUSD"].iloc[0])
    p2 = float(m2.loc[m2.MedicineID == "MED001", "RecommendedRetailPriceUSD"].iloc[0])
    assert round(p2 / p1, 2) == 1.10
