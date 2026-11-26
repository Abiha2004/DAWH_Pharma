"""Render report figures from the ops metric tables (the same data the Grafana dashboards read).

    DWH_DB_BACKEND=psql python scripts/make_report_figures.py  -> docs/img/*.png
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.common import db  # noqa: E402

OUT = ROOT / "docs" / "img"
OUT.mkdir(parents=True, exist_ok=True)
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
INK, MUTED, GRID = "#1f1f1f", "#6b6b6b", "#e6e4df"
for f in font_manager.findSystemFonts():
    if "Inter" in f:
        font_manager.fontManager.addfont(f)
plt.rcParams.update({"font.family": "Inter", "font.size": 8.5, "axes.edgecolor": GRID, "axes.labelcolor": MUTED,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.titlesize": 10, "axes.titleweight": "bold",
                     "axes.titlecolor": INK, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
                     "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False,
                     "figure.dpi": 200})


def save(fig, name):
    fig.tight_layout()
    fig.savefig(OUT / name, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("saved", name)


# ---------------------------------------------------------------- batch ingestion
b = db.fetch_df("SELECT refresh_id, source_domain, duration_s, rows_loaded, rows_malformed FROM ops.batch_load_log "
                "WHERE ingest_mode = 'batch' AND status = 'SUCCESS' ORDER BY batch_id")
srcs = ["sales", "iot", "warehouse", "hospital", "delivery", "manufacturing", "master"]
fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.0))
ax = axes[0]
h = 0.26
for i, rid in enumerate(["R0", "R1", "R2"]):
    d = b[b.refresh_id == rid].set_index("source_domain").reindex(srcs)
    ax.barh([y + (i - 1) * h for y in range(len(srcs))], d.duration_s.fillna(0), height=h - 0.03, color=CAT[i], label=rid)
ax.set_yticks(range(len(srcs)), srcs)
ax.invert_yaxis()
ax.set_xlabel("seconds")
ax.set_title("Batch load duration per source file")
ax.legend(title=None, loc="lower right")
ax.grid(axis="y", visible=False)
ax = axes[1]
for i, rid in enumerate(["R0", "R1", "R2"]):
    d = b[b.refresh_id == rid].set_index("source_domain").reindex(srcs)
    ax.barh([y + (i - 1) * h for y in range(len(srcs))], d.rows_loaded.fillna(0), height=h - 0.03, color=CAT[i], label=rid)
ax.set_xscale("log")
ax.set_yticks(range(len(srcs)), [])
ax.invert_yaxis()
ax.set_xlabel("rows loaded (log scale)")
ax.set_title("Rows processed per batch")
ax.grid(axis="y", visible=False)
save(fig, "fig_batch_ingestion.png")

# ---------------------------------------------------------------- streaming
s = db.fetch_df("SELECT refresh_id, micro_batch_no, throughput_eps, avg_latency_s, p95_latency_s, events_malformed "
                "FROM ops.stream_metrics ORDER BY id")
fig, axes = plt.subplots(1, 2, figsize=(9.2, 2.8))
for i, rid in enumerate(["R1", "R2"]):
    d = s[s.refresh_id == rid]
    axes[0].plot(d.micro_batch_no + 1, d.throughput_eps, color=CAT[i], lw=2, marker="o", ms=3.5, label=rid)
    axes[1].plot(d.micro_batch_no + 1, d.avg_latency_s, color=CAT[i], lw=2, marker="o", ms=3.5, label=f"{rid} avg")
    axes[1].plot(d.micro_batch_no + 1, d.p95_latency_s, color=CAT[i], lw=1.2, ls="--", label=f"{rid} p95")
axes[0].set_title("Stream throughput (events / s)")
axes[0].set_xlabel("micro-batch")
axes[0].set_ylim(0, None)
axes[0].legend(loc="lower right")
axes[1].set_title("End-to-end latency (s)")
axes[1].set_xlabel("micro-batch")
axes[1].set_ylim(0, None)
axes[1].legend(ncol=2, loc="lower right")
save(fig, "fig_streaming.png")

# ---------------------------------------------------------------- storage
st = db.fetch_df("SELECT DISTINCT ON (table_name) source_domain, row_count FROM ops.storage_snapshot "
                 "WHERE layer = 'bronze' ORDER BY table_name, captured_at DESC")
st = st.sort_values("row_count", ascending=False)
lay = db.fetch_df("SELECT refresh_id, layer, sum(row_count) AS rows FROM (SELECT DISTINCT ON (refresh_id, table_name) * "
                  "FROM ops.storage_snapshot ORDER BY refresh_id, table_name, captured_at DESC) x "
                  "WHERE layer IN ('bronze','silver','gold','quarantine') GROUP BY 1, 2")
fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.0), gridspec_kw={"width_ratios": [1, 1.25]})
ax = axes[0]
w, _ = ax.pie(st.row_count, colors=CAT[:len(st)], startangle=90, counterclock=False,
              wedgeprops={"width": 0.38, "edgecolor": "white", "linewidth": 2})
tot = st.row_count.sum()
ax.legend(w, [f"{d}  {100 * n / tot:.1f}%" for d, n in zip(st.source_domain, st.row_count)], loc="center left",
          bbox_to_anchor=(0.95, 0.5), fontsize=7.5)
ax.text(0, 0, f"{tot / 1e6:.2f}M\nbronze rows", ha="center", va="center", fontsize=8.5, color=INK)
ax.set_title("Storage breakdown by source domain")
ax = axes[1]
layers = ["bronze", "silver", "gold", "quarantine"]
for i, rid in enumerate(["R0", "R1", "R2"]):
    d = lay[lay.refresh_id == rid].set_index("layer").reindex(layers)
    ax.bar([x + (i - 1) * 0.26 for x in range(4)], d.rows.fillna(0), width=0.23, color=CAT[i], label=rid)
ax.set_xticks(range(4), layers)
ax.set_yscale("log")
ax.set_ylabel("rows (log scale)")
ax.set_title("Rows per layer after each refresh")
ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.32), ncol=3)
ax.grid(axis="x", visible=False)
save(fig, "fig_storage.png")

# ---------------------------------------------------------------- query layer
qd = db.fetch_df("SELECT query_id, variant, percentile_cont(0.5) WITHIN GROUP (ORDER BY server_ms) AS ms FROM ops.query_log "
                 "WHERE refresh_id = 'R2' GROUP BY 1, 2 ORDER BY 1")
pv = qd.pivot(index="query_id", columns="variant", values="ms")
fig, ax = plt.subplots(figsize=(9.2, 2.9))
x = range(len(pv))
ax.bar([i - 0.2 for i in x], pv.baseline, width=0.38, color=CAT[0], label="baseline (star-schema joins)")
ax.bar([i + 0.2 for i in x], pv.optimized, width=0.38, color=CAT[1], label="optimized (materialized views + indexes)")
for i, (bb, oo) in enumerate(zip(pv.baseline, pv.optimized)):
    ax.text(i, max(bb, oo) * 1.25, f"{bb / oo:.0f}×", ha="center", fontsize=7, color=INK)
ax.set_xticks(list(x), pv.index)
ax.tick_params(axis="x", length=0)
ax.set_yscale("log")
ax.set_ylabel("execution time, ms (log)")
ax.set_title("Average query execution time per dashboard widget (after refresh R2) - label = speed-up")
ax.set_ylim(0.5, pv.baseline.max() * 4)
ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.3), ncol=2)
ax.grid(axis="x", visible=False)
save(fig, "fig_query_layer.png")

# ---------------------------------------------------------------- oversampling: monthly sales history + refresh months
ms = db.fetch_df("SELECT d.year_month, count(*) AS n FROM dwh.fact_pharmacy_sales f JOIN dwh.dim_date d ON d.date_key = f.sale_date_key "
                 "GROUP BY 1 ORDER BY 1")
fig, ax = plt.subplots(figsize=(9.2, 2.6))
cols = [CAT[1] if m >= "2026-01" else CAT[0] for m in ms.year_month]
ax.bar(range(len(ms)), ms.n, color=cols, width=0.8)
ax.set_xticks(range(0, len(ms), 3), ms.year_month[::3], rotation=0, fontsize=7.5)
ax.set_ylabel("sales transactions")
ax.set_title("Monthly sales in the gold fact - history (blue) and generated refresh months R1, R2 (orange)")
for i, (m, n) in enumerate(zip(ms.year_month, ms.n)):
    if m in ("2025-01", "2026-01", "2026-02"):
        ax.text(i, n * 1.03, f"{n:,}", ha="center", fontsize=7, color=INK)
ax.grid(axis="x", visible=False)
ax.tick_params(axis="x", length=0)
save(fig, "fig_oversampling_monthly.png")
