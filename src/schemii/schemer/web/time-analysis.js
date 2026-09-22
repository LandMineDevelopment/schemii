import { nodeColumns } from '#model/model-columns.js';
import { availableFields } from './report-state.js';

const timeType = /^(date|timestamp(?:\(\d+\))?(?: with(?:out)? time zone)?|timestamptz)$/i;

function isTimeField(field, model, catalog) {
  const node = model.definition.nodes.find(item => item.id === field.table);
  const type = nodeColumns(model.definition, catalog, node).find(item => item.name === field.column)?.dataType || '';
  return timeType.test(type);
}

export function availableTimeDimensions(model, catalog) {
  const draft = { ...model.definition, fields: [] };
  return availableFields(draft, catalog).filter(field => isTimeField(field, model, catalog));
}

export function timeDimensions(tile, model, catalog) {
  return tile.dimensions.filter(field => isTimeField(field, model, catalog));
}

export function selectTimeDimension(tile, field) {
  const selected = { table: field.table, column: field.column, aggregate: 'none' };
  const previous = tile.timeAnalysis && [tile.timeAnalysis.table, tile.timeAnalysis.column];
  const matches = (candidate, key) => key && candidate.table === key[0] && candidate.column === key[1];
  if (['bar', 'line', 'donut'].includes(tile.kind)) {
    tile.dimensions = [selected];
  } else {
    const previousIndex = tile.dimensions.findIndex(candidate => matches(candidate, previous));
    const dimensions = tile.dimensions.filter(candidate => !matches(candidate, previous) && !matches(candidate, [selected.table, selected.column]));
    dimensions.splice(Math.min(previousIndex < 0 ? dimensions.length : previousIndex, dimensions.length), 0, selected);
    tile.dimensions = dimensions;
  }
  if (tile.timeAnalysis) {
    tile.timeAnalysis.table = selected.table;
    tile.timeAnalysis.column = selected.column;
  }
}

export function timeAnalysisErrors(tile) {
  const time = tile.timeAnalysis;
  if (!time) return [];
  const errors = [];
  if (['detail', 'kpi'].includes(tile.kind)) errors.push('Time analysis needs a grouped view.');
  if (!tile.dimensions.some(field => field.table === time.table && field.column === time.column)) errors.push('Choose a time dimension that is included in this view.');
  if (!time.timezone?.trim()) errors.push('Enter a time zone.');
  else { try { new Intl.DateTimeFormat('en', { timeZone: time.timezone }); } catch { errors.push('Enter a valid time zone, such as UTC or America/New_York.'); } }
  if (tile.measures.some(field => field.aggregate === 'none')) errors.push('Time analysis requires aggregated measures.');
  return errors;
}

// The query keeps base outputs first, then a computed block for each measure.
export function timeSeries(tile, metric = 'values') {
  const time = tile.timeAnalysis;
  const count = tile.measures.length, offset = tile.dimensions.length;
  const comparison = time && time.comparison !== 'none';
  const width = (comparison ? 3 : 0) + (time?.runningTotal ? 1 : 0);
  return tile.measures.flatMap((_, index) => {
    const computed = offset + count + index * width;
    if (metric === 'running' && time?.runningTotal) return [{ column: computed + (comparison ? 3 : 0), drill: null }];
    if (metric === 'change' && comparison) return [{ column: computed + 1, drill: null }];
    if (metric === 'percent' && comparison) return [{ column: computed + 2, drill: null }];
    return [{ column: offset + index, drill: index }, ...(comparison ? [{ column: computed, drill: null }] : [])];
  });
}
