// Dashboard status filter: option selection business logic
// Toggle/outside-click/Escape handled by native popover; this handles option -> hidden input -> form submit
document.addEventListener("click", function (e) {
  const option = e.target.closest(".status-filter-option");
  if (!option) return;

  const filter = option.closest(".status-filter");
  const trigger = filter.querySelector(".status-filter-trigger");
  const hiddenInput = filter.querySelector('input[name="status"]');
  if (!trigger || !hiddenInput) return;

  const value = option.dataset.statusValue;
  hiddenInput.value = value;

  // Update trigger appearance
  const color = option.style.getPropertyValue("--option-color") || "#888";
  trigger.style.setProperty("--status-color", color);

  function buildChevronSvg() {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "status-filter-chevron");
    svg.setAttribute("width", "12");
    svg.setAttribute("height", "12");
    svg.setAttribute("viewBox", "0 0 12 12");
    svg.setAttribute("fill", "none");
    var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "M3 4.5L6 7.5L9 4.5");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "1.5");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    svg.appendChild(path);
    return svg;
  }

  if (value) {
    trigger.textContent = "";
    var dot = document.createElement("span");
    dot.className = "status-filter-dot";
    trigger.appendChild(dot);
    trigger.appendChild(document.createTextNode(option.textContent.trim() + " "));
    trigger.appendChild(buildChevronSvg());
  } else {
    trigger.style.setProperty("--status-color", "#888");
    trigger.textContent = "Status ";
    trigger.appendChild(buildChevronSvg());
  }

  // Update active state on all options
  const allOptions = filter.querySelectorAll(".status-filter-option");
  for (let i = 0; i < allOptions.length; i++) {
    allOptions[i].classList.toggle("active", allOptions[i].dataset.statusValue === value);
  }

  // Close popover and submit
  const popoverEl = filter.querySelector("[popover]");
  if (popoverEl) popoverEl.hidePopover();
  const form = filter.closest("form");
  if (form) htmx.trigger(form, "submit");
});

// Flash animation on card status change + screen reader announcement + focus preservation
{
  let swappingCardId = null;

  document.addEventListener("htmx:beforeRequest", function (e) {
    const target = e.detail.target;
    if (target && target.classList && target.classList.contains("property-card")) {
      swappingCardId = target.dataset.propertyId || null;
    }
  });

  document.addEventListener("htmx:afterSwap", function (e) {
    const card = e.detail.target;
    if (card && card.classList && card.classList.contains("property-card")) {
      card.classList.add("status-just-changed");
      setTimeout(function () {
        card.classList.remove("status-just-changed");
      }, 700);
      // Focus preservation: restore focus to card link after swap
      if (swappingCardId && card.dataset.propertyId === swappingCardId) {
        const link = card.querySelector(".card-link");
        if (link) link.focus({ preventScroll: true });
        swappingCardId = null;
      }
    }
    // Announce status changes to screen readers
    const wrap = card && card.classList && card.classList.contains("status-selector-wrap")
      ? card
      : (card && card.closest ? card.closest(".status-selector-wrap") : null);
    if (wrap) {
      const label = wrap.querySelector(".status-selector-trigger");
      if (label) {
        const announcer = document.getElementById("sr-announcer");
        if (announcer) announcer.textContent = "Status changed to " + label.textContent.trim();
      }
    }
  });
}

// Arrow-key navigation in status menus
{
  // Focus first menuitem when a popover with role="menu" opens
  document.addEventListener("toggle", function (e) {
    const popover = e.target;
    if (!popover.hasAttribute("popover") || popover.getAttribute("role") !== "menu") return;
    if (e.newState === "open") {
      const first = popover.querySelector('[role="menuitem"]:not([disabled])');
      if (first) first.focus();
    }
  }, true);

  // Keyboard navigation within menus
  document.addEventListener("keydown", function (e) {
    const item = e.target.closest('[role="menuitem"]');
    if (!item) return;
    const menu = item.closest('[role="menu"]');
    if (!menu) return;

    const items = Array.from(menu.querySelectorAll('[role="menuitem"]:not([disabled])'));
    const idx = items.indexOf(item);

    if (e.key === "ArrowDown") {
      e.preventDefault();
      const next = items[(idx + 1) % items.length];
      if (next) next.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      const prev = items[(idx - 1 + items.length) % items.length];
      if (prev) prev.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      if (items[0]) items[0].focus();
    } else if (e.key === "End") {
      e.preventDefault();
      if (items[items.length - 1]) items[items.length - 1].focus();
    } else if (e.key === "Tab") {
      // Close popover on Tab
      if (menu.hasAttribute("popover")) {
        menu.hidePopover();
      }
    }
  });
}

// Fit badge stopPropagation: prevents card navigation when clicking fit badge
function attachFitBadgeStopProp(root) {
  root.querySelectorAll(".fit-badge[popovertarget]").forEach(function(btn) {
    if (btn._fitStopProp) return;
    btn._fitStopProp = true;
    btn.addEventListener("click", function(e) { e.stopPropagation(); });
  });
}
attachFitBadgeStopProp(document);
document.addEventListener("htmx:afterSwap", function(e) {
  attachFitBadgeStopProp(e.detail.target);
});
