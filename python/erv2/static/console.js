// ER-specific: the Triage tab (upload, START distribution, resource
// comparison, resource-request form). Everything else (chat, memory,
// database CRUD, the tab framework) lives in /common-static/console-base.js.

const TRIAGE_TILE_ORDER = ["Immediate", "Delayed", "Minor", "Expectant", "Pending"];
const TRIAGE_COLORS = {
  Immediate: "#c0392b", Delayed: "#d4a017", Minor: "#2a9d5c",
  Expectant: "#2b2b2b", Pending: "#8a97a3",
};
let patientRows = [];
let editingPatientId = null;

function polarPoint(cx, cy, r, angleDeg) {
  const rad = (angleDeg - 90) * Math.PI / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

function renderTriagePie(counts) {
  const wrap = document.getElementById("triagePieWrap");
  const total = TRIAGE_TILE_ORDER.reduce((s, l) => s + (counts[l] || 0), 0);
  const legend = `<div class="pie-legend">${
    TRIAGE_TILE_ORDER.map(l => `<div class="row">
      <span class="swatch" style="background:${TRIAGE_COLORS[l]}"></span>
      <span>${l}</span><span class="count">${counts[l] || 0}</span>
    </div>`).join("")
  }</div>`;

  if (!total) {
    wrap.innerHTML = `<div class="meta">No patients yet.</div>${legend}`;
    return;
  }

  const cx = 100, cy = 100, r = 90;
  let angle = 0;
  const slices = [];
  TRIAGE_TILE_ORDER.forEach(label => {
    const n = counts[label] || 0;
    if (!n) return;
    const sweep = (n / total) * 360;
    const startAngle = angle;
    const endAngle = angle + sweep;
    angle = endAngle;

    let path;
    if (sweep >= 359.99) {
      // full circle -- arc math degenerates, draw as a circle instead
      path = `M ${cx - r} ${cy} A ${r} ${r} 0 1 0 ${cx + r} ${cy} A ${r} ${r} 0 1 0 ${cx - r} ${cy} Z`;
    } else {
      const start = polarPoint(cx, cy, r, endAngle);
      const end = polarPoint(cx, cy, r, startAngle);
      const largeArc = sweep > 180 ? 1 : 0;
      path = `M ${cx} ${cy} L ${start.x} ${start.y} A ${r} ${r} 0 ${largeArc} 0 ${end.x} ${end.y} Z`;
    }

    const midAngle = (startAngle + endAngle) / 2;
    const labelPt = polarPoint(cx, cy, r * 0.62, midAngle);
    slices.push(
      `<path d="${path}" fill="${TRIAGE_COLORS[label]}" stroke="var(--surface)" stroke-width="2"></path>` +
      `<text class="pie-slice-label" x="${labelPt.x}" y="${labelPt.y}">${n}</text>`
    );
  });

  wrap.innerHTML =
    `<svg width="340" height="340" viewBox="0 0 200 200">${slices.join("")}</svg>` +
    legend;
}

async function loadTriageSnapshot() {
  const data = await (await fetch("/triage/snapshot")).json();
  const snapshot = data.snapshot;
  if (!data.ok || !snapshot) {
    renderTriagePie({});
    renderAcuityBoard(null);
    renderResourceBarsFromSnapshot(null);
    return;
  }
  renderTriagePie(snapshot.counts || {});
  renderAcuityBoard(snapshot.groups || {});
  renderResourceBarsFromSnapshot(snapshot.resources || {});
}

function vitalValue(value, suffix = "") {
  return value === null || value === undefined || value === "" ? "—" : `${value}${suffix}`;
}

function renderPatientCard(card) {
  const vitals = card.vitals || {};
  const signals = card.signals || [];
  const flags = card.flags || [];
  const signalHtml = signals.map(signal =>
    `<span class="signal ${esc(signal.state || "unknown")}">${esc(signal.label)}: ${esc(signal.value)}</span>`
  ).join("");
  const flagHtml = flags.map(flag =>
    `<span class="acuity-flag ${esc(flag.severity || "stable")}">${esc(flag.label)}</span>`
  ).join("");
  return `<article class="patient-card">
    <div class="patient-card-head">
      <strong>#${card.id}</strong>
      <span>${esc(card.category || "Pending")}</span>
    </div>
    <p>${esc(card.chief_complaint || "No chief complaint recorded")}</p>
    <div class="vitals-grid">
      <span><small>HR</small>${vitalValue(vitals.heart_rate)}</span>
      <span><small>RR</small>${vitalValue(vitals.respiratory_rate)}</span>
    </div>
    <div class="signal-grid">${signalHtml}</div>
    <div class="acuity-flags">${flagHtml}</div>
  </article>`;
}

function renderAcuityBoard(groups) {
  const board = document.getElementById("patientAcuityBoard");
  if (!board) return;
  if (!groups) {
    board.innerHTML = `<div class="resource-empty">No agent-generated acuity snapshot yet.</div>`;
    return;
  }
  board.innerHTML = TRIAGE_TILE_ORDER.map(category => {
    const patients = groups[category] || [];
    return `<section class="acuity-lane acuity-${category.toLowerCase()}">
      <div class="acuity-lane-head">
        <span class="swatch" style="background:${TRIAGE_COLORS[category]}"></span>
        <h3>${category}</h3>
        <strong>${patients.length}</strong>
      </div>
      <div class="acuity-cards">
        ${patients.length ? patients.map(renderPatientCard).join("") : `<div class="acuity-empty">No patients</div>`}
      </div>
    </section>`;
  }).join("");
}

function loadResourceBars() {
  return loadTriageSnapshot();
}

function renderResourceBarsFromSnapshot(resources) {
  if (!resources) {
    renderResourceBarGroup(document.getElementById("equipmentResourceBars"), [], "No agent-generated resource snapshot yet.");
    renderResourceBarGroup(document.getElementById("personnelResourceBars"), [], "No agent-generated resource snapshot yet.");
    return;
  }
  renderResourceBarGroup(document.getElementById("equipmentResourceBars"), resources.equipment || [], "No capacity or equipment logged yet.");
  renderResourceBarGroup(document.getElementById("personnelResourceBars"), resources.personnel || [], "No personnel logged yet.");
}

function renderResourceBarGroup(el, byType, emptyText) {
  if (!el) return;
  const entries = Array.isArray(byType)
    ? byType.map(item => [item.resource_type, item.quantity])
    : Object.entries(byType).sort((a, b) => b[1] - a[1]);
  if (!entries.length) {
    el.innerHTML = `<div class="meta resource-bar-empty">${emptyText}</div>`;
    return;
  }
  const maxQty = Math.max(...entries.map(([, q]) => q), 1);
  el.innerHTML = entries.map(([type, qty]) => {
    const pct = Math.max(4, Math.round((qty / maxQty) * 100));
    return `<div class="bar-row">
      <div class="bar-label"><span>${esc(type)}</span><span class="qty">${qty}</span></div>
      <div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div>
    </div>`;
  }).join("");
}

async function loadTriageDashboard() {
  await loadTriageSnapshot();
}

function ynValue(v) {
  return v === true ? "Yes" : v === false ? "No" : "";
}

function triageClass(category) {
  return (category || "Pending").toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

function renderPatientsTable() {
  const tbody = document.getElementById("patientsDbBody");
  if (!tbody) return;
  if (!patientRows.length) {
    tbody.innerHTML = `<tr><td class="empty-row" colspan="11">No patients yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = patientRows.map(r => {
    if (r.id === editingPatientId) {
      return `<tr>
        <td>${r.id}</td>
        <td><input id="p_complaint" value="${esc(r.chief_complaint || "")}"></td>
        <td><input id="p_hr" value="${r.heart_rate ?? ""}" type="number"></td>
        <td><select id="p_walk">${boolOptions(r.able_to_walk)}</select></td>
        <td><select id="p_breathing">${boolOptions(r.spontaneous_breathing)}</select></td>
        <td><input id="p_rr" value="${r.respiratory_rate ?? ""}" type="number"></td>
        <td><select id="p_pulse">${boolOptions(r.radial_pulse_present)}</select></td>
        <td><select id="p_commands">${boolOptions(r.obeys_commands)}</select></td>
        <td><input id="p_triage" value="${esc(r.triage_category || "")}"></td>
        <td>${(r.updated_at || "").substring(0,19)}</td>
        <td>
          <button class="btn btn-save" onclick="savePatient(${r.id})">Save</button>
          <button class="btn btn-cancel" onclick="cancelPatientEdit()">Cancel</button>
        </td>
      </tr>`;
    }
    return `<tr>
      <td>${r.id}</td>
      <td>${esc(r.chief_complaint || "")}</td>
      <td>${r.heart_rate ?? ""}</td>
      <td>${ynValue(r.able_to_walk)}</td>
      <td>${ynValue(r.spontaneous_breathing)}</td>
      <td>${r.respiratory_rate ?? ""}</td>
      <td>${ynValue(r.radial_pulse_present)}</td>
      <td>${ynValue(r.obeys_commands)}</td>
      <td><span class="triage-pill ${triageClass(r.triage_category)}">${esc(r.triage_category || "Pending")}</span></td>
      <td>${(r.updated_at || "").substring(0,19)}</td>
      <td>
        <button class="btn btn-edit" onclick="startPatientEdit(${r.id})">Edit</button>
        <button class="btn btn-del" onclick="deletePatient(${r.id})">Delete</button>
      </td>
    </tr>`;
  }).join("");
}

function boolOptions(value) {
  const choices = [["", ""], ["Yes", "Yes"], ["No", "No"]];
  const current = ynValue(value);
  return choices.map(([label, val]) =>
    `<option value="${val}" ${current === val ? "selected" : ""}>${label}</option>`
  ).join("");
}

function boolPayload(id) {
  const value = document.getElementById(id).value;
  if (value === "Yes") return true;
  if (value === "No") return false;
  return null;
}

async function loadPatientsTable() {
  const data = await (await fetch("/db/patients")).json();
  patientRows = data.rows || [];
  if (!editingPatientId) renderPatientsTable();
}

function startPatientEdit(id) { editingPatientId = id; renderPatientsTable(); }
function cancelPatientEdit() { editingPatientId = null; renderPatientsTable(); }

async function savePatient(id) {
  const payload = {
    chief_complaint: document.getElementById("p_complaint").value,
    heart_rate: document.getElementById("p_hr").value,
    able_to_walk: boolPayload("p_walk"),
    spontaneous_breathing: boolPayload("p_breathing"),
    respiratory_rate: document.getElementById("p_rr").value,
    radial_pulse_present: boolPayload("p_pulse"),
    obeys_commands: boolPayload("p_commands"),
    triage_category: document.getElementById("p_triage").value,
  };
  const resp = await fetch(`/db/patients/${id}`, {
    method: "PUT",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    alert("Save failed: " + (err.error || resp.status));
    return;
  }
  editingPatientId = null;
  await loadPatientsTable();
}

async function deletePatient(id) {
  if (!confirm("Delete this patient?")) return;
  await fetch(`/db/patients/${id}`, { method: "DELETE" });
  await loadPatientsTable();
}

async function addPatient() {
  const payload = {
    chief_complaint: document.getElementById("newPatientComplaint").value.trim(),
    heart_rate: document.getElementById("newPatientHR").value,
    able_to_walk: boolPayload("newPatientWalk"),
    spontaneous_breathing: boolPayload("newPatientBreathing"),
    respiratory_rate: document.getElementById("newPatientRR").value,
    radial_pulse_present: boolPayload("newPatientPulse"),
    obeys_commands: boolPayload("newPatientCommands"),
  };
  if (!payload.chief_complaint) return;
  const resp = await fetch("/db/patients", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  const data = await resp.json();
  if (!resp.ok || data.ok === false) {
    alert("Add failed: " + (data.error || resp.status));
    return;
  }
  ["newPatientComplaint", "newPatientHR", "newPatientWalk", "newPatientBreathing",
   "newPatientRR", "newPatientPulse", "newPatientCommands"].forEach(id => {
    document.getElementById(id).value = "";
  });
  await loadPatientsTable();
}

// ---- pathways (rendered inline in the employee chat panel) ----
let currentPathwayGraph = null;
let currentPathwayState = null;

const PW_NODE_W = 210;

// Invoked by clicking an [here](app:pathway:<patientId>:<pathwayId>) link
// in a chat message. Loads the graph + current state into the pathway
// panel alongside the chat -- there is no separate pathways page.
async function openChatPathwayGraph(patientId, pathwayId) {
  const grid = document.getElementById("employeeChatGrid");
  const panel = document.getElementById("employeePathwayPanel");
  const graphWrap = document.getElementById("pathwayGraphWrap");
  document.getElementById("pathwayChooser").style.display = "none";

  panel.style.display = "";
  grid.classList.add("with-pathway");
  graphWrap.innerHTML = `<div class="meta">Loading...</div>`;

  const [graphResp, stateResp] = await Promise.all([
    fetch(`/pathways/${pathwayId}/graph`),
    fetch(`/pathways/state?patient_id=${patientId}&pathway_id=${pathwayId}`),
  ]);
  const graphData = await graphResp.json();
  const stateData = await stateResp.json();

  if (!graphData.ok) {
    graphWrap.innerHTML = `<div class="meta">${esc(graphData.error || "Could not load pathway graph.")}</div>`;
    return;
  }
  currentPathwayGraph = graphData.graph;

  if (!stateData.ok) {
    graphWrap.innerHTML = `<div class="meta">${esc(stateData.error || "Could not load pathway state.")}</div>`;
    return;
  }
  currentPathwayState = { ...stateData.state, patientId: Number(patientId), pathwayId };

  renderPathwayGraph();
}

function wrapPathwayLabel(text, maxChars) {
  const words = (text || "").split(" ");
  const lines = [];
  let current = "";
  words.forEach(word => {
    const attempt = current ? `${current} ${word}` : word;
    if (attempt.length > maxChars && current) {
      lines.push(current);
      current = word;
    } else {
      current = attempt;
    }
  });
  if (current) lines.push(current);
  return lines;
}

function pathwayNodeClass(n) {
  if (n.kind === "decision") return "pw-node-decision";
  if (n.kind === "action") return "pw-node-action";
  const text = `${n.label} ${n.disposition || ""}`.toLowerCase();
  if (text.includes("admit") || text.includes("admission")) return "pw-node-terminal-admit";
  if (text.includes("discharge")) return "pw-node-terminal-discharge";
  return "pw-node-terminal-neutral";
}

// Action nodes show their actual protocol content (labs/medications/resource
// needs), not just a title -- matching the source pathway's density instead
// of hiding that information behind a click.
function pathwayNodeContentLines(n) {
  const header = wrapPathwayLabel(n.label, 24);
  if (n.kind !== "action") return { header, bullets: [] };
  const bulletSource = [
    ...(n.labs || []).map(x => `Lab: ${x}`),
    ...(n.medications || []).map(x => `Med: ${x}`),
    ...(n.resource_needs || []).map(x => `Need: ${x}`),
  ];
  const bullets = bulletSource.flatMap(text => wrapPathwayLabel(text, 28));
  return { header, bullets };
}

function pathwayNodeHeight(n) {
  if (n.kind === "decision") return 80;
  if (n.kind === "terminal") return 60;
  const { header, bullets } = pathwayNodeContentLines(n);
  return Math.max(60, 20 + (header.length + bullets.length) * 16 + 14);
}

function pathwayNodeCenter(n) {
  const h = pathwayNodeHeight(n);
  return { cx: n.x + PW_NODE_W / 2, cy: n.y + h / 2 };
}

function renderPathwayGraph() {
  const wrap = document.getElementById("pathwayGraphWrap");
  if (!currentPathwayGraph) return;
  const graph = currentPathwayGraph;
  const state = currentPathwayState || {};
  const visited = new Set(state.visited_nodes || []);
  const pendingId = state.pending_node ? state.pending_node.id : null;
  const nodesById = Object.fromEntries(graph.nodes.map(n => [n.id, n]));

  const maxX = Math.max(...graph.nodes.map(n => n.x)) + PW_NODE_W + 40;
  const maxY = Math.max(...graph.nodes.map(n => n.y + pathwayNodeHeight(n))) + 40;

  const edgesSvg = graph.edges.map(e => {
    const from = nodesById[e.from], to = nodesById[e.to];
    if (!from || !to) return "";
    const a = pathwayNodeCenter(from), b = pathwayNodeCenter(to);
    const mx = (a.cx + b.cx) / 2, my = (a.cy + b.cy) / 2;
    const label = e.label
      ? `<text class="pw-edge-label" x="${mx}" y="${my - 6}" text-anchor="middle">${esc(e.label)}</text>`
      : "";
    return `<line class="pw-edge" x1="${a.cx}" y1="${a.cy}" x2="${b.cx}" y2="${b.cy}" marker-end="url(#pwArrow)"></line>${label}`;
  }).join("");

  const nodesSvg = graph.nodes.map(n => {
    const cls = ["pw-node", pathwayNodeClass(n)];
    if (visited.has(n.id)) cls.push("visited");
    if (n.id === pendingId) cls.push("pending");

    const h = pathwayNodeHeight(n);
    const cx = n.x + PW_NODE_W / 2;
    let tspans;

    if (n.kind === "action") {
      const { header, bullets } = pathwayNodeContentLines(n);
      const lineHeight = 16;
      let y = n.y + 18;
      const headerTspans = header.map(line => {
        const t = `<tspan x="${cx}" y="${y}" class="pw-node-title-line">${esc(line)}</tspan>`;
        y += lineHeight;
        return t;
      }).join("");
      const bulletTspans = bullets.map(line => {
        const t = `<tspan x="${cx}" y="${y}" class="pw-node-bullet-line">${esc(line)}</tspan>`;
        y += lineHeight;
        return t;
      }).join("");
      tspans = headerTspans + bulletTspans;
    } else {
      const centerY = n.y + h / 2;
      const lines = wrapPathwayLabel(n.label, 20);
      const lineHeight = 14;
      const startY = centerY - ((lines.length - 1) * lineHeight) / 2;
      tspans = lines.map((line, i) =>
        `<tspan x="${cx}" y="${startY + i * lineHeight}" dominant-baseline="middle">${esc(line)}</tspan>`
      ).join("");
    }

    let shape;
    if (n.kind === "decision") {
      const points = [
        [n.x + 20, n.y], [n.x + PW_NODE_W - 20, n.y], [n.x + PW_NODE_W, n.y + h / 2],
        [n.x + PW_NODE_W - 20, n.y + h], [n.x + 20, n.y + h], [n.x, n.y + h / 2],
      ].map(p => p.join(",")).join(" ");
      shape = `<polygon class="pw-node-shape" points="${points}"></polygon>`;
    } else if (n.kind === "terminal") {
      shape = `<ellipse class="pw-node-shape" cx="${cx}" cy="${n.y + h / 2}" rx="${PW_NODE_W / 2}" ry="${h / 2}"></ellipse>`;
    } else {
      shape = `<rect class="pw-node-shape" x="${n.x}" y="${n.y}" width="${PW_NODE_W}" height="${h}" rx="8"></rect>`;
    }

    return `<g class="${cls.join(' ')}" data-node-id="${n.id}" onclick="onPathwayNodeClick('${n.id}')">
      ${shape}
      <text class="pw-node-label">${tspans}</text>
    </g>`;
  }).join("");

  wrap.innerHTML = `<svg viewBox="0 0 ${maxX} ${Math.max(maxY, 100)}" width="${maxX}" height="${Math.max(maxY, 100)}">
    <defs>
      <marker id="pwArrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" fill="var(--border-strong)"></path>
      </marker>
    </defs>
    ${edgesSvg}
    ${nodesSvg}
  </svg>`;
}

function onPathwayNodeClick(nodeId) {
  if (!currentPathwayState || !currentPathwayState.pending_node) return;
  if (currentPathwayState.pending_node.id !== nodeId) return; // only the pending node is actionable
  showPathwayChooser(currentPathwayState.pending_node);
}

function showPathwayChooser(pending) {
  const chooser = document.getElementById("pathwayChooser");
  chooser.style.display = "";
  const details = pending.option_details || {};
  chooser.innerHTML = `<div class="meta">${esc(pending.question)}</div>` +
    pending.options.map(opt => `
      <button class="pathway-option-btn" onclick="choosePathwayAnswer('${pending.id}','${opt}')">
        <strong>${esc(opt)}</strong>
        ${details[opt] ? `<small>${esc(details[opt])}</small>` : ""}
      </button>
    `).join("");
}

async function choosePathwayAnswer(nodeId, answer) {
  const { patientId, pathwayId } = currentPathwayState;
  const resp = await fetch("/pathways/advance", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ patient_id: patientId, pathway_id: pathwayId, node_id: nodeId, answer }),
  });
  const data = await resp.json();
  if (!data.ok) {
    alert("Could not record answer: " + data.error);
    return;
  }
  currentPathwayState = { ...data.state, patientId, pathwayId };
  document.getElementById("pathwayChooser").style.display = "none";
  renderPathwayGraph();
  // The reminder for the new pending question is posted into the chat
  // thread server-side (see ui.py's post_pathway_reminder_to_chat) --
  // the chat poll picks it up on its own, no client action needed here.
}

document.addEventListener("click", (evt) => {
  const link = evt.target.closest("[data-pathway-link]");
  if (!link) return;
  evt.preventDefault();
  const [patientId, pathwayId] = link.dataset.pathwayLink.split(":");
  openChatPathwayGraph(patientId, pathwayId);
});

registerView("triage", loadTriageDashboard);
initConsole();
