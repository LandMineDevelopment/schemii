/** A field may test its owner or an existing contributor-path source, never a new branch. */
export function conditionSources(draft, definition, output) {
  const owner = definition.source, contributor = output.nodeId || owner;
  if (definition.kind === "row") return new Set([owner]);
  const graph = new Map(draft.nodes.filter(n => !n.derivation).map(n => [n.id, []]));
  for (const edge of draft.edges.filter(e => e.enabled)) {
    graph.get(edge.source)?.push(edge.target); graph.get(edge.target)?.push(edge.source);
  }
  const parents = new Map([[owner, null]]), queue = [owner];
  for (let index = 0; index < queue.length; index++) for (const next of graph.get(queue[index]) || []) {
    if (!parents.has(next)) { parents.set(next, queue[index]); queue.push(next); }
  }
  const path = new Set([owner]);
  if (!parents.has(contributor)) return path; // Server reports the disconnected contributor.
  for (let node = contributor; node !== null; node = parents.get(node)) path.add(node);
  return path;
}

export function conditionSummary(condition, draft) {
  const operators = {eq:"=",ne:"≠",gt:">",gte:"≥",lt:"<",lte:"≤",in:"IN",not_in:"NOT IN",contains:"contains",is_null:"IS NULL",not_null:"IS NOT NULL"};
  const source = draft.nodes.find(n => n.id === condition.table)?.label || condition.table;
  const unary = ["is_null","not_null"].includes(condition.operator);
  const value = unary ? "" : ` ${condition.compareColumn != null ? `${source}.${condition.compareColumn || "(choose column)"}` : condition.valueSource === "today" ? "Today (UTC)" : JSON.stringify(condition.value ?? "")}`;
  return `${source}.${condition.column} ${operators[condition.operator] || condition.operator}${value}${condition.allowNull && !unary ? " OR NULL" : ""}`;
}
