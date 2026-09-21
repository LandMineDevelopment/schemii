import { comparableTypes } from "./logical-relationships.js";

export const columnComparisonOperators = new Set(["eq", "ne", "gt", "gte", "lt", "lte"]);

/** A comparison reads two fields of one row on the exact same model alias. */
export function comparisonColumns(condition, draft, catalog) {
  const node = draft.nodes.find(node => node.id === condition.table && !node.derivation);
  const columns = catalog.tables.find(table => table.name === node?.table)?.columns || [];
  const source = columns.find(column => column.name === condition.column);
  return columns.filter(column => comparableTypes(source?.dataType, column.dataType));
}

export function columnComparisonIssue(condition, draft, catalog) {
  if (condition.compareColumn == null) return "";
  if (!columnComparisonOperators.has(condition.operator)) return "Column comparisons require Equals, Does not equal, >, ≥, <, or ≤.";
  if (condition.parameterId != null || condition.value != null || condition.domain != null || condition.valueSource === "today") return "Choose a column, a fixed value, or a report input, not more than one.";
  if (!comparisonColumns(condition, draft, catalog).some(column => column.name === condition.compareColumn)) return "Choose a compatible comparison column on the same source.";
  return "";
}
