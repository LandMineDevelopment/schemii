import test from "node:test";
import assert from "node:assert/strict";
import { comparisonColumns, columnComparisonIssue } from "../../src/schemii/schemoo/web/column-comparisons.js";
import { describeFilterCondition, modelFilterIssue, columnFilterBindings } from "../../src/schemii/schemoo/web/model-filter-links.js";
import { conditionSummary } from "../../src/schemii/schemoo/web/derived-conditions.js";
import { splitDraft, joinModel } from "../../src/schemii/schemoo/web/model-state.js";

function fixture() {
  const condition = { table: "orders", column: "delivered", operator: "gt", compareColumn: "promised" };
  const option = { id: "late", label: "Late", inputs: [], conditions: [condition] };
  const scope = { id: "delivery", label: "Delivery", kind: "required", requirement: "optional", alternatives: [option] };
  const draft = { root: "orders", nodes: [{ id: "orders", table: "orders_table", label: "Orders" }, { id: "history", table: "orders_table", label: "History" }],
    edges: [], fields: [], scopes: [scope], selections: {}, reportFilters: [], limit: 100 };
  const catalog = { tables: [{ name: "orders_table", columns: [
    { name: "delivered", dataType: "date" }, { name: "promised", dataType: "date" },
    { name: "recorded", dataType: "timestamp" }, { name: "status", dataType: "text" },
    { name: "actual", dataType: "numeric(12, 2)" }, { name: "target", dataType: "int4" },
  ] }] };
  return { condition, option, scope, draft, catalog };
}

test("column choices reuse conservative type compatibility on the same source", () => {
  const { condition, draft, catalog } = fixture();
  assert.deepEqual(comparisonColumns(condition, draft, catalog).map(c => c.name), ["delivered", "promised"]);
  assert.deepEqual(comparisonColumns({ ...condition, column: "actual" }, draft, catalog).map(c => c.name), ["actual", "target"]);
  assert.deepEqual(comparisonColumns({ ...condition, table: "missing" }, draft, catalog), []);
  draft.nodes[0].derivation = {};
  assert.deepEqual(comparisonColumns(condition, draft, catalog), []);
});

test("invalid and incompatible RHS references are repairable authoring errors", () => {
  const { condition, draft, catalog, scope } = fixture();
  assert.equal(modelFilterIssue(scope, draft, catalog), "");
  for (const compareColumn of ["", "gone", "history.promised", "status", "recorded"]) {
    assert.match(columnComparisonIssue({ ...condition, compareColumn }, draft, catalog), /compatible comparison column/);
  }
  for (const operator of ["in", "contains", "is_null"]) {
    assert.match(columnComparisonIssue({ ...condition, operator }, draft, catalog), /Column comparisons require/);
  }
  for (const extra of [{ value: "" }, { parameterId: "p" }, { domain: {} }, { valueSource: "today" }]) {
    assert.match(columnComparisonIssue({ ...condition, ...extra }, draft, catalog), /not more than one/);
  }
  catalog.tables[0].columns = catalog.tables[0].columns.filter(c => c.name !== "promised");
  assert.match(modelFilterIssue(scope, draft, catalog), /compatible comparison column/);
  assert.equal(condition.compareColumn, "promised", "source drift must not drop the restriction");
});

test("summaries distinguish a column reference from the same text as a literal", () => {
  const { condition, option, draft } = fixture();
  assert.equal(describeFilterCondition(condition, option, draft), "Orders.delivered > Orders.promised");
  assert.equal(describeFilterCondition({ ...condition, allowNull: true }, option, draft), "(Orders.delivered > Orders.promised OR Orders.delivered IS NULL)");
  assert.equal(describeFilterCondition({ ...condition, table: "history" }, option, draft), "History.delivered > History.promised");
  assert.equal(describeFilterCondition({ ...condition, compareColumn: null, value: "Orders.promised" }, option, draft), 'Orders.delivered > "Orders.promised"');
  assert.equal(conditionSummary(condition, draft), "Orders.delivered > Orders.promised");
});

test("save/reopen retains the RHS while inline editing stays bound to the LHS alias", () => {
  const { condition, draft } = fixture();
  const reopened = joinModel(splitDraft(draft));
  assert.deepEqual(reopened.scopes[0].alternatives[0].conditions[0], condition);
  assert.equal(columnFilterBindings(reopened, "orders", "delivered").length, 1);
  assert.equal(columnFilterBindings(reopened, "orders", "promised").length, 0);
  assert.equal(columnFilterBindings(reopened, "history", "delivered").length, 0);
});
