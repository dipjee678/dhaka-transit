/**
 * Route Results page controller.
 * Fetches /api/plan + /api/plan/compare, renders recommended & alternative
 * journeys with provenance tags and step-by-step legs, and draws the selected
 * itinerary on the sticky Leaflet map.
 */
import {
  api, escapeHtml, formatClock, formatDistance, formatDuration,
  initNavbar, provenanceBadge, todayIso,
} from "./api.js";
import { DhakaMap } from "./map.js";

initNavbar("results");

const dmap = new DhakaMap("results-map");
const els = {
  fromSelect: document.getElementById("res-from"),
  toSelect: document.getElementById("res-to"),
  dateInput: document.getElementById("res-date"),
  timeInput: document.getElementById("res-time"),
  algSelect: document.getElementById("res-algorithm"),
  form: document.getElementById("replan-form"),
  warnings: document.getElementById("warnings-box"),
  summaryHeader: document.getElementById("results-heading"),
  journeysBox: document.getElementById("journeys-list"),
  compareBox: document.getElementById("compare-box"),
};

let currentPlan = null;
let renderedJourneys = [];

function renderLeg(leg) {
  const isWalk = leg.mode === "walk" || leg.mode === "transfer";
  const badgeClass = leg.mode === "metro" ? "badge-metro" : (leg.mode === "bus" ? "badge-bus" : "badge-walk");
  const modeLabel = leg.mode === "metro"
    ? (leg.route_short_name || "Metro")
    : leg.mode === "bus"
      ? (leg.route_short_name || "Bus")
      : (leg.mode === "transfer" ? "Transfer walk" : "Walk");

  const times = leg.departure_time && leg.arrival_time
    ? `<span>${escapeHtml(formatClock(leg.departure_time))} → ${escapeHtml(formatClock(leg.arrival_time))}</span>`
    : "";

  const waitNote = leg.wait_s > 0
    ? `<span>Est. wait: ${formatDuration(leg.wait_s)}</span> · `
    : "";

  const stopsHtml = (leg.intermediate_stops && leg.intermediate_stops.length > 2)
    ? `<details class="stops-expand">
         <summary>${leg.intermediate_stops.length - 1} stops (${formatDistance(leg.distance_m)})</summary>
         <ol>${leg.intermediate_stops.map((s) => `<li>${escapeHtml(s.stop_name)}</li>`).join("")}</ol>
       </details>`
    : "";

  return `
    <li class="leg-row mode-${escapeHtml(leg.mode)}">
      <div class="leg-rail"><span class="leg-dot"></span><span class="leg-line"></span></div>
      <div class="leg-body">
        <div class="leg-title">
          <span><span class="badge ${badgeClass}">${escapeHtml(modeLabel)}</span> ${escapeHtml(leg.from_name)} → ${escapeHtml(leg.to_name)}</span>
          <span>${formatDuration(leg.duration_s)}</span>
        </div>
        <div class="leg-sub">
          ${waitNote}${isWalk ? formatDistance(leg.distance_m) : (leg.headsign ? `Towards ${escapeHtml(leg.headsign)}` : "")}
          ${times ? ` · ${times}` : ""}
          · ${provenanceBadge(leg.provenance, leg.is_estimated)}
        </div>
        ${stopsHtml}
      </div>
    </li>
  `;
}

function renderJourneyCard(j, index, isSelected) {
  const tag = index === 0 ? "Recommended" : `Alternative ${index}`;
  const algLabel = j.algorithm === "raptor" ? "RAPTOR (scheduled)" : j.algorithm === "dijkstra" ? "Dijkstra (typical)" : "Walk only";
  const fareLabel = j.fare.total_bdt !== null && j.fare.total_bdt !== undefined
    ? `৳${j.fare.total_bdt} ${escapeHtml(j.fare.currency)}`
    : "Fare unavailable";

  const clockSpan = j.departure_time && j.arrival_time
    ? `${formatClock(j.departure_time)} → ${formatClock(j.arrival_time)}`
    : (j.outside_service_hours ? "Outside service hours (typical)" : "Frequency-based typical trip");

  const modeChain = j.legs
    .map((l) => {
      if (l.mode === "metro") return `<span class="badge badge-metro">${escapeHtml(l.route_short_name || "MRT")}</span>`;
      if (l.mode === "bus") return `<span class="badge badge-bus">${escapeHtml(l.route_short_name || "Bus")}</span>`;
      return `<span class="badge badge-walk">${l.mode === "transfer" ? "Transfer" : "Walk"}</span>`;
    })
    .join(" → ");

  return `
    <article class="journey-card ${isSelected ? "selected" : ""}" data-jidx="${index}" tabindex="0" role="button" aria-pressed="${isSelected}">
      <div class="journey-header">
        <div>
          <span class="badge ${index === 0 ? "badge-official" : "badge-walk"}">${tag}</span>
          <span class="badge badge-osm">${escapeHtml(algLabel)}</span>
          ${provenanceBadge(j.provenance, j.is_estimated)}
        </div>
        <div class="journey-duration">${formatDuration(j.total_duration_s)}</div>
      </div>
      <div style="margin-bottom:8px;">${modeChain}</div>
      <div class="journey-meta">
        <span>🕒 ${escapeHtml(clockSpan)}</span>
        <span>🚶 Walk ${formatDistance(j.total_walk_m)} (${formatDuration(j.total_walk_s)})</span>
        <span>🔁 ${j.transfers} transfer${j.transfers === 1 ? "" : "s"}</span>
        <span title="${escapeHtml(j.fare.note)}">💳 ${fareLabel}</span>
      </div>
      <ul class="leg-timeline">${j.legs.map(renderLeg).join("")}</ul>
    </article>
  `;
}

function selectJourney(idx) {
  const j = renderedJourneys[idx];
  if (!j || !currentPlan) return;
  document.querySelectorAll(".journey-card").forEach((c) => {
    const active = Number(c.dataset.jidx) === idx;
    c.classList.toggle("selected", active);
    c.setAttribute("aria-pressed", String(active));
  });
  dmap.renderJourney(j, currentPlan.origin, currentPlan.destination);
}

function renderComparison(cmp) {
  if (!cmp) {
    els.compareBox.innerHTML = "";
    return;
  }
  const r = cmp.raptor || {};
  const d = cmp.dijkstra || {};
  els.compareBox.innerHTML = `
    <div class="card">
      <h2 class="section-title" style="font-size:1rem;">Algorithm Comparison: RAPTOR vs. Dijkstra</h2>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr><th>Metric</th><th>RAPTOR (Timetable)</th><th>Dijkstra (Frequency Graph)</th></tr>
          </thead>
          <tbody>
            <tr><td>Journey found</td><td>${r.found ? `Yes (${r.pareto_count} Pareto option${r.pareto_count === 1 ? "" : "s"})` : "No"}</td><td>${d.found ? (d.outside_service_hours ? "Fallback (off-hours)" : "Yes") : "No"}</td></tr>
            <tr><td>Total time</td><td>${r.found ? formatDuration(r.total_duration_s) : "—"}</td><td>${d.found ? formatDuration(d.total_duration_s) : "—"}</td></tr>
            <tr><td>Transfers</td><td>${r.found ? r.transfers : "—"}</td><td>${d.found ? d.transfers : "—"}</td></tr>
            <tr><td>Generalized cost</td><td>${r.found ? r.generalized_cost : "—"}</td><td>${d.found ? d.generalized_cost : "—"}</td></tr>
            <tr><td>Compute time</td><td>${r.found ? `${r.computation_ms} ms` : "—"}</td><td>${d.found ? `${d.computation_ms} ms` : "—"}</td></tr>
          </tbody>
        </table>
      </div>
      <p class="muted" style="margin:10px 0 0;font-size:0.82rem;">
        Route agreement: <strong>${cmp.same_best_route ? "Both algorithms chose the same physical transit route." : "Algorithms differed (expected when timetable waiting differs from average headway or off-hours)."}</strong>
      </p>
    </div>
  `;
}

async function runPlan(params) {
  els.journeysBox.innerHTML = `<div class="card muted">Calculating multimodal route with RAPTOR &amp; Dijkstra…</div>`;
  els.warnings.hidden = true;
  try {
    const plan = await api.planRoute(params);
    currentPlan = plan;

    els.summaryHeader.textContent = `${plan.origin.name} → ${plan.destination.name}`;
    document.getElementById("results-sub").textContent =
      `Service date: ${plan.service_date}`
      + (plan.departure_time ? ` · Departure: ${formatClock(plan.departure_time)}` : " · Untimed typical query")
      + ` · Engine: ${plan.algorithm_requested}`;

    if (plan.warnings && plan.warnings.length) {
      els.warnings.innerHTML = `<strong>Data transparency notes:</strong><ul>${plan.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("")}</ul>`;
      els.warnings.hidden = false;
    }

    renderedJourneys = [
      ...(plan.recommended ? [plan.recommended] : []),
      ...(plan.alternatives || []),
    ];

    if (!renderedJourneys.length) {
      els.journeysBox.innerHTML = `<div class="card">No transit or walking route found within the walking radius. Try increasing the walk limit or picking a location closer to MRT Line 6.</div>`;
    } else {
      els.journeysBox.innerHTML = renderedJourneys.map((j, idx) => renderJourneyCard(j, idx, idx === 0)).join("");
      document.querySelectorAll(".journey-card").forEach((card) => {
        const handler = () => selectJourney(Number(card.dataset.jidx));
        card.addEventListener("click", handler);
        card.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); handler(); }
        });
      });
      selectJourney(0);
    }
    renderComparison(plan.comparison);
  } catch (err) {
    els.journeysBox.innerHTML = `<div class="card"><span class="badge badge-error">Error</span> ${escapeHtml(err.message)}</div>`;
  }
}

async function init() {
  const sp = new URLSearchParams(window.location.search);
  const stations = await api.getStops({ location_type: 1, limit: 100 });
  const optionsHtml = stations
    .map((s) => `<option value="${escapeHtml(s.stop_id)}">${escapeHtml(s.stop_name)} (${escapeHtml(s.stop_name_bn)})</option>`)
    .join("");
  els.fromSelect.innerHTML = optionsHtml;
  els.toSelect.innerHTML = optionsHtml;

  const params = {
    from_stop: sp.get("from_stop") || (!sp.get("from_lat") ? "mrt6_mirpur_10" : ""),
    from_lat: sp.get("from_lat") || "",
    from_lon: sp.get("from_lon") || "",
    from_name: sp.get("from_name") || "",
    to_stop: sp.get("to_stop") || (!sp.get("to_lat") ? "mrt6_motijheel" : ""),
    to_lat: sp.get("to_lat") || "",
    to_lon: sp.get("to_lon") || "",
    to_name: sp.get("to_name") || "",
    date: sp.get("date") || todayIso(),
    time: sp.get("time") || "08:30",
    algorithm: sp.get("algorithm") || "combined",
    modes: sp.get("modes") || "",
  };

  if (params.from_stop) els.fromSelect.value = params.from_stop;
  if (params.to_stop) els.toSelect.value = params.to_stop;
  els.dateInput.value = params.date;
  els.timeInput.value = params.time;
  els.algSelect.value = params.algorithm;

  els.form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const next = new URLSearchParams({
      from_stop: els.fromSelect.value,
      to_stop: els.toSelect.value,
      date: els.dateInput.value,
      time: els.timeInput.value,
      algorithm: els.algSelect.value,
    });
    window.history.replaceState({}, "", `?${next.toString()}`);
    runPlan(Object.fromEntries(next.entries()));
  });

  await dmap.loadTransitRoutes();
  await runPlan(params);
}

init();
