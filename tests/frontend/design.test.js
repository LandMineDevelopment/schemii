import assert from "node:assert/strict";
import test from "node:test";

import {
  createDesignRelationship,
  createDesignTable,
  designLayoutContent,
  designPositions,
  designToCatalog,
  updateDesignTable,
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

test("table editing preserves stable object identities and column-owned details", () => {
  let index = 0;
  const original = createDesignTable("customers", [
    { name: "id", dataType: "bigint", nullable: false, primary: true },
    { name: "email", dataType: "text", nullable: false, primary: false },
  ], () => uuids[index++]);
  original.columns[1].defaultExpression = "'unknown'::text";
  const content = { tables: [original], relationships: [], functions: [], views: [] };

  const updated = updateDesignTable(content, original.id, "accounts", [
    { id: original.columns[0].id, name: "account_id", dataType: "bigint", nullable: true, primary: true },
    { id: original.columns[1].id, name: "email", dataType: "citext", nullable: false, primary: false },
    { name: "active", dataType: "boolean", nullable: false, primary: false },
  ], () => uuids[index++]);

  assert.equal(updated.id, original.id);
  assert.equal(updated.keys[0].id, original.keys[0].id);
  assert.deepEqual(updated.columns.slice(0, 2).map(column => column.id), original.columns.map(column => column.id));
  assert.equal(updated.columns[1].defaultExpression, "'unknown'::text");
  assert.equal(updated.columns[0].nullable, false);
  assert.equal(updated.columns[2].id, `column_${"5".repeat(32)}`);
});

test("table editing blocks removal of columns used by relationships", () => {
  let value = 1;
  const nextUuid = () => `${String(value++).padStart(8, "0")}-0000-0000-0000-000000000000`;
  const parent = createDesignTable("parents", [
    { name: "id", dataType: "uuid", nullable: false, primary: true },
  ], nextUuid);
  const child = createDesignTable("children", [
    { name: "id", dataType: "uuid", nullable: false, primary: true },
    { name: "parent_id", dataType: "uuid", nullable: false, primary: false },
  ], nextUuid);
  const content = {
    tables: [parent, child],
    relationships: [{
      id: `relationship_${"f".repeat(32)}`,
      name: "children_parents_fkey",
      sourceTableId: child.id,
      sourceColumnIds: [child.columns[1].id],
      targetTableId: parent.id,
      targetColumnIds: [parent.columns[0].id],
      onUpdate: "NO ACTION",
      onDelete: "NO ACTION",
      deferrable: false,
      initiallyDeferred: false,
    }],
    functions: [],
    views: [],
  };

  assert.throws(() => updateDesignTable(content, child.id, child.name, [
    { id: child.columns[0].id, name: "id", dataType: "uuid", nullable: false, primary: true },
  ]), /relationship “children_parents_fkey”/);
  assert.throws(() => updateDesignTable(content, parent.id, parent.name, [
    { id: parent.columns[0].id, name: "id", dataType: "uuid", nullable: false, primary: false },
  ]), /targets this key/);
});

test("relationship authoring maps source columns to a composite target key", () => {
  let value = 1;
  const nextUuid = () => `${String(value++).padStart(8, "0")}-0000-0000-0000-000000000000`;
  const parent = createDesignTable("parents", [
    { name: "tenant_id", dataType: "uuid", nullable: false, primary: true },
    { name: "id", dataType: "bigint", nullable: false, primary: true },
  ], nextUuid);
  const child = createDesignTable("children", [
    { name: "id", dataType: "bigint", nullable: false, primary: true },
    { name: "tenant_id", dataType: "uuid", nullable: false, primary: false },
    { name: "parent_id", dataType: "bigint", nullable: false, primary: false },
  ], nextUuid);
  const content = { tables: [parent, child], relationships: [], functions: [], views: [] };

  const relationship = createDesignRelationship(content, {
    name: "children_parents_fkey",
    sourceTableId: child.id,
    sourceColumnIds: [child.columns[1].id, child.columns[2].id],
    targetTableId: parent.id,
    targetKeyId: parent.keys[0].id,
    onUpdate: "CASCADE",
    onDelete: "RESTRICT",
    deferrable: true,
    initiallyDeferred: true,
  }, nextUuid);

  assert.deepEqual(relationship.targetColumnIds, parent.keys[0].columnIds);
  assert.deepEqual(relationship.sourceColumnIds, [child.columns[1].id, child.columns[2].id]);
  assert.equal(relationship.onUpdate, "CASCADE");
  assert.equal(relationship.initiallyDeferred, true);
});
