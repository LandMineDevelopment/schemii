import { requestJson } from '#common/http.js';

/** One SQL execution, one forward-only stream, a browser-memory cache of fetched rows. */
export class ResultCache {
  constructor({ start, pageSize = 100, request = requestJson, onChange = () => {}, wait = ms => new Promise(resolve => setTimeout(resolve, ms)) }) {
    this.start = start; this.pageSize = pageSize; this.request = request; this.onChange = onChange; this.wait = wait;
    this.rows = []; this.columns = []; this.plan = null; this.nextCursor = null; this.hasMore = true;
    this.started = false; this.closed = false; this.loading = false; this.error = ''; this.cleanupWarning = '';
    this.controller = new AbortController(); this.startedAt = 0; this.elapsedMs = 0;
  }
  async initialize() {
    if (this.initializing) return this.initializing;
    this.initializing = this._initialize(); return this.initializing;
  }
  async _initialize() {
    if (this.closed) throw new Error('This result is closed. Refresh to run a new query.');
    this.startedAt = Date.now();
    // Do not abort admission: retain its receipt even if the user closes the view
    // during this request, so the admitted execution can be cancelled explicitly.
    const response = await this.start();
    this.ownsExecution = response.ownsExecution !== false; this.resultIndex = response.resultIndex;
    this.plan = response.plan; this.base = response.executionUrl || `/api/v1/common/query-executions/${response.execution.id}`;
    this.execution = response.execution;
    if (this.resultIndex !== undefined) this.resultRecord = this.execution.results.find(result => result.statementIndex === this.resultIndex);
    if (this.closed) { await this.release(true); throw new Error('Query cancelled.'); }
    while (['reserved', 'running'].includes(this.execution.status)) {
      await this.wait(250);
      this.execution = await this.request(this.base, { signal: this.controller.signal, timeoutMs: 900000 });
    }
    if (this.execution.status !== 'succeeded') throw new Error(this.execution.errorMessage || `Query ${this.execution.status}`);
    this.resultRecord = this.resultIndex !== undefined ? this.execution.results.find(result => result.statementIndex === this.resultIndex) : this.execution.results.length === 1 ? this.execution.results[0] : null;
    if (!this.resultRecord) throw new Error('This tile did not return its result set.');
    this.resultUrl = `${this.base}/results/${this.resultRecord.id}`;
    this.started = true;
  }
  loadMore() {
    if (this.pending) return this.pending;
    if (this.closed || !this.hasMore) return Promise.resolve(this.snapshot());
    this.pending = this._loadMore().finally(() => { this.pending = null; });
    return this.pending;
  }
  async _loadMore() {
    const batchStarted = Date.now();
    this.loading = true; this.onChange();
    try {
      await this.initialize();
      const params = new URLSearchParams({ page_size: String(this.pageSize) });
      if (this.nextCursor) params.set('cursor', this.nextCursor);
      const suffix = `?${params}`;
      const page = await this.request(this.resultUrl + suffix, { signal: this.controller.signal, timeoutMs: 900000 });
      if (this.closed) return this.snapshot();
      const labels = this.plan?.outputLabels || [];
      this.columns = page.columns.map((column, index) => ({ ...column, name: labels[index] || column.name }));
      this.rows.push(...page.rows); this.nextCursor = page.nextCursor;
      this.hasMore = Boolean(page.nextCursor); this.expiresAt = page.expiresAt;
      this.elapsedMs += Date.now() - batchStarted;
      if (page.truncated) this.error = 'The query reached a database result limit. Cached rows remain available.';
      if (!this.hasMore) await this.release(false);
      return this.snapshot();
    } catch (error) {
      // A cursor token is consumed once. A lost response must not retry the token
      // or re-run SQL behind the user's back. Already cached rows stay readable.
      this.error = this.closed ? 'Query cancelled. Cached rows remain available.' : `${error.message} Cached rows remain available; refresh explicitly to run again.`;
      this.hasMore = false;
      await this.release(true);
      throw new Error(this.error);
    } finally { this.loading = false; this.onChange(); }
  }
  async page(index) {
    const offset = index * this.pageSize;
    while (this.rows.length < offset + this.pageSize && this.hasMore && !this.closed) await this.loadMore();
    return { ...this.snapshot(), rows: this.rows.slice(offset, offset + this.pageSize), pageOffset: offset,
      hasMore: this.rows.length > offset + this.pageSize || this.hasMore };
  }
  snapshot() {
    return { plan: this.plan, columns: this.columns, rows: this.rows, elapsedMs: this.elapsedMs, rowLimit: this.pageSize,
      pageOffset: 0, hasMore: this.hasMore, warnings: [...(this.plan?.warnings || []), ...(this.cleanupWarning ? [this.cleanupWarning] : [])], cachedRows: this.rows.length, error: this.error };
  }
  async release(cancel) {
    if (!this.base) return;
    if (cancel && this.ownsExecution !== false) {
      try { await this.request(this.base, { method: 'DELETE' }); }
      catch { this.cleanupWarning = 'Server cancellation could not be confirmed. Check live queries before running again.'; }
    }
    for (const result of this.ownsExecution === false ? (this.resultRecord ? [this.resultRecord] : []) : this.execution?.results || []) {
      try { await this.request(`${this.base}/results/${result.id}`, { method: 'DELETE' }); }
      catch { this.cleanupWarning = 'Closing the database cursor could not be confirmed.'; }
    }
  }
  async close() {
    if (this.closed) return;
    this.closed = true; this.hasMore = false; this.controller.abort();
    await this.release(true);
    this.onChange();
  }
}

/** Dashboard tiles share one admitted database session with independent cursors. */
export class DashboardResultGroup {
  constructor({ start, request = requestJson, wait = ms => new Promise(resolve => setTimeout(resolve, ms)) }) {
    this.start = start; this.request = request; this.wait = wait; this.closed = false;
    this.controller = new AbortController();
  }
  ready() { this.pending ||= this._ready(); return this.pending; }
  async _ready() {
    const response = await this.start(); this.response = response; this.base = response.executionUrl;
    if (this.closed) { await this.closeReceipt(); throw new Error('Dashboard query cancelled.'); }
    if (!response.execution) return response;
    try {
      while (['reserved', 'running'].includes(response.execution.status)) {
        await this.wait(250);
        response.execution = await this.request(this.base, { signal: this.controller.signal, timeoutMs: 900000 });
      }
      if (response.execution.status !== 'succeeded') throw new Error(response.execution.errorMessage || `Dashboard query ${response.execution.status}`);
      return response;
    } catch (error) { await this.closeReceipt(); throw error; }
  }
  async tile(tileId) {
    const response = await this.ready();
    const tile = response.tiles.find(item => item.tileId === tileId);
    if (!tile) throw new Error(response.tileErrors.find(item => item.tileId === tileId)?.message || 'This tile could not be compiled. Edit its configuration and try again.');
    return { execution: response.execution, executionUrl: response.executionUrl, plan: tile.plan,
      resultIndex: tile.statementIndex, ownsExecution: false };
  }
  async closeReceipt() {
    if (!this.base || this.released) return;
    this.released = true;
    try { await this.request(this.base, { method: 'DELETE' }); }
    catch { this.cleanupWarning = 'Dashboard cursor cancellation could not be confirmed.'; }
  }
  async close() { this.closed = true; this.controller.abort(); await this.closeReceipt(); }
}
