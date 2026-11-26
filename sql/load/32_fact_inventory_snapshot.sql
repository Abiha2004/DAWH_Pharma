-- ============================================================================
-- FACT_INVENTORY_SNAPSHOT - incremental daily periodic snapshot (warehouse context
-- for SDI; becomes the core fact of the Phase 2 Inventory & Replenishment mart)
-- ============================================================================
INSERT INTO dwh.fact_inventory_snapshot (
    snapshot_date_key, expiry_date_key, medicine_key, warehouse_key, batch_lot_key, inventory_snapshot_id,
    inventory_status, opening_qty, received_qty, available_qty, reserved_qty, picked_qty, shipped_qty,
    transfer_in_qty, transfer_out_qty, damaged_qty, expired_qty, closing_qty, days_to_expiry,
    stockout_flag, near_expiry_flag, fefo_compliance_flag, _batch_id)
SELECT to_char(w.snapshot_date, 'YYYYMMDD')::int,
       COALESCE(to_char(w.batch_expiry_date, 'YYYYMMDD')::int, -1),
       COALESCE(dm.medicine_key, -1),
       COALESCE(dw.warehouse_key, -1),
       COALESCE(bl.batch_lot_key, -1),
       w.inventory_snapshot_id, w.inventory_status, w.opening_qty, w.received_qty, w.available_qty, w.reserved_qty,
       w.picked_qty, w.shipped_qty, w.transfer_in_qty, w.transfer_out_qty, w.damaged_qty, w.expired_qty,
       w.closing_qty, w.days_to_expiry,
       w.stockout_flag::int, w.near_expiry_flag::int, w.fefo_compliance_flag::int, w._batch_id
FROM staging.warehouse_snapshot w
LEFT JOIN dwh.dim_medicine dm
       ON dm.medicine_id = w.medicine_id AND w.snapshot_date >= dm.effective_from AND w.snapshot_date < dm.effective_to
LEFT JOIN dwh.dim_warehouse dw ON dw.warehouse_id = w.warehouse_id
LEFT JOIN dwh.dim_batch_lot bl ON bl.warehouse_lot_id = w.warehouse_lot_id
WHERE w._batch_id > (SELECT last_batch_id FROM ops.watermark WHERE target_table = 'dwh.fact_inventory_snapshot')
ON CONFLICT (inventory_snapshot_id) DO NOTHING;

UPDATE ops.watermark
SET last_batch_id = GREATEST(last_batch_id, COALESCE((SELECT max(_batch_id) FROM staging.warehouse_snapshot), 0)),
    updated_at = now()
WHERE target_table = 'dwh.fact_inventory_snapshot';
