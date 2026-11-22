/**
 * Open Dataset & Community Contributions page controller.
 */
import { api, escapeHtml, initNavbar, provenanceBadge } from "./api.js";

initNavbar("dataset");

const els = {
  zipBtn: document.getElementById("download-zip-btn"),
  filesBody: document.getElementById("gtfs-files-body"),
  sourcesBody: document.getElementById("sources-body"),
  gapsBody: document.getElementById("gaps-interchange-body"),
  validationBox: document.getElementById("validation-summary"),
  contribForm: document.getElementById("contrib-form"),
  contribMsg: document.getElementById("contrib-msg"),
  contribList: document.getElementById("contrib-list"),
};

els.zipBtn.href = api.downloadZipUrl();

function renderContributions(items) {
  if (!items.length) {
    els.contribList.innerHTML = `<tr><td colspan="5" class="muted">No community submissions yet. Be the first to contribute a bus stop or route!</td></tr>`;
    return;
  }
  els.contribList.innerHTML = items.map((c) => `
    <tr>
      <td>#${c.contribution_id}</td>
      <td><strong>${escapeHtml(c.contributor_name)}</strong></td>
      <td><span class="badge badge-community">${escapeHtml(c.kind)}</span></td>
      <td><code>${escapeHtml(JSON.stringify(c.payload))}</code></td>
      <td><span class="badge badge-estimated">${escapeHtml(c.status)}</span></td>
    </tr>
  `).join("");
}

els.contribForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  els.contribMsg.hidden = true;
  const fd = new FormData(els.contribForm);
  const payload = {
    name: String(fd.get("item_name") || "").trim(),
    route_or_operator: String(fd.get("route_info") || "").trim(),
    notes: String(fd.get("notes") || "").trim(),
  };
  const latStr = String(fd.get("lat") || "").trim();
  const lonStr = String(fd.get("lon") || "").trim();
  if (latStr || lonStr) {
    payload.lat = parseFloat(latStr);
    payload.lon = parseFloat(lonStr);
  }
  try {
    await api.submitContribution({
      contributor_name: String(fd.get("contributor_name") || "").trim(),
      contact: String(fd.get("contact") || "").trim(),
      kind: String(fd.get("kind")),
      payload,
    });
    els.contribForm.reset();
    els.contribMsg.className = "badge badge-official";
    els.contribMsg.textContent = "Submitted! Saved as pending review.";
    els.contribMsg.hidden = false;
    renderContributions(await api.getContributions());
  } catch (err) {
    els.contribMsg.className = "badge badge-error";
    els.contribMsg.textContent = `Submission failed: ${err.message}`;
    els.contribMsg.hidden = false;
  }
});

async function init() {
  try {
    const [ds, gaps, contribs] = await Promise.all([
      api.getDataset(),
      api.getGaps().catch(() => null),
      api.getContributions().catch(() => []),
    ]);

    document.getElementById("ds-version").textContent = ds.feed_version;
    document.getElementById("ds-stations").textContent = ds.counts.stations;
    document.getElementById("ds-osm-stops").textContent = ds.counts.osm_bus_stop_inventory.toLocaleString();
    document.getElementById("ds-errors").textContent = ds.validation_summary ? ds.validation_summary.errors : "—";
    document.getElementById("ds-attribution").textContent = ds.attribution;

    els.filesBody.innerHTML = ds.files.map((f) => `
      <tr>
        <td><code>${escapeHtml(f.filename)}</code></td>
        <td>${f.rows.toLocaleString()}</td>
        <td>${(f.size_bytes / 1024).toFixed(1)} KB</td>
        <td><a class="btn btn-sm btn-secondary" href="${api.downloadFileUrl(f.filename)}" download>Download</a></td>
      </tr>
    `).join("");

    els.sourcesBody.innerHTML = ds.data_sources.map((s) => `
      <tr>
        <td><code>${escapeHtml(s.source_id)}</code></td>
        <td>${s.url ? `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.name)}</a>` : escapeHtml(s.name)}</td>
        <td>${provenanceBadge(s.category)}</td>
        <td>${escapeHtml(s.license || "—")}</td>
        <td class="muted">${escapeHtml(s.notes || "")}</td>
      </tr>
    `).join("");

    const v = ds.validation_summary;
    if (v) {
      els.validationBox.innerHTML = `
        <span class="badge ${v.errors === 0 ? "badge-official" : "badge-error"}">Errors: ${v.errors}</span>
        <span class="badge ${v.warnings === 0 ? "badge-official" : "badge-estimated"}">Warnings: ${v.warnings}</span>
        <span class="badge badge-walk">Info: ${v.infos}</span>
        <span class="muted" style="margin-left:8px;">Provenance mix: ${Object.entries(ds.provenance_by_category).map(([k, n]) => `${k}: ${n}`).join(" · ")}</span>
      `;
    }

    if (gaps && gaps.mrt6_interchanges) {
      const sorted = [...gaps.mrt6_interchanges].sort((a, b) => b.bus_stops_500m - a.bus_stops_500m).slice(0, 8);
      els.gapsBody.innerHTML = sorted.map((r) => `
        <tr>
          <td><strong>${escapeHtml(r.name_en)}</strong></td>
          <td>${r.bus_stops_250m}</td>
          <td><strong>${r.bus_stops_500m}</strong></td>
          <td class="muted">${escapeHtml((r.closest_named || []).join("; ") || "None mapped in OSM")}</td>
        </tr>
      `).join("");
    }

    renderContributions(contribs);
  } catch (err) {
    els.validationBox.innerHTML = `<span class="badge badge-error">Could not load dataset metadata: ${escapeHtml(err.message)}</span>`;
  }
}

init();
