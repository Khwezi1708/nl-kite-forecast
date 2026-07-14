(function () {
  const bootstrap = window.MAP_BOOTSTRAP;
  const regionSelect = document.getElementById("region-select");
  const regionTitle = document.getElementById("region-title");
  const regionVerdict = document.getElementById("region-verdict");
  const regionSpotList = document.getElementById("region-spot-list");
  const regionSummary = document.getElementById("region-summary");
  const spotDetail = document.getElementById("spot-detail");
  const spotBack = document.getElementById("spot-back");

  let currentRegion = "all";
  let selectedSpotId = null;
  let markersById = {};

  const map = L.map("map", { scrollWheelZoom: true }).setView([52.2, 5.3], 7);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);

  function badgeClass(status) {
    if (status === "good") return "good";
    if (status === "marginal") return "marginal";
    return "no-go";
  }

  function statusLabel(status) {
    if (status === "good") return "Go";
    if (status === "marginal") return "Maybe";
    return "No-go";
  }

  function formatList(values) {
    if (!values || values.length === 0) return "—";
    return values.join(", ");
  }

  function spotInfoHtml(spot) {
    return `
      <div class="spot-info-row"><dt>Waterdiepte</dt><dd>${formatList(spot.waterdiepte)}</dd></div>
      <div class="spot-info-row"><dt>Niveau</dt><dd>${formatList(spot.niveau)}</dd></div>
      <div class="spot-info-row"><dt>Windrichtingen</dt><dd>${formatList(spot.windrichtingen)}</dd></div>
      <div class="spot-info-row"><dt>Openstelling</dt><dd>${spot.openstelling || "—"}</dd></div>
    `;
  }

  function markerIcon(status, selected) {
    return L.divIcon({
      className: "",
      html: `<div class="spot-marker ${status}${selected ? " selected" : ""}"></div>`,
      iconSize: [16, 16],
      iconAnchor: [8, 8],
    });
  }

  function filteredSpots() {
    if (currentRegion === "all") return bootstrap.spots;
    return bootstrap.spots.filter((spot) => spot.region === currentRegion);
  }

  function regionMeta() {
    if (currentRegion === "all") {
      const good = bootstrap.spots.filter((s) => s.status === "good").length;
      const marginal = bootstrap.spots.filter((s) => s.status === "marginal").length;
      const nogo = bootstrap.spots.length - good - marginal;
      return {
        name: "All Netherlands",
        recommendation:
          good > 0
            ? `Go — ${good} spot(s) with viable days in the next ${bootstrap.forecast_days} days`
            : marginal > 0
              ? `Maybe — only marginal conditions (${marginal} spots)`
              : "No-go — no viable days at any spot",
        top_spots: [...bootstrap.spots]
          .sort((a, b) => {
            const rank = { good: 0, marginal: 1, nogo: 2 };
            return rank[a.status] - rank[b.status] || b.good_days - a.good_days;
          })
          .slice(0, 5)
          .map((spot) => ({
            id: spot.id,
            name: spot.name,
            status: spot.status,
            good_days: spot.good_days,
            verdict: spot.verdict,
          })),
        spot_count: bootstrap.spots.length,
        good_spot_count: good,
        marginal_spot_count: marginal,
        nogo_spot_count: nogo,
      };
    }
    return bootstrap.regions.find((region) => region.id === currentRegion) || null;
  }

  function renderRegionSummary() {
    const meta = regionMeta();
    if (!meta) return;

    regionTitle.textContent = meta.name;
    regionVerdict.textContent = meta.recommendation;
    regionSpotList.innerHTML = "";

    (meta.top_spots || []).forEach((spot) => {
      const li = document.createElement("li");
      li.dataset.spotId = spot.id;
      li.innerHTML = `
        <span class="spot-name">${spot.name}</span>
        <span class="spot-days badge badge-${badgeClass(spot.status)}">${statusLabel(spot.status)}${spot.good_days ? ` · ${spot.good_days}d` : ""}</span>
      `;
      li.addEventListener("click", () => selectSpot(spot.id));
      regionSpotList.appendChild(li);
    });

    if (!meta.top_spots || meta.top_spots.length === 0) {
      const li = document.createElement("li");
      li.textContent = "No spots in this region.";
      regionSpotList.appendChild(li);
    }
  }

  function renderMarkers() {
    Object.values(markersById).forEach((marker) => map.removeLayer(marker));
    markersById = {};

    const spots = filteredSpots();
    spots.forEach((spot) => {
      const marker = L.marker([spot.lat, spot.lon], {
        icon: markerIcon(spot.status, spot.id === selectedSpotId),
      });
      marker.bindPopup(
        `<div class="popup-title">${spot.name}</div>
         <div class="popup-info">${formatList(spot.waterdiepte)} · ${formatList(spot.niveau)}</div>
         <div class="popup-info">Wind: ${formatList(spot.windrichtingen)}</div>
         <div class="popup-status">${statusLabel(spot.status)} — ${spot.good_days} good / ${spot.marginal_days} maybe days</div>`
      );
      marker.on("click", () => selectSpot(spot.id));
      marker.addTo(map);
      markersById[spot.id] = marker;
    });

    if (spots.length > 0) {
      const bounds = L.latLngBounds(spots.map((spot) => [spot.lat, spot.lon]));
      map.fitBounds(bounds.pad(0.12));
    }
  }

  function selectSpot(spotId) {
    const spot = bootstrap.spots.find((item) => item.id === spotId);
    if (!spot) return;

    selectedSpotId = spotId;
    renderMarkers();

    regionSummary.classList.add("hidden");
    spotDetail.classList.remove("hidden");

    document.getElementById("spot-name").textContent = spot.name;
    document.getElementById("spot-info").innerHTML = spotInfoHtml(spot);

    const verdictEl = document.getElementById("spot-verdict");
    verdictEl.className = `spot-verdict ${spot.status}`;
    verdictEl.textContent = spot.verdict;

    document.getElementById("spot-stats").innerHTML = `
      <div class="stat-box"><span class="stat-value">${spot.good_days}</span><span class="stat-label">Good days</span></div>
      <div class="stat-box"><span class="stat-value">${spot.marginal_days}</span><span class="stat-label">Maybe days</span></div>
      <div class="stat-box"><span class="stat-value">${spot.nogo_days}</span><span class="stat-label">No-go days</span></div>
    `;

    const windowEl = document.getElementById("spot-window");
    if (spot.best_window) {
      windowEl.textContent = `${spot.best_window.label}: ${spot.best_window.date_range} · ${spot.best_window.wind_range} ${spot.best_window.direction_display}`;
    } else {
      windowEl.textContent = spot.window_label || "No suitable window at this spot.";
    }

    const tbody = document.getElementById("spot-days-body");
    tbody.innerHTML = "";
    spot.days.forEach((day) => {
      const tr = document.createElement("tr");
      const badgeClass =
        day.suitability === "Good"
          ? "good"
          : day.suitability === "Marginal"
            ? "marginal"
            : "no-go";
      tr.innerHTML = `
        <td>${day.date_display}</td>
        <td>${day.avg_wind_kts != null ? `${day.avg_wind_kts} kn` : "—"}</td>
        <td>${day.max_gust_kts != null ? `${day.max_gust_kts} kn` : "—"}</td>
        <td>${day.direction_display || "—"}</td>
        <td><span class="badge badge-${badgeClass}">${day.suitability}</span></td>
      `;
      tbody.appendChild(tr);
    });

    const link = document.getElementById("spot-link");
    if (spot.permalink) {
      link.href = spot.permalink;
      link.classList.remove("hidden");
    } else {
      link.classList.add("hidden");
    }

    map.setView([spot.lat, spot.lon], Math.max(map.getZoom(), 10));
  }

  function showRegionView() {
    selectedSpotId = null;
    spotDetail.classList.add("hidden");
    regionSummary.classList.remove("hidden");
    renderMarkers();
  }

  regionSelect.addEventListener("change", () => {
    currentRegion = regionSelect.value;
    showRegionView();
    renderRegionSummary();
  });

  spotBack.addEventListener("click", showRegionView);

  document.querySelector(".refresh-form")?.addEventListener("submit", () => {
    const btn = document.getElementById("refresh-btn");
    btn.disabled = true;
    btn.textContent = "Refreshing…";
  });

  renderRegionSummary();
  renderMarkers();
})();
