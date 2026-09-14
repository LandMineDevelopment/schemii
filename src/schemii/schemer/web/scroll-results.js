import { installAutoPageLoader } from '#common/data-grid.js';

const loaders = new WeakMap();
export function scrollPosition(host) {
  const viewport = host.querySelector('.data-grid-viewport') || host;
  return { top: viewport.scrollTop, left: viewport.scrollLeft };
}
export function restoreScroll(host, position) {
  const viewport = host.querySelector('.data-grid-viewport') || host;
  viewport.scrollTop = position.top; viewport.scrollLeft = position.left;
}
export function autoLoadRows(host, stream, loadNext, canLoad = () => true) {
  loaders.get(host)?.destroy();
  const viewport = host.querySelector('.data-grid-viewport');
  if (!viewport) return;
  viewport.tabIndex = 0;
  viewport.setAttribute('aria-label', 'Report rows; scroll to load more');
  const loader = installAutoPageLoader({
    container: viewport,
    canLoad: () => viewport.isConnected && viewport.clientHeight > 0 && stream?.hasMore && !stream.loading && !stream.closed && canLoad(),
    loadNext,
  });
  loaders.set(host, loader);
  loader.check();
}
