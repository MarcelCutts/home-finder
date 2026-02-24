// Filter form: strip empty params, chip removal, explicit apply

// Strip empty-string params before HTMX sends request
document.addEventListener("htmx:configRequest", function (e) {
  const params = e.detail.parameters;
  const keys = Object.keys(params);
  for (let i = 0; i < keys.length; i++) {
    if (params[keys[i]] === "") {
      delete params[keys[i]];
    }
  }
  // Reset to page 1 when filter form triggers (not pagination links)
  const trigger = e.detail.elt;
  if (trigger && trigger.closest && trigger.closest(".filter-form")) {
    delete params["page"];
  }
});

// Inside modal: dispatch filterChange for live count update (no form submit)
document.addEventListener("change", function (e) {
  const inDialog = e.target.closest("dialog");
  if (inDialog) {
    inDialog.dispatchEvent(new CustomEvent("filterChange", { bubbles: true }));
  }
});

// Chip removal: clear field and submit
document.addEventListener("click", function (e) {
  const btn = e.target.closest(".filter-chip-remove[data-filter-key]");
  if (!btn) return;
  const key = btn.dataset.filterKey;
  const value = btn.dataset.filterValue;
  const form = document.querySelector(".filter-form");
  if (!form) return;

  if (key === "tag" && value) {
    // Uncheck the matching tag checkbox
    const checkboxes = form.querySelectorAll('input[name="tag"]');
    for (let i = 0; i < checkboxes.length; i++) {
      if (checkboxes[i].value === value) {
        checkboxes[i].checked = false;
      }
    }
  } else if (key === "bedrooms") {
    // Reset beds toggle to "Any"
    const anyRadio = form.querySelector('input[name="bedrooms"][value=""]');
    if (anyRadio) anyRadio.checked = true;
  } else if (key === "off_market") {
    const cb = form.querySelector('input[name="off_market"]');
    if (cb) cb.checked = false;
  } else {
    const field = form.querySelector('[name="' + key + '"]');
    if (field) {
      field.value = "";
    }
  }
  htmx.trigger(form, "submit");
});

// Filter modal: open, close, reset, apply
const dialog = document.getElementById("filter-modal");
const openBtn = document.getElementById("open-filters-btn");
const primaryFilters = document.getElementById("primary-filters");
const dialogSlot = document.getElementById("dialog-primary-slot");
const filterControls = document.querySelector(".filter-controls");
const isMobile = window.matchMedia("(max-width: 768px)");

if (dialog && openBtn) {
  openBtn.addEventListener("click", function () {
    if (isMobile.matches && primaryFilters && dialogSlot) {
      dialogSlot.appendChild(primaryFilters);
    }
    dialog.showModal();
  });

  dialog.querySelector(".filter-modal-close").addEventListener("click", function () {
    if (primaryFilters && primaryFilters.parentElement === dialogSlot) {
      filterControls.insertBefore(primaryFilters, filterControls.firstChild);
    }
    dialog.close();
  });

  dialog.querySelector(".filter-modal-reset").addEventListener("click", function () {
    const form = document.querySelector(".filter-form");
    // Clear all selects and checkboxes inside the dialog
    const selects = dialog.querySelectorAll("select");
    for (let i = 0; i < selects.length; i++) selects[i].value = "";
    const checkboxes = dialog.querySelectorAll('input[type="checkbox"]');
    for (let j = 0; j < checkboxes.length; j++) checkboxes[j].checked = false;
    // Reset primary filters (works whether in dialog or filter bar)
    if (primaryFilters) {
      const anyBeds = primaryFilters.querySelector('input[name="bedrooms"][value=""]');
      if (anyBeds) anyBeds.checked = true;
      const minP = primaryFilters.querySelector('input[name="min_price"]');
      if (minP) minP.value = "";
      const maxP = primaryFilters.querySelector('input[name="max_price"]');
      if (maxP) maxP.value = "";
      const fitScore = primaryFilters.querySelector('select[name="min_fit_score"]');
      if (fitScore) fitScore.value = "";
    }
    if (form) {
      const deskArea = form.querySelector('select[name="area"]');
      if (deskArea) deskArea.value = "";
      const deskAdded = form.querySelector('select[name="added"]');
      if (deskAdded) deskAdded.value = "";
    }
    dialog.dispatchEvent(new CustomEvent("filterChange", { bubbles: true }));
  });

  // Apply: relocate primary filters back, close dialog, then submit form
  dialog.querySelector(".filter-modal-apply").addEventListener("click", function (e) {
    e.preventDefault();
    if (primaryFilters && primaryFilters.parentElement === dialogSlot) {
      filterControls.insertBefore(primaryFilters, filterControls.firstChild);
    }
    dialog.close();
    const form = document.querySelector(".filter-form");
    if (form) htmx.trigger(form, "submit");
  });
}

// Toggle aria-busy on #results during HTMX requests
{
  const results = document.getElementById("results");
  if (results) {
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
  }
}

// Scroll to top of results after HTMX pagination swap
{
  const results = document.getElementById("results");
  if (results) {
    document.addEventListener("htmx:afterSettle", function (e) {
      if (e.detail.target !== results) return;
      const trigger = e.detail.requestConfig && e.detail.requestConfig.elt;
      if (trigger && trigger.closest && trigger.closest(".pagination")) {
        results.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  }
}

// Focus management after HTMX results swap
{
  const results = document.getElementById("results");
  if (results) {
    document.addEventListener("htmx:afterSettle", function (e) {
      if (e.detail.target !== results) return;
      const firstLink = results.querySelector(".property-card a");
      if (firstLink) firstLink.focus({ preventScroll: true });
    });
  }
}

// HTMX error handling: show toast for network/server errors
{
  function showToast(msg) {
    const toast = document.getElementById("error-toast");
    if (!toast) return;
    toast.textContent = "";
    const p = document.createElement("p");
    p.className = "toast-message";
    p.textContent = msg;
    toast.appendChild(p);
    clearTimeout(toast._timer);
    toast._timer = setTimeout(function () { toast.textContent = ""; }, 5000);
  }

  // Auto-dismiss response-targets content too
  document.addEventListener("htmx:beforeSwap", function (e) {
    if (e.detail.target && e.detail.target.id === "error-toast" && e.detail.xhr && e.detail.xhr.status >= 400) {
      clearTimeout(e.detail.target._timer);
      e.detail.target._timer = setTimeout(function () { e.detail.target.textContent = ""; }, 5000);
    }
  });

  document.addEventListener("htmx:responseError", function (e) {
    showToast("Something went wrong. Please try again.");
  });

  document.addEventListener("htmx:sendError", function (e) {
    showToast("Network error. Check your connection.");
  });
}
