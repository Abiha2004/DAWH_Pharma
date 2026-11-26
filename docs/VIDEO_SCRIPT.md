# Demo video script - Phase 1 (max 5:00)

**Before recording:** run `docker compose up -d` and do the **R0** initial load, so history is
already in the DWH. Open four browser tabs: Airflow, Grafana (Batch dashboard), Grafana (Storage
dashboard) and the GitHub repo. Record the screen with OBS or the Xbox Game Bar (Win + G).
Speak while you click; aim for about 4:40.

| Time | Show on screen | Say (short version) |
|---|---|---|
| 0:00–0:25 | Repo README + architecture image | "We built the Sales & Demand Intelligence mart as an automated pipeline: Airflow orchestrates, PostgreSQL holds bronze, silver and gold layers, Grafana shows four data-engineering dashboards." |
| 0:25–0:55 | Airflow DAG list, then the **Graph** of `sdi_04_gold_transform` | "Six DAGs, one per layer. They are chained with Airflow Datasets, so each layer starts automatically when the one below publishes data. DAG files only orchestrate; the logic is in src/ and sql/." |
| 0:55–1:30 | Trigger `sdi_00_data_generator` with `{"refresh_id": "R1"}`. Show `data/landing` filling up and the file-watcher log (`docker compose logs -f file-watcher`) | "Refresh 1 generates January 2026 for all 7 sources: the same month last year, shifted 12 months, plus 8% growth and 1% deliberately dirty rows. The file-watcher detects each file and triggers batch ingestion through the Airflow REST API." |
| 1:30–2:10 | Grafana **1 · Batch Ingestion**, then **2 · Streaming** while `sdi_02` runs | "Each file is one mapped task. The sales file is the slowest. The broken lines are counted, not loaded. Meanwhile e-commerce sales stream through our FastAPI endpoint at about 200 events per second with sub-second latency; malformed events go to a dead-letter table." |
| 2:10–2:50 | Airflow grid: `sdi_03` → `sdi_04` → `sdi_05` turning green. Grafana **3 · Storage & Staging** (DQ panel, rows per layer) | "Validation applies 37 data-quality rules. About 1.2% of rows are quarantined with the reason. The quality gate would stop the load above 5%. Then the gold layer loads incrementally, dimensions first." |
| 2:50–3:30 | Trigger **R2**. While it runs, open the SDI.Excel **SCD.Design** sheet | "Refresh 2 is February. It also changes three medicine prices or statuses and renames a pharmacy. The SCD Type 2 dimensions create new versions. January sales keep the old price and February sales get the new one." |
| 3:30–4:10 | Grafana **4 · Query Layer** + Storage dashboard after R2 (date coverage now Feb-2026) | "Thirteen dimensional queries run in two forms. On the star schema they average 180 ms; on our materialized views 6 ms, up to 239 times faster, with identical results. Storage now covers January 2023 to February 2026: the history was never reloaded." |
| 4:10–4:40 | Quick scroll through SDI.Excel (Cover → Dim.Tables → Source.To.Target → Query.Catalog), then the report PDF | "The SDI workbook documents every attribute, the lineage, the data-quality rules, the pipeline metrics and the queries. The report explains the dashboards and the design. Thank you." |

**Tips**
* Use Grafana's time picker "Last 1 hour" during the demo so the time-series panels zoom in on the run.
* If a refresh takes longer than you want on camera, cut the waiting part. Keep the moment the dashboards change.
* Upload to YouTube as *Unlisted* (or Google Drive with "anyone with the link") and paste the link into README.md and the Excel Cover sheet.
