import { exposedFields } from '#model/model-state.js';
import { fieldLabel, nodeColumns } from '#model/model-columns.js';

export function reportDraft(model, explore = model.explore) {
  return structuredClone({ ...model.definition, fields: explore?.fields || [],
    selections: explore?.selections || {}, reportFilters: explore?.reportFilters || [],
    root: model.definition.root, limit: explore?.limit || 100 });
}
export function reportExplore(draft) {
  return structuredClone({ root: draft.root, fields: draft.fields, selections: draft.selections,
    reportFilters: draft.reportFilters, limit: draft.limit });
}
export function availableFields(draft, catalog, search = '') {
  const needle = search.trim().toLocaleLowerCase();
  return exposedFields(draft, catalog).map(field => ({ ...field, label: fieldLabel(draft, catalog, field) }))
    .filter(field => field.label.toLocaleLowerCase().includes(needle));
}
export function aggregateChoices(draft, catalog, field) {
  const node = draft.nodes.find(n => n.id === field.table);
  if (node?.derivation?.kind === 'aggregate') return [['none', 'Summary value']];
  const type = nodeColumns(draft, catalog, node).find(c => c.name === field.column)?.dataType || '';
  const numeric = /^(smallint|integer|bigint|numeric|decimal|real|double precision)/.test(type);
  const comparable = numeric || /^(date|timestamp|time|text|character|varchar)/.test(type);
  return [['none', 'Plain field'], ['count', 'Count'], ['count_distinct', 'Count distinct'],
    ...(numeric ? [['sum', 'Sum'], ['avg', 'Average']] : []), ...(comparable ? [['min', 'Minimum'], ['max', 'Maximum']] : [])];
}
export function csvContent(result) {
  const escape = value => `"${String(value ?? '').replaceAll('"', '""')}"`;
  return [result.columns.map(c => c.name), ...result.rows].map(row => row.map(escape).join(',')).join('\r\n');
}
