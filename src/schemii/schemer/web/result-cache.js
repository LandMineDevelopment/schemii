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

const decodedRowBytes = row => JSON.stringify(row).length * 2 + row.length * 32 + 64;

/** Deliberate attachment boundary. Navigation keeps using the live cache; AI gets
 * a deep frozen, independently bounded copy with no cursor or query capability. */
export function freezeDisplayedResult(result, provenance, { maxRows = 100, maxBytes = 256 * 1024 } = {}) {
  if (!result.snapshotAt) throw new Error('Wait for the result snapshot before attaching displayed rows.');
  const copy = value => JSON.parse(JSON.stringify(value));
  const attachment = copy({ ...provenance, snapshotAt: result.snapshotAt,
    metric: result.metric || 'values', barGroupIndex: result.barGroupIndex || 0,
    cachedRows: result.cachedRows ?? result.rows.length, displayedRows: result.rows.length,
    partial: Boolean(result.partial || result.loading || result.hasMore || result.error || result.limitReached),
    cacheTruncated: Boolean(result.limitReached), chartTruncated: Boolean(result.visualTruncated),
    clientSampleTruncated: Boolean(result.rows.length),
    ...(result.projection ? { projection: result.projection } : {}),
    columns: result.columns.map(column => ({ name: String(column.name), type: String(column.dataType || column.type || 'unknown') })), rows: [] });
  const bytes = () => new TextEncoder().encode(JSON.stringify(attachment)).length;
  if (bytes() + 1 > maxBytes) throw new Error('Displayed column or filter metadata exceeds the attachment limit. Choose fewer fields.');
  for (const row of result.rows.slice(0, maxRows)) {
    attachment.rows.push(copy(row));
    if (bytes() + 1 > maxBytes) { attachment.rows.pop(); break; }
  }
  attachment.clientSampleTruncated = attachment.rows.length < result.rows.length;
  const freeze = value => { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; };
  return freeze(attachment);
}

/** Shared decoded capacity, with protected initial shares for main tiles. */
export class CacheBudget {
  constructor({ bytes = 64 * 1024 * 1024, rows = 50000 } = {}) {
    this.limit = bytes; this.rowLimit = rows; this.bytes = 0; this.rows = 0; this.reservations = new Set();
  }
  spare(kind) {
    const limit = kind === 'bytes' ? this.limit : this.rowLimit;
    return Math.max(0, limit - this[kind] - [...this.reservations].reduce((sum, view) => sum + view[kind], 0));
  }
  get available() { return this.spare('bytes') > 0 && this.spare('rows') > 0; }
  take(row) { return this.consume(row); }
  consume(row, view = null) {
    const bytes = decodedRowBytes(row);
    if (bytes > (view?.bytes || 0) + this.spare('bytes') || 1 > (view?.rows || 0) + this.spare('rows')) return 0;
    if (view) { view.bytes = Math.max(0, view.bytes - bytes); view.rows = Math.max(0, view.rows - 1); view.usedBytes += bytes; view.usedRows++; }
    this.bytes += bytes; this.rows++; return bytes;
  }
  release(bytes, rows) { this.bytes = Math.max(0, this.bytes - bytes); this.rows = Math.max(0, this.rows - rows); }
  register(ids) {
    const views = new Map([...new Set(ids)].map(id => {
      const root = this;
      const view = {
        bytes: 0, rows: 0, usedBytes: 0, usedRows: 0, active: true, reason: 'browser_dashboard_budget',
        get available() { return this.active && this.bytes + root.spare('bytes') > 0 && this.rows + root.spare('rows') > 0; },
        take(row) { return this.active ? root.consume(row, this) : 0; },
        finish() { if (!this.active) return; this.active = false; this.bytes = 0; this.rows = 0; root.reservations.delete(this); },
        release(bytes, rows) { this.finish(); root.release(bytes, rows); },
      };
      this.reservations.add(view); return [id, view];
    }));
    this.rebalance();
    return views;
  }
  rebalance() {
    for (const kind of ['bytes', 'rows']) {
      const cap = kind === 'bytes' ? 24 * 1024 * 1024 : 10000;
      const used = kind === 'bytes' ? 'usedBytes' : 'usedRows';
      let views = [...this.reservations], remaining = Math.max(0, (kind === 'bytes' ? this.limit : this.rowLimit) - this[kind]);
      for (const view of views) view[kind] = 0;
      while (views.length && remaining) {
        const portion = Math.floor(remaining / views.length), extra = remaining % views.length;
        let distributed = 0;
        views.forEach((view, index) => {
          const amount = Math.min(portion + (index < extra ? 1 : 0), cap - view[used] - view[kind]);
          view[kind] += amount; distributed += amount;
        });
        remaining -= distributed;
        views = views.filter(view => view[used] + view[kind] < cap);
      }
    }
  }
}

/** Bound retained selections even when their empty/error results consume no rows. */
export class DrillCacheRegistry extends Map {
  get(key) {
    const cache = super.get(key);
    if (cache) { super.delete(key); super.set(key, cache); }
    return cache;
  }
  set(key, cache) {
    super.set(key, cache);
    while (this.size > 16) {
      const oldest = this.keys().next().value, evicted = super.get(oldest);
      super.delete(oldest); evicted.dispose();
    }
    return this;
  }
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
        const localLimit = this.rows.length >= 10000 || this.bytes + decodedRowBytes(row) > 24 * 1024 * 1024;
        const bytes = localLimit ? 0 : this.budget.take(row);
        if (!bytes) { this.limitReached = true; this.reason = localLimit ? 'browser_result_limit' : this.budget.reason || 'browser_budget'; this.loading = false; this.hasMore = false; this.budget.finish?.(); this.controller.abort(); break; }
        this.bytes += bytes; this.rows.push(row);
      }
    }
    if (frame.type === 'complete') { this.budget.finish?.(); this.complete = true; this.limitReached ||= frame.limitReached; this.reason ||= frame.reason; this.hasMore = false; this.loading = false; }
    if (frame.type === 'error') { this.budget.finish?.(); this.error = frame.message; this.hasMore = false; this.loading = false; }
    this.elapsedMs = Date.now() - this.startedAt; this.notify();
  }
  loadMore() {
    if (this.pending) return this.pending;
    if (this.closed || !this.hasMore) return Promise.resolve(this.snapshot());
    if (!this.budget.available) {
      this.limitReached = true; this.reason = this.budget.reason || 'browser_budget'; this.hasMore = false; this.budget.finish?.(); this.notify();
      return Promise.resolve(this.snapshot());
    }
    this.loading = true; this.startedAt = Date.now(); this.notify();
    this.pending = this.run(); return this.pending;
  }
  async run() {
    try {
      await this.start(frame => this.accept(frame), this.controller.signal);
      if (!this.closed && !this.complete && !this.error && !this.limitReached) throw new Error('The result stream did not complete.');
      if (this.error) throw new Error(this.error);
    } catch (error) { if (!this.closed && !this.limitReached) this.error = `${error.message} Cached rows remain available; refresh to run again.`; }
    finally { this.budget.finish?.(); this.loading = false; this.hasMore = false; this.elapsedMs = Date.now() - this.startedAt; this.notify(); }
    return this.snapshot();
  }
  async page(index) { await this.loadMore(); const offset = index * this.pageSize; return { ...this.snapshot(), rows: this.rows.slice(offset, offset + this.pageSize), pageOffset: offset }; }
  snapshot() { return { plan: this.plan, columns: this.columns, rows: this.rows, elapsedMs: this.elapsedMs, pageOffset: 0, hasMore: this.hasMore, loading: this.loading, limitReached: this.limitReached, reason: this.reason, snapshotAt: this.snapshotAt, warnings: this.plan?.warnings || [], cachedRows: this.rows.length, error: this.error }; }
  async close() { if (this.closed) return; this.closed = true; if (this.loading) this.error = 'Query stopped. Cached rows remain available; refresh to run again.'; this.controller.abort(); this.budget.finish?.(); this.loading = false; this.hasMore = false; this.notify(); }
  dispose() { if (this.disposed) return; this.disposed = true; void this.close(); this.budget.release(this.bytes, this.rows.length); this.bytes = 0; this.rows.length = 0; this.listeners.clear(); }
}

/** Fans one eager dashboard stream into independently browsable tile caches. */
export class DashboardResultGroup {
  constructor({ start, budget = null, tileIds = [] }) { this.start = start; this.budget = budget; this.views = budget?.register(tileIds) || new Map(); this.listeners = new Map(); this.controller = new AbortController(); }
  budgetFor(tileId) { return this.views.get(tileId) || this.budget; }
  tile(tileId, callback) {
    this.listeners.set(tileId, callback);
    this.pending ||= Promise.resolve().then(() => this.start(frame => this.accept(frame), this.controller.signal));
    return this.pending;
  }
  accept(frame) {
    if (frame.type === 'start') {
      const active = new Set(frame.tiles.map(tile => tile.tileId));
      for (const [id, view] of this.views) if (!active.has(id)) view.finish();
      this.budget?.rebalance();
      for (const tile of frame.tiles) this.listeners.get(tile.tileId)?.({ ...frame, plan: tile.plan });
      for (const error of frame.tileErrors || []) this.listeners.get(error.tileId)?.({ ...error, type: 'error' });
    } else if (frame.tileId) this.listeners.get(frame.tileId)?.(frame);
    else if (frame.type === 'error') for (const callback of this.listeners.values()) callback(frame);
  }
  async close() { this.controller.abort(); for (const view of this.views.values()) view.finish(); }
}
