-- =============================================================
-- ER (Emergency Department) Database Setup
-- Run with: sudo -u postgres psql -p 5435 -f erv2/db_setup.sql
-- =============================================================

-- Database and user
CREATE DATABASE er_db;
CREATE USER er_app WITH PASSWORD 'er_pass';
GRANT ALL PRIVILEGES ON DATABASE er_db TO er_app;

-- Switch to the new database
\c er_db

-- Grant schema access to app user
GRANT USAGE ON SCHEMA public TO er_app;

-- =============================================================
-- Tables
-- =============================================================

-- ED-local resources (beds, staff, equipment on hand right now)
CREATE TABLE resources (
    id            SERIAL PRIMARY KEY,
    resource_type TEXT        NOT NULL,
    quantity      INTEGER     NOT NULL DEFAULT 0,
    status        TEXT        NOT NULL DEFAULT 'available',
    location      TEXT        NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

GRANT ALL ON TABLE resources TO er_app;
GRANT USAGE, SELECT ON SEQUENCE resources_id_seq TO er_app;

-- Patients (triage_category is NULL until the ER agent runs START triage).
-- Vitals map exactly to the five START decision points: ambulatory,
-- respirations, perfusion, mental status.
CREATE TABLE patients (
    id                    SERIAL PRIMARY KEY,
    arrival_time          TIMESTAMPTZ NOT NULL DEFAULT now(),
    chief_complaint       TEXT        NOT NULL CHECK (btrim(chief_complaint) <> ''),
    heart_rate            INTEGER     NOT NULL CHECK (heart_rate BETWEEN 0 AND 250),
    able_to_walk          BOOLEAN     NOT NULL,
    spontaneous_breathing BOOLEAN     NOT NULL,
    respiratory_rate      INTEGER     NOT NULL CHECK (respiratory_rate BETWEEN 0 AND 80),
    radial_pulse_present  BOOLEAN     NOT NULL,
    obeys_commands        BOOLEAN     NOT NULL,
    triage_category       TEXT        CHECK (triage_category IN ('Immediate', 'Delayed', 'Minor', 'Expectant')),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT patients_start_category_matches_fields CHECK (
        triage_category IS NULL OR triage_category = CASE
            WHEN able_to_walk THEN 'Minor'
            WHEN NOT spontaneous_breathing THEN 'Expectant'
            WHEN respiratory_rate > 30 THEN 'Immediate'
            WHEN NOT radial_pulse_present THEN 'Immediate'
            WHEN NOT obeys_commands THEN 'Immediate'
            ELSE 'Delayed'
        END
    )
);

CREATE UNIQUE INDEX patients_no_duplicate_upload_rows
ON patients (
    lower(btrim(chief_complaint)),
    heart_rate,
    able_to_walk,
    spontaneous_breathing,
    respiratory_rate,
    radial_pulse_present,
    obeys_commands
);

GRANT ALL ON TABLE patients TO er_app;
GRANT USAGE, SELECT ON SEQUENCE patients_id_seq TO er_app;

-- Per-patient progress through a clinical pathway (e.g. AUB) -- one row per
-- (patient, pathway), answers accumulate as the ED Director responds to the
-- agent's reminders of what the protocol needs next.
CREATE TABLE patient_pathway_progress (
    id            SERIAL PRIMARY KEY,
    patient_id    INTEGER     NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    pathway_id    TEXT        NOT NULL,
    answers       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (patient_id, pathway_id)
);

GRANT ALL ON TABLE patient_pathway_progress TO er_app;
GRANT USAGE, SELECT ON SEQUENCE patient_pathway_progress_id_seq TO er_app;

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

    PERFORM pg_notify('er_case_updates', payload::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================================================
-- Dedicated patients trigger: batch uploads set this session-local
-- flag to suppress the per-row notify, then fire one manual
-- pg_notify('ed_patient_updates', 'batch') after the whole batch commits.
-- Single-row inserts (normal arrivals) notify immediately with the row.
-- =============================================================

CREATE OR REPLACE FUNCTION notify_patient_change()
RETURNS trigger AS $$
BEGIN
    IF current_setting('app.batch_mode', true) = 'true' THEN
        RETURN NEW; -- skip notify, batch upload will notify manually
    END IF;
    PERFORM pg_notify('ed_patient_updates', row_to_json(NEW)::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================================================
-- Attach triggers
-- =============================================================

CREATE TRIGGER resources_notify_trigger
AFTER INSERT OR UPDATE OR DELETE ON resources
FOR EACH ROW EXECUTE FUNCTION notify_case_change();

CREATE TRIGGER patients_notify_trigger
AFTER INSERT ON patients
FOR EACH ROW EXECUTE FUNCTION notify_patient_change();
