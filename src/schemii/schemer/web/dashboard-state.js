import { formattingErrors, formattingOutputs } from './number-format.js';
import { calculationErrors, visibleMeasures } from "./measures.js";
import { timeAnalysisErrors } from './time-analysis.js';
import { availableFields, aggregateChoices } from './report-state.js';
import { nodeColumns, fieldLabel } from '#model/model-columns.js';

export const TILE_TYPES = [['detail', 'Detail report'], ['aggregate', 'Aggregation report'], ['bar', 'Bar chart'], ['line', 'Line chart'], ['donut', 'Donut chart'], ['kpi', 'KPI'], ['pivot', 'Pivot table']];
export const pivotDefaults = () => ({ rowSubtotals: true, columnSubtotals: true, rowTotals: true, columnTotals: true, grandTotal: true, maximumColumns: 12, maximumRows: 100 });
export const tileDimensions = tile => tile.kind === 'pivot' ? [...(tile.rowDimensions || []), ...(tile.columnDimensions || [])] : tile.dimensions;
export function tileAggregateChoices(tile, draft, catalog, field) {
  if (tile.kind !== 'pivot') return aggregateChoices(draft, catalog, field);
  const node = draft.nodes.find(node => node.id === field.table);
  if (node?.derivation?.kind !== 'aggregate') return aggregateChoices(draft, catalog, field).filter(([value]) => value !== 'none');
  const column = nodeColumns(draft, catalog, node).find(column => column.name === field.column);
  if (!column) return [];
  const type = (column.dataType || '').replace(/\(\d+(?:,\s*\d+)?\)/g, '').trim().toLowerCase();
  const numeric = /^(smallint|integer|bigint|numeric|decimal|real|double precision|int[248]|float[48])$/.test(type);
  const comparable = numeric || /^(date|timestamp|time|text|character|varchar)/.test(type);
  const distinct = comparable || /^(boolean|uuid|bytea|jsonb|inet|cidr|macaddr)$/.test(type);
  return [['count', 'Count of per-owner values'], ...(distinct ? [['count_distinct', 'Count distinct per-owner values']] : []),
    ...(numeric ? [['sum', 'Sum of per-owner values'], ['avg', 'Average of per-owner values']] : []),
    ...(comparable ? [['min', 'Minimum per-owner value'], ['max', 'Maximum per-owner value']] : [])];
}
export const fieldKey = field => JSON.stringify([field.table, field.column]);
export function newTile() {
  return { id: crypto.randomUUID(), title: '', kind: 'bar', dimensions: [], rowDimensions: [], columnDimensions: [], pivotTotals: pivotDefaults(), measures: [], hiddenMeasures: [], calculations: [], kpiComparison: null, numberFormats: [], formatLocale: null, detailFields: [], reportFilters: [], sort: [], aggregateFilters: [], topN: null, limit: 100 };
}
export function tileErrors(tile, model, catalog) {
  const errors = [];
  const count=visibleMeasures(tile).length;
  if (!tile.title.trim()) errors.push('Name this tile.');
  if (tile.kind === 'detail' && !tile.detailFields.length) errors.push('Choose at least one detail column.');
  if (tile.kind !== 'detail' && !count) errors.push('Choose at least one measure.');
  if (['bar', 'line', 'donut'].includes(tile.kind) && !tile.dimensions.length) errors.push('Choose at least one dimension for this chart.');
  if (['kpi', 'donut'].includes(tile.kind) && count !== 1) errors.push('Choose one measure for this view.');
  if(tile.kind==='kpi' && ((tile.calculations || []).length || tile.kpiComparison) && [...tile.measures,...(tile.hiddenMeasures || [])].some(field=>(field.aggregate || "none")==="none")) errors.push("KPI calculations need explicit aggregations to produce a single complete value.");
  if (tile.kind === 'kpi' && tile.dimensions.length) errors.push('A KPI has no grouping dimensions.');
  if (tile.kind !== 'detail' && !tile.detailFields.length) errors.push('Choose the columns to show when drilling into this tile.');
  if (tileDimensions(tile).length + tile.measures.length > 64 || tile.detailFields.length > 64) errors.push('Choose no more than 64 output columns.');
  if (tile.kind === 'pivot') {
    const axes = tileDimensions(tile), totals = { ...pivotDefaults(), ...tile.pivotTotals };
    if (!tile.rowDimensions?.length || !tile.columnDimensions?.length || tile.dimensions.length) errors.push('Choose separate pivot row and column dimensions.');
    if (tile.rowDimensions?.length > 4 || tile.columnDimensions?.length > 3 || tile.measures.length > 8) errors.push('Pivot limits: four row dimensions, three column dimensions and eight measures.');
    if (new Set([...axes, ...tile.measures, ...tile.detailFields].map(fieldKey)).size > 64) errors.push('Pivot and detail columns together support at most 64 semantic inputs.');
    if (new Set(axes.map(fieldKey)).size !== axes.length) errors.push('A field can occur on only one pivot axis.');
    if (model && catalog) for (const field of tile.measures) {
      if (field.aggregate && field.aggregate !== 'none' && !tileAggregateChoices(tile, model.definition, catalog, field).some(([value]) => value === field.aggregate)) errors.push(`Measure ${field.column}: choose a compatible explicit aggregation for this field.`);
    }
    if (tile.measures.some(field => (field.aggregate || 'none') === 'none')) errors.push('Pivot measures need an explicit aggregation. A model summary value is not a raw-row total.');
    if (!Number.isInteger(totals.maximumColumns) || totals.maximumColumns < 1 || totals.maximumColumns > 24 || !Number.isInteger(totals.maximumRows) || totals.maximumRows < 1 || totals.maximumRows > 200) errors.push('Choose 1–24 column groups and 1–200 preview rows.');
    if (tile.timeAnalysis && ((tile.timeAnalysis.comparison || 'none') !== 'none' || tile.timeAnalysis.runningTotal)) errors.push('Pivot calendar grouping supports period values. Use a report or chart for comparisons and running totals.');
  }
  return [...errors, ...timeAnalysisErrors({ ...tile, dimensions: tileDimensions(tile) }), ...reportControlErrors(tile, model, catalog), ...calculationErrors(tile,reportOutputs(tile,model,catalog)), ...formattingErrors(tile,formattingOutputs(tile,reportOutputs(tile,model,catalog)))];
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
  const fields = tile.kind === 'detail' ? tile.detailFields : [...tileDimensions(tile), ...tile.measures, ...(tile.hiddenMeasures || [])];
  const make = (field, kind = 'value') => {
    const node = model?.definition.nodes.find(node => node.id === field.table);
    let dataType = model ? nodeColumns(model.definition, catalog, node).find(column => column.name === field.column)?.dataType || '' : '';
    if (['count', 'count_distinct', 'sum', 'avg'].includes(field.aggregate) || ['change', 'percent_change', 'running_total'].includes(kind)) dataType = 'numeric';
    const suffix = { comparison: 'Previous value', change: 'Change', percent_change: 'Change (%)', running_total: 'Running total' }[kind];
    const label = `${field.aggregate && field.aggregate !== 'none' ? `${field.aggregate.toUpperCase()} · ` : ''}${model ? fieldLabel(model.definition, catalog, field) : `${field.table}.${field.column}`}${suffix ? ` · ${suffix}` : ''}`;
    return { table: field.table, column: field.column, aggregate: field.aggregate || 'none', kind, label, dataType };
  };
  const outputs = fields.map(field => make(field));
  if (tile.timeAnalysis) for (const field of [...tile.measures,...(tile.hiddenMeasures || [])]) {
    if ((tile.timeAnalysis.comparison || 'none') !== 'none') for (const kind of ['comparison', 'change', 'percent_change']) outputs.push(make(field, kind));
    if (tile.timeAnalysis.runningTotal) outputs.push(make(field, 'running_total'));
  }
  outputs.push(...(tile.calculations || []).map(item=>({table:"@report",column:item.id,aggregate:"none",kind:"calculated",label:item.label || "Unnamed calculation",dataType:"numeric"})));
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
  const measures = new Set([...tile.measures,...(tile.hiddenMeasures || [])].map(field => JSON.stringify([field.table, field.column, field.aggregate || 'none'])));
  filters.forEach((item, index) => {
    const output = keys.get(reportOutputKey(item)), prefix = `Result filter ${index + 1}: `;
    if (!output || output.kind!=="calculated" && !measures.has(JSON.stringify([item.table, item.column, item.aggregate || 'none']))) { errors.push(`${prefix}choose a selected measure or enabled time-analysis output.`); return; }
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
