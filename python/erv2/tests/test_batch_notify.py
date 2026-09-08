"""
Integration tests for insert_patients_batch's notification behavior:
- More than one record -> app.batch_mode suppresses per-row notify, exactly
  one 'batch' string fires after commit.
- Exactly one record -> no batch_mode, the per-row trigger fires normally
  with the full row as JSON (so a single manual add can be acted on
  immediately with real patient data, e.g. a pathway suggestion).
Requires a live er_db (ER_DB_DSN) -- skipped if not configured.
"""
import asyncio
import json
import os
from pathlib import Path

import asyncpg
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from triage import insert_patients_batch

DSN = os.getenv("ER_DB_DSN")
requires_db = pytest.mark.skipif(not DSN, reason="ER_DB_DSN not set")

TEST_MARKER = "TEST_BATCH_NOTIFY_"


def _make_records(n: int) -> list[dict]:
    return [
        {
            "chief_complaint": f"{TEST_MARKER}{i}",
            "heart_rate": 90,
            "able_to_walk": True,
            "spontaneous_breathing": True,
            "respiratory_rate": 16,
            "radial_pulse_present": True,
            "obeys_commands": True,
        }
        for i in range(n)
    ]


async def _wait_for(predicate, timeout=2.0, interval=0.1):
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return False


@pytest.fixture
async def cleanup_test_patients():
    yield
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(
            "DELETE FROM patients WHERE chief_complaint LIKE $1",
            f"{TEST_MARKER}%",
        )
    finally:
        await conn.close()


@requires_db
async def test_batch_insert_fires_exactly_one_notification(cleanup_test_patients):
    notifications = []

    listener_conn = await asyncpg.connect(DSN)
    await listener_conn.add_listener(
        "ed_patient_updates",
        lambda connection, pid, channel, payload: notifications.append(payload),
    )

    try:
        records = _make_records(5)
        inserted = await insert_patients_batch(DSN, records)
        assert inserted == 5

        await _wait_for(lambda: len(notifications) >= 1)
        # Give any (incorrect) extra per-row notifications a chance to arrive too,
        # so we're actually asserting "exactly one", not "at least one, checked early".
        await asyncio.sleep(0.3)
    finally:
        await listener_conn.close()

    assert notifications == ["batch"], (
        f"expected exactly one 'batch' notification, got {notifications!r} "
        "-- batch_mode suppression may be broken"
    )


@requires_db
async def test_single_insert_fires_row_json_not_batch_string(cleanup_test_patients):
    notifications = []

    listener_conn = await asyncpg.connect(DSN)
    await listener_conn.add_listener(
        "ed_patient_updates",
        lambda connection, pid, channel, payload: notifications.append(payload),
    )

    try:
        [record] = _make_records(1)
        inserted = await insert_patients_batch(DSN, [record])
        assert inserted == 1

        await _wait_for(lambda: len(notifications) >= 1)
        await asyncio.sleep(0.3)
    finally:
        await listener_conn.close()

    assert len(notifications) == 1, (
        f"expected exactly one notification for a single insert, got {notifications!r}"
    )
    assert notifications[0] != "batch", "a single insert should not use the generic 'batch' payload"
    payload = json.loads(notifications[0])
    assert payload["chief_complaint"] == record["chief_complaint"]


@requires_db
async def test_batch_insert_actually_writes_all_rows(cleanup_test_patients):
    records = _make_records(3)
    await insert_patients_batch(DSN, records)

    conn = await asyncpg.connect(DSN)
    try:
        rows = await conn.fetch(
            "SELECT chief_complaint, triage_category FROM patients "
            "WHERE chief_complaint LIKE $1 ORDER BY id",
            f"{TEST_MARKER}%",
        )
    finally:
        await conn.close()

    assert len(rows) == 3
    # triage_category must start NULL -- it's the agent's job (run_start_triage)
    # to fill it in later, not the insert itself.
    assert all(r["triage_category"] is None for r in rows)
