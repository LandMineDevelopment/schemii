import test from 'node:test';
import assert from 'node:assert/strict';
import { dimensionKey, dimensionLabel, lineSeries, linePath } from '../../src/schemii/schemer/web/chart-dimensions.js';
import { timeSeries } from '../../src/schemii/schemer/web/time-analysis.js';
import { chartPreview } from '../../src/schemii/schemer/web/chart-preview.js';
import { tileErrors, markSelection } from '../../src/schemii/schemer/web/dashboard-state.js';
const field = column => ({ table: 'events', column, aggregate: 'none' });
const tile = { title: 'Sales', kind: 'line', dimensions: [field('date'), field('region'), field('channel')], measures: [{ ...field('amount'), aggregate: 'sum' }], detailFields: [field('date')] };
test('dimension tuples distinguish separators, types, literal NULL, and SQL NULL', () => {
  const tuples = [['a · b', 'c'], ['a', 'b · c'], [null, 'x'], ['NULL', 'x'], [1, 'x'], ['1', 'x']];
  assert.equal(new Set(tuples.map(dimensionKey)).size, tuples.length);
  const two = { ...tile, dimensions: tile.dimensions.slice(0, 2) };
  assert.equal(new Set(tuples.map(row => dimensionLabel(two, row))).size, tuples.length);
  assert.equal(dimensionLabel({ ...tile, dimensions: [field('date')] }, ['Monday']), 'Monday');
  assert.equal(dimensionLabel({ ...tile, dimensions: [field('date')] }, [null]), 'NULL');
});
test('line grouping crosses full remaining tuple with measures and preserves original rows for drill', () => {
  const rows = [['Jan', 'East', 'web', 10, 9], ['Jan', 'West', 'web', 20, 18], ['Feb', 'East', 'web', null, null], ['Mar', 'East', 'web', 30, 28], ['Mar', 'West', 'web', 40, 36], ['Mar', 'East', 'store', 50, 48]];
  const timeTile = { ...tile, timeAnalysis: { comparison: 'previous_period' } };
  const result = { columns: ['date', 'region', 'channel', 'sum', 'previous'].map(name => ({ name })), rows };
  const grouped = lineSeries(timeTile, result, timeSeries(timeTile));
  assert.deepEqual(grouped.axis, ['Jan', 'Feb', 'Mar']);
  assert.equal(grouped.series.length, 6);
  assert.deepEqual(grouped.series.map(series => series.drill), [0, null, 0, null, 0, null]);
  assert.equal(grouped.series[0].points[0].row, rows[0]);
  assert.equal(linePath(grouped.series[0].points, 3, x => x, y => y), 'M0,10 M2,30 ');
  assert.equal(linePath(grouped.series[2].points, 3, x => x, y => y), 'M0,20 M2,40 ');
  assert.deepEqual(markSelection(tile, grouped.series[0].points[0].row, 0).dimensions.map(item => item.value), ['Jan', 'East', 'web']);
});
test('single dimensional lines retain order and connect adjacent values', () => {
  const one = { ...tile, dimensions: [field('date')] };
  const result = { columns: [{ name: 'date' }, { name: 'Sales' }], rows: [['B', 2], ['A', 1], ['C', 3]] };
  const grouped = lineSeries(one, result, timeSeries(one));
  assert.deepEqual(grouped.axis, ['B', 'A', 'C']);
  assert.equal(grouped.series[0].label, 'Sales');
  assert.equal(linePath(grouped.series[0].points, 1, x => x, y => y), 'M0,2 L1,1 L2,3 ');
});
test('sparse group expansion remains bounded by the chart preview mark budget', () => {
  const result = { columns: [], rows: Array.from({ length: 3000 }, (_, i) => [i, `group-${i}`, 'web', i]) };
  const preview = chartPreview(tile, result);
  const grouped = lineSeries(tile, preview, timeSeries(tile));
  assert.equal(preview.visualTruncated, true);
  assert.equal(grouped.series.reduce((count, series) => count + series.points.length, 0), 2000);
});
test('every grouped chart accepts multiple dimensions and still requires at least one', () => {
  for (const kind of ['bar', 'line', 'donut']) {
    assert.deepEqual(tileErrors({ ...tile, kind }), []);
    assert.match(tileErrors({ ...tile, kind, dimensions: [] }).join(' '), /at least one dimension/);
  }
  assert.match(tileErrors({ ...tile, kind: 'kpi' }).join(' '), /no grouping/);
});
