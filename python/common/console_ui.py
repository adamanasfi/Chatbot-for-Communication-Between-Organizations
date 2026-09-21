"""
Shared Starlette routes for the org consoles (employee chat, intercoord
read-only feed, memory inspector, generic resources CRUD, the /ui page and
its static assets). Each org's own ui.py calls register_console_routes()
first, then registers whatever org-specific routes it needs on top (e.g.
ER's patients/triage endpoints).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.staticfiles import StaticFiles


def _msg_text(msg: Any) -> str:
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and "text" in part:
                parts.append(str(part["text"]))
            else:
                parts.append(str(part))
        return " ".join(parts)
    return str(content)


async def _read_thread_messages(agent_obj, thread_id: str, *, show_tools: bool = False):
    config = {"configurable": {"thread_id": thread_id}}
    state = None
    if hasattr(agent_obj.graph, "aget_state"):
        state = await agent_obj.graph.aget_state(config)
    elif hasattr(agent_obj.graph, "get_state"):
        state = agent_obj.graph.get_state(config)

    values = getattr(state, "values", None) or {}
    messages = values.get("messages", [])
    items = []
    for m in messages:
        role = str(getattr(m, "type", m.__class__.__name__)).lower()
        if role == "ai":
            if show_tools:
                for tc in (getattr(m, "tool_calls", None) or []):
                    args_str = json.dumps(tc.get("args", {}), ensure_ascii=False)
                    items.append({"role": "tool_call", "text": f"{tc['name']}({args_str})"})
            text = _msg_text(m).strip()
            if text:
                items.append({"role": "ai", "text": text})
        elif role == "tool" and show_tools:
            text = _msg_text(m).strip()
            if text:
                items.append({"role": "tool_result", "text": text})
        elif role == "human":
            text = _msg_text(m).strip()
            if not text:
                continue
            # Strip A2A header and reclassify as peer message
            if text.startswith("SENDER:"):
                lines = text.splitlines()
                body = "\n".join(
                    l for l in lines
                    if not l.startswith("SENDER:") and not l.startswith("MODE:")
                ).strip()
                if body:
                    items.append({"role": "h-human", "text": body})
            else:
                items.append({"role": "human", "text": text})
    return items


def row_to_dict(row) -> dict:
    result = {}
    for k, v in dict(row).items():
        result[k] = v.isoformat() if hasattr(v, "isoformat") else v
    return result


def register_console_routes(
    app, agent, default_employee_thread_id: str, dsn: str, static_dir: Path,
    *, extra_template_vars: dict[str, str] | None = None,
) -> None:
    # ------------------------------------------------------------------ agent routes

    async def employee_chat(request: Request):
        body = await request.json()
        text = str(body.get("text", "")).strip()
        thread_id = str(body.get("thread_id", default_employee_thread_id))
        if not text:
            return JSONResponse({"error": "Missing non-empty 'text'"}, status_code=400)
        reply = await agent.run(text, thread_id=thread_id)
        return JSONResponse({"reply": reply, "thread_id": thread_id})

    async def employee_history(request: Request):
        thread_id = request.query_params.get("thread_id", default_employee_thread_id)
        return JSONResponse({
            "thread_id": thread_id,
            "messages": await _read_thread_messages(agent, thread_id, show_tools=True),
        })

    async def intercoord_chat(request: Request):
        thread_id = agent.intercoord_thread_id
        return JSONResponse({
            "thread_id": thread_id,
            "messages": await _read_thread_messages(agent, thread_id),
        })

    async def memory_view(request: Request):
        ns = request.path_params["namespace"]
        try:
            items = agent.store.search((agent.ORG, ns), limit=200)
            result = [
                {
                    "key": item.key,
                    "value": item.value,
                    "updated_at": item.updated_at.isoformat() if hasattr(item.updated_at, "isoformat") else str(item.updated_at),
                }
                for item in items
            ]
            if not result:
                try:
                    all_ns = [list(n) for n in agent.store.list_namespaces()]
                except Exception:
                    all_ns = []
                return JSONResponse({"items": [], "debug_namespaces": all_ns})
            return JSONResponse({"items": result})
        except Exception as exc:
            try:
                all_ns = [list(n) for n in agent.store.list_namespaces()]
            except Exception:
                all_ns = []
            return JSONResponse({"items": [], "error": str(exc), "debug_namespaces": all_ns})

    # ------------------------------------------------------------------ database routes

    async def db_resources(request: Request):
        if request.method == "GET":
            conn = await asyncpg.connect(dsn)
            try:
                rows = await conn.fetch("SELECT * FROM resources ORDER BY id")
                return JSONResponse({"rows": [row_to_dict(r) for r in rows]})
            finally:
                await conn.close()

        elif request.method == "POST":
            body = await request.json()
            conn = await asyncpg.connect(dsn)
            try:
                await conn.execute(
                    "INSERT INTO resources (resource_type, quantity, status, location) "
                    "VALUES ($1, $2, $3, $4)",
                    body["resource_type"],
                    int(body.get("quantity", 0)),
                    body["status"],
                    body["location"],
                )
                return JSONResponse({"ok": True})
            finally:
                await conn.close()

    async def db_resource_by_id(request: Request):
        resource_id = int(request.path_params["resource_id"])

        if request.method == "PUT":
            body = await request.json()
            conn = await asyncpg.connect(dsn)
            try:
                await conn.execute(
                    "UPDATE resources SET resource_type=$1, quantity=$2, status=$3, "
                    "location=$4, updated_at=now() WHERE id=$5",
                    body["resource_type"],
                    int(body.get("quantity", 0)),
                    body["status"],
                    body["location"],
                    resource_id,
                )
                return JSONResponse({"ok": True})
            except Exception as exc:
                print(f"[DB ERROR] PUT /db/resources/{resource_id}: {exc}")
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
            finally:
                await conn.close()

        elif request.method == "DELETE":
            conn = await asyncpg.connect(dsn)
            try:
                await conn.execute("DELETE FROM resources WHERE id=$1", resource_id)
                return JSONResponse({"ok": True})
            finally:
                await conn.close()

    # ------------------------------------------------------------------ UI page

    console_template = (static_dir / "console.html").read_text()

    async def ui_page(request: Request):
        html = console_template.replace("__EMPLOYEE_THREAD_ID__", default_employee_thread_id)
        for placeholder, value in (extra_template_vars or {}).items():
            html = html.replace(placeholder, value)
        return HTMLResponse(html)

    # ------------------------------------------------------------------ register

    app.add_route("/employee/chat",    employee_chat,    methods=["POST"])
    app.add_route("/employee/history", employee_history, methods=["GET"])
    app.add_route("/intercoord/chat",  intercoord_chat,  methods=["GET"])
    app.add_route("/memory/{namespace}", memory_view,    methods=["GET"])
    app.add_route("/db/resources",               db_resources,      methods=["GET", "POST"])
    app.add_route("/db/resources/{resource_id}", db_resource_by_id, methods=["PUT", "DELETE"])
    app.add_route("/ui", ui_page, methods=["GET"])
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    common_static = Path(__file__).parent / "static"
    app.mount("/common-static", StaticFiles(directory=common_static), name="common-static")
