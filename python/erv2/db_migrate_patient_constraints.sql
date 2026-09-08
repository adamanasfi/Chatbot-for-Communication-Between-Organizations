-- =============================================================
-- Tightens ER patient data integrity for START triage.
-- Run with: sudo -u postgres psql -p 5435 -d er_db -f python/erv2/db_migrate_patient_constraints.sql
-- =============================================================

ALTER TABLE patients
    ALTER COLUMN chief_complaint SET NOT NULL,
    ALTER COLUMN heart_rate SET NOT NULL,
    ALTER COLUMN able_to_walk SET NOT NULL,
    ALTER COLUMN spontaneous_breathing SET NOT NULL,
    ALTER COLUMN respiratory_rate SET NOT NULL,
    ALTER COLUMN radial_pulse_present SET NOT NULL,
    ALTER COLUMN obeys_commands SET NOT NULL;

ALTER TABLE patients
    ADD CONSTRAINT patients_chief_complaint_not_blank
        CHECK (btrim(chief_complaint) <> ''),
    ADD CONSTRAINT patients_heart_rate_range
        CHECK (heart_rate BETWEEN 0 AND 250),
    ADD CONSTRAINT patients_respiratory_rate_range
        CHECK (respiratory_rate BETWEEN 0 AND 80),
    ADD CONSTRAINT patients_triage_category_allowed
        CHECK (triage_category IN ('Immediate', 'Delayed', 'Minor', 'Expectant')),
    ADD CONSTRAINT patients_start_category_matches_fields
        CHECK (
            triage_category IS NULL OR triage_category = CASE
                WHEN able_to_walk THEN 'Minor'
                WHEN NOT spontaneous_breathing THEN 'Expectant'
                WHEN respiratory_rate > 30 THEN 'Immediate'
                WHEN NOT radial_pulse_present THEN 'Immediate'
                WHEN NOT obeys_commands THEN 'Immediate'
                ELSE 'Delayed'
            END
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
