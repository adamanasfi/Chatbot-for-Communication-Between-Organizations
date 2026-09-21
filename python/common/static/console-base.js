// Shared console logic: tab framework, chat, memory, and the generic
// resources CRUD table. Org-specific script (e.g. ER's triage tab) extends
// VIEW_LOADERS and calls registerView() before the DOM-ready bootstrap runs.

const EMPLOYEE_THREAD_ID = document.body.dataset.employeeThread;
const CONSOLE_KIND = document.body.dataset.console || "er";
const HUMAN_LABEL = document.body.dataset.humanLabel || "Director";
const AGENT_LABEL = document.body.dataset.agentLabel || "Agent";
const PEER_LABEL = document.body.dataset.peerLabel || "Peer Agent";
let currentView = "chat";
let rows = [];
let editingId = null;
let currentDbTable = null;
let currentChatChannel = null;

const VIEW_LOADERS = {
  chat: () => refreshChatView(),
  db: () => refreshDbView(),
  memory: () => loadAllMemory(),
};

// ---- escaping ----
function esc(s) {
  return (s ?? "").toString()
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function renderInlineMarkdown(text) {
  return esc(text)
    .replace(
      /\[([^\]]+)\]\(app:view:([a-z0-9_-]+)\)/gi,
      '<a class="message-view-link" href="#" data-view-link="$2">$1</a>'
    )
    .replace(
      /\[([^\]]+)\]\(app:pathway:(\d+):([a-z0-9_-]+)\)/gi,
      '<a class="message-pathway-link" href="#" data-pathway-link="$2:$3">$1</a>'
    )
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function flushMarkdownList(parts, listItems) {
  if (!listItems.length) return;
  parts.push(`<ul>${listItems.map(item => `<li>${item}</li>`).join("")}</ul>`);
  listItems.length = 0;
}

function renderBasicMarkdown(text) {
  const raw = (text || "").trim();
  if (!raw) return "";

  const parts = [];
  const listItems = [];
  raw.split(/\r?\n/).forEach(line => {
    const trimmed = line.trim();
    if (!trimmed) {
      flushMarkdownList(parts, listItems);
      return;
    }

    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushMarkdownList(parts, listItems);
      const level = Math.min(heading[1].length, 4);
      parts.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      return;
    }

    const bullet = trimmed.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      listItems.push(renderInlineMarkdown(bullet[1]));
      return;
    }

    flushMarkdownList(parts, listItems);
    parts.push(`<p>${renderInlineMarkdown(trimmed)}</p>`);
  });
  flushMarkdownList(parts, listItems);
  return parts.join("");
}

// ---- tab framework ----
function registerView(name, loader) {
  VIEW_LOADERS[name] = loader;
}
function showView(name) {
  const previousView = currentView;
  if (name === "chat" && previousView !== "chat") currentChatChannel = null;
  currentView = name;
  document.querySelectorAll("[data-view]").forEach(el => { el.style.display = "none"; });
  document.querySelectorAll("[data-tab]").forEach(el => el.classList.remove("active"));
  const view = document.querySelector(`[data-view="${name}"]`);
  const tab = document.querySelector(`[data-tab="${name}"]`);
  if (view) view.style.display = "";
  if (tab) tab.classList.add("active");
  if (VIEW_LOADERS[name]) VIEW_LOADERS[name]();
}

// ---- chat ----
// Maps a peer label (e.g. "Civil Defense Agent") to the console-kind key
// its avatar/logo assets are named after (avatar-<kind>-agent.svg). Add an
// entry here whenever a new org joins the shared console framework.
const PEER_LABEL_TO_KIND = [
  ["hcc", "hcc"],
  ["civil defense", "civildefense"],
  ["red cross", "redcross"],
  ["er", "er"],
];
function inferConsoleKind(label) {
  const normalized = (label || "").toLowerCase();
  const match = PEER_LABEL_TO_KIND.find(([needle]) => normalized.includes(needle));
  return match ? match[1] : "er";
}

function messageMeta(role, mode) {
  const isHuman = role.includes("human");
  if (mode === "employee" && isHuman) {
    return { cls: "human", name: HUMAN_LABEL, avatar: "director" };
  }
  if (mode === "employee") {
    return { cls: "ai", name: AGENT_LABEL, avatar: `${CONSOLE_KIND}-agent` };
  }
  if (mode === "interagent" && isHuman) {
    return { cls: "h-human", name: PEER_LABEL, avatar: `${inferConsoleKind(PEER_LABEL)}-agent` };
  }
  return { cls: "h-ai", name: AGENT_LABEL, avatar: `${CONSOLE_KIND}-agent` };
}

function renderMessageText(text) {
  const marker = "ATTACHED_FILE_CONTEXT:";
  const raw = text || "";
  const markerIndex = raw.indexOf(marker);
  if (markerIndex === -1) return renderBasicMarkdown(raw);

  const userText = raw.slice(0, markerIndex).trim();
  const payloadText = raw.slice(markerIndex + marker.length).trim();
  let attachmentHtml = `<div class="attachment-card">
    <span class="attachment-icon"></span>
    <span class="attachment-copy">
      <strong>Attached file</strong>
      <small>Workbook attached for agent review</small>
    </span>
  </div>`;
  try {
    const payload = JSON.parse(payloadText);
    const sheets = payload.sheets || [];
    const totalRows = sheets.reduce((sum, sheet) => sum + (Number(sheet.row_count) || 0), 0);
    const sheetLabel = sheets.length === 1 ? sheets[0].name : `${sheets.length} sheets`;
    attachmentHtml = `<div class="attachment-card">
      <span class="attachment-icon"></span>
      <span class="attachment-copy">
        <strong>${esc(payload.filename || "Attached workbook")}</strong>
        <small>${esc(sheetLabel)} · ${totalRows} row${totalRows === 1 ? "" : "s"}</small>
      </span>
    </div>`;
  } catch (_) {}

  return `${userText ? `<div>${renderBasicMarkdown(userText)}</div>` : ""}${attachmentHtml}`;
}

function renderMessages(el, messages, mode, options = {}) {
  const forceScroll = Boolean(options.forceScroll);
  const isFirstRender = el.dataset.renderedOnce !== "true";
  const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
  const wasNearBottom = distanceFromBottom < 96;
  const html = messages.map(m => {
    const role = (m.role || "").toLowerCase();
    if (role === "tool_call")
      return `<div class="tool-call">🔧 ${esc(m.text)}</div>`;
    if (role === "tool_result")
      return `<div class="tool-result">↩ ${esc(m.text)}</div>`;
    const meta = messageMeta(role, mode);
    return `<article class="message-row ${meta.cls}">
      <div class="avatar avatar-${meta.avatar}" aria-hidden="true"></div>
      <div class="message-stack">
        <div class="message-meta"><strong>${esc(meta.name)}</strong></div>
        <div class="bubble">${renderMessageText(m.text)}</div>
      </div>
    </article>`;
  }).join("");
  el.innerHTML = html || `<div class="empty-chat"><div class="empty-chat-icon"></div><div>No messages yet.</div></div>`;
  el.dataset.renderedOnce = "true";
  if (forceScroll || isFirstRender || wasNearBottom) {
    el.scrollTop = el.scrollHeight;
  }
}

// ---- memory ----
async function loadMemory(ns) {
  const data = await (await fetch(`/memory/${ns}`)).json();
  const el = document.getElementById(`${ns}Items`);
  const items = data.items || [];
  if (!items.length) {
    el.innerHTML = '<div class="meta" style="padding:8px">No entries yet.</div>';
    return;
  }
  el.innerHTML = items.map(item => {
    const content = item.value?.content || JSON.stringify(item.value, null, 2);
    const ts = (item.updated_at || "").substring(0, 19).replace("T", " ");
    return `<div class="memory-item">
      <div class="mem-content">${esc(content)}</div>
      <div class="mem-meta">${ts}</div>
    </div>`;
  }).join("");
}
async function loadAllMemory() {
  await Promise.all(["semantic", "episodic", "procedural"].map(ns =>
    loadMemory(ns).catch(() => {})
  ));
}
async function refreshEmployee(options = {}) {
  const res  = await fetch(`/employee/history?thread_id=${encodeURIComponent(EMPLOYEE_THREAD_ID)}`);
  const data = await res.json();
  renderMessages(document.getElementById("employeeMessages"), data.messages || [], "employee", options);
}
async function refreshIntercoord(options = {}) {
  const data = await (await fetch("/intercoord/chat")).json();
  renderMessages(document.getElementById("intercoordMessages"), data.messages || [], "interagent", options);
}
async function refreshAll(options = {}) {
  await Promise.all([refreshEmployee(options), refreshIntercoord(options)]);
}

async function sendPatientAttachmentToAgent(file, text) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("text", text || "");
  fd.append("thread_id", EMPLOYEE_THREAD_ID);
  const resp = await fetch("/employee/patient_attachment", { method: "POST", body: fd });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok || data.ok === false) {
    throw new Error(data.error || `Attachment failed (${resp.status})`);
  }
  return data;
}

function refreshChatView() {
  if (!currentChatChannel) {
    showChatHome();
    return;
  }
  refreshCurrentChatChannel();
}

function showChatHome() {
  currentChatChannel = null;
  document.querySelectorAll("[data-chat-detail]").forEach(el => { el.style.display = "none"; });
  const home = document.getElementById("chatHome");
  if (home) home.style.display = "";
}

function openChatChannel(name) {
  currentChatChannel = name;
  const home = document.getElementById("chatHome");
  if (home) home.style.display = "none";
  document.querySelectorAll("[data-chat-detail]").forEach(el => { el.style.display = "none"; });
  const detail = document.querySelector(`[data-chat-detail="${name}"]`);
  if (detail) detail.style.display = "";
  if (name === "employee") refreshEmployee({ forceScroll: true });
  if (name === "intercoord") refreshIntercoord({ forceScroll: true });
}

function refreshCurrentChatChannel() {
  if (currentChatChannel === "employee") refreshEmployee();
  if (currentChatChannel === "intercoord") refreshIntercoord();
}

// ---- database ----
function refreshDbView() {
  if (!currentDbTable) {
    showDbHome();
    return;
  }
  openDbTable(currentDbTable);
}

function showDbHome() {
  currentDbTable = null;
  document.querySelectorAll("[data-db-detail]").forEach(el => { el.style.display = "none"; });
  const home = document.getElementById("dbHome");
  if (home) home.style.display = "";
}

function openDbTable(name) {
  currentDbTable = name;
  const home = document.getElementById("dbHome");
  if (home) home.style.display = "none";
  document.querySelectorAll("[data-db-detail]").forEach(el => { el.style.display = "none"; });
  const detail = document.querySelector(`[data-db-detail="${name}"]`);
  if (detail) detail.style.display = "";
  if (name === "resources") loadResources();
  if (name === "patients" && typeof loadPatientsTable === "function") loadPatientsTable();
}

function statusClass(status) {
  const normalized = (status || "").toLowerCase();
  if (normalized.includes("available") || normalized.includes("ready")) return "good";
  if (normalized.includes("limited") || normalized.includes("pending")) return "warn";
  if (normalized.includes("unavailable") || normalized.includes("offline") || normalized.includes("full")) return "bad";
  return "neutral";
}

function isPersonnelResource(resourceType) {
  const normalized = (resourceType || "").toLowerCase();
  return [
    "nurse", "physician", "doctor", "surgeon", "clinician", "provider",
    "staff", "technician", "therapist", "paramedic", "emt", "resident",
    "attending", "specialist", "coordinator", "administrator"
  ].some(keyword => normalized.includes(keyword));
}

function resourceGroups() {
  const groups = {
    equipment: [],
    personnel: [],
  };
  rows.forEach(row => {
    const key = isPersonnelResource(row.resource_type) ? "personnel" : "equipment";
    groups[key].push(row);
  });
  return groups;
}

function renderResourceInsights() {
  const el = document.getElementById("resourceInsights");
  if (!el) return;

  if (!rows.length) {
    el.innerHTML = `<div class="resource-empty">No resource data to visualize yet.</div>`;
    return;
  }

  const groups = resourceGroups();

  const bars = (sectionRows, cssClass) => {
    if (!sectionRows.length) {
      return `<div class="resource-empty compact">No entries in this category.</div>`;
    }
    const entries = sectionRows
      .map(r => [r.resource_type || "Unspecified", Number(r.quantity) || 0, r.status || "Unknown"])
      .sort((a, b) => b[1] - a[1]);
    const maxQty = Math.max(...entries.map(([, qty]) => qty), 1);
    return entries.map(([label, qty, status]) => {
      const pct = Math.max(6, Math.round((qty / maxQty) * 100));
      return `<div class="viz-row">
        <div class="viz-label">
          <span>${esc(label)}</span>
          <span class="viz-meta"><em>${esc(status)}</em><strong>${qty}</strong></span>
        </div>
        <div class="viz-track"><div class="viz-fill ${cssClass}" style="width:${pct}%"></div></div>
      </div>`;
    }).join("");
  };

  el.innerHTML = `
    <div class="viz-grid separated">
      <div class="viz-card equipment">
        <h3>Capacity & Equipment</h3>
        ${bars(groups.equipment, "type")}
      </div>
      <div class="viz-card personnel">
        <h3>Personnel</h3>
        ${bars(groups.personnel, "status")}
      </div>
    </div>`;
}

function resourceGroupTotal(sectionRows) {
  return sectionRows.reduce((sum, r) => sum + (Number(r.quantity) || 0), 0);
}

function resourceGroupLabel(kind, sectionRows) {
  if (!sectionRows.length) return "No entries";
  const total = resourceGroupTotal(sectionRows);
  const noun = kind === "personnel" ? "people" : "units";
  return `${total} ${noun}`;
}

function renderResourceSection(title, subtitle, sectionRows, kind) {
  const body = sectionRows.length ? sectionRows.map(r => {
    if (r.id === editingId) {
      return `<tr class="editing-row">
        <td>${r.id}</td>
        <td><input id="e_type"     value="${esc(r.resource_type)}"></td>
        <td><input id="e_qty"      value="${r.quantity}" type="number"></td>
        <td><input id="e_status"   value="${esc(r.status)}"></td>
        <td><input id="e_location" value="${esc(r.location)}"></td>
        <td>${(r.updated_at || "").substring(0,19)}</td>
        <td>
          <button class="btn btn-save"   onclick="saveRow(${r.id})">Save</button>
          <button class="btn btn-cancel" onclick="cancelEdit()">Cancel</button>
        </td>
      </tr>`;
    }
    return `<tr>
      <td>${r.id}</td>
      <td>${esc(r.resource_type)}</td>
      <td><span class="quantity-chip">${r.quantity}</span></td>
      <td><span class="status-pill ${statusClass(r.status)}">${esc(r.status)}</span></td>
      <td><span class="location-chip">${esc(r.location)}</span></td>
      <td>${(r.updated_at || "").substring(0,19)}</td>
      <td>
        <button class="btn btn-edit" onclick="startEdit(${r.id})">Edit</button>
        <button class="btn btn-del"  onclick="deleteRow(${r.id})">Delete</button>
      </td>
    </tr>`;
  }).join("") : `<tr><td class="empty-row" colspan="7">No ${title.toLowerCase()} logged yet.</td></tr>`;

  return `<section class="resource-section ${kind}">
    <div class="resource-section-head">
      <div class="resource-section-icon"></div>
      <div>
        <h3>${title}</h3>
        <p>${subtitle}</p>
      </div>
      <span class="resource-section-count">${resourceGroupLabel(kind, sectionRows)}</span>
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
      <tbody>${body}</tbody>
    </table>
  </section>`;
}

function renderTable() {
  const container = document.getElementById("resourcesBody");
  if (!rows.length) {
    container.innerHTML = `<div class="resource-empty">No resources yet.</div>`;
    return;
  }
  const groups = resourceGroups();
  container.innerHTML =
    renderResourceSection(
      "Clinical Capacity & Equipment",
      "Beds, rooms, ventilators, supplies, and physical operational assets.",
      groups.equipment,
      "equipment"
    ) +
    renderResourceSection(
      "Personnel",
      "Clinicians, staff, and response roles available for deployment.",
      groups.personnel,
      "personnel"
    );
}
async function loadResources() {
  const data = await (await fetch("/db/resources")).json();
  rows = data.rows || [];
  renderResourceInsights();
  if (!editingId) renderTable();
}
function startEdit(id)  { editingId = id;   renderTable(); }
function cancelEdit()   { editingId = null; renderTable(); }
async function saveRow(id) {
  const payload = {
    resource_type: document.getElementById("e_type").value,
    quantity:      parseInt(document.getElementById("e_qty").value) || 0,
    status:        document.getElementById("e_status").value,
    location:      document.getElementById("e_location").value,
  };
  const resp = await fetch(`/db/resources/${id}`, {
    method: "PUT",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    alert("Save failed: " + (err.error || resp.status));
    return;
  }
  editingId = null;
  await loadResources();
}
async function deleteRow(id) {
  if (!confirm("Delete this resource?")) return;
  await fetch(`/db/resources/${id}`, { method: "DELETE" });
  await loadResources();
}
async function addResource() {
  const resource_type = document.getElementById("newType").value.trim();
  const quantity      = parseInt(document.getElementById("newQty").value) || 0;
  const status        = document.getElementById("newStatus").value.trim();
  const location      = document.getElementById("newLocation").value.trim();
  if (!resource_type || !location) return;
  await fetch("/db/resources", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ resource_type, quantity, status, location })
  });
  document.getElementById("newType").value     = "";
  document.getElementById("newQty").value      = "";
  document.getElementById("newStatus").value   = "";
  document.getElementById("newLocation").value = "";
  await loadResources();
}

// ---- bootstrap ----
function initConsole() {
  document.addEventListener("click", ev => {
    const link = ev.target.closest("[data-view-link]");
    if (!link) return;
    ev.preventDefault();
    showView(link.dataset.viewLink);
  });

  const chatFileInput = document.getElementById("chatPatientFile");
  const uploadStatus = document.getElementById("chatUploadStatus");
  if (chatFileInput && uploadStatus) {
    chatFileInput.addEventListener("change", () => {
      uploadStatus.textContent = chatFileInput.files.length ? chatFileInput.files[0].name : "";
    });
  }

  document.getElementById("employeeInput").addEventListener("keydown", ev => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      document.getElementById("employeeForm").requestSubmit();
    }
  });

  document.getElementById("employeeForm").addEventListener("submit", async ev => {
    ev.preventDefault();
    const input = document.getElementById("employeeInput");
    const fileInput = document.getElementById("chatPatientFile");
    const uploadStatus = document.getElementById("chatUploadStatus");
    const text  = input.value.trim();
    const file = fileInput?.files?.[0] || null;
    if (!text && !file) return;
    input.value = "";
    if (file) {
      if (uploadStatus) uploadStatus.textContent = "Sending attachment to agent...";
      try {
        const data = await sendPatientAttachmentToAgent(file, text);
        if (fileInput) fileInput.value = "";
        if (uploadStatus) uploadStatus.textContent = `Agent reviewed ${data.record_count} row(s)`;
      } catch (err) {
        if (uploadStatus) uploadStatus.textContent = err.message;
        input.value = text;
        return;
      }
    } else if (text) {
      await fetch("/employee/chat", {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ text, thread_id: EMPLOYEE_THREAD_ID })
      });
    }
    if (uploadStatus) setTimeout(() => { uploadStatus.textContent = ""; }, 3500);
    await refreshAll({ forceScroll: true });
    if (typeof loadTriageDashboard === "function") loadTriageDashboard();
  });

  refreshAll({ forceScroll: true });
  setInterval(() => {
    (VIEW_LOADERS[currentView] || (() => {}))();
  }, 1500);
}
