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
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 19,
  }).addTo(map);
  var popupDiv = document.createElement("div");
  popupDiv.textContent = title;
  L.marker([lat, lon]).addTo(map).bindPopup(popupDiv).openPopup();
})();

// Dashboard map with MarkerCluster + grid/map toggle
(function () {
  var mapEl = document.getElementById("dashboard-map");
  var resultsEl = document.getElementById("results");
  if (!mapEl || !resultsEl) return;

  var toggleBtns = document.querySelectorAll(".view-toggle-btn");
  var dashMap = null;
  var mapInitialized = false;

  function createMarkerIcon(rating) {
    var color;
    if (rating >= 4) color = "#28a745";
    else if (rating === 3) color = "#ffc107";
    else if (rating && rating < 3) color = "#dc3545";
    else color = "#8c8c8c";

    return L.divIcon({
      className: "rating-marker",
      html: '<div style="background:' + color + ';width:12px;height:12px;border-radius:50%;border:2px solid #1a1b23;box-shadow:0 1px 3px rgba(0,0,0,.4)"></div>',
      iconSize: [16, 16],
      iconAnchor: [8, 8],
      popupAnchor: [0, -10],
    });
  }

  function initMap() {
    if (mapInitialized) return;
    mapInitialized = true;

    var data = window.propertiesMapData || [];
    if (data.length === 0) return;

    dashMap = L.map("dashboard-map").setView([51.545, -0.055], 13);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19,
    }).addTo(dashMap);

    var cluster = L.markerClusterGroup({ maxClusterRadius: 40 });

    for (var i = 0; i < data.length; i++) {
      var p = data[i];
      var icon = createMarkerIcon(p.rating);
      var marker = L.marker([p.lat, p.lon], { icon: icon });

      // Build safe popup with DOM
      var popupDiv = document.createElement("div");
      popupDiv.style.cssText = "font-size:13px;line-height:1.4;color:#e2e2e8";
      var link = document.createElement("a");
      link.href = p.url;
      link.textContent = p.title;
      link.style.cssText = "font-weight:600;color:#818cf8";
      popupDiv.appendChild(link);
      popupDiv.appendChild(document.createElement("br"));
      var info = document.createTextNode(
        "\u00A3" + p.price.toLocaleString() + "/mo \u00B7 " + p.bedrooms + " bed"
        + (p.rating ? " \u00B7 " + p.rating + "\u2605" : "")
      );
      popupDiv.appendChild(info);

      marker.bindPopup(popupDiv);
      cluster.addLayer(marker);
    }

    dashMap.addLayer(cluster);
    dashMap.fitBounds(cluster.getBounds().pad(0.1));
  }

  toggleBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var view = btn.dataset.view;

      toggleBtns.forEach(function (b) {
        b.classList.remove("active");
        b.setAttribute("aria-pressed", "false");
      });
      btn.classList.add("active");
      btn.setAttribute("aria-pressed", "true");

      if (view === "map") {
        resultsEl.hidden = true;
        mapEl.hidden = false;
        initMap();
        if (dashMap) dashMap.invalidateSize();
      } else {
        mapEl.hidden = true;
        resultsEl.hidden = false;
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
