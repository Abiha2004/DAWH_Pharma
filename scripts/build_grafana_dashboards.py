"""Generate the four Data-Engineering Grafana dashboards as JSON (dashboards-as-code).

Design rules (from the course guidance):
  * gradient colours - light for low values, dark for high values (continuous-blues)
  * Z / F reading pattern - KPI stat cards across the top, the main chart top-left,
    supporting charts to the right, detail tables at the bottom
  * every dashboard auto-refreshes (10 s) so pipeline runs are visible live
Run:  python scripts/build_grafana_dashboards.py   -> grafana/dashboards/*.json
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "grafana" / "dashboards"
DS = {"type": "grafana-postgresql-datasource", "uid": "pharma-dwh"}
GRADIENT = {"mode": "continuous-blues"}

_id = 0


def _next() -> int:
    global _id
    _id += 1
    return _id


def target(sql: str, fmt: str = "table", ref: str = "A") -> dict:
    return {"refId": ref, "datasource": DS, "editorMode": "code", "format": fmt, "rawQuery": True, "rawSql": sql.strip()}


def stat(title, sql, x, y, w=4, h=4, unit="short", decimals=None, desc="", thresholds=None, color_mode="background"):
    fc = {"unit": unit, "color": GRADIENT if not thresholds else {"mode": "thresholds"}}
    if decimals is not None:
        fc["decimals"] = decimals
    if thresholds:
        fc["thresholds"] = {"mode": "absolute", "steps": thresholds}
    return {"id": _next(), "type": "stat", "title": title, "description": desc, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": fc, "overrides": []},
            "options": {"colorMode": color_mode, "graphMode": "none", "justifyMode": "center", "textMode": "value",
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
            "targets": [target(sql)]}


def bar(title, sql, x, y, w=12, h=9, unit="short", horizontal=False, desc="", stacking="none", xfield=None, legend=True,
        multi=False):
    opts = {"orientation": "horizontal" if horizontal else "vertical", "showValue": "auto", "stacking": stacking,
            "groupWidth": 0.7, "barWidth": 0.9, "xTickLabelRotation": 0 if horizontal else -30,
            "legend": {"showLegend": legend, "displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "multi"}}
    if xfield:
        opts["xField"] = xfield
    return {"id": _next(), "type": "barchart", "title": title, "description": desc, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "palette-classic"} if multi else GRADIENT,
                                         "custom": {"fillOpacity": 85, "gradientMode": "scheme", "lineWidth": 0}},
                            "overrides": []},
            "options": opts, "targets": [target(sql)]}


def timeseries(title, sql, x, y, w=12, h=9, unit="short", desc="", draw="line", points="always"):
    return {"id": _next(), "type": "timeseries", "title": title, "description": desc, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "palette-classic"},
                                         "custom": {"drawStyle": draw, "lineWidth": 2, "fillOpacity": 15,
                                                    "gradientMode": "opacity", "showPoints": points, "pointSize": 4,
                                                    "spanNulls": True}},
                            "overrides": []},
            "options": {"legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                        "tooltip": {"mode": "multi"}},
            "targets": [target(sql, "time_series")]}


def pie(title, sql, x, y, w=8, h=9, unit="short", desc=""):
    return {"id": _next(), "type": "piechart", "title": title, "description": desc, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "palette-classic"}}, "overrides": []},
            "options": {"pieType": "donut", "displayLabels": ["percent"],
                        "legend": {"showLegend": True, "displayMode": "table", "placement": "right",
                                   "values": ["value", "percent"]},
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True}},
            "targets": [target(sql)]}


def table(title, sql, x, y, w=24, h=8, desc="", gradient_cols=()):
    overrides = [{"matcher": {"id": "byName", "options": c},
                  "properties": [{"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "gradient"}},
                                 {"id": "color", "value": {"mode": "continuous-blues"}}]} for c in gradient_cols]
    return {"id": _next(), "type": "table", "title": title, "description": desc, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": {"custom": {"align": "auto", "filterable": True}}, "overrides": overrides},
            "options": {"showHeader": True, "cellHeight": "sm"}, "targets": [target(sql)]}


def text(content, x, y, w=24, h=3):
    return {"id": _next(), "type": "text", "title": "", "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "options": {"mode": "markdown", "content": content}}


def refresh_var(source_table: str) -> dict:
    return {"name": "refresh", "label": "Refresh", "type": "query", "datasource": DS,
            "query": f"SELECT DISTINCT refresh_id FROM {source_table} WHERE refresh_id IS NOT NULL ORDER BY 1",
            "definition": f"SELECT DISTINCT refresh_id FROM {source_table}", "multi": True, "includeAll": True,
            "current": {"selected": True, "text": ["All"], "value": ["$__all"]}, "refresh": 2, "sort": 1}


def dashboard(uid, title, panels, desc, variables=()) -> dict:
    return {"uid": uid, "title": title, "description": desc, "tags": ["pharma-dwh", "data-engineering", "phase-1"],
            "timezone": "browser", "schemaVersion": 39, "version": 1, "editable": True,
            "refresh": "10s", "time": {"from": "now-7d", "to": "now"},
            "templating": {"list": list(variables)}, "panels": panels,
            "links": [{"title": "All DE dashboards", "type": "dashboards", "tags": ["data-engineering"], "asDropdown": True}]}


# ============================================================== 1. BATCH INGESTION
F = "FROM ops.batch_load_log WHERE ingest_mode = 'batch' AND refresh_id IN ($refresh)"
batch = dashboard("de-batch-ingestion", "1 · Batch Ingestion (Landing CSV → Bronze)", [
    text("**Batch ingestion** - every file the file-watcher detects in `data/landing/` is loaded into the bronze "
         "layer. Duration = end − start of read + parse + COPY. Use the *Refresh* filter to compare the initial "
         "load (R0) with refresh 1 and 2.", 0, 0, 24, 2),
    stat("Files processed", f"SELECT count(*) {F} AND status <> 'RUNNING'", 0, 2),
    stat("Rows processed", f"SELECT COALESCE(sum(rows_loaded),0) {F}", 4, 2),
    stat("Avg load duration", f"SELECT avg(duration_s) {F} AND status = 'SUCCESS'", 8, 2, unit="s", decimals=2),
    stat("Batch success rate", f"SELECT 100.0 * count(*) FILTER (WHERE status IN ('SUCCESS','SKIPPED_DUPLICATE')) / NULLIF(count(*) FILTER (WHERE status <> 'RUNNING'),0) {F}",
         12, 2, unit="percent", decimals=1,
         thresholds=[{"color": "#c4162a", "value": None}, {"color": "#ff9830", "value": 95}, {"color": "#1f60c4", "value": 99}]),
    stat("Malformed rows skipped", f"SELECT COALESCE(sum(rows_malformed),0) {F}", 16, 2),
    stat("Avg throughput", f"SELECT avg(rows_per_sec) {F} AND status = 'SUCCESS' AND rows_loaded > 1000", 20, 2, unit="rowsps", decimals=0),
    bar("Load duration (seconds) per source file", f"""
SELECT source_domain || ' (' || refresh_id || ')' AS file, duration_s AS "Load duration (s)"
{F} AND status = 'SUCCESS' ORDER BY duration_s DESC""", 0, 6, 12, 10, unit="s", horizontal=True, legend=False,
        desc="Recommended chart: spot instantly which large file takes longest (sales, 500k rows)."),
    bar("Rows processed per batch", f"""
SELECT source_domain || ' (' || refresh_id || ')' AS file, rows_loaded AS "Rows loaded", rows_malformed AS "Malformed"
{F} AND status = 'SUCCESS' ORDER BY rows_loaded DESC""", 12, 6, 12, 10, horizontal=True, multi=True),
    bar("Success rate by source domain (%)", f"""
SELECT source_domain, round(100.0 * count(*) FILTER (WHERE status IN ('SUCCESS','SKIPPED_DUPLICATE')) / NULLIF(count(*),0), 1) AS "Success %",
       count(*) AS "Attempts"
{F} AND status <> 'RUNNING' GROUP BY source_domain ORDER BY 1""", 0, 16, 12, 8, xfield="source_domain", multi=True),
    timeseries("Rows ingested over time", f"""
SELECT end_ts AS time, source_domain AS metric, rows_loaded AS value
{F} AND status = 'SUCCESS' ORDER BY 1""", 12, 16, 12, 8, draw="bars"),
    table("Ingestion run history", f"""
SELECT batch_id, refresh_id, source_domain, file_name, file_size_mb AS "size MB", rows_read, rows_loaded,
       rows_malformed, duration_s, rows_per_sec, status, start_ts, error_message
{F} ORDER BY batch_id DESC""", 0, 24, 24, 9, gradient_cols=("duration_s", "rows_loaded")),
], "Batch load duration, rows per batch and success rate for every landed file.",
    [refresh_var("ops.batch_load_log")])

# ============================================================== 2. STREAMING
S = "FROM ops.stream_metrics WHERE refresh_id IN ($refresh)"
stream = dashboard("de-stream-ingestion", "2 · Streaming Ingestion (E-commerce Events → Bronze)", [
    text("**Streaming ingestion** - e-commerce sales events are replayed as timed micro-batches through the "
         "Ingestion API. Latency = database insert time − event creation time. Malformed events are counted in a "
         "try/except and sent to `ops.stream_dead_letter`.", 0, 0, 24, 2),
    stat("Events ingested", f"SELECT COALESCE(sum(events_loaded),0) {S}", 0, 2),
    stat("Overall throughput", f"SELECT sum(events_loaded) / NULLIF(extract(epoch FROM max(batch_end_ts) - min(batch_start_ts)),0) {S}",
         4, 2, unit="eps", decimals=1, desc="Total records ingested / total stream duration"),
    stat("Avg end-to-end latency", f"SELECT avg(avg_latency_s) {S}", 8, 2, unit="s", decimals=3),
    stat("P95 latency", f"SELECT avg(p95_latency_s) {S}", 12, 2, unit="s", decimals=3),
    stat("Malformed / dropped", f"SELECT COALESCE(sum(events_malformed),0) {S}", 16, 2,
         thresholds=[{"color": "#1f60c4", "value": None}, {"color": "#ff9830", "value": 50}, {"color": "#c4162a", "value": 200}]),
    stat("Micro-batches", f"SELECT count(*) {S}", 20, 2),
    timeseries("Stream throughput (events / second)", f"""
SELECT batch_end_ts AS time, refresh_id AS metric, throughput_eps AS value {S} ORDER BY 1""", 0, 6, 16, 10, unit="eps",
               desc="Recommended chart: pipeline stability over the simulation run."),
    pie("Event outcome", f"""
SELECT 'Loaded' AS outcome, sum(events_loaded) AS events {S}
UNION ALL SELECT 'Malformed / dropped', sum(events_malformed) {S}""", 16, 6, 8, 10),
    timeseries("End-to-end latency per micro-batch (s)", f"""
SELECT batch_end_ts AS time, 'avg latency' AS metric, avg_latency_s AS value {S}
UNION ALL SELECT batch_end_ts, 'p95 latency', p95_latency_s {S}
UNION ALL SELECT batch_end_ts, 'max latency', max_latency_s {S} ORDER BY 1""", 0, 16, 12, 9, unit="s"),
    bar("Dropped events by reason", """
SELECT split_part(reason, ':', 1) || CASE WHEN position(':' in reason) > 0 THEN ' (' || split_part(reason, ':', 2) || ')' ELSE '' END AS reason,
       count(*) AS events
FROM ops.stream_dead_letter WHERE refresh_id IN ($refresh) GROUP BY 1 ORDER BY 2 DESC""", 12, 16, 12, 9, horizontal=True, legend=False),
    table("Micro-batch log", f"""
SELECT refresh_id, micro_batch_no, batch_start_ts, batch_end_ts, events_received, events_loaded, events_malformed,
       throughput_eps, round(avg_latency_s, 4) AS avg_latency_s, round(p95_latency_s, 4) AS p95_latency_s
{S} ORDER BY id DESC""", 0, 25, 24, 8, gradient_cols=("throughput_eps",)),
], "Throughput, end-to-end latency and malformed-event counts of the e-commerce event stream.",
    [refresh_var("ops.stream_metrics")])

# ============================================================== 3. STORAGE & STAGING
LATEST = """(SELECT DISTINCT ON (schema_name, table_name) * FROM ops.storage_snapshot
             ORDER BY schema_name, table_name, captured_at DESC)"""
storage = dashboard("de-storage-staging", "3 · Storage & Staging (Bronze → Silver → Gold)", [
    text("**Storage & staging** - row counts, physical size and date coverage of every layer after each refresh, "
         "plus the data-quality results of the bronze → silver validation (rejected rows go to `quarantine`).", 0, 0, 24, 2),
    stat("Rows in gold facts", f"SELECT sum(row_count) FROM {LATEST} s WHERE layer = 'gold' AND table_name LIKE 'fact%'", 0, 2),
    stat("Rows in silver (ODS)", f"SELECT sum(row_count) FROM {LATEST} s WHERE layer = 'silver'", 4, 2),
    stat("Database size", "SELECT db_size_mb FROM ops.database_size_log ORDER BY id DESC LIMIT 1", 8, 2, unit="decmbytes", decimals=0),
    stat("Sales date coverage (days)", f"SELECT max_date - min_date FROM {LATEST} s WHERE table_name = 'fact_pharmacy_sales'", 12, 2,
         desc="MAX(date) − MIN(date) of the gold sales fact; must span 2023–2025 plus refresh months"),
    stat("Months covered", f"SELECT distinct_months FROM {LATEST} s WHERE table_name = 'fact_pharmacy_sales'", 16, 2),
    stat("Rows in quarantine", "SELECT count(*) FROM quarantine.rejected_rows", 20, 2,
         thresholds=[{"color": "#1f60c4", "value": None}, {"color": "#ff9830", "value": 5000}]),
    pie("Storage breakdown - bronze rows by source domain", f"""
SELECT source_domain, row_count FROM {LATEST} s WHERE layer = 'bronze' ORDER BY row_count DESC""", 0, 6, 8, 10,
        desc="Recommended chart: share of the 7 source domains"),
    pie("Storage footprint (MB) by layer", f"""
SELECT layer, round(sum(size_mb), 1) AS mb FROM {LATEST} s GROUP BY layer ORDER BY 2 DESC""", 8, 6, 8, 10, unit="decmbytes"),
    bar("Gold & silver row counts by refresh", """
SELECT refresh_id, sum(row_count) FILTER (WHERE layer = 'silver') AS "silver rows",
       sum(row_count) FILTER (WHERE layer = 'gold' AND table_name LIKE 'fact%') AS "gold fact rows"
FROM (SELECT DISTINCT ON (refresh_id, table_name) * FROM ops.storage_snapshot ORDER BY refresh_id, table_name, captured_at DESC) x
GROUP BY refresh_id ORDER BY 1""", 16, 6, 8, 10, xfield="refresh_id", multi=True),
    bar("Rows per table (latest)", f"""
SELECT schema_name || '.' || table_name AS table, row_count AS rows FROM {LATEST} s
WHERE row_count > 0 ORDER BY row_count DESC""", 0, 16, 12, 11, horizontal=True, legend=False),
    bar("Data-quality rejects by rule", """
SELECT source_domain || ': ' || rule_name AS rule, sum(failed_rows) AS rejected
FROM ops.dq_results WHERE refresh_id IN ($refresh) AND failed_rows > 0
GROUP BY 1 ORDER BY 2 DESC""", 12, 16, 12, 11, horizontal=True, legend=False),
    table("Date coverage & footprint per table (latest snapshot)", f"""
SELECT layer, schema_name, table_name, source_domain, row_count, size_mb, min_date, max_date, distinct_months, captured_at
FROM {LATEST} s ORDER BY CASE layer WHEN 'bronze' THEN 1 WHEN 'silver' THEN 2 WHEN 'quarantine' THEN 3 WHEN 'gold' THEN 4 ELSE 5 END,
         row_count DESC""", 0, 27, 24, 10, gradient_cols=("row_count", "size_mb")),
], "Row counts, storage footprint, date coverage and data quality across the medallion layers.",
    [refresh_var("ops.dq_results")])

# ============================================================== 4. QUERY LAYER
Q = "FROM ops.query_log WHERE refresh_id IN ($refresh)"
query = dashboard("de-query-layer", "4 · Analytical Query Layer & Optimization", [
    text("**Query layer** - every analytical query of the SDI mart runs in two forms: *baseline* (star-schema joins "
         "on the fact tables) and *optimized* (materialized views + indexes + partition pruning). Both return identical "
         "results; execution time comes from `EXPLAIN ANALYZE`, call time from a `time.perf_counter()` wrapper.", 0, 0, 24, 2),
    stat("Queries executed (today)", "SELECT count(*) FROM ops.query_log WHERE executed_at::date = current_date", 0, 2,
         desc="Daily active query count from the benchmark/dashboard refreshes"),
    stat("Avg baseline time", f"SELECT avg(server_ms) {Q} AND variant = 'baseline'", 4, 2, unit="ms", decimals=1),
    stat("Avg optimized time", f"SELECT avg(server_ms) {Q} AND variant = 'optimized'", 8, 2, unit="ms", decimals=1),
    stat("Median speed-up", f"""
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY b / NULLIF(o, 0)) FROM (
  SELECT query_id, avg(server_ms) FILTER (WHERE variant = 'baseline') AS b,
         avg(server_ms) FILTER (WHERE variant = 'optimized') AS o {Q} GROUP BY query_id) x""", 12, 2, unit="x", decimals=1),
    stat("Query error rate", f"SELECT 100.0 * count(*) FILTER (WHERE status = 'FAILED') / NULLIF(count(*),0) {Q}", 16, 2,
         unit="percent", decimals=2,
         thresholds=[{"color": "#1f60c4", "value": None}, {"color": "#ff9830", "value": 1}, {"color": "#c4162a", "value": 5}]),
    stat("Live dashboard queries", "SELECT COALESCE(sum(calls),0) FROM pg_stat_statements s JOIN pg_roles r ON r.oid = s.userid WHERE r.rolname = 'grafana_reader'",
         20, 2, desc="Queries Grafana itself has sent (pg_stat_statements)"),
    bar("Average query execution time per dashboard widget / KPI card (ms)", f"""
SELECT query_id || ' ' || split_part(widget, ' (', 1) AS widget,
       round(avg(server_ms) FILTER (WHERE variant = 'baseline'), 2) AS "baseline (star joins)",
       round(avg(server_ms) FILTER (WHERE variant = 'optimized'), 2) AS "optimized (MV + index)"
{Q} AND status = 'SUCCESS' GROUP BY query_id, widget ORDER BY query_id""", 0, 6, 16, 11, unit="ms", multi=True,
        desc="Recommended chart: identifies unoptimised joins / missing indexes"),
    bar("Speed-up factor per query (x)", f"""
SELECT query_id, round(avg(server_ms) FILTER (WHERE variant = 'baseline') / NULLIF(avg(server_ms) FILTER (WHERE variant = 'optimized'), 0), 1) AS "speed-up x"
{Q} GROUP BY query_id ORDER BY 1""", 16, 6, 8, 11, unit="x", legend=False, xfield="query_id"),
    timeseries("Execution time per run (ms)", f"""
SELECT executed_at AS time, variant AS metric, server_ms AS value {Q} AND status = 'SUCCESS' ORDER BY 1""", 0, 17, 12, 9, unit="ms",
               draw="points"),
    table("Slowest statements sent by Grafana (pg_stat_statements)", """
SELECT left(regexp_replace(query, '\\s+', ' ', 'g'), 120) AS statement, calls, round(mean_exec_time::numeric, 2) AS mean_ms,
       round(max_exec_time::numeric, 2) AS max_ms, rows
FROM pg_stat_statements s JOIN pg_roles r ON r.oid = s.userid
WHERE r.rolname = 'grafana_reader' ORDER BY mean_exec_time DESC LIMIT 15""", 12, 17, 12, 9, gradient_cols=("mean_ms",)),
    table("Query catalogue - latest benchmark", f"""
SELECT DISTINCT ON (query_id, variant) query_id, query_name, widget, variant, round(server_ms, 2) AS server_ms,
       round(client_ms, 2) AS client_ms, rows_returned, status, refresh_id, executed_at
{Q} ORDER BY query_id, variant, executed_at DESC""", 0, 26, 24, 10, gradient_cols=("server_ms",)),
], "Query execution time, query volume and error rate of the SDI analytical query layer.",
    [refresh_var("ops.query_log")])

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, d in [("01_batch_ingestion", batch), ("02_stream_ingestion", stream),
                    ("03_storage_staging", storage), ("04_query_layer", query)]:
        (OUT / f"{name}.json").write_text(json.dumps(d, indent=2), encoding="utf-8")
        print("written", name, len(d["panels"]), "panels")
