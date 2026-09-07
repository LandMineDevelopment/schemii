import test from "node:test";
import assert from "node:assert/strict";
import { aliasImpact, ensureAliasConnections, isAlias, removeAlias, removeModelNode, suggestedAliasLabel } from "../../src/schemii/schemoo/web/alias-model.js";

test("alias names are unique, source-derived, and respect the saved label bound", () => {
  const nodes = [{ id:"people", table:"people", label:"People" }];
  assert.equal(suggestedAliasLabel(nodes, nodes[0]), "People (alias)");
  nodes.push({ id:"alias_1", table:"people", label:"People (alias)" });
  assert.equal(suggestedAliasLabel(nodes, nodes[0]), "People (alias 2)");
  const long = { table:"people", label:"A".repeat(100) };
  assert.equal(suggestedAliasLabel(nodes, long).length, 100);
});

test("explicit missing-source removal reuses dependency cleanup and preserves restrictions",()=>{
  const draft={root:"gone",defaultRoot:"gone",nodes:[{id:"gone",table:"gone"},{id:"people",table:"people"}],edges:[{id:"fk",source:"gone",target:"people"}],fields:[{table:"gone",column:"id"}],exposedFields:[{table:"gone",column:"id"},{table:"people",column:"name"}],scopes:[{alternatives:[{conditions:[{table:"gone",column:"id",operator:"eq",parameterId:"required"}]}]}],reportFilters:[]};
  removeModelNode(draft,"gone");
  assert.equal(draft.root,"people");assert.equal(draft.defaultRoot,"people");assert.equal(draft.edges.length,0);
  assert.deepEqual(draft.exposedFields,[{table:"people",column:"name"}]);
  assert.deepEqual(draft.scopes[0].alternatives[0].conditions,[{table:"",column:"",operator:"eq",parameterId:"required"}]);
});

const catalog = { relationships: [
  { id: "people_org", sourceTable: "people", targetTable: "org" },
  { id: "org_parent", sourceTable: "org", targetTable: "org" },
  { id: "org_region", sourceTable: "org", targetTable: "region" },
] };
function draft() {
  return { root: "people", nodes: ["people", "org", "region"].map(table => ({ id: table, table })).concat({ id: "parent_org", table: "org" }),
    edges: catalog.relationships.map(r => ({ id: r.id, relationshipId: r.id, source: r.sourceTable, target: r.targetTable, enabled: true })),
    fields: [{ table: "people", column: "name" }], scopes: [], reportFilters: [], selections: {} };
}

test("aliases receive incoming, outgoing and self FK candidates without enabling cycles", () => {
  const model = draft();
  assert.equal(ensureAliasConnections(model, catalog), 3);
  assert.deepEqual(model.edges.slice(3).map(({ relationshipId, source, target, enabled }) => ({ relationshipId, source, target, enabled })), [
    { relationshipId: "people_org", source: "people", target: "parent_org", enabled: false },
    { relationshipId: "org_parent", source: "parent_org", target: "parent_org", enabled: false },
    { relationshipId: "org_region", source: "parent_org", target: "region", enabled: false },
  ]);
  assert.equal(ensureAliasConnections(model, catalog), 0);
  assert.ok(model.edges.slice(0, 3).every(edge => edge.enabled));
});

test("backfill preserves redirected example connections and independent toggle state", () => {
  const model = draft();
  model.edges[0].target = "parent_org";
  assert.equal(ensureAliasConnections(model, catalog), 2);
  assert.equal(model.edges.filter(edge => edge.relationshipId === "people_org").length, 1);
  assert.equal(model.edges[0].enabled, true);
  model.edges.find(edge => edge.source === "parent_org" && edge.target === "region").enabled = true;
  model.edges.find(edge => edge.id === "org_region").enabled = false;
  assert.equal(ensureAliasConnections(model, catalog), 0);
  assert.equal(model.edges.find(edge => edge.source === "parent_org" && edge.target === "region").enabled, true);
  assert.equal(model.edges.find(edge => edge.id === "org_region").enabled, false);
});

test("multiple aliases get independent stable IDs and missing canonical endpoints are not guessed", () => {
  const model = draft();
  model.nodes.push({ id: "second_org", table: "org" });
  ensureAliasConnections(model, catalog);
  const repeat = draft();
  repeat.nodes.push({ id: "second_org", table: "org" });
  ensureAliasConnections(repeat, catalog);
  assert.deepEqual(model.edges, repeat.edges);
  assert.equal(new Set(model.edges.map(edge => edge.id)).size, 9);
  const missing = draft();
  missing.nodes = missing.nodes.filter(node => node.id !== "region");
  assert.equal(ensureAliasConnections(missing, catalog), 2);
  assert.ok(model.edges.every(edge => edge.id.length < 200));
});

test("deletion clears every affected source binding but retains all restrictions and input values", () => {
  const model = draft();
  const binding = () => ({ table: "parent_org", column: "id", operator: "eq", parameterId: "parent", allowNull: false });
  model.root = "parent_org";
  model.fields.push({ table: "parent_org", column: "name" });
  model.scopes = [{ alternatives: [{ conditions: [binding()] }, { conditions: [binding()] }] }];
  model.reportFilters = [{ mode: "not_exists", conditions: [binding(), { table: "people", column: "name", value: "A" }] }];
  model.filters = [binding()];
  model.selections = { scope: { values: { parent: "chosen-id" } } };
  ensureAliasConnections(model, catalog);
  assert.deepEqual(aliasImpact(model, "parent_org"), { connections: 3, fields: 1, bindings: 4, isRoot: true });
  removeAlias(model, "parent_org");
  assert.equal(model.root, "org");
  assert.equal(model.nodes.length, 3);
  assert.equal(model.edges.length, 3);
  assert.deepEqual(model.fields, [{ table: "people", column: "name" }]);
  for (const condition of [...model.scopes[0].alternatives.flatMap(a => a.conditions), model.reportFilters[0].conditions[0], ...model.filters]) {
    assert.deepEqual(condition, { table: "", column: "", operator: "eq", parameterId: "parent", allowNull: false });
  }
  assert.equal(model.reportFilters[0].conditions[1].table, "people");
  assert.equal(model.selections.scope.values.parent, "chosen-id");
  assert.equal(ensureAliasConnections(model, catalog), 0);
});

test("original tables are protected and root falls back when canonical source is absent", () => {
  const model = draft();
  const before = structuredClone(model);
  assert.throws(() => removeAlias(model, "org"), /protected/);
  assert.throws(() => removeAlias(model, "missing"), /protected/);
  assert.deepEqual(model, before);
  assert.equal(isAlias(model.nodes[0]), false);
  assert.equal(isAlias(model.nodes[3]), true);
  model.nodes = model.nodes.filter(node => node.id !== "org");
  model.root = "parent_org";
  removeAlias(model, "parent_org");
  assert.equal(model.root, "people");
});
