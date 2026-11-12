import { catalogTable, relationshipColumns, edgeRelationship } from "./logical-relationships.js";
/** Suggest only an unambiguous existing FK mapping; never guess a model role. */
export function suggestSummaryConnection(draft, catalog, source, target) {
  const node = draft.nodes.find(n => n.id === source);
  if (source === target) {
    const keys = catalogTable(node, catalog)?.primaryKey || [];
    return keys.map(column => ({source: column, target: column}));
  }
  const candidates = draft.edges.filter(e => e.source === source && e.target === target)
    .map(e => edgeRelationship(e,catalog,draft.nodes)).filter(Boolean);
  if (candidates.length !== 1) return [];
  const relationship = candidates[0];
  return relationshipColumns(relationship).map(pair => ({...pair}));
}
