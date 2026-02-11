// Lightbox with focus trapping, touch/swipe, event delegation, and preloading
(function () {
  var lightbox = document.getElementById("lightbox");
  if (!lightbox) return;

  var img = lightbox.querySelector(".lightbox-img");
  var counter = lightbox.querySelector(".lightbox-counter");
  var images = Array.from(document.querySelectorAll("[data-lightbox]"));
  var currentIndex = 0;
  var previousFocus = null;
  var touchStartX = 0;
  var touchStartY = 0;

  function open(index) {
    currentIndex = index;
    img.src = images[index].src;
    counter.textContent = (index + 1) + " / " + images.length;
    lightbox.hidden = false;
    document.body.style.overflow = "hidden";
    previousFocus = document.activeElement;
    lightbox.querySelector(".lightbox-close").focus();
    preloadAdjacent();
  }

  function close() {
    lightbox.hidden = true;
    document.body.style.overflow = "";
    img.src = "";
    if (previousFocus) {
      previousFocus.focus();
      previousFocus = null;
    }
  }

  function prev() {
    open((currentIndex - 1 + images.length) % images.length);
  }

  function next() {
    open((currentIndex + 1) % images.length);
  }

  function preloadAdjacent() {
    var nextIdx = (currentIndex + 1) % images.length;
    if (nextIdx !== currentIndex) {
      new Image().src = images[nextIdx].src;
    }
  }

  // Event delegation for lightbox triggers
  document.addEventListener("click", function (e) {
    var target = e.target.closest("[data-lightbox]");
    if (!target) return;
    var idx = images.indexOf(target);
    if (idx >= 0) {
      e.preventDefault();
      open(idx);
    }
  });

  lightbox.querySelector(".lightbox-close").addEventListener("click", close);
  lightbox.querySelector(".lightbox-prev").addEventListener("click", prev);
  lightbox.querySelector(".lightbox-next").addEventListener("click", next);

  lightbox.addEventListener("click", function (e) {
    if (e.target === lightbox) close();
  });

  // Keyboard: navigation + focus trap
  document.addEventListener("keydown", function (e) {
    if (lightbox.hidden) return;
    if (e.key === "Escape") { close(); return; }
    if (e.key === "ArrowLeft") { prev(); return; }
    if (e.key === "ArrowRight") { next(); return; }

    // Focus trap
    if (e.key === "Tab") {
      var focusable = lightbox.querySelectorAll("button:not([hidden])");
      if (focusable.length === 0) return;
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
  });

  // Touch/swipe support
  lightbox.addEventListener("touchstart", function (e) {
    if (e.touches.length === 1) {
      touchStartX = e.touches[0].clientX;
      touchStartY = e.touches[0].clientY;
    }
  }, { passive: true });

  lightbox.addEventListener("touchend", function (e) {
    if (e.changedTouches.length === 1) {
      var dx = e.changedTouches[0].clientX - touchStartX;
      var dy = e.changedTouches[0].clientY - touchStartY;
      if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {
        if (dx < 0) next();
        else prev();
      }
    }
  }, { passive: true });
})();

// Detail page Leaflet map (single property)
(function () {
  var el = document.getElementById("map");
  if (!el) return;

  var lat = parseFloat(el.dataset.lat);
  var lon = parseFloat(el.dataset.lon);
  var title = el.dataset.title || "Property";

  if (isNaN(lat) || isNaN(lon)) return;

  var map = L.map("map").setView([lat, lon], 15);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
    subdomains: "abcd",
    maxZoom: 20,
  }).addTo(map);
  var popupDiv = document.createElement("div");
  popupDiv.textContent = title;
  L.marker([lat, lon]).addTo(map).bindPopup(popupDiv).openPopup();
})();

// Dashboard map with MarkerCluster + grid/split/map toggle + hover sync
(function () {
  var mapEl = document.getElementById("dashboard-map");
  var resultsEl = document.getElementById("results");
  var splitContainer = document.getElementById("split-container");
  if (!mapEl || !resultsEl) return;

  var toggleBtns = document.querySelectorAll(".view-toggle-btn");
  var dashMap = null;
  var cluster = null;
  var mapInitialized = false;
  var markersByPropertyId = {};

  function createPricePillIcon(price, id) {
    var formatted = "\u00A3" + Number(price).toLocaleString();
    return L.divIcon({
      className: "price-pill-marker",
      html: '<div class="price-pill" data-property-id="' + id + '">' + formatted + "</div>",
      iconSize: null,
      iconAnchor: [30, 15],
      popupAnchor: [0, -18],
    });
  }

  function buildRichPopup(p) {
    var container = document.createElement("div");
    container.className = "map-popup";

    if (p.image_url) {
      var img = document.createElement("img");
      img.className = "map-popup-img";
      img.src = p.image_url;
      img.alt = p.title;
      container.appendChild(img);
    }

    var title = document.createElement("a");
    title.className = "map-popup-title";
    title.href = p.url;
    title.textContent = p.title;
    container.appendChild(title);

    var meta = document.createElement("div");
    meta.className = "map-popup-meta";
    var price = document.createElement("strong");
    price.textContent = "\u00A3" + Number(p.price).toLocaleString() + "/mo";
    meta.appendChild(price);
    meta.appendChild(document.createTextNode(" \u00B7 " + p.bedrooms + " bed"));
    if (p.postcode) {
      meta.appendChild(document.createTextNode(" \u00B7 " + p.postcode));
    }
    container.appendChild(meta);

    if (p.rating) {
      var stars = document.createElement("div");
      stars.className = "map-popup-stars";
      for (var i = 0; i < 5; i++) {
        stars.appendChild(document.createTextNode(i < p.rating ? "\u2605" : "\u2606"));
      }
      container.appendChild(stars);
    }

    return container;
  }

  function attachMarkerEvents(marker, p) {
    marker.on("mouseover", function () {
      var card = resultsEl.querySelector('.property-card[data-property-id="' + p.id + '"]');
      if (card) {
        card.classList.add("card-highlighted");
        card.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    });
    marker.on("mouseout", function () {
      var card = resultsEl.querySelector('.property-card[data-property-id="' + p.id + '"]');
      if (card) card.classList.remove("card-highlighted");
    });
    marker.on("click", function () {
      var card = resultsEl.querySelector('.property-card[data-property-id="' + p.id + '"]');
      if (card) {
        card.scrollIntoView({ behavior: "smooth", block: "center" });
        card.classList.add("card-highlighted");
        setTimeout(function () { card.classList.remove("card-highlighted"); }, 2000);
      }
    });
    marker.on("popupopen", function () {
      var pill = mapEl.querySelector('.price-pill[data-property-id="' + p.id + '"]');
      if (pill) pill.classList.add("active");
    });
    marker.on("popupclose", function () {
      var pill = mapEl.querySelector('.price-pill[data-property-id="' + p.id + '"]');
      if (pill) pill.classList.remove("active");
    });
  }

  function buildMarkers(data) {
    if (cluster) dashMap.removeLayer(cluster);
    cluster = L.markerClusterGroup({ maxClusterRadius: 40 });
    markersByPropertyId = {};

    for (var i = 0; i < data.length; i++) {
      var p = data[i];
      var icon = createPricePillIcon(p.price, p.id);
      var marker = L.marker([p.lat, p.lon], { icon: icon });
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

    var data = window.propertiesMapData || [];
    if (data.length === 0) return;

    dashMap = L.map("dashboard-map").setView([51.545, -0.055], 13);
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
      subdomains: "abcd",
      maxZoom: 20,
    }).addTo(dashMap);

    buildMarkers(data);
  }

  // Card → Marker hover sync (event delegation)
  resultsEl.addEventListener("mouseenter", function (e) {
    var card = e.target.closest(".property-card[data-property-id]");
    if (!card) return;
    var id = card.dataset.propertyId;
    var pill = mapEl.querySelector('.price-pill[data-property-id="' + id + '"]');
    if (pill) pill.classList.add("highlighted");
  }, true);

  resultsEl.addEventListener("mouseleave", function (e) {
    var card = e.target.closest(".property-card[data-property-id]");
    if (!card) return;
    var id = card.dataset.propertyId;
    var pill = mapEl.querySelector('.price-pill[data-property-id="' + id + '"]');
    if (pill) pill.classList.remove("highlighted");
  }, true);

  // HTMX map data sync
  window._updateMapData = function (data) {
    window.propertiesMapData = data;
    if (dashMap && cluster) {
      buildMarkers(data);
    }
  };

  // View toggle: grid / split / map
  toggleBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var view = btn.dataset.view;

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
        var data = window.propertiesMapData || [];
        if (data.length === 0) {
          // No map data — fall back to grid-like display
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
})();

// Lazy loading with IntersectionObserver
(function () {
  var lazyImages = document.querySelectorAll("img[loading=lazy]");
  if (!("IntersectionObserver" in window) || lazyImages.length === 0) return;

  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        var img = entry.target;
        if (img.dataset.src) {
          img.src = img.dataset.src;
          img.removeAttribute("data-src");
        }
        observer.unobserve(img);
      }
    });
  });

  lazyImages.forEach(function (img) { observer.observe(img); });
})();
