/**
 * Dhaka Transit REST API client & shared formatting helpers.
 * Auto-detects same-origin when served from FastAPI, or falls back to
 * http://127.0.0.1:8000 when opened directly via file:// or a static host.
 */
const urlApi = new URLSearchParams(window.location.search).get("api");
export const API_BASE = (
  window.DHAKA_API_BASE
  || urlApi
  || (window.location.protocol === "file:" ? "http://127.0.0.1:8000" : "")
).replace(/\/$/, "");

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body);
    } catch { /* ignore non-JSON body */ }
    throw new Error(detail);
  }
  return res.json();
}

function qs(params) {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export const api = {
  getHealth: () => request("/api/health"),
  getStops: (params) => request(`/api/stops${qs(params)}`),
  getStop: (stopId) => request(`/api/stops/${encodeURIComponent(stopId)}`),
  getNearbyStops: (lat, lon, radius_m = 800, limit = 8) =>
    request(`/api/stops/nearby${qs({ lat, lon, radius_m, limit })}`),
  getOsmBusStops: (params) => request(`/api/stops/osm-inventory${qs(params)}`),
  getRoutes: (params) => request(`/api/routes${qs(params)}`),
  getRouteDetail: (routeId) => request(`/api/routes/${encodeURIComponent(routeId)}`),
  getRouteGeoJson: (routeId) => request(`/api/routes/${encodeURIComponent(routeId)}/geojson`),
  planRoute: (params) => request(`/api/plan${qs(params)}`),
  compareRoute: (params) => request(`/api/plan/compare${qs(params)}`),
  getDataset: () => request("/api/dataset"),
  getValidation: () => request("/api/dataset/validation"),
  getGaps: () => request("/api/dataset/gaps"),
  getContributions: (status) => request(`/api/contributions${qs({ status })}`),
  submitContribution: (body) =>
    request("/api/contributions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  downloadZipUrl: () => `${API_BASE}/api/dataset/download`,
  downloadFileUrl: (name) => `${API_BASE}/api/dataset/files/${encodeURIComponent(name)}`,
};

export function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function formatDuration(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  const mins = Math.round(s / 60);
  if (mins < 1) return `${s} sec`;
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  const rem = mins % 60;
  return rem ? `${h} hr ${rem} min` : `${h} hr`;
}

export function formatDistance(metres) {
  const m = Math.max(0, Number(metres || 0));
  return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`;
}

export function formatClock(hhmmss) {
  if (!hhmmss) return "";
  const [h, m] = hhmmss.split(":").map(Number);
  if (Number.isNaN(h) || Number.isNaN(m)) return hhmmss;
  const h24 = h % 24;
  const suffix = h24 >= 12 ? "PM" : "AM";
  const h12 = h24 % 12 || 12;
  return `${h12}:${String(m).padStart(2, "0")} ${suffix}`;
}

export function provenanceBadge(category, isEstimated = false) {
  const cat = isEstimated ? "estimated" : (category || "estimated");
  const labels = {
    official: "Official",
    osm: "OpenStreetMap",
    community: "Community",
    estimated: "Estimated",
  };
  return `<span class="badge badge-${escapeHtml(cat)}">${escapeHtml(labels[cat] || cat)}</span>`;
}

export function initNavbar(activeKey) {
  const toggle = document.getElementById("nav-toggle");
  const links = document.getElementById("nav-links");
  if (toggle && links) {
    toggle.addEventListener("click", () => {
      const open = links.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(open));
    });
  }
  document.querySelectorAll("#nav-links a[data-page]").forEach((a) => {
    if (a.dataset.page === activeKey) a.classList.add("active");
  });
}

export function todayIso() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
