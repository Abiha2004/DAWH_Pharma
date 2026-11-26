-- Runs once when the Postgres volume is first created.
CREATE DATABASE airflow;                     -- Airflow metadata DB (same server, separate database)
CREATE ROLE grafana_reader LOGIN PASSWORD 'grafana_reader';
GRANT pg_read_all_data  TO grafana_reader;   -- read-only on every schema (PG14+)
GRANT pg_read_all_stats TO grafana_reader;   -- lets Grafana read pg_stat_statements
\connect pharma_dwh
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
