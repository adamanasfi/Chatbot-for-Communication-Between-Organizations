"""
Integration test for PathwayTools.advance_pathway against the live er_db.
Requires ER_DB_DSN -- skipped if not configured.
"""
import os
from pathlib import Path

import asyncpg
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from pathway_tools import PathwayTools
from triage import insert_patients_batch

DSN = os.getenv("ER_DB_DSN")
requires_db = pytest.mark.skipif(not DSN, reason="ER_DB_DSN not set")

TEST_MARKER = "TEST_PATHWAY_TOOLS_"


@pytest.fixture
async def test_patient_id():
    [record] = await _insert_one_patient()
    conn = await asyncpg.connect(DSN)
    try:
        row = await conn.fetchrow(
            "SELECT id FROM patients WHERE chief_complaint = $1", record["chief_complaint"],
        )
    finally:
        await conn.close()
    patient_id = row["id"]
    yield patient_id
    conn = await asyncpg.connect(DSN)
    try:
        # ON DELETE CASCADE also removes any patient_pathway_progress rows.
        await conn.execute("DELETE FROM patients WHERE id = $1", patient_id)
    finally:
        await conn.close()


async def _insert_one_patient():
    records = [{
        "chief_complaint": f"{TEST_MARKER}heavy menstrual bleeding",
        "heart_rate": 90,
        "able_to_walk": True,
        "spontaneous_breathing": True,
        "respiratory_rate": 16,
        "radial_pulse_present": True,
        "obeys_commands": True,
    }]
    await insert_patients_batch(DSN, records)
    return records


@requires_db
async def test_advance_pathway_persists_answers_across_calls(test_patient_id):
    tools = PathwayTools(dsn=DSN)

    first = await tools.advance_pathway(
        patient_id=test_patient_id, pathway_id="abnormal_uterine_bleeding",
    )
    assert "Is the pregnancy test positive?" in first

    second = await tools.advance_pathway(
        patient_id=test_patient_id, pathway_id="abnormal_uterine_bleeding",
        node_id="pregnancy_test", answer="no",
    )
    assert "Are there signs of shock?" in second

    conn = await asyncpg.connect(DSN)
    try:
        row = await conn.fetchrow(
            "SELECT answers FROM patient_pathway_progress WHERE patient_id=$1 AND pathway_id=$2",
            test_patient_id, "abnormal_uterine_bleeding",
        )
    finally:
        await conn.close()
    assert row is not None
    assert '"pregnancy_test": "no"' in row["answers"]


@requires_db
async def test_advance_pathway_rejects_invalid_answer(test_patient_id):
    tools = PathwayTools(dsn=DSN)
    result = await tools.advance_pathway(
        patient_id=test_patient_id, pathway_id="abnormal_uterine_bleeding",
        node_id="pregnancy_test", answer="maybe",
    )
    assert "Invalid answer" in result


@requires_db
async def test_advance_pathway_unknown_pathway_id(test_patient_id):
    tools = PathwayTools(dsn=DSN)
    result = await tools.advance_pathway(patient_id=test_patient_id, pathway_id="not_a_real_pathway")
    assert "Unknown pathway_id" in result


@requires_db
async def test_advance_pathway_autonomously_notifies_hcc_on_reaching_action_node(test_patient_id):
    sent = []

    async def fake_notifier(message_text):
        sent.append(message_text)
        return "HCC_ACK"

    tools = PathwayTools(dsn=DSN, hcc_notifier=fake_notifier)

    # First decision (pregnancy_test -> no) only lands on another decision
    # node (signs_of_shock) -- no action node reached yet, no notify.
    await tools.advance_pathway(
        patient_id=test_patient_id, pathway_id="abnormal_uterine_bleeding",
        node_id="pregnancy_test", answer="no",
    )
    assert sent == []

    # signs_of_shock -> yes lands on the shock_management action node, which
    # carries labs/medications/resource_needs -- this must autonomously fire.
    await tools.advance_pathway(
        patient_id=test_patient_id, pathway_id="abnormal_uterine_bleeding",
        node_id="signs_of_shock", answer="yes",
    )
    assert len(sent) == 1
    assert "Shock management" in sent[0]
    assert "IV conjugated estrogen" in sent[0]
    assert "IV access" in sent[0]


@requires_db
async def test_advance_pathway_preview_call_does_not_renotify(test_patient_id):
    sent = []

    async def fake_notifier(message_text):
        sent.append(message_text)
        return "HCC_ACK"

    tools = PathwayTools(dsn=DSN, hcc_notifier=fake_notifier)

    await tools.advance_pathway(
        patient_id=test_patient_id, pathway_id="abnormal_uterine_bleeding",
        node_id="pregnancy_test", answer="no",
    )
    await tools.advance_pathway(
        patient_id=test_patient_id, pathway_id="abnormal_uterine_bleeding",
        node_id="signs_of_shock", answer="yes",
    )
    assert len(sent) == 1

    # A no-op preview call (no node_id/answer) re-runs the same visited
    # nodes -- it must not re-fire the resource request.
    await tools.advance_pathway(
        patient_id=test_patient_id, pathway_id="abnormal_uterine_bleeding",
    )
    assert len(sent) == 1
