// Aliases are model occurrences, never new physical tables or copied database objects.
export function isAlias(node) {
  return Boolean(node && !node.derivation && node.id !== node.table);
}

export function suggestedAliasLabel(nodes, source, maximumLength = 100) {
  const base = String(source?.label || source?.table || "Table").trim() || "Table";
  const labels = new Set((nodes || []).map(node => node.label));
  for (let number = 1; ; number++) {
    const suffix = number === 1 ? " (alias)" : ` (alias ${number})`;
    const candidate = `${base.slice(0, Math.max(1, maximumLength - suffix.length)).trimEnd()}${suffix}`;
    if (!labels.has(candidate)) return candidate;
  }
}

function edgeKey(relationshipId, source, target) {
  return JSON.stringify([relationshipId, source, target]);
}

function stableHash(value) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index++) {
    hash = Math.imul(hash ^ value.charCodeAt(index), 16777619);
  }
  return (hash >>> 0).toString(36);
}

export function ensureAliasConnections(draft, catalog) {
  const nodes = draft.nodes || [];
  const canonical = new Map(nodes.filter(node => !node.derivation && !isAlias(node)).map(node => [node.table, node.id]));
  const edges = draft.edges ||= [];
  const tuples = new Set(edges.map(edge => edgeKey(edge.relationshipId, edge.source, edge.target)));
  const ids = new Set(edges.map(edge => edge.id));
  let added = 0;
  for (const alias of nodes.filter(isAlias)) {
    for (const relationship of catalog.relationships || []) {
      const outgoing = relationship.sourceTable === alias.table;
      const incoming = relationship.targetTable === alias.table;
      if (!outgoing && !incoming) continue;
      // A physical self-FK remains a self-FK on each occurrence, not a guessed
      // cross-role connection. The author can redirect its endpoints explicitly.
      const source = outgoing ? alias.id : canonical.get(relationship.sourceTable);
      const target = incoming ? alias.id : canonical.get(relationship.targetTable);
      if (!source || !target) continue;
      const tuple = edgeKey(relationship.id, source, target);
      if (tuples.has(tuple)) continue;
      const stem = `alias_fk_${alias.id.slice(0, 70)}_${String(relationship.id).slice(0, 70)}_${stableHash(tuple)}`;
      let id = stem;
      let suffix = 1;
      while (ids.has(id)) id = `${stem}_${suffix++}`;
      edges.push({ id, relationshipId: relationship.id, source, target, enabled: false });
      tuples.add(tuple);
      ids.add(id);
      added++;
    }
  }
  return added;
}

function conditions(draft) {
  return [
    ...(draft.scopes || []).flatMap(scope => (scope.alternatives || []).flatMap(alternative => alternative.conditions || [])),
    ...(draft.reportFilters || []).flatMap(group => group.conditions || []),
    ...(draft.filters || []),
  ];
}

export function aliasImpact(draft, id) {
  return {
    connections: (draft.edges || []).filter(edge => edge.source === id || edge.target === id).length,
    fields: (draft.fields || []).filter(field => field.table === id).length,
    bindings: conditions(draft).filter(condition => condition.table === id || condition.domain?.nodeId === id).length + (draft.scopes || []).flatMap(s => s.alternatives || []).flatMap(a => a.inputs || []).filter(p => p.domain?.nodeId === id).length,
    isRoot: draft.root === id,
  };
}

export function removeAlias(draft, id) {
  const node = (draft.nodes || []).find(candidate => candidate.id === id);
  if (!isAlias(node)) throw new Error("Only model aliases can be deleted. Original warehouse tables are protected.");
  return removeModelNode(draft,id);
}

// Used for confirmed alias removal and explicit removal of a missing source.
// This edits model metadata only; callers decide which objects are removable.
export function removeModelNode(draft, id) {
  const node = (draft.nodes || []).find(candidate => candidate.id === id);
  if (!node) throw new Error("This model object no longer exists.");
  if (draft.nodes.some(n => n.derivation && (n.derivation.source === id || n.derivation.connection?.target === id || n.derivation.outputs.some(o => o.nodeId === id || o.conditions?.some(c => c.table === id))))) {
    throw new Error("Remove or rebind the calculated sources that depend on this object first.");
  }
  const impact = aliasImpact(draft, id);
  draft.nodes = draft.nodes.filter(candidate => candidate.id !== id);
  draft.edges = (draft.edges || []).filter(edge => edge.source !== id && edge.target !== id);
  draft.fields = (draft.fields || []).filter(field => field.table !== id);
  if (draft.exposedFields) draft.exposedFields=draft.exposedFields.filter(field=>field.table!==id);
  if (impact.isRoot) {
    draft.root = draft.nodes.find(candidate => candidate.id === node.table && candidate.table === node.table)?.id || draft.nodes[0]?.id || "";
  }
  if (draft.defaultRoot===id) draft.defaultRoot=draft.root;
  // Never silently loosen a model restriction. Keep the predicate, parameter,
  // and alternative, but require the author to bind its missing source again.
  for (const condition of conditions(draft)) {
    if (condition.table !== id) continue;
    condition.table = "";
    condition.column = "";
  }
  return impact;
}
