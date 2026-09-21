// Civil Defense-only view: visualization of partner-org vehicle tracking
// (currently Red Cross), built on top of the shared tab/chat/memory/
// resources framework in /common-static/console-base.js. Civil Defense
// never edits a tracked position -- those are pushed here directly by the
// partner's own process (see civildefensev2/ui.py's /partner/vehicle_
// tracking route) -- but can delete a row to clear stale/test entries
// from its own database.

let trackedVehicles = [];
let vehicleMap = null;
let vehicleMarkers = {}; // "source_org:vehicle_id" -> google.maps.Marker

function renderVehicleTable() {
  const tbody = document.getElementById("vehiclesBody");
  if (!trackedVehicles.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty-row">No tracked vehicles yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = trackedVehicles.map(v => `<tr>
    <td>${esc(v.source_org)}</td>
    <td>${v.vehicle_id}</td>
    <td>${esc(v.label)}</td>
    <td>${v.lat.toFixed(4)}</td>
    <td>${v.lng.toFixed(4)}</td>
    <td><span class="status-pill ${statusClass(v.status)}">${esc(v.status)}</span></td>
    <td>${(v.updated_at || "").substring(0,19)}</td>
    <td><button class="btn btn-del" onclick="deleteTrackedVehicle(${v.id})">Delete</button></td>
  </tr>`).join("");
}

async function deleteTrackedVehicle(id) {
  if (!confirm("Delete this tracked vehicle entry?")) return;
  await fetch(`/db/vehicle_tracking/${id}`, { method: "DELETE" });
  await loadVehicles();
}

function syncVehicleMarkers() {
  if (!vehicleMap) return;
  const seen = new Set();
  trackedVehicles.forEach(v => {
    const key = `${v.source_org}:${v.vehicle_id}`;
    seen.add(key);
    const position = { lat: v.lat, lng: v.lng };
    const label = `${v.label} (${v.source_org})`;
    let marker = vehicleMarkers[key];
    if (!marker) {
      marker = new google.maps.Marker({ position, map: vehicleMap, title: label });
      const info = new google.maps.InfoWindow();
      marker.addListener("click", () => {
        info.setContent(
          `<strong>${esc(v.label)}</strong> (${esc(v.source_org)})<br>Status: ${esc(v.status)}<br>` +
          `Updated: ${esc((v.updated_at || "").substring(0,19))}<br>` +
          `<a href="https://www.google.com/maps?q=${v.lat},${v.lng}" target="_blank" rel="noopener">Open in Google Maps</a>`
        );
        info.open(vehicleMap, marker);
      });
      vehicleMarkers[key] = marker;
    } else {
      marker.setPosition(position);
      marker.setTitle(label);
    }
  });
  Object.keys(vehicleMarkers).forEach(key => {
    if (!seen.has(key)) {
      vehicleMarkers[key].setMap(null);
      delete vehicleMarkers[key];
    }
  });
}

async function loadVehicles() {
  const data = await (await fetch("/db/vehicle_tracking")).json();
  trackedVehicles = data.rows || [];
  renderVehicleTable();
  syncVehicleMarkers();
}

// Called by the Google Maps JS API script tag once it has loaded (only
// present in the page when CIVILDEFENSE_GOOGLE_MAPS_API_KEY is configured
// server-side).
function initMap() {
  const notice = document.getElementById("mapsKeyNotice");
  if (notice) notice.style.display = "none";
  vehicleMap = new google.maps.Map(document.getElementById("vehicleMap"), {
    center: { lat: 33.8938, lng: 35.5018 }, // Beirut
    zoom: 12,
  });
  syncVehicleMarkers();
}

registerView("vehicles", loadVehicles);
initConsole();
