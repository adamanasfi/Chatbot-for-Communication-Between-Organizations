import asyncpg
import json
import os
from langchain_core.messages import SystemMessage


def register_db_listener(app, employee_agent, dsn: str, channel: str) -> None:

    async def on_case_change(connection, pid, channel, payload):
        try:
            data = json.loads(payload)
            table = data.get("table", "unknown")
            op = data.get("op", "unknown")
            row_id = data.get("row_id")
            diff = data.get("diff", {})

            if op == "INSERT":
                content = f"[DB EVENT] A new row was inserted into '{table}' (id={row_id}): {json.dumps(diff)}"
            elif op == "UPDATE":
                content = f"[DB EVENT] Row id={row_id} in '{table}' was updated: {json.dumps(diff)}"
            elif op == "DELETE":
                content = f"[DB EVENT] Row id={row_id} was deleted from '{table}': {json.dumps(diff)}"
            else:
                content = f"[DB EVENT] Unknown operation on '{table}': {payload}"

            await employee_agent.run(
                messages=[SystemMessage(content=content)],
                thread_id=os.getenv("ER_EMPLOYEE_THREAD_ID", "er-employee-1"),
            )
        except Exception as e:
            print(f"[DB LISTENER ERROR] {type(e).__name__}: {e}")

    async def on_patient_change(connection, pid, channel, payload):
        # Notification carries no authoritative data of its own -- it's just a
        # wake-up call. The agent always re-reads the full patients table itself
        # (run_start_triage selects every row with triage_category IS NULL).
        #
        # The instruction for what to do is put directly in the message text,
        # not left to the agent to infer from a general rule stated once in
        # the system prompt -- batch vs. single dispatch is a deterministic
        # fact about this event, not something that should depend on the LLM
        # correctly recalling and cross-referencing a rule from elsewhere.
        try:
            if payload == "batch":
                content = (
                    "[DB EVENT] A batch of new patients was uploaded to 'patients'. "
                    "This is a mass-casualty/batch arrival: call run_start_triage on "
                    "every patient with no triage_category set, then call "
                    "generate_ed_acuity_snapshot. Do not attempt clinical pathway "
                    "matching for this event -- it has no per-patient chief complaint."
                )
            else:
                content = (
                    f"[DB EVENT] A new patient was inserted into 'patients': {payload}. "
                    "This is a SINGLE day-to-day patient arrival, not a mass-casualty "
                    "batch: do NOT call run_start_triage or generate_ed_acuity_snapshot "
                    "for this event. Instead, call list_pathways and check this "
                    "patient's chief_complaint against each pathway's keywords. If one "
                    "plausibly matches, preview it (advance_pathway with no "
                    "node_id/answer -- this does not record anything) and tell the ED "
                    "Director which pathway may apply and the first question, awaiting "
                    "their confirmation before proceeding. If nothing matches, just "
                    "acknowledge the new patient internally."
                )

            await employee_agent.run(
                messages=[SystemMessage(content=content)],
                thread_id=os.getenv("ER_EMPLOYEE_THREAD_ID", "er-employee-1"),
            )
        except Exception as e:
            print(f"[DB LISTENER ERROR] {type(e).__name__}: {e}")

    async def startup():
        conn = await asyncpg.connect(dsn)
        await conn.add_listener(channel, on_case_change)
        await conn.add_listener("ed_patient_updates", on_patient_change)
        app.state.db_listener_conn = conn
        print(f"[DB LISTENER] Listening on channels '{channel}' and 'ed_patient_updates'")

    async def shutdown():
        conn = getattr(app.state, "db_listener_conn", None)
        if conn:
            await conn.close()
            print(f"[DB LISTENER] Connection closed")

    app.add_event_handler("startup", startup)
    app.add_event_handler("shutdown", shutdown)
