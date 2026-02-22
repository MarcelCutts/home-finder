// Dashboard map with MarkerCluster + grid/split/map toggle + hover sync

const mapEl = document.getElementById("dashboard-map");
const resultsEl = document.getElementById("results");
const splitContainer = document.getElementById("split-container");
if (mapEl && resultsEl) {
  const toggleBtns = document.querySelectorAll(".view-toggle-btn");
  let dashMap = null;
  let cluster = null;
  let mapInitialized = false;
  let markersByPropertyId = {};
  let pinnedCardRequestId = 0;

  function readMapData() {
    const el = document.getElementById("properties-map-data");
    if (!el) return [];
    try { return JSON.parse(el.textContent); }
    catch { return []; }
  }

  function buildPinnedSkeleton() {
    const s = document.createElement("div");
    s.className = "pinned-skeleton";
    s.setAttribute("aria-hidden", "true");
    s.innerHTML =
      '<div class="skel-image"></div>' +
      '<div class="skel-body">' +
        '<div class="skel-line skel-line-short"></div>' +
        '<div class="skel-line skel-line-long"></div>' +
        '<div class="skel-line skel-line-med"></div>' +
      '</div>' +
      '<div class="skel-footer"><div class="skel-pill"></div><div class="skel-pill"></div></div>';
    return s;
  }

  function createPricePillIcon(price, id, isOffMarket) {
    const formatted = "\u00A3" + Number(price).toLocaleString();
    const cls = "price-pill" + (isOffMarket ? " price-pill-off-market" : "");
    return L.divIcon({
      className: "price-pill-marker",
      html: '<div class="' + cls + '" data-property-id="' + id + '">' + formatted + "</div>",
      iconSize: null,
      iconAnchor: [30, 15],
      popupAnchor: [0, -18],
    });
  }

  function buildRichPopup(p) {
    const container = document.createElement("div");
    container.className = "map-popup";

    if (p.image_url) {
      const img = document.createElement("img");
      img.className = "map-popup-img";
      img.src = p.image_url;
      img.alt = p.bedrooms + " bed \u2014 " + (p.postcode || "Property");
      container.appendChild(img);
    }

    const titleText = p.bedrooms + " bed \u2014 " + (p.postcode || "Property");
    const title = document.createElement("a");
    title.className = "map-popup-title";
    title.href = p.url;
    title.textContent = titleText;
    container.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "map-popup-meta";
    const price = document.createElement("strong");
    price.textContent = "\u00A3" + Number(p.price).toLocaleString();
    meta.appendChild(price);
    meta.appendChild(document.createTextNode(" pcm"));
    container.appendChild(meta);

    // Dot rating
    if (p.rating && typeof p.rating === "number" && p.rating >= 1 && p.rating <= 5) {
      const dots = document.createElement("span");
      dots.className = "quality-dots quality-dots-" + p.rating;
      dots.style.marginTop = "4px";
      dots.style.display = "inline-block";
      dots.appendChild(document.createTextNode(p.rating + "/5 "));
      for (let d = 1; d <= 5; d++) {
        var dot = document.createElement("span");
        dot.className = "dot " + (d <= p.rating ? "filled" : "empty");
        dot.textContent = "\u25CF";
        dots.appendChild(dot);
      }
      container.appendChild(dots);
    }

    // One-line tagline
    if (p.one_line) {
      const tagline = document.createElement("div");
      tagline.className = "map-popup-meta";
      tagline.style.marginTop = "4px";
      tagline.textContent = p.one_line;
      container.appendChild(tagline);
    }

    // Commute pill
    if (p.commute_minutes) {
      const pill = document.createElement("span");
      pill.className = "commute-pill";
      if (p.commute_minutes <= 15) pill.className += " commute-green";
      else if (p.commute_minutes <= 30) pill.className += " commute-indigo";
      else if (p.commute_minutes <= 45) pill.className += " commute-amber";
      else pill.className += " commute-red";
      pill.textContent = p.commute_minutes + " min";
      pill.style.marginTop = "6px";
      pill.style.display = "inline-block";
      container.appendChild(pill);
    }

    // Value badge
    if (p.value_rating) {
      const badge = document.createElement("span");
      badge.className = "value-badge value-" + p.value_rating;
      badge.textContent = p.value_rating;
      badge.style.marginTop = "4px";
      badge.style.marginLeft = p.commute_minutes ? "4px" : "0";
      badge.style.display = "inline-block";
      container.appendChild(badge);
    }

    return container;
  }

  function attachMarkerEvents(marker, p) {
    marker.on("mouseover", function () {
      const card = resultsEl.querySelector('.property-card[data-property-id="' + p.id + '"]');
      if (card) {
        card.classList.add("card-highlighted");
        card.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    });
    marker.on("mouseout", function () {
      const card = resultsEl.querySelector('.property-card[data-property-id="' + p.id + '"]');
      if (card) card.classList.remove("card-highlighted");
    });
    marker.on("click", async function () {
      const card = resultsEl.querySelector('.property-card[data-property-id="' + p.id + '"]');
      if (card) {
        card.scrollIntoView({ behavior: "smooth", block: "center" });
        card.classList.add("card-highlighted");
        setTimeout(function () { card.classList.remove("card-highlighted"); }, 2000);
        return;
      }
      // Card not on current page -- fetch and pin it at top of results
      const prev = resultsEl.querySelector(".pinned-card");
      if (prev) prev.remove();

      const thisRequest = ++pinnedCardRequestId;
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const fadeMs = reducedMotion ? 0 : 300;

      // Build stable wrapper with skeleton inside
      const wrapper = document.createElement("div");
      wrapper.className = "pinned-card";
      const dismiss = document.createElement("button");
      dismiss.className = "pinned-card-dismiss";
      dismiss.setAttribute("aria-label", "Dismiss pinned card");
      dismiss.textContent = "\u00d7";
      dismiss.onclick = function () { wrapper.remove(); };
      wrapper.appendChild(dismiss);
      const inner = document.createElement("div");
      inner.className = "pinned-card-inner";
      inner.appendChild(buildPinnedSkeleton());
      wrapper.appendChild(inner);
      resultsEl.insertBefore(wrapper, resultsEl.firstChild);
      resultsEl.scrollTop = 0;

      try {
        const r = await fetch("/property/" + encodeURIComponent(p.id) + "/card");
        if (!r.ok) throw new Error(r.status);
        const html = await r.text();
        if (thisRequest !== pinnedCardRequestId) return;
        // Fade out skeleton
        inner.classList.add("fade-out");
        setTimeout(function () {
          if (thisRequest !== pinnedCardRequestId) return;
          // TRUST BOUNDARY: HTML from /property/{id}/card is server-rendered
          // with Jinja2 auto-escaping. DOMParser used instead of innerHTML;
          // content is trusted first-party markup.
          const parser = new DOMParser();
          const doc = parser.parseFromString(html, "text/html");
          inner.textContent = "";
          while (doc.body.firstChild) {
            inner.appendChild(doc.body.firstChild);
          }
          inner.classList.remove("fade-out");
          const pinned = wrapper.querySelector(".property-card");
          if (pinned) {
            pinned.scrollIntoView({ behavior: "smooth", block: "center" });
            pinned.classList.add("card-highlighted");
            setTimeout(function () { pinned.classList.remove("card-highlighted"); }, 2000);
          }
        }, fadeMs);
      } catch {
        if (thisRequest !== pinnedCardRequestId) return;
        wrapper.remove();
        window.location.href = "/property/" + encodeURIComponent(p.id);
      }
    });
    marker.on("popupopen", function () {
      const pill = mapEl.querySelector('.price-pill[data-property-id="' + p.id + '"]');
      if (pill) pill.classList.add("active");
    });
    marker.on("popupclose", function () {
      const pill = mapEl.querySelector('.price-pill[data-property-id="' + p.id + '"]');
      if (pill) pill.classList.remove("active");
    });
  }

  function buildMarkers(data) {
    if (cluster) dashMap.removeLayer(cluster);
    cluster = L.markerClusterGroup({ maxClusterRadius: 40 });
    markersByPropertyId = {};

    for (let i = 0; i < data.length; i++) {
      const p = data[i];
      const icon = createPricePillIcon(p.price, p.id, p.is_off_market);
      const marker = L.marker([p.lat, p.lon], { icon: icon });
      marker.bindPopup(buildRichPopup(p), { maxWidth: 280, minWidth: 220 });
      attachMarkerEvents(marker, p);
      cluster.addLayer(marker);
      markersByPropertyId[p.id] = marker;
    }

    dashMap.addLayer(cluster);
    if (data.length > 0) {
      dashMap.fitBounds(cluster.getBounds().pad(0.1));
    }
  }

  function initMap() {
    if (mapInitialized) return;
    mapInitialized = true;

    const data = readMapData();
    if (data.length === 0) return;

    dashMap = L.map("dashboard-map").setView([51.545, -0.055], 13);
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
      subdomains: "abcd",
      maxZoom: 20,
    }).addTo(dashMap);

    buildMarkers(data);
  }

  // Card -> Marker hover sync (event delegation)
  resultsEl.addEventListener("mouseenter", function (e) {
    const card = e.target.closest(".property-card[data-property-id]");
    if (!card) return;
    const id = card.dataset.propertyId;
    const pill = mapEl.querySelector('.price-pill[data-property-id="' + id + '"]');
    if (pill) pill.classList.add("highlighted");
  }, true);

  resultsEl.addEventListener("mouseleave", function (e) {
    const card = e.target.closest(".property-card[data-property-id]");
    if (!card) return;
    const id = card.dataset.propertyId;
    const pill = mapEl.querySelector('.price-pill[data-property-id="' + id + '"]');
    if (pill) pill.classList.remove("highlighted");
  }, true);

  // HTMX map data sync: re-read JSON data element after swap
  document.addEventListener("htmx:afterSwap", function (e) {
    if (e.detail.target === resultsEl && dashMap && cluster) {
      const data = readMapData();
      buildMarkers(data);
    }
  });

  // View toggle: grid / split / map
  toggleBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      const view = btn.dataset.view;

      toggleBtns.forEach(function (b) {
        b.classList.remove("active");
        b.setAttribute("aria-pressed", "false");
      });
      btn.classList.add("active");
      btn.setAttribute("aria-pressed", "true");

      if (view === "grid") {
        if (splitContainer) splitContainer.classList.remove("split-active");
        resultsEl.hidden = false;
        mapEl.hidden = true;
      } else if (view === "split") {
        const data = readMapData();
        if (data.length === 0) {
          // No map data -- fall back to grid-like display
          if (splitContainer) splitContainer.classList.remove("split-active");
          resultsEl.hidden = false;
          mapEl.hidden = true;
          return;
        }
        if (splitContainer) splitContainer.classList.add("split-active");
        resultsEl.hidden = false;
        mapEl.hidden = false;
        initMap();
        if (dashMap) dashMap.invalidateSize();
      } else if (view === "map") {
        if (splitContainer) splitContainer.classList.remove("split-active");
        resultsEl.hidden = true;
        mapEl.hidden = false;
        initMap();
        if (dashMap) dashMap.invalidateSize();
      }
    });
  });
}
