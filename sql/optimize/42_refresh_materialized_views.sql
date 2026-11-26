-- Refreshed by Airflow after every gold load. CONCURRENTLY = dashboards keep reading.
-- Order matters: mv_sales_vs_demand is built from two other MVs.
REFRESH MATERIALIZED VIEW CONCURRENTLY mart.mv_sales_month_med_channel;
REFRESH MATERIALIZED VIEW CONCURRENTLY mart.mv_sales_commercial;
REFRESH MATERIALIZED VIEW CONCURRENTLY mart.mv_sales_month_org;
REFRESH MATERIALIZED VIEW CONCURRENTLY mart.mv_demand_month_hospital;
REFRESH MATERIALIZED VIEW CONCURRENTLY mart.mv_sales_vs_demand;
REFRESH MATERIALIZED VIEW CONCURRENTLY mart.mv_inventory_month_wh_med;
