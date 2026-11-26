-- ============================================================================
-- FACT_HOSPITAL_DEMAND - incremental monthly periodic snapshot
-- dim_organization is used twice (role-playing): the hospital and its pharmacy.
-- ============================================================================
INSERT INTO dwh.fact_hospital_demand (
    demand_month_key, medicine_key, org_key, hospital_pharmacy_org_key, channel_key, disease_key, demand_profile_key,
    hospital_demand_record_id, estimated_disease_cases, disease_related_admissions, outpatient_encounters,
    forecast_demand_packs, requested_qty_packs, approved_qty_packs, received_qty_packs, emergency_request_qty_packs,
    dispensed_qty_packs, consumed_qty_packs, wastage_qty_packs, unfulfilled_demand_packs,
    opening_stock_packs, closing_stock_packs, stockout_days, service_level_pct, avg_lead_time_days,
    demand_trend_index, _batch_id)
SELECT to_char(h.demand_month, 'YYYYMMDD')::int,
       COALESCE(dm.medicine_key, -1),
       COALESCE(o.org_key, -1),
       COALESCE(hp.org_key, -1),
       4,                                   -- Hospital System Demand
       COALESCE(dd.disease_key, -1),
       COALESCE(dp.demand_profile_key, -1),
       h.hospital_demand_record_id, h.estimated_disease_cases, h.disease_related_admissions, h.outpatient_encounters,
       h.forecast_demand_packs, h.requested_qty_packs, h.approved_qty_packs, h.received_qty_packs,
       h.emergency_request_qty_packs, h.dispensed_qty_packs, h.consumed_qty_packs, h.wastage_qty_packs,
       h.unfulfilled_demand_packs, h.opening_stock_packs, h.closing_stock_packs, h.stockout_days,
       h.service_level_pct, h.avg_lead_time_days, h.demand_trend_index, h._batch_id
FROM staging.hospital_demand h
LEFT JOIN dwh.dim_medicine dm
       ON dm.medicine_id = h.medicine_id AND h.demand_month >= dm.effective_from AND h.demand_month < dm.effective_to
LEFT JOIN dwh.dim_organization o
       ON o.org_natural_key = 'HOSPITAL|' || h.hospital_id
      AND h.demand_month >= o.effective_from AND h.demand_month < o.effective_to
LEFT JOIN dwh.dim_organization hp
       ON hp.org_natural_key = 'PHARMACY|' || h.hospital_pharmacy_id
      AND h.demand_month >= hp.effective_from AND h.demand_month < hp.effective_to
LEFT JOIN dwh.dim_disease dd ON dd.disease_category = dm.disease_category
LEFT JOIN dwh.dim_demand_profile dp
       ON dp.medication_request_status IS NOT DISTINCT FROM h.medication_request_status
      AND dp.formulary_status IS NOT DISTINCT FROM h.formulary_status
WHERE h._batch_id > (SELECT last_batch_id FROM ops.watermark WHERE target_table = 'dwh.fact_hospital_demand')
ON CONFLICT (hospital_demand_record_id) DO NOTHING;

UPDATE ops.watermark
SET last_batch_id = GREATEST(last_batch_id, COALESCE((SELECT max(_batch_id) FROM staging.hospital_demand), 0)),
    updated_at = now()
WHERE target_table = 'dwh.fact_hospital_demand';
