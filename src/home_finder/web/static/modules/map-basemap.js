// Shared provider contract. This is a browser-visible key, restricted at CARTO.
export function addBasemap(map, element, onUnavailable) {
  const key = element.dataset.mapKey;
  if (!key) {
    onUnavailable();
    return;
  }
  const layer = L.tileLayer(
    "https://basemaps.cartocdn.com/rastertiles/dark_all/{z}/{x}/{y}{r}.png?key=" + encodeURIComponent(key),
    {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a> &copy; <a href="https://carto.com/attribution/">CARTO</a>',
      maxZoom: 20,
    },
  );
  let failed = false;
  layer.on("tileerror", function () {
    if (failed) return;
    failed = true;
    onUnavailable();
  });
  layer.addTo(map);
}
