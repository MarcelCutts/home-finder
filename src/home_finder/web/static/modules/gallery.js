// Gallery view: full-page overlay with filmstrip, tabs, crossfade, keyboard/touch

const gallery = document.getElementById("gallery-view");
if (gallery) {
  const mainImg = gallery.querySelector(".gallery-main-img");
  const nextImg = gallery.querySelector(".gallery-main-img-next");
  const counter = gallery.querySelector(".gallery-counter");
  const filmstrip = gallery.querySelector(".gallery-filmstrip");
  const stage = gallery.querySelector(".gallery-stage");
  const spinner = gallery.querySelector(".gallery-spinner");
  const thumbs = filmstrip ? Array.from(filmstrip.querySelectorAll(".gallery-filmstrip-thumb")) : [];
  const imageSrcs = thumbs.map(function (t) { return t.dataset.src; });
  const tabs = Array.from(gallery.querySelectorAll(".gallery-tab"));
  const panels = Array.from(gallery.querySelectorAll(".gallery-panel"));

  let currentIndex = 0;
  let navigateId = 0;
  let crossfadeTimer = null;
  let imageCache = {};  // src -> Image object (holding ref prevents GC/cache eviction)
  let touchStartX = 0;
  let touchStartY = 0;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Collect all hero/page [data-lightbox] images to map click -> gallery index
  const heroImages = Array.from(document.querySelectorAll("[data-lightbox]"));

  async function loadImage(src) {
    if (imageCache[src]) return true;
    const img = new Image();
    img.src = src;
    try {
      await img.decode();
      imageCache[src] = img;
      return true;
    } catch {
      return false;
    }
  }

  function showSpinner() {
    if (spinner) spinner.hidden = false;
  }

  function hideSpinner() {
    if (spinner) spinner.hidden = true;
  }

  function updateFilmstrip(index) {
    thumbs.forEach(function (t, i) {
      const isActive = i === index;
      t.classList.toggle("active", isActive);
      t.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    if (filmstrip && thumbs[index]) {
      const thumb = thumbs[index];
      const scrollLeft = thumb.offsetLeft - filmstrip.clientWidth / 2 + thumb.offsetWidth / 2;
      filmstrip.scrollLeft = scrollLeft;
    }
  }

  function updateCounter(index) {
    if (counter) {
      counter.textContent = (index + 1) + " / " + imageSrcs.length;
    }
  }

  async function doSwap(src, index, animate, navId) {
    if (!mainImg) return;
    clearTimeout(crossfadeTimer);
    mainImg.classList.remove("crossfade-out");
    if (nextImg) nextImg.classList.remove("crossfade-in");

    if (animate && nextImg) {
      nextImg.src = src;
      nextImg.alt = "Property photo " + (index + 1);

      try { await nextImg.decode(); } catch {}
      if (navId !== navigateId) return;
      mainImg.classList.add("crossfade-out");
      nextImg.classList.add("crossfade-in");
      await new Promise(function (r) { crossfadeTimer = setTimeout(r, 200); });
      if (navId !== navigateId) return;
      mainImg.src = src;
      mainImg.alt = "Property photo " + (index + 1);

      try { await mainImg.decode(); } catch {}
      if (navId !== navigateId) return;
      // Disable transition -- this is a bookkeeping swap, not a visual crossfade.
      mainImg.style.transition = "none";
      nextImg.style.transition = "none";
      mainImg.classList.remove("crossfade-out");
      nextImg.classList.remove("crossfade-in");
      // Force reflow so the browser commits the instant opacity change.
      void mainImg.offsetHeight;
      // Re-enable CSS transitions for the next crossfade.
      mainImg.style.transition = "";
      nextImg.style.transition = "";
    } else {
      mainImg.src = src;
      mainImg.alt = "Property photo " + (index + 1);
      mainImg.decode().catch(function () {});
    }
  }

  function preloadAround(index) {
    for (let offset = 1; offset <= 3; offset++) {
      const fwd = (index + offset) % imageSrcs.length;
      const bwd = (index - offset + imageSrcs.length) % imageSrcs.length;
      if (fwd !== index) loadImage(imageSrcs[fwd]);
      if (bwd !== index && bwd !== fwd) loadImage(imageSrcs[bwd]);
    }
  }

  async function showImage(index, animate) {
    if (imageSrcs.length === 0) return;
    index = ((index % imageSrcs.length) + imageSrcs.length) % imageSrcs.length;
    currentIndex = index;
    const thisNav = ++navigateId;

    updateFilmstrip(index);
    updateCounter(index);

    const src = imageSrcs[index];

    if (imageCache[src]) {
      hideSpinner();
      doSwap(src, index, animate && !reducedMotion, thisNav);
      preloadAround(index);
      return;
    }

    showSpinner();
    const ok = await loadImage(src);
    if (thisNav !== navigateId) return;
    hideSpinner();
    doSwap(src, index, false, thisNav);
    if (!ok && mainImg) mainImg.alt = "Image unavailable";
    preloadAround(index);
  }

  function open(index, tab) {
    tab = tab || "photos";
    gallery.showModal();
    switchTab(tab);
    if (tab === "photos" && imageSrcs.length > 0) {
      showImage(index, false);
    }
    gallery.querySelector(".gallery-close").focus();
  }

  function close() {
    gallery.close();
  }

  function switchTab(tabName) {
    tabs.forEach(function (t) {
      const isActive = t.dataset.tab === tabName;
      t.classList.toggle("active", isActive);
      t.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    panels.forEach(function (p) {
      const panelTab = p.id.replace("gallery-panel-", "");
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
    const moreOverlay = e.target.closest("[data-gallery-more]");
    if (moreOverlay) {
      e.preventDefault();
      e.stopPropagation();
      const startIdx = parseInt(moreOverlay.dataset.galleryMore, 10);
      open(startIdx, "photos");
      return;
    }

    const target = e.target.closest("[data-lightbox]");
    if (!target) return;
    e.preventDefault();

    if (target.dataset.lightbox === "floorplan") {
      open(0, "floorplan");
      return;
    }

    // Hero gallery image -- find matching index in filmstrip by src
    const clickedSrc = target.tagName === "IMG" ? target.src : (target.querySelector("img") || {}).src;
    let galleryIdx = 0;
    if (clickedSrc) {
      for (let i = 0; i < imageSrcs.length; i++) {
        // Compare by checking if src ends with same path
        if (clickedSrc === imageSrcs[i] || clickedSrc.indexOf(imageSrcs[i]) >= 0 || imageSrcs[i].indexOf(clickedSrc.split("/").pop()) >= 0) {
          galleryIdx = i;
          break;
        }
      }
      // Fallback: use position in hero images array
      if (galleryIdx === 0 && heroImages.indexOf(target) > 0) {
        const heroIdx = heroImages.indexOf(target);
        if (heroIdx < imageSrcs.length) galleryIdx = heroIdx;
      }
    }
    open(galleryIdx, "photos");
  });

  // Close button
  gallery.querySelector(".gallery-close").addEventListener("click", close);

  // Cleanup on native dialog close (Escape or .close())
  gallery.addEventListener("close", function () {
    clearTimeout(crossfadeTimer);
    hideSpinner();
    if (mainImg) { mainImg.classList.remove("crossfade-out"); mainImg.src = ""; }
    if (nextImg) { nextImg.classList.remove("crossfade-in"); nextImg.src = ""; }
    imageCache = {};
  });

  // Nav arrows
  const prevBtn = gallery.querySelector(".gallery-prev");
  const nextBtn = gallery.querySelector(".gallery-next");
  if (prevBtn) prevBtn.addEventListener("click", prev);
  if (nextBtn) nextBtn.addEventListener("click", next);

  // Filmstrip thumb clicks
  if (filmstrip) {
    filmstrip.addEventListener("click", function (e) {
      const thumb = e.target.closest(".gallery-filmstrip-thumb");
      if (!thumb) return;
      const idx = parseInt(thumb.dataset.index, 10);
      if (!isNaN(idx)) showImage(idx, true);
    });
  }

  // Tab clicks
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      switchTab(tab.dataset.tab);
    });
  });

  // Keyboard: arrow navigation (Escape + focus trap handled natively by <dialog>)
  document.addEventListener("keydown", function (e) {
    if (!gallery.open) return;
    if (e.key === "ArrowLeft") { prev(); return; }
    if (e.key === "ArrowRight") { next(); return; }
  });

  // Pointer swipe support on stage
  if (stage) {
    stage.addEventListener("pointerdown", function (e) {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      touchStartX = e.clientX;
      touchStartY = e.clientY;
    });
    stage.addEventListener("pointerup", function (e) {
      const dx = e.clientX - touchStartX;
      const dy = e.clientY - touchStartY;
      if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {
        if (dx < 0) next(); else prev();
      }
    });
  }
}

// Hero gallery keyboard activation (Enter/Space on hero-main/hero-thumb)
document.addEventListener("keydown", function (e) {
  if (e.key !== "Enter" && e.key !== " ") return;
  const target = e.target;
  if (!target.classList) return;
  if (target.classList.contains("hero-main") || target.classList.contains("hero-thumb")) {
    e.preventDefault();
    const lightboxEl = target.querySelector("[data-lightbox]");
    if (lightboxEl) lightboxEl.click();
  }
});
