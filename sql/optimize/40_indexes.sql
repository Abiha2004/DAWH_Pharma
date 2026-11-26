-- ============================================================================
-- OLAP indexing strategy for the SDI star
--  * B-tree composite indexes on the most-used fact foreign-key combinations
--  * BRIN on the (append-only, date-ordered) snapshot fact: tiny, ideal for ranges
--  * Sales fact is RANGE-partitioned by year, so date filters prune partitions
--  * Partial index on current dimension versions (SCD2 lookups)
-- ============================================================================
CREATE INDEX IF NOT EXISTS ix_fps_date_med     ON dwh.fact_pharmacy_sales (sale_date_key, medicine_key);
CREATE INDEX IF NOT EXISTS ix_fps_med_channel  ON dwh.fact_pharmacy_sales (medicine_key, channel_key);
CREATE INDEX IF NOT EXISTS ix_fps_org          ON dwh.fact_pharmacy_sales (org_key, sale_date_key);
CREATE INDEX IF NOT EXISTS ix_fps_wh_med       ON dwh.fact_pharmacy_sales (warehouse_key, medicine_key);
CREATE INDEX IF NOT EXISTS ix_fps_lot          ON dwh.fact_pharmacy_sales (batch_lot_key);
CREATE INDEX IF NOT EXISTS ix_fps_batch        ON dwh.fact_pharmacy_sales (_batch_id);

CREATE INDEX IF NOT EXISTS ix_fhd_month_med    ON dwh.fact_hospital_demand (demand_month_key, medicine_key);
CREATE INDEX IF NOT EXISTS ix_fhd_org_med      ON dwh.fact_hospital_demand (org_key, medicine_key);
CREATE INDEX IF NOT EXISTS ix_fhd_disease      ON dwh.fact_hospital_demand (disease_key);

CREATE INDEX IF NOT EXISTS brin_fis_date       ON dwh.fact_inventory_snapshot USING brin (snapshot_date_key);
CREATE INDEX IF NOT EXISTS ix_fis_wh_med       ON dwh.fact_inventory_snapshot (warehouse_key, medicine_key);
CREATE INDEX IF NOT EXISTS ix_fis_lot          ON dwh.fact_inventory_snapshot (batch_lot_key);

CREATE INDEX IF NOT EXISTS ix_dim_org_cur      ON dwh.dim_organization (org_natural_key) WHERE is_current;
CREATE INDEX IF NOT EXISTS ix_dim_med_class    ON dwh.dim_medicine (therapeutic_class, disease_category);

ANALYZE dwh.fact_pharmacy_sales;
ANALYZE dwh.fact_hospital_demand;
ANALYZE dwh.fact_inventory_snapshot;
