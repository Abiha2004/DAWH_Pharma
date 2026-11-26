-- ============================================================================
-- FACT_PHARMACY_SALES - incremental load (only staging batches above the watermark)
-- Surrogate-key lookups; SCD2 dimensions are joined "as of" the sale date.
-- Missing members map to the Unknown member (-1) so no sale is ever dropped.
-- ============================================================================
INSERT INTO dwh.fact_pharmacy_sales (
    sale_date_key, delivery_date_key, expiry_date_key, sale_hour,
    medicine_key, org_key, channel_key, warehouse_key, batch_lot_key, disease_key,
    segment_key, payer_key, promotion_key, sales_profile_key,
    sales_transaction_id, pharmacy_order_id, prescription_id, ingest_mode,
    quantity_sold_packs, unit_retail_price_usd, gross_sales_usd, discount_usd, net_sales_usd,
    sales_tax_usd, cogs_usd, gross_margin_usd, returned_qty_packs, days_to_expiry_at_sale, _batch_id)
SELECT to_char(s.sales_date, 'YYYYMMDD')::int,
       COALESCE(to_char(s.pharmacy_delivery_date, 'YYYYMMDD')::int, -1),
       COALESCE(to_char(s.batch_expiry_date, 'YYYYMMDD')::int, -1),
       EXTRACT(HOUR FROM s.sales_ts_utc AT TIME ZONE 'UTC'),
       COALESCE(dm.medicine_key, -1),
       COALESCE(o.org_key, -1),
       CASE WHEN s.sales_channel = 'Ecommerce' THEN 3
            WHEN s.pharmacy_type = 'Hospital Pharmacy' THEN 2
            WHEN s.sales_channel = 'Physical Pharmacy' THEN 1 ELSE -1 END,
       COALESCE(w.warehouse_key, -1),
       COALESCE(bl.batch_lot_key, -1),
       COALESCE(dd.disease_key, -1),
       COALESCE(cs.segment_key, -1),
       COALESCE(p.payer_key, -1),
       COALESCE(pr.promotion_key, -1),
       COALESCE(sp.sales_profile_key, -1),
       s.sales_transaction_id, s.pharmacy_order_id, s.prescription_id, s.ingest_mode,
       s.quantity_sold_packs, s.unit_retail_price_usd, s.gross_sales_usd, s.discount_usd, s.net_sales_usd,
       s.sales_tax_usd, s.cogs_usd, s.gross_margin_usd, s.returned_qty_packs, s.days_to_expiry_at_sale, s._batch_id
FROM staging.pharmacy_sale s
LEFT JOIN dwh.dim_medicine dm
       ON dm.medicine_id = s.medicine_id AND s.sales_date >= dm.effective_from AND s.sales_date < dm.effective_to
LEFT JOIN dwh.dim_organization o
       ON o.org_natural_key = 'PHARMACY|' || s.pharmacy_id
      AND s.sales_date >= o.effective_from AND s.sales_date < o.effective_to
LEFT JOIN dwh.dim_warehouse w         ON w.warehouse_id = s.source_warehouse_id
LEFT JOIN dwh.dim_batch_lot bl        ON bl.warehouse_lot_id = s.warehouse_lot_id
LEFT JOIN dwh.dim_disease dd          ON dd.disease_category = dm.disease_category
LEFT JOIN dwh.dim_customer_segment cs ON cs.segment_name = s.customer_segment
LEFT JOIN dwh.dim_payer p             ON p.payer_type = s.payer_type
LEFT JOIN dwh.dim_promotion pr        ON pr.promotion_code = s.promotion_code
LEFT JOIN dwh.dim_sales_profile sp
       ON sp.payment_method IS NOT DISTINCT FROM s.payment_method
      AND sp.prescription_required_flag IS NOT DISTINCT FROM s.prescription_required_flag
      AND sp.ecommerce_delivery_status IS NOT DISTINCT FROM s.ecommerce_delivery_status
      AND sp.return_flag IS NOT DISTINCT FROM s.return_flag
      AND sp.sale_status IS NOT DISTINCT FROM s.sale_status
      AND sp.delivery_otif_flag IS NOT DISTINCT FROM s.delivery_otif_flag
WHERE s._batch_id > (SELECT last_batch_id FROM ops.watermark WHERE target_table = 'dwh.fact_pharmacy_sales')
ON CONFLICT (sales_transaction_id, sale_date_key) DO NOTHING;

UPDATE ops.watermark
SET last_batch_id = GREATEST(last_batch_id, COALESCE((SELECT max(_batch_id) FROM staging.pharmacy_sale), 0)),
    updated_at = now()
WHERE target_table = 'dwh.fact_pharmacy_sales';
