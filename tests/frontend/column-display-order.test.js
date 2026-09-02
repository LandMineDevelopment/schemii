import assert from "node:assert/strict";
import test from "node:test";

import {
  applyColumnDisplayOrders,
  reconcileColumnDisplayOrder,
  removeColumnDisplayOrder,
  replaceColumnDisplayOrder,
} from "../../src/schemii/common/web/assets/column-display-order.js";

const table = {
  name: "orders",
  columns: [
    { name: "created_at", ordinal: 3 },
    { name: "id", ordinal: 1 },
    { name: "customer_id", ordinal: 2 },
  ],
};

test("column display order retains known preferences and appends new physical columns", () => {
  assert.deepEqual(
    reconcileColumnDisplayOrder(table, ["customer_id", "removed", "id"])
      .map(column => column.name),
    ["customer_id", "id", "created_at"],
  );
});

test("catalog display order never changes PostgreSQL ordinals", () => {
  const catalog = { tables: [table] };
  const custom = applyColumnDisplayOrders(
    catalog,
    [{ name: "orders", columns: ["created_at", "id", "customer_id"] }],
    new Map([["orders", "custom"]]),
  );
  const physical = applyColumnDisplayOrders(
    catalog,
    [{ name: "orders", columns: ["created_at", "id", "customer_id"] }],
    new Map([["orders", "database"]]),
  );

  assert.deepEqual(custom.tables[0].columns.map(column => column.name), ["created_at", "id", "customer_id"]);
  assert.deepEqual(custom.tables[0].columns.map(column => column.ordinal), [3, 1, 2]);
  assert.deepEqual(physical.tables[0].columns.map(column => column.name), ["id", "customer_id", "created_at"]);
  assert.notEqual(custom.tables[0], table);
  assert.deepEqual(table.columns.map(column => column.name), ["created_at", "id", "customer_id"]);
});

test("workspace preferences replace or remove one table without mutating siblings", () => {
  const original = [{ name: "customers", columns: ["id"] }];
  const replaced = replaceColumnDisplayOrder(original, "orders", ["created_at", "id"]);

  assert.deepEqual(replaced, [
    { name: "customers", columns: ["id"] },
    { name: "orders", columns: ["created_at", "id"] },
  ]);
  assert.deepEqual(removeColumnDisplayOrder(replaced, "orders"), original);
  assert.deepEqual(original, [{ name: "customers", columns: ["id"] }]);
});
