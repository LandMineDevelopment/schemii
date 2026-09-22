import { timeAnalysisErrors } from './time-analysis.js';
import { availableFields, aggregateChoices } from './report-state.js';

export const TILE_TYPES = [['detail', 'Detail report'], ['aggregate', 'Aggregation report'], ['bar', 'Bar chart'], ['line', 'Line chart'], ['donut', 'Donut chart'], ['kpi', 'KPI']];
export const fieldKey = field => JSON.stringify([field.table, field.column]);
export function newTile() {
  return { id: crypto.randomUUID(), title: '', kind: 'bar', dimensions: [], measures: [], detailFields: [], reportFilters: [], limit: 100 };
}
export function tileErrors(tile) {
  const errors = [];
  if (!tile.title.trim()) errors.push('Name this tile.');
  if (tile.kind === 'detail' && !tile.detailFields.length) errors.push('Choose at least one detail column.');
  if (tile.kind !== 'detail' && !tile.measures.length) errors.push('Choose at least one measure.');
  if (['bar', 'line', 'donut'].includes(tile.kind) && tile.dimensions.length !== 1) errors.push('Choose one dimension for this chart.');
  if (['kpi', 'donut'].includes(tile.kind) && tile.measures.length !== 1) errors.push('Choose one measure for this view.');
  if (tile.kind === 'kpi' && tile.dimensions.length) errors.push('A KPI has no grouping dimensions.');
  if (tile.kind !== 'detail' && !tile.detailFields.length) errors.push('Choose the columns to show when drilling into this tile.');
  if (tile.dimensions.length + tile.measures.length > 64 || tile.detailFields.length > 64) errors.push('Choose no more than 64 output columns.');
  return [...errors, ...timeAnalysisErrors(tile)];
}
export function dashboardUpdate(dashboard, patch = {}) {
  return { name: dashboard.name, modelId: dashboard.modelId, modelRevision: dashboard.modelRevision,
    optionalFilters: structuredClone(dashboard.optionalFilters || []), selections: structuredClone(dashboard.selections),
    tiles: structuredClone(dashboard.tiles), expectedRevision: dashboard.revision, ...patch };
}
export function markSelection(tile, row, measureIndex) {
  return { dimensions: tile.dimensions.map((field, index) => ({ table: field.table, column: field.column, value: row[index] })), measureIndex };
}
export function fieldChoices(model, catalog, search = '', measures = false) {
  const draft = { ...model.definition, fields: [] };
  return availableFields(draft, catalog, search).filter(field => !measures || aggregateChoices(draft, catalog, field).some(([value]) => value !== 'none') || model.definition.nodes.find(n => n.id === field.table)?.derivation?.kind === 'aggregate');
}
