// Filter form: strip empty params, auto-apply selects, chip removal
(function () {
  // Strip empty-string params before HTMX sends request
  document.addEventListener("htmx:configRequest", function (e) {
    var params = e.detail.parameters;
    var keys = Object.keys(params);
    for (var i = 0; i < keys.length; i++) {
      if (params[keys[i]] === "") {
        delete params[keys[i]];
      }
    }
    // Reset to page 1 when filter form triggers (not pagination links)
    var trigger = e.detail.elt;
    if (trigger && trigger.closest && trigger.closest(".filter-form")) {
      delete params["page"];
    }
  });

  // Auto-apply: submit form on <select> or radio change (not inside dialog)
  document.addEventListener("change", function (e) {
    var tag = e.target.tagName;
    var type = (e.target.type || "").toLowerCase();
    var inDialog = e.target.closest("dialog");
    if (inDialog) {
      // Inside modal: dispatch filterChange for live count
      inDialog.dispatchEvent(new CustomEvent("filterChange", { bubbles: true }));
      return;
    }
    if (tag === "SELECT" || (tag === "INPUT" && type === "radio")) {
      var form = e.target.closest(".filter-form");
      if (form) {
        htmx.trigger(form, "submit");
      }
    }
  });

  // Chip removal: clear field and re-submit
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".filter-chip-remove[data-filter-key]");
    if (!btn) return;
    var key = btn.dataset.filterKey;
    var value = btn.dataset.filterValue;
    var form = document.querySelector(".filter-form");
    if (!form) return;

    if (key === "tag" && value) {
      // Uncheck the matching tag checkbox
      var checkboxes = form.querySelectorAll('input[name="tag"]');
      for (var i = 0; i < checkboxes.length; i++) {
        if (checkboxes[i].value === value) {
          checkboxes[i].checked = false;
        }
      }
    } else if (key === "bedrooms") {
      // Reset beds toggle to "Any"
      var anyRadio = form.querySelector('input[name="bedrooms"][value=""]');
      if (anyRadio) anyRadio.checked = true;
    } else {
      var field = form.querySelector('[name="' + key + '"]');
      if (field) {
        field.value = "";
      }
    }
    htmx.trigger(form, "submit");
  });

  // Filter modal: open, close, reset
  var dialog = document.getElementById("filter-modal");
  var openBtn = document.getElementById("open-filters-btn");
  if (dialog && openBtn) {
    openBtn.addEventListener("click", function () {
      syncToMobile();
      dialog.showModal();
    });

    dialog.querySelector(".filter-modal-close").addEventListener("click", function () {
      dialog.close();
    });

    dialog.querySelector(".filter-modal-reset").addEventListener("click", function () {
      // Clear all selects and checkboxes inside the dialog
      var selects = dialog.querySelectorAll("select");
      for (var i = 0; i < selects.length; i++) selects[i].value = "";
      var checkboxes = dialog.querySelectorAll('input[type="checkbox"]');
      for (var j = 0; j < checkboxes.length; j++) checkboxes[j].checked = false;
      // Reset mobile beds toggle
      var anyBeds = dialog.querySelector('input[name="bedrooms_mobile"][value=""]');
      if (anyBeds) anyBeds.checked = true;
      // Reset mobile price/rating
      var mobileInputs = dialog.querySelectorAll('.mobile-primary-section input[type="number"]');
      for (var k = 0; k < mobileInputs.length; k++) mobileInputs[k].value = "";
      var mobileSelects = dialog.querySelectorAll('.mobile-primary-section select');
      for (var l = 0; l < mobileSelects.length; l++) mobileSelects[l].value = "";
      dialog.dispatchEvent(new CustomEvent("filterChange", { bubbles: true }));
    });

    // Apply closes dialog and syncs values from mobile to desktop
    dialog.querySelector(".filter-modal-apply").addEventListener("click", function (e) {
      syncFromMobile();
      dialog.close();
    });

    // Close modal after HTMX swap completes (apply was clicked)
    document.addEventListener("htmx:afterRequest", function (e) {
      if (dialog.open && e.detail.elt && e.detail.elt.closest && e.detail.elt.closest(".filter-form")) {
        dialog.close();
      }
    });
  }

  // Mobile ↔ Desktop input sync
  var isMobile = window.matchMedia("(max-width: 768px)");

  function syncToMobile() {
    if (!isMobile.matches || !dialog) return;
    var form = document.querySelector(".filter-form");
    if (!form) return;
    // Beds
    var desktopBeds = form.querySelector('input[name="bedrooms"]:checked');
    if (desktopBeds) {
      var mobileBeds = dialog.querySelector('input[name="bedrooms_mobile"][value="' + desktopBeds.value + '"]');
      if (mobileBeds) mobileBeds.checked = true;
    }
    // Price
    var minP = form.querySelector('input[name="min_price"]');
    var minPM = dialog.querySelector('input[name="min_price_mobile"]');
    if (minP && minPM) minPM.value = minP.value;
    var maxP = form.querySelector('input[name="max_price"]');
    var maxPM = dialog.querySelector('input[name="max_price_mobile"]');
    if (maxP && maxPM) maxPM.value = maxP.value;
    // Rating
    var rating = form.querySelector('select[name="min_rating"]');
    var ratingM = dialog.querySelector('select[name="min_rating_mobile"]');
    if (rating && ratingM) ratingM.value = rating.value;
  }

  function syncFromMobile() {
    if (!isMobile.matches || !dialog) return;
    var form = document.querySelector(".filter-form");
    if (!form) return;
    // Beds
    var mobileBeds = dialog.querySelector('input[name="bedrooms_mobile"]:checked');
    if (mobileBeds) {
      var desktopBeds = form.querySelector('input[name="bedrooms"][value="' + mobileBeds.value + '"]');
      if (desktopBeds) desktopBeds.checked = true;
    }
    // Price
    var minPM = dialog.querySelector('input[name="min_price_mobile"]');
    var minP = form.querySelector('input[name="min_price"]');
    if (minPM && minP) minP.value = minPM.value;
    var maxPM = dialog.querySelector('input[name="max_price_mobile"]');
    var maxP = form.querySelector('input[name="max_price"]');
    if (maxPM && maxP) maxP.value = maxPM.value;
    // Rating
    var ratingM = dialog.querySelector('select[name="min_rating_mobile"]');
    var rating = form.querySelector('select[name="min_rating"]');
    if (ratingM && rating) rating.value = ratingM.value;
  }

  // Enable/disable mobile duplicates based on screen size
  function toggleMobileInputs() {
    if (!dialog) return;
    var mobile = isMobile.matches;
    var mobileInputs = dialog.querySelectorAll('.mobile-primary-section input, .mobile-primary-section select');
    for (var i = 0; i < mobileInputs.length; i++) {
      mobileInputs[i].disabled = !mobile;
    }
  }
  toggleMobileInputs();
  isMobile.addEventListener("change", toggleMobileInputs);
})();

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

  var map = L.map("map", {
    scrollWheelZoom: false,
    zoomControl: false,
    boxZoom: false,
  }).setView([lat, lon], 15);

  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
    subdomains: "abcd",
    maxZoom: 20,
  }).addTo(map);

  L.control.zoom({ position: "bottomright" }).addTo(map);

  // Enable scroll/pinch zoom after clicking the map (prevents scroll hijacking)
  map.on("click", function () {
    map.scrollWheelZoom.enable();
  });
  map.getContainer().addEventListener("mouseleave", function () {
    map.scrollWheelZoom.disable();
  });

  // Subtle area radius
  L.circle([lat, lon], {
    radius: 500,
    color: "#8b5cf6",
    fillColor: "#8b5cf6",
    fillOpacity: 0.04,
    weight: 1,
    opacity: 0.15,
    interactive: false,
  }).addTo(map);

  // Custom accent pin marker with pulse
  var markerIcon = L.divIcon({
    className: "detail-marker",
    html: '<div class="detail-marker-pin"></div><div class="detail-marker-pulse"></div>',
    iconSize: [30, 42],
    iconAnchor: [15, 42],
    popupAnchor: [0, -36],
  });

  L.marker([lat, lon], { icon: markerIcon, alt: title })
    .addTo(map)
    .bindTooltip(title, {
      permanent: true,
      direction: "top",
      offset: [0, -42],
      className: "detail-tooltip",
    });
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
      img.alt = p.bedrooms + " bed \u2014 " + (p.postcode || "Property");
      container.appendChild(img);
    }

    var titleText = p.bedrooms + " bed \u2014 " + (p.postcode || "Property");
    var title = document.createElement("a");
    title.className = "map-popup-title";
    title.href = p.url;
    title.textContent = titleText;
    container.appendChild(title);

    var meta = document.createElement("div");
    meta.className = "map-popup-meta";
    var price = document.createElement("strong");
    price.textContent = "\u00A3" + Number(p.price).toLocaleString();
    meta.appendChild(price);
    meta.appendChild(document.createTextNode(" pcm"));
    container.appendChild(meta);

    // Dot rating
    if (p.rating) {
      var dots = document.createElement("span");
      dots.className = "quality-dots quality-dots-" + p.rating;
      dots.style.marginTop = "4px";
      dots.style.display = "inline-block";
      var dotsHtml = p.rating + "/5 ";
      for (var d = 1; d <= 5; d++) {
        dotsHtml += '<span class="dot ' + (d <= p.rating ? "filled" : "empty") + '">\u25CF</span>';
      }
      dots.innerHTML = dotsHtml;
      container.appendChild(dots);
    }

    // One-line tagline
    if (p.one_line) {
      var tagline = document.createElement("div");
      tagline.className = "map-popup-meta";
      tagline.style.marginTop = "4px";
      tagline.textContent = p.one_line;
      container.appendChild(tagline);
    }

    // Commute pill
    if (p.commute_minutes) {
      var pill = document.createElement("span");
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
      var badge = document.createElement("span");
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

// Section navigation: sticky shadow + active link highlighting
(function () {
  var nav = document.getElementById("section-nav");
  if (!nav) return;

  var links = nav.querySelectorAll(".section-nav-links a");
  var sections = [];
  links.forEach(function (link) {
    var id = link.getAttribute("href").slice(1);
    var section = document.getElementById(id);
    if (section) sections.push({ el: section, link: link });
  });
  if (sections.length === 0) return;

  // Highlight active section link via IntersectionObserver
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        links.forEach(function (l) { l.classList.remove("active"); });
        for (var i = 0; i < sections.length; i++) {
          if (sections[i].el === entry.target) {
            sections[i].link.classList.add("active");
            break;
          }
        }
      }
    });
  }, { rootMargin: "-20% 0px -70% 0px" });

  sections.forEach(function (s) { observer.observe(s.el); });

  // Smooth scroll on click
  links.forEach(function (link) {
    link.addEventListener("click", function (e) {
      e.preventDefault();
      var id = link.getAttribute("href").slice(1);
      var target = document.getElementById(id);
      if (target) target.scrollIntoView({ behavior: "smooth" });
    });
  });

  // Toggle sticky shadow on scroll
  var lastScrollY = 0;
  window.addEventListener("scroll", function () {
    var y = window.scrollY;
    if (y > 100 && lastScrollY <= 100) nav.classList.add("sticky");
    else if (y <= 100 && lastScrollY > 100) nav.classList.remove("sticky");
    lastScrollY = y;
  }, { passive: true });
})();

// Description expand/collapse
(function () {
  var text = document.getElementById("description-text");
  var btn = document.getElementById("description-toggle");
  if (!text || !btn) return;

  // Show button only if content overflows
  requestAnimationFrame(function () {
    if (text.scrollHeight > text.clientHeight + 10) {
      btn.hidden = false;
    }
  });

  btn.addEventListener("click", function () {
    var expanded = text.classList.toggle("expanded");
    btn.textContent = expanded ? "Show less" : "Read more";
  });
})();

// Gallery "+N more" overlay handler
(function () {
  var overlay = document.querySelector("[data-gallery-more]");
  if (!overlay) return;

  overlay.addEventListener("click", function (e) {
    e.stopPropagation();
    var images = Array.from(document.querySelectorAll("[data-lightbox]"));
    var startIndex = parseInt(overlay.dataset.galleryMore, 10);
    // Open lightbox at the first hidden image index
    if (startIndex >= 0 && startIndex < images.length) {
      images[startIndex].click();
    }
  });
})();

// Benchmark bar fill animation
(function () {
  var bars = document.querySelectorAll(".benchmark-fill");
  if (!("IntersectionObserver" in window) || bars.length === 0) return;

  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.classList.add("animate");
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.3 });

  bars.forEach(function (bar) { observer.observe(bar); });
})();

// Scroll to top of results after HTMX pagination swap
(function () {
  var results = document.getElementById("results");
  if (!results) return;

  document.addEventListener("htmx:afterSwap", function (e) {
    if (e.detail.target !== results) return;
    var trigger = e.detail.requestConfig && e.detail.requestConfig.elt;
    if (trigger && trigger.closest && trigger.closest(".pagination")) {
      results.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
})();
