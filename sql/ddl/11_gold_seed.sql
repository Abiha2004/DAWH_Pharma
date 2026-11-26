-- Seed static dimension content and "Unknown" members (key -1). Idempotent.

-- Date dimension 2022-01-01 .. 2027-12-31 (covers history + all refresh windows)
INSERT INTO dwh.dim_date
SELECT to_char(d, 'YYYYMMDD')::INT,
       d::DATE,
       EXTRACT(DAY FROM d),
       trim(to_char(d, 'Day')),
       EXTRACT(ISODOW FROM d),
       EXTRACT(ISODOW FROM d) IN (6, 7),
       EXTRACT(WEEK FROM d),
       EXTRACT(MONTH FROM d),
       trim(to_char(d, 'Month')),
       to_char(d, 'Mon'),
       EXTRACT(QUARTER FROM d),
       to_char(d, 'YYYY') || '-Q' || EXTRACT(QUARTER FROM d),
       EXTRACT(YEAR FROM d),
       to_char(d, 'YYYY-MM'),
       date_trunc('month', d)::DATE,
       (d + INTERVAL '1 day')::DATE = (date_trunc('month', d) + INTERVAL '1 month')::DATE
FROM generate_series(DATE '2022-01-01', DATE '2027-12-31', INTERVAL '1 day') AS g(d)
ON CONFLICT (date_key) DO NOTHING;

INSERT INTO dwh.dim_date (date_key, full_date, day_name, month_name, year_month, quarter_name)
VALUES (-1, NULL, 'Unknown', 'Unknown', 'Unknown', 'Unknown')
ON CONFLICT (date_key) DO NOTHING;

-- Channel dimension: the source only says "Physical Pharmacy" / "Ecommerce";
-- the business view splits physical sales into Retail vs Hospital Pharmacy.
INSERT INTO dwh.dim_channel VALUES
    (-1, 'UNKNOWN',     'Unknown',                'Unknown',       'Unknown', FALSE, FALSE),
    ( 1, 'RETAIL',      'Retail Pharmacy',        'Retail',        'Sales',   FALSE, FALSE),
    ( 2, 'HOSP_PHARM',  'Hospital Pharmacy',      'Institutional', 'Sales',   FALSE, TRUE),
    ( 3, 'ECOM',        'E-commerce',             'Digital',       'Sales',   TRUE,  FALSE),
    ( 4, 'HOSP_DEMAND', 'Hospital System Demand', 'Institutional', 'Demand',  FALSE, TRUE)
ON CONFLICT (channel_key) DO NOTHING;

-- Unknown members for every surrogate-keyed dimension
INSERT INTO dwh.dim_medicine (medicine_key, medicine_id, generic_name, brand_name, disease_category,
       therapeutic_class, cold_chain_label, effective_from, product_status)
VALUES (-1, 'UNKNOWN', 'Unknown', 'Unknown', 'Unknown', 'Unknown', 'Unknown', DATE '1900-01-01', 'Unknown')
ON CONFLICT DO NOTHING;
INSERT INTO dwh.dim_organization (org_key, org_natural_key, org_source_type, org_id, org_name, org_type, region, effective_from)
VALUES (-1, 'UNKNOWN', 'UNKNOWN', 'UNKNOWN', 'Unknown', 'Unknown', 'Unknown', DATE '1900-01-01')
ON CONFLICT DO NOTHING;
INSERT INTO dwh.dim_warehouse (warehouse_key, warehouse_id, warehouse_name, region)
VALUES (-1, 'UNKNOWN', 'Unknown', 'Unknown') ON CONFLICT DO NOTHING;
INSERT INTO dwh.dim_batch_lot (batch_lot_key, warehouse_lot_id) VALUES (-1, 'UNKNOWN') ON CONFLICT DO NOTHING;
INSERT INTO dwh.dim_disease (disease_key, disease_category, icd10_category, disease_group)
VALUES (-1, 'Unknown', 'Unknown', 'Unknown') ON CONFLICT DO NOTHING;
INSERT INTO dwh.dim_customer_segment (segment_key, segment_name, segment_group, age_group)
VALUES (-1, 'Unknown', 'Unknown', 'Unknown') ON CONFLICT DO NOTHING;
INSERT INTO dwh.dim_payer (payer_key, payer_type, payer_group) VALUES (-1, 'Unknown', 'Unknown') ON CONFLICT DO NOTHING;
INSERT INTO dwh.dim_promotion (promotion_key, promotion_code, promotion_type, discount_pct, is_promotion)
VALUES (-1, 'UNKNOWN', 'Unknown', 0, FALSE) ON CONFLICT DO NOTHING;
INSERT INTO dwh.dim_sales_profile (sales_profile_key, payment_method, sale_status)
VALUES (-1, 'Unknown', 'Unknown') ON CONFLICT DO NOTHING;
INSERT INTO dwh.dim_demand_profile (demand_profile_key, medication_request_status, formulary_status)
VALUES (-1, 'Unknown', 'Unknown') ON CONFLICT DO NOTHING;

INSERT INTO ops.watermark (target_table) VALUES
    ('dwh.dim_medicine'), ('dwh.fact_pharmacy_sales'), ('dwh.fact_hospital_demand'), ('dwh.fact_inventory_snapshot')
ON CONFLICT DO NOTHING;
