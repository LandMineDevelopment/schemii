import { dashboardUpdate, fieldKey, tileErrors, tileAggregateChoices } from './dashboard-state.js';
import { nodeColumns } from '#model/model-columns.js';
import { columnComparisonIssue } from '#model/column-comparisons.js';

// This object is independent of both the displayed dashboard and loaded context.
// Only its final, validated request can replace the saved dashboard.
export function modelUpgradeDraft(dashboard, model) {
  return dashboardUpdate(dashboard, { modelRevision: model.revision });
}

export function modelUpgradeIssues(draft, model, catalog) {
  const definition = model.definition, issues = [];
  const nodes = new Map(definition.nodes.map(node => [node.id, node]));
  const scopes = new Map(definition.scopes.map(scope => [scope.id, scope]));
  const exposed = definition.exposedFields == null ? null : new Set(definition.exposedFields.map(fieldKey));
  const fieldProblem = field => {
    const node = nodes.get(field.table);
    const label = `${node?.label || field.table} · ${field.column}`;
    if (!node) return `${label}: source was removed from the model.`;
    if (!nodeColumns(definition, catalog, node).some(column => column.name === field.column)) return `${label}: source column is unavailable.`;
    if (exposed && !exposed.has(fieldKey(field))) return `${label}: field is no longer exposed.`;
    return null;
  };
  function selections(values, tileId = null) {
    for (const [scopeId, selection] of Object.entries(values || {})) {
      const scope = scopes.get(scopeId);
      const allowed = scope && (tileId
        ? scope.kind !== 'required' && scope.requirement !== 'optional'
        : scope.kind === 'required' && scope.requirement !== 'optional' || draft.optionalFilters.includes(scopeId));
      let problem = !allowed ? 'Filter is no longer available here.' : null;
      if (!problem && selection.active && (tileId || !draft.optionalFilters.includes(scopeId))) problem = 'Only an offered optional filter may be activated.';
      const alternative = scope?.alternatives.find(item => item.id === selection.alternativeId) || (selection.alternativeId == null ? scope?.alternatives[0] : null);
      if (!problem && selection.alternativeId != null && !alternative) problem = 'The selected filter option was removed.';
      if (!problem && Object.keys(selection.values || {}).some(id => !scope.alternatives.some(option => option.inputs.some(input => input.id === id)))) problem = 'A saved filter input was removed.';
      if (problem) issues.push({ tileId, scopeId, action: 'resetSelection', message: `${scope?.label || scopeId}: ${problem}` });
    }
  }
  for (const scopeId of draft.optionalFilters) {
    if (scopes.get(scopeId)?.requirement !== 'optional') issues.push({ scopeId, action: 'removeOptional', message: `${scopes.get(scopeId)?.label || scopeId}: offered filter was removed or is no longer optional.` });
  }
  selections(draft.selections);
  if (draft.tiles.length) for (const node of definition.nodes) {
    if (!catalog.tables.some(table => table.name === node.table)) issues.push({ action: 'repairModel', message: `${node.label || node.id}: source ${node.table} is missing from the database. Repair the source model in Schemoo before upgrading.` });
  }
  for (const tile of draft.tiles) {
    const add = (message, extra = {}) => issues.push({ tileId: tile.id, message, ...extra });
    for (const message of tileErrors(tile, model, catalog)) add(message);
    for (const [key, label] of [['dimensions', 'Dimension'], ['rowDimensions', 'Pivot row dimension'], ['columnDimensions', 'Pivot column dimension'], ['measures', 'Measure'], ['detailFields', 'Detail column']]) {
      for (const field of tile[key] || []) {
        const problem = fieldProblem(field);
        if (problem) add(`${label}: ${problem}`);
        else if (key === 'measures') {
          const node = nodes.get(field.table);
          const type = nodeColumns(definition, catalog, node).find(column => column.name === field.column)?.dataType || '';
          const numeric = /^(smallint|integer|bigint|numeric|decimal|real|double precision|int[248]|float[48])(?:\b|\()/i.test(type);
          const choices = tileAggregateChoices(tile, definition, catalog, field);
          const raw = field.aggregate === 'none' && (node?.derivation?.kind !== 'aggregate' || !node.derivation.outputs.some(output => output.id === field.column));
          if (raw || !choices.some(([value]) => value === field.aggregate) || ['bar', 'line', 'donut'].includes(tile.kind) && !['count', 'count_distinct'].includes(field.aggregate) && !numeric) add(`Measure ${field.table} · ${field.column}: aggregation is no longer compatible with this field.`);
        }
      }
    }
    for (const [filterIndex, filter] of tile.reportFilters.entries()) {
      const problems = filter.conditions.flatMap(condition => [fieldProblem(condition), ...(condition.compareColumn != null ? [fieldProblem({ table: condition.table, column: condition.compareColumn })] : [])]).filter(Boolean);
      problems.push(...filter.conditions.map(condition => columnComparisonIssue(condition, definition, catalog)).filter(Boolean));
      if (!filter.conditions.length) problems.push('Choose at least one condition.');
      if (problems.length) add(`Report filter ${filter.label || filterIndex + 1}: ${[...new Set(problems)].join(' ')}`, { action: 'removeFilter', filterIndex });
    }
    selections(tile.selections, tile.id);
    if (tile.timeAnalysis) {
      const time = tile.timeAnalysis, node = nodes.get(time.table);
      const type = nodeColumns(definition, catalog, node).find(column => column.name === time.column)?.dataType || '';
      if (!/^(date|timestamp(?:\(\d+\))?(?: with(?:out)? time zone)?|timestamptz)$/i.test(type)) add('Time analysis: this dimension is no longer a date or timestamp.', { action: 'clearTime' });
    }
  }
  return issues;
}

// Every removal is an explicit review action. No predicate or selection is
// silently pruned while opening a draft or preparing its save request.
export function repairUpgradeIssue(draft, issue) {
  const tile = issue.tileId ? draft.tiles.find(tile => tile.id === issue.tileId) : null;
  if (issue.action === 'removeOptional') {
    draft.optionalFilters = draft.optionalFilters.filter(id => id !== issue.scopeId);
    delete draft.selections[issue.scopeId];
  } else if (issue.action === 'resetSelection') delete (tile ? tile.selections : draft.selections)[issue.scopeId];
  else if (issue.action === 'removeFilter' && tile) tile.reportFilters.splice(issue.filterIndex, 1);
  else if (issue.action === 'clearTime' && tile) tile.timeAnalysis = null;
}
