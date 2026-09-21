-- Stores the latest known position of vehicles tracked by partner
-- organizations (currently Red Cross), written directly by that partner's
-- process -- no A2A call, no LLM inference on either side. Civil Defense's
-- agent reads this on demand via a tool; nothing here triggers it
-- automatically. Run against an existing civildefense_db.

CREATE TABLE IF NOT EXISTS vehicle_tracking (
    id          SERIAL PRIMARY KEY,
    source_org  TEXT             NOT NULL,
    vehicle_id  INTEGER          NOT NULL,
    label       TEXT             NOT NULL,
    lat         DOUBLE PRECISION NOT NULL,
    lng         DOUBLE PRECISION NOT NULL,
    status      TEXT             NOT NULL DEFAULT 'active',
    updated_at  TIMESTAMPTZ      NOT NULL DEFAULT now(),
    UNIQUE (source_org, vehicle_id)
);

GRANT ALL ON TABLE vehicle_tracking TO civildefense_app;
GRANT USAGE, SELECT ON SEQUENCE vehicle_tracking_id_seq TO civildefense_app;
