import test from 'node:test';
import assert from 'node:assert/strict';
import { pivotMatrix, pivotSelection, renderPivot, displayedPivot } from '../../src/schemii/schemer/web/pivot.js';
import { freezeDisplayedResult } from '../../src/schemii/schemer/web/result-cache.js';
import { pivotDefaults, tileErrors, tileAggregateChoices } from '../../src/schemii/schemer/web/dashboard-state.js';

const field = (column, aggregate = 'none') => ({ table: 'facts', column, aggregate });
const tile = (changes = {}) => ({ id: 'pivot', title: 'Sales', kind: 'pivot', dimensions: [],
  rowDimensions: [field('region')], columnDimensions: [field('category')],
  measures: [field('amount', 'avg'), field('customer', 'count_distinct')], detailFields: [field('id')],
  pivotTotals: pivotDefaults(), reportFilters: [], selections: {}, limit: 2, ...changes });
const record = (r, c, avg, count, rd = 1, cd = 1, omitted = false) => [r, c, avg, count, rd, cd, 'data', 2, omitted, 4];
const result = (rows, changes = {}) => ({ rows, columns: ['Region', 'Category', 'Average amount', 'Distinct customers'].map(name => ({ name })), ...changes });

test('sparse matrix separates real NULLs, literal total names and explicit grouping totals without summing cells', () => {
  const matrix = pivotMatrix(tile(), result([record(null, 'A', 20, 1), record('All rows', 'A', 50, 2),
    record(null, null, 235 / 6, 4, 0, 0), record(null, null, 20, 1, 1, 0)]));
  assert.equal(matrix.rows.length, 3); assert.equal(matrix.columns.length, 2);
  assert.deepEqual(matrix.rows.map(row => [row.depth, ...row.values]), [[1, 'All rows'], [1, null], [0]]);
  assert.ok([...matrix.cells.values()].some(([, , avg, count]) => avg === 235 / 6 && count === 4));
  assert.equal(matrix.cells.size, 4); // Absent combinations are absent, not invented zeroes.
});

test('cell, row, column, subtotal and grand-total scopes include only exact axis prefixes', () => {
  const config = tile({ rowDimensions: [field('region'), field('team')] });
  for (const [rd, cd, kind, columns] of [[2, 1, 'cell', ['region', 'team', 'category']], [2, 0, 'row', ['region', 'team']],
    [0, 1, 'column', ['category']], [1, 1, 'subtotal', ['region', 'category']], [0, 0, 'grand_total', []]]) {
    const selected = pivotSelection(config, { depth: rd, values: [null, 'Team'] }, { depth: cd, values: ['A'] }, 1);
    assert.deepEqual(selected.pivot, { kind, rowDepth: rd, columnDepth: cd });
    assert.deepEqual(selected.dimensions.map(field => field.column), columns);
    assert.equal(selected.measureIndex, 1);
    if (rd) assert.equal(selected.dimensions[0].value, null);
    assert.equal(Object.hasOwn(selected, 'sql'), false);
  }
});

test('server capacity status and independent client cardinality guard stop before sparse matrix allocation', () => {
  const config = tile({ pivotTotals: { ...pivotDefaults(), maximumColumns: 1 } });
  const capacity = record(null, null, null, null, 0, 0); capacity[6] = 'column_capacity'; capacity[7] = 2;
  assert.deepEqual(pivotMatrix(config, result([capacity])), { capacity: true, count: 2, maximum: 1 });
  assert.equal(pivotMatrix(config, result([record('R', 'A', 10, 1), record('R', 'B', 20, 1)])).capacity, true);
  assert.equal(pivotMatrix(tile({ pivotTotals: { maximumColumns: 10000 } }), result([])).cells, undefined);
});

test('bounded metadata rejects malformed depth and discloses incomplete or row-capped populations', () => {
  assert.match(pivotMatrix(tile(), result([record('R', 'C', 1, 1, 2, 1)])).error, /metadata/);
  assert.equal(pivotMatrix(tile(), result([record('R', 'C', 1, 1, 1, 1, true)], { limitReached: true })).rowsOmitted, true);
  for (const change of [{ loading: true }, { limitReached: true }, { error: 'Interrupted' }]) assert.equal(pivotMatrix(tile(), result([], change)).partial, true);
});

class FakeNode {
  constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.style = {}; this.events = {}; this.scrollTop = 0; }
  append(...values) { this.children.push(...values); }
  replaceChildren(...values) { this.children = [...values]; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, handler) { this.events[name] = handler; }
  set textContent(value) { this.content = value; this.children = []; }
  get textContent() { return this.content ?? this.children.map(node => node.textContent ?? '').join(''); }
}
function mounted(config, data, options = {}) {
  globalThis.Node = FakeNode;
  globalThis.document = { createElement: tag => new FakeNode(tag), createTextNode: text => ({ textContent: text }) };
  const host = new FakeNode('main'); renderPivot(host, config, data, options);
  const all = node => [node, ...node.children.flatMap(child => child instanceof FakeNode ? all(child) : [])];
  return { host, nodes: () => all(host) };
}

test('matrix renders labelled multilevel headers, keyboard drill buttons and exact total scope; repeated views have unique IDs', () => {
  const config = tile({ columnDimensions: [field('category'), field('team')], topN: 1 });
  const rows = [['East', 'A', 'One', 10, 1, 1, 2, 'data', 1, false, 2],
    [null, null, null, 40, 3, 0, 0, 'data', 1, false, 2]];
  const selections = [], first = mounted(config, result(rows), { onDrill: selection => selections.push(selection) });
  assert.match(first.host.textContent, /before Top N.*Top 1.*other cells/);
  assert.ok(first.nodes().some(node => node.tag === 'th' && node.attributes.scope === 'colgroup' && /category: A/.test(node.textContent)));
  const grand = first.nodes().find(node => node.tag === 'button' && /All rows · All columns.*View contributing/.test(node.attributes['aria-label']));
  let stopped = false; grand.onclick({ stopPropagation() { stopped = true; } });
  assert.ok(stopped); assert.deepEqual(selections[0], { dimensions: [], measureIndex: 0, pivot: { kind: 'grand_total', rowDepth: 0, columnDepth: 0 } });
  assert.equal(first.nodes().find(node => node.className?.includes('pivot-viewport')).attributes.tabindex, '0');
  const cell = first.nodes().find(node => node.tag === 'td' && node.attributes.headers);
  const accessibleHeaders = cell.attributes.headers.split(' ').map(id => first.nodes().find(node => node.attributes.id === id));
  assert.equal(accessibleHeaders.length, 4); assert.ok(accessibleHeaders.every(Boolean));
  assert.ok(accessibleHeaders.some(header => header.attributes.scope === 'colgroup'));
  const second = mounted(config, result(rows));
  const ids = first.nodes().filter(node => node.attributes.id).map(node => node.attributes.id);
  assert.ok(second.nodes().filter(node => node.attributes.id).every(node => !ids.includes(node.attributes.id)));
});

test('bounded row window keeps scrolling usable with fixed 44px rows and does not allocate a full dense matrix', () => {
  const rows = Array.from({ length: 180 }, (_, index) => record(`R${String(index).padStart(3, '0')}`, 'A', index, 1));
  const view = mounted(tile({ pivotTotals: { ...pivotDefaults(), maximumRows: 200 } }), result(rows));
  const body = view.nodes().find(node => node.tag === 'tbody'), viewport = view.nodes().find(node => node.events.scroll);
  assert.ok(body.children.length <= 52); assert.match(body.children.at(-1).children[0].style.height, /px$/);
  viewport.scrollTop = 5000; viewport.events.scroll();
  assert.ok(body.children.length <= 52); assert.ok(Number(body.children[1].attributes['aria-rowindex']) > 90);
});

test('displayed pivot uses the mounted sparse window, axis headers and explicit NULL-versus-total scopes', () => {
  const config = tile(), data = result([record(null, 'A', 23.5, 7), record(null, null, 101.25, 19, 0, 0), record(null, null, 33.75, 11, 1, 0)], { snapshotAt: '2026-11-12T00:00:00Z' });
  const view = mounted(config, data), captured = displayedPivot(view.host, config, data);
  assert.deepEqual(captured.columns.map(item => item.name), ['Region', 'Category', 'Average amount', 'Distinct customers', 'Row grouping depth', 'Column grouping depth', 'Pivot record type', 'Column group count', 'Preview row cap reached', 'Qualifying source groups', 'Pivot scope']);
  const scopes = new Map(captured.rows.map(row => [row.at(-1), row]));
  assert.deepEqual(scopes.get('cell'), [...data.rows[0], 'cell']);
  assert.deepEqual(scopes.get('row'), [...data.rows[2], 'row']);
  assert.deepEqual(scopes.get('grand_total'), [...data.rows[1], 'grand_total']);
  assert.equal(captured.projection.rowAxes, 1); assert.equal(captured.projection.columnAxes, 1);
  const frozen = freezeDisplayedResult(captured, { pane: 'main' }); data.rows[0][2] = 999;
  assert.equal(frozen.rows.find(row => row.at(-1) === 'cell')[2], 23.5); assert.equal(Object.isFrozen(frozen.projection), true);
  const many = result(Array.from({ length: 180 }, (_, i) => record(`R${String(i).padStart(3, '0')}`, 'A', 1000 + i, 2000 + i)), { snapshotAt: data.snapshotAt });
  const largeConfig = tile({ pivotTotals: { ...pivotDefaults(), maximumRows: 200 } });
  const window = mounted(largeConfig, many), first = displayedPivot(window.host, largeConfig, many);
  assert.equal(first.rows.length, 50); assert.equal(first.projection.rowStart, 0); assert.equal(first.projection.rowEnd, 50);
  const viewport = window.nodes().find(node => node.events.scroll); viewport.scrollTop = 5000; viewport.events.scroll();
  const moved = displayedPivot(window.host, largeConfig, many); assert.equal(moved.rows.length, 50); assert.ok(moved.projection.rowStart > 90);
  assert.equal(moved.rows[0][2], 1000 + moved.projection.rowStart); assert.equal(moved.cachedRows, 180); assert.equal(moved.visualTruncated, true);
  const sample = freezeDisplayedResult(moved, { pane: 'main' }, { maxRows: 3 });
  assert.equal(sample.displayedRows, 50); assert.equal(sample.cachedRows, 180); assert.equal(sample.rows.length, 3);
  assert.equal(sample.chartTruncated, true); assert.equal(sample.clientSampleTruncated, true);
});

test('pivot capacity diagnostics never become attached numeric facts and compact capture keeps its actual window', () => {
  const config = tile({ pivotTotals: { ...pivotDefaults(), maximumColumns: 1 } });
  const sentinel = record(null, null, 9123, 8456, 0, 0); sentinel[6] = 'column_capacity'; sentinel[7] = 2;
  const data = result([sentinel], { snapshotAt: '2026-11-12T00:00:00Z' }), view = mounted(config, data);
  const captured = displayedPivot(view.host, config, data);
  assert.deepEqual(captured.rows, []); assert.deepEqual(captured.columns, []); assert.equal(captured.projection.status, 'capacity');
  assert.match(captured.projection.notice, /No numeric cells/); assert.equal(freezeDisplayedResult(captured, { pane: 'main' }).displayedRows, 0);
  const rows = result(Array.from({ length: 80 }, (_, i) => record(`R${i}`, 'A', i, i + 10)));
  const compact = mounted(config, rows, { compact: true }); assert.equal(displayedPivot(compact.host, config, rows).rows.length, 12);
});

for (const [data, message] of [[result([], { loading: true }), /Waiting for pivot/], [result([]), /No qualifying groups/],
  [result([], { error: 'Interrupted' }), /did not complete/], [result([record(null, null, null, 0, 0, 0, false).map((x, i) => i === 9 ? 0 : x)]), /No qualifying source rows/]]) {
  test(`matrix empty/loading/error recovery: ${String(message)}`, () => assert.match(mounted(tile(), data).host.textContent, message));
}

test('pivot validation blocks implicit summary lifting and calendar windows while preserving ordinary tiles', () => {
  assert.deepEqual(tileErrors(tile()), []);
  assert.ok(tileErrors(tile({ measures: [field('amount')] })).some(error => /explicit aggregation/.test(error)));
  assert.ok(tileErrors(tile({ columnDimensions: [field('region')] })).some(error => /one pivot axis/.test(error)));
  assert.deepEqual(tileErrors({ ...tile(), kind: 'aggregate', dimensions: [field('region')], rowDimensions: [], columnDimensions: [] }), []);
});

test('pivot summary aggregation policy uses exposed semantic output types and retains ordinary summary values', () => {
  for (const [type, allowed, denied] of [
    ['numeric(12,2)', ['count', 'count_distinct', 'sum', 'avg', 'min', 'max'], []],
    ['date', ['count', 'count_distinct', 'min', 'max'], ['sum', 'avg']],
    ['text', ['count', 'count_distinct', 'min', 'max'], ['sum', 'avg']],
    ['boolean', ['count', 'count_distinct'], ['sum', 'avg', 'min', 'max']],
    ['json', ['count'], ['count_distinct', 'sum', 'avg', 'min', 'max']],
  ]) {
    const draft = { nodes: [{ id: 'facts', table: 'physical' }, { id: 'summary', table: 'physical', derivation: { kind: 'aggregate', source: 'facts', groupBy: ['id'], outputs: [{ id: 'value', operation: 'max', column: 'value' }] } }] };
    const catalog = { tables: [{ name: 'physical', columns: [{ name: 'id', dataType: 'integer' }, { name: 'value', dataType: type }] }] };
    const measure = { table: 'summary', column: 'value' };
    const choices = tileAggregateChoices(tile(), draft, catalog, measure).map(([value]) => value);
    for (const value of allowed) assert.ok(choices.includes(value), `${type} missing ${value}`);
    for (const value of ['none', ...denied]) assert.ok(!choices.includes(value), `${type} offered ${value}`);
    assert.deepEqual(tileAggregateChoices({ kind: 'aggregate' }, draft, catalog, measure), [['none', 'Summary value']]);
  }
});
