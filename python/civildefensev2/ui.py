import asyncpg
import json
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse


def register_ui_routes(
    app, agent, default_employee_thread_id: str, dsn: str
) -> None:

    # ------------------------------------------------------------------ helpers

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
        agent_obj, thread_id: str, *, show_tools: bool = False
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

    def _row_to_dict(row) -> dict:
        result = {}
        for k, v in dict(row).items():
            result[k] = v.isoformat() if hasattr(v, "isoformat") else v
        return result

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
                return JSONResponse({"rows": [_row_to_dict(r) for r in rows]})
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

    async def ui_page(request: Request):
        html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Civil Defense Coordination Console</title>
  <style>
    :root {{
      --bg0:#f3f7ff; --bg1:#dfeafe; --ink:#12233f; --muted:#5a6a86;
      --panel:#ffffffcc; --accent:#1f6fe5; --accent-soft:#cfe0ff;
      --peer:#e64f3a; --peer-soft:#ffd9d2;
      --green:#2a9d5c;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0; color:var(--ink);
      font-family:"Trebuchet MS","Segoe UI",sans-serif; font-size:18px;
      background:
        radial-gradient(circle at 15% 10%, #ffffff 0%, transparent 45%),
        linear-gradient(140deg, var(--bg0), var(--bg1));
      min-height:100vh;
    }}
    .wrap {{ max-width:1200px; margin:32px auto; padding:0 18px; }}

    /* title */
    .title {{
      background:var(--panel); border:1px solid #ffffff;
      border-radius:22px; padding:16px 20px; backdrop-filter:blur(8px);
      box-shadow:0 10px 35px #0d1f4018;
      display:flex; justify-content:space-between; align-items:center;
    }}
    .title h1 {{ margin:0; font-size:34px; letter-spacing:0.2px; }}
    .title-right {{ display:flex; flex-direction:column; align-items:flex-end; gap:8px; }}
    .title-right small {{ color:var(--muted); font-weight:700; font-size:18px; }}

    /* tabs */
    .tabs {{ display:flex; gap:8px; }}
    .tab {{
      border:none; border-radius:10px; padding:8px 22px;
      font-weight:700; font-size:16px; cursor:pointer;
      background:#e8eef8; color:var(--muted); transition:background 0.15s;
    }}
    .tab.active {{ background:var(--accent); color:white; }}

    /* chat grid */
    .grid {{ margin-top:16px; display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
    .panel {{
      background:var(--panel); border:1px solid #ffffff; border-radius:20px;
      padding:14px; box-shadow:0 10px 25px #0d1f4014;
      min-height:68vh; display:flex; flex-direction:column;
    }}
    .panel h2 {{ margin:4px 0 10px; font-size:24px; }}
    .messages {{
      flex:1; overflow:auto; padding:6px 6px 12px;
      border-radius:14px; background:#f8fbff; border:1px solid #e6eefc;
    }}
    .bubble {{
      max-width:82%; padding:10px 12px; border-radius:12px;
      margin:8px 0; white-space:pre-wrap; line-height:1.4; font-size:19px;
      box-shadow:0 2px 8px #00000010;
    }}
    .human       {{ margin-left:auto;  background:var(--accent);      color:white; }}
    .ai          {{ margin-right:auto; background:var(--accent-soft);              }}
    .h-human {{ margin-left:auto;  background:var(--peer);         color:white; }}
    .h-ai    {{ margin-right:auto; background:var(--peer-soft);                }}
    form {{ display:flex; gap:10px; margin-top:12px; }}
    textarea {{
      flex:1; resize:vertical; min-height:52px; max-height:130px;
      border:1px solid #cad8f6; border-radius:12px; padding:10px;
      font:inherit; font-size:18px; outline:none;
    }}
    button {{
      border:none; background:var(--accent); color:white; border-radius:12px;
      padding:0 16px; font-weight:700; font-size:18px; cursor:pointer;
    }}
    .meta {{ margin-top:6px; color:var(--muted); font-size:14px; }}
    @media (max-width:980px) {{ .grid {{ grid-template-columns:1fr; }} }}

    /* tool call bubbles */
    .tool-call {{
      margin:6px 0; padding:6px 12px;
      background:#f5f5f5; border:1px solid #ddd; border-radius:8px;
      font-family:monospace; font-size:13px; color:#555;
      white-space:pre-wrap; word-break:break-all;
    }}
    .tool-result {{
      margin:2px 0 8px 12px; padding:4px 10px;
      border-left:3px solid #ccc; font-size:13px;
      color:#888; white-space:pre-wrap; word-break:break-word;
    }}

    /* memory view */
    .memory-wrap {{ margin-top:16px; }}
    .memory-grid {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px; }}
    .memory-section {{
      background:var(--panel); border:1px solid #dde8ff; border-radius:20px;
      padding:14px; box-shadow:0 10px 25px #0d1f4014;
      max-height:75vh; display:flex; flex-direction:column;
    }}
    .memory-section h2 {{ margin:4px 0 10px; font-size:22px; }}
    .memory-items {{ flex:1; overflow-y:auto; }}
    .memory-item {{
      background:#f3f7ff; border:1px solid #dde8ff; border-radius:10px;
      padding:10px 12px; margin-bottom:8px;
    }}
    .memory-item .mem-content {{
      color:var(--ink); font-size:15px; white-space:pre-wrap;
      word-break:break-word; margin-bottom:4px;
    }}
    .memory-item .mem-meta {{ color:var(--muted); font-size:12px; }}

    /* database view */
    .db-wrap {{ margin-top:16px; }}
    .db-panel {{
      background:var(--panel); border:1px solid #ffffff; border-radius:20px;
      padding:20px; box-shadow:0 10px 25px #0d1f4014;
    }}
    .db-panel h2 {{ margin:0 0 16px; font-size:24px; }}
    .add-form {{
      display:flex; gap:8px; margin-bottom:20px; flex-wrap:wrap; align-items:center;
      background:#f8fbff; border:1px solid #e6eefc;
      border-radius:14px; padding:12px;
    }}
    .add-form input {{
      flex:1; min-width:110px; border:1px solid #cad8f6; border-radius:10px;
      padding:8px 12px; font:inherit; font-size:16px; outline:none;
    }}
    .add-form button {{
      background:var(--green); border-radius:10px;
      padding:8px 18px; font-size:16px; white-space:nowrap;
    }}
    table {{ width:100%; border-collapse:collapse; font-size:17px; }}
    thead th {{
      text-align:left; padding:10px 12px;
      background:var(--accent); color:white; font-weight:700;
    }}
    thead th:first-child {{ border-radius:10px 0 0 0; }}
    thead th:last-child  {{ border-radius:0 10px 0 0; }}
    tbody td {{ padding:10px 12px; border-bottom:1px solid #e6eefc; vertical-align:middle; }}
    tbody tr:last-child td {{ border-bottom:none; }}
    tbody tr:hover td {{ background:#f3f7ff; }}
    tbody td input {{
      width:100%; border:1px solid #cad8f6; border-radius:8px;
      padding:4px 8px; font:inherit; font-size:16px; outline:none;
    }}
    .btn {{
      border:none; color:white; border-radius:8px;
      padding:5px 14px; font-weight:700; font-size:14px; cursor:pointer; margin:0 2px;
    }}
    .btn-edit   {{ background:var(--peer);  }}
    .btn-del    {{ background:#c0392b;       }}
    .btn-save   {{ background:var(--green);  }}
    .btn-cancel {{ background:#888;          }}
  </style>
</head>
<body>
<div class="wrap">

  <div class="title">
    <h1>Civil Defense Coordination Console</h1>
    <div class="title-right">
      <small>Employee thread: {default_employee_thread_id}</small>
      <div class="tabs">
        <button class="tab active" id="tabChat"   onclick="showChat()">Chat</button>
        <button class="tab"        id="tabDB"     onclick="showDB()">Database</button>
        <button class="tab"        id="tabMemory" onclick="showMemory()">Memory</button>
      </div>
    </div>
  </div>

  <!-- CHAT VIEW -->
  <div id="chatView">
    <div class="grid">
      <section class="panel">
        <h2>Civil Defense Employee ↔ Civil Defense Agent</h2>
        <div id="employeeMessages" class="messages"></div>
        <form id="employeeForm">
          <textarea id="employeeInput" placeholder="Ask Civil Defense agent..."></textarea>
          <button type="submit">Send</button>
        </form>
        <div class="meta">This panel is interactive.</div>
      </section>
      <section class="panel">
        <h2>Civil Defense Agent &lt;-&gt; Red Cross Agent (Read-Only)</h2>
        <div id="intercoordMessages" class="messages"></div>
        <div class="meta">Cross-organization coordination channel.</div>
      </section>
    </div>
  </div>

  <!-- DATABASE VIEW -->
  <div id="dbView" style="display:none" class="db-wrap">
    <div class="db-panel">
      <h2>Resources</h2>

      <div class="add-form">
        <input id="newType"     placeholder="Resource type" />
        <input id="newQty"      placeholder="Quantity" type="number" style="max-width:110px" />
        <input id="newStatus"   placeholder="Status" />
        <input id="newLocation" placeholder="Location" />
        <button onclick="addResource()">+ Add</button>
      </div>

      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Resource Type</th>
            <th>Quantity</th>
            <th>Status</th>
            <th>Location</th>
            <th>Updated</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="resourcesBody"></tbody>
      </table>
    </div>
  </div>

  <!-- MEMORY VIEW -->
  <div id="memoryView" style="display:none" class="memory-wrap">
    <div class="memory-grid">
      <div class="memory-section">
        <h2>Semantic Memory</h2>
        <div id="semanticItems" class="memory-items"></div>
      </div>
      <div class="memory-section">
        <h2>Episodic Memory</h2>
        <div id="episodicItems" class="memory-items"></div>
      </div>
      <div class="memory-section">
        <h2>Procedural Memory</h2>
        <div id="proceduralItems" class="memory-items"></div>
      </div>
    </div>
  </div>

</div>
<script>
const EMPLOYEE_THREAD_ID = "{default_employee_thread_id}";
let currentView = "chat";
let rows = [];
let editingId = null;

// ---- escaping ----
function esc(s) {{
  return (s ?? "").toString()
    .replaceAll("&","&amp;").replaceAll("<","&lt;")
    .replaceAll(">","&gt;").replaceAll('"',"&quot;");
}}

// ---- tab toggle ----
function showChat() {{
  currentView = "chat";
  document.getElementById("chatView").style.display   = "";
  document.getElementById("dbView").style.display     = "none";
  document.getElementById("memoryView").style.display = "none";
  document.getElementById("tabChat").classList.add("active");
  document.getElementById("tabDB").classList.remove("active");
  document.getElementById("tabMemory").classList.remove("active");
}}
function showDB() {{
  currentView = "db";
  document.getElementById("chatView").style.display   = "none";
  document.getElementById("dbView").style.display     = "";
  document.getElementById("memoryView").style.display = "none";
  document.getElementById("tabChat").classList.remove("active");
  document.getElementById("tabDB").classList.add("active");
  document.getElementById("tabMemory").classList.remove("active");
  loadResources();
}}
function showMemory() {{
  currentView = "memory";
  document.getElementById("chatView").style.display   = "none";
  document.getElementById("dbView").style.display     = "none";
  document.getElementById("memoryView").style.display = "";
  document.getElementById("tabChat").classList.remove("active");
  document.getElementById("tabDB").classList.remove("active");
  document.getElementById("tabMemory").classList.add("active");
  loadAllMemory();
}}

// ---- chat ----
function renderMessages(el, messages, mode) {{
  const html = messages.map(m => {{
    const role = (m.role || "").toLowerCase();
    if (role === "tool_call")
      return `<div class="tool-call">🔧 ${{esc(m.text)}}</div>`;
    if (role === "tool_result")
      return `<div class="tool-result">↩ ${{esc(m.text)}}</div>`;
    let cls = "ai";
    if (mode === "employee") cls = role.includes("human") ? "human" : "ai";
    if (mode === "interagent") cls = role.includes("human") ? "h-human" : "h-ai";
    return `<div class="bubble ${{cls}}">${{esc(m.text)}}</div>`;
  }}).join("");
  el.innerHTML = html || `<div class="meta">No messages yet.</div>`;
  el.scrollTop = el.scrollHeight;
}}

// ---- memory ----
async function loadMemory(ns) {{
  const data = await (await fetch(`/memory/${{ns}}`)).json();
  const el = document.getElementById(`${{ns}}Items`);
  const items = data.items || [];
  if (!items.length) {{
    el.innerHTML = '<div class="meta" style="padding:8px">No entries yet.</div>';
    return;
  }}
  el.innerHTML = items.map(item => {{
    const content = item.value?.content || JSON.stringify(item.value, null, 2);
    const ts = (item.updated_at || "").substring(0, 19).replace("T", " ");
    return `<div class="memory-item">
      <div class="mem-content">${{esc(content)}}</div>
      <div class="mem-meta">${{ts}}</div>
    </div>`;
  }}).join("");
}}
async function loadAllMemory() {{
  await Promise.all(["semantic", "episodic", "procedural"].map(ns =>
    loadMemory(ns).catch(() => {{}})
  ));
}}
async function refreshEmployee() {{
  const res  = await fetch(`/employee/history?thread_id=${{encodeURIComponent(EMPLOYEE_THREAD_ID)}}`);
  const data = await res.json();
  renderMessages(document.getElementById("employeeMessages"), data.messages || [], "employee");
}}
async function refreshIntercoord() {{
  const data = await (await fetch("/intercoord/chat")).json();
  renderMessages(document.getElementById("intercoordMessages"), data.messages || [], "interagent");
}}
async function refreshAll() {{
  await Promise.all([refreshEmployee(), refreshIntercoord()]);
}}
document.getElementById("employeeForm").addEventListener("submit", async ev => {{
  ev.preventDefault();
  const input = document.getElementById("employeeInput");
  const text  = input.value.trim();
  if (!text) return;
  input.value = "";
  await fetch("/employee/chat", {{
    method: "POST",
    headers: {{"Content-Type":"application/json"}},
    body: JSON.stringify({{ text, thread_id: EMPLOYEE_THREAD_ID }})
  }});
  await refreshAll();
}});

// ---- database ----
function renderTable() {{
  const tbody = document.getElementById("resourcesBody");
  if (!rows.length) {{
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:#aaa;padding:20px">No resources yet.</td></tr>`;
    return;
  }}
  tbody.innerHTML = rows.map(r => {{
    if (r.id === editingId) {{
      return `<tr>
        <td>${{r.id}}</td>
        <td><input id="e_type"     value="${{esc(r.resource_type)}}"></td>
        <td><input id="e_qty"      value="${{r.quantity}}" type="number"></td>
        <td><input id="e_status"   value="${{esc(r.status)}}"></td>
        <td><input id="e_location" value="${{esc(r.location)}}"></td>
        <td>${{(r.updated_at || "").substring(0,19)}}</td>
        <td>
          <button class="btn btn-save"   onclick="saveRow(${{r.id}})">Save</button>
          <button class="btn btn-cancel" onclick="cancelEdit()">Cancel</button>
        </td>
      </tr>`;
    }}
    return `<tr>
      <td>${{r.id}}</td>
      <td>${{esc(r.resource_type)}}</td>
      <td>${{r.quantity}}</td>
      <td>${{esc(r.status)}}</td>
      <td>${{esc(r.location)}}</td>
      <td>${{(r.updated_at || "").substring(0,19)}}</td>
      <td>
        <button class="btn btn-edit" onclick="startEdit(${{r.id}})">Edit</button>
        <button class="btn btn-del"  onclick="deleteRow(${{r.id}})">Delete</button>
      </td>
    </tr>`;
  }}).join("");
}}
async function loadResources() {{
  const data = await (await fetch("/db/resources")).json();
  rows = data.rows || [];
  if (!editingId) renderTable();
}}
function startEdit(id)  {{ editingId = id;   renderTable(); }}
function cancelEdit()   {{ editingId = null; renderTable(); }}
async function saveRow(id) {{
  const payload = {{
    resource_type: document.getElementById("e_type").value,
    quantity:      parseInt(document.getElementById("e_qty").value) || 0,
    status:        document.getElementById("e_status").value,
    location:      document.getElementById("e_location").value,
  }};
  const resp = await fetch(`/db/resources/${{id}}`, {{
    method: "PUT",
    headers: {{"Content-Type":"application/json"}},
    body: JSON.stringify(payload),
  }});
  if (!resp.ok) {{
    const err = await resp.json().catch(() => ({{}}));
    alert("Save failed: " + (err.error || resp.status));
    return;
  }}
  editingId = null;
  await loadResources();
}}
async function deleteRow(id) {{
  if (!confirm("Delete this resource?")) return;
  await fetch(`/db/resources/${{id}}`, {{ method: "DELETE" }});
  await loadResources();
}}
async function addResource() {{
  const resource_type = document.getElementById("newType").value.trim();
  const quantity      = parseInt(document.getElementById("newQty").value) || 0;
  const status        = document.getElementById("newStatus").value.trim();
  const location      = document.getElementById("newLocation").value.trim();
  if (!resource_type || !location) return;
  await fetch("/db/resources", {{
    method: "POST",
    headers: {{"Content-Type":"application/json"}},
    body: JSON.stringify({{ resource_type, quantity, status, location }})
  }});
  document.getElementById("newType").value     = "";
  document.getElementById("newQty").value      = "";
  document.getElementById("newStatus").value   = "";
  document.getElementById("newLocation").value = "";
  await loadResources();
}}

// ---- auto-refresh ----
refreshAll();
setInterval(() => {{
  if (currentView === "chat") refreshAll().catch(() => {{}});
  else if (currentView === "db" && !editingId) loadResources().catch(() => {{}});
  else if (currentView === "memory") loadAllMemory().catch(() => {{}});
}}, 1500);
</script>
</body>
</html>"""
        return HTMLResponse(html)

    # ------------------------------------------------------------------ register

    app.add_route("/employee/chat",    employee_chat,    methods=["POST"])
    app.add_route("/employee/history", employee_history, methods=["GET"])
    app.add_route("/intercoord/chat",  intercoord_chat,  methods=["GET"])
    app.add_route("/memory/{namespace}", memory_view,    methods=["GET"])
    app.add_route("/db/resources",               db_resources,      methods=["GET", "POST"])
    app.add_route("/db/resources/{resource_id}", db_resource_by_id, methods=["PUT", "DELETE"])
    app.add_route("/ui", ui_page, methods=["GET"])
