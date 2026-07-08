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
                thread_id=os.getenv("CIVIL_DEFENSE_EMPLOYEE_THREAD_ID", "civil-defense-employee-1"),
            )
        except Exception as e:
            print(f"[DB LISTENER ERROR] {type(e).__name__}: {e}")

    async def startup():
        conn = await asyncpg.connect(dsn)
        await conn.add_listener(channel, on_case_change)
        app.state.db_listener_conn = conn
        print(f"[DB LISTENER] Listening on channel '{channel}'")

    async def shutdown():
        conn = getattr(app.state, "db_listener_conn", None)
        if conn:
            await conn.close()
            print(f"[DB LISTENER] Connection closed")

    app.add_event_handler("startup", startup)
    app.add_event_handler("shutdown", shutdown)
