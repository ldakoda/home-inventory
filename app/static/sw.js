// Minimal service worker: no offline caching, just satisfies PWA installability
// (Chrome requires a registered service worker with a fetch handler before it
// will offer the install prompt). Every request passes straight through.
self.addEventListener("fetch", () => {});
