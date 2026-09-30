import { createHash } from 'node:crypto';
import { TextDecoder } from 'node:util';

export class ProtocolFailure extends Error {
  constructor(code) { super(code); this.code = code; }
}
const fail = code => { throw new ProtocolFailure(code); };
const MASK = (1n << 256n) - 1n;
// An order-independent multiset fingerprint preserves multiplicity without
// retaining source rows. Count + column oracle accompany the 256-bit sum.
export class RowOracle {
  constructor(expected) {
    if (!expected || !Number.isSafeInteger(expected.rows) || expected.rows < 0 ||
        !Array.isArray(expected.columns) || !expected.columns.length ||
        !/^[a-f0-9]{64}$/.test(expected.digest || '')) fail('invalid_oracle');
    this.expected = expected; this.count = 0; this.sum = 0n;
  }
  columns(columns) {
    if (JSON.stringify(columns) !== JSON.stringify(this.expected.columns)) fail('wrong_columns');
  }
  row(row) {
    if (!Array.isArray(row) || row.length !== this.expected.columns.length) fail('wrong_row_shape');
    this.count++;
    if (this.count > this.expected.rows) fail('unexpected_rows');
    this.sum = (this.sum + BigInt(`0x${createHash('sha256').update(JSON.stringify(row)).digest('hex')}`)) & MASK;
  }
  finish() {
    if (this.count !== this.expected.rows) fail('wrong_row_count');
    if (this.sum.toString(16).padStart(64, '0') !== this.expected.digest) fail('wrong_or_duplicate_rows');
    return { rows: this.count };
  }
}
export function oracleFor(columns, rows, extra = {}) {
  const expected = { columns, rows: rows.length, digest: '0'.repeat(64), ...extra };
  const oracle = new RowOracle(expected);
  for (const row of rows) oracle.row(row);
  expected.digest = oracle.sum.toString(16).padStart(64, '0');
  return expected;
}

export class NDJSONOracle {
  constructor(tiles, { maxBytes = 32 * 1024 * 1024, maxLineBytes = 1024 * 1024,
    clock = () => performance.now(), onExecution = () => {} } = {}) {
    if (!tiles || !Object.keys(tiles).length || Object.keys(tiles).length > 20) fail('invalid_tiles');
    this.tiles = new Map(Object.entries(tiles).map(([id, oracle]) => [id, new RowOracle(oracle)]));
    this.complete = new Set(); this.started = false; this.ended = false; this.execution = false;
    this.buffer = ''; this.bytes = 0; this.maxBytes = maxBytes; this.maxLineBytes = maxLineBytes;
    this.decoder = new TextDecoder('utf-8', { fatal: true }); this.clock = clock;
    this.startedAt = clock(); this.firstRowMs = null; this.onExecution = onExecution;
  }
  push(chunk) {
    this.bytes += chunk.byteLength;
    if (this.bytes > this.maxBytes) fail('body_budget');
    try { this.buffer += this.decoder.decode(chunk, { stream: true }); } catch { fail('invalid_utf8'); }
    let end;
    while ((end = this.buffer.indexOf('\n')) !== -1) {
      const line = this.buffer.slice(0, end); this.buffer = this.buffer.slice(end + 1);
      if (Buffer.byteLength(line) > this.maxLineBytes) fail('line_budget');
      if (!line.trim()) fail('empty_event');
      let event; try { event = JSON.parse(line); } catch { fail('invalid_json'); }
      this.event(event);
    }
    if (Buffer.byteLength(this.buffer) > this.maxLineBytes) fail('line_budget');
  }
  event(event) {
    if (!event || this.ended) fail('event_after_end');
    if (event.type === 'error') fail('stream_error');
    if (event.type === 'start') {
      if (this.started || !Array.isArray(event.tiles) || (event.tileErrors || []).length) fail('invalid_start');
      const ids = event.tiles.map(tile => tile.tileId).sort();
      if (JSON.stringify(ids) !== JSON.stringify([...this.tiles.keys()].sort())) fail('wrong_tiles');
      this.started = true; return;
    }
    if (!this.started) fail('missing_start');
    if (event.type === 'execution') {
      if (this.execution || !/^cex_[a-f0-9]{32}$/.test(event.executionId || '')) fail('invalid_execution');
      this.execution = true; this.onExecution(event.executionId); return;
    }
    if (event.type === 'end') {
      if (this.complete.size !== this.tiles.size || !this.execution) fail('missing_tile_completion');
      this.ended = true; return;
    }
    const oracle = this.tiles.get(event.tileId);
    if (!oracle || this.complete.has(event.tileId)) fail('wrong_or_completed_tile');
    if (!this.execution) fail('missing_execution');
    if (event.type === 'rows') {
      if (!Array.isArray(event.columns) || !Array.isArray(event.rows)) fail('invalid_rows');
      oracle.columns(event.columns.map(column => column.name));
      for (const row of event.rows) {
        oracle.row(row);
        if (this.firstRowMs === null) this.firstRowMs = this.clock() - this.startedAt;
      }
    } else if (event.type === 'complete') {
      oracle.finish();
      if (event.rowCount !== oracle.count || event.limitReached !== !!oracle.expected.previewReason ||
          (event.reason ?? null) !== (oracle.expected.previewReason ?? null)) fail('unexpected_truncation');
      this.complete.add(event.tileId);
    } else fail('unknown_event');
  }
  finish() {
    try { this.buffer += this.decoder.decode(); } catch { fail('invalid_utf8'); }
    if (this.buffer.length || !this.ended) fail('missing_end');
    return { firstRowMs: this.firstRowMs, fullDrainMs: this.clock() - this.startedAt,
      bytes: this.bytes, rows: [...this.tiles.values()].reduce((n, tile) => n + tile.count, 0) };
  }
}

// Stateful RFC4180 parser: quoted newlines and quotes can cross any chunk.
// A final record terminator is required to reject a cut-off partial download.
export class CSVOracle {
  constructor(expected, { maxBytes = 32 * 1024 * 1024, maxRecordBytes = 1024 * 1024,
    clock = () => performance.now() } = {}) {
    this.oracle = new RowOracle(expected); this.decoder = new TextDecoder('utf-8', { fatal: true });
    this.row = []; this.field = ''; this.state = 'field'; this.cr = false; this.header = false;
    this.bytes = 0; this.recordBytes = 0; this.maxBytes = maxBytes; this.maxRecordBytes = maxRecordBytes;
    this.clock = clock; this.startedAt = clock(); this.firstRowMs = null;
  }
  push(chunk) {
    this.bytes += chunk.byteLength;
    if (this.bytes > this.maxBytes) fail('body_budget');
    let text; try { text = this.decoder.decode(chunk, { stream: true }); } catch { fail('invalid_utf8'); }
    this.consume(text);
  }
  consume(text) {
    for (const char of text) {
      this.recordBytes += Buffer.byteLength(char);
      if (this.recordBytes > this.maxRecordBytes) fail('record_budget');
      if (this.cr) { this.cr = false; if (char === '\n') continue; }
      if (this.state === 'quoted') {
        if (char === '"') this.state = 'quote'; else this.field += char;
        continue;
      }
      if (this.state === 'quote' && char === '"') { this.field += '"'; this.state = 'quoted'; continue; }
      if (char === '"' && this.state === 'field' && !this.field) { this.state = 'quoted'; continue; }
      if (char === ',') { this.row.push(this.field); this.field = ''; this.state = 'field'; continue; }
      if (char === '\r' || char === '\n') {
        this.row.push(this.field);
        if (!this.header) { this.oracle.columns(this.row); this.header = true; }
        else { this.oracle.row(this.row); if (this.firstRowMs === null) this.firstRowMs = this.clock() - this.startedAt; }
        this.field = ''; this.row = []; this.state = 'field'; this.recordBytes = 0; this.cr = char === '\r';
        continue;
      }
      if (this.state === 'quote' || char === '"') fail('invalid_csv');
      this.field += char;
    }
  }
  finish() {
    try { this.consume(this.decoder.decode()); } catch (error) { if (error instanceof ProtocolFailure) throw error; fail('invalid_utf8'); }
    if (!this.header || this.row.length || this.field.length || this.state !== 'field') fail('partial_csv');
    this.oracle.finish();
    return { firstRowMs: this.firstRowMs, fullDrainMs: this.clock() - this.startedAt,
      bytes: this.bytes, rows: this.oracle.count };
  }
}
