// Filter form: strip empty params, chip removal, explicit apply

function setFilterValues(values) {
  const form = document.querySelector(".filter-form");
  if (!form) return;

  // A valid bookmarked bedroom count can be outside the usual compact toggles.
  const beds = values.bedrooms == null ? "" : String(values.bedrooms);
  form.querySelectorAll("[data-custom-bedrooms]").forEach(el => el.remove());
  if (![...form.querySelectorAll('[name="bedrooms"]')].some(el => el.value === beds)) {
    const input = document.createElement("input");
    Object.assign(input, {type: "radio", name: "bedrooms", id: "beds-custom", value: beds});
    input.className = "sr-only";
    input.dataset.customBedrooms = "";
    const label = document.createElement("label");
    label.htmlFor = input.id;
    label.textContent = beds;
    label.dataset.customBedrooms = "";
    form.querySelector(".beds-toggle").append(input, label);
  }

  for (const field of form.elements) {
    if (!field.name) continue;
    const value = field.name === "tag" ? values.tags || [] : values[field.name] ?? "";
    if (field.type === "checkbox") {
      field.checked = Array.isArray(value) ? value.includes(field.value) : field.value === value;
    } else if (field.type === "radio") {
      field.checked = field.value === String(value);
    } else {
      if (field.tagName === "SELECT") {
        field.querySelectorAll("[data-custom-filter]").forEach(option => option.remove());
        if (value !== "" && ![...field.options].some(option => option.value === String(value))) {
          const option = new Option(String(value), String(value));
          option.dataset.customFilter = "";
          field.add(option);
        }
      }
      field.value = value;
    }
  }
  document.dispatchEvent(new CustomEvent("filters:restored"));
}

function syncAppliedFilters() {
  const data = document.getElementById("applied-filters");
  if (!data) return;
  const applied = JSON.parse(data.textContent);
  setFilterValues(applied.values);
  const navCount = document.getElementById("nav-count");
  if (navCount) {
    navCount.textContent = applied.total + " properties";
    if (applied.active_count) {
      const detail = document.createElement("small");
      detail.textContent = "(" + applied.active_count + " filter" +
        (applied.active_count === 1 ? "" : "s") + " active)";
      navCount.append(" ", detail);
    }
  }
  const reset = document.querySelector(".filter-reset");
  if (reset) reset.hidden = applied.active_count === 0;
  const badge = document.querySelector("#open-filters-btn .filter-badge");
  if (badge) {
    badge.textContent = applied.secondary_count;
    badge.hidden = applied.secondary_count === 0;
  }
  const count = document.getElementById("modal-count");
  if (count) count.textContent = applied.total;
}

syncAppliedFilters();
// Native history can restore unsaved form values; applied server state wins.
window.addEventListener("pageshow", syncAppliedFilters);
document.addEventListener("htmx:afterSwap", function (e) {
  if (e.detail.target.id === "results") syncAppliedFilters();
});

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
    // Clear every filter, including status/assessment/size, before explicit Apply.
    setFilterValues({sort: "newest", tags: []});
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
