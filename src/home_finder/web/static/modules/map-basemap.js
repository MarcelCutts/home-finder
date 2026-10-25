// Shared provider contract. This is a browser-visible key, restricted at CARTO.
// The tiles match the page theme as it stands when the map is created. style.css
// sets color-scheme with each token set, so reading it back is the one rule.
function prefersDarkTiles() {
  return getComputedStyle(document.documentElement).colorScheme === "dark";
}

export function addBasemap(map, element, onUnavailable) {
  const key = element.dataset.mapKey;
  if (!key) {
    onUnavailable();
    return;
  }
  const tileStyle = prefersDarkTiles() ? "dark_all" : "light_all";
  const layer = L.tileLayer(
    "https://basemaps.cartocdn.com/rastertiles/" + tileStyle + "/{z}/{x}/{y}{r}.png?key=" + encodeURIComponent(key),
    {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a> &copy; <a href="https://carto.com/attribution/">CARTO</a>',
      maxZoom: 20,
    },
  );
  // A single missing tile does not make the whole map unusable. Leaflet fires
  // load after the initial visible tiles have either loaded or failed.
  let hasLoadedTile = false;
  layer.on("tileload", function () { hasLoadedTile = true; });
  layer.once("load", function () {
    if (!hasLoadedTile) onUnavailable();
  });
  layer.addTo(map);
}
