-- ============================================================================
-- Type 1 conformed dimensions + junk dimensions (idempotent upserts)
-- ============================================================================

-- DIM_DISEASE (from master data)
INSERT INTO dwh.dim_disease (disease_category, icd10_category, disease_group)
SELECT DISTINCT disease_category, icd10_category,
       CASE WHEN disease_category IN ('Diabetes Mellitus', 'Cardiovascular Disease',
                                      'Neurological and Mental Health Conditions') THEN 'Chronic'
            WHEN disease_category IN ('Infectious Disease', 'Respiratory Disease') THEN 'Acute / Seasonal'
            ELSE 'Mixed' END
FROM staging.medicine
ON CONFLICT (disease_category) DO UPDATE SET icd10_category = EXCLUDED.icd10_category;

-- DIM_CUSTOMER_SEGMENT
INSERT INTO dwh.dim_customer_segment (segment_name, segment_group, age_group)
SELECT DISTINCT customer_segment,
       CASE WHEN customer_segment ILIKE '%chronic%' THEN 'Chronic care'
            WHEN customer_segment ILIKE '%acute%'   THEN 'Acute care'
            WHEN customer_segment ILIKE '%pediatric%' THEN 'Pediatric'
            ELSE 'Wellness' END,
       CASE WHEN customer_segment ILIKE 'senior%' THEN 'Senior (65+)'
            WHEN customer_segment ILIKE 'pediatric%' THEN 'Child (0-17)'
            WHEN customer_segment ILIKE 'adult%' THEN 'Adult (18-64)' ELSE 'All ages' END
FROM staging.pharmacy_sale
WHERE customer_segment IS NOT NULL
  AND _batch_id > (SELECT last_batch_id FROM ops.watermark WHERE target_table = 'dwh.fact_pharmacy_sales')
ON CONFLICT (segment_name) DO NOTHING;

-- DIM_PAYER
INSERT INTO dwh.dim_payer (payer_type, payer_group)
SELECT DISTINCT payer_type,
       CASE WHEN payer_type IN ('Insurance', 'Government program') THEN 'Third-party payer'
            WHEN payer_type = 'Cash' THEN 'Out-of-pocket'
            WHEN payer_type ILIKE 'Hospital%' THEN 'Institutional' ELSE 'Other' END
FROM staging.pharmacy_sale
WHERE payer_type IS NOT NULL
  AND _batch_id > (SELECT last_batch_id FROM ops.watermark WHERE target_table = 'dwh.fact_pharmacy_sales')
ON CONFLICT (payer_type) DO NOTHING;

-- DIM_PROMOTION (discount % parsed from the code, e.g. ONLINE10 -> 10%)
INSERT INTO dwh.dim_promotion (promotion_code, promotion_type, discount_pct, is_promotion)
SELECT DISTINCT promotion_code,
       CASE WHEN promotion_code = 'None' THEN 'No promotion'
            ELSE initcap(regexp_replace(promotion_code, '[0-9]+$', '')) END,
       COALESCE(NULLIF(regexp_replace(promotion_code, '^[A-Za-z]+', ''), '')::numeric, 0),
       promotion_code <> 'None'
FROM staging.pharmacy_sale
WHERE _batch_id > (SELECT last_batch_id FROM ops.watermark WHERE target_table = 'dwh.fact_pharmacy_sales')
ON CONFLICT (promotion_code) DO NOTHING;

-- DIM_SALES_PROFILE (JUNK): every observed combination of low-cardinality flags
INSERT INTO dwh.dim_sales_profile (payment_method, prescription_required_flag, ecommerce_delivery_status,
                                   return_flag, sale_status, delivery_otif_flag)
SELECT DISTINCT payment_method, prescription_required_flag, ecommerce_delivery_status,
       return_flag, sale_status, delivery_otif_flag
FROM staging.pharmacy_sale
WHERE _batch_id > (SELECT last_batch_id FROM ops.watermark WHERE target_table = 'dwh.fact_pharmacy_sales')
ON CONFLICT DO NOTHING;

-- DIM_DEMAND_PROFILE (JUNK)
INSERT INTO dwh.dim_demand_profile (medication_request_status, formulary_status)
SELECT DISTINCT medication_request_status, formulary_status
FROM staging.hospital_demand
WHERE _batch_id > (SELECT last_batch_id FROM ops.watermark WHERE target_table = 'dwh.fact_hospital_demand')
ON CONFLICT DO NOTHING;

-- DIM_WAREHOUSE (Type 1). City/state come from the manufacturer-delivery feed (bronze 03),
-- zone counts from the storage zones seen in the warehouse snapshots.
INSERT INTO dwh.dim_warehouse (warehouse_id, warehouse_gln, warehouse_name, city, state_code, region, country_code,
                               total_capacity_packs, cold_zone_count, ambient_zone_count, cold_chain_capable, _batch_id)
SELECT w.warehouse_id, max(w.warehouse_gln), max(w.warehouse_name), max(g.city), max(g.st), ops.us_region(max(g.st)),
       COALESCE(max(g.cc), 'US'), max(w.warehouse_total_capacity),
       count(DISTINCT w.storage_zone_id) FILTER (WHERE w.storage_condition LIKE '2-8%'),
       count(DISTINCT w.storage_zone_id) FILTER (WHERE w.storage_condition NOT LIKE '2-8%'),
       bool_or(w.storage_condition LIKE '2-8%'), max(w._batch_id)
FROM staging.warehouse_snapshot w
LEFT JOIN (SELECT DISTINCT "DestinationWarehouseID" AS wid, "DestinationCity" AS city,
                  "DestinationState" AS st, "DestinationCountryCode" AS cc
           FROM raw.manufacturer_delivery) g ON g.wid = w.warehouse_id
GROUP BY w.warehouse_id
ON CONFLICT (warehouse_id) DO UPDATE
SET warehouse_name = EXCLUDED.warehouse_name, total_capacity_packs = EXCLUDED.total_capacity_packs,
    cold_zone_count = EXCLUDED.cold_zone_count, ambient_zone_count = EXCLUDED.ambient_zone_count,
    cold_chain_capable = EXCLUDED.cold_chain_capable,
    city = COALESCE(EXCLUDED.city, dwh.dim_warehouse.city), state_code = COALESCE(EXCLUDED.state_code, dwh.dim_warehouse.state_code),
    region = CASE WHEN EXCLUDED.region = 'Unknown' THEN dwh.dim_warehouse.region ELSE EXCLUDED.region END;

-- DIM_BATCH_LOT: warehouse lots enriched with manufacturing batch attributes
INSERT INTO dwh.dim_batch_lot (warehouse_lot_id, production_batch_id, manufacturing_order_id, medicine_id, warehouse_id,
       plant_id, storage_zone_id, storage_condition, order_date, production_completed, batch_release_date,
       batch_expiry_date, batch_status, quality_test_status, yield_pct, _batch_id)
SELECT DISTINCT ON (l.warehouse_lot_id)
       l.warehouse_lot_id, l.production_batch_id, mb.manufacturing_order_id, l.medicine_id, l.warehouse_id,
       mb.plant_id, l.storage_zone_id, l.storage_condition, mb.order_date, mb.actual_completion_date,
       mb.batch_release_date, l.batch_expiry_date, mb.batch_status, mb.quality_test_status, mb.yield_pct, l._batch_id
FROM (
    SELECT warehouse_lot_id, production_batch_id, medicine_id, warehouse_id, storage_zone_id, storage_condition,
           batch_expiry_date, _batch_id, 1 AS pri
    FROM staging.warehouse_snapshot
    WHERE _batch_id > (SELECT last_batch_id FROM ops.watermark WHERE target_table = 'dwh.fact_inventory_snapshot')
    UNION ALL       -- lots referenced by sales but never seen in a snapshot
    SELECT warehouse_lot_id, production_batch_id, medicine_id, source_warehouse_id, NULL, NULL,
           batch_expiry_date, _batch_id, 2
    FROM staging.pharmacy_sale
    WHERE warehouse_lot_id IS NOT NULL
      AND _batch_id > (SELECT last_batch_id FROM ops.watermark WHERE target_table = 'dwh.fact_pharmacy_sales')
) l
LEFT JOIN staging.manufacturing_batch mb ON mb.production_batch_id = l.production_batch_id
ORDER BY l.warehouse_lot_id, l.pri
ON CONFLICT (warehouse_lot_id) DO NOTHING;
