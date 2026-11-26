-- ============================================================================
-- Pre-aggregated summary tables (materialized views) for sub-second BI queries.
-- Each MV has a UNIQUE index so it can be refreshed CONCURRENTLY (readers are
-- never blocked while Airflow refreshes it after each load).
-- Grains were chosen from the dashboard widgets they serve.
-- ============================================================================

-- 1) Sales by month x medicine x channel x source warehouse  (~30k rows instead of 600k+)
CREATE MATERIALIZED VIEW IF NOT EXISTS mart.mv_sales_month_med_channel AS
SELECT d.month_start_date, d.year_month, f.medicine_key, f.channel_key, f.warehouse_key,
       count(*)                       AS txn_count,
       sum(f.quantity_sold_packs)     AS units,
       sum(f.gross_sales_usd)         AS gross_sales,
       sum(f.discount_usd)            AS discount,
       sum(f.net_sales_usd)           AS net_sales,
       sum(f.cogs_usd)                AS cogs,
       sum(f.gross_margin_usd)        AS gross_margin,
       sum(f.returned_qty_packs)      AS returned_units,
       sum(f.days_to_expiry_at_sale)  AS sum_days_to_expiry
FROM dwh.fact_pharmacy_sales f
JOIN dwh.dim_date d ON d.date_key = f.sale_date_key
GROUP BY 1, 2, 3, 4, 5;
CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_smmc ON mart.mv_sales_month_med_channel (month_start_date, medicine_key, channel_key, warehouse_key);

-- 2) Commercial segmentation: month x channel x segment x payer x promotion
CREATE MATERIALIZED VIEW IF NOT EXISTS mart.mv_sales_commercial AS
SELECT d.month_start_date, f.channel_key, f.segment_key, f.payer_key, f.promotion_key,
       m.cold_chain_flag,
       count(*) AS txn_count, sum(f.quantity_sold_packs) AS units, sum(f.net_sales_usd) AS net_sales,
       sum(f.gross_margin_usd) AS gross_margin, sum(f.discount_usd) AS discount,
       sum(f.returned_qty_packs) AS returned_units, sum(f.days_to_expiry_at_sale) AS sum_days_to_expiry
FROM dwh.fact_pharmacy_sales f
JOIN dwh.dim_date d ON d.date_key = f.sale_date_key
JOIN dwh.dim_medicine m ON m.medicine_key = f.medicine_key
GROUP BY 1, 2, 3, 4, 5, 6;
CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_sc ON mart.mv_sales_commercial
    (month_start_date, channel_key, segment_key, payer_key, promotion_key, cold_chain_flag);

-- 3) Sales by month x organisation (region roll-ups, pharmacy rankings)
CREATE MATERIALIZED VIEW IF NOT EXISTS mart.mv_sales_month_org AS
SELECT d.month_start_date, f.org_key, f.channel_key,
       count(*) AS txn_count, sum(f.quantity_sold_packs) AS units,
       sum(f.net_sales_usd) AS net_sales, sum(f.gross_margin_usd) AS gross_margin
FROM dwh.fact_pharmacy_sales f
JOIN dwh.dim_date d ON d.date_key = f.sale_date_key
GROUP BY 1, 2, 3;
CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_smo ON mart.mv_sales_month_org (month_start_date, org_key, channel_key);

-- 4) Hospital demand by month x hospital x medicine-level keys (forecast accuracy, unmet demand)
CREATE MATERIALIZED VIEW IF NOT EXISTS mart.mv_demand_month_hospital AS
SELECT d.month_start_date, f.org_key, f.medicine_key, f.disease_key,
       sum(f.forecast_demand_packs) AS forecast, sum(f.requested_qty_packs) AS requested,
       sum(f.approved_qty_packs) AS approved, sum(f.received_qty_packs) AS received,
       sum(f.dispensed_qty_packs) AS dispensed, sum(f.consumed_qty_packs) AS consumed,
       sum(f.wastage_qty_packs) AS wastage, sum(f.unfulfilled_demand_packs) AS unfulfilled,
       sum(f.stockout_days) AS stockout_days, count(*) AS demand_rows, sum(f.service_level_pct) AS sum_service_level
FROM dwh.fact_hospital_demand f
JOIN dwh.dim_date d ON d.date_key = f.demand_month_key
GROUP BY 1, 2, 3, 4;
CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_dmh ON mart.mv_demand_month_hospital (month_start_date, org_key, medicine_key, disease_key);

-- 5) Drill-across: retail/e-com sales vs hospital demand on conformed Month x Medicine
CREATE MATERIALIZED VIEW IF NOT EXISTS mart.mv_sales_vs_demand AS
WITH s AS (
    SELECT month_start_date, m.medicine_id, sum(units) AS units_sold, sum(net_sales) AS net_sales
    FROM mart.mv_sales_month_med_channel x JOIN dwh.dim_medicine m ON m.medicine_key = x.medicine_key
    GROUP BY 1, 2
), h AS (
    SELECT month_start_date, m.medicine_id, sum(forecast) AS forecast, sum(requested) AS requested,
           sum(received) AS received, sum(consumed) AS consumed, sum(unfulfilled) AS unfulfilled
    FROM mart.mv_demand_month_hospital x JOIN dwh.dim_medicine m ON m.medicine_key = x.medicine_key
    GROUP BY 1, 2
)
SELECT COALESCE(s.month_start_date, h.month_start_date) AS month_start_date,
       COALESCE(s.medicine_id, h.medicine_id)           AS medicine_id,
       COALESCE(s.units_sold, 0) AS units_sold, COALESCE(s.net_sales, 0) AS net_sales,
       COALESCE(h.forecast, 0) AS hosp_forecast, COALESCE(h.requested, 0) AS hosp_requested,
       COALESCE(h.received, 0) AS hosp_received, COALESCE(h.consumed, 0) AS hosp_consumed,
       COALESCE(h.unfulfilled, 0) AS hosp_unfulfilled
FROM s FULL OUTER JOIN h ON h.month_start_date = s.month_start_date AND h.medicine_id = s.medicine_id;
CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_svd ON mart.mv_sales_vs_demand (month_start_date, medicine_id);

-- 6) Warehouse availability context: month x warehouse x medicine
CREATE MATERIALIZED VIEW IF NOT EXISTS mart.mv_inventory_month_wh_med AS
SELECT d.month_start_date, f.warehouse_key, f.medicine_key,
       count(*) AS snapshot_rows,                       -- store sums + counts (not averages)
       sum(f.available_qty) AS sum_available_qty,       -- so any roll-up stays exact
       sum(f.stockout_flag) AS stockout_lot_days,
       sum(f.near_expiry_flag) AS near_expiry_lot_days,
       sum(f.fefo_compliance_flag) AS fefo_compliant_lot_days,
       sum(f.expired_qty) AS expired_qty,
       sum(f.damaged_qty) AS damaged_qty,
       sum(f.shipped_qty) AS shipped_qty
FROM dwh.fact_inventory_snapshot f
JOIN dwh.dim_date d ON d.date_key = f.snapshot_date_key
GROUP BY 1, 2, 3;
CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_imwm ON mart.mv_inventory_month_wh_med (month_start_date, warehouse_key, medicine_key);
