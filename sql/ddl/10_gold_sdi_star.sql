-- ============================================================================
-- GOLD layer: Sales & Demand Intelligence (SDI) star schema / fact constellation
--
--  Facts
--    fact_pharmacy_sales      transaction fact     1 row = 1 sales transaction
--    fact_hospital_demand     periodic snapshot    1 row = 1 hospital x medicine x month
--    fact_inventory_snapshot  periodic snapshot    1 row = 1 warehouse lot x day
--                             (warehouse availability context for SDI; it is the
--                              conformed seed of the Phase 2 inventory mart)
--  Dimensions
--    dim_date (role-playing), dim_medicine (SCD2), dim_organization (SCD2),
--    dim_channel, dim_warehouse, dim_batch_lot, dim_disease,
--    dim_customer_segment, dim_payer, dim_promotion,
--    dim_sales_profile (JUNK), dim_demand_profile (JUNK)
--  Every dimension has an "Unknown" member (key -1) so no fact row is ever lost.
-- ============================================================================

-- ---------------------------------------------------------------- DIM_DATE
CREATE TABLE IF NOT EXISTS dwh.dim_date (
    date_key          INTEGER PRIMARY KEY,          -- YYYYMMDD smart key
    full_date         DATE UNIQUE,
    day_of_month      SMALLINT,
    day_name          TEXT,
    day_of_week_iso   SMALLINT,
    is_weekend        BOOLEAN,
    week_of_year      SMALLINT,
    month_number      SMALLINT,
    month_name        TEXT,
    month_short       TEXT,
    quarter_number    SMALLINT,
    quarter_name      TEXT,                          -- e.g. 2025-Q3
    year_number       SMALLINT,
    year_month        TEXT,                          -- e.g. 2025-07
    month_start_date  DATE,
    is_month_end      BOOLEAN
);

-- Role-playing views (one physical table, several business roles)
CREATE OR REPLACE VIEW dwh.dim_sale_date        AS SELECT * FROM dwh.dim_date;
CREATE OR REPLACE VIEW dwh.dim_delivery_date    AS SELECT * FROM dwh.dim_date;
CREATE OR REPLACE VIEW dwh.dim_expiry_date      AS SELECT * FROM dwh.dim_date;
CREATE OR REPLACE VIEW dwh.dim_demand_month     AS SELECT * FROM dwh.dim_date WHERE day_of_month = 1 OR date_key = -1;
CREATE OR REPLACE VIEW dwh.dim_snapshot_date    AS SELECT * FROM dwh.dim_date;

-- ---------------------------------------------------------------- DIM_MEDICINE (SCD Type 2)
CREATE TABLE IF NOT EXISTS dwh.dim_medicine (
    medicine_key               SERIAL PRIMARY KEY,
    medicine_id                TEXT NOT NULL,          -- natural key
    internal_sku               TEXT,
    gtin14                     TEXT,
    generic_name               TEXT,
    brand_name                 TEXT,
    disease_category           TEXT,
    icd10_category             TEXT,
    atc_code                   TEXT,
    therapeutic_class          TEXT,
    dosage_form                TEXT,
    strength                   TEXT,
    route_of_administration    TEXT,
    pack_size                  INTEGER,
    prescription_class         TEXT,
    controlled_flag            BOOLEAN,
    cold_chain_flag            BOOLEAN,
    cold_chain_label           TEXT,                   -- 'Cold chain (2-8 C)' / 'Ambient (15-25 C)'
    storage_min_temp_c         NUMERIC(6,2),
    storage_max_temp_c         NUMERIC(6,2),
    shelf_life_months          INTEGER,
    unit_mfg_cost_usd          NUMERIC(12,4),          -- SCD2 tracked
    wholesale_price_usd        NUMERIC(12,4),          -- SCD2 tracked
    rrp_usd                    NUMERIC(12,4),          -- SCD2 tracked
    price_band                 TEXT,                   -- derived: Low / Mid / High / Premium
    product_status             TEXT,                   -- SCD2 tracked
    -- SCD2 housekeeping
    scd_hash                   TEXT,
    effective_from             DATE NOT NULL,
    effective_to               DATE NOT NULL DEFAULT DATE '9999-12-31',
    is_current                 BOOLEAN NOT NULL DEFAULT TRUE,
    version_no                 INTEGER NOT NULL DEFAULT 1,
    _batch_id                  INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_dim_medicine_ver ON dwh.dim_medicine (medicine_id, effective_from);
CREATE INDEX IF NOT EXISTS ix_dim_medicine_cur ON dwh.dim_medicine (medicine_id) WHERE is_current;

-- ---------------------------------------------------------------- DIM_ORGANIZATION (SCD Type 2)
-- Pharmacies (chain, independent, hospital pharmacies) and hospitals in one
-- dimension, keyed by a source-prefixed natural key so IDs cannot collide.
CREATE TABLE IF NOT EXISTS dwh.dim_organization (
    org_key                SERIAL PRIMARY KEY,
    org_natural_key        TEXT NOT NULL,              -- PHARMACY|PHR014 , HOSPITAL|HOSP001
    org_source_type        TEXT,                       -- PHARMACY | HOSPITAL
    org_id                 TEXT,
    org_name               TEXT,                       -- SCD2 tracked
    org_type               TEXT,                       -- Chain / Independent / Hospital Pharmacy / hospital type
    gln                    TEXT,
    parent_hospital_id     TEXT,                       -- hospital pharmacy -> hospital (outrigger-style link)
    city                   TEXT,                       -- SCD2 tracked
    state_code             TEXT,
    region                 TEXT,
    country_code           TEXT,
    licensed_beds          INTEGER,
    icu_beds               INTEGER,
    avg_occupancy_pct      NUMERIC(6,2),
    trauma_level           TEXT,
    catchment_population   INTEGER,
    hospital_size_band     TEXT,                       -- derived from beds
    scd_hash               TEXT,
    effective_from         DATE NOT NULL,
    effective_to           DATE NOT NULL DEFAULT DATE '9999-12-31',
    is_current             BOOLEAN NOT NULL DEFAULT TRUE,
    version_no             INTEGER NOT NULL DEFAULT 1,
    _batch_id              INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_dim_org_ver ON dwh.dim_organization (org_natural_key, effective_from);

-- ---------------------------------------------------------------- DIM_CHANNEL (static, Type 0)
CREATE TABLE IF NOT EXISTS dwh.dim_channel (
    channel_key        SMALLINT PRIMARY KEY,
    channel_code       TEXT UNIQUE,
    channel_name       TEXT,
    channel_group      TEXT,     -- Retail | Institutional | Digital
    source_process     TEXT,     -- Sales | Demand
    online_flag        BOOLEAN,
    institutional_flag BOOLEAN
);

-- ---------------------------------------------------------------- DIM_WAREHOUSE (Type 1)
CREATE TABLE IF NOT EXISTS dwh.dim_warehouse (
    warehouse_key          SERIAL PRIMARY KEY,
    warehouse_id           TEXT UNIQUE NOT NULL,
    warehouse_gln          TEXT,
    warehouse_name         TEXT,
    city                   TEXT,
    state_code             TEXT,
    region                 TEXT,
    country_code           TEXT,
    total_capacity_packs   INTEGER,
    cold_zone_count        INTEGER,       -- number of 2-8 C storage zones
    ambient_zone_count     INTEGER,
    cold_chain_capable     BOOLEAN,
    _batch_id              INTEGER
);

-- ---------------------------------------------------------------- DIM_BATCH_LOT (Type 1)
CREATE TABLE IF NOT EXISTS dwh.dim_batch_lot (
    batch_lot_key          SERIAL PRIMARY KEY,
    warehouse_lot_id       TEXT UNIQUE NOT NULL,
    production_batch_id    TEXT,
    manufacturing_order_id TEXT,
    medicine_id            TEXT,
    warehouse_id           TEXT,
    plant_id               TEXT,
    storage_zone_id        TEXT,
    storage_condition      TEXT,
    order_date             DATE,
    production_completed   DATE,
    batch_release_date     DATE,
    batch_expiry_date      DATE,
    batch_status           TEXT,
    quality_test_status    TEXT,
    yield_pct              NUMERIC(7,2),
    _batch_id              INTEGER
);

-- ---------------------------------------------------------------- DIM_DISEASE (Type 1)
CREATE TABLE IF NOT EXISTS dwh.dim_disease (
    disease_key        SERIAL PRIMARY KEY,
    disease_category   TEXT UNIQUE NOT NULL,
    icd10_category     TEXT,
    disease_group      TEXT       -- Chronic / Acute / Mixed
);

-- ---------------------------------------------------------------- small conformed dimensions
CREATE TABLE IF NOT EXISTS dwh.dim_customer_segment (
    segment_key        SERIAL PRIMARY KEY,
    segment_name       TEXT UNIQUE NOT NULL,
    segment_group      TEXT,       -- Chronic care | Acute care | Pediatric | Wellness
    age_group          TEXT
);

CREATE TABLE IF NOT EXISTS dwh.dim_payer (
    payer_key          SERIAL PRIMARY KEY,
    payer_type         TEXT UNIQUE NOT NULL,
    payer_group        TEXT        -- Third-party | Out-of-pocket | Institutional
);

CREATE TABLE IF NOT EXISTS dwh.dim_promotion (
    promotion_key      SERIAL PRIMARY KEY,
    promotion_code     TEXT UNIQUE NOT NULL,
    promotion_type     TEXT,       -- Loyalty | Seasonal | Online | Wellness | No promotion
    discount_pct       NUMERIC(5,2),
    is_promotion       BOOLEAN
);

-- ---------------------------------------------------------------- JUNK dimensions
-- Low-cardinality flags/indicators that would otherwise clutter the fact table.
CREATE TABLE IF NOT EXISTS dwh.dim_sales_profile (
    sales_profile_key          SERIAL PRIMARY KEY,
    payment_method             TEXT,
    prescription_required_flag BOOLEAN,
    ecommerce_delivery_status  TEXT,
    return_flag                BOOLEAN,
    sale_status                TEXT,
    delivery_otif_flag         BOOLEAN,
    UNIQUE NULLS NOT DISTINCT (payment_method, prescription_required_flag, ecommerce_delivery_status,
                               return_flag, sale_status, delivery_otif_flag)
);

CREATE TABLE IF NOT EXISTS dwh.dim_demand_profile (
    demand_profile_key         SERIAL PRIMARY KEY,
    medication_request_status  TEXT,
    formulary_status           TEXT,
    UNIQUE NULLS NOT DISTINCT (medication_request_status, formulary_status)
);

-- ============================================================================ FACTS
-- FACT_PHARMACY_SALES: transaction grain, RANGE-partitioned by year on sale_date_key
CREATE TABLE IF NOT EXISTS dwh.fact_pharmacy_sales (
    sales_fact_key           BIGSERIAL,
    sale_date_key            INTEGER  NOT NULL,   -- role: Sale Date
    delivery_date_key        INTEGER  NOT NULL,   -- role: Pharmacy Delivery Date
    expiry_date_key          INTEGER  NOT NULL,   -- role: Batch Expiry Date
    sale_hour                SMALLINT,
    medicine_key             INTEGER  NOT NULL,
    org_key                  INTEGER  NOT NULL,
    channel_key              SMALLINT NOT NULL,
    warehouse_key            INTEGER  NOT NULL,
    batch_lot_key            INTEGER  NOT NULL,
    disease_key              INTEGER  NOT NULL,
    segment_key              INTEGER  NOT NULL,
    payer_key                INTEGER  NOT NULL,
    promotion_key            INTEGER  NOT NULL,
    sales_profile_key        INTEGER  NOT NULL,   -- junk dimension
    -- degenerate dimensions
    sales_transaction_id     TEXT     NOT NULL,
    pharmacy_order_id        TEXT,
    prescription_id          TEXT,
    ingest_mode              TEXT,                -- batch | stream (lineage)
    -- measures
    quantity_sold_packs      INTEGER,             -- additive
    unit_retail_price_usd    NUMERIC(12,4),       -- non-additive
    gross_sales_usd          NUMERIC(14,2),       -- additive
    discount_usd             NUMERIC(14,2),       -- additive
    net_sales_usd            NUMERIC(14,2),       -- additive
    sales_tax_usd            NUMERIC(14,2),       -- additive
    cogs_usd                 NUMERIC(14,2),       -- additive
    gross_margin_usd         NUMERIC(14,2),       -- additive
    returned_qty_packs       INTEGER,             -- additive
    days_to_expiry_at_sale   INTEGER,             -- non-additive (AVG / MIN)
    _batch_id                INTEGER,
    _loaded_at               TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (sales_fact_key, sale_date_key),
    UNIQUE (sales_transaction_id, sale_date_key)
) PARTITION BY RANGE (sale_date_key);

CREATE TABLE IF NOT EXISTS dwh.fact_pharmacy_sales_2023 PARTITION OF dwh.fact_pharmacy_sales FOR VALUES FROM (20230101) TO (20240101);
CREATE TABLE IF NOT EXISTS dwh.fact_pharmacy_sales_2024 PARTITION OF dwh.fact_pharmacy_sales FOR VALUES FROM (20240101) TO (20250101);
CREATE TABLE IF NOT EXISTS dwh.fact_pharmacy_sales_2025 PARTITION OF dwh.fact_pharmacy_sales FOR VALUES FROM (20250101) TO (20260101);
CREATE TABLE IF NOT EXISTS dwh.fact_pharmacy_sales_2026 PARTITION OF dwh.fact_pharmacy_sales FOR VALUES FROM (20260101) TO (20270101);
CREATE TABLE IF NOT EXISTS dwh.fact_pharmacy_sales_default PARTITION OF dwh.fact_pharmacy_sales DEFAULT;

-- FACT_HOSPITAL_DEMAND: periodic snapshot, monthly grain
CREATE TABLE IF NOT EXISTS dwh.fact_hospital_demand (
    demand_fact_key              BIGSERIAL PRIMARY KEY,
    demand_month_key             INTEGER  NOT NULL,   -- role: Demand Month (1st of month)
    medicine_key                 INTEGER  NOT NULL,
    org_key                      INTEGER  NOT NULL,   -- hospital
    hospital_pharmacy_org_key    INTEGER  NOT NULL,   -- role-playing use of dim_organization
    channel_key                  SMALLINT NOT NULL,
    disease_key                  INTEGER  NOT NULL,
    demand_profile_key           INTEGER  NOT NULL,   -- junk dimension
    hospital_demand_record_id    TEXT UNIQUE NOT NULL, -- degenerate
    estimated_disease_cases      INTEGER,
    disease_related_admissions   INTEGER,
    outpatient_encounters        INTEGER,
    forecast_demand_packs        INTEGER,   -- additive
    requested_qty_packs          INTEGER,   -- additive
    approved_qty_packs           INTEGER,   -- additive
    received_qty_packs           INTEGER,   -- additive
    emergency_request_qty_packs  INTEGER,   -- additive
    dispensed_qty_packs          INTEGER,   -- additive
    consumed_qty_packs           INTEGER,   -- additive
    wastage_qty_packs            INTEGER,   -- additive
    unfulfilled_demand_packs     INTEGER,   -- additive
    opening_stock_packs          INTEGER,   -- semi-additive (not across time)
    closing_stock_packs          INTEGER,   -- semi-additive (not across time)
    stockout_days                INTEGER,   -- additive (medicine-days)
    service_level_pct            NUMERIC(7,2),  -- non-additive
    avg_lead_time_days           NUMERIC(7,2),  -- non-additive
    demand_trend_index           NUMERIC(8,4),  -- non-additive
    _batch_id                    INTEGER,
    _loaded_at                   TIMESTAMPTZ DEFAULT now()
);

-- FACT_INVENTORY_SNAPSHOT: periodic snapshot, daily grain
CREATE TABLE IF NOT EXISTS dwh.fact_inventory_snapshot (
    inventory_fact_key       BIGSERIAL PRIMARY KEY,
    snapshot_date_key        INTEGER NOT NULL,
    expiry_date_key          INTEGER NOT NULL,
    medicine_key             INTEGER NOT NULL,
    warehouse_key            INTEGER NOT NULL,
    batch_lot_key            INTEGER NOT NULL,
    inventory_snapshot_id    TEXT UNIQUE NOT NULL,   -- degenerate
    inventory_status         TEXT,
    opening_qty              INTEGER,   -- semi-additive
    received_qty             INTEGER,   -- additive
    available_qty            INTEGER,   -- semi-additive
    reserved_qty             INTEGER,   -- semi-additive
    picked_qty               INTEGER,   -- additive
    shipped_qty              INTEGER,   -- additive
    transfer_in_qty          INTEGER,   -- additive
    transfer_out_qty         INTEGER,   -- additive
    damaged_qty              INTEGER,   -- additive
    expired_qty              INTEGER,   -- additive
    closing_qty              INTEGER,   -- semi-additive
    days_to_expiry           INTEGER,   -- non-additive
    stockout_flag            SMALLINT,  -- 0/1, use SUM for counts, AVG for rates
    near_expiry_flag         SMALLINT,
    fefo_compliance_flag     SMALLINT,
    _batch_id                INTEGER,
    _loaded_at               TIMESTAMPTZ DEFAULT now()
);
