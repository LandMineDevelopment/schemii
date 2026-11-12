const numeric = new Set(["smallint","integer","bigint","numeric","real","double precision"]);
const aliases = {int2:"smallint",int4:"integer",int:"integer",int8:"bigint",decimal:"numeric",float4:"real",float8:"double precision",varchar:"character varying",bool:"boolean",timestamp:"timestamp without time zone",timestamptz:"timestamp with time zone",time:"time without time zone",timetz:"time with time zone"};
const equality = new Set([...numeric,"text","character varying","character","boolean","uuid","date","timestamp without time zone","timestamp with time zone","time without time zone","time with time zone","interval","bytea","jsonb","inet","cidr","macaddr","macaddr8","bit","bit varying","money","oid"]);
export function comparableTypes(left, right) {
  const normalize = value => { const name=String(value || "").trim().toLowerCase().replace(/\s+/g," ").replace(/\(\s*\d+(?:\s*,\s*\d+)?\s*\)/g,"").trim(); return aliases[name] || name; };
  left=normalize(left);right=normalize(right);
  return equality.has(left) && equality.has(right) && (left===right || (numeric.has(left)&&numeric.has(right)) || (["text","character varying"].includes(left)&&["text","character varying"].includes(right)));
}

/** Physical identity is a tuple; dots remain literal identifier characters. */
export function sourceIdentity(source, catalog, side = null) {
  return JSON.stringify([source?.[side ? `${side}Namespace` : "namespace"] || catalog.namespace || "public",
    source?.[side ? `${side}Table` : "table"] ?? source?.name ?? ""]);
}
export const sameSource = (left, right, catalog) => sourceIdentity(left, catalog) === sourceIdentity(right, catalog);
export function catalogTable(source, catalog) {
  return catalog.tables.find(table => sameSource(table, source, catalog));
}
export function sourceLabel(source, catalog) {
  const [namespace, name] = JSON.parse(sourceIdentity(source, catalog));
  return namespace === (catalog.namespace || "public") ? name : `${namespace}.${name}`;
}
export function sourceNodeId(source, catalog) {
  const [namespace, name] = JSON.parse(sourceIdentity(source, catalog));
  return namespace === (catalog.namespace || "public") ? name : JSON.stringify([namespace, name]);
}
export function relationshipColumns(relationship) {
  return relationship?.columns ?? (relationship?.sourceColumn && relationship?.targetColumn
    ? [{source: relationship.sourceColumn, target: relationship.targetColumn}] : []);
}
export const mappingLabel = (relationship, side) => relationshipColumns(relationship).map(pair => pair[side]).join(" + ");

/** Legacy IDs resolve only inside the saved default schema; never first-match. */
export function edgeRelationship(edge, catalog, nodes = null) {
  if (edge.kind === "logical") return edge;
  const compatible = relation => !nodes || ["source","target"].every(side=>{
    const node=nodes.find(node=>node.id===edge[side]);
    return node && sourceIdentity(node,catalog)===sourceIdentity(relation,catalog,side);
  });
  const exact = catalog.relationships.filter(relation => relation.id === edge.relationshipId && compatible(relation));
  if (exact.length) return exact.length === 1 ? exact[0] : undefined;
  const matches = catalog.relationships.filter(relation => compatible(relation) && relation.legacyId === edge.relationshipId
    && (relation.sourceNamespace || catalog.namespace || "public") === (catalog.namespace || "public")
    && (relation.targetNamespace || catalog.namespace || "public") === (catalog.namespace || "public"));
  return matches.length === 1 ? matches[0] : undefined;
}

export function validateLogicalRelationship(edge, draft, catalog) {
  const pairs = relationshipColumns(edge);
  if (!Array.isArray(pairs) || !pairs.length || pairs.length > 32 || pairs.some(pair => !pair || !pair.source || !pair.target))
    return "Choose one to 32 complete ordered source/target column pairs.";
  if ((edge.sourceColumn || edge.targetColumn) && edge.columns
    && JSON.stringify(pairs) !== JSON.stringify([{source:edge.sourceColumn,target:edge.targetColumn}]))
    return "Relationship columns conflict with the legacy column pair.";
  if (["source", "target"].some(side => new Set(pairs.map(pair => pair[side])).size !== pairs.length))
    return "Relationship mappings require distinct source and target columns.";
  for (const side of ["source", "target"]) {
    const node = draft.nodes.find(node => node.id === edge[side] && !node.derivation);
    if (!node || pairs.some(pair => !catalogTable(node, catalog)?.columns.some(column => column.name === pair[side])))
      return `Choose an available ${side} object and column.`;
  }
  const signature = (value, reverse = false) => JSON.stringify(relationshipColumns(value).map(pair => [pair[reverse ? "target" : "source"], pair[reverse ? "source" : "target"]]).sort());
  if (draft.edges.some(other => other.id !== edge.id && other.kind === "logical" && (
    (other.source === edge.source && other.target === edge.target && signature(other) === signature(edge)) ||
    (other.source === edge.target && other.target === edge.source && signature(other, true) === signature(edge))
  ))) return "This logical relationship already exists. Edit the existing connection.";
  const columnType = (side, column) => catalogTable(draft.nodes.find(node=>node.id===edge[side]),catalog).columns.find(value=>value.name===column).dataType;
  if (pairs.some(pair => !comparableTypes(columnType("source", pair.source),columnType("target", pair.target))))
    return "Connection columns must have comparable types without casts.";
  return "";
}
