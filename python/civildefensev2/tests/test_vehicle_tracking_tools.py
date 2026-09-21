import os
from pathlib import Path

import asyncpg
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from vehicle_tracking_tools import VehicleTrackingTools, upsert_vehicle_tracking

DSN = os.getenv("CIVILDEFENSE_DB_DSN")
requires_db = pytest.mark.skipif(not DSN, reason="CIVILDEFENSE_DB_DSN not set")

TEST_VEHICLE_ID = 999001  # unlikely to collide with a real Red Cross vehicle id


@pytest.fixture
async def cleanup_tracking_row():
    yield
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(
            "DELETE FROM vehicle_tracking WHERE source_org='redcross' AND vehicle_id=$1",
            TEST_VEHICLE_ID,
        )
    finally:
        await conn.close()


@requires_db
async def test_upsert_creates_then_updates_in_place(cleanup_tracking_row):
    await upsert_vehicle_tracking(
        DSN, source_org="redcross", vehicle_id=TEST_VEHICLE_ID,
        label="Test Ambulance", lat=33.9, lng=35.5, status="active",
    )
    conn = await asyncpg.connect(DSN)
    try:
        row = await conn.fetchrow(
            "SELECT * FROM vehicle_tracking WHERE source_org='redcross' AND vehicle_id=$1",
            TEST_VEHICLE_ID,
        )
    finally:
        await conn.close()
    assert row is not None
    assert row["label"] == "Test Ambulance"
    assert row["lat"] == 33.9

    # Pushing again with a moved position must UPDATE, not duplicate.
    await upsert_vehicle_tracking(
        DSN, source_org="redcross", vehicle_id=TEST_VEHICLE_ID,
        label="Test Ambulance", lat=34.0, lng=35.7, status="active",
    )
    conn = await asyncpg.connect(DSN)
    try:
        rows = await conn.fetch(
            "SELECT * FROM vehicle_tracking WHERE source_org='redcross' AND vehicle_id=$1",
            TEST_VEHICLE_ID,
        )
    finally:
        await conn.close()
    assert len(rows) == 1
    assert rows[0]["lat"] == 34.0


@requires_db
async def test_list_tracked_vehicles_includes_pushed_row(cleanup_tracking_row):
    await upsert_vehicle_tracking(
        DSN, source_org="redcross", vehicle_id=TEST_VEHICLE_ID,
        label="Test Ambulance", lat=33.9, lng=35.5, status="active",
    )
    tools = VehicleTrackingTools(dsn=DSN)
    result = await tools.list_tracked_vehicles(source_org="redcross")
    assert "Test Ambulance" in result
    assert str(TEST_VEHICLE_ID) in result


@requires_db
async def test_list_tracked_vehicles_empty_source_org():
    tools = VehicleTrackingTools(dsn=DSN)
    result = await tools.list_tracked_vehicles(source_org="not_a_real_org")
    assert "No vehicle tracking data" in result
