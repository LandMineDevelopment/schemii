import { nodeColumns } from "./model-columns.js";
// Persist semantic rules separately from presentation and ad-hoc exploration.
// A null allowlist is the existing unrestricted model contract, not a selection
// of preview outputs. Resolve it only when an author starts restricting fields.
export function exposedFields(draft, catalog) {
  if (draft.exposedFields != null) return draft.exposedFields;
  return draft.nodes.flatMap(node => nodeColumns(draft, catalog, node).map(column => ({
    table: node.id, column: column.name, aggregate: "none",
  })));
}

export function setFieldExposure(draft, catalog, table, column, enabled) {
  const node = draft.nodes.find(node => node.id === table);
  if (!nodeColumns(draft, catalog, node).some(field => field.name === column)) {
    throw new Error("This source column no longer exists.");
  }
  const fields = exposedFields(draft, catalog);
  const matches = field => field.table === table && field.column === column;
  draft.exposedFields = fields.filter(field => !matches(field));
  if (enabled) draft.exposedFields.push({ table, column, aggregate: "none" });
  if (!enabled) draft.fields = (draft.fields || []).filter(field => !matches(field));
  // Report predicates are intentional restrictions. Never silently remove one;
  // the server reports a hidden-filter conflict until the author resolves it.
}

export function inheritAliasExposure(draft, sourceId, aliasId) {
  if (draft.exposedFields == null) return;
  const existing = new Set(draft.exposedFields.filter(field => field.table === aliasId).map(field => field.column));
  const inherited = draft.exposedFields.filter(field => field.table === sourceId && !existing.has(field.column))
    .map(field => ({ table: aliasId, column: field.column, aggregate: "none" }));
  draft.exposedFields.push(...inherited);
}

// Upgrade the older canvas-selection authoring model without changing its
// visible choices. Callers keep this as an unsaved definition edit until Save.
export function initializeExposureFromPreview(draft) {
  if (draft.exposedFields != null) return false;
  const unique = new Map((draft.fields || []).map(field => [JSON.stringify([field.table, field.column]),
    { table: field.table, column: field.column, aggregate: "none" }]));
  draft.exposedFields = [...unique.values()];
  return true;
}

export function splitDraft(draft) {
  return {
    definition: { root: draft.defaultRoot ?? draft.root, nodes: draft.nodes.map(({ x, y, ...node }) => node), edges: draft.edges, scopes: draft.scopes,
      ...(draft.sourceContract !== undefined ? {sourceContract:draft.sourceContract} : {}),
      ...(draft.exposedFields !== undefined ? { exposedFields: draft.exposedFields } : {}) },
    layout: { positions: draft.nodes.filter(n => Number.isFinite(n.x) && Number.isFinite(n.y)).map(({ id, x, y }) => ({ id, x, y })) },
    explore: { root: draft.root, fields: draft.fields, selections: draft.selections, reportFilters: draft.reportFilters, limit: draft.limit || 100 },
  };
}

export function joinModel(model) {
  const positions = new Map((model.layout?.positions || []).map(p => [p.id, p]));
  return structuredClone({ ...model.definition, ...model.explore,
    fields: model.explore?.fields || [], selections: model.explore?.selections || {}, reportFilters: model.explore?.reportFilters || [],
    defaultRoot: model.definition.root, root: model.explore?.root || model.definition.root, limit: model.explore?.limit || 100,
    nodes: model.definition.nodes.map(n => ({ ...n, ...(positions.has(n.id) ? { x: positions.get(n.id).x, y: positions.get(n.id).y } : {}) })),
  });
}

export function changedParts(model, draft, name, initialLayout = model.layout) {
  const parts = splitDraft(draft);
  // Older saved previews can omit an Explore root or store an empty one. Compare
  // against the same effective defaults used by joinModel, while retaining
  // the saved model itself for revision-checked writes.
  const initialExplore = {
    root: model.explore?.root || model.definition.root,
    fields: model.explore?.fields || [],
    selections: model.explore?.selections || {},
    reportFilters: model.explore?.reportFilters || [],
    limit: model.explore?.limit || 100,
  };
  return Object.fromEntries(Object.keys(parts).map(key => [key,
    stableJson(parts[key]) !== stableJson(key === "layout" ? initialLayout : key === "explore" ? initialExplore : model[key]) || (key === "definition" && name !== model.name),
  ]));
}

function stableJson(value) {
  return JSON.stringify(value, (_key, item) => item && typeof item === "object" && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key,item[key]])) : item);
}
