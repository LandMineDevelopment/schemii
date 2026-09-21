import { conditionSources } from "./derived-conditions.js";
import { columnComparisonIssue } from "./column-comparisons.js";

/** Model occurrence IDs, never physical table names: aliases have separate rules. */
export function boundFilterSources(scope) {
  return new Set((scope.alternatives || []).flatMap(option => (option.conditions || []).map(condition => condition.table)).filter(Boolean));
}

/** Exact authored bindings only; aliases and calculated outputs are not interchangeable. */
export function columnFilterBindings(draft, nodeId, column) {
  const node = draft.nodes.find(candidate => candidate.id === nodeId);
  if (!node || node.derivation) return [];
  return (draft.scopes || []).flatMap(scope => (scope.alternatives || []).flatMap(alternative => {
    const conditionIndexes = (alternative.conditions || []).flatMap((condition, index) => condition.table === nodeId && condition.column === column ? [index] : []);
    return conditionIndexes.length ? [{scope, alternative, conditionIndexes, conditions: conditionIndexes.map(index => alternative.conditions[index])}] : [];
  }));
}

/** Plain text only; callers render this with textContent, never as SQL or markup. */
export function describeFilterCondition(condition, alternative, draft, context = {}) {
  const node = draft.nodes.find(candidate => candidate.id === condition.table);
  const field = condition.table === context.table
    ? (condition.column === context.column ? "" : condition.column || "column")
    : `${node?.label || node?.table || condition.table || "Choose source"}.${condition.column || "column"}`;
  const prefix = field ? `${field} ` : "";
  const operators = {eq: "equals", ne: "does not equal", in: "IN", not_in: "NOT IN", gt: ">", gte: "≥", lt: "<", lte: "≤", contains: "contains", range_contains_date: "contains date", is_null: "IS NULL", not_null: "IS NOT NULL"};
  const comparison = operators[condition.operator] || condition.operator;
  if (["is_null", "not_null"].includes(condition.operator)) return `${prefix}${comparison}`;
  const literal = value => value === undefined ? "Choose value" : value === null ? "NULL" : JSON.stringify(value);
  let value;
  if (condition.parameterId) {
    const input = (alternative?.inputs || []).find(candidate => candidate.id === condition.parameterId);
    value = `[${input?.label || "Missing report input"}]`;
  } else if (condition.compareColumn != null) value = `${node?.label || node?.table || condition.table}.${condition.compareColumn || "(choose column)"}`;
  else if (condition.valueSource === "today") value = "Today (UTC)";
  else value = Array.isArray(condition.value) ? `(${condition.value.map(literal).join(", ")})` : literal(condition.value);
  const predicate = `${prefix}${comparison} ${value}`;
  return condition.allowNull ? `(${predicate} OR ${prefix}IS NULL)` : predicate;
}

/** Rules that can affect this object, including a calculated object's input paths.
 * `direct: false` identifies required model-wide rules, not a source binding.
 * This is navigation metadata; the compiler decides which alternative executes.
 */
export function filtersAffectingNode(draft, nodeId) {
  const node = draft.nodes.find(candidate => candidate.id === nodeId);
  if (!node) return [];
  const sources = new Set([nodeId]);
  if (node.derivation) {
    sources.add(node.derivation.source);
    for (const output of node.derivation.outputs || []) {
      for (const source of conditionSources(draft, node.derivation, output)) sources.add(source);
    }
  }
  return (draft.scopes || []).flatMap(scope => {
    const direct = [...boundFilterSources(scope)].some(source => sources.has(source));
    return direct || scope.kind === "required" ? [{scope, direct}] : [];
  });
}

/** Authoring hints only. Server validation remains authoritative. */
export function modelFilterIssue(scope, draft, catalog) {
  if (!scope.label?.trim()) return "Give this filter a name.";
  if (!scope.alternatives?.length) return "Add at least one option.";
  for (const option of scope.alternatives) {
    if (!option.label?.trim()) return "Give each option a name.";
    for (const input of option.inputs || []) {
      if (!input.label?.trim()) return "Give each report input a name.";
      if (input.domain) {
        const domain = input.domain;
        const table = domain.nodeId != null ? draft.nodes.find(n => n.id === domain.nodeId)?.table : domain.table;
        const columns = catalog.tables.find(t => t.name === table)?.columns || [];
        if (!columns.some(c => c.name === domain.column) || (domain.labelColumn && !columns.some(c => c.name === domain.labelColumn))) return "Choose a valid domain value column and display label. Its source may have been removed.";
      }
      if (!(option.conditions || []).some(c => c.parameterId === input.id)) return `Bind “${input.label}” to a source, or remove that unused input for a fixed rule.`;
    }
    for (const c of option.conditions || []) {
      const comparisonIssue = columnComparisonIssue(c, draft, catalog);
      if (comparisonIssue) return comparisonIssue;
      if (["in", "not_in"].includes(c.operator) && !c.parameterId && (!Array.isArray(c.value) || !c.value.length)) return "Choose at least one value for IN / NOT IN.";
      if (c.domain && c.value == null) return "Choose a fixed value from the domain list.";
      if (c.domain) {
        const table = c.domain.nodeId != null ? draft.nodes.find(n => n.id === c.domain.nodeId)?.table : c.domain.table;
        const columns = catalog.tables.find(t => t.name === table)?.columns || [];
        if (!columns.some(column => column.name === c.domain.column) || (c.domain.labelColumn && !columns.some(column => column.name === c.domain.labelColumn))) return "Choose a valid fixed-value domain source and display label.";
      }
      const node = draft.nodes.find(n => n.id === c.table);
      if (!catalog.tables.find(t => t.name === node?.table)?.columns.some(column => column.name === c.column)) return "Choose a valid source column for every condition.";
      if (c.parameterId && !(option.inputs || []).some(p => p.id === c.parameterId)) return "A condition refers to a removed input. Choose its value source again.";
    }
  }
  return "";
}
