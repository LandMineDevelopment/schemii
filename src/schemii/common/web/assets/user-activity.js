// One shared signal for intentional product use. Never an idle heartbeat.
export function trackUserActivity({ document, window, fetch, now = Date.now }) {
  let lastSent = -Infinity;
  let pending = false;
  async function record(event) {
    if (document.visibilityState !== 'visible' || (event && !event.isTrusted)) return;
    if (pending || now() - lastSent < 60_000) return;
    pending = true;
    lastSent = now();
    try {
      await fetch('/api/v1/activity', { method: 'POST', cache: 'no-store' });
    } catch { /* No background retry: the next user interaction may try again. */ }
    finally { pending = false; }
  }
  const events = ['pointerdown', 'keydown', 'wheel', 'touchstart'];
  for (const name of events) document.addEventListener(name, record, { passive: true });
  const visible = event => record(event);
  document.addEventListener('visibilitychange', visible);
  window.addEventListener('pageshow', visible);
  record();
  return () => {
    for (const name of events) document.removeEventListener(name, record);
    document.removeEventListener('visibilitychange', visible);
    window.removeEventListener('pageshow', visible);
  };
}

if (typeof document !== 'undefined') {
  trackUserActivity({ document, window, fetch: window.fetch.bind(window) });
}
