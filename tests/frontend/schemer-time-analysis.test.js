import test from 'node:test';
import assert from 'node:assert/strict';
import { availableTimeDimensions, selectTimeDimension, timeDimensions, timeAnalysisErrors, timeSeries } from '../../src/schemii/schemer/web/time-analysis.js';
import { tileErrors, dashboardUpdate } from '../../src/schemii/schemer/web/dashboard-state.js';
const field = (column, aggregate = 'none') => ({ table: 'events', column, aggregate });
const tile = { title: 'Monthly sales', kind: 'line', dimensions: [field('occurred')], measures: [field('amount', 'sum'), field('amount', 'count')], detailFields: [field('occurred')], timeAnalysis: { table: 'events', column: 'occurred', granularity: 'month', timezone: 'UTC', comparison: 'previous_period', runningTotal: true } };
test('time series use computed blocks per measure and never drill into composite values', () => {
  assert.deepEqual(timeSeries(tile), [{column:1,drill:0},{column:3,drill:null},{column:2,drill:1},{column:7,drill:null}]);
  assert.deepEqual(timeSeries(tile, 'running'), [{column:6,drill:null},{column:10,drill:null}]);
  assert.deepEqual(timeSeries(tile, 'change'), [{column:4,drill:null},{column:8,drill:null}]);
  assert.deepEqual(timeSeries(tile, 'percent'), [{column:5,drill:null},{column:9,drill:null}]);
  const noComparison = {...tile, timeAnalysis: {...tile.timeAnalysis, comparison: 'none'}};
  assert.deepEqual(timeSeries(noComparison, 'running'), [{column:3,drill:null},{column:4,drill:null}]);
  assert.deepEqual(timeSeries({...tile,timeAnalysis:null}), [{column:1,drill:0},{column:2,drill:1}]);
});
test('time dimension choices include timestamps with precision and exclude time-only fields', () => {
  const model = {definition:{nodes:[{id:'events',table:'events'}]}};
  const catalog = {tables:[{name:'events',columns:[{name:'occurred',dataType:'timestamp(3) with time zone'},{name:'clock',dataType:'time'},{name:'date',dataType:'date'}]}]};
  assert.deepEqual(timeDimensions({...tile,dimensions:[field('occurred'),field('clock'),field('date')]},model,catalog).map(f=>f.column), ['occurred','date']);
  assert.deepEqual(availableTimeDimensions(model,catalog).map(f=>f.column), ['occurred','date']);
});
test('choosing time preserves other chart and aggregate dimensions', () => {
  const chart = {...tile, dimensions:[field('category')], timeAnalysis:null};
  chart.timeAnalysis = {table:'events',column:'occurred'};
  selectTimeDimension(chart, field('date'));
  assert.deepEqual(chart.dimensions, [field('date'),field('category')]);
  assert.deepEqual([chart.timeAnalysis.table, chart.timeAnalysis.column], ['events','date']);

  const aggregate = {...tile, kind:'aggregate', dimensions:[field('category'),field('occurred')], timeAnalysis:{table:'events',column:'occurred'}};
  selectTimeDimension(aggregate, field('date'));
  assert.deepEqual(aggregate.dimensions, [field('category'),field('date')]);
});
test('invalid time configuration blocks save while ordinary tiles remain compatible', () => {
  assert.deepEqual(tileErrors(tile), []);
  assert.match(timeAnalysisErrors({...tile, dimensions:[]}).join(' '), /included/);
  assert.match(timeAnalysisErrors({...tile, timeAnalysis:{...tile.timeAnalysis,timezone:'not/a/zone'}}).join(' '), /valid time zone/);
  assert.match(timeAnalysisErrors({...tile, measures:[field('amount')]}).join(' '), /aggregated/);
  assert.deepEqual(timeAnalysisErrors({...tile,timeAnalysis:null}), []);
});
test('saved dashboard update retains independent time settings', () => {
  const dashboard={name:'Sales',modelId:'model',modelRevision:1,revision:2,selections:{},tiles:[tile]};
  const update=dashboardUpdate(dashboard);
  assert.deepEqual(update.tiles[0].timeAnalysis,tile.timeAnalysis);
  update.tiles[0].timeAnalysis.timezone='America/New_York';
  assert.equal(tile.timeAnalysis.timezone,'UTC');
});

test('replacing an existing time dimension preserves its chart position and other groups', () => {
  const chart = {...tile, dimensions:[field('category'),field('occurred'),field('region')], timeAnalysis:{table:'events',column:'occurred'}};
  selectTimeDimension(chart, field('date'));
  assert.deepEqual(chart.dimensions, [field('category'),field('date'),field('region')]);
});
