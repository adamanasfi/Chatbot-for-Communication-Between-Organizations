-- =============================================================
-- Adds the `patients` table (+ dedicated batch-mode-aware notify)
-- to an already-provisioned er_db. Safe to run once.
-- Run with: sudo -u postgres psql -p 5435 < erv2/db_migrate_patients.sql
-- =============================================================

\c er_db

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

-- Dedicated trigger function (separate from notify_case_change/resources):
-- batch uploads set app.batch_mode to suppress the per-row notify and fire
-- one manual pg_notify('ed_patient_updates', 'batch') after the batch commits.
-- Single-row inserts notify immediately with the row itself.
CREATE OR REPLACE FUNCTION notify_patient_change()
RETURNS trigger AS $$
BEGIN
    IF current_setting('app.batch_mode', true) = 'true' THEN
        RETURN NEW;
    END IF;
    PERFORM pg_notify('ed_patient_updates', row_to_json(NEW)::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER patients_notify_trigger
AFTER INSERT ON patients
FOR EACH ROW EXECUTE FUNCTION notify_patient_change();
