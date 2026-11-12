import test from "node:test";
import assert from "node:assert/strict";
import { importedDraft, defaultSourceSelection, MODEL_LIMITS } from "../../src/schemii/schemoo/web/model-draft.js";
import { splitDraft, joinModel } from "../../src/schemii/schemoo/web/model-state.js";
import { sourceIdentity, sourceNodeIds, sourceLabel } from "../../src/schemii/schemoo/web/logical-relationships.js";
import { reconcileSourceCatalog, addSourceSelection } from "../../src/schemii/schemoo/web/source-reconciliation.js";

const column = name => ({ name, dataType: "integer", nullable: false });
const largeCatalog = () => ({ tables: Array.from({ length: 101 }, (_, i) => ({ name: `table_${i}`, primaryKey: ["id"], columns: [column("id"), column("name")] })), relationships: [], positions: [] });

test("a 101-source catalog requires a choice and creates a bounded subset without raising limits", () => {
  const catalog = largeCatalog();
  assert.deepEqual(defaultSourceSelection(catalog), []);
  assert.throws(() => importedDraft(catalog), /at most 100 sources/);
  const draft = importedDraft(catalog, [{ table: "table_100", columns: ["name"] }]);
  assert.equal(draft.nodes.length, 1); assert.equal(draft.exposedFields.length, 1);
  assert.equal(draft.sourceContract.scope, "model");
  assert.deepEqual(draft.sourceContract.tables[0].columns.map(c => c.name), ["id", "name"]);
  const reopened = joinModel(splitDraft(draft)), before = structuredClone(reopened);
  assert.equal(reconcileSourceCatalog(reopened, catalog).tables.length, 100);
  assert.deepEqual(reopened, before);
  assert.deepEqual(MODEL_LIMITS, { sources: 100, relationships: 200, fields: 1000 });
});

test("a 1,001-field source allows selected outputs plus hidden FK and key dependencies", () => {
  const catalog = { tables: [{ name: "wide", primaryKey: ["f0"], columns: Array.from({ length: 1001 }, (_, i) => column(`f${i}`)) }, { name: "parent", columns: [column("id")] }],
    relationships: [{ id: "fk", name: "fk", sourceTable: "wide", sourceColumn: "f1", targetTable: "parent", targetColumn: "id" }], positions: [{ name: "wide", x: 50, y: 60 }] };
  assert.deepEqual(defaultSourceSelection(catalog), []);
  assert.throws(() => importedDraft(catalog), /1,000 exposed fields/);
  const draft = importedDraft(catalog, [{ table: "wide", columns: ["f1000"] }, { table: "parent", columns: [] }]);
  assert.deepEqual(draft.exposedFields, [{ table: "wide", column: "f1000", aggregate: "none" }]);
  assert.deepEqual(draft.sourceContract.tables[0].columns.map(c => c.name), ["f0", "f1", "f1000"]);
  assert.equal(draft.nodes[0].x, 50); assert.equal(draft.edges.length, 1);
});

test("small schemas retain a pleasant all-source default and selected preview output", () => {
  const catalog = { tables: [{ name: "people", columns: [column("id"), column("name")] }], relationships: [] };
  assert.deepEqual(defaultSourceSelection(catalog), [{ table: "people", columns: ["id", "name"] }]);
  assert.equal(importedDraft(catalog).fields[0].column, "name");
  const choice = importedDraft(catalog, [{ table: "people", columns: ["id"] }]);
  assert.equal(choice.fields[0].column, "id");
  assert.deepEqual(choice.sourceContract.tables[0].columns.map(c => c.name), ["id"]);
});

test("empty, inaccessible, stale, and over-connected selections fail clearly", () => {
  const catalog = largeCatalog();
  assert.throws(() => importedDraft(catalog, []), /Select at least one source/);
  assert.throws(() => importedDraft(catalog, [{ table: "table_0", columns: [] }]), /Select at least one field/);
  assert.throws(() => importedDraft(catalog, [{ table: "missing", columns: ["id"] }]), /unavailable/);
  assert.throws(() => importedDraft(catalog, [{ table: "table_0", columns: ["missing"] }]), /unavailable/);
  catalog.relationships = Array.from({ length: 201 }, (_, i) => ({ id: `fk${i}`, sourceTable: "table_0", sourceColumn: "id", targetTable: "table_1", targetColumn: "id" }));
  assert.throws(() => importedDraft(catalog, [{ table: "table_0", columns: ["name"] }, { table: "table_1", columns: ["name"] }]), /more than 200 connections/);
});

test("explicit addition is bounded, disabled and atomic when capacity is exceeded", () => {
  const catalog = largeCatalog(), draft = importedDraft(catalog, [{ table: "table_0", columns: ["name"] }]);
  const contract = structuredClone(draft.sourceContract);
  catalog.relationships = [{ id: "fk", sourceTable: "table_1", sourceColumn: "id", targetTable: "table_0", targetColumn: "id" }];
  addSourceSelection(draft, catalog, [{ table: "table_1", columns: ["name"] }]);
  assert.equal(draft.nodes.length, 2); assert.equal(draft.edges[0].enabled, false);
  assert.deepEqual(draft.sourceContract, contract); // server owns new dependency baselines
  const before = structuredClone(draft);
  draft.nodes.push(...Array.from({ length: 98 }, (_, i) => ({ id: `alias_${i}`, table: "table_0" })));
  const full = structuredClone(draft);
  assert.throws(() => addSourceSelection(draft, catalog, [{ table: "table_2", columns: ["id"] }]), /exceed 100 model objects/);
  assert.deepEqual(draft, full); assert.equal(before.nodes.length, 2);
});

test("adding fields preserves alias exposure and prefers the existing physical occurrence", () => {
  const catalog = largeCatalog(), draft = importedDraft(catalog, [{ table: "table_0", columns: ["name"] }]);
  draft.nodes.push({ id: "role", table: "table_0", label: "Role" });
  addSourceSelection(draft, catalog, [{ table: "table_0", columns: ["id"] }]);
  assert.deepEqual(draft.exposedFields.map(field => [field.table, field.column]), [["table_0", "name"], ["table_0", "id"]]);
});

test("adding selected fields does not import relationships between unrelated modeled sources", () => {
  const catalog = largeCatalog();
  const draft = importedDraft(catalog, [0, 1, 2].map(index => ({ table: `table_${index}`, columns: ["name"] })));
  catalog.relationships = [
    { id: "selected_fk", sourceTable: "table_0", sourceColumn: "id", targetTable: "table_1", targetColumn: "id" },
    { id: "unrelated_fk", sourceTable: "table_1", sourceColumn: "id", targetTable: "table_2", targetColumn: "id" },
  ];
  const contract = structuredClone(draft.sourceContract);
  addSourceSelection(draft, catalog, [{ table: "table_0", columns: ["id"] }]);
  assert.deepEqual(draft.edges.map(edge => [edge.relationshipId, edge.enabled]), [["selected_fk", false]]);
  assert.deepEqual(draft.sourceContract, contract);
  assert.equal(draft.exposedFields.length, 4);
});

test("qualified source selection preserves duplicate names and every composite dependency",()=>{
  const catalog={namespace:"one",tables:["one","two"].map(namespace=>({namespace,name:"same.name",primaryKey:["tenant","id"],columns:[column("tenant"),column("id"),column("value")]})),relationships:[{id:"fk",sourceNamespace:"one",sourceTable:"same.name",targetNamespace:"two",targetTable:"same.name",columns:[{source:"tenant",target:"tenant"},{source:"id",target:"id"}]}]};
  const draft=importedDraft(catalog,[{namespace:"one",table:"same.name",columns:["value"]},{namespace:"two",table:"same.name",columns:[]}]);
  assert.deepEqual(draft.nodes.map(node=>[node.id,node.namespace,node.table]),[["same.name","one","same.name"],['["two","same.name"]',"two","same.name"]]);
  assert.equal(draft.edges[0].target,'["two","same.name"]');
  assert.deepEqual(draft.sourceContract.tables.map(table=>table.columns.map(column=>column.name)),[["tenant","id","value"],["tenant","id"]]);
  const before=structuredClone(draft);reconcileSourceCatalog(draft,catalog);assert.deepEqual(draft,before);
  addSourceSelection(draft,catalog,[{namespace:"two",table:"same.name",columns:["value"]}]);
  assert.equal(draft.nodes.length,2);assert.deepEqual(draft.exposedFields.map(field=>field.table),["same.name",'["two","same.name"]']);
});

test("quoted legacy names cannot collide with qualified generated IDs or their references",()=>{
  const legacy='["two","same"]';
  const catalog={namespace:"one",tables:[{namespace:"one",name:legacy,columns:[column("id")]},{namespace:"two",name:"same",columns:[column("id")]}],
    relationships:[{id:"fk",sourceNamespace:"one",sourceTable:legacy,targetNamespace:"two",targetTable:"same",columns:[{source:"id",target:"id"}]}]};
  const draft=importedDraft(catalog), reversed=importedDraft({...catalog,tables:[...catalog.tables].reverse()});
  const ids=new Map(draft.nodes.map(node=>[sourceIdentity(node,catalog),node.id]));
  assert.equal(new Set(draft.nodes.map(node=>node.id)).size,2);
  assert.equal(ids.get(sourceIdentity({namespace:"one",table:legacy},catalog)),legacy);
  assert.equal(ids.get(sourceIdentity({namespace:"two",table:"same"},catalog)),`${legacy}_2`);
  assert.deepEqual(new Map(reversed.nodes.map(node=>[sourceIdentity(node,catalog),node.id])),ids);
  assert.deepEqual(draft.exposedFields.map(field=>field.table),draft.nodes.map(node=>node.id));
  assert.equal(draft.edges[0].source,draft.nodes[0].id);assert.equal(draft.edges[0].target,draft.nodes[1].id);
  const existing=importedDraft(catalog,[{namespace:"two",table:"same",columns:["id"]}]);
  const old=existing.nodes[0].id;
  addSourceSelection(existing,catalog,[{namespace:"one",table:legacy,columns:["id"]}]);
  assert.equal(existing.nodes[0].id,old);assert.equal(new Set(existing.nodes.map(node=>node.id)).size,2);
  assert.ok(existing.exposedFields.every(field=>existing.nodes.some(node=>node.id===field.table)));
});

test("generated IDs remain bounded and deterministic for long escaped identifiers",()=>{
  const sources=["x","y"].map(end=>({namespace:'"'.repeat(63),table:'"'.repeat(62)+end}));
  const catalog={namespace:"public"}, ids=sourceNodeIds(sources,catalog);
  assert.equal(new Set(ids.values()).size,2);
  assert.ok([...ids.values()].every(id=>Array.from(id).length<=200));
  assert.deepEqual(sourceNodeIds([...sources].reverse(),catalog),ids);
});

test("source display quotes components so dotted names remain unambiguous",()=>{
  const catalog={namespace:"public"};
  assert.equal(sourceLabel({namespace:"a.b",table:"c"},catalog),'"a.b".c');
  assert.equal(sourceLabel({namespace:"a",table:"b.c"},catalog),'a."b.c"');
  assert.equal(sourceLabel({namespace:"public",table:'a."b'},catalog),'"a.""b"');
});
