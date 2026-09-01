import assert from "node:assert/strict";
import test from "node:test";

import {
  createDesignTable,
  designLayoutContent,
  designPositions,
  designToCatalog,
} from "../../src/schemii/schemii/web/assets/design.js";

const uuids = [
  "11111111-1111-1111-1111-111111111111",
  "22222222-2222-2222-2222-222222222222",
  "33333333-3333-3333-3333-333333333333",
  "44444444-4444-4444-4444-444444444444",
  "55555555-5555-5555-5555-555555555555",
];

test("table authoring creates stable IDs and one composite primary key", () => {
  let index = 0;
  const table = createDesignTable("customers", [
    { name: "tenant_id", dataType: "uuid", nullable: true, primary: true },
    { name: "customer_id", dataType: "bigint", nullable: true, primary: true },
    { name: "email", dataType: "text", nullable: false, primary: false },
  ], () => uuids[index++]);

  assert.equal(table.id, `table_${"5".repeat(32)}`);
  assert.equal(table.columns[0].id, `column_${"1".repeat(32)}`);
  assert.equal(table.columns[0].nullable, false);
  assert.deepEqual(table.keys[0].columnIds, [
    `column_${"1".repeat(32)}`,
    `column_${"2".repeat(32)}`,
  ]);
});

test("desired designs use the existing catalog canvas without losing stable layout IDs", () => {
  let index = 0;
  const table = createDesignTable("customers", [
    { name: "id", dataType: "bigint", nullable: false, primary: true },
  ], () => uuids[index++]);
  const design = {
    revision: 1,
    fingerprint: "a".repeat(64),
    content: { tables: [table], relationships: [], functions: [], views: [] },
  };
  const layout = {
    content: { objects: [{ objectId: table.id, layer: "tables", x: 90, y: 120 }] },
  };

  const catalog = designToCatalog({ name: "Customer model" }, design);
  assert.equal(catalog.source, "design");
  assert.equal(catalog.tables[0].primaryKey.columns[0], "id");
  assert.deepEqual(designPositions(design, layout), [{ name: "customers", x: 90, y: 120 }]);
  assert.deepEqual(
    designLayoutContent(design, [{ name: "customers", x: 140, y: 160 }]),
    { objects: [{ objectId: table.id, layer: "tables", x: 140, y: 160 }] },
  );
});

test("table authoring rejects duplicate columns before making a request", () => {
  assert.throws(() => createDesignTable("customers", [
    { name: "id", dataType: "bigint", nullable: false },
    { name: "id", dataType: "text", nullable: true },
  ], () => uuids[0]), /unique/);
});
