import test from "node:test";
import assert from "node:assert/strict";
import { boundFilterSources, filtersAffectingNode, modelFilterIssue, columnFilterBindings, describeFilterCondition } from "../../src/schemii/schemoo/web/model-filter-links.js";

function rule(id, table, kind = "conditional") {
  return {id, label: id, kind, alternatives: [{id: "default", label: "Default", inputs: [], conditions: [{table, column: "id", operator: "not_null"}]}]};
}

test("inspector predicates omit only redundant source context", () => {
  const draft = {nodes:[{id:"certs",label:"Certifications"},{id:"alias",label:"History"}]};
  const alternative = {inputs:[{id:"date",label:"As of"}]};
  const condition = {table:"certs",column:"expires",operator:"gte",parameterId:"date",allowNull:true};
  const context = {table:"certs",column:"expires"};
  assert.equal(describeFilterCondition(condition,alternative,draft,context), "(≥ [As of] OR IS NULL)");
  assert.equal(describeFilterCondition({...condition,column:"starts",allowNull:false},alternative,draft,context), "starts ≥ [As of]");
  assert.equal(describeFilterCondition({...condition,table:"alias",operator:"not_null"},alternative,draft,context), "History.expires IS NOT NULL");
  assert.equal(describeFilterCondition({...condition,operator:"is_null"},alternative,draft,context), "IS NULL");
});
function fixture() {
  const draft = {
    nodes: [{id: "people", table: "people"}, {id: "certs", table: "certs"}, {id: "definitions", table: "definitions"}, {id: "other_cert_alias", table: "certs"},
      {id: "summary", table: "certs", derivation: {kind: "aggregate", source: "certs", outputs: [{nodeId: "definitions"}]}}],
    edges: [{source: "certs", target: "people", enabled: true}, {source: "certs", target: "definitions", enabled: true}],
    scopes: [rule("person rule", "people"), rule("cert rule", "certs"), rule("definition rule", "definitions"), rule("alias rule", "other_cert_alias"), rule("required alias rule", "other_cert_alias", "required")],
  };
  const catalog = {tables: ["people", "certs", "definitions"].map(name => ({name, columns: [{name: "id"}]}))};
  return {draft, catalog};
}

test("bound sources include each alternative once and ignore domain lookup sources", () => {
  const scope = rule("filter", "certs");
  scope.alternatives.push({conditions: [{table: "certs"}, {table: "people", domain: {nodeId: "definitions"}}, {table: ""}]});
  assert.deepEqual([...boundFilterSources(scope)], ["certs", "people"]);
});

test("table links distinguish source bindings from global required rules and aliases", () => {
  const {draft} = fixture();
  assert.deepEqual(filtersAffectingNode(draft, "certs").map(({scope, direct}) => [scope.id, direct]), [["cert rule", true], ["required alias rule", false]]);
  assert.deepEqual(filtersAffectingNode(draft, "missing"), []);
});

test("summary links include contributor paths but not unrelated conditional branches", () => {
  const {draft} = fixture();
  assert.deepEqual(filtersAffectingNode(draft, "summary").map(({scope, direct}) => [scope.id, direct]), [["cert rule", true], ["definition rule", true], ["required alias rule", false]]);
  draft.edges[1].enabled = false;
  assert.deepEqual(filtersAffectingNode(draft, "summary").map(({scope}) => scope.id), ["cert rule", "required alias rule"]);
});

test("row calculation links refer to source rather than other contributors", () => {
  const {draft} = fixture();
  draft.nodes[4].derivation.kind = "row";
  assert.deepEqual(filtersAffectingNode(draft, "summary").map(({scope}) => scope.id), ["cert rule", "required alias rule"]);
});

test("structural warning uses model bindings not missing preview values", () => {
  const {draft, catalog} = fixture();
  const scope = draft.scopes[1];
  scope.alternatives[0].inputs.push({id: "value", label: "Report value", type: "text", defaultValue: ""});
  scope.alternatives[0].conditions[0] = {table: "certs", column: "id", operator: "eq", parameterId: "value"};
  assert.equal(modelFilterIssue(scope, draft, catalog), "");
  scope.alternatives[0].conditions[0].table = "deleted alias";
  assert.match(modelFilterIssue(scope, draft, catalog), /valid source column/);
});

test("invalid inputs, list choices and domain references surface useful authoring warnings", () => {
  const {draft, catalog} = fixture();
  const scope = draft.scopes[0], option = scope.alternatives[0];
  option.inputs.push({id: "value", label: "As of date"});
  assert.match(modelFilterIssue(scope, draft, catalog), /Bind “As of date”/);
  option.inputs = [];
  option.conditions[0] = {table: "people", column: "id", operator: "in", value: []};
  assert.match(modelFilterIssue(scope, draft, catalog), /at least one value/);
  option.conditions[0].value = ["id"];
  option.conditions[0].domain = {nodeId: "deleted", column: "id"};
  assert.match(modelFilterIssue(scope, draft, catalog), /valid fixed-value domain/);
});

test("column bindings preserve scopes, alternatives and AND positions without including aliases", () => {
  const {draft} = fixture(), scope = draft.scopes[1], alternative = scope.alternatives[0];
  alternative.conditions.push({table: "certs", column: "other", operator: "not_null"}, {table: "certs", column: "id", operator: "eq", value: "a"});
  scope.alternatives.push({id: "unrestricted", conditions: []}, {id: "second", conditions: [{table: "certs", column: "id", operator: "eq", value: "b"}]});
  const bindings = columnFilterBindings(draft, "certs", "id");
  assert.equal(bindings.length, 2);
  assert.equal(bindings[0].scope, scope);
  assert.equal(bindings[0].alternative, alternative);
  assert.deepEqual(bindings[0].conditionIndexes, [0, 2]);
  assert.deepEqual(bindings[0].conditions, [alternative.conditions[0], alternative.conditions[2]]);
  assert.equal(bindings[1].alternative.id, "second");
  assert.equal(columnFilterBindings(draft, "other_cert_alias", "id").length, 2);
  assert.deepEqual(columnFilterBindings(draft, "certs", "missing"), []);
});

test("column bindings never manufacture references for derived or missing nodes", () => {
  const {draft} = fixture();
  draft.scopes.push(rule("invalid calculated binding", "summary"));
  assert.deepEqual(columnFilterBindings(draft, "summary", "id"), []);
  assert.deepEqual(columnFilterBindings(draft, "missing", "id"), []);
});

test("condition text names the actual alias and report input, not its default value", () => {
  const {draft} = fixture();
  draft.nodes[3].label = "Historical certifications";
  const option = {inputs: [{id: "as_of", label: "As-of date", defaultValue: "2025-01-01"}]};
  assert.equal(describeFilterCondition({table: "other_cert_alias", column: "expiration_date", operator: "gte", parameterId: "as_of", allowNull: true}, option, draft), "(Historical certifications.expiration_date ≥ [As-of date] OR Historical certifications.expiration_date IS NULL)");
  assert.equal(describeFilterCondition({table: "certs", column: "id", operator: "eq", parameterId: "gone", value: "stale"}, option, draft), "certs.id equals [Missing report input]");
});

test("condition text clearly distinguishes list membership, null checks, literals and today's date", () => {
  const {draft} = fixture(), option = {inputs: []};
  const describe = condition => describeFilterCondition({table: "certs", column: "id", ...condition}, option, draft);
  assert.equal(describe({operator: "in", value: ["Active", "Pending"]}), 'certs.id IN ("Active", "Pending")');
  assert.equal(describe({operator: "not_in", value: [1, 2]}), "certs.id NOT IN (1, 2)");
  assert.equal(describe({operator: "not_null", value: "ignored", allowNull: true}), "certs.id IS NOT NULL");
  assert.equal(describe({operator: "is_null"}), "certs.id IS NULL");
  assert.equal(describe({operator: "eq", value: false}), "certs.id equals false");
  assert.equal(describe({operator: "gt", valueSource: "today"}), "certs.id > Today (UTC)");
});
