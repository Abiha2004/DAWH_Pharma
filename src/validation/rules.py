"""Data-quality rule catalogue (bronze -> silver).

Each source declares:
  raw / stg        : bronze and silver tables
  key              : natural key column in silver
  cols             : silver column -> SQL expression over the bronze row `r`
  rules            : (rule_name, rule_type, condition that is TRUE when the row is BAD)
                     conditions refer to the typed silver column names
  dup_existing     : also reject keys already present in silver (False for master data,
                     where an unchanged row is simply 'no change', not an error)

The same catalogue is exported to the SDI Excel workbook (DQ.Rules sheet).
"""
from __future__ import annotations

TOL = 0.02

SALES_COLS = {
    "sales_transaction_id": "NULLIF(btrim(r.\"SalesTransactionID\"), '')",
    "sales_date": "ops.try_date(r.\"SalesDate\")",
    "sales_ts_utc": "ops.try_ts(r.\"SalesTimestampUTC\")",
    "sales_channel": "r.\"SalesChannel\"",
    "pharmacy_id": "NULLIF(r.\"PharmacyID\", '')",
    "pharmacy_gln": "r.\"PharmacyGLN\"",
    "pharmacy_name": "btrim(r.\"PharmacyName\")",
    "pharmacy_type": "r.\"PharmacyType\"",
    "hospital_id": "NULLIF(r.\"HospitalID\", '')",
    "pharmacy_city": "r.\"PharmacyCity\"",
    "pharmacy_state": "upper(r.\"PharmacyState\")",
    "country_code": "r.\"CountryCode\"",
    "pharmacy_order_id": "r.\"PharmacyOrderID\"",
    "pharmacy_delivery_id": "r.\"PharmacyDeliveryID\"",
    "pharmacy_delivery_date": "ops.try_date(r.\"PharmacyDeliveryDate\")",
    "delivery_otif_flag": "ops.yn(r.\"DeliveryOTIFFlag\")",
    "warehouse_lot_id": "NULLIF(r.\"WarehouseLotID\", '')",
    "source_warehouse_id": "NULLIF(r.\"SourceWarehouseID\", '')",
    "production_batch_id": "NULLIF(r.\"ProductionBatchID\", '')",
    "medicine_id": "NULLIF(upper(btrim(r.\"MedicineID\")), '')",
    "batch_expiry_date": "ops.try_date(r.\"BatchExpiryDate\")",
    "days_to_expiry_at_sale": "ops.try_int(r.\"DaysToExpiryAtSale\")",
    "quantity_sold_packs": "ops.try_int(r.\"QuantitySoldPacks\")",
    "unit_retail_price_usd": "ops.try_num(r.\"UnitRetailPriceUSD\")",
    "gross_sales_usd": "ops.try_num(r.\"GrossSalesAmountUSD\")",
    "discount_usd": "ops.try_num(r.\"DiscountAmountUSD\")",
    "net_sales_usd": "ops.try_num(r.\"NetSalesAmountUSD\")",
    "sales_tax_usd": "ops.try_num(r.\"SalesTaxUSD\")",
    "cogs_usd": "ops.try_num(r.\"CostOfGoodsSoldUSD\")",
    "gross_margin_usd": "ops.try_num(r.\"GrossMarginUSD\")",
    "promotion_code": "COALESCE(NULLIF(r.\"PromotionCode\", ''), 'None')",
    "prescription_required_flag": "ops.yn(r.\"PrescriptionRequiredFlag\")",
    "prescription_id": "NULLIF(r.\"PrescriptionID\", '')",
    "customer_segment": "r.\"CustomerSegment\"",
    "payer_type": "r.\"PayerType\"",
    "payment_method": "r.\"PaymentMethod\"",
    "ecommerce_delivery_status": "r.\"EcommerceDeliveryStatus\"",
    "return_flag": "ops.yn(r.\"ReturnFlag\")",
    "returned_qty_packs": "ops.try_int(r.\"ReturnedQuantityPacks\")",
    "sale_status": "r.\"SaleStatus\"",
    "source_system": "r.\"SourceSystem\"",
    "ingest_mode": "r._ingest_mode",
}

HOSPITAL_COLS = {
    "hospital_demand_record_id": "NULLIF(btrim(r.\"HospitalDemandRecordID\"), '')",
    "demand_month": "ops.try_date(r.\"DemandMonth\")",
    "hospital_id": "NULLIF(r.\"HospitalID\", '')",
    "hospital_gln": "r.\"HospitalGLN\"",
    "hospital_name": "btrim(r.\"HospitalName\")",
    "hospital_type": "r.\"HospitalType\"",
    "hospital_pharmacy_id": "r.\"HospitalPharmacyID\"",
    "city": "r.\"City\"",
    "state": "upper(r.\"State\")",
    "country_code": "r.\"CountryCode\"",
    "licensed_beds": "ops.try_int(r.\"LicensedBeds\")",
    "icu_beds": "ops.try_int(r.\"ICUBeds\")",
    "avg_occupancy_pct": "ops.try_num(r.\"AverageOccupancyRatePct\")",
    "trauma_level": "r.\"TraumaLevel\"",
    "catchment_population": "ops.try_int(r.\"CatchmentPopulation\")",
    "medicine_id": "NULLIF(upper(btrim(r.\"MedicineID\")), '')",
    "disease_category": "r.\"DiseaseCategory\"",
    "icd10_category": "r.\"ICD10Category\"",
    "estimated_disease_cases": "ops.try_int(r.\"EstimatedDiseaseCases\")",
    "disease_related_admissions": "ops.try_int(r.\"DiseaseRelatedAdmissions\")",
    "outpatient_encounters": "ops.try_int(r.\"OutpatientEncounters\")",
    "forecast_demand_packs": "ops.try_int(r.\"ForecastDemandPacks\")",
    "requested_qty_packs": "ops.try_int(r.\"RequestedQuantityPacks\")",
    "approved_qty_packs": "ops.try_int(r.\"ApprovedQuantityPacks\")",
    "received_qty_packs": "ops.try_int(r.\"ReceivedQuantityPacks\")",
    "emergency_request_qty_packs": "ops.try_int(r.\"EmergencyRequestQuantityPacks\")",
    "opening_stock_packs": "ops.try_int(r.\"OpeningStockPacks\")",
    "dispensed_qty_packs": "ops.try_int(r.\"DispensedQuantityPacks\")",
    "consumed_qty_packs": "ops.try_int(r.\"ConsumedQuantityPacks\")",
    "wastage_qty_packs": "ops.try_int(r.\"WastageQuantityPacks\")",
    "closing_stock_packs": "ops.try_int(r.\"ClosingStockPacks\")",
    "unfulfilled_demand_packs": "ops.try_int(r.\"UnfulfilledDemandPacks\")",
    "stockout_days": "ops.try_int(r.\"StockoutDays\")",
    "service_level_pct": "ops.try_num(r.\"ServiceLevelPct\")",
    "medication_request_status": "r.\"MedicationRequestStatus\"",
    "formulary_status": "r.\"FormularyStatus\"",
    "avg_lead_time_days": "ops.try_num(r.\"AverageLeadTimeDays\")",
    "demand_trend_index": "ops.try_num(r.\"DemandTrendIndex\")",
}

WAREHOUSE_COLS = {
    "inventory_snapshot_id": "NULLIF(btrim(r.\"InventorySnapshotID\"), '')",
    "snapshot_date": "ops.try_date(r.\"SnapshotDate\")",
    "warehouse_lot_id": "NULLIF(r.\"WarehouseLotID\", '')",
    "production_batch_id": "NULLIF(r.\"ProductionBatchID\", '')",
    "medicine_id": "NULLIF(upper(btrim(r.\"MedicineID\")), '')",
    "warehouse_id": "NULLIF(r.\"WarehouseID\", '')",
    "warehouse_gln": "r.\"WarehouseGLN\"",
    "warehouse_name": "r.\"WarehouseName\"",
    "storage_zone_id": "r.\"StorageZoneID\"",
    "storage_condition": "r.\"StorageCondition\"",
    "batch_expiry_date": "ops.try_date(r.\"BatchExpiryDate\")",
    "days_to_expiry": "ops.try_int(r.\"DaysToExpiry\")",
    "inventory_status": "r.\"InventoryStatus\"",
    "opening_qty": "ops.try_int(r.\"OpeningQuantityPacks\")",
    "received_qty": "ops.try_int(r.\"ReceivedQuantityPacks\")",
    "available_qty": "ops.try_int(r.\"AvailableQuantityPacks\")",
    "reserved_qty": "ops.try_int(r.\"ReservedQuantityPacks\")",
    "picked_qty": "ops.try_int(r.\"PickedQuantityPacks\")",
    "shipped_qty": "ops.try_int(r.\"ShippedQuantityPacks\")",
    "transfer_in_qty": "ops.try_int(r.\"TransferInQuantityPacks\")",
    "transfer_out_qty": "ops.try_int(r.\"TransferOutQuantityPacks\")",
    "damaged_qty": "ops.try_int(r.\"DamagedQuantityPacks\")",
    "expired_qty": "ops.try_int(r.\"ExpiredQuantityPacks\")",
    "closing_qty": "ops.try_int(r.\"ClosingQuantityPacks\")",
    "stockout_flag": "ops.yn(r.\"StockoutFlag\")",
    "near_expiry_flag": "ops.yn(r.\"NearExpiryFlag\")",
    "fefo_compliance_flag": "ops.yn(r.\"FEFOComplianceFlag\")",
    "allocated_bin_capacity": "ops.try_int(r.\"AllocatedBinCapacityPacks\")",
    "warehouse_total_capacity": "ops.try_int(r.\"WarehouseTotalCapacityPacks\")",
}

MANUFACTURING_COLS = {
    "manufacturing_order_id": "NULLIF(btrim(r.\"ManufacturingOrderID\"), '')",
    "production_batch_id": "NULLIF(r.\"ProductionBatchID\", '')",
    "medicine_id": "NULLIF(upper(btrim(r.\"MedicineID\")), '')",
    "plant_id": "r.\"PlantID\"",
    "order_date": "ops.try_date(r.\"OrderDate\")",
    "actual_completion_date": "ops.try_date(r.\"ActualCompletionDate\")",
    "batch_release_date": "ops.try_date(r.\"BatchReleaseDate\")",
    "expiry_date": "ops.try_date(r.\"ExpiryDate\")",
    "planned_qty_packs": "ops.try_int(r.\"PlannedQuantityPacks\")",
    "released_qty_packs": "ops.try_int(r.\"ReleasedQuantityPacks\")",
    "yield_pct": "ops.try_num(r.\"YieldPct\")",
    "batch_status": "r.\"BatchStatus\"",
    "quality_test_status": "r.\"QualityTestStatus\"",
    "total_mfg_cost_usd": "ops.try_num(r.\"TotalManufacturingCostUSD\")",
}

MASTER_COLS = {
    "medicine_id": "NULLIF(upper(btrim(r.\"MedicineID\")), '')",
    "effective_from_date": "COALESCE(ops.try_date(r.\"EffectiveFromDate\"), DATE '2023-01-01')",
    "internal_sku": "r.\"InternalSKU\"", "gtin14": "r.\"GTIN14\"",
    "generic_name": "btrim(r.\"GenericName\")", "brand_name": "btrim(r.\"FictionalBrandName\")",
    "disease_category": "r.\"DiseaseCategory\"", "icd10_category": "r.\"ICD10Category\"",
    "atc_code": "r.\"ATCCode\"", "therapeutic_class": "r.\"TherapeuticClass\"",
    "dosage_form": "r.\"DosageForm\"", "strength": "r.\"Strength\"",
    "route_of_administration": "r.\"RouteOfAdministration\"", "pack_size": "ops.try_int(r.\"PackSize\")",
    "base_uom": "r.\"BaseUnitOfMeasure\"", "prescription_class": "r.\"PrescriptionClass\"",
    "controlled_flag": "ops.yn(r.\"ControlledMedicineFlag\")", "cold_chain_flag": "ops.yn(r.\"ColdChainFlag\")",
    "storage_min_temp_c": "ops.try_num(r.\"StorageMinTempC\")", "storage_max_temp_c": "ops.try_num(r.\"StorageMaxTempC\")",
    "max_humidity_pct": "ops.try_num(r.\"MaxHumidityPct\")", "shelf_life_months": "ops.try_int(r.\"ShelfLifeMonths\")",
    "min_remaining_shelf_days": "ops.try_int(r.\"MinimumRemainingShelfLifeDays\")",
    "primary_supplier_id": "r.\"PrimarySupplierID\"", "primary_plant_id": "r.\"PrimaryPlantID\"",
    "mfg_lead_time_days": "ops.try_int(r.\"ManufacturingLeadTimeDays\")",
    "standard_batch_size_packs": "ops.try_int(r.\"StandardBatchSizePacks\")",
    "safety_stock_days": "ops.try_int(r.\"SafetyStockDays\")",
    "reorder_review_cycle_days": "ops.try_int(r.\"ReorderReviewCycleDays\")",
    "unit_mfg_cost_usd": "ops.try_num(r.\"UnitManufacturingCostUSD\")",
    "wholesale_price_usd": "ops.try_num(r.\"WholesalePriceUSD\")",
    "rrp_usd": "ops.try_num(r.\"RecommendedRetailPriceUSD\")",
    "product_status": "r.\"ProductStatus\"", "source_system": "r.\"SourceSystem\"",
}

KNOWN_MED = "NOT EXISTS (SELECT 1 FROM staging.medicine m WHERE m.medicine_id = v.medicine_id)"

SOURCES = {
    "master": {
        "raw": "raw.master_data", "stg": "staging.medicine", "key": "medicine_id",
        "conflict": "(medicine_id, effective_from_date)", "dup_key": "medicine_id, effective_from_date",
        "cols": MASTER_COLS, "dup_existing": False,
        "rules": [
            ("medicine_id_missing", "completeness", "medicine_id IS NULL"),
            ("price_not_positive", "validity", "rrp_usd IS NULL OR rrp_usd <= 0 OR wholesale_price_usd <= 0"),
            ("price_below_cost", "consistency", "wholesale_price_usd < unit_mfg_cost_usd"),
            ("cold_chain_temp_mismatch", "consistency", "cold_chain_flag AND storage_max_temp_c > 8"),
        ],
    },
    "manufacturing": {
        "raw": "raw.manufacturing_data", "stg": "staging.manufacturing_batch", "key": "manufacturing_order_id",
        "cols": MANUFACTURING_COLS, "dup_existing": True,
        "rules": [
            ("order_id_missing", "completeness", "manufacturing_order_id IS NULL"),
            ("unknown_medicine", "referential", KNOWN_MED),
            ("expiry_not_after_release", "consistency", "expiry_date <= batch_release_date"),
        ],
    },
    "warehouse": {
        "raw": "raw.warehouse_data", "stg": "staging.warehouse_snapshot", "key": "inventory_snapshot_id",
        "cols": WAREHOUSE_COLS, "dup_existing": True,
        "rules": [
            ("snapshot_id_missing", "completeness", "inventory_snapshot_id IS NULL"),
            ("medicine_id_missing", "completeness", "medicine_id IS NULL"),
            ("snapshot_date_invalid", "validity", "snapshot_date IS NULL"),
            ("negative_stock", "validity", "closing_qty < 0 OR available_qty < 0 OR opening_qty < 0"),
            ("unknown_medicine", "referential", KNOWN_MED),
        ],
    },
    "sales": {
        "raw": "raw.pharmacy_sales", "stg": "staging.pharmacy_sale", "key": "sales_transaction_id",
        "cols": SALES_COLS, "dup_existing": True,
        "rules": [
            ("transaction_id_missing", "completeness", "sales_transaction_id IS NULL"),
            ("medicine_id_missing", "completeness", "medicine_id IS NULL"),
            ("sale_date_invalid", "validity",
             "sales_date IS NULL OR sales_date < DATE '{min_sale_date}' OR sales_date > DATE '{max_sale_date}'"),
            ("price_not_positive", "validity", "unit_retail_price_usd IS NULL OR unit_retail_price_usd <= 0"),
            ("quantity_not_positive", "validity", "quantity_sold_packs IS NULL OR quantity_sold_packs <= 0"),
            ("sold_after_expiry", "validity", "days_to_expiry_at_sale < 0"),
            ("net_ne_gross_minus_discount", "consistency", f"abs(gross_sales_usd - discount_usd - net_sales_usd) > {TOL}"),
            ("margin_ne_net_minus_cogs", "consistency", f"abs(net_sales_usd - cogs_usd - gross_margin_usd) > {TOL}"),
            ("unknown_medicine", "referential", KNOWN_MED),
        ],
    },
    "hospital": {
        "raw": "raw.hospital_demand", "stg": "staging.hospital_demand", "key": "hospital_demand_record_id",
        "cols": HOSPITAL_COLS, "dup_existing": True,
        "rules": [
            ("record_id_missing", "completeness", "hospital_demand_record_id IS NULL"),
            ("medicine_id_missing", "completeness", "medicine_id IS NULL"),
            ("demand_month_invalid", "validity", "demand_month IS NULL OR extract(day FROM demand_month) <> 1"),
            ("negative_quantity", "validity",
             "LEAST(forecast_demand_packs, requested_qty_packs, approved_qty_packs, received_qty_packs, "
             "dispensed_qty_packs, consumed_qty_packs, wastage_qty_packs, unfulfilled_demand_packs) < 0"),
            ("stock_balance_mismatch", "consistency",
             "opening_stock_packs + received_qty_packs - dispensed_qty_packs - wastage_qty_packs <> closing_stock_packs"),
            ("service_level_out_of_range", "validity", "service_level_pct NOT BETWEEN 0 AND 100"),
            ("unknown_medicine", "referential", KNOWN_MED),
        ],
    },
}

# Phase 1 silver scope (03 delivery and 07 IoT stay in bronze until Phases 2/3)
SDI_SOURCES = ["master", "manufacturing", "warehouse", "sales", "hospital"]
