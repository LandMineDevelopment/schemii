import { splitDraft, exposedFields } from "./model-state.js";
import { reconcileSourceCatalog } from "./source-reconciliation.js";
import { nodeColumns } from "./model-columns.js";

export const PREVIEW_OUTPUT_LIMIT = 64;

/** Snapshot a table's exposed columns as ordinary outputs, never a SELECT * rule. */
export function tablePreviewAdditions(draft, catalog, table) {
  const node = draft.nodes.find(item => item.id === table);
  const exposed = new Set(exposedFields(draft, catalog).filter(field => field.table === table).map(field => field.column));
  const selected = new Set(draft.fields.filter(field => field.table === table && (!field.aggregate || field.aggregate === "none")).map(field => field.column));
  const columns = node ? nodeColumns(draft, catalog, node).filter(column => exposed.has(column.name)) : [];
  const fields = columns.filter(column => !selected.has(column.name)).map(column => ({ table, column: column.name, aggregate: "none" }));
  const remaining = Math.max(0, PREVIEW_OUTPUT_LIMIT - draft.fields.length);
  return { fields, total: columns.length, remaining, canAdd: fields.length > 0 && fields.length <= remaining };
}

export const previewState = draft => structuredClone(splitDraft(draft).explore);
export function samePreview(a, b) {
  const stable = value => JSON.stringify(value, (_key, item) => item && typeof item === "object" && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
  return stable(a) === stable(b);
}

/** Reuse source reconciliation without copying saved schemas or widening filters. */
export function reconcilePreview(explore, draft, catalog) {
  const next = structuredClone(draft);
  Object.assign(next, structuredClone(explore));
  reconcileSourceCatalog(next, catalog);
  const nodes = new Set(next.nodes.map(node => node.id));
  const exposure = next.exposedFields == null ? null : new Set(next.exposedFields.map(f => JSON.stringify([f.table, f.column])));
  next.fields = next.fields.filter(field => nodes.has(field.table)
    && (exposure == null || exposure.has(JSON.stringify([field.table, field.column])))
    && (!next.nodes.find(node => node.id === field.table)?.derivation
      || nodeColumns(next, catalog, next.nodes.find(node => node.id === field.table)).some(column => column.name === field.column)));
  const scopes = new Map(next.scopes.map(scope => [scope.id, scope]));
  next.selections = Object.fromEntries(Object.entries(next.selections || {}).filter(([id]) => scopes.has(id)));
  // An obsolete alternative or report predicate remains visible for repair.
  // Removing it silently could broaden the requested population.
  const state = previewState(next);
  return { explore: state, removedOutputs: (explore.fields || []).length - state.fields.length,
    changed: !samePreview(explore, state) };
}
