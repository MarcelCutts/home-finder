// Fit breakdown bar fill animation (detail page)
{
  const bars = document.querySelectorAll(".fit-breakdown-fill");
  if ("IntersectionObserver" in window && bars.length > 0) {
    const observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("animate");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.3 });

    bars.forEach(function (bar) { observer.observe(bar); });
  }
}

// Fit factor chips: tap-to-toggle on touch devices
{
  if (window.matchMedia("(pointer: coarse)").matches) {
    const groups = document.querySelectorAll(".fit-breakdown-group");
    if (groups.length > 0) {
      groups.forEach(function (group) {
        group.addEventListener("click", function () {
          groups.forEach(function (g) {
            if (g !== group) g.classList.remove("expanded");
          });
          group.classList.toggle("expanded");
        });
      });
    }
  }
}

// Section navigation: sticky shadow + active link highlighting
{
  const nav = document.getElementById("section-nav");
  if (nav) {
    const links = nav.querySelectorAll(".section-nav-links a");
    const sections = [];
    links.forEach(function (link) {
      const id = link.getAttribute("href").slice(1);
      const section = document.getElementById(id);
      if (section) sections.push({ el: section, link: link });
    });

    if (sections.length > 0) {
      // Highlight active section link via IntersectionObserver
      const observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            links.forEach(function (l) { l.classList.remove("active"); });
            for (let i = 0; i < sections.length; i++) {
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
          const href = link.getAttribute("href");
          if (href === "#top") {
            window.scrollTo({ top: 0, behavior: "smooth" });
            return;
          }
          const id = href.slice(1);
          const target = document.getElementById(id);
          if (target) target.scrollIntoView({ behavior: "smooth" });
        });
      });

      // Toggle sticky shadow on scroll
      let lastScrollY = 0;
      window.addEventListener("scroll", function () {
        const y = window.scrollY;
        if (y > 100 && lastScrollY <= 100) nav.classList.add("sticky");
        else if (y <= 100 && lastScrollY > 100) nav.classList.remove("sticky");
        lastScrollY = y;
      }, { passive: true });
    }
  }
}

// Description expand/collapse
{
  const text = document.getElementById("description-text");
  const btn = document.getElementById("description-toggle");
  if (text && btn) {
    // Show button only if content overflows
    requestAnimationFrame(function () {
      if (text.scrollHeight > text.clientHeight + 10) {
        btn.hidden = false;
      }
    });

    btn.addEventListener("click", function () {
      const expanded = text.classList.toggle("expanded");
      btn.textContent = expanded ? "Show less" : "Read more";
      btn.setAttribute("aria-expanded", String(expanded));
    });
  }
}

// Viewing group header toggle (event delegation)
document.addEventListener("click", function (e) {
  const header = e.target.closest(".viewing-group-header");
  if (!header) return;
  const body = header.nextElementSibling;
  if (body) body.classList.toggle("open");
  const toggle = header.querySelector(".viewing-group-toggle");
  if (toggle) toggle.classList.toggle("expanded");
  const expanded = header.getAttribute("aria-expanded") === "true";
  header.setAttribute("aria-expanded", String(!expanded));
});

// Scroll to highlighted element (area page)
const highlighted = document.getElementById("highlighted");
if (highlighted) highlighted.scrollIntoView({ behavior: "smooth", block: "center" });
