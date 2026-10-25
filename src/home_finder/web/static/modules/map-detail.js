// Detail page Leaflet map (single property)
const { addBasemap } = await import("./map-basemap.js" + new URL(import.meta.url).search);

const el = document.getElementById("map");
if (el && typeof L !== "undefined") {
  const lat = parseFloat(el.dataset.lat);
  const lon = parseFloat(el.dataset.lon);
  const title = el.dataset.title || "Property";

  if (!isNaN(lat) && !isNaN(lon)) {
    const map = L.map("map", {
      scrollWheelZoom: false,
      zoomControl: false,
      boxZoom: false,
    }).setView([lat, lon], 15);

    addBasemap(map, el, function () {
      map.remove();
      el.hidden = true;
      document.getElementById("map-status").hidden = false;
    });

    L.control.zoom({ position: "bottomright" }).addTo(map);

    // Enable scroll/pinch zoom after clicking the map (prevents scroll hijacking)
    map.on("click", function () {
      map.scrollWheelZoom.enable();
    });
    map.getContainer().addEventListener("mouseleave", function () {
      map.scrollWheelZoom.disable();
    });

    // Subtle area radius, in the page's ink. Resolved here rather than passed as
    // var(--ink): the SVG renderer would accept that, a canvas renderer would not.
    const ink = getComputedStyle(document.documentElement).getPropertyValue("--ink").trim();
    L.circle([lat, lon], {
      radius: 500,
      color: ink,
      fillColor: ink,
      fillOpacity: 0.04,
      weight: 1,
      opacity: 0.15,
      interactive: false,
    }).addTo(map);

    // Ink pin marker with pulse
    const markerIcon = L.divIcon({
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
  }
} else if (el) {
  el.hidden = true;
  document.getElementById("map-status").hidden = false;
}
