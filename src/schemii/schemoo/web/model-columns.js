import { catalogTable } from "./logical-relationships.js";
/** Resolve authored outputs separately from physical catalog columns. */
export function nodeColumns(draft, catalog, node) {
  if (!node) return [];
  if (!node.derivation) return catalogTable(node, catalog)?.columns || [];
  const owner = draft.nodes.find(n => n.id === node.derivation.source);
  const physical = id => catalogTable(draft.nodes.find(n => n.id === id), catalog)?.columns || [];
  const keys = node.derivation.kind === "aggregate" ? (node.derivation.groupBy || []).map(name => physical(owner?.id).find(c => c.name === name)).filter(Boolean) : [];
  return [...keys, ...node.derivation.outputs.map(output => ({
    name: output.id, label: output.label,
    dataType: output.operation === "list" ? "text" : ["count", "count_distinct"].includes(output.operation) ? "bigint"
      : ["add", "subtract", "multiply", "divide", "avg"].includes(output.operation) ? "numeric"
      : physical(output.nodeId || owner?.id).find(c => c.name === output.column)?.dataType || "numeric",
  }))];
}

export function fieldLabel(draft, catalog, field) {
  const node = draft.nodes.find(n => n.id === field.table);
  const column = nodeColumns(draft, catalog, node).find(c => c.name === field.column);
  return `${node?.label || field.table} · ${column?.label || field.column}`;
}
