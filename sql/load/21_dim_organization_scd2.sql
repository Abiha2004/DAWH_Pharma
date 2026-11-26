-- ============================================================================
-- DIM_ORGANIZATION - SCD Type 2 over pharmacies (from sales) and hospitals (from demand)
-- Tracked: org_name, org_type, city, state, beds. Natural key = SOURCE|ID.
-- A change is effective from the first date the new attribute values were seen.
-- Uses the fact watermarks so only newly staged rows are scanned.
-- ============================================================================
CREATE TEMP TABLE org_src ON COMMIT DROP AS
WITH pharm AS (
    SELECT 'PHARMACY|' || pharmacy_id AS nk, 'PHARMACY' AS src, pharmacy_id AS id, pharmacy_name AS name,
           pharmacy_type AS otype, pharmacy_gln AS gln, hospital_id AS parent, pharmacy_city AS city,
           pharmacy_state AS st, country_code AS cc,
           NULL::int AS beds, NULL::int AS icu, NULL::numeric AS occ, NULL::text AS trauma, NULL::int AS catch,
           min(sales_date) AS first_seen, max(_batch_id) AS bid
    FROM staging.pharmacy_sale
    WHERE _batch_id > (SELECT last_batch_id FROM ops.watermark WHERE target_table = 'dwh.fact_pharmacy_sales')
      AND pharmacy_id IS NOT NULL
    GROUP BY 1,2,3,4,5,6,7,8,9,10
), hosp AS (
    SELECT 'HOSPITAL|' || hospital_id, 'HOSPITAL', hospital_id, hospital_name, hospital_type, hospital_gln,
           NULL::text, city, state, country_code, licensed_beds, icu_beds, avg_occupancy_pct, trauma_level,
           catchment_population, min(demand_month), max(_batch_id)
    FROM staging.hospital_demand
    WHERE _batch_id > (SELECT last_batch_id FROM ops.watermark WHERE target_table = 'dwh.fact_hospital_demand')
    GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15
)
SELECT DISTINCT ON (nk) *, md5(concat_ws('|', name, otype, city, st, beds)) AS scd_hash
FROM (SELECT * FROM pharm UNION ALL SELECT * FROM hosp) u
ORDER BY nk, first_seen DESC;

CREATE TEMP TABLE org_changed ON COMMIT DROP AS
SELECT s.*, d.version_no AS old_version
FROM org_src s JOIN dwh.dim_organization d ON d.org_natural_key = s.nk AND d.is_current
WHERE d.scd_hash IS DISTINCT FROM s.scd_hash AND s.first_seen > d.effective_from;

UPDATE dwh.dim_organization d SET effective_to = c.first_seen, is_current = FALSE
FROM org_changed c WHERE d.org_natural_key = c.nk AND d.is_current;

INSERT INTO dwh.dim_organization (
    org_natural_key, org_source_type, org_id, org_name, org_type, gln, parent_hospital_id, city, state_code,
    region, country_code, licensed_beds, icu_beds, avg_occupancy_pct, trauma_level, catchment_population,
    hospital_size_band, scd_hash, effective_from, effective_to, is_current, version_no, _batch_id)
SELECT s.nk, s.src, s.id, s.name, s.otype, s.gln, s.parent, s.city, s.st, ops.us_region(s.st), s.cc,
       s.beds, s.icu, s.occ, s.trauma, s.catch,
       CASE WHEN s.beds IS NULL THEN 'N/A (pharmacy)' WHEN s.beds < 300 THEN 'Small (<300 beds)'
            WHEN s.beds < 600 THEN 'Medium (300-599 beds)' ELSE 'Large (600+ beds)' END,
       s.scd_hash,
       CASE WHEN c.nk IS NULL THEN DATE '1900-01-01' ELSE s.first_seen END,
       DATE '9999-12-31', TRUE, COALESCE(c.old_version, 0) + 1, s.bid
FROM org_src s LEFT JOIN org_changed c ON c.nk = s.nk
WHERE c.nk IS NOT NULL
   OR NOT EXISTS (SELECT 1 FROM dwh.dim_organization d WHERE d.org_natural_key = s.nk);
