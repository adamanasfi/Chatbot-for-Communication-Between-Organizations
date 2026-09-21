import asyncio
import os
from pathlib import Path

import asyncpg
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from vehicle_tools import VehicleTools, build_maps_link, load_vehicle, push_vehicle_tracking

DSN = os.getenv("REDCROSS_DB_DSN")
requires_db = pytest.mark.skipif(not DSN, reason="REDCROSS_DB_DSN not set")

TEST_MARKER = "TEST_VEHICLE_TOOLS_"


@pytest.fixture
async def test_vehicle_id():
    conn = await asyncpg.connect(DSN)
    try:
        row = await conn.fetchrow(
            "INSERT INTO vehicles (label, lat, lng, status) "
            "VALUES ($1, $2, $3, $4) RETURNING id",
            f"{TEST_MARKER}ambulance-1", 33.8938, 35.5018, "active",
        )
    finally:
        await conn.close()
    vehicle_id = row["id"]
    yield vehicle_id
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute("DELETE FROM vehicles WHERE id=$1", vehicle_id)
    finally:
        await conn.close()


def test_build_maps_link():
    assert build_maps_link(33.8938, 35.5018) == "https://www.google.com/maps?q=33.8938,35.5018"


@requires_db
async def test_load_vehicle_returns_row(test_vehicle_id):
    vehicle = await load_vehicle(DSN, test_vehicle_id)
    assert vehicle is not None
    assert vehicle["label"] == f"{TEST_MARKER}ambulance-1"
    assert vehicle["lat"] == 33.8938


@requires_db
async def test_load_vehicle_unknown_id_returns_none():
    assert await load_vehicle(DSN, -1) is None


@requires_db
async def test_list_vehicles_includes_label_and_id(test_vehicle_id):
    tools = VehicleTools(dsn=DSN, notifier=None)
    result = await tools.list_vehicles()
    assert f"{test_vehicle_id}: {TEST_MARKER}ambulance-1 (status: active)" in result


async def test_list_vehicles_empty_db_message(monkeypatch):
    import vehicle_tools

    class FakeConn:
        async def fetch(self, *a, **kw):
            return []
        async def close(self):
            pass

    async def fake_connect(dsn):
        return FakeConn()

    monkeypatch.setattr(vehicle_tools.asyncpg, "connect", fake_connect)
    tools = VehicleTools(dsn="fake-dsn", notifier=None)
    result = await tools.list_vehicles()
    assert "No vehicles" in result


@requires_db
async def test_share_vehicle_location_sends_single_notification(test_vehicle_id):
    sent = []

    async def fake_notifier(message_text):
        sent.append(message_text)
        return "CIVIL_DEFENSE_ACK"

    tools = VehicleTools(dsn=DSN, notifier=fake_notifier)
    result = await tools.share_vehicle_location(vehicle_id=test_vehicle_id)

    assert len(sent) == 1
    assert "33.8938" in sent[0]
    assert "https://www.google.com/maps?q=33.8938,35.5018" in sent[0]
    assert result == "CIVIL_DEFENSE_ACK"


@requires_db
async def test_share_vehicle_location_unknown_vehicle_does_not_notify():
    sent = []

    async def fake_notifier(message_text):
        sent.append(message_text)
        return "CIVIL_DEFENSE_ACK"

    tools = VehicleTools(dsn=DSN, notifier=fake_notifier)
    result = await tools.share_vehicle_location(vehicle_id=-1)

    assert sent == []
    assert "No vehicle found" in result


@requires_db
async def test_share_vehicle_location_without_notifier_returns_message_only(test_vehicle_id):
    tools = VehicleTools(dsn=DSN, notifier=None)
    result = await tools.share_vehicle_location(vehicle_id=test_vehicle_id)
    assert "Map: https://www.google.com/maps?q=33.8938,35.5018" in result


@requires_db
async def test_share_vehicle_location_geocodes_and_notifies(test_vehicle_id, monkeypatch):
    import vehicle_tools

    geocode_calls = []

    async def fake_geocode(lat, lng):
        geocode_calls.append((lat, lng))
        return "123 Fake St, Beirut"

    monkeypatch.setattr(vehicle_tools, "reverse_geocode", fake_geocode)

    tracking_calls = []

    async def fake_push(civil_defense_base_url, vehicle):
        tracking_calls.append((civil_defense_base_url, vehicle["id"]))

    monkeypatch.setattr(vehicle_tools, "push_vehicle_tracking", fake_push)

    sent = []

    async def fake_notifier(message_text):
        sent.append(message_text)
        return "ACK"

    tools = VehicleTools(dsn=DSN, civil_defense_base_url="http://fake-civildefense", notifier=fake_notifier)

    # One-shot share: a human asked, so geocoding and the A2A notify both fire.
    await tools.share_vehicle_location(vehicle_id=test_vehicle_id)
    assert len(geocode_calls) == 1
    assert len(sent) == 1
    assert "123 Fake St, Beirut" in sent[0]
    assert tracking_calls == [("http://fake-civildefense", test_vehicle_id)]


@requires_db
async def test_stream_pushes_tracking_updates_with_one_start_notification_no_geocoding(test_vehicle_id, monkeypatch):
    import vehicle_tools
    monkeypatch.setattr(vehicle_tools, "MIN_STREAM_INTERVAL_SECONDS", 1)

    geocode_calls = []

    async def fake_geocode(lat, lng):
        geocode_calls.append((lat, lng))
        return "should never be used by the stream"

    monkeypatch.setattr(vehicle_tools, "reverse_geocode", fake_geocode)

    tracking_calls = []

    async def fake_push(civil_defense_base_url, vehicle):
        tracking_calls.append(vehicle["id"])

    monkeypatch.setattr(vehicle_tools, "push_vehicle_tracking", fake_push)

    sent = []

    async def fake_notifier(message_text):
        sent.append(message_text)
        return "ACK"

    tools = VehicleTools(dsn=DSN, civil_defense_base_url="http://fake-civildefense", notifier=fake_notifier)
    start_result = await tools.start_vehicle_location_stream(
        vehicle_id=test_vehicle_id, interval_seconds=1,
    )
    assert "Started streaming" in start_result

    # The stream itself is a background task -- this test only calls
    # start once, then waits for the loop to fire on its own a few times.
    await asyncio.sleep(3.2)
    stop_result = await tools.stop_vehicle_location_stream(vehicle_id=test_vehicle_id)

    assert "Stopped location streaming" in stop_result
    assert len(tracking_calls) >= 2  # multiple DB pushes from ONE start call
    assert all(vid == test_vehicle_id for vid in tracking_calls)
    assert len(sent) == 1      # exactly one start notification, not per-tick
    assert "Started sharing live location updates" in sent[0]
    assert geocode_calls == [] # never geocodes, not even for the start notification


async def test_push_vehicle_tracking_posts_correct_payload_over_http(monkeypatch):
    """
    push_vehicle_tracking must never touch a database directly (that was
    the bug: Red Cross holding Civil Defense's DB credentials) -- it has
    to be a plain HTTP POST to Civil Defense's own ingest endpoint.
    """
    import vehicle_tools

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(vehicle_tools.httpx, "AsyncClient", FakeAsyncClient)

    vehicle = {"id": 42, "label": "Ambulance 9", "lat": 33.9, "lng": 35.5, "status": "active"}
    await push_vehicle_tracking("http://civildefense.example", vehicle)

    assert captured["url"] == "http://civildefense.example/partner/vehicle_tracking"
    assert captured["json"] == {
        "source_org": "redcross",
        "vehicle_id": 42,
        "label": "Ambulance 9",
        "lat": 33.9,
        "lng": 35.5,
        "status": "active",
    }


@requires_db
async def test_start_stream_twice_is_rejected(test_vehicle_id):
    tools = VehicleTools(dsn=DSN, notifier=None)
    first = await tools.start_vehicle_location_stream(vehicle_id=test_vehicle_id, interval_seconds=5)
    second = await tools.start_vehicle_location_stream(vehicle_id=test_vehicle_id, interval_seconds=5)
    assert "Started streaming" in first
    assert "already streaming" in second
    await tools.stop_vehicle_location_stream(vehicle_id=test_vehicle_id)


@requires_db
async def test_start_stream_twice_only_notifies_once(test_vehicle_id):
    sent = []

    async def fake_notifier(message_text):
        sent.append(message_text)
        return "ACK"

    tools = VehicleTools(dsn=DSN, notifier=fake_notifier)
    await tools.start_vehicle_location_stream(vehicle_id=test_vehicle_id, interval_seconds=5)
    await tools.start_vehicle_location_stream(vehicle_id=test_vehicle_id, interval_seconds=5)
    assert len(sent) == 1  # rejected second call must not notify again
    await tools.stop_vehicle_location_stream(vehicle_id=test_vehicle_id)


@requires_db
async def test_stop_stream_when_none_active():
    tools = VehicleTools(dsn=DSN, notifier=None)
    result = await tools.stop_vehicle_location_stream(vehicle_id=999999)
    assert "No active location stream" in result


@requires_db
async def test_start_stream_unknown_vehicle():
    tools = VehicleTools(dsn=DSN, notifier=None)
    result = await tools.start_vehicle_location_stream(vehicle_id=-1)
    assert "No vehicle found" in result


@requires_db
async def test_start_stream_enforces_minimum_interval(test_vehicle_id):
    tools = VehicleTools(dsn=DSN, notifier=None)
    result = await tools.start_vehicle_location_stream(vehicle_id=test_vehicle_id, interval_seconds=0)
    assert "Started streaming" in result
    assert "every 4s" in result  # clamped up to MIN_STREAM_INTERVAL_SECONDS
    await tools.stop_vehicle_location_stream(vehicle_id=test_vehicle_id)


@requires_db
async def test_route_simulation_moves_vehicle_toward_destination_without_streaming(test_vehicle_id, monkeypatch):
    import vehicle_tools
    monkeypatch.setattr(vehicle_tools, "MIN_ROUTE_STEP_SECONDS", 1)

    tools = VehicleTools(dsn=DSN, notifier=None)
    result = await tools.start_vehicle_route_simulation(
        vehicle_id=test_vehicle_id,
        destination_lat=34.1, destination_lng=35.7,
        steps=3, step_seconds=1,
    )
    assert "Started simulating" in result
    # Route simulation is decoupled from streaming -- it must never start
    # or mention a Civil Defense stream on its own.
    assert "stream" not in result.lower()

    # 3 steps at 1s apart finish on their own well within this wait -- a
    # finite route naturally completes rather than running forever.
    await asyncio.sleep(3.5)
    vehicle = await load_vehicle(DSN, test_vehicle_id)
    assert vehicle["lat"] == pytest.approx(34.1, abs=0.01)
    assert vehicle["lng"] == pytest.approx(35.7, abs=0.01)

    # The route task is already done by itself.
    stop_result = await tools.stop_vehicle_route_simulation(vehicle_id=test_vehicle_id)
    assert "No active route simulation" in stop_result


@requires_db
async def test_start_route_simulation_twice_is_rejected(test_vehicle_id):
    tools = VehicleTools(dsn=DSN, notifier=None)
    first = await tools.start_vehicle_route_simulation(
        vehicle_id=test_vehicle_id, destination_lat=34.1, destination_lng=35.7,
        steps=5, step_seconds=5,
    )
    second = await tools.start_vehicle_route_simulation(
        vehicle_id=test_vehicle_id, destination_lat=34.1, destination_lng=35.7,
        steps=5, step_seconds=5,
    )
    assert "Started simulating" in first
    assert "already following a simulated route" in second
    await tools.stop_vehicle_route_simulation(vehicle_id=test_vehicle_id)


@requires_db
async def test_stop_route_simulation_when_none_active():
    tools = VehicleTools(dsn=DSN, notifier=None)
    result = await tools.stop_vehicle_route_simulation(vehicle_id=999999)
    assert "No active route simulation" in result


@requires_db
async def test_start_route_simulation_unknown_vehicle():
    tools = VehicleTools(dsn=DSN, notifier=None)
    result = await tools.start_vehicle_route_simulation(
        vehicle_id=-1, destination_lat=34.1, destination_lng=35.7,
    )
    assert "No vehicle found" in result
