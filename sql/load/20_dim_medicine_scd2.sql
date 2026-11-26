-- ============================================================================
-- DIM_MEDICINE - Slowly Changing Dimension Type 2
-- Tracked (Type 2) : rrp_usd, wholesale_price_usd, unit_mfg_cost_usd, product_status,
--                    therapeutic_class, prescription_class, cold_chain_flag, storage temps
-- Overwritten (Type 1): names, SKU, GTIN, dosage descriptors
-- Validity is half-open: effective_from <= date < effective_to
-- Only staging rows newer than the watermark are considered (incremental).
-- ============================================================================
CREATE TEMP TABLE med_src ON COMMIT DROP AS
SELECT DISTINCT ON (m.medicine_id) m.*,
       md5(concat_ws('|', m.rrp_usd, m.wholesale_price_usd, m.unit_mfg_cost_usd, m.product_status,
                     m.therapeutic_class, m.prescription_class, m.cold_chain_flag,
                     m.storage_min_temp_c, m.storage_max_temp_c)) AS scd_hash
FROM staging.medicine m
WHERE m._batch_id > (SELECT last_batch_id FROM ops.watermark WHERE target_table = 'dwh.dim_medicine')
ORDER BY m.medicine_id, m.effective_from_date DESC;

-- 1) Type 1 attributes: overwrite on every version of the member
UPDATE dwh.dim_medicine d
SET generic_name = s.generic_name, brand_name = s.brand_name, internal_sku = s.internal_sku,
    gtin14 = s.gtin14, dosage_form = s.dosage_form, strength = s.strength
FROM med_src s
WHERE d.medicine_id = s.medicine_id
  AND (d.generic_name, d.brand_name, d.internal_sku) IS DISTINCT FROM (s.generic_name, s.brand_name, s.internal_sku);

-- 2) Expire the current version when a Type 2 attribute changed
CREATE TEMP TABLE med_changed ON COMMIT DROP AS
SELECT s.*, d.version_no AS old_version
FROM med_src s
JOIN dwh.dim_medicine d ON d.medicine_id = s.medicine_id AND d.is_current
WHERE d.scd_hash IS DISTINCT FROM s.scd_hash
  AND s.effective_from_date > d.effective_from;

UPDATE dwh.dim_medicine d
SET effective_to = c.effective_from_date, is_current = FALSE
FROM med_changed c
WHERE d.medicine_id = c.medicine_id AND d.is_current;

-- 3) Insert new versions (changed members) and brand-new members
INSERT INTO dwh.dim_medicine (
    medicine_id, internal_sku, gtin14, generic_name, brand_name, disease_category, icd10_category, atc_code,
    therapeutic_class, dosage_form, strength, route_of_administration, pack_size, prescription_class,
    controlled_flag, cold_chain_flag, cold_chain_label, storage_min_temp_c, storage_max_temp_c, shelf_life_months,
    unit_mfg_cost_usd, wholesale_price_usd, rrp_usd, price_band, product_status,
    scd_hash, effective_from, effective_to, is_current, version_no, _batch_id)
SELECT s.medicine_id, s.internal_sku, s.gtin14, s.generic_name, s.brand_name, s.disease_category, s.icd10_category,
       s.atc_code, s.therapeutic_class, s.dosage_form, s.strength, s.route_of_administration, s.pack_size,
       s.prescription_class, s.controlled_flag, s.cold_chain_flag,
       CASE WHEN s.cold_chain_flag THEN 'Cold chain (' || s.storage_min_temp_c::int || '-' || s.storage_max_temp_c::int || ' C)'
            ELSE 'Ambient (' || s.storage_min_temp_c::int || '-' || s.storage_max_temp_c::int || ' C)' END,
       s.storage_min_temp_c, s.storage_max_temp_c, s.shelf_life_months,
       s.unit_mfg_cost_usd, s.wholesale_price_usd, s.rrp_usd,
       CASE WHEN s.rrp_usd < 5 THEN 'Low (<$5)' WHEN s.rrp_usd < 15 THEN 'Mid ($5-15)'
            WHEN s.rrp_usd < 40 THEN 'High ($15-40)' ELSE 'Premium ($40+)' END,
       s.product_status, s.scd_hash,
       CASE WHEN c.medicine_id IS NULL THEN DATE '1900-01-01' ELSE s.effective_from_date END,
       DATE '9999-12-31', TRUE, COALESCE(c.old_version, 0) + 1, s._batch_id
FROM med_src s
LEFT JOIN med_changed c ON c.medicine_id = s.medicine_id
WHERE c.medicine_id IS NOT NULL
   OR NOT EXISTS (SELECT 1 FROM dwh.dim_medicine d WHERE d.medicine_id = s.medicine_id);

UPDATE ops.watermark SET last_batch_id = GREATEST(last_batch_id, COALESCE((SELECT max(_batch_id) FROM staging.medicine), 0)),
                         updated_at = now()
WHERE target_table = 'dwh.dim_medicine';
