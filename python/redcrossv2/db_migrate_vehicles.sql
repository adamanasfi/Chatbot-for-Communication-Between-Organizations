-- Adds vehicle location tracking, shared with other organizations on demand
-- (one-shot snapshot via the share_vehicle_location tool, not continuous
-- streaming). Run against an existing redcross_db.

CREATE TABLE IF NOT EXISTS vehicles (
    id          SERIAL PRIMARY KEY,
    label       TEXT             NOT NULL,
    lat         DOUBLE PRECISION NOT NULL,
    lng         DOUBLE PRECISION NOT NULL,
    status      TEXT             NOT NULL DEFAULT 'active',
    updated_at  TIMESTAMPTZ      NOT NULL DEFAULT now()
);

GRANT ALL ON TABLE vehicles TO redcross_app;
GRANT USAGE, SELECT ON SEQUENCE vehicles_id_seq TO redcross_app;

CREATE TRIGGER vehicles_notify_trigger
AFTER INSERT OR UPDATE OR DELETE ON vehicles
FOR EACH ROW EXECUTE FUNCTION notify_case_change();
