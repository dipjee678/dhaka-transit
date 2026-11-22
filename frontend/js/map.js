/**
 * Reusable Leaflet map module for Dhaka Transit.
 * Renders MRT Line 6, GTFS bus routes, 1,023 OSM bus stops (via Canvas for
 * smooth 60fps pan/zoom), user markers, and multimodal journey polylines.
 */
import { api, escapeHtml, formatDistance, formatDuration, provenanceBadge } from "./api.js";

export const DHAKA_CENTER = [23.792, 90.386];
export const DHAKA_ZOOM = 12;

export class DhakaMap {
  constructor(elementId, { center = DHAKA_CENTER, zoom = DHAKA_ZOOM } = {}) {
    this.map = L.map(elementId, { zoomControl: true }).setView(center, zoom);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(this.map);

    this.canvasRenderer = L.canvas({ padding: 0.5 });
    this.layers = {
      metroTrack: L.layerGroup().addTo(this.map),
      metroStations: L.layerGroup().addTo(this.map),
      busRoutes: L.layerGroup().addTo(this.map),
      osmBusStops: L.layerGroup(), // toggled on when requested
      journey: L.layerGroup().addTo(this.map),
      markers: L.layerGroup().addTo(this.map),
      nearby: L.layerGroup().addTo(this.map),
    };
    this.originMarker = null;
    this.destMarker = null;
    this.osmLoaded = false;
    this.stationMarkers = new Map();
  }

  async loadTransitRoutes({ onPickStop = null } = {}) {
    this.layers.metroTrack.clearLayers();
    this.layers.metroStations.clearLayers();
    this.layers.busRoutes.clearLayers();
    this.stationMarkers.clear();

    const routes = await api.getRoutes();
    for (const r of routes) {
      const gj = await api.getRouteGeoJson(r.route_id);
      const color = r.route_color ? `#${r.route_color}` : (r.mode === "metro" ? "#006E34" : "#D97706");
      const trackGroup = r.mode === "metro" ? this.layers.metroTrack : this.layers.busRoutes;
      const stopGroup = r.mode === "metro" ? this.layers.metroStations : this.layers.busRoutes;

      // Draw one clean track line (direction 0) so overlapping tracks stay crisp
      let lineDrawn = false;
      for (const f of gj.features) {
        if (f.geometry.type === "LineString") {
          if (r.mode === "metro" && lineDrawn) continue;
          lineDrawn = true;
          const latlngs = f.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
          L.polyline(latlngs, { color: "#FFFFFF", weight: 8, opacity: 0.85 }).addTo(trackGroup);
          L.polyline(latlngs, { color, weight: 5, opacity: 0.95 })
            .bindPopup(
              `<strong>${escapeHtml(r.route_short_name)}</strong> — ${escapeHtml(r.route_long_name)}<br>`
              + `${r.route_long_name_bn ? escapeHtml(r.route_long_name_bn) + "<br>" : ""}`
              + `${provenanceBadge(r.provenance?.category)}`
            )
            .addTo(trackGroup);
        } else if (f.geometry.type === "Point") {
          const [lon, lat] = f.geometry.coordinates;
          const p = f.properties;
          const marker = L.circleMarker([lat, lon], {
            radius: r.mode === "metro" ? 6.5 : 5,
            color,
            weight: 2.5,
            fillColor: "#FFFFFF",
            fillOpacity: 1,
          }).addTo(stopGroup);

          const actions = onPickStop
            ? `<div style="margin-top:8px;display:flex;gap:6px;">
                 <button type="button" class="btn btn-sm btn-primary" data-pick="from" data-id="${escapeHtml(p.stop_id)}">From here</button>
                 <button type="button" class="btn btn-sm btn-secondary" data-pick="to" data-id="${escapeHtml(p.stop_id)}">To here</button>
               </div>`
            : `<div style="margin-top:6px;"><a href="results.html?from_stop=${encodeURIComponent(p.stop_id)}&to_stop=mrt6_motijheel">Plan from here →</a></div>`;

          marker.bindPopup(
            `<div><strong>${escapeHtml(p.stop_name)}</strong>`
            + `${p.stop_name_bn ? `<br><span style="color:#475569">${escapeHtml(p.stop_name_bn)}</span>` : ""}`
            + `<div style="margin-top:4px;"><span class="badge badge-${r.mode === "metro" ? "metro" : "bus"}">${escapeHtml(r.route_short_name)}</span> `
            + `${provenanceBadge("osm")}</div>${actions}</div>`
          );

          if (onPickStop) {
            marker.on("popupopen", (ev) => {
              const el = ev.popup.getElement();
              el?.querySelectorAll("button[data-pick]").forEach((btn) => {
                btn.addEventListener("click", () => {
                  onPickStop(btn.dataset.pick, { stop_id: p.stop_id, stop_name: p.stop_name, lat, lon });
                  marker.closePopup();
                });
              });
            });
          }
          this.stationMarkers.set(p.stop_id, { marker, lat, lon, props: p });
        }
      }
    }
    return routes;
  }

  async loadOsmBusStops({ kind = "", namedOnly = false, visible = true } = {}) {
    this.layers.osmBusStops.clearLayers();
    const stops = await api.getOsmBusStops({ kind, named_only: namedOnly, limit: 1500 });
    for (const s of stops) {
      const m = L.circleMarker([s.lat, s.lon], {
        renderer: this.canvasRenderer,
        radius: s.kind === "bus_station" ? 5 : 3.5,
        color: "#B45309",
        weight: 1.2,
        fillColor: "#F59E0B",
        fillOpacity: 0.85,
      });
      m.bindPopup(
        `<strong>${escapeHtml(s.name || "Unnamed OSM bus stop")}</strong>`
        + `${s.name_bn ? `<br><span style="color:#475569">${escapeHtml(s.name_bn)}</span>` : ""}`
        + `<div style="margin-top:4px;"><span class="badge badge-osm">OSM ${escapeHtml(s.kind)}</span> `
        + `<span class="badge badge-estimated">Route unmapped</span></div>`
        + `<div style="margin-top:4px;font-size:11px;color:#64748B;">OSM ID: ${escapeHtml(s.osm_id)} · ${s.lat.toFixed(5)}, ${s.lon.toFixed(5)}</div>`
      );
      m.addTo(this.layers.osmBusStops);
    }
    this.osmLoaded = true;
    if (visible && !this.map.hasLayer(this.layers.osmBusStops)) {
      this.layers.osmBusStops.addTo(this.map);
    }
    return stops;
  }

  setLayerVisible(key, visible) {
    const layer = this.layers[key];
    if (!layer) return;
    if (visible && !this.map.hasLayer(layer)) layer.addTo(this.map);
    if (!visible && this.map.hasLayer(layer)) this.map.removeLayer(layer);
  }

  _pinIcon(label, bg) {
    return L.divIcon({
      className: "custom-pin",
      html: `<div style="width:28px;height:28px;border-radius:50% 50% 50% 0;transform:rotate(-45deg);background:${bg};border:2.5px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.35);display:grid;place-items:center;"><span style="transform:rotate(45deg);color:#fff;font-weight:800;font-size:12px;">${escapeHtml(label)}</span></div>`,
      iconSize: [28, 28],
      iconAnchor: [14, 28],
      popupAnchor: [0, -28],
    });
  }

  setEndpoint(which, lat, lon, label = "") {
    const isOrigin = which === "from";
    const key = isOrigin ? "originMarker" : "destMarker";
    if (this[key]) this.layers.markers.removeLayer(this[key]);
    const m = L.marker([lat, lon], {
      icon: this._pinIcon(isOrigin ? "A" : "B", isOrigin ? "#006E34" : "#DC2626"),
    })
      .bindPopup(`<strong>${isOrigin ? "Origin" : "Destination"}:</strong> ${escapeHtml(label || `${lat.toFixed(5)}, ${lon.toFixed(5)}`)}`)
      .addTo(this.layers.markers);
    this[key] = m;
    return m;
  }

  renderJourney(journey, origin, destination) {
    this.layers.journey.clearLayers();
    this.layers.markers.clearLayers();
    const bounds = [];

    if (origin) {
      this.setEndpoint("from", origin.lat, origin.lon, origin.name);
      bounds.push([origin.lat, origin.lon]);
    }
    if (destination) {
      this.setEndpoint("to", destination.lat, destination.lon, destination.name);
      bounds.push([destination.lat, destination.lon]);
    }

    for (const leg of journey.legs || []) {
      const coords = (leg.geometry && leg.geometry.length >= 2)
        ? leg.geometry
        : [[leg.from_lat, leg.from_lon], [leg.to_lat, leg.to_lon]];
      coords.forEach((c) => bounds.push(c));

      const isWalk = leg.mode === "walk" || leg.mode === "transfer";
      const color = isWalk
        ? "#475569"
        : (leg.route_color ? `#${leg.route_color}` : (leg.mode === "metro" ? "#006E34" : "#D97706"));

      if (isWalk) {
        L.polyline(coords, { color, weight: 4.5, dashArray: "6 8", opacity: 0.9 })
          .bindPopup(`<strong>${leg.mode === "transfer" ? "Transfer walk" : "Walk"}</strong><br>${formatDistance(leg.distance_m)} · ${formatDuration(leg.duration_s)}`)
          .addTo(this.layers.journey);
      } else {
        L.polyline(coords, { color: "#FFFFFF", weight: 9, opacity: 0.9 }).addTo(this.layers.journey);
        L.polyline(coords, { color, weight: 5.5, opacity: 1 })
          .bindPopup(`<strong>${escapeHtml(leg.route_short_name || leg.mode)}</strong><br>${escapeHtml(leg.from_name)} → ${escapeHtml(leg.to_name)}<br>${formatDuration(leg.duration_s)}`)
          .addTo(this.layers.journey);

        for (const st of leg.intermediate_stops || []) {
          L.circleMarker([st.lat, st.lon], {
            radius: 5.5, color, weight: 2.5, fillColor: "#FFFFFF", fillOpacity: 1,
          })
            .bindPopup(`<strong>${escapeHtml(st.stop_name)}</strong>`)
            .addTo(this.layers.journey);
        }
      }
    }

    if (bounds.length >= 2) {
      this.map.fitBounds(bounds, { padding: [44, 44], maxZoom: 15 });
    }
  }

  showNearbySearch(lat, lon, radiusM) {
    this.layers.nearby.clearLayers();
    L.circle([lat, lon], {
      radius: radiusM, color: "#0284C7", weight: 1.5, fillColor: "#0284C7", fillOpacity: 0.08,
    }).addTo(this.layers.nearby);
    L.circleMarker([lat, lon], {
      radius: 6, color: "#0284C7", fillColor: "#FFFFFF", fillOpacity: 1, weight: 2.5,
    }).addTo(this.layers.nearby);
  }

  focusStop(stopId) {
    const entry = this.stationMarkers.get(stopId);
    if (!entry) return;
    this.map.setView([entry.lat, entry.lon], 15);
    entry.marker.openPopup();
  }
}
