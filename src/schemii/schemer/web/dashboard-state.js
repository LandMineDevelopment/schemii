import { timeAnalysisErrors } from './time-analysis.js';
import { availableFields, aggregateChoices } from './report-state.js';
import { nodeColumns, fieldLabel } from '#model/model-columns.js';

export const TILE_TYPES = [['detail', 'Detail report'], ['aggregate', 'Aggregation report'], ['bar', 'Bar chart'], ['line', 'Line chart'], ['donut', 'Donut chart'], ['kpi', 'KPI']];
export const fieldKey = field => JSON.stringify([field.table, field.column]);
export function newTile() {
  return { id: crypto.randomUUID(), title: '', kind: 'bar', dimensions: [], measures: [], detailFields: [], reportFilters: [], sort: [], aggregateFilters: [], topN: null, limit: 100 };
}
export function tileErrors(tile, model, catalog) {
  const errors = [];
  if (!tile.title.trim()) errors.push('Name this tile.');
  if (tile.kind === 'detail' && !tile.detailFields.length) errors.push('Choose at least one detail column.');
  if (tile.kind !== 'detail' && !tile.measures.length) errors.push('Choose at least one measure.');
  if (['bar', 'line', 'donut'].includes(tile.kind) && !tile.dimensions.length) errors.push('Choose at least one dimension for this chart.');
  if (['kpi', 'donut'].includes(tile.kind) && tile.measures.length !== 1) errors.push('Choose one measure for this view.');
  if (tile.kind === 'kpi' && tile.dimensions.length) errors.push('A KPI has no grouping dimensions.');
  if (tile.kind !== 'detail' && !tile.detailFields.length) errors.push('Choose the columns to show when drilling into this tile.');
  if (tile.dimensions.length + tile.measures.length > 64 || tile.detailFields.length > 64) errors.push('Choose no more than 64 output columns.');
  return [...errors, ...timeAnalysisErrors(tile), ...reportControlErrors(tile, model, catalog)];
}
export const reportOutputKey = output => JSON.stringify([output.table, output.column, output.aggregate || 'none', output.kind || 'value']);
// Compare decimal values without first rounding either token through Number.
// Rejected text remains the draft value so rerenders and failed Apply preserve it.
export function numericFilterValue(text) {
  const token = text.trim();
  if (!token) return null;
  const canonical = value => {
    const match = /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:[eE]([+-]?\d+))?$/.exec(value);
    if (!match) return null;
    const fraction = match[3] ?? match[4] ?? '';
    let digits = `${match[2] || ''}${fraction}`.replace(/^0+/, '');
    if (!digits) return '0';
    const trailing = digits.length - digits.replace(/0+$/, '').length;
    digits = digits.slice(0, digits.length - trailing);
    const exponent = BigInt(match[5] || '0') - BigInt(fraction.length) + BigInt(trailing);
    return `${match[1] === '-' ? '-' : ''}${digits}e${exponent}`;
  };
  if (token.length > 4000) return text;
  const value = Number(token);
  if (!Number.isFinite(value) || Number.isInteger(value) && !Number.isSafeInteger(value)) return text;
  return canonical(token) === canonical(JSON.stringify(value)) ? value : text;
}
export function validTimeFilterValue(value, type) {
  if (typeof value !== 'string') return false;
  const match = /^(\d{2})(:?)(\d{2})(?:\2(\d{2})(?:[.,](\d{1,6}))?)?([Zz]|[+-](?:\d{2}(?::\d{2}(?::\d{2})?)?|\d{4}(?:\d{2})?))?$/.exec(value);
  if (!match) return false;
  const [, hour, , minute, second = '00', fraction = '', zone] = match;
  if (Number(hour) > 24 || Number(minute) > 59 || Number(second) > 59 || Number(hour) === 24 && Number(`${minute}${second}${fraction}`) !== 0) return false;
  const normalized = type.replace(/\(\d+\)/g, '').trim().toLowerCase();
  const zoned = normalized === 'timetz' || normalized === 'time with time zone';
  if (Boolean(zone) !== zoned) return false;
  if (zone && !/^[Zz]$/.test(zone)) {
    const parts = zone.slice(1).replaceAll(':', '');
    if (Number(parts.slice(0, 2)) > 15 || Number(parts.slice(2, 4) || 0) > 59 || Number(parts.slice(4, 6) || 0) > 59) return false;
  }
  return true;
}
export function reportOutputs(tile, model, catalog) {
  const fields = tile.kind === 'detail' ? tile.detailFields : [...tile.dimensions, ...tile.measures];
  const make = (field, kind = 'value') => {
    const node = model?.definition.nodes.find(node => node.id === field.table);
    let dataType = model ? nodeColumns(model.definition, catalog, node).find(column => column.name === field.column)?.dataType || '' : '';
    if (['count', 'count_distinct', 'sum', 'avg'].includes(field.aggregate) || ['change', 'percent_change', 'running_total'].includes(kind)) dataType = 'numeric';
    const suffix = { comparison: 'Previous value', change: 'Change', percent_change: 'Change (%)', running_total: 'Running total' }[kind];
    const label = `${field.aggregate && field.aggregate !== 'none' ? `${field.aggregate.toUpperCase()} · ` : ''}${model ? fieldLabel(model.definition, catalog, field) : `${field.table}.${field.column}`}${suffix ? ` · ${suffix}` : ''}`;
    return { table: field.table, column: field.column, aggregate: field.aggregate || 'none', kind, label, dataType };
  };
  const outputs = fields.map(field => make(field));
  if (tile.timeAnalysis) for (const field of tile.measures) {
    if ((tile.timeAnalysis.comparison || 'none') !== 'none') for (const kind of ['comparison', 'change', 'percent_change']) outputs.push(make(field, kind));
    if (tile.timeAnalysis.runningTotal) outputs.push(make(field, 'running_total'));
  }
  return outputs;
}
export function reportControlErrors(tile, model, catalog) {
  const errors = [], outputs = reportOutputs(tile, model, catalog), keys = new Map(outputs.map(output => [reportOutputKey(output), output]));
  const sort = tile.sort || [], filters = tile.aggregateFilters || [];
  if (tile.topN != null && (!Number.isSafeInteger(tile.topN) || tile.topN < 1 || tile.topN > 2147483647)) errors.push('Top N must be a positive whole number, or blank for all results.');
  if (sort.length > 32 || filters.length > 32) errors.push('Choose at most 32 sort outputs and 32 aggregate-result filters.');
  const used = new Set();
  sort.forEach((item, index) => {
    const key = reportOutputKey(item);
    if (!keys.has(key)) errors.push(`Sort ${index + 1}: choose a selected output or remove this sort.`);
    if (used.has(key)) errors.push(`Sort ${index + 1}: this output is already sorted.`);
    if (!['asc', 'desc'].includes(item.direction) || !['first', 'last'].includes(item.nulls || 'last')) errors.push(`Sort ${index + 1}: choose a direction and NULL placement.`);
    used.add(key);
  });
  const measures = new Set(tile.measures.map(field => JSON.stringify([field.table, field.column, field.aggregate || 'none'])));
  filters.forEach((item, index) => {
    const output = keys.get(reportOutputKey(item)), prefix = `Result filter ${index + 1}: `;
    if (!output || !measures.has(JSON.stringify([item.table, item.column, item.aggregate || 'none']))) { errors.push(`${prefix}choose a selected measure or enabled time-analysis output.`); return; }
    if (['is_null', 'not_null'].includes(item.operator)) { if (item.value != null) errors.push(`${prefix}NULL checks have no comparison value.`); return; }
    if (!['eq', 'ne', 'gt', 'gte', 'lt', 'lte'].includes(item.operator)) errors.push(`${prefix}choose a comparison operator.`);
    const value = item.value, type = output.dataType.replace(/\(\d+(?:,\s*\d+)?\)/g, '').trim().toLowerCase();
    if (value == null || typeof value === 'number' && !Number.isFinite(value) || typeof value === 'string' && (value.length > 4000 || value.includes('\0'))) { errors.push(`${prefix}enter a finite comparison value or choose a NULL check.`); return; }
    const numeric = /^(smallint|integer|bigint|numeric|decimal|real|double precision|int[248]|float[48])$/.test(type);
    if (model && numeric && (typeof value !== 'number' || Number.isInteger(value) && !Number.isSafeInteger(value))) errors.push(`${prefix}enter a number that can be saved without rounding (integers must be within ±9007199254740991). Your entered text is kept for correction.`);
    else if (model && /^(time(?: without time zone| with time zone)?|timetz)$/.test(type) && !validTimeFilterValue(value, type)) errors.push(`${prefix}enter an ISO time${type === 'timetz' || type.endsWith(' with time zone') ? ' with an explicit UTC offset' : ' without a UTC offset'}, for example ${type === 'timetz' || type.endsWith(' with time zone') ? '09:00:00+02:00' : '09:00:00'}.`);
    else if (model && !numeric && (type === 'boolean' ? typeof value !== 'boolean' || !['eq', 'ne'].includes(item.operator) : typeof value !== 'string')) errors.push(`${prefix}enter a ${type || 'compatible'} value.`);
  });
  return errors;
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
  return availableFields(draft, catalog, search).filter(field => {
    const node = draft.nodes.find(node => node.id === field.table);
    return nodeColumns(draft, catalog, node).some(column => column.name === field.column)
      && (!measures || aggregateChoices(draft, catalog, field).some(([value]) => value !== 'none') || node?.derivation?.kind === 'aggregate');
  });
}
