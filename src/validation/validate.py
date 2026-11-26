"""SILVER LOAD: validate a bronze batch and promote clean rows to staging.

For every pending bronze batch (ops.batch_load_log, oldest first):
  1. type-cast every column with safe casts (bad values become NULL, never errors)
  2. evaluate every DQ rule + in-batch and cross-batch duplicate checks
  3. write per-rule counts to ops.dq_results            (DQ dashboard panel)
  4. write failing rows + reason to quarantine.rejected_rows
  5. insert passing rows into staging.* (ON CONFLICT DO NOTHING -> idempotent)
  6. quality gate: raise if the reject rate exceeds config.validation.max_reject_rate_pct
Everything for one batch runs as ONE SQL script = one transaction.
"""
from __future__ import annotations

import logging

from src.common import db
from src.common.config import cfg
from src.validation.rules import SDI_SOURCES, SOURCES

log = logging.getLogger(__name__)


class QualityGateError(RuntimeError):
    pass


def build_sql(source: str, batch_id: int, refresh_id: str) -> str:
    s = SOURCES[source]
    vcfg = cfg()["validation"]
    key = s["key"]
    dup_key = s.get("dup_key", key)
    select = ",\n       ".join(f"{expr} AS {col}" for col, expr in s["cols"].items())
    rules = [(n, t, c.format(**vcfg)) for n, t, c in s["rules"]]
    rules.append(("duplicate_in_batch", "uniqueness", "_dup_rank > 1"))
    if s["dup_existing"]:
        rules.append(("duplicate_existing_key", "uniqueness",
                      f"EXISTS (SELECT 1 FROM {s['stg']} x WHERE x.{key} = v.{key})"))
    flag_cols = ",\n       ".join(f"COALESCE(({c}), FALSE) AS f_{n}" for n, _, c in rules)
    any_bad = " OR ".join(f"f_{n}" for n, _, _ in rules)
    first_fail = "CASE " + " ".join(f"WHEN f_{n} THEN '{n}'" for n, _, _ in rules) + " END"
    dq_rows = "\nUNION ALL ".join(
        f"SELECT {batch_id}, '{refresh_id}', '{source}', '{n}', '{t}', count(*)::int, "
        f"(count(*) FILTER (WHERE f_{n}))::int FROM v2" for n, t, _ in rules)
    cols = ", ".join(s["cols"].keys())
    conflict = s.get("conflict", f"({key})")

    return f"""
CREATE TEMP TABLE v ON COMMIT DROP AS
SELECT r._row_num AS _row_num,
       {select},
       row_number() OVER (PARTITION BY {", ".join(f"({s['cols'][k.strip()]})" for k in dup_key.split(","))}
                          ORDER BY r._row_num) AS _dup_rank
FROM {s['raw']} r
WHERE r._batch_id = {batch_id};

CREATE TEMP TABLE v2 ON COMMIT DROP AS
SELECT v.*,
       {flag_cols}
FROM v;

INSERT INTO ops.dq_results (batch_id, refresh_id, source_domain, rule_name, rule_type, checked_rows, failed_rows)
{dq_rows};

INSERT INTO quarantine.rejected_rows (batch_id, source_domain, natural_key, failed_rule, payload)
SELECT {batch_id}, '{source}', v2.{key}, {first_fail},
       to_jsonb(r) - '_batch_id' - '_source_file' - '_ingested_at'
FROM v2 JOIN {s['raw']} r ON r._batch_id = {batch_id} AND r._row_num = v2._row_num
WHERE {any_bad};

INSERT INTO {s['stg']} ({cols}, _batch_id)
SELECT {cols}, {batch_id} FROM v2
WHERE NOT ({any_bad})
ON CONFLICT {conflict} DO NOTHING;

UPDATE ops.batch_load_log SET validated_at = now() WHERE batch_id = {batch_id};
"""


def pending_batches(sources: list[str] | None = None) -> list[tuple[int, str, str]]:
    sources = sources or SDI_SOURCES
    in_list = ",".join(f"'{s}'" for s in sources if s in SOURCES)   # internal constants only
    df = db.fetch_df(
        "SELECT batch_id, source_domain, refresh_id FROM ops.batch_load_log "
        f"WHERE status = 'SUCCESS' AND validated_at IS NULL AND source_domain IN ({in_list}) ORDER BY batch_id")
    batches = [(int(r.batch_id), r.source_domain, r.refresh_id) for r in df.itertuples()]
    # Dependency guard: files land in parallel, so a fact batch can arrive before the medicine
    # master it references. Defer every non-master batch of a refresh until that refresh's
    # master data is in silver - otherwise the referential rule would reject 100% of the rows.
    ready = [b for b in batches if b[1] == "master" or _master_ready(b[2], batches)]
    return sorted(ready, key=lambda b: (b[1] != "master", b[0]))      # master data always first


def _master_ready(refresh_id: str, pending: list[tuple[int, str, str]]) -> bool:
    from src.common.config import path
    if any(s == "master" and r == refresh_id for _, s, r in pending):
        return True                                   # master is in this same run and is validated first
    if not int(db.fetch_scalar("SELECT count(*) FROM staging.medicine") or 0):
        return False                                  # no master data in silver yet
    suffix = "" if refresh_id == "R0" else f"__{refresh_id}"
    name = f"01_master_data{suffix}.csv"
    if (path("landing") / name).exists() or (path("landing") / ".processing" / name).exists():
        return False                                  # this refresh's master file is still being ingested
    running = int(db.fetch_scalar(
        "SELECT count(*) FROM ops.batch_load_log WHERE source_domain = 'master' AND refresh_id = %s "
        "AND status = 'RUNNING'", [refresh_id]) or 0)
    return running == 0


def validate_batch(batch_id: int, source: str, refresh_id: str, enforce_gate: bool = True) -> dict:
    db.execute(build_sql(source, batch_id, refresh_id))
    res = db.fetch_df(
        "SELECT max(checked_rows) AS checked, "
        "(SELECT count(*) FROM quarantine.rejected_rows WHERE batch_id = %s) AS rejected "
        "FROM ops.dq_results WHERE batch_id = %s", [batch_id, batch_id])
    checked, rejected = int(res.iat[0, 0] or 0), int(res.iat[0, 1] or 0)
    rate = 100.0 * rejected / checked if checked else 0.0
    out = {"batch_id": batch_id, "source": source, "checked": checked, "rejected": rejected,
           "reject_rate_pct": round(rate, 3)}
    log.info("Validated %s", out)
    if enforce_gate and rate > float(cfg()["validation"]["max_reject_rate_pct"]):
        raise QualityGateError(f"Batch {batch_id} ({source}) reject rate {rate:.2f}% exceeds gate")
    return out


def validate_pending(sources: list[str] | None = None) -> list[dict]:
    return [validate_batch(b, s, r) for b, s, r in pending_batches(sources)]
