import assert from "node:assert/strict";
import test from "node:test";

import {
  alignRelationshipColumnTypes,
  createDesignRelationship,
  createDesignTable,
  deleteDesignKey,
  designLayoutContent,
  designPositions,
  designToCatalog,
  relationshipDraftFromColumns,
  relationshipDraftFromExisting,
  saveDesignKey,
  suggestDesignKeyName,
  updateDesignRelationship,
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
  assert.equal(catalog.tables[0].primaryKey.designId, table.keys[0].id);
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

test("graphical column selection seeds the exact composite target key", () => {
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

  const draft = relationshipDraftFromColumns(content, {
    sourceTableId: child.id,
    sourceColumnId: child.columns[2].id,
    targetTableId: parent.id,
    targetColumnId: parent.columns[1].id,
  });

  assert.equal(draft.targetKeyId, parent.keys[0].id);
  assert.deepEqual(draft.sourceColumnIds, [child.columns[1].id, child.columns[2].id]);
  assert.deepEqual(draft.targetColumnIds, parent.keys[0].columnIds);
  assert.deepEqual(draft.eligibleTargetKeyIds, [parent.keys[0].id]);
});

test("relationship type alignment makes foreign-key columns match the referenced key atomically", () => {
  let value = 1;
  const nextUuid = () => `${String(value++).padStart(8, "0")}-0000-0000-0000-000000000000`;
  const parent = createDesignTable("parents", [
    { name: "tenant_id", dataType: "uuid", nullable: false, primary: true },
    { name: "id", dataType: "bigint", nullable: false, primary: true },
  ], nextUuid);
  const child = createDesignTable("children", [
    { name: "tenant_id", dataType: "text", nullable: false, primary: false },
    { name: "parent_id", dataType: "integer", nullable: false, primary: false },
  ], nextUuid);
  const content = { tables: [parent, child], relationships: [], functions: [], views: [] };
  const relationship = createDesignRelationship(content, {
    name: "children_parents_fkey",
    sourceTableId: child.id,
    sourceColumnIds: child.columns.map(column => column.id),
    targetTableId: parent.id,
    targetKeyId: parent.keys[0].id,
    onUpdate: "NO ACTION",
    onDelete: "NO ACTION",
    deferrable: false,
    initiallyDeferred: false,
  }, nextUuid);

  const aligned = alignRelationshipColumnTypes(content, relationship);

  assert.deepEqual(aligned.changes.map(change => [change.from, change.to]), [["text", "uuid"], ["integer", "bigint"]]);
  assert.deepEqual(aligned.content.tables[1].columns.map(column => column.dataType), ["uuid", "bigint"]);
  assert.deepEqual(content.tables[1].columns.map(column => column.dataType), ["text", "integer"]);
});

test("relationship editing preserves identity and revalidates mappings against target keys", () => {
  let value = 1;
  const nextUuid = () => `${String(value++).padStart(8, "0")}-0000-0000-0000-000000000000`;
  const parent = createDesignTable("parents", [
    { name: "id", dataType: "bigint", nullable: false, primary: true },
    { name: "code", dataType: "text", nullable: false, primary: false },
  ], nextUuid);
  const withUnique = saveDesignKey({ tables: [parent], relationships: [], functions: [], views: [] }, {
    tableId: parent.id,
    name: "parents_code_key",
    kind: "unique",
    columnIds: [parent.columns[1].id],
  }, nextUuid);
  const child = createDesignTable("children", [
    { name: "parent_id", dataType: "bigint", nullable: false, primary: false },
    { name: "parent_code", dataType: "text", nullable: false, primary: false },
  ], nextUuid);
  const base = { ...withUnique.content, tables: [withUnique.content.tables[0], child] };
  const created = createDesignRelationship(base, {
    name: "children_parent_id_fkey",
    sourceTableId: child.id,
    sourceColumnIds: [child.columns[0].id],
    targetTableId: parent.id,
    targetKeyId: parent.keys[0].id,
    onUpdate: "NO ACTION",
    onDelete: "NO ACTION",
    deferrable: false,
    initiallyDeferred: false,
  }, nextUuid);
  const content = { ...base, relationships: [created] };

  const sameName = updateDesignRelationship(content, created.id, {
    name: created.name,
    sourceTableId: child.id,
    sourceColumnIds: [child.columns[0].id],
    targetTableId: parent.id,
    targetKeyId: parent.keys[0].id,
    onUpdate: "NO ACTION",
    onDelete: "NO ACTION",
    deferrable: false,
    initiallyDeferred: false,
  }, nextUuid);
  assert.equal(sameName.id, created.id);
  assert.equal(sameName.name, created.name);

  const updated = updateDesignRelationship(content, created.id, {
    name: "children_parent_code_fkey",
    sourceTableId: child.id,
    sourceColumnIds: [child.columns[1].id],
    targetTableId: parent.id,
    targetKeyId: withUnique.key.id,
    onUpdate: "CASCADE",
    onDelete: "RESTRICT",
    deferrable: true,
    initiallyDeferred: true,
  }, nextUuid);

  assert.equal(updated.id, created.id);
  assert.deepEqual(updated.targetColumnIds, [parent.columns[1].id]);
  assert.equal(updated.onUpdate, "CASCADE");
  assert.equal(updated.initiallyDeferred, true);
  assert.equal(content.relationships[0].name, "children_parent_id_fkey");
  assert.throws(() => updateDesignRelationship(content, `relationship_${"f".repeat(32)}`, {
    ...updated,
    targetKeyId: withUnique.key.id,
  }), /no longer/);
});

test("relationship editing drafts retain exact mappings and expose every target key", () => {
  let value = 1;
  const nextUuid = () => `${String(value++).padStart(8, "0")}-0000-0000-0000-000000000000`;
  const parent = createDesignTable("parents", [
    { name: "id", dataType: "bigint", nullable: false, primary: true },
    { name: "code", dataType: "text", nullable: false, primary: false },
  ], nextUuid);
  const keyed = saveDesignKey({ tables: [parent], relationships: [], functions: [], views: [] }, {
    tableId: parent.id,
    name: "parents_code_key",
    kind: "unique",
    columnIds: [parent.columns[1].id],
  }, nextUuid);
  const child = createDesignTable("children", [
    { name: "parent_code", dataType: "text", nullable: false, primary: false },
  ], nextUuid);
  const base = { ...keyed.content, tables: [keyed.content.tables[0], child] };
  const relationship = createDesignRelationship(base, {
    name: "children_parent_code_fkey",
    sourceTableId: child.id,
    sourceColumnIds: [child.columns[0].id],
    targetTableId: parent.id,
    targetKeyId: keyed.key.id,
    onUpdate: "NO ACTION",
    onDelete: "NO ACTION",
    deferrable: false,
    initiallyDeferred: false,
  }, nextUuid);

  const draft = relationshipDraftFromExisting({ ...base, relationships: [relationship] }, relationship.id);

  assert.deepEqual(draft.sourceColumnIds, relationship.sourceColumnIds);
  assert.equal(draft.targetKeyId, keyed.key.id);
  assert.deepEqual(draft.eligibleTargetKeyIds, keyed.content.tables[0].keys.map(key => key.id));
  assert.equal(draft.sourceColumnId, null);
  assert.equal(draft.targetColumnId, null);
});

test("graphical target selection rejects columns that are not keys", () => {
  let value = 1;
  const nextUuid = () => `${String(value++).padStart(8, "0")}-0000-0000-0000-000000000000`;
  const source = createDesignTable("orders", [
    { name: "customer_id", dataType: "bigint", nullable: false, primary: false },
  ], nextUuid);
  const target = createDesignTable("customers", [
    { name: "id", dataType: "bigint", nullable: false, primary: true },
    { name: "email", dataType: "text", nullable: false, primary: false },
  ], nextUuid);
  const content = { tables: [source, target], relationships: [], functions: [], views: [] };

  assert.throws(() => relationshipDraftFromColumns(content, {
    sourceTableId: source.id,
    sourceColumnId: source.columns[0].id,
    targetTableId: target.id,
    targetColumnId: target.columns[1].id,
  }), /primary or unique key/);
});

test("graphical key authoring creates ordered composite keys and primary keys enforce not null", () => {
  let value = 1;
  const nextUuid = () => `${String(value++).padStart(8, "0")}-0000-0000-0000-000000000000`;
  const table = createDesignTable("memberships", [
    { name: "tenant_id", dataType: "uuid", nullable: true, primary: false },
    { name: "email", dataType: "text", nullable: true, primary: false },
  ], nextUuid);
  const content = { tables: [table], relationships: [], functions: [], views: [] };
  const uniqueName = suggestDesignKeyName(content, {
    tableId: table.id,
    kind: "unique",
    columnIds: [table.columns[0].id, table.columns[1].id],
  });

  const unique = saveDesignKey(content, {
    tableId: table.id,
    name: uniqueName,
    kind: "unique",
    columnIds: [table.columns[0].id, table.columns[1].id],
  }, nextUuid);
  assert.equal(uniqueName, "memberships_tenant_id_email_key");
  assert.deepEqual(unique.key.columnIds, [table.columns[0].id, table.columns[1].id]);
  assert.equal(unique.content.tables[0].columns[0].nullable, true);

  const primary = saveDesignKey(unique.content, {
    tableId: table.id,
    name: "memberships_pkey",
    kind: "primary",
    columnIds: [table.columns[0].id],
  }, nextUuid);
  assert.equal(primary.content.tables[0].columns[0].nullable, false);
  assert.equal(content.tables[0].keys.length, 0);
});

test("key editing and deletion preserve referenced target keys", () => {
  let value = 1;
  const nextUuid = () => `${String(value++).padStart(8, "0")}-0000-0000-0000-000000000000`;
  const parent = createDesignTable("parents", [
    { name: "id", dataType: "bigint", nullable: false, primary: true },
    { name: "code", dataType: "text", nullable: false, primary: false },
  ], nextUuid);
  const child = createDesignTable("children", [
    { name: "parent_id", dataType: "bigint", nullable: false, primary: false },
  ], nextUuid);
  const base = { tables: [parent, child], relationships: [], functions: [], views: [] };
  const relationship = createDesignRelationship(base, {
    name: "children_parents_fkey",
    sourceTableId: child.id,
    sourceColumnIds: [child.columns[0].id],
    targetTableId: parent.id,
    targetKeyId: parent.keys[0].id,
    onUpdate: "NO ACTION",
    onDelete: "NO ACTION",
    deferrable: false,
    initiallyDeferred: false,
  }, nextUuid);
  const content = { ...base, relationships: [relationship] };

  const renamed = saveDesignKey(content, {
    tableId: parent.id,
    keyId: parent.keys[0].id,
    name: "parents_identity_pkey",
    kind: "primary",
    columnIds: [parent.columns[0].id],
  }, nextUuid);
  assert.equal(renamed.key.name, "parents_identity_pkey");
  assert.throws(() => saveDesignKey(content, {
    tableId: parent.id,
    keyId: parent.keys[0].id,
    name: "parents_pkey",
    kind: "primary",
    columnIds: [parent.columns[1].id],
  }, nextUuid), /targets this key/);
  assert.throws(() => deleteDesignKey(content, parent.id, parent.keys[0].id), /targets this key/);

  const withoutRelationship = { ...content, relationships: [] };
  assert.equal(deleteDesignKey(withoutRelationship, parent.id, parent.keys[0].id).content.tables[0].keys.length, 0);
});
