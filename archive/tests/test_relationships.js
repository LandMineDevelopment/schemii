const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
const styles = fs.readFileSync("src/schemii/web/styles.css", "utf8");
const start = source.indexOf("function relationshipColumnPairs");
const end = source.indexOf("function relationshipIncludesColumn", start);
assert.notEqual(start, -1, "relationship helper start marker is missing");
assert.notEqual(end, -1, "relationship helper end marker is missing");

const edge = {
  id: "edge",
  name: "graph_edge",
  columns: [
    { id: "edge_graph", name: "graph_id", type: "uuid" },
    { id: "edge_from", name: "from_node_id", type: "uuid" },
    { id: "edge_to", name: "to_node_id", type: "uuid" }
  ],
  uniqueConstraints: [],
  checks: [{ id: "edge_from_check", name: "graph_edge_from_check", columnIds: ["edge_from"] }]
};
const node = {
  id: "node",
  name: "graph_node",
  columns: [
    { id: "node_graph", name: "graph_id", type: "uuid" },
    { id: "node_id", name: "id", type: "uuid", primary: true, unique: true },
    { id: "node_name", name: "name", type: "text" }
  ],
  primaryKey: { id: "node_pk", columnIds: ["node_id"] },
  uniqueConstraints: [{ id: "node_identity", name: "graph_node_graph_id_id_key", columnIds: ["node_graph", "node_id"] }]
};
const relationship = {
  id: "from_fk",
  name: "graph_edge_from_node_id_fkey",
  constraintName: "graph_edge_from_node_id_fkey",
  fromTableId: edge.id,
  toTableId: node.id,
  fromColumnId: "edge_from",
  toColumnId: "node_id"
};
const context = vm.createContext({
  TextEncoder,
  schema: { tables: [edge, node], relationships: [relationship] }
});
vm.runInContext(`
  function getTable(tableId) { return schema.tables.find(table => table.id === tableId); }
  function getColumn(tableId, columnId) { return getTable(tableId)?.columns.find(column => column.id === columnId); }
  function postgresNameWithSuffix(base, suffix) { return base + suffix; }
  function defaultPrimaryKeyName(tableName) { return tableName + "_pkey"; }
  function columnCheckConstraints(table, column) { return (table.checks ?? []).filter(check => check.columnIds.includes(column.id)); }
  ${source.slice(start, end)}
  globalThis.relationshipColumnPairs = relationshipColumnPairs;
  globalThis.setRelationshipColumnPairs = setRelationshipColumnPairs;
  globalThis.reorderRelationshipPair = reorderRelationshipPair;
  globalThis.dropRelationshipPair = dropRelationshipPair;
  globalThis.columnForeignKeyRelationships = columnForeignKeyRelationships;
  globalThis.columnDatabaseIconTargets = columnDatabaseIconTargets;
  globalThis.availableRelationshipName = availableRelationshipName;
  globalThis.tableHasReferencedKey = tableHasReferencedKey;
  globalThis.validateRelationshipDraft = validateRelationshipDraft;
`, context);

const compositePairs = [
  { fromColumnId: "edge_graph", toColumnId: "node_graph" },
  { fromColumnId: "edge_from", toColumnId: "node_id" }
];
const canonical = context.setRelationshipColumnPairs({ ...relationship }, compositePairs);
assert.deepEqual(Array.from(canonical.fromColumnIds), ["edge_graph", "edge_from"]);
assert.deepEqual(Array.from(canonical.toColumnIds), ["node_graph", "node_id"]);
assert.equal("fromColumnId" in canonical, false);
assert.equal("toColumnId" in canonical, false);

const single = context.setRelationshipColumnPairs({ ...canonical }, [compositePairs[1]]);
assert.equal(single.fromColumnId, "edge_from");
assert.equal(single.toColumnId, "node_id");
assert.equal("fromColumnIds" in single, false);
assert.equal("toColumnIds" in single, false);
let foreignKeys = context.columnForeignKeyRelationships(edge, edge.columns[1]);
assert.equal(foreignKeys.single.length, 1);
assert.equal(foreignKeys.composite.length, 0);
let iconTargets = context.columnDatabaseIconTargets("foreign-key", edge, edge.columns[1]);
assert.deepEqual(Array.from(iconTargets, target => target.id), ["from_fk"]);
iconTargets = context.columnDatabaseIconTargets("check", edge, edge.columns[1]);
assert.deepEqual(Array.from(iconTargets, target => target.id), ["edge_from_check"]);
iconTargets = context.columnDatabaseIconTargets("primary-key", node, node.columns[1]);
assert.deepEqual(Array.from(iconTargets, target => target.label), ["graph_node_pkey"]);
assert.deepEqual(Array.from(iconTargets, target => target.columnId), ["node_id"]);

const ordered = [{ id: "first" }, { id: "second" }, { id: "third" }];
assert.equal(context.reorderRelationshipPair(ordered, 2, 0), true);
assert.deepEqual(Array.from(ordered, pair => pair.id), ["third", "first", "second"]);
assert.equal(context.dropRelationshipPair(ordered, 0, 2, true), true);
assert.deepEqual(Array.from(ordered, pair => pair.id), ["first", "second", "third"]);
assert.equal(context.dropRelationshipPair(ordered, 1, 1, false), false);

assert.equal(context.tableHasReferencedKey(node, ["node_id", "node_graph"]), true);
assert.equal(context.validateRelationshipDraft(relationship, compositePairs, relationship.name), "");
assert.equal(
  context.validateRelationshipDraft(relationship, [compositePairs[0], compositePairs[0]], relationship.name),
  "Each foreign key column can appear only once"
);

node.uniqueConstraints = [];
assert.equal(
  context.validateRelationshipDraft(relationship, compositePairs, relationship.name),
  "Referenced columns must match a primary or unique key"
);
node.uniqueConstraints = [{ id: "node_identity", columnIds: ["node_graph", "node_id"] }];

context.schema.relationships.push({
  id: "duplicate",
  name: "graph_edge_duplicate_fkey",
  fromTableId: edge.id,
  toTableId: node.id,
  fromColumnIds: ["edge_graph", "edge_from"],
  toColumnIds: ["node_graph", "node_id"]
});
foreignKeys = context.columnForeignKeyRelationships(edge, edge.columns[0]);
assert.equal(foreignKeys.single.length, 0);
assert.equal(foreignKeys.composite.length, 1);
iconTargets = context.columnDatabaseIconTargets("composite-foreign-key", edge, edge.columns[0]);
assert.deepEqual(Array.from(iconTargets, target => target.id), ["duplicate"]);
foreignKeys = context.columnForeignKeyRelationships(edge, edge.columns[1]);
assert.equal(foreignKeys.single.length, 1);
assert.equal(foreignKeys.composite.length, 1);
assert.equal(
  context.validateRelationshipDraft(relationship, compositePairs, relationship.name),
  "That relationship already exists"
);
assert.equal(
  context.validateRelationshipDraft(relationship, [...compositePairs].reverse(), relationship.name),
  "That relationship already exists"
);
assert.equal(
  context.validateRelationshipDraft(relationship, [{ fromColumnId: "edge_to", toColumnId: "node_id" }], "graph_edge_duplicate_fkey"),
  "That foreign key name already exists on the source table"
);

assert.equal(
  context.availableRelationshipName({ id: "new", fromTableId: edge.id }, [{ fromColumnId: "edge_from" }], "new"),
  "graph_edge_from_node_id_fkey_2"
);

const saveStart = source.indexOf("function saveRelationshipEditor");
const saveEnd = source.indexOf("function applyView", saveStart);
assert.notEqual(saveStart, -1, "relationship save marker is missing");
assert.notEqual(saveEnd, -1, "relationship save end marker is missing");
const saveEdge = {
  id: "edge",
  columns: [{ id: "edge_from", name: "from_node_id", type: "integer" }]
};
const saveNode = {
  id: "node",
  columns: [{ id: "node_id", name: "id", type: "uuid" }]
};
const saveRelationship = {
  id: "from_fk",
  fromTableId: "edge",
  fromColumnId: "edge_from",
  toTableId: "node",
  toColumnId: "node_id"
};
const saveContext = vm.createContext({
  schema: { postgres: { namespace: "public" }, tables: [saveEdge, saveNode], relationships: [saveRelationship] },
  relationshipEditorState: {
    relationship: saveRelationship,
    pairs: [{ fromColumnId: "edge_from", toColumnId: "node_id" }],
    isNew: false,
    name: "graph_edge_from_node_id_fkey"
  },
  checkpointType: null
});
vm.runInContext(`
  function getTable(id) { return schema.tables.find(table => table.id === id); }
  function getColumn(tableId, columnId) { return getTable(tableId).columns.find(column => column.id === columnId); }
  function validateRelationshipDraft() { return ""; }
  function checkpointHistory() { checkpointType = getColumn("edge", "edge_from").type; }
  function setRelationshipColumnPairs(item, pairs) {
    item.fromColumnId = pairs[0].fromColumnId;
    item.toColumnId = pairs[0].toColumnId;
  }
  function saveSchema() {}
  function closeRelationshipEditor() {}
  function render() {}
  function showToast() {}
  ${source.slice(saveStart, saveEnd)}
  globalThis.saveRelationshipEditor = saveRelationshipEditor;
`, saveContext);
saveContext.saveRelationshipEditor();
assert.equal(saveContext.checkpointType, "integer");
assert.equal(saveEdge.columns[0].type, "uuid");
assert.equal(saveContext.schema.relationships[0].constraintName, "graph_edge_from_node_id_fkey");

assert.match(styles, /\.relationship-pair\s*\{[^}]*grid-template-columns:\s*18px minmax\(0, 1fr\) 20px minmax\(0, 1fr\) auto;/);
assert.match(styles, /\.relationship-pair label\s*\{[^}]*grid-column:\s*auto;/);
assert.match(styles, /\.relationship-pair-drag\s*\{[^}]*align-self:\s*center;[^}]*border:\s*0;[^}]*outline:\s*0;/);
assert.match(source, /class="relationship-pair-drag" draggable="true"/);
assert.match(source, /class="database-object-icon foreign-key-icon"/);
assert.match(source, /class="database-object-icon composite-foreign-key-icon"/);
assert.match(styles, /\.foreign-key-icon\s*\{\s*color:\s*#65a9ff;/, "single foreign keys must retain their blue color");
assert.match(styles, /\.composite-foreign-key-icon\s*\{\s*color:\s*#9b82f4;/, "composite foreign keys must retain their purple color");
assert.match(source, /<circle cx="5" cy="7" r="3"\/><path d="m7\.5 8\.5 5 4M10 10\.5l1\.5-1\.5"\/>/, "single foreign keys must use the primary-key shape");
const keyBadgeMarkup = source.slice(source.indexOf('<span class="key-badge'), source.indexOf('<span class="column-name'));
assert.match(keyBadgeMarkup, /data-object-icon="foreign-key"/);
assert.match(keyBadgeMarkup, /data-object-icon="composite-foreign-key"/);
assert.match(source, /elements\.tablesLayer\.addEventListener\("contextmenu"/);
assert.match(source, /class="connection-hit" data-relationship-id=/, "connections must carry relationship identity on a hit target");
assert.match(source, /elements\.connections\.addEventListener\("contextmenu"/, "connection right-clicks must open relationship editing");
assert.match(source, /event\.target\.closest\("\.connection-hit"\)/, "connection clicks must not dismiss the workspace inspector");
assert.match(styles, /\.connection-hit\s*\{[^}]*stroke-width:\s*14;[^}]*pointer-events:\s*stroke;/, "connections need a usable pointer hit area");
assert.match(styles, /\.connection-hit:hover \+ \.connection-line\s*\{[^}]*stroke:\s*#8a96a5;[^}]*stroke-width:\s*2;[^}]*filter:\s*none;/, "connection hover should remain subtle");

console.log("Relationship editor tests passed");
