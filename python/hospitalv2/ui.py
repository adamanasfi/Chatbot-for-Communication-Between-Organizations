from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse


def register_ui_routes(app, shared_agent, default_employee_thread_id: str) -> None:
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

    async def _read_thread_messages(thread_id: str, *, employee_view: bool = False):
        config = {"configurable": {"thread_id": thread_id}}
        state = None
        if hasattr(shared_agent.graph, "aget_state"):
            state = await shared_agent.graph.aget_state(config)
        elif hasattr(shared_agent.graph, "get_state"):
            state = shared_agent.graph.get_state(config)

        values = getattr(state, "values", None) or {}
        messages = values.get("messages", [])
        items = []
        for m in messages:
            role = str(getattr(m, "type", m.__class__.__name__)).lower()
            if employee_view and role not in {"human", "ai"}:
                continue
            items.append({"role": role, "text": _msg_text(m)})
        return items

    async def employee_chat(request: Request):
        body = await request.json()
        text = str(body.get("text", "")).strip()
        thread_id = str(body.get("thread_id", default_employee_thread_id))
        if not text:
            return JSONResponse({"error": "Missing non-empty 'text'"}, status_code=400)

        reply = await shared_agent.run(text, thread_id=thread_id)
        return JSONResponse({"reply": reply, "thread_id": thread_id})

    async def employee_history(request: Request):
        thread_id = request.query_params.get("thread_id", default_employee_thread_id)
        return JSONResponse(
            {
                "thread_id": thread_id,
                "messages": await _read_thread_messages(thread_id, employee_view=True),
            }
        )

    async def interagent_chat(request: Request):
        thread_id = shared_agent.tools_service.interagent_thread_id
        return JSONResponse(
            {"thread_id": thread_id, "messages": await _read_thread_messages(thread_id)}
        )

    async def ui_page(request: Request):
        html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Hospital Coordination Console</title>
  <style>
    :root {{
      --bg0:#f3f7ff;
      --bg1:#dfeafe;
      --ink:#12233f;
      --muted:#5a6a86;
      --panel:#ffffffcc;
      --hospital:#1f6fe5;
      --hospital-soft:#cfe0ff;
      --cross:#e64f3a;
      --cross-soft:#ffd9d2;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0; color:var(--ink);
      font-family: "Trebuchet MS", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 15% 10%, #ffffff 0%, transparent 45%),
        linear-gradient(140deg, var(--bg0), var(--bg1));
      min-height:100vh;
    }}
    .wrap {{ max-width:1200px; margin:32px auto; padding:0 18px; }}
    .title {{
      background:var(--panel); border:1px solid #ffffff;
      border-radius:22px; padding:16px 20px; backdrop-filter: blur(8px);
      box-shadow: 0 10px 35px #0d1f4018;
      display:flex; justify-content:space-between; align-items:center;
    }}
    .title h1 {{ margin:0; font-size:26px; letter-spacing:0.2px; }}
    .title small {{ color:var(--muted); font-weight:700; }}
    .grid {{ margin-top:16px; display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
    .panel {{
      background:var(--panel); border:1px solid #ffffff; border-radius:20px;
      padding:14px; box-shadow: 0 10px 25px #0d1f4014;
      min-height:68vh; display:flex; flex-direction:column;
    }}
    .panel h2 {{ margin:4px 0 10px; font-size:18px; }}
    .messages {{
      flex:1; overflow:auto; padding:6px 6px 12px;
      border-radius:14px; background:#f8fbff;
      border:1px solid #e6eefc;
    }}
    .bubble {{
      max-width:82%; padding:10px 12px; border-radius:12px;
      margin:8px 0; white-space:pre-wrap; line-height:1.35;
      box-shadow:0 2px 8px #00000010;
    }}
    .human {{ margin-left:auto; background:var(--hospital); color:white; }}
    .ai {{ margin-right:auto; background:var(--hospital-soft); }}
    .cross-human {{ margin-left:auto; background:var(--cross); color:white; }}
    .cross-ai {{ margin-right:auto; background:var(--cross-soft); }}
    form {{ display:flex; gap:10px; margin-top:12px; }}
    textarea {{
      flex:1; resize:vertical; min-height:52px; max-height:130px;
      border:1px solid #cad8f6; border-radius:12px; padding:10px;
      font:inherit; outline:none;
    }}
    button {{
      border:none; background:var(--hospital); color:white; border-radius:12px;
      padding:0 16px; font-weight:700; cursor:pointer;
    }}
    .meta {{ margin-top:6px; color:var(--muted); font-size:12px; }}
    @media (max-width: 980px) {{ .grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="title">
      <h1>Hospital Coordination Console</h1>
      <small>Employee thread: {default_employee_thread_id}</small>
    </div>
    <div class="grid">
      <section class="panel">
        <h2>Hospital Employee ↔ Hospital LLM</h2>
        <div id="employeeMessages" class="messages"></div>
        <form id="employeeForm">
          <textarea id="employeeInput" placeholder="Ask Hospital agent..."></textarea>
          <button type="submit">Send</button>
        </form>
        <div class="meta">This panel is interactive.</div>
      </section>
      <section class="panel">
        <h2>Inter-Agent Thread (Read-Only)</h2>
        <div id="interagentMessages" class="messages"></div>
        <div class="meta">Hospital LLM ↔ Red Cross LLM (same thread visible in both org UIs).</div>
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
    if (mode === "interagent") cls = role.includes("human") ? "cross-human" : "cross-ai";
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
async function refreshInteragent() {{
  const res = await fetch("/interagent/chat");
  const data = await res.json();
  renderMessages(document.getElementById("interagentMessages"), data.messages || [], "interagent");
}}
async function refreshAll() {{
  try {{ await Promise.all([refreshEmployee(), refreshInteragent()]); }} catch (e) {{}}
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
    app.add_route("/interagent/chat", interagent_chat, methods=["GET"])
    app.add_route("/ui", ui_page, methods=["GET"])
