// Red Cross-only view: vehicle location tracking (map + table), built on
// top of the shared tab/chat/memory/resources framework in
// /common-static/console-base.js.
//
// No one types coordinates: a new vehicle is placed by clicking the map
// (or dropped at a default point if the map isn't available), and an
// existing vehicle's location is edited by dragging its marker.

const BEIRUT_CENTER = { lat: 33.8938, lng: 35.5018 };

let vehicleRows = [];
let editingVehicleId = null;
let vehicleMap = null;
let vehicleMarkers = {}; // id -> google.maps.Marker
let pendingNewVehicle = null; // { label, status } while waiting for a map click

function renderVehicleTable() {
  const tbody = document.getElementById("vehiclesBody");
  if (!vehicleRows.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty-row">No vehicles yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = vehicleRows.map(r => {
    if (r.id === editingVehicleId) {
      return `<tr>
        <td>${r.id}</td>
        <td><input id="ev_label"  value="${esc(r.label)}"></td>
        <td>${r.lat.toFixed(4)}</td>
        <td>${r.lng.toFixed(4)}</td>
        <td><input id="ev_status" value="${esc(r.status)}"></td>
        <td>${(r.updated_at || "").substring(0,19)}</td>
        <td>
          <button class="btn btn-save"   onclick="saveVehicleRow(${r.id})">Save</button>
          <button class="btn btn-cancel" onclick="cancelEditVehicle()">Cancel</button>
        </td>
      </tr>`;
    }
    return `<tr>
      <td>${r.id}</td>
      <td>${esc(r.label)}</td>
      <td>${r.lat.toFixed(4)}</td>
      <td>${r.lng.toFixed(4)}</td>
      <td><span class="status-pill ${statusClass(r.status)}">${esc(r.status)}</span></td>
      <td>${(r.updated_at || "").substring(0,19)}</td>
      <td>
        <button class="btn btn-edit" onclick="startEditVehicle(${r.id})">Edit</button>
        <button class="btn btn-del"  onclick="deleteVehicleRow(${r.id})">Delete</button>
        <button class="btn btn-dev"  onclick="simulateVehicle(${r.id})" title="Dev/test only -- fakes movement toward a random nearby point">Simulate</button>
      </td>
    </tr>`;
  }).join("");
}

async function simulateVehicle(id) {
  await fetch(`/db/vehicles/${id}/simulate`, { method: "POST" });
}

async function saveVehiclePosition(id, lat, lng) {
  const vehicle = vehicleRows.find(v => v.id === id);
  if (!vehicle) return;
  await fetch(`/db/vehicles/${id}`, {
    method: "PUT",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ label: vehicle.label, lat, lng, status: vehicle.status }),
  });
  vehicle.lat = lat;
  vehicle.lng = lng;
  if (!editingVehicleId) renderVehicleTable();
}

function attachMarkerBehavior(marker, v) {
  const info = new google.maps.InfoWindow();
  marker._isDragging = false;
  marker.addListener("click", () => {
    info.setContent(
      `<strong>${esc(v.label)}</strong><br>Status: ${esc(v.status)}<br>` +
      `Updated: ${esc((v.updated_at || "").substring(0,19))}<br>` +
      `<small>Drag the marker to move this vehicle.</small><br>` +
      `<a href="https://www.google.com/maps?q=${v.lat},${v.lng}" target="_blank" rel="noopener">Open in Google Maps</a>`
    );
    info.open(vehicleMap, marker);
  });
  marker.addListener("dragstart", () => { marker._isDragging = true; });
  marker.addListener("dragend", () => {
    marker._isDragging = false;
    const pos = marker.getPosition();
    saveVehiclePosition(v.id, pos.lat(), pos.lng());
  });
}

function syncVehicleMarkers() {
  if (!vehicleMap) return;
  const seen = new Set();
  vehicleRows.forEach(v => {
    seen.add(v.id);
    const position = { lat: v.lat, lng: v.lng };
    const label = `${v.label} (${v.status})`;
    let marker = vehicleMarkers[v.id];
    if (!marker) {
      marker = new google.maps.Marker({ position, map: vehicleMap, title: label, draggable: true });
      attachMarkerBehavior(marker, v);
      vehicleMarkers[v.id] = marker;
    } else {
      // Don't fight an in-progress drag with a poll-driven position reset.
      if (!marker._isDragging) {
        marker.setPosition(position);
      }
      marker.setTitle(label);
    }
  });
  Object.keys(vehicleMarkers).forEach(id => {
    if (!seen.has(Number(id))) {
      vehicleMarkers[id].setMap(null);
      delete vehicleMarkers[id];
    }
  });
}

async function loadVehicles() {
  const data = await (await fetch("/db/vehicles")).json();
  vehicleRows = data.rows || [];
  if (!editingVehicleId) renderVehicleTable();
  syncVehicleMarkers();
}
function startEditVehicle(id)  { editingVehicleId = id;   renderVehicleTable(); }
function cancelEditVehicle()   { editingVehicleId = null; renderVehicleTable(); }
async function saveVehicleRow(id) {
  const vehicle = vehicleRows.find(v => v.id === id);
  const payload = {
    label:  document.getElementById("ev_label").value,
    lat:    vehicle.lat,
    lng:    vehicle.lng,
    status: document.getElementById("ev_status").value,
  };
  const resp = await fetch(`/db/vehicles/${id}`, {
    method: "PUT",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    alert("Save failed: " + (err.error || resp.status));
    return;
  }
  editingVehicleId = null;
  await loadVehicles();
}
async function deleteVehicleRow(id) {
  if (!confirm("Delete this vehicle?")) return;
  await fetch(`/db/vehicles/${id}`, { method: "DELETE" });
  await loadVehicles();
}

async function createVehicleAt(label, status, lat, lng) {
  await fetch("/db/vehicles", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ label, lat, lng, status }),
  });
  await loadVehicles();
}

// Clicking "+ Add" doesn't create the vehicle immediately: if the map is
// available, it arms map-click placement so the user picks the spot by
// hand instead of typing coordinates. Without a map (no API key), it
// falls back to dropping the vehicle at a default point they can still
// drag once a map becomes available.
async function addVehicle() {
  const label  = document.getElementById("newVehicleLabel").value.trim();
  const status = document.getElementById("newVehicleStatus").value.trim() || "active";
  if (!label) return;

  if (!vehicleMap) {
    await createVehicleAt(label, status, BEIRUT_CENTER.lat, BEIRUT_CENTER.lng);
    document.getElementById("newVehicleLabel").value  = "";
    document.getElementById("newVehicleStatus").value = "active";
    return;
  }

  pendingNewVehicle = { label, status };
  document.getElementById("mapPlacementHint").style.display = "";
  document.getElementById("addVehicleBtn").disabled = true;
  vehicleMap.setOptions({ draggableCursor: "crosshair" });
}

// Called by the Google Maps JS API script tag once it has loaded (only
// present in the page when GOOGLE_MAPS_API_KEY is configured server-side).
function initMap() {
  const notice = document.getElementById("mapsKeyNotice");
  if (notice) notice.style.display = "none";
  vehicleMap = new google.maps.Map(document.getElementById("vehicleMap"), {
    center: BEIRUT_CENTER,
    zoom: 12,
  });
  vehicleMap.addListener("click", async (event) => {
    if (!pendingNewVehicle) return;
    const { label, status } = pendingNewVehicle;
    pendingNewVehicle = null;
    document.getElementById("mapPlacementHint").style.display = "none";
    document.getElementById("addVehicleBtn").disabled = false;
    vehicleMap.setOptions({ draggableCursor: null });
    document.getElementById("newVehicleLabel").value  = "";
    document.getElementById("newVehicleStatus").value = "active";
    await createVehicleAt(label, status, event.latLng.lat(), event.latLng.lng());
  });
  syncVehicleMarkers();
}

registerView("vehicles", loadVehicles);
initConsole();
