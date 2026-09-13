const numeric = new Set(["smallint","integer","bigint","numeric","real","double precision"]);
const aliases = {int2:"smallint",int4:"integer",int:"integer",int8:"bigint",decimal:"numeric",float4:"real",float8:"double precision",varchar:"character varying",bool:"boolean",timestamp:"timestamp without time zone",timestamptz:"timestamp with time zone",time:"time without time zone",timetz:"time with time zone"};
const equality = new Set([...numeric,"text","character varying","character","boolean","uuid","date","timestamp without time zone","timestamp with time zone","time without time zone","time with time zone","interval","bytea","jsonb","inet","cidr","macaddr","macaddr8","bit","bit varying","money","oid"]);
export function comparableTypes(left, right) {
  const normalize = value => { const name=String(value || "").trim().toLowerCase().replace(/\s+/g," ").replace(/\(\s*\d+(?:\s*,\s*\d+)?\s*\)/g,"").trim(); return aliases[name] || name; };
  left=normalize(left);right=normalize(right);
  return equality.has(left) && equality.has(right) && (left===right || (numeric.has(left)&&numeric.has(right)) || (["text","character varying"].includes(left)&&["text","character varying"].includes(right)));
}

/** Resolve an edge's join columns without treating model relationships as database FKs. */
export function edgeRelationship(edge, catalog) {
  return edge.kind === "logical" ? edge : catalog.relationships.find(relation => relation.id === edge.relationshipId);
}

export function validateLogicalRelationship(edge, draft, catalog) {
  for (const side of ["source", "target"]) {
    const node = draft.nodes.find(node => node.id === edge[side] && !node.derivation);
    if (!node || !catalog.tables.find(table => table.name === node.table)?.columns.some(column => column.name === edge[`${side}Column`])) {
      return `Choose an available ${side} object and column.`;
    }
  }
  if (draft.edges.some(other => other.id !== edge.id && other.kind === "logical" && (
    (other.source === edge.source && other.target === edge.target && other.sourceColumn === edge.sourceColumn && other.targetColumn === edge.targetColumn) ||
    (other.source === edge.target && other.target === edge.source && other.sourceColumn === edge.targetColumn && other.targetColumn === edge.sourceColumn)
  ))) return "This logical relationship already exists. Edit the existing connection.";
  const columnType = side => {const node=draft.nodes.find(node=>node.id===edge[side]);return catalog.tables.find(table=>table.name===node.table).columns.find(column=>column.name===edge[`${side}Column`]).dataType;};
  if(!comparableTypes(columnType("source"),columnType("target")))return "Connection columns must have comparable types without casts.";
  return "";
}
