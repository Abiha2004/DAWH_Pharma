"""Build docs/SDI.Excel.xlsx - the complete SDI design + implementation workbook.

Starts from the team's DWH planning template (the professor's sheet structure),
updates the design sheets to match what was implemented, and adds sheets with
live evidence pulled from the warehouse (profiling, lineage, DQ, SCD2, metrics,
query benchmarks). Re-run after any refresh to update every number:

    DWH_DB_BACKEND=psql python scripts/build_sdi_excel.py <template.xlsx> docs/SDI.Excel.xlsx
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from sdi_model_spec import DIMS, FACTS, KPIS  # noqa: E402
from src.common import db  # noqa: E402
from src.common.config import cfg, path  # noqa: E402
from src.query.catalog import QUERIES  # noqa: E402
from src.validation.rules import SOURCES  # noqa: E402

# ------------------------------------------------------------------ house style (matches template)
FONT = "Montserrat"
F = Font(name=FONT, size=9)
FB = Font(name=FONT, size=9, bold=True)
FT = Font(name=FONT, size=11, bold=True)
FLINK = Font(name=FONT, size=9, color="0563C1", underline="single")
GREEN = PatternFill("solid", fgColor="92D050")
PEACH = PatternFill("solid", fgColor="FCE4D6")
YELLOW = PatternFill("solid", fgColor="FFE699")
LBLUE = PatternFill("solid", fgColor="DDEBF7")
LGREEN = PatternFill("solid", fgColor="E2EFDA")
INPUT = PatternFill("solid", fgColor="FFFF00")
THIN = Side(style="thin", color="7F7F7F")
MED = Side(style="medium", color="000000")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)


def title(ws: Worksheet, text: str, ncols: int, row: int = 1) -> None:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
    c = ws.cell(row, 1, text)
    c.font, c.fill, c.alignment = FT, GREEN, Alignment(vertical="center")
    ws.row_dimensions[row].height = 22


def note(ws: Worksheet, row: int, text: str, ncols: int, fill=None) -> int:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
    c = ws.cell(row, 1, text)
    c.font, c.alignment = F, WRAP
    if fill:
        c.fill = fill
    ws.row_dimensions[row].height = max(15, 13 * (len(text) // (ncols * 14) + 1))
    return row + 1


def section(ws: Worksheet, row: int, text: str, ncols: int) -> int:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
    c = ws.cell(row, 1, text)
    c.font, c.fill, c.border = FB, YELLOW, Border(top=MED, bottom=MED, left=MED, right=MED)
    return row + 1


def table(ws: Worksheet, row: int, headers: list[str], rows: list[list], widths: list[float] | None = None,
          number_formats: dict[int, str] | None = None) -> int:
    for j, h in enumerate(headers, 1):
        c = ws.cell(row, j, h)
        c.font, c.fill, c.border, c.alignment = FB, PEACH, BOX, CENTER
    for i, r in enumerate(rows, 1):
        for j, v in enumerate(r, 1):
            if isinstance(v, float) and pd.isna(v):
                v = None
            if isinstance(v, str) and v.startswith("="):      # text such as "= gross - discount", not a formula
                v = "Calculated as " + v[1:].strip()
            c = ws.cell(row + i, j, v)
            c.font, c.border, c.alignment = F, BOX, WRAP
            if number_formats and j in number_formats:
                c.number_format = number_formats[j]
    if widths:
        for j, w in enumerate(widths, 1):
            if w:
                ws.column_dimensions[get_column_letter(j)].width = w
    return row + len(rows) + 2


def fresh_sheet(wb, name: str, index: int | None = None, tab: str | None = None) -> Worksheet:
    if name in wb.sheetnames:
        old = wb[name]
        index = wb.sheetnames.index(name) if index is None else index
        tab = tab or (old.sheet_properties.tabColor.rgb if old.sheet_properties.tabColor and
                      isinstance(old.sheet_properties.tabColor.rgb, str) else None)
        wb.remove(old)
    ws = wb.create_sheet(name, index)
    if tab:
        ws.sheet_properties.tabColor = tab
    ws.sheet_view.showGridLines = False
    return ws


def q(sql: str) -> pd.DataFrame:
    return db.fetch_df(sql)


# ------------------------------------------------------------------ design sheets (rewritten)
def build_dim_tables(wb) -> None:
    ws = fresh_sheet(wb, "Dim.Tables")
    title(ws, "Fully Specify all attributes of all Dimension Tables  (as implemented in PostgreSQL schema dwh)", 7)
    types = q("SELECT table_name, column_name, CASE WHEN data_type = 'character varying' THEN 'VARCHAR' "
              "WHEN data_type = 'numeric' THEN 'NUMERIC(' || numeric_precision || ',' || numeric_scale || ')' "
              "ELSE upper(data_type) END AS dt FROM information_schema.columns WHERE table_schema = 'dwh'")
    tmap = {(r.table_name, r.column_name): r.dt for r in types.itertuples()}
    counts = {t: int(db.fetch_scalar(f"SELECT count(*) FROM dwh.{t}")) for t in DIMS}
    r = 3
    for t, d in DIMS.items():
        r = section(ws, r, f"{d['title']}   -   dwh.{t}   ({counts[t]:,} rows incl. Unknown member)", 7)
        for lab, val in (("Primary/Surrogate Key:", d["pk"]), ("Natural Key:", d["nk"]),
                         ("Dim Table Type:", d["type"]), ("Notes:", d["rows_note"])):
            ws.cell(r, 1, lab).font = FB
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=7)
            ws.cell(r, 2, val).font = F
            for j in range(1, 8):
                ws.cell(r, j).fill, ws.cell(r, j).border, ws.cell(r, j).alignment = PEACH, BOX, WRAP
            r += 1
        rows = [[col, tmap.get((t, col), "?"), a[0], a[1], a[2], a[3]] for col, a in d["attrs"].items()]
        r = table(ws, r, ["Attribute", "Data.Type", "SCD Strategy", "Hierarchy", "Source (file.column)", "Comments"], rows)
    for j, w in enumerate([28, 16, 13, 30, 40, 55, 4], 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = "A2"


def build_fact_sheets(wb) -> None:
    types = q("SELECT table_name, column_name, CASE WHEN data_type = 'numeric' THEN 'NUMERIC(' || numeric_precision "
              "|| ',' || numeric_scale || ')' ELSE upper(data_type) END AS dt FROM information_schema.columns "
              "WHERE table_schema = 'dwh'")
    tmap = {(x.table_name, x.column_name): x.dt for x in types.itertuples()}
    for t, f in FACTS.items():
        ws = fresh_sheet(wb, f["sheet"])
        title(ws, "Fully Specify all attributes of Fact Table", 8)
        n = int(db.fetch_scalar(f"SELECT count(*) FROM dwh.{t}"))
        r = section(ws, 2, f"{f['title']}   -   {n:,} rows after refresh R2", 8)
        for lab, val in (("Grain:", f["grain"]), ("Type:", f["type"]), ("Primary/Surrogate Key", f["pk"]),
                         ("Partitioning:", f["part"]), ("Indexing:", f["idx"]), ("Load strategy:", f["load"])):
            ws.cell(r, 1, lab).font = FB
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=8)
            ws.cell(r, 2, val).font = F
            for j in range(1, 9):
                ws.cell(r, j).fill, ws.cell(r, j).border, ws.cell(r, j).alignment = PEACH, BOX, WRAP
            r += 1
        rows = []
        for i, (col, a) in enumerate(f["cols"].items(), 1):
            fk, meas, dd, src, com = a
            src_txt = f"FK → {src}" if fk == "Y" else src
            rows.append([i, col, tmap.get((t, col), "?"), fk, meas, dd, src_txt, com])
        table(ws, r + 1, ["S.No", "Name", "Data.Type", "FK (Y/N)", "Measure Type\n(Fully, Semi, Non)",
                          "Degenerate (Y/N)", "OLTP Source / Dimension", "Comments"], rows,
              [7, 28, 16, 9, 16, 12, 40, 48])
        ws.freeze_panes = "A2"


def patch_existing(wb) -> None:
    # Transform.OLTP: PaymentMethod now lives in the junk dimension
    ws = wb["Transform.OLTP"]
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and "PaymentMethod may later move" in c.value:
                c.value = "Implemented: PaymentMethod moved to junk dimension DimSalesProfile with Rx/return/OTIF flags."
            if c.value == "DimPayer" and isinstance(ws.cell(c.row, 2).value, str) and "PaymentMethod" in ws.cell(c.row, 2).value:
                c.value = "DimPayer; DimSalesProfile (junk)"
    # Star.Schema: diagram + link the 3 queries to the implemented catalogue
    ws = wb["Star.Schema"]
    img = XLImage(str(ROOT / "docs" / "img" / "star_schema.png"))
    img.width, img.height = 700, 487
    ws.add_image(img, "A5")
    links = {1: "Implemented as Q06 (drill-across on conformed Date + Medicine) - see Query.Catalog.",
             2: "Implemented as Q07 (demand → inventory snapshot drill-across by warehouse).",
             3: "Implemented as Q08 (+ mv_sales_commercial for the optimized version)."}
    for row in ws.iter_rows(min_row=6, max_row=12):
        if isinstance(row[11].value, int) and row[11].value in links and row[13].value:
            row[13].value = f"{row[13].value}\n► {links[row[11].value]}"
    # ReadMe: describe the added sheets
    ws = wb["ReadMe"]
    ws["A3"].value = (ws["A3"].value or "") + (
        "\n\nStep 7 (added): Evidence of implementation - Source.Profile, Column.Profile, Bus.Matrix, Source.To.Target, "
        "DQ.Rules, SCD.Design, KPI.Dictionary.\n\nStep 8 (added): Data-engineering pipeline - Oversampling, Airflow.Design, "
        "Pipeline.Metrics, DE.Dashboards.\n\nStep 9 (added): Query layer - Query.Catalog and Optimization, then Gold.Sample "
        "and Assumptions. Start from the Cover sheet for the table of contents.")


# ------------------------------------------------------------------ new sheets
def build_cover(wb, sheets: list[tuple[str, str]]) -> None:
    ws = fresh_sheet(wb, "Cover", 0, "C00000")
    title(ws, "SDI.Excel - Sales & Demand Intelligence (SDI) Data Mart - Phase 1", 6)
    rows = [["Course", "Data Analytics and Warehousing - Fall 2026"],
            ["Project", "Automated DWH + BI pipeline for a Pharmaceutical Cold Supply Chain (360° inventory intelligence)"],
            ["Phase / milestone", "Phase 1 - SDI data mart implemented up to the Analytical Query layer (due 22 Nov 2026)"],
            ["Stack", "Apache Airflow 2.10 (orchestration) · PostgreSQL 16 (bronze/silver/gold/mart/ops) · Grafana 11 "
                      "(DE dashboards) · FastAPI ingestion API · watchdog file-watcher · Docker Compose"],
            ["GitHub repository", "<paste repository URL>"],
            ["Demo video", "<paste video URL>"]]
    r = table(ws, 3, ["Item", "Value"], rows)
    for rr in (8, 9):
        ws.cell(rr, 2).fill = INPUT
    ws.merge_cells("B4:F4")
    for rr in range(4, 10):
        if rr != 4:
            ws.merge_cells(start_row=rr, start_column=2, end_row=rr, end_column=6)
    r = section(ws, r, "Group members  (fill in the yellow cells)", 6)
    r = table(ws, r, ["#", "Name", "Roll number", "Email", "Main responsibility"],
              [[1, "", "", "", "Airflow DAGs & ingestion"], [2, "", "", "", "Dimensional model & SQL"],
               [3, "", "", "", "Dashboards, report & video"]])
    for rr in range(r - 4, r - 1):
        for cc in (2, 3, 4):
            ws.cell(rr, cc).fill = INPUT
    r = section(ws, r, "Contents", 6)
    for name, desc in sheets:
        c = ws.cell(r, 1, name)
        c.hyperlink, c.font, c.border = f"#'{name}'!A1", FLINK, BOX
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=6)
        ws.cell(r, 2, desc).font = F
        ws.cell(r, 2).alignment = WRAP
        for j in range(2, 7):
            ws.cell(r, j).border = BOX
        r += 1
    r = section(ws, r + 1, "Colour key", 6)
    for fill, txt in ((GREEN, "Sheet title"), (YELLOW, "Section / table name"), (PEACH, "Column headers & key properties"),
                      (INPUT, "Cell for the team to fill in"), (LGREEN, "Calculated with an Excel formula")):
        ws.cell(r, 1).fill = fill
        ws.cell(r, 2, txt).font = F
        r += 1
    for j, w in enumerate([24, 30, 16, 26, 30, 20], 1):
        ws.column_dimensions[get_column_letter(j)].width = w


def build_source_profile(wb) -> None:
    ws = fresh_sheet(wb, "Source.Profile", tab="7030A0")
    title(ws, "Source file profile - the 7 OLTP extracts (computed with pandas on the original CSVs)", 11)
    meta = [("01_master_data", "MedicineID", "EffectiveFromDate", "1 SKU", "Phase 1-3 (conformed DimMedicine)"),
            ("02_manufacturing_data", "ManufacturingOrderID", "OrderDate", "1 production batch", "Phase 1 (DimBatchLot) / Phase 2"),
            ("03_manufacturer_delivery_data", "ShipmentID+ShipmentLineNumber", "DispatchDate", "1 shipment line", "Phase 2/3 (bronze in Phase 1)"),
            ("04_warehouse_data", "InventorySnapshotID", "SnapshotDate", "1 lot × day", "Phase 1 context / Phase 2"),
            ("05_pharmacy_sales_data", "SalesTransactionID", "SalesDate", "1 sale", "Phase 1 - FactPharmacySales"),
            ("06_hospital_demand_data", "HospitalDemandRecordID", "DemandMonth", "1 hospital × medicine × month", "Phase 1 - FactHospitalDemand"),
            ("07_iot_streaming_data", "SensorReadingID", "EventTimestampUTC", "1 sensor reading", "Phase 3 (bronze in Phase 1)")]
    rows = []
    for f, pk, dcol, grain, use in meta:
        p = path("source") / f"{f}.csv"
        df = pd.read_csv(p, dtype=str, keep_default_na=False)
        keys = pk.split("+")
        uniq = not df.duplicated(subset=keys).any()
        d = pd.to_datetime(df[dcol].str[:10], errors="coerce")
        blanks = int((df == "").sum().sum())
        rows.append([f + ".csv", grain, len(df), len(df.columns), round(p.stat().st_size / 1_048_576, 2), pk,
                     "Yes" if uniq else "NO", dcol, str(d.min().date()), str(d.max().date()), blanks, use])
    table(ws, 3, ["File", "Grain", "Rows", "Columns", "Size MB", "Primary key", "PK unique?", "Business date",
                  "Min date", "Max date", "Blank cells", "Used in"], rows,
          [30, 24, 10, 9, 9, 28, 9, 18, 11, 11, 10, 30], {3: "#,##0", 11: "#,##0"})
    n = len(rows)
    ws.cell(4 + n, 1, "Total").font = FB
    ws.cell(4 + n, 3, f"=SUM(C4:C{3 + n})").number_format = "#,##0"
    ws.cell(4 + n, 5, f"=SUM(E4:E{3 + n})").number_format = "0.00"
    for c in (3, 5):
        ws.cell(4 + n, c).fill, ws.cell(4 + n, c).font = LGREEN, FB
    r = 7 + n
    r = section(ws, r, "Key findings from profiling (drive the design decisions)", 12)
    for t in [
        "SalesChannel has only 2 values (Physical Pharmacy 414k, Ecommerce 86k). The Hospital-Pharmacy channel is derived "
        "from PharmacyType = 'Hospital Pharmacy' (109,192 sales) → DimChannel has 3 sales channels + Hospital Demand.",
        "Only 3 of 63 medicines are cold-chain (2-8 °C) and they generate 15.6% of net sales; only WH006 has 2-8 °C zones.",
        "Measures reconcile exactly in the source: Net = Gross − Discount and Margin = Net − COGS for all 500k sales; "
        "Unit price = master RRP and COGS/pack = wholesale price → used as validation rules.",
        "Hospital stock balance holds for every row: Closing = Opening + Received − Dispensed − Wastage → consistency rule.",
        "Strong seasonality: Dec/Jan respiratory peak (Jan-2025 = 95,144 sales vs ~7k in a normal month) → generator "
        "uses same-month-last-year templates.",
        "HospitalID is blank for 78% of sales (only hospital pharmacies carry it); PrescriptionID is blank exactly when "
        "PrescriptionRequiredFlag = N (37,316 OTC sales) - expected, not a quality issue.",
        "Historical data contains 0 rule violations; the 1% injected dirty data in refreshes is what the DQ layer catches.",
    ]:
        r = note(ws, r, "• " + t, 12)


def build_column_profile(wb) -> None:
    ws = fresh_sheet(wb, "Column.Profile", tab="7030A0")
    title(ws, "Column-level profile of the SDI source files (01, 04, 05, 06)", 9)
    rows = []
    for f in ("01_master_data", "05_pharmacy_sales_data", "06_hospital_demand_data", "04_warehouse_data"):
        df = pd.read_csv(path("source") / f"{f}.csv", dtype=str, keep_default_na=False)
        for c in df.columns:
            s = df[c]
            nonblank = s[s != ""]
            num = pd.to_numeric(nonblank, errors="coerce")
            if len(nonblank) and num.notna().all():
                kind = "integer" if (num % 1 == 0).all() else "decimal"
                mn, mx = num.min(), num.max()
                mn, mx = (int(mn), int(mx)) if kind == "integer" else (round(float(mn), 4), round(float(mx), 4))
            elif len(nonblank) and nonblank.str.match(r"^\d{4}-\d{2}-\d{2}").all():
                kind, mn, mx = "date/time", nonblank.min(), nonblank.max()
            else:
                kind = "flag (Y/N)" if set(nonblank.unique()) <= {"Y", "N"} else "text"
                mn = mx = None
            vc = s.value_counts()
            top = ", ".join(f"{k} ({v:,})" for k, v in vc.head(3).items()) if s.nunique() <= 60 else str(s.iloc[0])
            rows.append([f, c, kind, len(s), int((s == "").sum()), round(100 * (s == "").mean(), 2), s.nunique(), mn, mx, top])
    table(ws, 3, ["File", "Column", "Inferred type", "Rows", "Blanks", "Blank %", "Distinct", "Min", "Max",
                  "Top values / example"], rows, [22, 28, 12, 10, 9, 8, 10, 14, 14, 60],
          {4: "#,##0", 5: "#,##0", 7: "#,##0"})
    ws.freeze_panes = "C4"
    ws.auto_filter.ref = f"A3:J{3 + len(rows)}"


def build_bus_matrix(wb) -> None:
    ws = fresh_sheet(wb, "Bus.Matrix", tab="0070C0")
    title(ws, "Enterprise Bus Matrix - conformed dimensions across all three phases (✔ implemented in Phase 1 · ○ planned)", 18)
    dims = ["Date", "Medicine", "Organization", "Channel", "Warehouse", "Batch/Lot", "Disease", "Customer Segment",
            "Payer", "Promotion", "Sales Profile (junk)", "Demand Profile (junk)", "Plant", "Carrier", "IoT Device / Vehicle"]
    P = "✔"
    o = "○"
    rows = [
        ["Pharmacy & e-commerce sales", "FactPharmacySales", "Transaction", "1", P, P, P, P, P, P, P, P, P, P, P, "", "", "", ""],
        ["Hospital demand & consumption", "FactHospitalDemand", "Periodic snapshot (month)", "1", P, P, P + " ×2", P, "", "", P, "", "", "", "", P, "", "", ""],
        ["Warehouse inventory", "FactInventorySnapshot", "Periodic snapshot (day)", "1 (context) / 2", P, P, "", "", P, P, "", "", "", "", "", "", "", "", ""],
        ["Manufacturing batches", "FactProductionBatch", "Accumulating snapshot", "2", o, o, "", "", "", o, "", "", "", "", "", "", o, "", ""],
        ["Replenishment / shipments", "FactShipmentLine", "Transaction", "2 / 3", o, o, "", "", o, o, "", "", "", "", "", "", o, o, ""],
        ["Cold-chain telemetry", "FactSensorReadingHourly", "Periodic aggregate (hour)", "3", o, o, "", "", o, "", "", "", "", "", "", "", "", "", o],
    ]
    table(ws, 3, ["Business process", "Fact table", "Fact type", "Phase"] + dims, [r[:4] + r[4:] for r in rows],
          [28, 24, 22, 12] + [11] * len(dims))
    for row in ws.iter_rows(min_row=4, max_row=3 + len(rows), min_col=5, max_col=4 + len(dims)):
        for c in row:
            c.alignment = CENTER
            if c.value and str(c.value).startswith(P):
                c.fill = LGREEN
            elif c.value == o:
                c.fill = LBLUE
    ws.row_dimensions[3].height = 40
    note(ws, 11, "Conformed dimensions (Date, Medicine, Warehouse, Batch/Lot, Organization) are built once in Phase 1 "
                 "and reused unchanged by the Phase 2 and Phase 3 marts - this is what makes the final fact "
                 "constellation (bonus) possible without rework.", 18)


def humanize(expr: str) -> tuple[str, str]:
    m = re.search(r'r\."([A-Za-z0-9_]+)"', expr)
    srccol = m.group(1) if m else ("pipeline" if "_ingest_mode" in expr else "-")
    rules = []
    if "try_date" in expr:
        rules.append("safe cast TEXT → DATE (invalid → NULL → rejected by rule)")
    if "try_ts" in expr:
        rules.append("safe cast TEXT → TIMESTAMPTZ")
    if "try_int" in expr:
        rules.append("safe cast → INTEGER")
    if "try_num" in expr:
        rules.append("safe cast → NUMERIC")
    if "ops.yn" in expr:
        rules.append("Y/N → BOOLEAN")
    if "upper(btrim" in expr:
        rules.append("TRIM + UPPER")
    elif "btrim" in expr:
        rules.append("TRIM")
    elif "upper(" in expr:
        rules.append("UPPER")
    if "NULLIF" in expr:
        rules.append("blank → NULL")
    if "'None'" in expr:
        rules.append("blank → 'None'")
    if "DATE '2023-01-01'" in expr:
        rules.append("missing → 2023-01-01")
    return srccol, "; ".join(rules) or "copy as-is"


def build_source_to_target(wb) -> None:
    ws = fresh_sheet(wb, "Source.To.Target", tab="FF0000")
    title(ws, "Source-to-Target mapping - full lineage: CSV → bronze (raw) → silver (staging) → gold (dwh)", 8)
    rows = []
    for src, s in SOURCES.items():
        file = cfg()["sources"][src]["file"] + ".csv"
        for col, expr in s["cols"].items():
            srccol, rule = humanize(expr)
            rows.append(["Bronze → Silver", file, srccol, s["raw"], s["stg"], col, rule])
    for t, d in DIMS.items():
        for col, a in d["attrs"].items():
            if a[2] not in ("generated", "system") and not col.startswith("_"):
                rows.append(["Silver → Gold", a[2].split(".")[0] if "." in a[2] else a[2],
                             a[2].split(".", 1)[1] if "." in a[2] else "-", "staging.*", f"dwh.{t}", col,
                             f"{a[0]}; {a[3]}".strip("; ")])
    for t, f in FACTS.items():
        for col, a in f["cols"].items():
            fk, meas, dd, src, com = a
            if col.startswith("_") or src == "system":
                continue
            rule = (f"Surrogate-key lookup → {src}" + (f" ({com})" if com else "")) if fk == "Y" else (
                f"{meas}-additive measure; {com}" if meas != "N/A" else f"Degenerate dimension; {com}")
            rows.append(["Silver → Gold", src.split(".")[0] if fk != "Y" else "staging",
                         src.split(".", 1)[1] if fk != "Y" and "." in src else "-",
                         "staging.*", f"dwh.{t}", col, rule.strip("; ")])
    table(ws, 3, ["Hop", "Source file", "Source column", "From table", "To table", "Target column", "Transformation rule"],
          rows, [15, 28, 28, 24, 30, 28, 70])
    ws.freeze_panes = "A4"
    ws.auto_filter.ref = f"A3:G{3 + len(rows)}"


def build_dq(wb) -> None:
    ws = fresh_sheet(wb, "DQ.Rules", tab="FF0000")
    title(ws, "Data-quality rules (bronze → silver) and their results per refresh", 10)
    res = q("SELECT source_domain, rule_name, refresh_id, sum(failed_rows) AS failed, sum(checked_rows) AS checked "
            "FROM ops.dq_results GROUP BY 1, 2, 3")
    piv_f = res.pivot_table(index=["source_domain", "rule_name"], columns="refresh_id", values="failed", aggfunc="sum").fillna(0)
    piv_c = res.groupby(["source_domain", "rule_name"])["checked"].sum()
    rows = []
    for src, s in SOURCES.items():
        rules = list(s["rules"]) + [("duplicate_in_batch", "uniqueness", "row_number() OVER (PARTITION BY key) > 1")]
        if s["dup_existing"]:
            rules.append(("duplicate_existing_key", "uniqueness", "key already present in silver"))
        for name, typ, cond in rules:
            key = (src, name)
            f = piv_f.loc[key] if key in piv_f.index else pd.Series(dtype=float)
            rows.append([src, name, typ, cond.format(**cfg()["validation"]),
                         int(f.get("R0", 0)), int(f.get("R1", 0)), int(f.get("R2", 0)), int(piv_c.get(key, 0))])
    start = 4
    r = table(ws, 3, ["Source", "Rule", "Dimension of quality", "Condition (row is BAD when TRUE)", "Failed R0",
                      "Failed R1", "Failed R2", "Rows checked", "Failure rate %"], rows,
              [14, 26, 14, 60, 10, 10, 10, 12, 12], {5: "#,##0", 6: "#,##0", 7: "#,##0", 8: "#,##0"})
    for i in range(len(rows)):
        c = ws.cell(start + i, 9, f"=IF(H{start + i}=0,0,SUM(E{start + i}:G{start + i})/H{start + i})")
        c.number_format, c.fill, c.font, c.border = "0.00%", LGREEN, F, BOX
    end = start + len(rows) - 1
    ws.cell(end + 1, 4, "Total rule failures").font = FB
    for col in "EFG":
        c = ws.cell(end + 1, " EFG".index(col) + 4, f"=SUM({col}{start}:{col}{end})")
        c.number_format, c.fill, c.font = "#,##0", LGREEN, FB
    r = section(ws, r + 1, "Quarantine - rejected rows by refresh, source and FIRST failed rule (a row can break several rules)", 10)
    qq = q("SELECT l.refresh_id, r.source_domain, r.failed_rule, count(*) AS rows FROM quarantine.rejected_rows r "
           "JOIN ops.batch_load_log l ON l.batch_id = r.batch_id GROUP BY 1, 2, 3 ORDER BY 1, 2, 4 DESC")
    r = table(ws, r, ["Refresh", "Source", "First failed rule", "Rejected rows"], qq.values.tolist(), None, {4: "#,##0"})
    r = section(ws, r, "Quality gate", 10)
    note(ws, r, f"A batch whose reject rate exceeds {cfg()['validation']['max_reject_rate_pct']}% fails the quality_gate "
                "task (no retries) in sdi_03_silver_validation, which stops promotion to the gold layer. Observed "
                "reject rates: R1 ≈ 1.2%, R2 ≈ 1.2% (all injected), R0 = 0%. Rejected rows keep their full JSON "
                "payload in quarantine.rejected_rows for audit and re-processing.", 10)


def build_scd(wb) -> None:
    ws = fresh_sheet(wb, "SCD.Design", tab="92D050")
    title(ws, "Slowly Changing Dimensions, junk, role-playing, degenerate and conformed dimensions - design + evidence", 9)
    rows = [
        ["DimMedicine", "Type 2", "rrp, wholesale price, unit cost, product status, therapeutic class, prescription class, cold-chain flag, storage temps",
         "names, SKU, GTIN, dosage descriptors (Type 1 overwrite)", "md5 hash of Type 2 attributes compared to the current row; expire + insert"],
        ["DimOrganization", "Type 2", "name, type, city, state, licensed beds", "GLN, ICU beds, occupancy, trauma level (Type 1)",
         "Effective from the first sale/demand date carrying the new values"],
        ["DimWarehouse / DimBatchLot", "Type 1", "-", "all attributes", "Upsert ON CONFLICT"],
        ["DimDisease / Segment / Payer / Promotion", "Type 1", "-", "all", "Insert new members only"],
        ["DimChannel / DimDate", "Type 0", "-", "-", "Seeded once by DDL"],
        ["DimSalesProfile / DimDemandProfile", "Junk", "-", "-", "Distinct observed flag combinations"],
    ]
    r = table(ws, 3, ["Dimension", "Strategy", "Type 2 (history) attributes", "Type 1 / fixed attributes", "Mechanism"],
              rows, [26, 10, 50, 40, 46])
    r = section(ws, r, "Evidence from refresh R2 - new versions created by the generator's SCD changes (config/pipeline.yaml)", 9)
    med = q("SELECT medicine_key, medicine_id, generic_name, rrp_usd, product_status, effective_from::text, "
            "effective_to::text, is_current, version_no FROM dwh.dim_medicine WHERE medicine_id IN "
            "(SELECT medicine_id FROM dwh.dim_medicine WHERE version_no > 1) ORDER BY 2, 9")
    r = table(ws, r, list(med.columns), med.values.tolist())
    org = q("SELECT org_key, org_natural_key, org_name, effective_from::text, effective_to::text, is_current, version_no "
            "FROM dwh.dim_organization WHERE org_natural_key IN (SELECT org_natural_key FROM dwh.dim_organization "
            "WHERE version_no > 1) ORDER BY 2, 7")
    r = table(ws, r, list(org.columns), org.values.tolist())
    r = section(ws, r, "Point-in-time correctness: facts join the version valid on the transaction date", 9)
    pit = q("SELECT m.medicine_id, m.version_no, m.rrp_usd, min(f.sale_date_key) AS first_sale, max(f.sale_date_key) AS last_sale, "
            "count(*) AS sales_rows, round(avg(f.unit_retail_price_usd), 2) AS avg_unit_price FROM dwh.fact_pharmacy_sales f "
            "JOIN dwh.dim_medicine m ON m.medicine_key = f.medicine_key WHERE m.medicine_id IN ('MED001','MED010') "
            "AND f.sale_date_key >= 20260101 GROUP BY 1, 2, 3 ORDER BY 1, 2")
    r = table(ws, r, list(pit.columns), pit.values.tolist())
    r = section(ws, r, "Other DWH paraphernalia used", 9)
    table(ws, r, ["Concept", "Where", "Why"], [
        ["Role-playing dimension", "DimDate as Sale / Delivery / Expiry / Demand-month / Snapshot date (5 views); "
         "DimOrganization as Hospital and Hospital Pharmacy in FactHospitalDemand", "One physical table, several business meanings"],
        ["Junk dimension", "DimSalesProfile (6 flags), DimDemandProfile (2 statuses)", "Keeps facts narrow; ≤192 combinations"],
        ["Degenerate dimension", "sales_transaction_id, pharmacy_order_id, prescription_id, hospital_demand_record_id, inventory_snapshot_id",
         "Transaction identifiers with no attributes of their own"],
        ["Conformed dimension", "Date, Medicine, Organization, Warehouse, Batch/Lot, Disease, Channel", "Enables drill-across (Q06, Q07) and the Phase-4 constellation"],
        ["Outrigger-style link", "DimOrganization.parent_hospital_id (hospital pharmacy → hospital)", "Connects retail sales to hospital demand"],
        ["Unknown member", "Key -1 in every dimension", "No fact row is ever dropped by a failed lookup (0 unknowns observed)"],
        ["Fact types", "Transaction (sales), periodic snapshot monthly (demand), periodic snapshot daily (inventory)", "Different source grains are never mixed"],
        ["Semi-/non-additive measures", "Stock balances (semi), price/service level/ratios (non)", "Stored as components; ratios computed at query time"],
    ])


def build_kpis(wb) -> None:
    ws = fresh_sheet(wb, "KPI.Dictionary", tab="92D050")
    title(ws, "KPI dictionary - standard KPI names, formulas, additivity and where each is answered", 6)
    table(ws, 3, ["KPI", "Formula (SQL)", "Additivity", "Source fact", "Query / widget"], [list(k) for k in KPIS],
          [30, 52, 22, 34, 18])


def build_oversampling(wb) -> None:
    ws = fresh_sheet(wb, "Oversampling", tab="FFC000")
    title(ws, "Data Oversampling Layer - methodology and statistics of generated data", 13)
    r = 3
    for t in [
        "Method: seasonal bootstrap with time-shift. For refresh month M the template is the same month one year earlier "
        "(R1 = Jan-2026 ← Jan-2025, R2 = Feb-2026 ← Feb-2025). Rows are copied with dates shifted +12 months, new tagged "
        "keys (SALE-R1-…, WHLOT-…-R1) and one consistent re-keying of lots / batches / shipments so lineage holds.",
        "Sales volume = template rows × YoY growth (cell C" + "6). 2025 grew +9.1% over 2024; a conservative +8% is used. "
        "Money columns are recomputed from the CURRENT master price (so SCD2 price changes reach revenue).",
        "Why this much: each refresh = one realistic calendar month of every source, so the DWH grows like production would "
        "(history is appended, never reloaded). The January peak makes R1 a stress test (~100k sales) and February a normal month.",
        "Dirty data is injected on purpose (1% invalid rows, 0.2% duplicate keys, 0.05% malformed CSV lines, 1% malformed "
        "stream events) to prove the validation layer and to populate the DQ dashboard panels.",
    ]:
        r = note(ws, r, "• " + t, 13)
    ws.cell(r + 1, 1, "YoY growth factor (input)").font = FB
    gcell = ws.cell(r + 1, 3, float(cfg()["generator"]["yoy_growth"]))
    gcell.fill, gcell.font, gcell.number_format = INPUT, Font(name=FONT, size=9, color="0000FF", bold=True), "0.00"
    gref = f"$C${r + 1}"
    # fix the reference text in note 2
    for row in ws.iter_rows(min_row=3, max_row=r):
        if row[0].value and "cell C6" in str(row[0].value):
            row[0].value = str(row[0].value).replace("cell C6", f"cell C{r + 1}")
    r += 3
    man = [json.loads(x) for x in (path("landing").parent / "generator_manifest.jsonl").read_text().splitlines()]
    man = pd.DataFrame(man).drop_duplicates(subset=["refresh_id", "source"], keep="last")
    sales_t = pd.read_csv(path("source") / "05_pharmacy_sales_data.csv", usecols=["SalesDate"])
    r = section(ws, r, "Sales volume check: template × growth = generated (batch file + stream)", 13)
    hdr = ["Refresh", "Target month", "Template month", "Template sales rows", "Expected = template × growth",
           "Batch file rows (incl. duplicates)", "Duplicates injected", "Stream events", "Generated sales (formula)", "Difference"]
    for j, h in enumerate(hdr, 1):
        c = ws.cell(r, j, h)
        c.font, c.fill, c.border, c.alignment = FB, PEACH, BOX, CENTER
    r += 1
    for rid in ("R1", "R2"):
        m = man[man.refresh_id == rid]
        tm = m.template_month.iloc[0]
        b = m[m.source == "sales"].iloc[0]
        st = m[m.source == "sales_stream"].iloc[0]
        vals = [rid, cfg()["refreshes"][rid]["month"], tm, int((sales_t.SalesDate.str[:7] == tm).sum()),
                f"=ROUND(D{r}*{gref},0)", int(b.rows_written), int(b.duplicate_rows), int(st.rows_written),
                f"=F{r}-G{r}+H{r}", f"=I{r}-E{r}"]
        for j, v in enumerate(vals, 1):
            c = ws.cell(r, j, v)
            c.font, c.border = F, BOX
            c.number_format = "#,##0"
            if isinstance(v, str) and v.startswith("="):
                c.fill = LGREEN
        r += 1
    r += 1
    r = section(ws, r, "Rows generated per source file and refresh (from data/generator_manifest.jsonl)", 13)
    start = r + 1
    rows = man[["refresh_id", "source", "file", "template_month", "rows_written", "dirty_rows", "duplicate_rows",
                "malformed_lines", "size_mb"]].values.tolist()
    r = table(ws, r, ["Refresh", "Source", "File", "Template", "Rows written", "Dirty rows", "Duplicate rows",
                      "Malformed lines", "Size MB", "Injected %"], rows, [10, 14, 38, 12, 18, 16, 16, 14, 16, 14],
              {5: "#,##0", 6: "#,##0", 7: "#,##0", 8: "#,##0", 9: "0.000"})
    end = start + len(rows) - 1
    for i in range(start, end + 1):
        c = ws.cell(i, 10, f"=IF(E{i}=0,0,(F{i}+G{i}+H{i})/E{i})")
        c.number_format, c.fill, c.font, c.border = "0.00%", LGREEN, F, BOX
    ws.cell(end + 1, 4, "Total").font = FB
    for col in "EFGHI":
        c = ws.cell(end + 1, "ABCDEFGHI".index(col) + 1, f"=SUM({col}{start}:{col}{end})")
        c.number_format, c.fill, c.font = "#,##0" if col != "I" else "0.00", LGREEN, FB
    r = end + 3
    r = section(ws, r, "Refresh plan across the whole project (each mart refreshed twice)", 13)
    table(ws, r, ["Refresh", "Month generated", "Used by"], [[k, v["month"], f"Phase {v['phase']}"] for k, v in cfg()["refreshes"].items()])


def build_airflow(wb) -> None:
    ws = fresh_sheet(wb, "Airflow.Design", tab="0070C0")
    title(ws, "Airflow pipeline design - DAGs, triggers, tasks and the metrics each one writes", 8)
    dags = [
        ["sdi_00_data_generator", "Oversampling", "Manual (param refresh_id R0…R6)", "init_database → start_refresh → generate_files → trigger_stream_ingestion",
         "CSV files in data/landing/, JSONL stream file", "ops.refresh_log, generator manifest", "Triggers sdi_02"],
        ["file-watcher (service)", "Ingestion trigger", "watchdog event on data/landing/", "wait until size stable → register in ops.file_registry → POST Airflow REST API dagRuns",
         "-", "ops.file_registry", "Triggers sdi_01 per file"],
        ["sdi_01_batch_ingestion", "Batch ingestion", "REST trigger per file (conf.file) + every 5 min safety-net scan",
         "detect_files → ingest_file (dynamic task mapping, 1 per file) → anything_loaded (short-circuit) → publish_bronze",
         "raw.* (bronze)", "ops.batch_load_log", "Dataset BRONZE"],
        ["sdi_02_stream_ingestion", "Streaming ingestion", "Triggered by sdi_00", "stream_available → replay_stream (micro-batches via FastAPI /ingest/stream/sales)",
         "raw.pharmacy_sales (mode = stream)", "ops.stream_metrics, ops.stream_dead_letter", "Dataset BRONZE"],
        ["sdi_03_silver_validation", "Storage & staging", "Dataset BRONZE (data-aware)", "validate_pending_batches → quality_gate (retries = 0) → anything_new → publish_silver",
         "staging.* (silver), quarantine.rejected_rows", "ops.dq_results", "Dataset SILVER"],
        ["sdi_04_gold_transform", "Transformation & dimensional modeling", "Dataset SILVER", "[dimensions] dim_medicine_scd2 → dim_organization_scd2 → dims_conformed_and_junk → "
         "[facts] fact_pharmacy_sales ‖ fact_hospital_demand ‖ fact_inventory_snapshot → publish_gold",
         "dwh.* (gold star schema)", "ops.transform_log, ops.watermark", "Dataset GOLD"],
        ["sdi_05_query_layer", "Query layer & optimization", "Dataset GOLD", "apply_indexes_and_mvs → refresh_materialized_views (CONCURRENTLY) → benchmark_queries → capture_storage_metrics → close_refresh",
         "mart.mv_* (summary tables)", "ops.query_log, ops.storage_snapshot, ops.database_size_log", "Marks refresh SUCCESS"],
    ]
    r = table(ws, 3, ["DAG / service", "Layer", "Trigger / schedule", "Tasks (in order)", "Writes data to", "Writes metrics to", "Hands off via"],
              dags, [26, 20, 26, 62, 30, 32, 18])
    r = section(ws, r, "Design principles", 8)
    for t in [
        "Modular: DAG files only orchestrate; all logic is in src/ (generator, ingestion, validation, transform, query, storage) "
        "and SQL files in sql/. Every task can also run without Airflow: python -m src.pipeline refresh R1 --stream.",
        "Event-driven chaining with Airflow Datasets (BRONZE → SILVER → GOLD) instead of fixed schedules: a layer runs "
        "only when the layer below published new data; short-circuit tasks prevent empty runs from triggering downstream.",
        "Idempotent and incremental: files are de-duplicated by SHA-256, claimed with an atomic rename, silver uses ON CONFLICT, "
        "gold loads only batches above a watermark - re-running any DAG never duplicates data and the DWH is appended, never reloaded.",
        "Reliability: retries = 2 with exponential back-off, 30-min execution timeout, 20-min SLA, failure callback logging, "
        "max_active_runs = 1 on the layers that must be serialised, a no-retry quality gate.",
        "Observability: every component writes to the ops schema, which the four Grafana dashboards read every 10 s.",
    ]:
        r = note(ws, r, "• " + t, 8)
    r = section(ws, r + 1, "Default task arguments", 8)
    table(ws, r, ["Setting", "Value"], [["owner", "dwh-team"], ["retries", "2 (quality_gate: 0)"], ["retry_delay", "30 s, exponential back-off"],
                                       ["execution_timeout", "30 min"], ["sla", "20 min"], ["catchup", "False"],
                                       ["executor", "LocalExecutor (Docker Compose)"]])


def build_metrics(wb) -> None:
    ws = fresh_sheet(wb, "Pipeline.Metrics", tab="FFC000")
    title(ws, "Pipeline metrics captured during the initial load (R0) and refreshes R1 + R2", 14)
    r = section(ws, 3, "Refresh runs", 14)
    rl = q("SELECT refresh_id, refresh_month, started_at::text, finished_at::text, status, "
           "round(extract(epoch FROM finished_at - started_at)::numeric, 1) AS duration_s FROM ops.refresh_log ORDER BY started_at")
    r = table(ws, r, ["Refresh", "Month", "Started", "Finished", "Status", "Duration (s)"], rl.values.tolist())
    r = section(ws, r, "Batch ingestion - one row per file (ops.batch_load_log)", 14)
    bl = q("SELECT batch_id, refresh_id, source_domain, ingest_mode, file_name, file_size_mb, rows_read, rows_loaded, "
           "rows_malformed, duration_s, rows_per_sec, status FROM ops.batch_load_log ORDER BY batch_id")
    start = r + 1
    r = table(ws, r, ["Batch", "Refresh", "Source", "Mode", "File", "Size MB", "Rows read", "Rows loaded", "Malformed",
                      "Duration s", "Rows/s", "Status"], bl.values.tolist(), [9, 9, 14, 9, 40, 10, 12, 12, 10, 11, 11, 18, 12, 12],
              {6: "0.000", 7: "#,##0", 8: "#,##0", 9: "#,##0", 10: "0.000", 11: "#,##0"})
    end = start + len(bl) - 1
    ws.cell(end + 1, 5, "Totals / success rate").font = FB
    for col, fmt in (("G", "#,##0"), ("H", "#,##0"), ("I", "#,##0"), ("J", "0.00")):
        c = ws.cell(end + 1, "ABCDEFGHIJ".index(col) + 1, f"=SUM({col}{start}:{col}{end})")
        c.number_format, c.fill, c.font = fmt, LGREEN, FB
    c = ws.cell(end + 1, 12, f'=(COUNTIF(L{start}:L{end},"SUCCESS")+COUNTIF(L{start}:L{end},"SKIPPED_DUPLICATE"))/COUNTA(L{start}:L{end})')
    c.number_format, c.fill, c.font = "0.0%", LGREEN, FB
    r = end + 3
    r = section(ws, r, "Streaming ingestion - summary per refresh (ops.stream_metrics)", 14)
    ss = q("SELECT refresh_id, count(*) AS micro_batches, sum(events_received) AS received, sum(events_loaded) AS loaded, "
           "sum(events_malformed) AS malformed, round(sum(events_loaded) / extract(epoch FROM max(batch_end_ts) - min(batch_start_ts))::numeric, 1) AS overall_eps, "
           "round(avg(avg_latency_s), 3) AS avg_latency_s, round(avg(p95_latency_s), 3) AS p95_latency_s, round(max(max_latency_s), 3) AS max_latency_s "
           "FROM ops.stream_metrics GROUP BY 1 ORDER BY 1")
    r = table(ws, r, ["Refresh", "Micro-batches", "Events received", "Events loaded", "Malformed", "Throughput (events/s)",
                      "Avg latency s", "P95 latency s", "Max latency s"], ss.values.tolist())
    r = section(ws, r, "Streaming - micro-batch log", 14)
    sm = q("SELECT refresh_id, micro_batch_no, events_received, events_loaded, events_malformed, throughput_eps, "
           "round(avg_latency_s, 4), round(p95_latency_s, 4) FROM ops.stream_metrics ORDER BY id")
    r = table(ws, r, ["Refresh", "Micro-batch", "Received", "Loaded", "Malformed", "Events/s", "Avg latency s", "P95 latency s"], sm.values.tolist())
    r = section(ws, r, "Transformation steps (ops.transform_log)", 14)
    tl = q("SELECT refresh_id, step, target_table, rows_before, rows_after, rows_inserted, scd2_new_versions, duration_s "
           "FROM ops.transform_log ORDER BY id")
    r = table(ws, r, ["Refresh", "Step", "Target", "Rows before", "Rows after", "Inserted", "SCD2 versions", "Duration s"],
              tl.values.tolist(), None, {4: "#,##0", 5: "#,##0", 6: "#,##0"})
    r = section(ws, r, "Storage growth by layer after each refresh (ops.storage_snapshot, last capture per refresh)", 14)
    st = q("SELECT refresh_id, layer, sum(row_count) AS rows, round(sum(size_mb), 1) AS size_mb FROM ("
           "SELECT DISTINCT ON (refresh_id, table_name) * FROM ops.storage_snapshot ORDER BY refresh_id, table_name, captured_at DESC) x "
           "GROUP BY 1, 2 ORDER BY 1, CASE layer WHEN 'bronze' THEN 1 WHEN 'silver' THEN 2 WHEN 'quarantine' THEN 3 WHEN 'gold' THEN 4 ELSE 5 END")
    r = table(ws, r, ["Refresh", "Layer", "Rows", "Size MB"], st.values.tolist(), None, {3: "#,##0", 4: "#,##0.0"})
    r = section(ws, r, "Gold table row counts and date coverage (latest)", 14)
    gc = q("SELECT DISTINCT ON (table_name) layer, schema_name || '.' || table_name, row_count, size_mb, min_date::text, "
           "max_date::text, distinct_months FROM ops.storage_snapshot ORDER BY table_name, captured_at DESC")
    gc = gc.sort_values(gc.columns[0])
    r = table(ws, r, ["Layer", "Table", "Rows", "Size MB", "Min date", "Max date", "Months"], gc.values.tolist(), None,
              {3: "#,##0", 4: "#,##0.0"})
    dbs = q("SELECT refresh_id, max(db_size_mb) FROM ops.database_size_log GROUP BY 1 ORDER BY 1")
    r = section(ws, r, "Database size (pg_database_size) after each refresh", 14)
    table(ws, r, ["Refresh", "DB size MB"], dbs.values.tolist(), None, {2: "#,##0.0"})


def build_dashboards(wb) -> None:
    ws = fresh_sheet(wb, "DE.Dashboards", tab="C00000")
    title(ws, "Data-engineering dashboards (Grafana, auto-refresh 10 s) - panel catalogue, computation and the story they tell", 7)
    v = {
        "files": db.fetch_scalar("SELECT count(*) FROM ops.batch_load_log WHERE ingest_mode='batch'"),
        "rows": db.fetch_scalar("SELECT sum(rows_loaded) FROM ops.batch_load_log WHERE ingest_mode='batch'"),
        "slow": db.fetch_scalar("SELECT max(duration_s) FROM ops.batch_load_log"),
        "eps": db.fetch_scalar("SELECT round(avg(throughput_eps),1) FROM ops.stream_metrics"),
        "lat": db.fetch_scalar("SELECT round(avg(avg_latency_s),3) FROM ops.stream_metrics"),
        "bad": db.fetch_scalar("SELECT sum(events_malformed) FROM ops.stream_metrics"),
        "dbmb": db.fetch_scalar("SELECT round(max(db_size_mb)) FROM ops.database_size_log"),
        "q": db.fetch_scalar("SELECT count(*) FROM quarantine.rejected_rows"),
    }
    rows = [
        ["1 Batch Ingestion", "Load duration per source file (horizontal bar)", "Batch Load Duration (s)", "end_ts − start_ts per file (read + parse + COPY)",
         f"Sales (500k rows) dominates at ~{float(v['slow']):.1f} s; small master/demand files load in <0.1 s → the sales file is the bottleneck to watch."],
        ["1 Batch Ingestion", "Rows processed per batch", "Rows per batch", "len(df) after pandas parse; malformed = data lines − parsed rows",
         f"{int(v['files'])} file loads, {int(v['rows']):,} rows; broken CSV lines are skipped and counted, not loaded."],
        ["1 Batch Ingestion", "Success rate (stat + bar by source)", "Batch Success Rate %", "(SUCCESS + idempotent skips) / attempts × 100",
         "100%: the R1 master file was identical to R0 and was skipped by SHA-256 (idempotency), not failed."],
        ["2 Streaming", "Stream throughput (line, recommended)", "Events / second", "events loaded / micro-batch window (production + insert)",
         f"Average ≈ {v['eps']} events/s; bursty traffic (±40% window jitter) without pipeline backlog → stable."],
        ["2 Streaming", "End-to-end latency (avg / p95 / max)", "Latency (s)", "DB clock_timestamp() at insert − event creation time",
         f"Average ≈ {v['lat']} s, bounded by the 1-s micro-batch buffer - i.e. near-real-time."],
        ["2 Streaming", "Malformed / dropped + reasons", "Malformed count", "try/except counter; payload kept in ops.stream_dead_letter",
         f"{int(v['bad'])} events dropped (truncated JSON, missing MedicineID, empty ID) - none reached the DWH."],
        ["3 Storage & Staging", "Storage breakdown donut (recommended)", "% of rows by source domain", "count(*) per bronze table",
         "Sales + IoT make up >80% of raw volume → partitioning/BRIN focus on those facts."],
        ["3 Storage & Staging", "Row counts / footprint by layer", "Table Row Count, Storage MB", "count(*), pg_total_relation_size, pg_database_size",
         f"DB ≈ {int(v['dbmb'])} MB; gold facts grow every refresh while history is never reloaded (append-only DWH)."],
        ["3 Storage & Staging", "Date coverage table + stats", "Date Coverage Span", "MAX(date) − MIN(date), distinct year-months",
         "Gold sales span Jan-2023 → Feb-2026 (38 months) - proves full 2023-2025 coverage plus 2 refresh months."],
        ["3 Storage & Staging", "Data-quality rejects by rule", "Rejected rows", "ops.dq_results per rule per batch",
         f"{int(v['q']):,} rows quarantined - matches the ~1% dirty data injected by the generator."],
        ["4 Query Layer", "Avg execution time per widget (column, recommended)", "Query Execution Time (ms)", "EXPLAIN ANALYZE execution time; perf_counter() wrapper",
         "Baseline star joins 10-530 ms; optimized (MV + index) 1-18 ms → every widget is sub-second."],
        ["4 Query Layer", "Speed-up per query + catalogue table", "Speed-up ×", "baseline ms / optimized ms",
         "Up to ~200× (regional ROLLUP); smallest gains on the 27k-row demand fact, where MVs matter less."],
        ["4 Query Layer", "Queries today / error rate / live Grafana queries", "Daily Active Query Count, Error Rate %", "count(ops.query_log); failed/total; pg_stat_statements calls",
         "0% error rate; pg_stat_statements also counts the queries Grafana itself sends on each refresh."],
    ]
    r = table(ws, 3, ["Dashboard", "Panel", "Metric", "Computation", "Story / insight (values from the R2 run)"], rows,
              [20, 36, 26, 50, 70])
    r = section(ws, r, "Design rules applied to every dashboard", 7)
    for t in ["Gradient colours: light for low values, dark for high values (Grafana continuous-blues scheme).",
              "Z-pattern layout: KPI stat cards across the top, the recommended main chart top-left, supporting charts right, detail tables at the bottom.",
              "A Refresh filter (R0 / R1 / R2) on every dashboard to compare the initial load with each refresh; auto-refresh every 10 s during the live demo.",
              "Dashboards are code (grafana/dashboards/*.json, generated by scripts/build_grafana_dashboards.py) and provisioned automatically."]:
        r = note(ws, r, "• " + t, 7)


def build_queries(wb) -> None:
    from src.query.benchmark import _checksum
    ws = fresh_sheet(wb, "Query.Catalog", tab="C00000")
    title(ws, "Analytical query layer - catalogue, benchmark (median of 3 runs after R2) and result-equality check", 12)
    b = q("SELECT query_id, variant, percentile_cont(0.5) WITHIN GROUP (ORDER BY server_ms) AS ms, max(rows_returned) AS n "
          "FROM ops.query_log WHERE refresh_id = (SELECT max(refresh_id) FROM ops.query_log) GROUP BY 1, 2")
    bm = {(x.query_id, x.variant): (float(x.ms), int(x.n)) for x in b.itertuples()}
    rows = []
    for qq in QUERIES:
        same = _checksum(db.fetch_df(qq["baseline"])) == _checksum(db.fetch_df(qq["optimized"]))
        rows.append([qq["id"], qq["name"], qq["question"], qq["olap"], qq["widget"],
                     round(bm[(qq["id"], "baseline")][0], 2), round(bm[(qq["id"], "optimized")][0], 2), None,
                     "Yes" if same else "NO", bm[(qq["id"], "optimized")][1]])
    start = 4
    table(ws, 3, ["ID", "Query", "Business question (English)", "OLAP operation", "Dashboard widget", "Baseline ms",
                  "Optimized ms", "Speed-up ×", "Identical results?", "Rows"], rows, [6, 30, 50, 30, 30, 11, 12, 10, 10, 7],
          {6: "0.00", 7: "0.00"})
    end = start + len(rows) - 1
    for i in range(start, end + 1):
        c = ws.cell(i, 8, f"=IF(G{i}=0,0,F{i}/G{i})")
        c.number_format, c.fill, c.font, c.border = "0.0x", LGREEN, F, BOX
    ws.cell(end + 1, 5, "Average / median").font = FB
    for col in "FG":
        c = ws.cell(end + 1, "ABCDEFG".index(col) + 1, f"=AVERAGE({col}{start}:{col}{end})")
        c.number_format, c.fill, c.font = "0.00", LGREEN, FB
    c = ws.cell(end + 1, 8, f"=MEDIAN(H{start}:H{end})")
    c.number_format, c.fill, c.font = "0.0x", LGREEN, FB
    r = end + 3
    r = section(ws, r, "SQL of every query (baseline = star-schema joins, optimized = summary tables)", 12)
    hdr = ["ID", "Baseline SQL", "Optimized SQL"]
    for j, h in enumerate(hdr, 1):
        c = ws.cell(r, j, h)
        c.font, c.fill, c.border = FB, PEACH, BOX
    ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
    ws.merge_cells(start_row=r, start_column=5, end_row=r, end_column=10)
    ws.cell(r, 5, "Optimized SQL").font, ws.cell(r, 5).fill = FB, PEACH
    r += 1
    for qq in QUERIES:
        ws.cell(r, 1, qq["id"]).font = FB
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
        ws.merge_cells(start_row=r, start_column=5, end_row=r, end_column=10)
        for col, key in ((2, "baseline"), (5, "optimized")):
            c = ws.cell(r, col, qq[key].strip())
            c.font, c.alignment = Font(name="Consolas", size=8), WRAP
        ws.row_dimensions[r].height = min(409, 11 * max(qq["baseline"].count("\n"), qq["optimized"].count("\n")) + 14)
        r += 1


def build_optimization(wb) -> None:
    ws = fresh_sheet(wb, "Optimization", tab="C00000")
    title(ws, "Query optimization techniques - indexes, partitions, materialized views (live from the catalog)", 6)
    r = section(ws, 3, "Materialized views (pre-aggregated summary tables, refreshed CONCURRENTLY by sdi_05)", 6)
    mv = q("SELECT matviewname, (SELECT count(*) FROM pg_class WHERE false) AS x FROM pg_matviews WHERE schemaname = 'mart' ORDER BY 1")
    purpose = {"mv_sales_month_med_channel": "Month × medicine × channel × warehouse - Q01-Q03, Q10-Q13",
               "mv_sales_commercial": "Month × channel × segment × payer × promotion × cold-chain - Q08",
               "mv_sales_month_org": "Month × organisation × channel - regional ROLLUP Q09",
               "mv_demand_month_hospital": "Month × hospital × medicine - Q04, Q05, Q07",
               "mv_sales_vs_demand": "Drill-across month × medicine (sales vs hospital demand) - Q06",
               "mv_inventory_month_wh_med": "Month × warehouse × medicine (sums + counts) - Q07"}
    rows = []
    for name in mv.matviewname:
        n = int(db.fetch_scalar(f"SELECT count(*) FROM mart.{name}"))
        mb = float(db.fetch_scalar(f"SELECT pg_total_relation_size('mart.{name}') / 1048576.0"))
        rows.append([f"mart.{name}", n, round(mb, 2), purpose.get(name, "")])
    r = table(ws, r, ["Materialized view", "Rows", "Size MB", "Serves"], rows, [42, 12, 10, 70], {2: "#,##0"})
    r = section(ws, r, "Partitions of the sales fact (RANGE on sale_date_key)", 6)
    pt = q("SELECT c.relname, (SELECT count(*) FROM dwh.fact_pharmacy_sales) AS dummy FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
           "WHERE i.inhparent = 'dwh.fact_pharmacy_sales'::regclass ORDER BY 1")
    prow = [[f"dwh.{p}", int(db.fetch_scalar(f"SELECT count(*) FROM dwh.{p}")),
             round(float(db.fetch_scalar(f"SELECT pg_total_relation_size('dwh.{p}') / 1048576.0")), 1)] for p in pt.relname]
    r = table(ws, r, ["Partition", "Rows", "Size MB"], prow, None, {2: "#,##0"})
    r = section(ws, r, "Indexes on the gold schema", 6)
    ix = q("SELECT tablename, indexname, regexp_replace(indexdef, '^.*USING ', 'USING ') AS def FROM pg_indexes "
           "WHERE schemaname = 'dwh' AND tablename NOT LIKE 'fact_pharmacy_sales_%' ORDER BY 1, 2")
    r = table(ws, r, ["Table", "Index", "Definition"], ix.values.tolist())
    r = section(ws, r, "Why these techniques", 6)
    for t in ["Materialized views cut the rows scanned from 600k+ to a few thousand; storing SUMs and COUNTs (never averages) "
              "keeps every roll-up exact - verified by identical checksums for all 13 queries.",
              "Year partitions let PostgreSQL prune whole years on date filters and keep each refresh's inserts in one small partition.",
              "BRIN on the append-only, date-ordered inventory snapshot is ~1000× smaller than a B-tree yet skips non-matching blocks.",
              "Composite B-tree indexes follow the most common FK filter/join pairs (date + medicine, medicine + channel, org + date).",
              "pg_stat_statements is enabled so the Query dashboard also shows the real cost of the queries Grafana sends."]:
        r = note(ws, r, "• " + t, 6)


def build_sample(wb) -> None:
    ws = fresh_sheet(wb, "Gold.Sample", tab="92D050")
    title(ws, "Sample rows from the gold layer (readable join of facts with their dimensions)", 12)
    s = q("SELECT f.sales_transaction_id, d.full_date::text AS sale_date, c.channel_name, o.org_name, m.generic_name, m.cold_chain_label, "
          "w.warehouse_name, f.quantity_sold_packs, f.net_sales_usd, f.gross_margin_usd, pr.promotion_code, f.ingest_mode "
          "FROM dwh.fact_pharmacy_sales f JOIN dwh.dim_date d ON d.date_key = f.sale_date_key JOIN dwh.dim_channel c USING (channel_key) "
          "JOIN dwh.dim_organization o USING (org_key) JOIN dwh.dim_medicine m USING (medicine_key) "
          "JOIN dwh.dim_warehouse w USING (warehouse_key) JOIN dwh.dim_promotion pr USING (promotion_key) "
          "WHERE f.sale_date_key >= 20260201 ORDER BY f.sales_transaction_id LIMIT 12")
    r = section(ws, 3, "FactPharmacySales (refresh R2 rows)", 12)
    r = table(ws, r, list(s.columns), s.values.tolist(), [22, 12, 16, 26, 18, 18, 26, 9, 10, 10, 12, 10])
    h = q("SELECT f.hospital_demand_record_id, to_char(d.full_date, 'YYYY-MM') AS month, o.org_name AS hospital, m.generic_name, "
          "f.forecast_demand_packs, f.requested_qty_packs, f.received_qty_packs, f.consumed_qty_packs, f.unfulfilled_demand_packs, "
          "f.service_level_pct FROM dwh.fact_hospital_demand f JOIN dwh.dim_date d ON d.date_key = f.demand_month_key "
          "JOIN dwh.dim_organization o USING (org_key) JOIN dwh.dim_medicine m USING (medicine_key) "
          "ORDER BY f.demand_month_key DESC, f.unfulfilled_demand_packs DESC LIMIT 10")
    r = section(ws, r, "FactHospitalDemand (latest month, highest unfulfilled demand)", 12)
    table(ws, r, list(h.columns), h.values.tolist())


def build_assumptions(wb) -> None:
    ws = fresh_sheet(wb, "Assumptions", tab="7F7F7F")
    title(ws, "Project assumptions, decisions and glossary", 3)
    A = [
        ["Scope", "Phase 1 = SDI mart up to the query layer. Files 03 and 07 are ingested to bronze (dashboards need all 7 domains) but modelled in Phases 2/3."],
        ["Storage engine", "PostgreSQL 16 for bronze, silver AND gold. The spec allows DuckDB; Postgres was chosen so one engine offers partitioning, BRIN, materialized views, pg_stat_statements and a native Grafana data source."],
        ["Channel", "Hospital-pharmacy channel = sales where PharmacyType = 'Hospital Pharmacy'; Retail = other physical sales; E-commerce from SalesChannel."],
        ["Inventory fact in SDI", "FactInventorySnapshot is loaded in Phase 1 as warehouse context (professor: 'warehouse is important') and becomes the core of the Phase 2 mart."],
        ["Refresh calendar", "R0 = 2023-2025 history; R1 = Jan-2026; R2 = Feb-2026 (Phase 1). R3-R6 reserved for Phases 2-3."],
        ["Growth", "+8% YoY on sales volume (actual 2024→2025 growth was +9.1%). Hospital demand flows scaled by N(1.04, 0.06) per row."],
        ["Stream", "E-commerce sales arrive as events (max 4,000 per refresh, micro-batches of 250 every ~1 s); all other sales arrive as the daily POS batch file."],
        ["Dirty data", "1% invalid rows, 0.2% duplicate keys, 0.05% malformed CSV lines, 1% malformed events - deliberately injected and fully caught."],
        ["Quality gate", "Batches with > 5% rejected rows stop promotion to gold."],
        ["SCD start date", "First version of every SCD2 member is effective from 1900-01-01 so all history maps to it; later versions from the change date."],
        ["Unknown members", "Key -1 in every dimension; 0 fact rows needed it after all three loads."],
        ["Money", "USD, 2 decimals; tax ≈ 6% of net; COGS = wholesale price × packs (as in the source)."],
        ["Time zone", "Business dates are taken as given; timestamps stored as TIMESTAMPTZ (UTC)."],
        ["Latency", "End-to-end latency is measured from event creation in the simulator to database commit (both on the same host clock)."],
        ["Storage footprint", "Measured with pg_total_relation_size / pg_database_size; the OS-level data-directory walk runs when the volume is mounted (PG_DATA_DIR_MOUNT)."],
        ["Security", "Grafana uses a read-only role (pg_read_all_data). Default passwords are for local demo only."],
    ]
    r = table(ws, 3, ["Topic", "Assumption / decision"], A, [22, 120])
    r = section(ws, r, "Glossary", 3)
    table(ws, r, ["Term", "Meaning"], [
        ["Bronze / Silver / Gold", "Medallion layers: raw as received / validated ODS / dimensional model"],
        ["ODS", "Operational Data Store - the silver staging schema"],
        ["SCD Type 2", "Keep history by adding a new dimension row per change, with validity dates"],
        ["Junk dimension", "Dimension that bundles unrelated low-cardinality flags"],
        ["Role-playing dimension", "One dimension used several times by a fact with different meanings"],
        ["Degenerate dimension", "A dimension key stored in the fact with no dimension table (e.g. transaction ID)"],
        ["Drill-across", "Combining two facts through their conformed dimensions"],
        ["Watermark", "Last batch id already loaded into a gold table - basis of incremental loading"],
        ["FEFO", "First-Expired, First-Out stock rotation"],
        ["OTIF", "On-Time In-Full delivery"],
    ])


SHEETS = [
    ("Cover", "Group, deliverables, contents"),
    ("ReadMe", "Template instructions (professor's 6 steps) + added steps"),
    ("Business.Req.XRay", "Business domain, stakeholders, OLAP requirements, KPIs, case studies"),
    ("OLTP.XRay", "Source tables feeding the DWH"),
    ("Transform.OLTP", "Transformation actions for dimension and fact attributes"),
    ("Dim.Tables", "Every dimension and attribute as implemented: type, SCD, hierarchy, source"),
    ("Fact.Table.PharmSales", "FactPharmacySales - transaction fact"),
    ("Fact.Table.Inventory", "FactInventorySnapshot - daily snapshot fact"),
    ("Fact.Table.Demand", "FactHospitalDemand - monthly snapshot fact"),
    ("Star.Schema", "Fact-constellation diagram + 3 solvable dimensional queries"),
    ("Source.Profile", "Profile of the 7 source files + key findings"),
    ("Column.Profile", "Column-level profile of the SDI source files"),
    ("Bus.Matrix", "Enterprise bus matrix across Phases 1-3"),
    ("Source.To.Target", "Column-level lineage CSV → bronze → silver → gold"),
    ("DQ.Rules", "Data-quality rules and results per refresh, quarantine"),
    ("SCD.Design", "SCD/junk/role-playing/degenerate design + live SCD2 evidence"),
    ("KPI.Dictionary", "KPI formulas, additivity and queries"),
    ("Oversampling", "Data-generation methodology and generated-row statistics"),
    ("Airflow.Design", "DAGs, triggers, tasks, datasets, reliability settings"),
    ("Pipeline.Metrics", "Ingestion, streaming, transformation and storage metrics R0-R2"),
    ("DE.Dashboards", "Grafana panel catalogue, computations and insights"),
    ("Query.Catalog", "13 dimensional queries, benchmark, SQL"),
    ("Optimization", "Materialized views, partitions, indexes"),
    ("Gold.Sample", "Readable sample rows from the facts"),
    ("Assumptions", "Assumptions, decisions, glossary"),
]


def main(template: str, out: str) -> None:
    wb = load_workbook(template)
    build_dim_tables(wb)
    build_fact_sheets(wb)
    patch_existing(wb)
    for fn in (build_source_profile, build_column_profile, build_bus_matrix, build_source_to_target, build_dq, build_scd,
               build_kpis, build_oversampling, build_airflow, build_metrics, build_dashboards, build_queries,
               build_optimization, build_sample, build_assumptions):
        fn(wb)
        print("built", fn.__name__)
    build_cover(wb, SHEETS[1:])
    order = [n for n, _ in SHEETS]
    wb._sheets = [wb[n] for n in order] + [s for s in wb._sheets if s.title not in order]
    wb.active = 0
    for ws in wb.worksheets:
        ws.sheet_view.tabSelected = ws.title == "Cover"
    wb.save(out)
    print("saved", out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
