/**
 * Home page controller: bilingual stop autocomplete, map-click coordinate
 * picker, preset journeys, live network stats, and navigation to results.html.
 */
import { api, escapeHtml, initNavbar, todayIso } from "./api.js";
import { DhakaMap } from "./map.js";

initNavbar("home");

let allPlaces = [];
let pickMode = null; // 'from' | 'to' | null
const state = {
  from: null, // { stop_id?, name, lat, lon }
  to: null,
};

const els = {
  fromInput: document.getElementById("from-input"),
  toInput: document.getElementById("to-input"),
  fromList: document.getElementById("from-list"),
  toList: document.getElementById("to-list"),
  dateInput: document.getElementById("date-input"),
  timeInput: document.getElementById("time-input"),
  algSelect: document.getElementById("algorithm-select"),
  pickHint: document.getElementById("pick-hint"),
  formError: document.getElementById("form-error"),
  form: document.getElementById("search-form"),
};

els.dateInput.value = todayIso();

const dmap = new DhakaMap("home-map");

function setChoice(which, place) {
  state[which] = place;
  const input = which === "from" ? els.fromInput : els.toInput;
  input.value = place.name;
  dmap.setEndpoint(which, place.lat, place.lon, place.name);
  els.formError.hidden = true;
}

function parseCoords(text) {
  const m = text.trim().match(/^(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)$/);
  if (!m) return null;
  return { lat: parseFloat(m[1]), lon: parseFloat(m[2]), name: `${m[1]}, ${m[2]}` };
}

function resolveText(which) {
  if (state[which]) return state[which];
  const txt = (which === "from" ? els.fromInput : els.toInput).value.trim();
  if (!txt) return null;
  const coords = parseCoords(txt);
  if (coords) return coords;
  const needle = txt.toLowerCase();
  const exact = allPlaces.find((p) =>
    p.name.toLowerCase() === needle || (p.name_bn && p.name_bn.toLowerCase() === needle)
  );
  if (exact) return exact;
  return allPlaces.find((p) =>
    p.name.toLowerCase().includes(needle) || (p.name_bn && p.name_bn.toLowerCase().includes(needle))
  ) || null;
}

function setupAutocomplete(which, input, listEl) {
  function render(query) {
    const q = query.trim().toLowerCase();
    const matches = allPlaces
      .filter((p) => !q || p.name.toLowerCase().includes(q) || (p.name_bn && p.name_bn.toLowerCase().includes(q)))
      .slice(0, 12);
    if (!matches.length) {
      listEl.classList.remove("open");
      return;
    }
    listEl.innerHTML = matches.map((p) => `
      <li class="autocomplete-item" role="option" data-id="${escapeHtml(p.id)}">
        <span><strong>${escapeHtml(p.name)}</strong> ${p.name_bn ? `<span class="autocomplete-bn">${escapeHtml(p.name_bn)}</span>` : ""}</span>
        <span class="badge ${p.kind === "metro" ? "badge-metro" : "badge-osm"}">${escapeHtml(p.tag)}</span>
      </li>
    `).join("");
    listEl.classList.add("open");
    listEl.querySelectorAll("li").forEach((li) => {
      li.addEventListener("mousedown", (ev) => {
        ev.preventDefault();
        const found = allPlaces.find((x) => x.id === li.dataset.id);
        if (found) setChoice(which, found);
        listEl.classList.remove("open");
      });
    });
  }

  input.addEventListener("focus", () => render(input.value));
  input.addEventListener("input", () => {
    state[which] = null;
    render(input.value);
  });
  input.addEventListener("blur", () => setTimeout(() => listEl.classList.remove("open"), 140));
}

setupAutocomplete("from", els.fromInput, els.fromList);
setupAutocomplete("to", els.toInput, els.toList);

document.getElementById("swap-btn").addEventListener("click", () => {
  const prevFrom = state.from;
  const prevTo = state.to;
  const prevFromTxt = els.fromInput.value;
  const prevToTxt = els.toInput.value;
  state.from = prevTo;
  state.to = prevFrom;
  els.fromInput.value = prevToTxt;
  els.toInput.value = prevFromTxt;
  if (state.from) dmap.setEndpoint("from", state.from.lat, state.from.lon, state.from.name);
  if (state.to) dmap.setEndpoint("to", state.to.lat, state.to.lon, state.to.name);
});

document.querySelectorAll("button[data-pick-map]").forEach((btn) => {
  btn.addEventListener("click", () => {
    pickMode = btn.dataset.pickMap;
    els.pickHint.hidden = false;
    els.pickHint.textContent = `Click anywhere on the Dhaka map to set ${pickMode === "from" ? "Origin (A)" : "Destination (B)"}.`;
  });
});

dmap.map.on("click", (e) => {
  if (!pickMode) return;
  const { lat, lng } = e.latlng;
  setChoice(pickMode, {
    id: `coord_${lat.toFixed(5)}_${lng.toFixed(5)}`,
    name: `${lat.toFixed(5)}, ${lng.toFixed(5)}`,
    lat,
    lon: lng,
  });
  pickMode = null;
  els.pickHint.hidden = true;
});

document.querySelectorAll(".chip[data-from]").forEach((chip) => {
  chip.addEventListener("click", () => {
    const f = allPlaces.find((p) => p.stop_id === chip.dataset.from);
    const t = allPlaces.find((p) => p.stop_id === chip.dataset.to);
    if (f) setChoice("from", f);
    if (t) setChoice("to", t);
  });
});

els.form.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const origin = resolveText("from");
  const dest = resolveText("to");
  if (!origin || !dest) {
    els.formError.textContent = "Please select or enter both a starting location and destination (station name or lat, lon).";
    els.formError.hidden = false;
    return;
  }
  const sp = new URLSearchParams();
  if (origin.stop_id) sp.set("from_stop", origin.stop_id);
  else {
    sp.set("from_lat", origin.lat.toFixed(6));
    sp.set("from_lon", origin.lon.toFixed(6));
    sp.set("from_name", origin.name);
  }
  if (dest.stop_id) sp.set("to_stop", dest.stop_id);
  else {
    sp.set("to_lat", dest.lat.toFixed(6));
    sp.set("to_lon", dest.lon.toFixed(6));
    sp.set("to_name", dest.name);
  }
  if (els.dateInput.value) sp.set("date", els.dateInput.value);
  if (els.timeInput.value) sp.set("time", els.timeInput.value);
  sp.set("algorithm", els.algSelect.value);

  const modes = Array.from(document.querySelectorAll('input[name="mode"]:checked')).map((c) => c.value);
  if (modes.length && modes.length < 2) sp.set("modes", modes.join(","));

  window.location.href = `results.html?${sp.toString()}`;
});

async function bootstrap() {
  try {
    const [health, stations, osmBus] = await Promise.all([
      api.getHealth(),
      api.getStops({ location_type: 1, limit: 100 }),
      api.getOsmBusStops({ named_only: true, limit: 400 }),
    ]);

    document.getElementById("stat-stations").textContent = health.stations;
    document.getElementById("stat-routes").textContent = health.routes;
    document.getElementById("stat-osm").textContent = health.osm_bus_stop_inventory.toLocaleString();
    document.getElementById("stat-nodes").textContent = health.graph.nodes;

    allPlaces = [
      ...stations.map((s) => ({
        id: s.stop_id,
        stop_id: s.stop_id,
        name: s.stop_name,
        name_bn: s.stop_name_bn,
        lat: s.stop_lat,
        lon: s.stop_lon,
        kind: "metro",
        tag: "MRT-6",
      })),
      ...osmBus.map((b) => ({
        id: b.osm_id,
        name: b.name,
        name_bn: b.name_bn,
        lat: b.lat,
        lon: b.lon,
        kind: "osm",
        tag: "OSM stop",
      })),
    ];

    await dmap.loadTransitRoutes({
      onPickStop: (which, stop) => {
        const match = allPlaces.find((p) => p.stop_id === stop.stop_id) || {
          id: stop.stop_id, stop_id: stop.stop_id, name: stop.stop_name, lat: stop.lat, lon: stop.lon,
        };
        setChoice(which, match);
      },
    });
  } catch (err) {
    els.formError.textContent = `Could not reach Dhaka Transit API (${err.message}). Start the FastAPI backend on port 8000.`;
    els.formError.hidden = false;
  }
}

bootstrap();
