const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
const mutationStart = source.indexOf("function isColumnReferencedInText");
const mutationEnd = source.indexOf("function relationshipColumnPairs", mutationStart);
const deleteStart = source.indexOf("function deleteTable(tableId)");
const deleteEnd = source.indexOf("function updateColumn(", deleteStart);
for (const [name, marker] of Object.entries({ mutationStart, mutationEnd, deleteStart, deleteEnd })) {
  assert.notEqual(marker, -1, `${name} marker is missing`);
}

const table = {
  id: "table_orders",
  name: "orders",
  columns: [{ id: "column_amount", name: "amount" }],
  uniqueConstraints: [],
  checks: [
    { id: "check_amount", name: "orders_amount_check", definition: "CHECK (orders.amount >= 0)" },
    { id: "check_active", name: "orders_active_check", definition: "CHECK (is_active)" }
  ],
  indexes: [{ id: "index_amount", name: "orders_amount_idx", definition: "CREATE INDEX orders_amount_idx ON public.orders (amount)" }],
  triggers: [{ id: "trigger_updated", name: "orders_set_updated_at", definition: "CREATE TRIGGER orders_set_updated_at BEFORE UPDATE ON public.orders EXECUTE FUNCTION set_updated_at()" }]
};
const mutationContext = vm.createContext({ table });
vm.runInContext(`
  const schema = { tables: [table], relationships: [] };
  function uid(prefix) { return prefix + "_1"; }
  function availablePrimaryKeyName(name) { return name + "_pkey"; }
  function availableUniqueConstraintName() { return "unused_key"; }
  ${source.slice(mutationStart, mutationEnd)}
  globalThis.columnCheckConstraints = columnCheckConstraints;
  globalThis.findColumnDependentObjects = findColumnDependentObjects;
  globalThis.updateTableNameInObjects = updateTableNameInObjects;
  globalThis.updateColumnNameInObjects = updateColumnNameInObjects;
`, mutationContext);

const dependencies = mutationContext.findColumnDependentObjects(table, "column_amount");
assert.deepEqual(Array.from(dependencies, dependency => dependency.kind), ["check", "index"]);
assert.equal(dependencies[0].item.id, "check_amount");
assert.equal(dependencies[1].item.id, "index_amount");
assert.deepEqual(Array.from(mutationContext.columnCheckConstraints(table, table.columns[0]), check => check.id), ["check_amount"]);

table.name = "purchases";
mutationContext.updateTableNameInObjects(table, "orders", "purchases");
assert.equal(table.checks[0].name, "purchases_amount_check");
assert.equal(table.checks[0].definition, "CHECK (purchases.amount >= 0)");
assert.equal(table.checks[1].name, "purchases_active_check");
assert.equal(table.indexes[0].name, "purchases_amount_idx");
assert.equal(table.indexes[0].definition, "CREATE INDEX purchases_amount_idx ON public.purchases (amount)");
assert.equal(table.triggers[0].name, "purchases_set_updated_at");
assert.equal(table.triggers[0].definition, "CREATE TRIGGER purchases_set_updated_at BEFORE UPDATE ON public.purchases EXECUTE FUNCTION set_updated_at()");

table.columns[0].name = "total";
mutationContext.updateColumnNameInObjects(table, "column_amount", "amount", "total");
assert.equal(table.checks[0].name, "purchases_total_check");
assert.equal(table.checks[0].definition, "CHECK (purchases.total >= 0)");
assert.equal(table.checks[1].definition, "CHECK (is_active)");
assert.equal(table.indexes[0].name, "purchases_total_idx");
assert.equal(table.indexes[0].definition, "CREATE INDEX purchases_total_idx ON public.purchases (total)");

const deleteContext = vm.createContext({
  schema: { tables: [table, { id: "table_other", checks: [] }], relationships: [{ fromTableId: table.id, toTableId: "table_other" }] },
  selectedTableIds: new Set([table.id]),
  selectedTableId: table.id,
  confirm: () => true,
  checkpointHistory: () => {},
  saveSchema: () => {},
  render: () => {}
});
vm.runInContext(`
  function getTable(tableId) { return schema.tables.find(item => item.id === tableId); }
  ${source.slice(deleteStart, deleteEnd)}
  globalThis.deleteTable = deleteTable;
`, deleteContext);
deleteContext.deleteTable(table.id);
assert.equal(deleteContext.schema.tables.length, 1);
assert.equal(deleteContext.schema.tables[0].id, "table_other");
assert.equal(deleteContext.schema.relationships.length, 0);

console.log("Check constraint mutation tests passed");
