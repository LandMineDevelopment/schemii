/** Parse NDJSON incrementally, including split UTF-8 characters and partial lines. */
export async function readResultStream(url, body, onFrame, signal, fetcher = fetch) {
  const response = await fetcher(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', Accept: 'application/x-ndjson' }, body: JSON.stringify(body), signal });
  if (!response.ok) { const text = await response.text(); let message = text; try { const data = JSON.parse(text); message = data.error?.message || data.detail?.message || data.detail || data.message || text; } catch {} throw new Error(typeof message === 'string' ? message : JSON.stringify(message)); }
  if (!response.body) throw new Error('Streaming responses are unavailable in this browser.');
  const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = '', ended = false;
  const consume = line => { if (!line.trim()) return; const frame = JSON.parse(line); if (ended) throw new Error('Unexpected data after stream completion.'); onFrame(frame); if (frame.type === 'end') ended = true; };
  try {
    while (true) {
      const { value, done } = await reader.read(); buffer += decoder.decode(value, { stream: !done });
      let newline; while ((newline = buffer.indexOf('\n')) >= 0) { consume(buffer.slice(0, newline)); buffer = buffer.slice(newline + 1); }
      if (buffer.length > 20 * 1024 * 1024) throw new Error('A result batch exceeded the browser safety limit.');
      if (done) break;
    }
    if (buffer.trim()) consume(buffer);
    if (!ended) throw new Error('The result stream ended unexpectedly. Refresh to run again.');
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

/** Shared across main results and drills; accounts conservatively for parsed JS values. */
export class CacheBudget {
  constructor({ bytes = 64 * 1024 * 1024, rows = 50000 } = {}) { this.limit = bytes; this.rowLimit = rows; this.bytes = 0; this.rows = 0; }
  take(row) { const bytes = JSON.stringify(row).length * 2 + row.length * 32 + 64; if (this.bytes + bytes > this.limit || this.rows >= this.rowLimit) return 0; this.bytes += bytes; this.rows++; return bytes; }
  release(bytes, rows) { this.bytes = Math.max(0, this.bytes - bytes); this.rows = Math.max(0, this.rows - rows); }
}

/** One eager stream; navigation only reads its bounded browser cache. */
export class ResultCache {
  constructor({ start, pageSize = 100, onChange = () => {}, budget = new CacheBudget() }) {
    Object.assign(this, { start, pageSize, onChange, budget, rows: [], columns: [], plan: null, started: false, closed: false, loading: false, hasMore: true, error: '', elapsedMs: 0, bytes: 0, limitReached: false });
    this.controller = new AbortController(); this.listeners = new Set();
  }
  subscribe(callback) { this.listeners.add(callback); return () => this.listeners.delete(callback); }
  notify() { this.onChange(); for (const listener of this.listeners) listener(); }
  accept(frame) {
    if (this.closed) return;
    if (frame.type === 'start') { this.started = true; this.plan = frame.plan || {}; this.snapshotAt = frame.snapshotAt; }
    if (frame.type === 'rows' && !this.limitReached) {
      this.columns = frame.columns.map((column, index) => ({ ...column, name: this.plan?.outputLabels?.[index] || column.name }));
      for (const row of frame.rows) {
        const bytes = this.rows.length < 10000 && this.bytes < 24 * 1024 * 1024 ? this.budget.take(row) : 0;
        if (!bytes) { this.limitReached = true; this.reason = 'browser_budget'; this.loading = false; this.hasMore = false; this.controller.abort(); break; }
        this.bytes += bytes; this.rows.push(row);
      }
    }
    if (frame.type === 'complete') { this.complete = true; this.limitReached ||= frame.limitReached; this.reason ||= frame.reason; this.hasMore = false; this.loading = false; }
    if (frame.type === 'error') { this.error = frame.message; this.hasMore = false; this.loading = false; }
    this.elapsedMs = Date.now() - this.startedAt; this.notify();
  }
  loadMore() {
    if (this.pending) return this.pending;
    if (this.closed || !this.hasMore) return Promise.resolve(this.snapshot());
    this.loading = true; this.startedAt = Date.now(); this.notify();
    this.pending = this.run(); return this.pending;
  }
  async run() {
    try {
      await this.start(frame => this.accept(frame), this.controller.signal);
      if (!this.closed && !this.complete && !this.error && !this.limitReached) throw new Error('The result stream did not complete.');
      if (this.error) throw new Error(this.error);
    } catch (error) { if (!this.closed && !this.limitReached) this.error = `${error.message} Cached rows remain available; refresh to run again.`; }
    finally { this.loading = false; this.hasMore = false; this.elapsedMs = Date.now() - this.startedAt; this.notify(); }
    return this.snapshot();
  }
  async page(index) { await this.loadMore(); const offset = index * this.pageSize; return { ...this.snapshot(), rows: this.rows.slice(offset, offset + this.pageSize), pageOffset: offset }; }
  snapshot() { return { plan: this.plan, columns: this.columns, rows: this.rows, elapsedMs: this.elapsedMs, pageOffset: 0, hasMore: this.hasMore, loading: this.loading, limitReached: this.limitReached, reason: this.reason, snapshotAt: this.snapshotAt, warnings: this.plan?.warnings || [], cachedRows: this.rows.length, error: this.error }; }
  async close() { if (this.closed) return; this.closed = true; if (this.loading) this.error = 'Query stopped. Cached rows remain available; refresh to run again.'; this.controller.abort(); this.loading = false; this.hasMore = false; this.notify(); }
  dispose() { if (this.disposed) return; this.disposed = true; void this.close(); this.budget.release(this.bytes, this.rows.length); this.bytes = 0; this.rows.length = 0; this.listeners.clear(); }
}

/** Fans one eager dashboard stream into independently browsable tile caches. */
export class DashboardResultGroup {
  constructor({ start }) { this.start = start; this.listeners = new Map(); this.controller = new AbortController(); }
  tile(tileId, callback) {
    this.listeners.set(tileId, callback);
    this.pending ||= Promise.resolve().then(() => this.start(frame => this.accept(frame), this.controller.signal));
    return this.pending;
  }
  accept(frame) {
    if (frame.type === 'start') {
      for (const tile of frame.tiles) this.listeners.get(tile.tileId)?.({ ...frame, plan: tile.plan });
      for (const error of frame.tileErrors || []) this.listeners.get(error.tileId)?.({ ...error, type: 'error' });
    } else if (frame.tileId) this.listeners.get(frame.tileId)?.(frame);
    else if (frame.type === 'error') for (const callback of this.listeners.values()) callback(frame);
  }
  async close() { this.controller.abort(); }
}
