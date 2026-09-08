-- =============================================================
-- Adds `patient_pathway_progress` to an already-provisioned er_db.
-- Safe to run once.
-- Run with: sudo -u postgres psql -p 5435 < erv2/db_migrate_pathways.sql
-- =============================================================

\c er_db

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
