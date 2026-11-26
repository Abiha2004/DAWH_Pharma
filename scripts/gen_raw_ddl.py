"""Regenerate sql/ddl/02_bronze_raw.sql from the CSV headers in data/source/."""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.common.config import cfg, path  # noqa: E402

out = ["-- BRONZE layer: raw copies of the 7 source files, every column TEXT so that no",
       "-- source row is lost at load time. Typing and validation happen in the silver layer.",
       "-- Generated from the CSV headers by scripts/gen_raw_ddl.py.", ""]
for name, m in cfg()["sources"].items():
    with open(path("source") / f"{m['file']}.csv", newline="", encoding="utf-8") as fh:
        hdr = next(csv.reader(fh))
    cols = ",\n".join(f'    "{c}" TEXT' for c in hdr)
    short = m["table"].split(".")[1]
    out.append(f"""CREATE TABLE IF NOT EXISTS {m['table']} (
{cols},
    _batch_id        INTEGER     NOT NULL,
    _source_file     TEXT        NOT NULL,
    _row_num         INTEGER     NOT NULL,
    _ingest_mode     TEXT        NOT NULL DEFAULT 'batch',   -- batch | stream
    _event_created_at TIMESTAMPTZ,                           -- stream events only
    _ingested_at     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS ix_{short}_batch ON {m['table']} (_batch_id);
""")
Path("sql/ddl/02_bronze_raw.sql").write_text("\n".join(out), encoding="utf-8")
print("written sql/ddl/02_bronze_raw.sql")
