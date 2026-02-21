// Detail page Leaflet map (single property)

const el = document.getElementById("map");
if (el) {
  const lat = parseFloat(el.dataset.lat);
  const lon = parseFloat(el.dataset.lon);
  const title = el.dataset.title || "Property";

  if (!isNaN(lat) && !isNaN(lon)) {
    const map = L.map("map", {
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
}
