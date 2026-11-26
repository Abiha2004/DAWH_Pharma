-- ============================================================================
-- Medallion layout inside one PostgreSQL database
--   raw        BRONZE  : files exactly as received (all TEXT) + load metadata
--   staging    SILVER  : typed, validated, de-duplicated rows (the ODS)
--   quarantine         : rows rejected by data-quality rules, with the reason
--   dwh        GOLD    : SDI star schema (dimensions + facts)
--   mart               : materialized views / pre-aggregated summary tables
--   ops                : pipeline observability (feeds the Grafana dashboards)
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS quarantine;
CREATE SCHEMA IF NOT EXISTS dwh;
CREATE SCHEMA IF NOT EXISTS mart;
CREATE SCHEMA IF NOT EXISTS ops;

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;   -- real query telemetry for the Query dashboard

-- Safe casts: return NULL instead of raising, so one bad value never aborts a batch.
CREATE OR REPLACE FUNCTION ops.try_num(t TEXT) RETURNS NUMERIC
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF t IS NULL OR btrim(t) = '' THEN RETURN NULL; END IF;
    RETURN t::NUMERIC;
EXCEPTION WHEN others THEN RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION ops.try_int(t TEXT) RETURNS INTEGER
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF t IS NULL OR btrim(t) = '' THEN RETURN NULL; END IF;
    RETURN round(t::NUMERIC)::INTEGER;
EXCEPTION WHEN others THEN RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION ops.try_date(t TEXT) RETURNS DATE
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF t IS NULL OR btrim(t) = '' THEN RETURN NULL; END IF;
    RETURN t::DATE;
EXCEPTION WHEN others THEN RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION ops.try_ts(t TEXT) RETURNS TIMESTAMPTZ
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF t IS NULL OR btrim(t) = '' THEN RETURN NULL; END IF;
    RETURN t::TIMESTAMPTZ;
EXCEPTION WHEN others THEN RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION ops.yn(t TEXT) RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE upper(btrim(t)) WHEN 'Y' THEN TRUE WHEN 'YES' THEN TRUE WHEN '1' THEN TRUE
                                WHEN 'N' THEN FALSE WHEN 'NO' THEN FALSE WHEN '0' THEN FALSE END
$$;

-- US Census region from state code (used by organization and warehouse dimensions)
CREATE OR REPLACE FUNCTION ops.us_region(st TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN st IN ('CT','MA','ME','NH','RI','VT','NJ','NY','PA')                     THEN 'Northeast'
        WHEN st IN ('IL','IN','MI','OH','WI','IA','KS','MN','MO','NE','ND','SD')     THEN 'Midwest'
        WHEN st IN ('DE','FL','GA','MD','NC','SC','VA','DC','WV','AL','KY','MS','TN',
                    'AR','LA','OK','TX')                                              THEN 'South'
        WHEN st IN ('AZ','CO','ID','MT','NV','NM','UT','WY','AK','CA','HI','OR','WA') THEN 'West'
        ELSE 'Unknown' END
$$;
