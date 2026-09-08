-- =============================================================
-- HCC (Hospital Command Center) Database Setup
-- Run with: sudo -u postgres psql -p 5436 -f hccv2/db_setup.sql
-- =============================================================

-- Database and user
CREATE DATABASE hcc_db;
CREATE USER hcc_app WITH PASSWORD 'hcc_pass';
GRANT ALL PRIVILEGES ON DATABASE hcc_db TO hcc_app;

-- Switch to the new database
\c hcc_db

-- Grant schema access to app user
GRANT USAGE ON SCHEMA public TO hcc_app;

-- =============================================================
-- Tables
-- =============================================================

-- Hospital-wide resource pool (beds, staff, equipment HCC can allocate)
CREATE TABLE resources (
    id            SERIAL PRIMARY KEY,
    resource_type TEXT        NOT NULL,
    quantity      INTEGER     NOT NULL DEFAULT 0,
    status        TEXT        NOT NULL DEFAULT 'available',
    location      TEXT        NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

GRANT ALL ON TABLE resources TO hcc_app;
GRANT USAGE, SELECT ON SEQUENCE resources_id_seq TO hcc_app;

-- =============================================================
-- Helper: compute a jsonb diff between two rows (used for UPDATE)
-- =============================================================

CREATE OR REPLACE FUNCTION jsonb_diff(old_row jsonb, new_row jsonb)
RETURNS jsonb AS $$
DECLARE
    result jsonb := '{}'::jsonb;
    key    text;
BEGIN
    FOR key IN SELECT jsonb_object_keys(new_row)
    LOOP
        IF old_row -> key IS DISTINCT FROM new_row -> key THEN
            result := result || jsonb_build_object(
                key, jsonb_build_object('old', old_row -> key, 'new', new_row -> key)
            );
        END IF;
    END LOOP;
    RETURN result;
END;
$$ LANGUAGE plpgsql;

-- =============================================================
-- Reusable trigger function (generic across tables)
-- =============================================================

CREATE OR REPLACE FUNCTION notify_case_change()
RETURNS trigger AS $$
DECLARE
    diff    jsonb;
    payload jsonb;
BEGIN
    IF TG_OP = 'INSERT' THEN
        diff := to_jsonb(NEW);
    ELSIF TG_OP = 'UPDATE' THEN
        diff := jsonb_diff(to_jsonb(OLD), to_jsonb(NEW));
        IF diff = '{}'::jsonb THEN
            RETURN NEW;
        END IF;
    ELSIF TG_OP = 'DELETE' THEN
        diff := to_jsonb(OLD);
    END IF;

    payload := jsonb_build_object(
        'table',  TG_TABLE_NAME,
        'op',     TG_OP,
        'row_id', COALESCE(NEW.id, OLD.id),
        'diff',   diff
    );

    PERFORM pg_notify('hcc_case_updates', payload::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================================================
-- Attach trigger to each table
-- =============================================================

CREATE TRIGGER resources_notify_trigger
AFTER INSERT OR UPDATE OR DELETE ON resources
FOR EACH ROW EXECUTE FUNCTION notify_case_change();
