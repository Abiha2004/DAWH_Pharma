"""DATA OVERSAMPLING LAYER - synthesises one new month of data for all 7 sources.

Method: "seasonal bootstrap with time-shift"
------------------------------------------------
For refresh month M (e.g. 2026-01) the template is the same calendar month one
year earlier (T = 2025-01). Every source is generated from its own T-slice:

  * dates are shifted +12 months (same day-of-month), so weekly and monthly
    seasonality (e.g. the Dec-Jan respiratory peak) is preserved;
  * natural keys get a refresh tag (SALE-R1-000000001, WHLOT-0000658-R1 ...) so
    new rows never collide with history and their template row stays traceable;
  * lots, production batches and shipments are re-keyed with ONE consistent
    mapping per refresh, so the sale -> lot -> batch -> shipment lineage holds;
  * sales are bootstrap-resampled (with replacement) to template x YoY growth
    (2025 grew +9.1% over 2024; config uses a conservative +8%); timestamps get
    random jitter inside the same day; money columns are recomputed from the
    *current* master price, so SCD Type 2 price changes flow into revenue;
  * hospital demand flows are scaled by a per-row factor and the stock balance
    (closing = opening + received - dispensed - wastage) is recomputed;
  * a controlled share of dirty rows, duplicate keys and malformed CSV lines is
    injected so the validation layer and DQ dashboards have something to catch;
  * e-commerce sales are split off into a JSONL event stream for the streaming
    ingestion path; everything else lands as CSV files in data/landing/.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.common.config import cfg, path

log = logging.getLogger(__name__)

DATE_COLS = {
    "manufacturing": ["OrderDate", "PlannedStartDate", "PlannedCompletionDate", "ActualStartDate",
                      "ActualCompletionDate", "QualitySampleDate", "BatchReleaseDate", "ExpiryDate"],
    "delivery": ["DispatchDate", "PromisedDeliveryDate", "ActualDeliveryDate"],
    "warehouse": ["SnapshotDate", "BatchExpiryDate"],
    "sales": ["SalesDate", "PharmacyDeliveryDate", "BatchExpiryDate"],
    "hospital": ["DemandMonth"],
}
TS_COLS = {"manufacturing": ["RecordCreatedTimestampUTC"], "sales": ["SalesTimestampUTC"],
           "iot": ["EventTimestampUTC", "IngestionTimestampUTC"]}


# ----------------------------------------------------------------- helpers
def _read(name: str) -> pd.DataFrame:
    meta = cfg()["sources"][name]
    return pd.read_csv(path("source") / f"{meta['file']}.csv", dtype=str, keep_default_na=False)


def _read_month(name: str, col: str, month: str, chunksize: int = 50_000) -> pd.DataFrame:
    """Memory-safe read: stream the source in chunks and keep only the template month.
    Avoids loading the 200+ MB sales / IoT files fully into the container's RAM."""
    meta = cfg()["sources"][name]
    parts = []
    for chunk in pd.read_csv(path("source") / f"{meta['file']}.csv", dtype=str,
                             keep_default_na=False, chunksize=chunksize):
        parts.append(chunk[chunk[col].str[:7] == month])
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _shift_dates(s: pd.Series, months: int) -> pd.Series:
    d = pd.to_datetime(s, errors="coerce")
    out = (d + pd.DateOffset(months=months)).dt.strftime("%Y-%m-%d")
    return out.where(d.notna(), s)


def _shift_ts(s: pd.Series, months: int) -> pd.Series:
    d = pd.to_datetime(s, errors="coerce", utc=True)
    out = (d + pd.DateOffset(months=months)).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return out.where(d.notna(), s)


def _tag(s: pd.Series, rid: str) -> pd.Series:
    return s.where(s == "", s + f"-{rid}")


def _money(x) -> pd.Series:
    return pd.Series(np.round(np.asarray(x, dtype=float) + 1e-9, 2))


def current_master(rid: str) -> pd.DataFrame:
    """Master data as of refresh `rid`: originals + all SCD changes up to and including rid."""
    m = _read("master")
    order = list(cfg()["refreshes"].keys())
    for r in order[: order.index(rid) + 1]:
        month = cfg()["refreshes"][r]["month"]
        for ch in (cfg()["generator"].get("scd_changes") or {}).get(r, []):
            idx = m["MedicineID"] == ch["MedicineID"]
            if "factor" in ch:
                m.loc[idx, ch["field"]] = (m.loc[idx, ch["field"]].astype(float) * ch["factor"]).round(2).astype(str)
            else:
                m.loc[idx, ch["field"]] = ch["value"]
            m.loc[idx, "EffectiveFromDate"] = f"{month}-01"
    return m


# ----------------------------------------------------------------- generator
class RefreshGenerator:
    def __init__(self, rid: str):
        self.rid = rid
        self.month = cfg()["refreshes"][rid]["month"]
        self.g = cfg()["generator"]
        self.rng = np.random.default_rng(int(self.g["random_seed"]) + int(rid[1:]))
        m = pd.Period(self.month, "M")
        self.template = str(m - 12)                     # same month last year
        self.shift = 12
        self.stats: list[dict] = []
        self.ship_map: dict = {}

    # ---- template slices -------------------------------------------------
    def _slice(self, df: pd.DataFrame, col: str) -> pd.DataFrame:
        return df[df[col].str[:7] == self.template].copy()

    # ---- dirty-data injection --------------------------------------------
    def _inject_dirty(self, df: pd.DataFrame, rules: list, key: str) -> tuple[pd.DataFrame, int, int]:
        n = len(df)
        n_dirty = int(round(n * self.g["dirty_row_rate"]))
        dirty_idx = self.rng.choice(df.index, size=min(n_dirty, n), replace=False) if n_dirty else []
        for i, ix in enumerate(dirty_idx):
            col, val = rules[i % len(rules)]
            df.at[ix, col] = val
        n_dup = int(round(n * self.g["duplicate_rate"]))
        dups = df.loc[self.rng.choice(df.index, size=n_dup, replace=False)] if n_dup else df.iloc[0:0]
        df = pd.concat([df, dups], ignore_index=True)
        return df, len(dirty_idx), len(dups)

    def _write(self, source: str, df: pd.DataFrame, dirty: int = 0, dups: int = 0, malformed_rate: float = 0.0) -> Path:
        meta = cfg()["sources"][source]
        f = path("landing") / f"{meta['file']}__{self.rid}.csv"
        tmp = f.with_name("." + f.name + ".part")
        df.to_csv(tmp, index=False)
        n_bad = int(round(len(df) * malformed_rate))
        if n_bad:   # append broken lines (too many fields) - the loader skips and counts them as malformed
            with open(tmp, "a", encoding="utf-8") as fh:
                for k in range(n_bad):
                    fh.write(f"BROKEN-{self.rid}-{k},corrupted record" + "," * (len(df.columns) + 2) + "\n")
        tmp.rename(f)            # atomic: the file-watcher only ever sees complete files
        self.stats.append({"refresh_id": self.rid, "source": source, "file": f.name, "template_month": self.template,
                           "rows_written": len(df), "dirty_rows": dirty, "duplicate_rows": dups,
                           "malformed_lines": n_bad, "size_mb": round(f.stat().st_size / 1_048_576, 3)})
        return f

    # ---- sources ----------------------------------------------------------
    def master(self) -> pd.DataFrame:
        m = current_master(self.rid)
        self._write("master", m)
        return m

    def manufacturing(self) -> dict:
        t = _read_month("manufacturing", "OrderDate", self.template)
        for c in DATE_COLS["manufacturing"]:
            t[c] = _shift_dates(t[c], self.shift)
        for c in TS_COLS["manufacturing"]:
            t[c] = _shift_ts(t[c], self.shift)
        t["ManufacturingOrderID"] = [f"MO-{self.rid}-{i:05d}" for i in range(1, len(t) + 1)]
        t["ProductionBatchID"] = _tag(t["ProductionBatchID"], self.rid)
        self._write("manufacturing", t)
        return {}

    def delivery(self) -> dict:
        t = _read_month("delivery", "DispatchDate", self.template)
        for c in DATE_COLS["delivery"]:
            t[c] = _shift_dates(t[c], self.shift)
        ship_map = {s: f"MFG-SHP-{self.rid}-{i:05d}" for i, s in enumerate(t["ShipmentID"].unique(), 1)}
        t["ShipmentID"] = t["ShipmentID"].map(ship_map)
        t["ProductionBatchID"] = _tag(t["ProductionBatchID"], self.rid)
        t["ManufacturingOrderID"] = _tag(t["ManufacturingOrderID"], self.rid)
        t["SSCC18"] = t["SSCC18"].str[:-4] + self.rid[1:].zfill(4)
        self._write("delivery", t)
        self.ship_map = ship_map
        return ship_map

    def warehouse(self) -> None:
        t = _read_month("warehouse", "SnapshotDate", self.template)
        for c in DATE_COLS["warehouse"]:
            t[c] = _shift_dates(t[c], self.shift)
        t["InventorySnapshotID"] = [f"INV-{self.rid}-{i:08d}" for i in range(1, len(t) + 1)]
        for c in ("WarehouseLotID", "ProductionBatchID"):
            t[c] = _tag(t[c], self.rid)
        t["InboundShipmentID"] = t["InboundShipmentID"].map(lambda x: self.ship_map.get(x, f"{x}-{self.rid}"))
        rules = [("ClosingQuantityPacks", "-50"), ("MedicineID", ""), ("SnapshotDate", "2026-13-45")]
        t, dirty, dups = self._inject_dirty(t.reset_index(drop=True), rules, "InventorySnapshotID")
        self._write("warehouse", t, dirty, dups)

    def sales(self, master: pd.DataFrame) -> pd.DataFrame:
        t = _read_month("sales", "SalesDate", self.template).reset_index(drop=True)
        n = int(round(len(t) * float(self.g["yoy_growth"])))
        s = t.loc[self.rng.choice(t.index, size=n, replace=True)].reset_index(drop=True)
        for c in DATE_COLS["sales"]:
            s[c] = _shift_dates(s[c], self.shift)
        # jitter time-of-day so resampled copies are distinct events
        ts = pd.to_datetime(s["SalesDate"]) + pd.to_timedelta(self.rng.integers(7 * 3600, 22 * 3600, size=n), unit="s")
        s["SalesTimestampUTC"] = ts.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        s = s.sort_values("SalesTimestampUTC").reset_index(drop=True)
        s["SalesTransactionID"] = [f"SALE-{self.rid}-{i:09d}" for i in range(1, n + 1)]
        has_rx = s["PrescriptionID"] != ""
        s.loc[has_rx, "PrescriptionID"] = [f"RX-{self.rid}-{i:09d}" for i in range(1, int(has_rx.sum()) + 1)]
        for c in ("WarehouseLotID", "ProductionBatchID"):
            s[c] = _tag(s[c], self.rid)
        # recompute money with the CURRENT master price list (SCD2 changes reach revenue)
        px = master.set_index("MedicineID")
        qty = s["QuantitySoldPacks"].astype(int)
        price = s["MedicineID"].map(px["RecommendedRetailPriceUSD"].astype(float))
        whs = s["MedicineID"].map(px["WholesalePriceUSD"].astype(float))
        disc_rate = s["PromotionCode"].map({"LOYALTY5": .05, "SEASON5": .05, "ONLINE10": .10, "WELLNESS8": .08}).fillna(0.0)
        gross = _money(qty * price)
        disc = _money(gross * disc_rate)
        net = _money(gross - disc)
        cogs = _money(qty * whs)
        s["UnitRetailPriceUSD"] = price.round(2).astype(str)
        s["GrossSalesAmountUSD"], s["DiscountAmountUSD"], s["NetSalesAmountUSD"] = gross, disc, net
        s["SalesTaxUSD"] = _money(net * 0.06)
        s["CostOfGoodsSoldUSD"] = cogs
        s["GrossMarginUSD"] = _money(net - cogs)
        # SCD2 demo on the organisation dimension
        for ch in (self.g.get("org_changes") or {}).get(self.rid, []):
            s.loc[s["PharmacyID"] == ch["PharmacyID"], ch["field"]] = ch["value"]
        s = s.astype(str)

        # e-commerce events -> stream (capped so a demo stream stays a few minutes long)
        cap = int(cfg()["streaming"].get("max_stream_events", 4000))
        ecom_idx = s.index[s["SalesChannel"] == "Ecommerce"]
        stream_idx = ecom_idx[:cap] if len(ecom_idx) > cap else ecom_idx
        stream = s.loc[stream_idx]
        batch = s.drop(index=stream_idx).reset_index(drop=True)

        rules = [("UnitRetailPriceUSD", "-3.50"), ("MedicineID", ""), ("SalesDate", "2026-02-30"),
                 ("MedicineID", "MED999"), ("QuantitySoldPacks", "0"), ("NetSalesAmountUSD", "999.99"),
                 ("SalesTransactionID", "")]
        batch, dirty, dups = self._inject_dirty(batch, rules, "SalesTransactionID")
        self._write("sales", batch, dirty, dups, float(self.g["malformed_line_rate"]))
        self._write_stream(stream)
        return s

    def _write_stream(self, stream: pd.DataFrame) -> None:
        f = path("stream") / f"sales_stream__{self.rid}.jsonl"
        rate = float(cfg()["streaming"]["malformed_event_rate"])
        bad = set(self.rng.choice(len(stream), size=int(round(len(stream) * rate)), replace=False)) if len(stream) else set()
        with open(f, "w", encoding="utf-8") as fh:
            for i, rec in enumerate(stream.to_dict("records")):
                if i in bad:
                    kind = i % 3
                    if kind == 0:
                        fh.write('{"SalesTransactionID": "' + rec["SalesTransactionID"] + '", "truncated_json": \n')
                        continue
                    if kind == 1:
                        rec.pop("MedicineID", None)
                    else:
                        rec["SalesTransactionID"] = ""
                fh.write(json.dumps(rec) + "\n")
        self.stats.append({"refresh_id": self.rid, "source": "sales_stream", "file": f.name, "template_month": self.template,
                           "rows_written": len(stream), "dirty_rows": len(bad), "duplicate_rows": 0,
                           "malformed_lines": 0, "size_mb": round(f.stat().st_size / 1_048_576, 3)})

    def hospital(self) -> None:
        t = _read_month("hospital", "DemandMonth", self.template).reset_index(drop=True)
        t["DemandMonth"] = _shift_dates(t["DemandMonth"], self.shift)
        t["HospitalDemandRecordID"] = [f"HDEM-{self.rid}-{i:06d}" for i in range(1, len(t) + 1)]
        f = self.rng.normal(1.0 + (float(self.g["yoy_growth"]) - 1) / 2, 0.06, size=len(t)).clip(0.8, 1.3)
        flows = ["EstimatedDiseaseCases", "DiseaseRelatedAdmissions", "OutpatientEncounters", "ForecastDemandPacks",
                 "RequestedQuantityPacks", "ApprovedQuantityPacks", "ReceivedQuantityPacks",
                 "EmergencyRequestQuantityPacks", "DispensedQuantityPacks", "ConsumedQuantityPacks"]
        for c in flows:
            t[c] = np.round(t[c].astype(float) * f).astype(int)
        t["ConsumedQuantityPacks"] = np.minimum(t["ConsumedQuantityPacks"], t["DispensedQuantityPacks"])
        closing = (t["OpeningStockPacks"].astype(int) + t["ReceivedQuantityPacks"] - t["DispensedQuantityPacks"]
                   - t["WastageQuantityPacks"].astype(int))
        short = (-closing).clip(lower=0)                       # cannot dispense stock we do not have
        t["DispensedQuantityPacks"] -= short
        t["ConsumedQuantityPacks"] = np.minimum(t["ConsumedQuantityPacks"], t["DispensedQuantityPacks"])
        t["ClosingStockPacks"] = closing + short
        t["UnfulfilledDemandPacks"] = t["UnfulfilledDemandPacks"].astype(int) + short
        t = t.astype(str)
        rules = [("RequestedQuantityPacks", "-25"), ("MedicineID", "MED999"), ("DemandMonth", "")]
        t, dirty, dups = self._inject_dirty(t, rules, "HospitalDemandRecordID")
        self._write("hospital", t, dirty, dups)

    def iot(self, ship_map: dict) -> None:
        t = _read_month("iot", "EventTimestampUTC", self.template)
        for c in TS_COLS["iot"]:
            t[c] = _shift_ts(t[c], self.shift)
        t["SensorReadingID"] = [f"IOTR-{self.rid}-{i:09d}" for i in range(1, len(t) + 1)]
        t["ShipmentID"] = t["ShipmentID"].map(lambda x: ship_map.get(x, x + f"-{self.rid}" if x else x))
        self._write("iot", t)

    # ---- orchestration ---------------------------------------------------
    def run(self, sources: list[str] | None = None) -> list[dict]:
        sources = sources or ["master", "manufacturing", "delivery", "warehouse", "sales", "hospital", "iot"]
        m = current_master(self.rid)
        ship_map: dict = {}
        for src in sources:
            log.info("Generating %s for %s (template %s)", src, self.rid, self.template)
            if src == "master":
                m = self.master()
            elif src == "delivery":
                ship_map = self.delivery()
            elif src == "sales":
                self.sales(m)
            elif src == "iot":
                self.iot(ship_map)
            else:
                getattr(self, src)()
        manifest = path("landing").parent / "generator_manifest.jsonl"
        with open(manifest, "a", encoding="utf-8") as fh:
            for row in self.stats:
                fh.write(json.dumps({**row, "generated_at": datetime.now(timezone.utc).isoformat()}) + "\n")
        return self.stats


def generate(rid: str, sources: list[str] | None = None) -> list[dict]:
    return RefreshGenerator(rid).run(sources)


def stage_initial_load() -> list[str]:
    """Copy the 7 original files into landing (refresh R0 = historical initial load)."""
    import shutil
    out = []
    for meta in cfg()["sources"].values():
        src = path("source") / f"{meta['file']}.csv"
        dst = path("landing") / src.name
        tmp = dst.with_name("." + dst.name + ".part")
        shutil.copyfile(src, tmp)
        tmp.rename(dst)
        out.append(dst.name)
    return out
