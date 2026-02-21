// Fix Leaflet tile gap rendering (white lines between tiles)
// https://github.com/Leaflet/Leaflet/issues/3575
// Still needed as of Leaflet 1.9.4 — revisit when upgrading to 2.x
var _origInitTile = L.GridLayer.prototype._initTile;
L.GridLayer.include({
  _initTile: function (tile) {
    _origInitTile.call(this, tile);
    var tileSize = this.getTileSize();
    tile.style.width = tileSize.x + 1 + "px";
    tile.style.height = tileSize.y + 1 + "px";
  },
});

// Filter form: strip empty params, chip removal, explicit apply
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

  // Inside modal: dispatch filterChange for live count update (no form submit)
  document.addEventListener("change", function (e) {
    var inDialog = e.target.closest("dialog");
    if (inDialog) {
      inDialog.dispatchEvent(new CustomEvent("filterChange", { bubbles: true }));
    }
  });

  // Chip removal: clear field and submit
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
    } else if (key === "off_market") {
      var cb = form.querySelector('input[name="off_market"]');
      if (cb) cb.checked = false;
    } else {
      var field = form.querySelector('[name="' + key + '"]');
      if (field) {
        field.value = "";
      }
    }
    htmx.trigger(form, "submit");
  });

  // Filter modal: open, close, reset, apply
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
      var form = document.querySelector(".filter-form");
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
      // Also reset desktop primary filters
      if (form) {
        var deskBeds = form.querySelector('input[name="bedrooms"][value=""]');
        if (deskBeds) deskBeds.checked = true;
        var deskArea = form.querySelector('select[name="area"]');
        if (deskArea) deskArea.value = "";
        var deskRating = form.querySelector('select[name="min_rating"]');
        if (deskRating) deskRating.value = "";
        var deskMinP = form.querySelector('input[name="min_price"]');
        if (deskMinP) deskMinP.value = "";
        var deskMaxP = form.querySelector('input[name="max_price"]');
        if (deskMaxP) deskMaxP.value = "";
        var deskAdded = form.querySelector('select[name="added"]');
        if (deskAdded) deskAdded.value = "";
      }
      dialog.dispatchEvent(new CustomEvent("filterChange", { bubbles: true }));
    });

    // Apply: sync mobile values, close dialog, then submit form
    dialog.querySelector(".filter-modal-apply").addEventListener("click", function (e) {
      e.preventDefault();
      syncFromMobile();
      dialog.close();
      var form = document.querySelector(".filter-form");
      if (form) htmx.trigger(form, "submit");
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

// Fit badge popover: click-outside close
(function () {
  document.addEventListener("click", function (e) {
    var openDetails = document.querySelectorAll("details.fit-popover-wrap[open]");
    for (var i = 0; i < openDetails.length; i++) {
      if (!openDetails[i].contains(e.target)) {
        openDetails[i].removeAttribute("open");
      }
    }
  });
})();

// Fit breakdown bar fill animation (detail page)
(function () {
  var bars = document.querySelectorAll(".fit-breakdown-fill");
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

// Fit factor chips: tap-to-toggle on touch devices
(function () {
  if (!window.matchMedia("(pointer: coarse)").matches) return;

  var groups = document.querySelectorAll(".fit-breakdown-group");
  if (groups.length === 0) return;

  groups.forEach(function (group) {
    group.addEventListener("click", function () {
      groups.forEach(function (g) {
        if (g !== group) g.classList.remove("expanded");
      });
      group.classList.toggle("expanded");
    });
  });
})();

// Gallery view: full-page overlay with filmstrip, tabs, crossfade, keyboard/touch
(function () {
  var gallery = document.getElementById("gallery-view");
  if (!gallery) return;

  var mainImg = gallery.querySelector(".gallery-main-img");
  var nextImg = gallery.querySelector(".gallery-main-img-next");
  var counter = gallery.querySelector(".gallery-counter");
  var filmstrip = gallery.querySelector(".gallery-filmstrip");
  var stage = gallery.querySelector(".gallery-stage");
  var spinner = gallery.querySelector(".gallery-spinner");
  var thumbs = filmstrip ? Array.from(filmstrip.querySelectorAll(".gallery-filmstrip-thumb")) : [];
  var imageSrcs = thumbs.map(function (t) { return t.dataset.src; });
  var tabs = Array.from(gallery.querySelectorAll(".gallery-tab"));
  var panels = Array.from(gallery.querySelectorAll(".gallery-panel"));

  var currentIndex = 0;
  var navigateId = 0;
  var crossfadeTimer = null;
  var imageCache = {};  // src → Image object (holding ref prevents GC/cache eviction)
  var previousFocus = null;
  var touchStartX = 0;
  var touchStartY = 0;
  var reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Collect all hero/page [data-lightbox] images to map click → gallery index
  var heroImages = Array.from(document.querySelectorAll("[data-lightbox]"));

  function loadImage(src) {
    if (imageCache[src]) return Promise.resolve(true);
    return new Promise(function (resolve) {
      var img = new Image();
      img.src = src;
      img.decode().then(function () {
        imageCache[src] = img;
        resolve(true);
      }).catch(function () {
        resolve(false);
      });
    });
  }

  function showSpinner() {
    if (spinner) spinner.hidden = false;
  }

  function hideSpinner() {
    if (spinner) spinner.hidden = true;
  }

  function updateFilmstrip(index) {
    thumbs.forEach(function (t, i) {
      var isActive = i === index;
      t.classList.toggle("active", isActive);
      t.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    if (filmstrip && thumbs[index]) {
      var thumb = thumbs[index];
      var scrollLeft = thumb.offsetLeft - filmstrip.clientWidth / 2 + thumb.offsetWidth / 2;
      filmstrip.scrollLeft = scrollLeft;
    }
  }

  function updateCounter(index) {
    if (counter) {
      counter.textContent = (index + 1) + " / " + imageSrcs.length;
    }
  }

  function doSwap(src, index, animate, navId) {
    if (!mainImg) return;
    clearTimeout(crossfadeTimer);
    mainImg.classList.remove("crossfade-out");
    if (nextImg) nextImg.classList.remove("crossfade-in");

    if (animate && nextImg) {
      nextImg.src = src;
      nextImg.alt = "Property photo " + (index + 1);

      function startFade() {
        if (navId !== navigateId) return;
        mainImg.classList.add("crossfade-out");
        nextImg.classList.add("crossfade-in");
        crossfadeTimer = setTimeout(function () {
          if (navId !== navigateId) return;
          mainImg.src = src;
          mainImg.alt = "Property photo " + (index + 1);

          function finishSwap() {
            if (navId !== navigateId) return;
            // Disable transition — this is a bookkeeping swap, not a visual crossfade.
            mainImg.style.transition = "none";
            nextImg.style.transition = "none";
            mainImg.classList.remove("crossfade-out");
            nextImg.classList.remove("crossfade-in");
            // Force reflow so the browser commits the instant opacity change.
            void mainImg.offsetHeight;
            // Re-enable CSS transitions for the next crossfade.
            mainImg.style.transition = "";
            nextImg.style.transition = "";
          }

          mainImg.decode().then(finishSwap).catch(finishSwap);
        }, 200);
      }

      nextImg.decode().then(startFade).catch(startFade);
    } else {
      mainImg.src = src;
      mainImg.alt = "Property photo " + (index + 1);
      mainImg.decode().catch(function () {});
    }
  }

  function preloadAround(index) {
    for (var offset = 1; offset <= 3; offset++) {
      var fwd = (index + offset) % imageSrcs.length;
      var bwd = (index - offset + imageSrcs.length) % imageSrcs.length;
      if (fwd !== index) loadImage(imageSrcs[fwd]);
      if (bwd !== index && bwd !== fwd) loadImage(imageSrcs[bwd]);
    }
  }

  function showImage(index, animate) {
    if (imageSrcs.length === 0) return;
    index = ((index % imageSrcs.length) + imageSrcs.length) % imageSrcs.length;
    currentIndex = index;
    var thisNav = ++navigateId;

    updateFilmstrip(index);
    updateCounter(index);

    var src = imageSrcs[index];

    if (imageCache[src]) {
      hideSpinner();
      doSwap(src, index, animate && !reducedMotion, thisNav);
      preloadAround(index);
      return;
    }

    showSpinner();
    loadImage(src).then(function (ok) {
      if (thisNav !== navigateId) return;
      hideSpinner();
      if (ok) {
        doSwap(src, index, false, thisNav);
      } else {
        doSwap(src, index, false, thisNav);
        if (mainImg) mainImg.alt = "Image unavailable";
      }
      preloadAround(index);
    });
  }

  function open(index, tab) {
    tab = tab || "photos";
    gallery.hidden = false;
    document.body.style.overflow = "hidden";
    previousFocus = document.activeElement;
    switchTab(tab);
    if (tab === "photos" && imageSrcs.length > 0) {
      showImage(index, false);
    }
    gallery.querySelector(".gallery-close").focus();
  }

  function close() {
    gallery.hidden = true;
    document.body.style.overflow = "";
    clearTimeout(crossfadeTimer);
    hideSpinner();
    if (mainImg) { mainImg.classList.remove("crossfade-out"); mainImg.src = ""; }
    if (nextImg) { nextImg.classList.remove("crossfade-in"); nextImg.src = ""; }
    imageCache = {};
    if (previousFocus) {
      previousFocus.focus();
      previousFocus = null;
    }
  }

  function switchTab(tabName) {
    tabs.forEach(function (t) {
      var isActive = t.dataset.tab === tabName;
      t.classList.toggle("active", isActive);
      t.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    panels.forEach(function (p) {
      var panelTab = p.id.replace("gallery-panel-", "");
      if (panelTab === tabName) {
        p.classList.remove("gallery-panel-hidden");
      } else {
        p.classList.add("gallery-panel-hidden");
      }
    });
  }

  function prev() {
    showImage(currentIndex - 1, true);
  }

  function next() {
    showImage(currentIndex + 1, true);
  }

  // Click delegation for opening gallery from hero images and floorplan images
  document.addEventListener("click", function (e) {
    // "+N more" overlay
    var moreOverlay = e.target.closest("[data-gallery-more]");
    if (moreOverlay) {
      e.preventDefault();
      e.stopPropagation();
      var startIdx = parseInt(moreOverlay.dataset.galleryMore, 10);
      open(startIdx, "photos");
      return;
    }

    var target = e.target.closest("[data-lightbox]");
    if (!target) return;
    e.preventDefault();

    if (target.dataset.lightbox === "floorplan") {
      open(0, "floorplan");
      return;
    }

    // Hero gallery image — find matching index in filmstrip by src
    var clickedSrc = target.tagName === "IMG" ? target.src : (target.querySelector("img") || {}).src;
    var galleryIdx = 0;
    if (clickedSrc) {
      for (var i = 0; i < imageSrcs.length; i++) {
        // Compare by checking if src ends with same path
        if (clickedSrc === imageSrcs[i] || clickedSrc.indexOf(imageSrcs[i]) >= 0 || imageSrcs[i].indexOf(clickedSrc.split("/").pop()) >= 0) {
          galleryIdx = i;
          break;
        }
      }
      // Fallback: use position in hero images array
      if (galleryIdx === 0 && heroImages.indexOf(target) > 0) {
        var heroIdx = heroImages.indexOf(target);
        if (heroIdx < imageSrcs.length) galleryIdx = heroIdx;
      }
    }
    open(galleryIdx, "photos");
  });

  // Close button
  gallery.querySelector(".gallery-close").addEventListener("click", close);

  // Nav arrows
  var prevBtn = gallery.querySelector(".gallery-prev");
  var nextBtn = gallery.querySelector(".gallery-next");
  if (prevBtn) prevBtn.addEventListener("click", prev);
  if (nextBtn) nextBtn.addEventListener("click", next);

  // Filmstrip thumb clicks
  if (filmstrip) {
    filmstrip.addEventListener("click", function (e) {
      var thumb = e.target.closest(".gallery-filmstrip-thumb");
      if (!thumb) return;
      var idx = parseInt(thumb.dataset.index, 10);
      if (!isNaN(idx)) showImage(idx, true);
    });
  }

  // Tab clicks
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      switchTab(tab.dataset.tab);
    });
  });

  // Keyboard: navigation + focus trap
  document.addEventListener("keydown", function (e) {
    if (gallery.hidden) return;
    if (e.key === "Escape") { close(); return; }
    if (e.key === "ArrowLeft") { prev(); return; }
    if (e.key === "ArrowRight") { next(); return; }

    // Focus trap
    if (e.key === "Tab") {
      var focusable = Array.from(gallery.querySelectorAll("button:not([hidden]):not([disabled])"));
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

  // Touch/swipe support on stage
  if (stage) {
    stage.addEventListener("touchstart", function (e) {
      if (e.touches.length === 1) {
        touchStartX = e.touches[0].clientX;
        touchStartY = e.touches[0].clientY;
      }
    }, { passive: true });

    stage.addEventListener("touchend", function (e) {
      if (e.changedTouches.length === 1) {
        var dx = e.changedTouches[0].clientX - touchStartX;
        var dy = e.changedTouches[0].clientY - touchStartY;
        if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {
          if (dx < 0) next();
          else prev();
        }
      }
    }, { passive: true });
  }
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
  var pinnedCardRequestId = 0;

  function buildPinnedSkeleton() {
    var s = document.createElement("div");
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
    var formatted = "\u00A3" + Number(price).toLocaleString();
    var cls = "price-pill" + (isOffMarket ? " price-pill-off-market" : "");
    return L.divIcon({
      className: "price-pill-marker",
      html: '<div class="' + cls + '" data-property-id="' + id + '">' + formatted + "</div>",
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
    if (p.rating && typeof p.rating === "number" && p.rating >= 1 && p.rating <= 5) {
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
        return;
      }
      // Card not on current page — fetch and pin it at top of results
      var prev = resultsEl.querySelector(".pinned-card");
      if (prev) prev.remove();

      var thisRequest = ++pinnedCardRequestId;
      var reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      var fadeMs = reducedMotion ? 0 : 300;

      // Build stable wrapper with skeleton inside
      var wrapper = document.createElement("div");
      wrapper.className = "pinned-card";
      var dismiss = document.createElement("button");
      dismiss.className = "pinned-card-dismiss";
      dismiss.setAttribute("aria-label", "Dismiss pinned card");
      dismiss.textContent = "\u00d7";
      dismiss.onclick = function () { wrapper.remove(); };
      wrapper.appendChild(dismiss);
      var inner = document.createElement("div");
      inner.className = "pinned-card-inner";
      inner.appendChild(buildPinnedSkeleton());
      wrapper.appendChild(inner);
      resultsEl.insertBefore(wrapper, resultsEl.firstChild);
      resultsEl.scrollTop = 0;

      fetch("/property/" + encodeURIComponent(p.id) + "/card")
        .then(function (r) {
          if (!r.ok) throw new Error(r.status);
          return r.text();
        })
        .then(function (html) {
          if (thisRequest !== pinnedCardRequestId) return;
          // Fade out skeleton
          inner.classList.add("fade-out");
          setTimeout(function () {
            if (thisRequest !== pinnedCardRequestId) return;
            var parser = new DOMParser();
            var doc = parser.parseFromString(html, "text/html");
            inner.textContent = "";
            while (doc.body.firstChild) {
              inner.appendChild(doc.body.firstChild);
            }
            inner.classList.remove("fade-out");
            var pinned = wrapper.querySelector(".property-card");
            if (pinned) {
              pinned.scrollIntoView({ behavior: "smooth", block: "center" });
              pinned.classList.add("card-highlighted");
              setTimeout(function () { pinned.classList.remove("card-highlighted"); }, 2000);
            }
          }, fadeMs);
        })
        .catch(function () {
          if (thisRequest !== pinnedCardRequestId) return;
          wrapper.remove();
          window.location.href = "/property/" + encodeURIComponent(p.id);
        });
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
      var icon = createPricePillIcon(p.price, p.id, p.is_off_market);
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
      var href = link.getAttribute("href");
      if (href === "#top") {
        window.scrollTo({ top: 0, behavior: "smooth" });
        return;
      }
      var id = href.slice(1);
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

// ("+N more" overlay is now handled inside the gallery controller above)

// Status selector popover: toggle on click, close on outside click / Escape
(function () {
  document.addEventListener("click", function (e) {
    var trigger = e.target.closest(".status-selector-trigger");
    if (trigger) {
      e.preventDefault();
      var selector = trigger.closest(".status-selector");
      var wasOpen = selector.classList.contains("open");

      // Close any other open selectors
      document.querySelectorAll(".status-selector.open").forEach(function (s) {
        s.classList.remove("open");
        s.querySelector(".status-selector-trigger").setAttribute("aria-expanded", "false");
      });

      if (!wasOpen) {
        selector.classList.add("open");
        trigger.setAttribute("aria-expanded", "true");
      }
      return;
    }

    // Close on click outside
    if (!e.target.closest(".status-popover")) {
      document.querySelectorAll(".status-selector.open").forEach(function (s) {
        s.classList.remove("open");
        s.querySelector(".status-selector-trigger").setAttribute("aria-expanded", "false");
      });
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      document.querySelectorAll(".status-selector.open").forEach(function (s) {
        s.classList.remove("open");
        var trigger = s.querySelector(".status-selector-trigger");
        trigger.setAttribute("aria-expanded", "false");
        trigger.focus();
      });
    }
  });
})();

// Dashboard status filter popover: toggle, select, close on outside click / Escape
// Uses document-level event delegation (no re-binding needed after HTMX swaps)
(function () {
  document.addEventListener("click", function (e) {
    // 1. Trigger click — toggle popover
    var trigger = e.target.closest(".status-filter-trigger");
    if (trigger) {
      e.preventDefault();
      var filter = trigger.closest(".status-filter");
      var wasOpen = filter.classList.contains("open");

      // Close any other open filters first
      document.querySelectorAll(".status-filter.open").forEach(function (f) {
        f.classList.remove("open");
        var t = f.querySelector(".status-filter-trigger");
        if (t) t.setAttribute("aria-expanded", "false");
      });

      if (!wasOpen) {
        filter.classList.add("open");
        trigger.setAttribute("aria-expanded", "true");
      }
      return;
    }

    // 2. Option click — update value, trigger label, and submit
    var option = e.target.closest(".status-filter-option");
    if (option) {
      var filter = option.closest(".status-filter");
      var trigger = filter.querySelector(".status-filter-trigger");
      var hiddenInput = filter.querySelector('input[name="status"]');
      if (!trigger || !hiddenInput) return;

      var value = option.dataset.statusValue;
      hiddenInput.value = value;

      // Update trigger appearance
      var color = option.style.getPropertyValue("--option-color") || "#888";
      trigger.style.setProperty("--status-color", color);

      if (value) {
        trigger.innerHTML =
          '<span class="status-filter-dot"></span>' +
          option.textContent.trim() +
          ' <svg class="status-filter-chevron" width="12" height="12" viewBox="0 0 12 12" fill="none">' +
          '<path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>' +
          "</svg>";
      } else {
        trigger.style.setProperty("--status-color", "#888");
        trigger.innerHTML =
          "Status" +
          ' <svg class="status-filter-chevron" width="12" height="12" viewBox="0 0 12 12" fill="none">' +
          '<path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>' +
          "</svg>";
      }

      // Update active state on all options
      var allOptions = filter.querySelectorAll(".status-filter-option");
      for (var i = 0; i < allOptions.length; i++) {
        allOptions[i].classList.toggle("active", allOptions[i].dataset.statusValue === value);
      }

      // Close and submit
      filter.classList.remove("open");
      trigger.setAttribute("aria-expanded", "false");
      var form = filter.closest("form");
      if (form) htmx.trigger(form, "submit");
      return;
    }

    // 3. Outside click — close all open filters
    if (!e.target.closest(".status-filter")) {
      document.querySelectorAll(".status-filter.open").forEach(function (f) {
        f.classList.remove("open");
        var t = f.querySelector(".status-filter-trigger");
        if (t) t.setAttribute("aria-expanded", "false");
      });
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      document.querySelectorAll(".status-filter.open").forEach(function (f) {
        f.classList.remove("open");
        var t = f.querySelector(".status-filter-trigger");
        if (t) {
          t.setAttribute("aria-expanded", "false");
          t.focus();
        }
      });
    }
  });
})();

// Flash animation on card status change via HTMX
(function () {
  document.addEventListener("htmx:afterSwap", function (e) {
    var card = e.detail.target;
    if (card && card.classList && card.classList.contains("property-card")) {
      card.classList.add("status-just-changed");
      setTimeout(function () {
        card.classList.remove("status-just-changed");
      }, 700);
    }
  });
})();

// Toggle aria-busy on #results during HTMX requests
(function () {
  var results = document.getElementById("results");
  if (!results) return;

  document.addEventListener("htmx:beforeRequest", function (e) {
    if (e.detail.target === results) {
      results.setAttribute("aria-busy", "true");
    }
  });
  document.addEventListener("htmx:afterSwap", function (e) {
    if (e.detail.target === results) {
      results.setAttribute("aria-busy", "false");
    }
  });
  document.addEventListener("htmx:responseError", function (e) {
    if (e.detail.target === results) {
      results.setAttribute("aria-busy", "false");
    }
  });
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

// Hero gallery keyboard activation (Enter/Space on hero-main/hero-thumb)
(function () {
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    var target = e.target;
    if (!target.classList) return;
    if (target.classList.contains("hero-main") || target.classList.contains("hero-thumb")) {
      e.preventDefault();
      var lightboxEl = target.querySelector("[data-lightbox]");
      if (lightboxEl) lightboxEl.click();
    }
  });
})();

// Viewing group header toggle (event delegation)
(function () {
  document.addEventListener("click", function (e) {
    var header = e.target.closest(".viewing-group-header");
    if (!header) return;
    var body = header.nextElementSibling;
    if (body) body.classList.toggle("open");
    var toggle = header.querySelector(".viewing-group-toggle");
    if (toggle) toggle.classList.toggle("expanded");
    var expanded = header.getAttribute("aria-expanded") === "true";
    header.setAttribute("aria-expanded", String(!expanded));
  });
})();
