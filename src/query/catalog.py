"""ANALYTICAL QUERY LAYER - the dimensional queries stakeholders run against the SDI mart.

Every query exists twice:
  baseline  : written directly against the star schema (fact + dimension joins)
  optimized : the same question answered from pre-aggregated materialized views
The benchmark runs both, checks that they return IDENTICAL results (checksum)
and logs execution time for the Query-Layer dashboard.
Relative periods ("last 3 months") are anchored on the latest date in the DWH,
so results move automatically after each refresh.
"""
from __future__ import annotations

LAST_MONTH = "(SELECT max(month_start_date) FROM mart.mv_sales_month_med_channel)"
LAST_MONTH_BASE = ("(SELECT d.month_start_date FROM dwh.dim_date d "
                   "WHERE d.date_key = (SELECT max(sale_date_key) FROM dwh.fact_pharmacy_sales))")
LAST_DEMAND_MONTH = "(SELECT max(month_start_date) FROM mart.mv_demand_month_hospital)"
LAST_DEMAND_MONTH_BASE = ("(SELECT to_date(max(demand_month_key)::text, 'YYYYMMDD') FROM dwh.fact_hospital_demand)")

QUERIES = [
    {
        "id": "Q01", "name": "Channel revenue split & gross margin by month",
        "widget": "Channel Revenue Split (stacked column)",
        "question": "How much net revenue and gross margin does each channel (Retail, Hospital Pharmacy, E-commerce) generate each month?",
        "olap": "Roll-up (day -> month), slice by channel",
        "baseline": """
SELECT d.year_month, c.channel_name,
       sum(f.quantity_sold_packs) AS units,
       round(sum(f.net_sales_usd), 2) AS net_sales,
       round(sum(f.gross_margin_usd), 2) AS gross_margin,
       round(100 * sum(f.gross_margin_usd) / NULLIF(sum(f.net_sales_usd), 0), 2) AS gm_pct
FROM dwh.fact_pharmacy_sales f
JOIN dwh.dim_date d ON d.date_key = f.sale_date_key
JOIN dwh.dim_channel c ON c.channel_key = f.channel_key
GROUP BY d.year_month, c.channel_name
ORDER BY d.year_month, c.channel_name""",
        "optimized": """
SELECT x.year_month, c.channel_name,
       sum(x.units) AS units,
       round(sum(x.net_sales), 2) AS net_sales,
       round(sum(x.gross_margin), 2) AS gross_margin,
       round(100 * sum(x.gross_margin) / NULLIF(sum(x.net_sales), 0), 2) AS gm_pct
FROM mart.mv_sales_month_med_channel x
JOIN dwh.dim_channel c ON c.channel_key = x.channel_key
GROUP BY x.year_month, c.channel_name
ORDER BY x.year_month, c.channel_name""",
    },
    {
        "id": "Q02", "name": "Top 10 fast-moving medicines (last 3 months)",
        "widget": "Top 10 Fast Movers by Therapeutic Class (bar)",
        "question": "Which 10 medicines sold the most packs in the last 3 months, and to which therapeutic class / cold-chain group do they belong?",
        "olap": "Slice (last 3 months), rank (window function)",
        "baseline": f"""
SELECT m.medicine_id, m.generic_name, m.therapeutic_class, m.cold_chain_label,
       sum(f.quantity_sold_packs) AS units, round(sum(f.net_sales_usd), 2) AS net_sales,
       rank() OVER (ORDER BY sum(f.quantity_sold_packs) DESC) AS velocity_rank
FROM dwh.fact_pharmacy_sales f
JOIN dwh.dim_date d ON d.date_key = f.sale_date_key
JOIN dwh.dim_medicine m ON m.medicine_key = f.medicine_key
WHERE d.month_start_date > {LAST_MONTH_BASE} - INTERVAL '3 months'
GROUP BY m.medicine_id, m.generic_name, m.therapeutic_class, m.cold_chain_label
ORDER BY units DESC, m.medicine_id
LIMIT 10""",
        "optimized": f"""
SELECT m.medicine_id, m.generic_name, m.therapeutic_class, m.cold_chain_label,
       sum(x.units) AS units, round(sum(x.net_sales), 2) AS net_sales,
       rank() OVER (ORDER BY sum(x.units) DESC) AS velocity_rank
FROM mart.mv_sales_month_med_channel x
JOIN dwh.dim_medicine m ON m.medicine_key = x.medicine_key
WHERE x.month_start_date > {LAST_MONTH} - INTERVAL '3 months'
GROUP BY m.medicine_id, m.generic_name, m.therapeutic_class, m.cold_chain_label
ORDER BY units DESC, m.medicine_id
LIMIT 10""",
    },
    {
        "id": "Q03", "name": "Bottom 10 slow-moving medicines (last 3 months)",
        "widget": "Top 10 Slow Movers by Therapeutic Class (bar)",
        "question": "Which 10 medicines sold the fewest packs in the last 3 months (candidates for overstock / expiry risk)?",
        "olap": "Slice, rank ascending",
        "baseline": f"""
SELECT m.medicine_id, m.generic_name, m.therapeutic_class, m.cold_chain_label,
       sum(f.quantity_sold_packs) AS units,
       round(avg(f.days_to_expiry_at_sale), 1) AS avg_days_to_expiry_at_sale
FROM dwh.fact_pharmacy_sales f
JOIN dwh.dim_date d ON d.date_key = f.sale_date_key
JOIN dwh.dim_medicine m ON m.medicine_key = f.medicine_key
WHERE d.month_start_date > {LAST_MONTH_BASE} - INTERVAL '3 months'
GROUP BY m.medicine_id, m.generic_name, m.therapeutic_class, m.cold_chain_label
ORDER BY units ASC, m.medicine_id
LIMIT 10""",
        "optimized": f"""
SELECT m.medicine_id, m.generic_name, m.therapeutic_class, m.cold_chain_label,
       sum(x.units) AS units,
       round(sum(x.sum_days_to_expiry)::numeric / NULLIF(sum(x.txn_count), 0), 1) AS avg_days_to_expiry_at_sale
FROM mart.mv_sales_month_med_channel x
JOIN dwh.dim_medicine m ON m.medicine_key = x.medicine_key
WHERE x.month_start_date > {LAST_MONTH} - INTERVAL '3 months'
GROUP BY m.medicine_id, m.generic_name, m.therapeutic_class, m.cold_chain_label
ORDER BY units ASC, m.medicine_id
LIMIT 10""",
    },
    {
        "id": "Q04", "name": "Hospital forecast vs actual consumption ratio",
        "widget": "Forecast Accuracy by Hospital (heatmap)",
        "question": "For each hospital and disease category over the last 12 months, how does actual consumption compare with forecast demand?",
        "olap": "Dice (hospital x disease), ratio measure (non-additive, computed at query time)",
        "baseline": f"""
SELECT o.org_name AS hospital, dd.disease_category,
       sum(f.forecast_demand_packs) AS forecast, sum(f.consumed_qty_packs) AS consumed,
       round(sum(f.consumed_qty_packs)::numeric / NULLIF(sum(f.forecast_demand_packs), 0), 3) AS consumption_to_forecast,
       round(100.0 * (sum(f.consumed_qty_packs) - sum(f.forecast_demand_packs)) / NULLIF(sum(f.forecast_demand_packs), 0), 2) AS forecast_bias_pct
FROM dwh.fact_hospital_demand f
JOIN dwh.dim_organization o ON o.org_key = f.org_key
JOIN dwh.dim_disease dd ON dd.disease_key = f.disease_key
JOIN dwh.dim_date d ON d.date_key = f.demand_month_key
WHERE d.month_start_date > {LAST_DEMAND_MONTH_BASE} - INTERVAL '12 months'
GROUP BY o.org_name, dd.disease_category
ORDER BY hospital, dd.disease_category""",
        "optimized": f"""
SELECT o.org_name AS hospital, dd.disease_category,
       sum(x.forecast) AS forecast, sum(x.consumed) AS consumed,
       round(sum(x.consumed)::numeric / NULLIF(sum(x.forecast), 0), 3) AS consumption_to_forecast,
       round(100.0 * (sum(x.consumed) - sum(x.forecast)) / NULLIF(sum(x.forecast), 0), 2) AS forecast_bias_pct
FROM mart.mv_demand_month_hospital x
JOIN dwh.dim_organization o ON o.org_key = x.org_key
JOIN dwh.dim_disease dd ON dd.disease_key = x.disease_key
WHERE x.month_start_date > {LAST_DEMAND_MONTH} - INTERVAL '12 months'
GROUP BY o.org_name, dd.disease_category
ORDER BY hospital, dd.disease_category""",
    },
    {
        "id": "Q05", "name": "Unfulfilled demand hot-spots (hospital x medicine)",
        "widget": "Unfulfilled Demand Top 15 (table)",
        "question": "Which hospital-medicine pairs have the most unfulfilled demand in the last 12 months, with stockout days and service level?",
        "olap": "Dice, rank, top-N",
        "baseline": f"""
SELECT o.org_name AS hospital, m.generic_name, m.cold_chain_label,
       sum(f.requested_qty_packs) AS requested, sum(f.unfulfilled_demand_packs) AS unfulfilled,
       sum(f.stockout_days) AS stockout_days, round(avg(f.service_level_pct), 2) AS avg_service_level_pct
FROM dwh.fact_hospital_demand f
JOIN dwh.dim_organization o ON o.org_key = f.org_key
JOIN dwh.dim_medicine m ON m.medicine_key = f.medicine_key
JOIN dwh.dim_date d ON d.date_key = f.demand_month_key
WHERE d.month_start_date > {LAST_DEMAND_MONTH_BASE} - INTERVAL '12 months'
GROUP BY o.org_name, m.generic_name, m.cold_chain_label
ORDER BY unfulfilled DESC, hospital, m.generic_name
LIMIT 15""",
        "optimized": f"""
SELECT o.org_name AS hospital, m.generic_name, m.cold_chain_label,
       sum(x.requested) AS requested, sum(x.unfulfilled) AS unfulfilled,
       sum(x.stockout_days) AS stockout_days,
       round(sum(x.sum_service_level) / NULLIF(sum(x.demand_rows), 0), 2) AS avg_service_level_pct
FROM mart.mv_demand_month_hospital x
JOIN dwh.dim_organization o ON o.org_key = x.org_key
JOIN dwh.dim_medicine m ON m.medicine_key = x.medicine_key
WHERE x.month_start_date > {LAST_DEMAND_MONTH} - INTERVAL '12 months'
GROUP BY o.org_name, m.generic_name, m.cold_chain_label
ORDER BY unfulfilled DESC, hospital, m.generic_name
LIMIT 15""",
    },
    {
        "id": "Q06", "name": "Drill-across: pharmacy sales vs hospital demand by month & disease",
        "widget": "Sales vs Hospital Demand (combo line)",
        "question": "For each month and disease category, how do units sold through pharmacies/e-commerce compare with hospital requested, consumed and unfulfilled packs?",
        "olap": "Drill-across two fact tables on conformed Date + Medicine dimensions",
        "baseline": """
WITH s AS (
    SELECT d.month_start_date, m.disease_category, sum(f.quantity_sold_packs) AS units_sold
    FROM dwh.fact_pharmacy_sales f
    JOIN dwh.dim_date d ON d.date_key = f.sale_date_key
    JOIN dwh.dim_medicine m ON m.medicine_key = f.medicine_key
    GROUP BY 1, 2
), h AS (
    SELECT d.month_start_date, m.disease_category, sum(f.requested_qty_packs) AS requested,
           sum(f.consumed_qty_packs) AS consumed, sum(f.unfulfilled_demand_packs) AS unfulfilled
    FROM dwh.fact_hospital_demand f
    JOIN dwh.dim_date d ON d.date_key = f.demand_month_key
    JOIN dwh.dim_medicine m ON m.medicine_key = f.medicine_key
    GROUP BY 1, 2
)
SELECT to_char(COALESCE(s.month_start_date, h.month_start_date), 'YYYY-MM') AS year_month,
       COALESCE(s.disease_category, h.disease_category) AS disease_category,
       COALESCE(s.units_sold, 0) AS units_sold, COALESCE(h.requested, 0) AS hosp_requested,
       COALESCE(h.consumed, 0) AS hosp_consumed, COALESCE(h.unfulfilled, 0) AS hosp_unfulfilled
FROM s FULL OUTER JOIN h ON h.month_start_date = s.month_start_date AND h.disease_category = s.disease_category
ORDER BY 1, 2""",
        "optimized": """
SELECT to_char(x.month_start_date, 'YYYY-MM') AS year_month, m.disease_category,
       sum(x.units_sold) AS units_sold, sum(x.hosp_requested) AS hosp_requested,
       sum(x.hosp_consumed) AS hosp_consumed, sum(x.hosp_unfulfilled) AS hosp_unfulfilled
FROM mart.mv_sales_vs_demand x
JOIN dwh.dim_medicine m ON m.medicine_id = x.medicine_id AND m.is_current
GROUP BY 1, 2
ORDER BY 1, 2""",
    },
    {
        "id": "Q07", "name": "Warehouse availability for high-demand medicines",
        "widget": "Warehouse Availability vs Demand (matrix)",
        "question": "For the 10 medicines with the highest hospital requests in the last 3 months, which warehouses show stockout exposure, near-expiry stock or weak FEFO compliance?",
        "olap": "Drill-across demand -> inventory snapshot, dice by warehouse",
        "baseline": f"""
WITH top_med AS (
    SELECT f.medicine_key FROM dwh.fact_hospital_demand f
    JOIN dwh.dim_date d ON d.date_key = f.demand_month_key
    WHERE d.month_start_date > {LAST_DEMAND_MONTH_BASE} - INTERVAL '3 months'
    GROUP BY f.medicine_key ORDER BY sum(f.requested_qty_packs) DESC, f.medicine_key LIMIT 10
), inv_last AS (SELECT max(snapshot_date_key) AS mx FROM dwh.fact_inventory_snapshot)
SELECT w.warehouse_name, m.generic_name,
       round(avg(i.available_qty), 1) AS avg_available_qty,
       sum(i.stockout_flag) AS stockout_lot_days,
       sum(i.near_expiry_flag) AS near_expiry_lot_days,
       round(100 * avg(i.fefo_compliance_flag), 2) AS fefo_compliance_pct
FROM dwh.fact_inventory_snapshot i
JOIN top_med t ON t.medicine_key = i.medicine_key
JOIN dwh.dim_warehouse w ON w.warehouse_key = i.warehouse_key
JOIN dwh.dim_medicine m ON m.medicine_key = i.medicine_key
JOIN dwh.dim_date d ON d.date_key = i.snapshot_date_key
WHERE d.month_start_date > (SELECT date_trunc('month', to_date(mx::text, 'YYYYMMDD')) FROM inv_last) - INTERVAL '3 months'
GROUP BY w.warehouse_name, m.generic_name
ORDER BY stockout_lot_days DESC, w.warehouse_name, m.generic_name""",
        "optimized": f"""
WITH top_med AS (
    SELECT x.medicine_key FROM mart.mv_demand_month_hospital x
    WHERE x.month_start_date > {LAST_DEMAND_MONTH} - INTERVAL '3 months'
    GROUP BY x.medicine_key ORDER BY sum(x.requested) DESC, x.medicine_key LIMIT 10
), inv_last AS (SELECT date_trunc('month', to_date(max(snapshot_date_key)::text, 'YYYYMMDD')) AS mx
                FROM dwh.fact_inventory_snapshot)
SELECT w.warehouse_name, m.generic_name,
       round(sum(i.sum_available_qty)::numeric / NULLIF(sum(i.snapshot_rows), 0), 1) AS avg_available_qty,
       sum(i.stockout_lot_days) AS stockout_lot_days,
       sum(i.near_expiry_lot_days) AS near_expiry_lot_days,
       round(100.0 * sum(i.fefo_compliant_lot_days) / NULLIF(sum(i.snapshot_rows), 0), 2) AS fefo_compliance_pct
FROM mart.mv_inventory_month_wh_med i
JOIN top_med t ON t.medicine_key = i.medicine_key
JOIN dwh.dim_warehouse w ON w.warehouse_key = i.warehouse_key
JOIN dwh.dim_medicine m ON m.medicine_key = i.medicine_key
WHERE i.month_start_date > (SELECT mx FROM inv_last) - INTERVAL '3 months'
GROUP BY w.warehouse_name, m.generic_name
ORDER BY stockout_lot_days DESC, w.warehouse_name, m.generic_name""",
    },
    {
        "id": "Q08", "name": "Commercial segmentation: channel x segment x payer x promotion",
        "widget": "Promotion & Segment Profitability (table)",
        "question": "How do net sales, gross-margin %, return rate and days-to-expiry-at-sale vary by channel, customer segment, payer and promotion, for cold-chain vs ambient medicines?",
        "olap": "Dice on 4 dimensions + cold-chain slice",
        "baseline": """
SELECT c.channel_name, cs.segment_name, p.payer_type, pr.promotion_code, m.cold_chain_flag,
       round(sum(f.net_sales_usd), 2) AS net_sales,
       round(100 * sum(f.gross_margin_usd) / NULLIF(sum(f.net_sales_usd), 0), 2) AS gm_pct,
       round(100.0 * sum(f.returned_qty_packs) / NULLIF(sum(f.quantity_sold_packs), 0), 3) AS return_rate_pct,
       round(avg(f.days_to_expiry_at_sale), 1) AS avg_days_to_expiry
FROM dwh.fact_pharmacy_sales f
JOIN dwh.dim_channel c ON c.channel_key = f.channel_key
JOIN dwh.dim_customer_segment cs ON cs.segment_key = f.segment_key
JOIN dwh.dim_payer p ON p.payer_key = f.payer_key
JOIN dwh.dim_promotion pr ON pr.promotion_key = f.promotion_key
JOIN dwh.dim_medicine m ON m.medicine_key = f.medicine_key
GROUP BY c.channel_name, cs.segment_name, p.payer_type, pr.promotion_code, m.cold_chain_flag
ORDER BY net_sales DESC, 1, 2, 3, 4, 5""",
        "optimized": """
SELECT c.channel_name, cs.segment_name, p.payer_type, pr.promotion_code, x.cold_chain_flag,
       round(sum(x.net_sales), 2) AS net_sales,
       round(100 * sum(x.gross_margin) / NULLIF(sum(x.net_sales), 0), 2) AS gm_pct,
       round(100.0 * sum(x.returned_units) / NULLIF(sum(x.units), 0), 3) AS return_rate_pct,
       round(sum(x.sum_days_to_expiry)::numeric / NULLIF(sum(x.txn_count), 0), 1) AS avg_days_to_expiry
FROM mart.mv_sales_commercial x
JOIN dwh.dim_channel c ON c.channel_key = x.channel_key
JOIN dwh.dim_customer_segment cs ON cs.segment_key = x.segment_key
JOIN dwh.dim_payer p ON p.payer_key = x.payer_key
JOIN dwh.dim_promotion pr ON pr.promotion_key = x.promotion_key
GROUP BY c.channel_name, cs.segment_name, p.payer_type, pr.promotion_code, x.cold_chain_flag
ORDER BY net_sales DESC, 1, 2, 3, 4, 5""",
    },
    {
        "id": "Q09", "name": "Regional roll-up: region > state (ROLLUP)",
        "widget": "Revenue by Region (map / treemap)",
        "question": "What are net sales by region and state over the last 12 months, including regional subtotals and the grand total?",
        "olap": "ROLLUP hierarchy Region -> State with subtotals",
        "baseline": f"""
SELECT COALESCE(o.region, 'ALL REGIONS') AS region, COALESCE(o.state_code, 'ALL STATES') AS state,
       round(sum(f.net_sales_usd), 2) AS net_sales, sum(f.quantity_sold_packs) AS units
FROM dwh.fact_pharmacy_sales f
JOIN dwh.dim_organization o ON o.org_key = f.org_key
JOIN dwh.dim_date d ON d.date_key = f.sale_date_key
WHERE d.month_start_date > {LAST_MONTH_BASE} - INTERVAL '12 months'
GROUP BY ROLLUP (o.region, o.state_code)
ORDER BY 1, 2""",
        "optimized": f"""
SELECT COALESCE(o.region, 'ALL REGIONS') AS region, COALESCE(o.state_code, 'ALL STATES') AS state,
       round(sum(x.net_sales), 2) AS net_sales, sum(x.units) AS units
FROM mart.mv_sales_month_org x
JOIN dwh.dim_organization o ON o.org_key = x.org_key
WHERE x.month_start_date > {LAST_MONTH} - INTERVAL '12 months'
GROUP BY ROLLUP (o.region, o.state_code)
ORDER BY 1, 2""",
    },
    {
        "id": "Q10", "name": "Channel x disease category cube",
        "widget": "Channel x Disease Matrix (heatmap)",
        "question": "What are net sales for every combination of channel and disease category, with all subtotals (data cube)?",
        "olap": "CUBE (channel, disease category)",
        "baseline": """
SELECT COALESCE(c.channel_name, 'ALL CHANNELS') AS channel, COALESCE(m.disease_category, 'ALL DISEASES') AS disease,
       round(sum(f.net_sales_usd), 2) AS net_sales
FROM dwh.fact_pharmacy_sales f
JOIN dwh.dim_channel c ON c.channel_key = f.channel_key
JOIN dwh.dim_medicine m ON m.medicine_key = f.medicine_key
GROUP BY CUBE (c.channel_name, m.disease_category)
ORDER BY 1, 2""",
        "optimized": """
SELECT COALESCE(c.channel_name, 'ALL CHANNELS') AS channel, COALESCE(m.disease_category, 'ALL DISEASES') AS disease,
       round(sum(x.net_sales), 2) AS net_sales
FROM mart.mv_sales_month_med_channel x
JOIN dwh.dim_channel c ON c.channel_key = x.channel_key
JOIN dwh.dim_medicine m ON m.medicine_key = x.medicine_key
GROUP BY CUBE (c.channel_name, m.disease_category)
ORDER BY 1, 2""",
    },
    {
        "id": "Q11", "name": "Month-over-month growth by channel",
        "widget": "MoM Growth % (line)",
        "question": "What is the month-over-month net-sales growth rate of each channel?",
        "olap": "Window function LAG over time hierarchy",
        "baseline": """
WITH m AS (
    SELECT d.month_start_date, f.channel_key, sum(f.net_sales_usd) AS net_sales
    FROM dwh.fact_pharmacy_sales f JOIN dwh.dim_date d ON d.date_key = f.sale_date_key
    GROUP BY 1, 2
)
SELECT to_char(m.month_start_date, 'YYYY-MM') AS year_month, c.channel_name, round(m.net_sales, 2) AS net_sales,
       round(100 * (m.net_sales - lag(m.net_sales) OVER w) / NULLIF(lag(m.net_sales) OVER w, 0), 2) AS mom_growth_pct
FROM m JOIN dwh.dim_channel c ON c.channel_key = m.channel_key
WINDOW w AS (PARTITION BY m.channel_key ORDER BY m.month_start_date)
ORDER BY 1, 2""",
        "optimized": """
WITH m AS (
    SELECT month_start_date, channel_key, sum(net_sales) AS net_sales
    FROM mart.mv_sales_month_med_channel GROUP BY 1, 2
)
SELECT to_char(m.month_start_date, 'YYYY-MM') AS year_month, c.channel_name, round(m.net_sales, 2) AS net_sales,
       round(100 * (m.net_sales - lag(m.net_sales) OVER w) / NULLIF(lag(m.net_sales) OVER w, 0), 2) AS mom_growth_pct
FROM m JOIN dwh.dim_channel c ON c.channel_key = m.channel_key
WINDOW w AS (PARTITION BY m.channel_key ORDER BY m.month_start_date)
ORDER BY 1, 2""",
    },
    {
        "id": "Q12", "name": "KPI cards: latest month vs previous month",
        "widget": "KPI Cards (Net Sales, GM %, Transactions, Return Rate)",
        "question": "What were net sales, gross-margin %, transaction count and return rate in the latest month, and how do they compare with the previous month?",
        "olap": "Slice (two months), period comparison",
        "baseline": f"""
SELECT to_char(d.month_start_date, 'YYYY-MM') AS year_month,
       round(sum(f.net_sales_usd), 2) AS net_sales,
       round(100 * sum(f.gross_margin_usd) / NULLIF(sum(f.net_sales_usd), 0), 2) AS gm_pct,
       count(*) AS transactions,
       round(100.0 * sum(f.returned_qty_packs) / NULLIF(sum(f.quantity_sold_packs), 0), 3) AS return_rate_pct
FROM dwh.fact_pharmacy_sales f JOIN dwh.dim_date d ON d.date_key = f.sale_date_key
WHERE d.month_start_date >= {LAST_MONTH_BASE} - INTERVAL '1 month'
GROUP BY 1 ORDER BY 1""",
        "optimized": f"""
SELECT to_char(x.month_start_date, 'YYYY-MM') AS year_month,
       round(sum(x.net_sales), 2) AS net_sales,
       round(100 * sum(x.gross_margin) / NULLIF(sum(x.net_sales), 0), 2) AS gm_pct,
       sum(x.txn_count) AS transactions,
       round(100.0 * sum(x.returned_units) / NULLIF(sum(x.units), 0), 3) AS return_rate_pct
FROM mart.mv_sales_month_med_channel x
WHERE x.month_start_date >= {LAST_MONTH} - INTERVAL '1 month'
GROUP BY 1 ORDER BY 1""",
    },
    {
        "id": "Q13", "name": "Warehouse contribution to sales by channel",
        "widget": "Sales by Source Warehouse (stacked bar)",
        "question": "Which source warehouses feed the most revenue in each channel over the last 12 months, and how much of it is cold-chain product?",
        "olap": "Dice warehouse x channel, cold-chain slice",
        "baseline": f"""
SELECT w.warehouse_name, c.channel_name,
       round(sum(f.net_sales_usd), 2) AS net_sales,
       round(sum(f.net_sales_usd) FILTER (WHERE m.cold_chain_flag), 2) AS cold_chain_net_sales
FROM dwh.fact_pharmacy_sales f
JOIN dwh.dim_warehouse w ON w.warehouse_key = f.warehouse_key
JOIN dwh.dim_channel c ON c.channel_key = f.channel_key
JOIN dwh.dim_medicine m ON m.medicine_key = f.medicine_key
JOIN dwh.dim_date d ON d.date_key = f.sale_date_key
WHERE d.month_start_date > {LAST_MONTH_BASE} - INTERVAL '12 months'
GROUP BY w.warehouse_name, c.channel_name
ORDER BY 1, 2""",
        "optimized": f"""
SELECT w.warehouse_name, c.channel_name,
       round(sum(x.net_sales), 2) AS net_sales,
       round(sum(x.net_sales) FILTER (WHERE m.cold_chain_flag), 2) AS cold_chain_net_sales
FROM mart.mv_sales_month_med_channel x
JOIN dwh.dim_warehouse w ON w.warehouse_key = x.warehouse_key
JOIN dwh.dim_channel c ON c.channel_key = x.channel_key
JOIN dwh.dim_medicine m ON m.medicine_key = x.medicine_key
WHERE x.month_start_date > {LAST_MONTH} - INTERVAL '12 months'
GROUP BY w.warehouse_name, c.channel_name
ORDER BY 1, 2""",
    },
]
