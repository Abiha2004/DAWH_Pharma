// Builds docs/Phase.1.Report.docx and docs/Oversampling.Report.docx (then converted to PDF).
// Run from repo root:  node docs/report_src/build_reports.js
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, ImageRun, Table, TableRow, TableCell, WidthType, ShadingType,
  AlignmentType, HeadingLevel, LevelFormat, BorderStyle, Footer, PageNumber, TabStopType,
} = require("docx");

const IMG = path.join(__dirname, "..", "img");
const W = 9638;                     // A4 content width in DXA with 2 cm margins
const FONT = "Arial";
const BODY = 22;                    // 11 pt
const SMALL = 18;                   // 9 pt for tables and captions

const p = (text, opts = {}) => new Paragraph({
  spacing: { after: opts.after ?? 100, before: opts.before ?? 0, line: 252 },
  alignment: opts.align,
  keepNext: opts.keepNext,
  children: (Array.isArray(text) ? text : [text]).map(t => typeof t === "string" ? new TextRun({ text: t, size: opts.size || BODY, font: FONT }) : t),
});
const b = (t, size = BODY) => new TextRun({ text: t, bold: true, size, font: FONT });
const r = (t, size = BODY) => new TextRun({ text: t, size, font: FONT });
const h1 = t => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(t)] });
const h2 = t => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(t)] });
const bullet = (parts, lvl = 0) => new Paragraph({
  numbering: { reference: "bul", level: lvl }, spacing: { after: 40, line: 252 },
  children: (Array.isArray(parts) ? parts : [parts]).map(t => typeof t === "string" ? r(t) : t),
});
const img = (file, widthPx, caption) => {
  const meta = require("child_process").execSync(`python3 -c "from PIL import Image; print(*Image.open('${path.join(IMG, file)}').size)"`).toString().trim().split(" ").map(Number);
  const h = Math.round(widthPx * meta[1] / meta[0]);
  const out = [new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 20 }, keepNext: true,
    children: [new ImageRun({ type: "png", data: fs.readFileSync(path.join(IMG, file)), transformation: { width: widthPx, height: h },
      altText: { title: caption, description: caption, name: file } })] })];
  if (caption) out.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 100 },
    children: [new TextRun({ text: caption, italics: true, size: SMALL, font: FONT, color: "555555" })] }));
  return out;
};
const border = { style: BorderStyle.SINGLE, size: 4, color: "A6A6A6" };
const borders = { top: border, bottom: border, left: border, right: border };
const table = (headers, rows, widths) => new Table({
  width: { size: W, type: WidthType.DXA }, columnWidths: widths,
  rows: [headers, ...rows].map((row, i) => new TableRow({
    tableHeader: i === 0, cantSplit: true,
    children: row.map((cell, j) => new TableCell({
      width: { size: widths[j], type: WidthType.DXA }, borders,
      shading: i === 0 ? { type: ShadingType.CLEAR, fill: "FCE4D6", color: "auto" } : undefined,
      margins: { top: 30, bottom: 30, left: 80, right: 80 },
      children: [new Paragraph({ spacing: { after: 0, line: 240 },
        children: [new TextRun({ text: String(cell), size: SMALL, font: FONT, bold: i === 0 })] })],
    })),
  })),
});

const styles = {
  default: { document: { run: { font: FONT, size: BODY } } },
  paragraphStyles: [
    { id: "Title", name: "Title", basedOn: "Normal", run: { font: FONT, size: 34, bold: true, color: "1F3864" },
      paragraph: { spacing: { before: 0, after: 60 }, keepNext: true } },
    { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
      run: { font: FONT, size: 26, bold: true, color: "1F3864" }, paragraph: { spacing: { before: 160, after: 80 }, keepNext: true, outlineLevel: 0 } },
    { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
      run: { font: FONT, size: 23, bold: true, color: "2F5597" }, paragraph: { spacing: { before: 100, after: 60 }, keepNext: true, outlineLevel: 1 } },
  ],
};
const numbering = { config: [{ reference: "bul", levels: [
  { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 360, hanging: 240 } } } },
  { level: 1, format: LevelFormat.BULLET, text: "–", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 240 } } } }] }] };
const footer = label => new Footer({ children: [new Paragraph({ alignment: AlignmentType.RIGHT,
  children: [new TextRun({ text: `${label}  ·  page `, size: 16, font: FONT, color: "777777" }),
             new TextRun({ children: [PageNumber.CURRENT], size: 16, font: FONT, color: "777777" })] })] });
const section = (label, children) => ({
  properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1134, bottom: 1134, left: 1134, right: 1134 } } },
  footers: { default: footer(label) }, children,
});

// ============================================================================ PHASE 1 REPORT
const report = [
  new Paragraph({ style: "Title", children: [new TextRun("Phase.1.Report - Sales & Demand Intelligence (SDI) Data Mart")] }),
  p([r("Data Analytics and Warehousing, Fall 2026  ·  Automated DWH + BI pipeline for a pharmaceutical cold supply chain  ·  "),
     b("Group: "), r("<member 1>, <member 2>, <member 3>  ·  "), b("Repository: "), r("<GitHub URL>")], { size: 20, after: 120 }),

  h1("1. What we built and how it fits together"),
  p("Phase 1 implements the SDI mart up to the analytical query layer as a fully automated, event-driven pipeline. "
    + "Apache Airflow orchestrates six DAGs. PostgreSQL 16 holds every layer in a medallion layout: bronze (raw files as received), "
    + "silver (validated operational data store), gold (the star schema), mart (materialized views) and ops (pipeline metrics). "
    + "Grafana reads the ops schema for the four data-engineering dashboards. Everything starts with one docker compose up."),
  ...img("architecture.png", 640, "Figure 1 - Pipeline architecture: each DAG publishes an Airflow Dataset that triggers the next layer; every component writes metrics to ops.*"),
  p([b("Why PostgreSQL for the mart (instead of DuckDB): "), r("one engine gives us range partitioning, BRIN and B-tree indexes, materialized views "
    + "with concurrent refresh, pg_stat_statements for real query telemetry and a native Grafana data source. "
    + "The star schema is engine-agnostic SQL and can be mirrored to DuckDB later.")]),

  h1("2. Design approach of the Airflow pipeline"),
  table(["DAG", "Layer", "Triggered by", "What it does"], [
    ["sdi_00_data_generator", "Oversampling", "Manual: refresh R0 / R1 / R2", "Initialises the DB (idempotent), writes one new month of data for all 7 sources into data/landing, then triggers the stream DAG"],
    ["sdi_01_batch_ingestion", "Batch ingestion", "File-watcher REST call per file, plus a 5-min safety scan", "Dynamic task mapping (one task per file): claim file → SHA-256 de-dup → pandas parse → COPY to bronze → ops.batch_load_log"],
    ["sdi_02_stream_ingestion", "Streaming ingestion", "sdi_00", "Replays e-commerce events in micro-batches of 250 through the FastAPI ingestion API → ops.stream_metrics"],
    ["sdi_03_silver_validation", "Storage & staging", "Dataset BRONZE", "Safe casts, 37 DQ rules, duplicate checks → silver / quarantine; quality gate (>5% rejects blocks gold)"],
    ["sdi_04_gold_transform", "Dimensional model", "Dataset SILVER", "SCD2 medicine → SCD2 organization → conformed + junk dims → 3 facts in parallel (incremental by watermark)"],
    ["sdi_05_query_layer", "Query layer", "Dataset GOLD", "Indexes + 6 materialized views (refreshed concurrently) → 13-query benchmark → storage metrics → close refresh"],
  ], [2400, 1300, 1950, 3988]),
  p("", { after: 40 }),
  bullet([b("Modular: "), r("DAG files only orchestrate. All logic lives in src/ and sql/, so every task is unit-sized and can also run without Airflow (python -m src.pipeline refresh R1).")]),
  bullet([b("Event-driven, not time-driven: "), r("Airflow Datasets chain bronze → silver → gold → query. Short-circuit tasks stop empty runs from triggering downstream layers.")]),
  bullet([b("Idempotent and incremental: "), r("Files are de-duplicated by SHA-256 and claimed by atomic rename, so the watcher and the scan can never double-load. Silver uses ON CONFLICT and gold loads only batches above a watermark. The DWH is appended to, never emptied.")]),
  bullet([b("Reliable: "), r("Tasks retry twice with exponential back-off, with a 30-minute timeout, a 20-minute SLA and failure callbacks. max_active_runs = 1 on layers that must be serialised. The quality gate has no retries.")]),

  h1("3. Data oversampling and dimensional model (summary)"),
  p("Each refresh synthesises one calendar month of all 7 sources by time-shifting the same month of 2025 by +12 months (seasonal bootstrap). "
    + "Sales are resampled at +8% year-on-year growth and lineage is kept by re-keying lots, batches and shipments consistently. "
    + "Refresh 1 (Jan-2026) produced 102,756 sales, mirroring the real January respiratory peak; refresh 2 (Feb-2026) produced 8,395. "
    + "About 1% dirty rows, 0.2% duplicate keys and broken CSV lines were injected on purpose. Details are in Oversampling.Report.pdf."),
  p("The gold layer is a fact constellation: FactPharmacySales (transaction grain, year-partitioned), FactHospitalDemand "
    + "(monthly snapshot) and FactInventorySnapshot (daily snapshot, the warehouse context). They share conformed Date "
    + "(role-playing ×5), Medicine (SCD2), Organization (SCD2, role-playing), Channel, Warehouse, Batch/Lot and Disease dimensions, "
    + "plus two junk dimensions. Refresh 2 changed three medicine prices or statuses and renamed one pharmacy. The pipeline created "
    + "exactly 3 and 1 new versions, and January sales still point to version 1. All attributes, the lineage and the evidence are in SDI.Excel.xlsx."),
  ...img("star_schema.png", 560, "Figure 2 - SDI fact constellation in the gold layer (green = conformed, blue = junk dimensions)"),

  h1("4. The data-engineering dashboards and the story they tell"),
  p("Four Grafana dashboards auto-refresh every 10 seconds, so the live demo shows them moving while the DAGs run. "
    + "All use light-to-dark gradient colours for magnitude and a Z-pattern layout: KPI cards across the top, the recommended chart top-left, "
    + "detail tables at the bottom. A Refresh filter compares R0, R1 and R2. The figures below are rendered from the same ops tables the dashboards query."),
  h2("4.1 Batch ingestion dashboard"),
  ...img("fig_batch_ingestion.png", 610, "Figure 3 - Load duration and rows per source file for the initial load (R0) and refreshes R1 and R2 (ops.batch_load_log)"),
  p("Metrics: load duration (end − start of read + parse + COPY), rows processed per batch (len(df)), malformed lines and batch success rate. "
    + "The story: the 500k-row sales file is the bottleneck (10.2 s at about 49k rows/s), while every other file loads in under 4 s. "
    + "The whole 934k-row history ingests in 16.6 s. Success rate is 100%. The R1 master file was identical to R0 and was skipped by "
    + "its SHA-256 hash, which is idempotency working, not a failure. 49 + 3 broken CSV lines were skipped and counted, never loaded."),
  h2("4.2 Streaming ingestion dashboard"),
  ...img("fig_streaming.png", 610, "Figure 4 - Throughput and end-to-end latency per micro-batch (ops.stream_metrics)"),
  p("Metrics: throughput = events loaded ÷ micro-batch window; end-to-end latency = database commit time − event creation time; "
    + "malformed or dropped events counted in a try/except. The story: throughput holds at about 185–225 events/s under bursty traffic "
    + "(±40% window jitter) with no backlog. The final R2 dip is simply the last, partly filled micro-batch. Average latency is about 0.6 s "
    + "(p95 about 1.1 s), bounded by the 1-second micro-batch buffer, so the stream is near real time. 55 malformed events "
    + "(truncated JSON, missing MedicineID, empty ID) went to a dead-letter table and none reached the DWH."),
  h2("4.3 Storage & staging dashboard"),
  ...img("fig_storage.png", 610, "Figure 5 - Storage breakdown by source domain and row counts per layer after each refresh (ops.storage_snapshot)"),
  p("Metrics: table row counts, storage footprint (pg_total_relation_size, pg_database_size), date coverage (MAX − MIN date, distinct months) "
    + "and data-quality rejects by rule. The story: sales and IoT are 86% of raw volume, which is why the sales fact is partitioned and the "
    + "snapshot fact uses BRIN. Each refresh adds rows to every layer and the history is never reloaded. The gold sales fact now spans "
    + "30 Jan 2023 to 28 Feb 2026 (38 months), proving full 2023–2025 coverage plus both refresh months. Quarantine holds 1,391 rejected rows, "
    + "matching the injected dirty data. The database is 1.07 GB."),
  h2("4.4 Analytical query layer dashboard"),
  ...img("fig_query_layer.png", 610, "Figure 6 - Median execution time per widget query: baseline vs optimized (ops.query_log, EXPLAIN ANALYZE)"),
  p("Metrics: query execution time (EXPLAIN ANALYZE plus a time.perf_counter wrapper), daily active query count, query error rate and live "
    + "Grafana query cost from pg_stat_statements. The story: the 13 dimensional queries (roll-up, slice/dice, drill-across, ROLLUP, CUBE, "
    + "window functions) average 180 ms on the star schema but 6.3 ms on the materialized views, a median speed-up of 37× and up to 239×. "
    + "Every widget is sub-second and the error rate is 0%. Gains are small for Q04, Q05 and Q07 because the demand fact has only 28k rows. "
    + "Correctness is proven: baseline and optimized results have identical checksums for all 13 queries."),

  h1("5. Data quality evidence"),
  table(["Refresh", "Rows validated", "Rejected to quarantine", "Main reasons"], [
    ["R0 (2023–2025)", "640,024", "0", "History is internally consistent"],
    ["R1 (Jan-2026)", "108,063", "1,249 (1.16%)", "Injected: negative price, unknown MED999, invalid date, zero quantity, duplicates"],
    ["R2 (Feb-2026)", "13,314", "142 (1.07%)", "Same rule mix; quality gate (5%) passed"],
  ], [1700, 1800, 2200, 3938]),
  p("", { after: 40 }),

  h1("6. Assumptions"),
  bullet("Scope: files 01, 02, 04, 05 and 06 are modelled in Phase 1. Files 03 and 07 are ingested to bronze (the dashboards cover all 7 domains) and modelled in Phases 2 and 3."),
  bullet("Channel: the source only has Physical Pharmacy / E-commerce. Physical sales at PharmacyType = Hospital Pharmacy form the Hospital-Pharmacy channel."),
  bullet("The inventory snapshot fact is loaded now as warehouse context for SDI and becomes the core of the Phase 2 mart."),
  bullet("Refresh calendar: R0 = history, R1 = Jan-2026, R2 = Feb-2026; R3–R6 are reserved for Phases 2 and 3. YoY growth is +8% (actual 2024→25 growth was +9.1%)."),
  bullet("Streaming: up to 4,000 e-commerce sales per refresh arrive as events, and the rest arrive in the daily POS batch file. Latency uses one host clock."),
  bullet("SCD2: first versions are effective from 1900-01-01 so all history maps to them. Unknown members (key −1) exist in every dimension; 0 facts needed one."),
  bullet("Default passwords are for local demonstration only. Grafana connects with a read-only role."),
];

// ============================================================================ OVERSAMPLING REPORT
const over = [
  new Paragraph({ style: "Title", children: [new TextRun("Data Oversampling Layer - Methodology & Statistics")] }),
  p([r("SDI Phase 1  ·  DAG "), b("sdi_00_data_generator"), r("  ·  code: src/generator/oversample.py  ·  config: config/pipeline.yaml")], { size: 20, after: 120 }),
  h1("1. Goal"),
  p("Simulate a live 24/7 supply chain so the warehouse and dashboards visibly change. Each refresh appends one new, realistic month "
    + "of data for all 7 source files to the existing history. Nothing is truncated: the DWH keeps 2023–2025 and grows by one month per refresh."),
  h1("2. Method: seasonal bootstrap with time-shift"),
  bullet([b("Template = same month last year. "), r("For refresh month M the generator copies the slice of each source for M − 12 months (R1 = Jan-2026 from Jan-2025, R2 = Feb-2026 from Feb-2025). "
    + "This preserves seasonality, which is strong here: Dec/Jan is a respiratory peak with 95k sales in Jan-2025 against about 7k in a normal month.")]),
  bullet([b("Shift +12 months, re-key. "), r("All dates move forward a year on the same day of the month. Natural keys get a refresh tag (SALE-R1-000000001, WHLOT-0000658-R1), so new rows never collide with history and each stays traceable to its template.")]),
  bullet([b("Keep lineage. "), r("One mapping per refresh re-keys lots, production batches and shipments, so sale → lot → batch → shipment and IoT → shipment links stay valid.")]),
  bullet([b("Grow and vary sales. "), r("Rows are resampled with replacement to template × 1.08 (2025 grew 9.1% over 2024; we use a conservative 8%), with random time-of-day jitter. "
    + "Money columns are recomputed from the current master price list, so SCD Type 2 price changes reach revenue.")]),
  bullet([b("Keep hospital demand consistent. "), r("Flow quantities are scaled per row by N(1.04, 0.06), and closing stock is recomputed so that Opening + Received − Dispensed − Wastage = Closing still holds.")]),
  bullet([b("SCD demonstration. "), r("In R2 the master data raises the price of MED001 (+10%) and MED010 (+12%) and sets MED055 to 'Under review', and pharmacy PHR014 is rebranded.")]),
  bullet([b("Dirty data on purpose. "), r("1% invalid rows (negative price, unknown medicine, invalid date, zero quantity, broken totals, empty ID), 0.2% duplicate keys, 0.05% broken CSV lines and 1% malformed stream events. These prove the validation layer works.")]),
  bullet([b("Batch vs stream. "), r("E-commerce sales (up to 4,000 per refresh) are written as a JSONL event stream; all other rows land as CSV files that the file-watcher picks up.")]),
  h1("3. Why this much data"),
  p("One month per refresh is the natural unit of a monthly business cycle. It is large enough to move every KPI and chart, and small enough to "
    + "load in about one minute. The January peak makes R1 a stress test (~100k sales, about 20% of all history in one batch), and February is a "
    + "normal month. The DWH is therefore refreshed twice with two very different loads. The calendar continues for later phases: "
    + "R3–R4 (Mar–Apr 2026) for the Phase 2 mart and R5–R6 (May–Jun 2026) for Phase 3, so every mart and the final constellation are refreshed twice."),
  h1("4. Statistics of the generated data"),
  table(["Source file", "R1 rows (Jan-2026)", "R2 rows (Feb-2026)", "Injected issues R1 / R2"], [
    ["01 master data (full snapshot)", "63 (identical → skipped)", "63 (3 SCD changes)", "- / -"],
    ["02 manufacturing", "25", "15", "- / -"],
    ["03 manufacturer delivery", "34", "43", "- / -"],
    ["04 warehouse snapshots", "4,366", "4,084", "44 dirty + 9 dup / 41 + 8"],
    ["05 sales - batch file", "98,954 (+49 broken lines)", "6,880 (+3 broken lines)", "988 dirty + 198 dup / 69 + 14"],
    ["05 sales - e-commerce stream", "4,000 events", "1,529 events", "40 / 15 malformed events"],
    ["06 hospital demand", "758 (756 + 2 dup)", "758 (756 + 2 dup)", "8 dirty + 2 dup / 8 + 2"],
    ["07 IoT readings", "8,622", "8,650", "- / -"],
    ["Total rows generated", "116,822", "22,022", "1,338 / 160"],
  ], [2600, 2300, 2300, 2438]),
  p("", { after: 60 }),
  p([b("Check: "), r("expected sales = template × growth = 95,144 × 1.08 = 102,756 (R1) and 7,773 × 1.08 = 8,395 (R2). Generated: 98,954 − 198 duplicates + 4,000 stream = 102,756 and 6,880 − 14 + 1,529 = 8,395. "
    + "The difference is 0, and the SDI.Excel Oversampling sheet recomputes it with live formulas.")]),
  p([b("Effect in the DWH: "), r("the gold sales fact grew from 500,000 → 601,728 → 610,039 rows, hospital demand from 27,216 → 27,964 → 28,712, "
    + "and inventory snapshots from 112,184 → 116,497 → 120,532. 1,249 (R1) and 142 (R2) rows were quarantined; 0 history rows were touched.")]),
  ...img("fig_oversampling_monthly.png", 620, "Figure 1 - Monthly sales transactions: the generated months continue the seasonal pattern (Jan peak, normal Feb)"),
];

async function build() {
  const outDir = path.join(__dirname, "..");
  for (const [name, children, label] of [["Phase.1.Report", report, "Phase.1.Report · SDI"],
                                          ["Oversampling.Report", over, "Oversampling Report · SDI"]]) {
    const doc = new Document({ creator: "SDI team", title: name, styles, numbering, sections: [section(label, children)] });
    fs.writeFileSync(path.join(outDir, `${name}.docx`), await Packer.toBuffer(doc));
    console.log("written", name);
  }
}
build();
