/**
 * Full-screen interactive Map page controller.
 * Manages transit layers, OSM bus-stop filters, stop search, and click-to-find-nearby.
 */
import { api, escapeHtml, formatDistance, formatDuration, initNavbar } from "./api.js";
import { DhakaMap } from "./map.js";

initNavbar("map");

const dmap = new DhakaMap("full-map", { zoom: 12 });
let stations = [];
let osmStops = [];

const els = {
  toggleMetroTrack: document.getElementById("layer-metro-track"),
  toggleMetroStations: document.getElementById("layer-metro-stations"),
  toggleOsmBus: document.getElementById("layer-osm-bus"),
  osmKind: document.getElementById("osm-kind-filter"),
  searchInput: document.getElementById("map-search"),
  stationList: document.getElementById("station-list"),
  nearbyList: document.getElementById("nearby-results"),
  statusBar: document.getElementById("map-status"),
};

function renderStationList(filter = "") {
  const q = filter.trim().toLowerCase();
  const filtered = stations.filter((s) =>
    !q || s.stop_name.toLowerCase().includes(q) || (s.stop_name_bn && s.stop_name_bn.toLowerCase().includes(q))
  );
  if (!filtered.length) {
    els.stationList.innerHTML = `<li class="muted">No MRT-6 stations match "${escapeHtml(filter)}".</li>`;
    return;
  }
  els.stationList.innerHTML = filtered.map((s) => `
    <li>
      <button type="button" class="list-item-btn" data-stop="${escapeHtml(s.stop_id)}">
        <div style="display:flex;justify-content:space-between;gap:8px;align-items:center;">
          <strong>${escapeHtml(s.stop_name)}</strong>
          <span class="badge badge-metro">MRT-6</span>
        </div>
        ${s.stop_name_bn ? `<div class="muted" style="font-size:0.8rem">${escapeHtml(s.stop_name_bn)}</div>` : ""}
      </button>
    </li>
  `).join("");
  els.stationList.querySelectorAll("button[data-stop]").forEach((btn) => {
    btn.addEventListener("click", () => dmap.focusStop(btn.dataset.stop));
  });
}

els.toggleMetroTrack.addEventListener("change", () => {
  dmap.setLayerVisible("metroTrack", els.toggleMetroTrack.checked);
});
els.toggleMetroStations.addEventListener("change", () => {
  dmap.setLayerVisible("metroStations", els.toggleMetroStations.checked);
});
els.toggleOsmBus.addEventListener("change", async () => {
  if (els.toggleOsmBus.checked && !dmap.osmLoaded) {
    els.statusBar.textContent = "Loading 1,023 mapped OSM bus stops…";
    osmStops = await dmap.loadOsmBusStops({ kind: els.osmKind.value, visible: true });
    els.statusBar.textContent = `Showing ${osmStops.length} mapped OSM bus stops.`;
  } else {
    dmap.setLayerVisible("osmBusStops", els.toggleOsmBus.checked);
  }
});
els.osmKind.addEventListener("change", async () => {
  if (!els.toggleOsmBus.checked) return;
  osmStops = await dmap.loadOsmBusStops({ kind: els.osmKind.value, visible: true });
  els.statusBar.textContent = `Showing ${osmStops.length} OSM stops (${els.osmKind.value || "all kinds"}).`;
});

els.searchInput.addEventListener("input", () => renderStationList(els.searchInput.value));

dmap.map.on("click", async (e) => {
  const { lat, lng } = e.latlng;
  dmap.showNearbySearch(lat, lng, 1200);
  els.nearbyList.innerHTML = `<li class="muted">Searching stops within 1.2 km of ${lat.toFixed(4)}, ${lng.toFixed(4)}…</li>`;
  try {
    const nearby = await api.getNearbyStops(lat, lng, 1200, 6);
    if (!nearby.length) {
      els.nearbyList.innerHTML = `<li class="muted">No served GTFS stations within 1.2 km of this point.</li>`;
      return;
    }
    els.nearbyList.innerHTML = nearby.map((s) => `
      <li class="list-item-btn" style="cursor:default;">
        <div style="display:flex;justify-content:space-between;gap:8px;">
          <strong>${escapeHtml(s.stop_name)}</strong>
          <span class="badge badge-walk">${formatDistance(s.walk_distance_m)} · ${formatDuration(s.walk_time_s)}</span>
        </div>
        <div style="margin-top:6px;display:flex;gap:8px;font-size:0.8rem;">
          <a href="results.html?from_lat=${lat.toFixed(5)}&from_lon=${lng.toFixed(5)}&to_stop=mrt6_motijheel">Plan to Motijheel</a>
          ·
          <a href="results.html?from_stop=${encodeURIComponent(s.stop_id)}&to_stop=mrt6_uttara_north">To Uttara North</a>
        </div>
      </li>
    `).join("");
  } catch (err) {
    els.nearbyList.innerHTML = `<li class="badge badge-error">${escapeHtml(err.message)}</li>`;
  }
});

async function init() {
  try {
    stations = await api.getStops({ location_type: 1, limit: 100 });
    renderStationList();
    await dmap.loadTransitRoutes();
    // Load OSM bus stops right away on the full map explorer
    osmStops = await dmap.loadOsmBusStops({ visible: els.toggleOsmBus.checked });
    els.statusBar.textContent = `Loaded ${stations.length} MRT-6 stations and ${osmStops.length} mapped OSM bus stops. Click anywhere on the map to inspect nearby stops.`;
  } catch (err) {
    els.statusBar.textContent = `API error: ${err.message}`;
  }
}

init();
