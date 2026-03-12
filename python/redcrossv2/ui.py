from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse


def register_ui_routes(
    app, employee_agent, coordinator_agent, default_employee_thread_id: str
) -> None:
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

    async def _read_thread_messages(
        agent_obj, thread_id: str, *, allowed_roles: set[str] | None = None
    ):
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
            if allowed_roles is not None and role not in allowed_roles:
                continue
            text = _msg_text(m).strip()
            if not text:
                continue
            items.append({"role": role, "text": text})
        return items

    async def employee_chat(request: Request):
        body = await request.json()
        text = str(body.get("text", "")).strip()
        thread_id = str(body.get("thread_id", default_employee_thread_id))
        if not text:
            return JSONResponse({"error": "Missing non-empty 'text'"}, status_code=400)

        reply = await employee_agent.run(text, thread_id=thread_id)
        return JSONResponse({"reply": reply, "thread_id": thread_id})

    async def employee_history(request: Request):
        thread_id = request.query_params.get("thread_id", default_employee_thread_id)
        return JSONResponse(
            {
                "thread_id": thread_id,
                "messages": await _read_thread_messages(
                    employee_agent, thread_id, allowed_roles={"human", "ai"}
                ),
            }
        )

    async def handoff_chat(request: Request):
        # Red Cross employee LLM -> Red Cross coordinator LLM
        thread_id = coordinator_agent.handoff_thread_id
        return JSONResponse(
            {
                "thread_id": thread_id,
                "messages": await _read_thread_messages(
                    coordinator_agent, thread_id, allowed_roles={"human", "ai"}
                ),
            }
        )

    async def intercoord_chat(request: Request):
        # Red Cross coordinator LLM <-> Hospital coordinator LLM
        thread_id = coordinator_agent.intercoord_thread_id
        return JSONResponse(
            {
                "thread_id": thread_id,
                "messages": await _read_thread_messages(
                    coordinator_agent, thread_id, allowed_roles={"human", "ai"}
                ),
            }
        )

    async def ui_page(request: Request):
        html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Red Cross Coordination Console</title>
  <style>
    :root {{
      --bg0:#fff4f1;
      --bg1:#ffdeda;
      --ink:#3a1210;
      --muted:#7a4943;
      --panel:#ffffffcc;
      --redcross:#d73f28;
      --redcross-soft:#ffd8d1;
      --hospital:#2a6de3;
      --hospital-soft:#d6e4ff;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0; color:var(--ink);
      font-family: "Trebuchet MS", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 80% 8%, #fffefb 0%, transparent 44%),
        linear-gradient(155deg, var(--bg0), var(--bg1));
      min-height:100vh;
    }}
    .wrap {{ max-width:1200px; margin:32px auto; padding:0 18px; }}
    .title {{
      background:var(--panel); border:1px solid #fff4f2;
      border-radius:22px; padding:16px 20px; backdrop-filter: blur(8px);
      box-shadow: 0 10px 35px #40130d18;
      display:flex; justify-content:space-between; align-items:center;
    }}
    .title h1 {{ margin:0; font-size:26px; letter-spacing:0.2px; }}
    .title small {{ color:var(--muted); font-weight:700; }}
    .grid {{ margin-top:16px; display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px; }}
    .panel {{
      background:var(--panel); border:1px solid #fff3ef; border-radius:20px;
      padding:14px; box-shadow: 0 10px 25px #40130d14;
      min-height:68vh; display:flex; flex-direction:column;
    }}
    .panel h2 {{ margin:4px 0 10px; font-size:18px; }}
    .messages {{
      flex:1; overflow:auto; padding:6px 6px 12px;
      border-radius:14px; background:#fff9f7;
      border:1px solid #ffe6e0;
    }}
    .bubble {{
      max-width:82%; padding:10px 12px; border-radius:12px;
      margin:8px 0; white-space:pre-wrap; line-height:1.35;
      box-shadow:0 2px 8px #00000010;
    }}
    .human {{ margin-left:auto; background:var(--redcross); color:white; }}
    .ai {{ margin-right:auto; background:var(--redcross-soft); }}
    .h-human {{ margin-left:auto; background:var(--hospital); color:white; }}
    .h-ai {{ margin-right:auto; background:var(--hospital-soft); }}
    form {{ display:flex; gap:10px; margin-top:12px; }}
    textarea {{
      flex:1; resize:vertical; min-height:52px; max-height:130px;
      border:1px solid #f1c9c0; border-radius:12px; padding:10px;
      font:inherit; outline:none;
    }}
    button {{
      border:none; background:var(--redcross); color:white; border-radius:12px;
      padding:0 16px; font-weight:700; cursor:pointer;
    }}
    .meta {{ margin-top:6px; color:var(--muted); font-size:12px; }}
    @media (max-width: 980px) {{ .grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="title">
      <h1>Red Cross Coordination Console</h1>
      <small>Employee thread: {default_employee_thread_id}</small>
    </div>
    <div class="grid">
      <section class="panel">
        <h2>Red Cross Employee ↔ Red Cross LLM</h2>
        <div id="employeeMessages" class="messages"></div>
        <form id="employeeForm">
          <textarea id="employeeInput" placeholder="Ask Red Cross agent..."></textarea>
          <button type="submit">Send</button>
        </form>
        <div class="meta">This panel is interactive.</div>
      </section>
      <section class="panel">
        <h2>Red Cross LLM -> Red Cross Coordinator (Read-Only)</h2>
        <div id="handoffMessages" class="messages"></div>
        <div class="meta">Local delegation from employee assistant to coordinator.</div>
      </section>
      <section class="panel">
        <h2>Red Cross Coordinator <-> Hospital Coordinator (Read-Only)</h2>
        <div id="intercoordMessages" class="messages"></div>
        <div class="meta">Cross-organization coordinator channel.</div>
      </section>
    </div>
  </div>
<script>
const EMPLOYEE_THREAD_ID = "{default_employee_thread_id}";
function esc(s) {{
  return (s || "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
}}
function renderMessages(el, messages, mode) {{
  const html = messages.map(m => {{
    const role = (m.role || "").toLowerCase();
    let cls = "ai";
    if (mode === "employee") cls = role.includes("human") ? "human" : "ai";
    if (mode === "interagent") cls = role.includes("human") ? "h-human" : "h-ai";
    return `<div class="bubble ${{cls}}">${{esc(m.text)}}</div>`;
  }}).join("");
  el.innerHTML = html || `<div class="meta">No messages yet.</div>`;
  el.scrollTop = el.scrollHeight;
}}
async function refreshEmployee() {{
  const res = await fetch(`/employee/history?thread_id=${{encodeURIComponent(EMPLOYEE_THREAD_ID)}}`);
  const data = await res.json();
  renderMessages(document.getElementById("employeeMessages"), data.messages || [], "employee");
}}
async function refreshHandoff() {{
  const res = await fetch("/handoff/chat");
  const data = await res.json();
  renderMessages(document.getElementById("handoffMessages"), data.messages || [], "interagent");
}}
async function refreshIntercoord() {{
  const res = await fetch("/intercoord/chat");
  const data = await res.json();
  renderMessages(document.getElementById("intercoordMessages"), data.messages || [], "interagent");
}}
async function refreshAll() {{
  try {{
    await Promise.all([
      refreshEmployee(),
      refreshHandoff(),
      refreshIntercoord(),
    ]);
  }} catch (e) {{}}
}}
document.getElementById("employeeForm").addEventListener("submit", async (ev) => {{
  ev.preventDefault();
  const input = document.getElementById("employeeInput");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  await fetch("/employee/chat", {{
    method: "POST",
    headers: {{ "Content-Type": "application/json" }},
    body: JSON.stringify({{ text, thread_id: EMPLOYEE_THREAD_ID }})
  }});
  await refreshAll();
}});
refreshAll();
setInterval(refreshAll, 1500);
</script>
</body>
</html>"""
        return HTMLResponse(html)

    app.add_route("/employee/chat", employee_chat, methods=["POST"])
    app.add_route("/employee/history", employee_history, methods=["GET"])
    app.add_route("/handoff/chat", handoff_chat, methods=["GET"])
    app.add_route("/intercoord/chat", intercoord_chat, methods=["GET"])
    app.add_route("/ui", ui_page, methods=["GET"])
