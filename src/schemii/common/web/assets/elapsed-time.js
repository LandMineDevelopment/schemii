/** Human-readable wall time; this is not PostgreSQL's measured execution time. */
export function formatElapsed(milliseconds) {
  const seconds = Math.max(0, Number(milliseconds) || 0) / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${(seconds % 60).toFixed(1)}s`;
}

export function createElapsedTimer({ onTick, now = () => performance.now(), schedule = globalThis.setInterval, clear = globalThis.clearInterval }) {
  let started = null;
  let elapsed = 0;
  let interval = null;
  const value = () => started === null ? elapsed : Math.max(0, now() - started);
  const tick = () => onTick(value());
  const stop = () => {
    elapsed = value();
    started = null;
    if (interval !== null) clear(interval);
    interval = null;
    tick();
    return elapsed;
  };
  return {
    start() { stop(); elapsed = 0; started = now(); tick(); interval = schedule(tick, 100); },
    stop,
    value,
    get running() { return started !== null; },
  };
}
