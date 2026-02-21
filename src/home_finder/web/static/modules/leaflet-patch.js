// Global error handlers
window.onerror = function (msg, src, line, col, err) {
  console.error("[app] Uncaught error:", msg, "at", src + ":" + line + ":" + col, err);
};
window.addEventListener("unhandledrejection", function (e) {
  console.error("[app] Unhandled promise rejection:", e.reason);
});

// Fix Leaflet tile gap rendering (white lines between tiles)
// https://github.com/Leaflet/Leaflet/issues/3575
// Still needed as of Leaflet 1.9.4 — revisit when upgrading to 2.x
const _origInitTile = L.GridLayer.prototype._initTile;
L.GridLayer.include({
  _initTile: function (tile) {
    _origInitTile.call(this, tile);
    const tileSize = this.getTileSize();
    tile.style.width = tileSize.x + 1 + "px";
    tile.style.height = tileSize.y + 1 + "px";
  },
});
