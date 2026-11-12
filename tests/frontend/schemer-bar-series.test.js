import test from 'node:test';
import assert from 'node:assert/strict';
import { barGroups, barDimensionValue, barMeasureName, barColor } from '../../src/schemii/schemer/web/bar-series.js';
import { timeSeries } from '../../src/schemii/schemer/web/time-analysis.js';
import { markSelection } from '../../src/schemii/schemer/web/dashboard-state.js';
import { renderVisualization, displayedVisualization } from '../../src/schemii/schemer/web/visualizations.js';
const field = (column, aggregate = 'none') => ({ table: 'personnel', column, aggregate });
const tile = { kind: 'bar', dimensions: [field('pay_band'), field('start_date')], measures: [field('id', 'count_distinct')], timeAnalysis: { table: 'personnel', column: 'start_date', granularity: 'month', comparison: 'none' } };
const rows = [[1, '2024-09-01', 43], [1, '2024-10-01', 47], [2, '2024-09-01', 301], [2, '2024-11-01', 498]];
test('bars cluster by category and reuse series colors without recomputing measures', () => {
  const result = barGroups(tile, { rows }, timeSeries(tile));
  assert.deepEqual(result.groups.map(group => group.label), ['1', '2']);
  assert.deepEqual(result.groups.map(group => group.bars.length), [2, 2]);
  assert.deepEqual(result.series.map(series => series.label), ['Sep 2024', 'Oct 2024', 'Nov 2024']);
  assert.equal(result.groups[0].bars[0].series, result.groups[1].bars[0].series);
  assert.equal(result.groups[1].bars[0].row, rows[2]);
  assert.deepEqual(markSelection(tile, result.groups[1].bars[0].row, 0).dimensions.map(d => d.value), [2, '2024-09-01']);
  const firstBatch = barGroups(tile, { rows: rows.slice(0, 2) }, timeSeries(tile));
  assert.deepEqual(result.series.slice(0, 2), firstBatch.series);
});
test('changing group dimension retains original row order for drill selections', () => {
  const result = barGroups(tile, { rows }, timeSeries(tile), 1);
  assert.deepEqual(result.groups.map(group => group.label), ['Sep 2024', 'Oct 2024', 'Nov 2024']);
  assert.deepEqual(result.series.map(series => series.label), ['1', '2']);
  assert.deepEqual(result.groups[0].bars.map(bar => bar.row[2]), [43, 301]);
  assert.deepEqual(markSelection(tile, result.groups[0].bars[1].row, 0).dimensions.map(d => d.value), [2, '2024-09-01']);
});
test('tuple grouping keeps sparse, null, literal-null, separator, and typed values distinct', () => {
  const complex = { ...tile, dimensions: [field('pay_band'), field('region'), field('channel')], timeAnalysis: null };
  const rows = [[1, null, 'web', 0], [1, 'NULL', 'web', -5], [1, 'a · b', 'c', null], [1, 'a', 'b · c', 10], ['1', null, 'web', 2]];
  const result = barGroups(complex, { rows }, timeSeries(complex));
  assert.equal(result.groups.length, 2);
  assert.equal(result.series.length, 4);
  assert.notEqual(result.groups[0].label, result.groups[1].label);
  assert.equal(new Set(result.series.map(series => series.label)).size, 4);
  assert.equal(result.groups.reduce((total, group) => total + group.bars.length, 0), rows.length);
});
test('multiple measures and time outputs retain correct columns and drill eligibility', () => {
  const timed = { ...tile, measures: [field('id', 'count'), field('salary', 'sum')], timeAnalysis: { ...tile.timeAnalysis, comparison: 'previous_period', runningTotal: true } };
  const result = { rows: [[1, '2024-09-01', 3, 100, 2, 1, 50, 3, 80, 20, 25, 100]] };
  const values = barGroups(timed, result, timeSeries(timed));
  assert.deepEqual(values.series.map(series => [series.column, series.drill]), [[2, 0], [4, null], [3, 1], [8, null]]);
  assert.equal(barMeasureName(timed, 8), 'Sum · Salary · Previous period');
  assert.equal(barMeasureName(timed, 11), 'Sum · Salary · Running total');
  for (const metric of ['change', 'percent', 'running']) {
    assert.ok(barGroups(timed, result, timeSeries(timed, metric)).series.every(series => series.drill === null));
  }
});
test('single-dimension bars remain one group per original category', () => {
  const one = { ...tile, dimensions: [field('pay_band')], timeAnalysis: null };
  const result = barGroups(one, { rows: [[1, 3], [2, 0], [null, -1]] }, timeSeries(one));
  assert.equal(result.series.length, 1);
  assert.deepEqual(result.groups.map(group => group.label), ['1', '2', 'NULL']);
});
test('calendar labels honor granularity without local-time date shifts and colors do not repeat at six series', () => {
  assert.equal(barDimensionValue(tile, 1, '2024-09-01'), 'Sep 2024');
  assert.equal(barDimensionValue({ ...tile, timeAnalysis: { ...tile.timeAnalysis, granularity: 'year' } }, 1, '2024-01-01'), '2024');
  assert.equal(new Set(Array.from({ length: 25 }, (_, index) => barColor(index))).size, 25);
});


test('identical measure columns from different model nodes remain distinguishable', () => {
  const two = { ...tile, measures: [field('id', 'count'), { table: 'customers', column: 'id', aggregate: 'count' }] };
  assert.equal(barMeasureName(two, 2), 'Count · Personnel · Id');
  assert.equal(barMeasureName(two, 3), 'Count · Customers · Id');
});

test('calculated columns beyond complete time blocks use their stable named mapping',()=>{
  const current={...tile,outputMeasureNames:{9:'Weighted ratio of sums'}};
  assert.equal(barMeasureName(current,9),'Weighted ratio of sums');
  const grouped=barGroups(current,{rows:[[1,'2024-09-01',4,0,0,0,0,0,0,2.5]]},[{column:9,drill:1}]);
  assert.equal(grouped.series[0].label,'Sep 2024');
  assert.equal(grouped.series[0].column,9);assert.equal(grouped.groups[0].bars[0].row[9],2.5);
});

class ProjectionNode {
  constructor(tag) { this.tagName = tag; this.children = []; this.dataset = {}; this.attrs = {}; this.style = { setProperty() {} }; this.classList = { add() {}, remove() {}, toggle() {} }; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.ownText = ''; this.children = items; }
  set textContent(value) { this.ownText = String(value); this.children = []; }
  get textContent() { return (this.ownText || '') + this.children.map(child => child.textContent).join(''); }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return this.attrs[name]; }
  addEventListener() {}
  querySelector(selector) { return projectionNodes(this).slice(1).find(node => node.tagName === selector) || null; }
  querySelectorAll() { return []; }
}
const projectionNodes = root => [root, ...root.children.flatMap(projectionNodes)];
test('actual chart and KPI DOM render the same calculated and comparison outputs that capture projects', t => {
  const oldDocument = globalThis.document, oldNode = globalThis.Node;
  globalThis.Node = ProjectionNode;
  globalThis.document = { createElement: tag => new ProjectionNode(tag), createElementNS: (_ns, tag) => new ProjectionNode(tag), createTextNode: text => { const node = new ProjectionNode('#text'); node.textContent = text; return node; } };
  t.after(() => { globalThis.document = oldDocument; globalThis.Node = oldNode; });
  const host = new ProjectionNode('main'), tile = { id: 'actual', title: 'Share', kind: 'bar', dimensions: [field('region')], measures: [], hiddenMeasures: [field('amount', 'sum')], calculations: [{ id: 'share', visible: true }] };
  const result = { columns: [{ name: 'Region' }, { name: 'Share' }], rows: [['East', .25], ['West', .75]], plan: { calculatedMeasures: [{ id: 'share', index: 1, label: 'Share' }] } };
  renderVisualization(host, tile, result);
  assert.deepEqual(projectionNodes(host).filter(node => node.className === 'bar-number').map(node => Number(node.textContent)), [.25, .75]);
  assert.deepEqual(displayedVisualization(host, tile, result).rows, [['East', .25], ['West', .75]]);
  result.rows.push(['Not painted yet', 12345]);
  assert.deepEqual(displayedVisualization(host, tile, result).rows, [['East', .25], ['West', .75]]);
  result.rows.pop();
  renderVisualization(host, { ...tile, kind: 'donut' }, result);
  assert.equal(projectionNodes(host).filter(node => node.className === 'donut-entry').length, 2);
  assert.deepEqual(displayedVisualization(host, { ...tile, kind: 'donut' }, result).rows, [['East', .25], ['West', .75]]);
  const kpi = { ...tile, kind: 'kpi', dimensions: [] }, compared = { columns: ['Current', 'Target', 'Absolute', 'Percent', 'Status'].map(name => ({ name })), rows: [[23, 17, 6, 35.294, 'favorable']], plan: { kpiComparison: { label: 'Target', favorable: 'higher', indices: [1, 2, 3, 4], baselineUnit: 'number', absoluteUnit: 'number' } } };
  renderVisualization(host, kpi, compared); assert.match(host.textContent, /Target: 17/); assert.match(host.textContent, /Absolute variance: 6/); assert.match(host.textContent, /Percentage variance: 35/); assert.match(host.textContent, /favorable/);
  assert.deepEqual(displayedVisualization(host, kpi, compared).rows, [[23, 17, 6, 35.294, 'favorable']]);
});
