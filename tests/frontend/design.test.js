import assert from "node:assert/strict";
import test from "node:test";

import {
  alignRelationshipColumnTypes,
  createDesignRelationship,
  createDesignTable,
  deleteDesignRoutine,
  deleteDesignTrigger,
  deleteDesignView,
  deleteDesignCheck,
  deleteDesignIndex,
  deleteDesignKey,
  designLayoutContent,
  designPositions,
  designToCatalog,
  expressionColumnIds,
  relationshipDraftFromColumns,
  relationshipDraftFromExisting,
  saveDesignCheck,
  saveDesignIndex,
  saveDesignKey,
  saveDesignRoutine,
  saveDesignTrigger,
  saveDesignView,
  suggestDesignCheckName,
  suggestDesignIndexName,
  suggestDesignKeyName,
  toggleDesignIndexColumn,
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

test("view authoring stores only durable source inputs and retains stable identity on edit", () => {
  const base = { tables: [], relationships: [], functions: [], views: [] };
  const created = saveDesignView(base, {
    name: "account_totals",
    kind: "materialized_view",
    populateOnCreate: false,
    definition: "SELECT account_id, sum(total) AS total FROM orders GROUP BY account_id;",
  }, () => uuids[0]);

  assert.equal(created.view.id, `view_${"1".repeat(32)}`);
  assert.equal(created.view.populateOnCreate, false);
  assert.equal(created.view.definition.endsWith(";"), false);
  assert.deepEqual(Object.keys(created.view), ["id", "name", "kind", "definition", "populateOnCreate"]);

  const edited = saveDesignView(created.content, {
    viewId: created.view.id,
    name: "account_totals",
    kind: "view",
    definition: "SELECT account_id, total FROM orders",
  }, () => uuids[1]);
  assert.equal(edited.view.id, created.view.id);
  assert.equal(edited.view.populateOnCreate, null);
  assert.equal(deleteDesignView(edited.content, edited.view.id).content.views.length, 0);
});

test("routine authoring stores source only and retains stable identity on edit", () => {
  const base = { tables: [], relationships: [], functions: [], views: [] };
  const definition = "CREATE FUNCTION total(amount numeric) RETURNS numeric LANGUAGE sql AS $$ SELECT amount $$";
  const created = saveDesignRoutine(base, { definition }, () => uuids[0]);

  assert.deepEqual(created.routine, {
    id: `function_${"1".repeat(32)}`,
    definition,
  });

  const revisedDefinition = definition.replace("SELECT amount", "SELECT amount * 2");
  const edited = saveDesignRoutine(created.content, {
    routineId: created.routine.id,
    definition: revisedDefinition,
  });
  assert.equal(edited.content.functions[0].id, created.routine.id);
  assert.equal(edited.content.functions[0].definition, revisedDefinition);

  assert.deepEqual(deleteDesignRoutine(edited.content, created.routine.id).functions, []);
});

test("trigger authoring stores source only and projects derived contracts into the catalog", () => {
  let index = 0;
  const table = createDesignTable("orders", [
    { name: "id", dataType: "bigint", nullable: false },
    { name: "status", dataType: "text", nullable: false },
  ], () => uuids[index++]);
  const base = { tables: [table], relationships: [], functions: [], views: [], triggers: [] };
  const definition = "CREATE TRIGGER orders_touch BEFORE UPDATE OF status ON orders FOR EACH ROW EXECUTE FUNCTION touch_order()";
  const created = saveDesignTrigger(base, { definition }, () => uuids[index++]);

  assert.deepEqual(created.trigger, {
    id: `trigger_${"4".repeat(32)}`,
    definition,
  });
  created.content.triggers[0] = {
    ...created.content.triggers[0],
    name: "orders_touch",
    relationName: "orders",
    timing: "before",
    events: ["update"],
    orientation: "row",
    functionName: "touch_order",
    functionArguments: [],
    updateColumns: ["status"],
    referencedColumns: ["status"],
    whenExpression: null,
    transitionRelations: [],
    constraint: false,
    deferrable: false,
    initiallyDeferred: false,
  };
  const design = { revision: 1, fingerprint: "a".repeat(64), content: created.content };
  const catalog = designToCatalog({ name: "Orders" }, design);

  assert.equal(catalog.triggers[0].relationName, "orders");
  assert.equal(catalog.tables[0].triggers[0].functionName, "touch_order");
  assert.deepEqual(deleteDesignTrigger(created.content, created.trigger.id).triggers, []);
});

test("table and column renames cannot silently desynchronize trigger source", () => {
  let index = 0;
  const table = createDesignTable("orders", [
    { name: "id", dataType: "bigint", nullable: false },
    { name: "status", dataType: "text", nullable: false },
  ], () => uuids[index++]);
  const content = {
    tables: [table],
    relationships: [],
    functions: [],
    views: [],
    triggers: [{ name: "orders_touch", relationName: "orders", referencedColumns: ["status"] }],
  };
  const values = table.columns.map(column => ({ ...column, primary: false }));

  assert.throws(() => updateDesignTable(content, table.id, "purchases", values), /triggers/);
  values[1].name = "state";
  assert.throws(() => updateDesignTable(content, table.id, "orders", values), /orders_touch/);
});

test("desired routine catalog entries expose the server-derived identity signature", () => {
  const routine = {
    id: `function_${"1".repeat(32)}`,
    name: "calculate_total",
    kind: "function",
    arguments: "amount DECIMAL, tax DECIMAL DEFAULT 0",
    identityArguments: "DECIMAL, DECIMAL",
    returnType: "DECIMAL",
    language: "sql",
    definition: "CREATE FUNCTION calculate_total(amount numeric, tax numeric DEFAULT 0) RETURNS numeric LANGUAGE sql AS $$ SELECT amount * (1 + tax) $$",
  };
  const design = {
    revision: 1,
    fingerprint: "a".repeat(64),
    content: { tables: [], relationships: [], functions: [routine], views: [] },
  };

  const catalogRoutine = designToCatalog({ name: "Design" }, design).functions[0];

  assert.equal(catalogRoutine.designId, routine.id);
  assert.equal(catalogRoutine.identityArguments, "DECIMAL, DECIMAL");
});

test("view authoring rejects generated CREATE wrappers and relation-name collisions", () => {
  const table = createDesignTable("accounts", [{ name: "id", dataType: "uuid", nullable: false }], () => uuids[0]);
  const content = { tables: [table], relationships: [], functions: [], views: [] };
  assert.throws(() => saveDesignView(content, {
    name: "accounts",
    kind: "view",
    definition: "SELECT 1",
  }, () => uuids[1]), /already exists/);
  assert.throws(() => saveDesignView(content, {
    name: "account_view",
    kind: "view",
    definition: "CREATE VIEW account_view AS SELECT 1",
  }, () => uuids[1]), /SELECT query body/);
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

test("column value behaviors and generated dependencies are authored from one table editor payload", () => {
  let value = 1;
  const nextUuid = () => `${String(value++).padStart(8, "0")}-0000-0000-0000-000000000000`;
  const table = createDesignTable("line_items", [
    { name: "quantity", dataType: "integer", nullable: false },
    { name: "unit_price", dataType: "numeric(12,2)", nullable: false },
    { name: "total", dataType: "numeric(12,2)", nullable: false, generatedExpression: "quantity * unit_price" },
    { name: "created_at", dataType: "timestamptz", nullable: false, defaultExpression: "now()" },
    { name: "sequence", dataType: "bigint", nullable: true, identity: "by_default" },
  ], nextUuid);

  assert.deepEqual(table.columns[2].generatedSourceColumnIds, [table.columns[0].id, table.columns[1].id]);
  assert.equal(table.columns[3].defaultExpression, "now()");
  assert.equal(table.columns[4].identity, "by_default");
  assert.equal(table.columns[4].nullable, false);
  const withoutIdentity = updateDesignTable(
    { tables: [table], relationships: [], functions: [], views: [] },
    table.id,
    table.name,
    table.columns.map(column => ({
      ...column,
      nullable: column.id === table.columns[4].id ? true : column.nullable,
      identity: column.id === table.columns[4].id ? null : column.identity,
      primary: false,
    })),
    nextUuid,
  );
  assert.equal(withoutIdentity.columns[4].identity, null);
  assert.equal(withoutIdentity.columns[4].nullable, true);
  assert.throws(() => createDesignTable("invalid", [{
    name: "id",
    dataType: "bigint",
    defaultExpression: "1",
    identity: "always",
  }], nextUuid), /one value behavior/);
});

test("table column reordering preserves physical order without silently changing composite key order", () => {
  let value = 1;
  const nextUuid = () => `${String(value++).padStart(8, "0")}-0000-0000-0000-000000000000`;
  const table = createDesignTable("memberships", [
    { name: "tenant_id", dataType: "uuid", nullable: false, primary: true },
    { name: "member_id", dataType: "uuid", nullable: false, primary: true },
    { name: "label", dataType: "text", nullable: false, primary: false },
  ], nextUuid);
  const originalPrimaryOrder = [...table.keys[0].columnIds];

  const reordered = updateDesignTable(
    { tables: [table], relationships: [], functions: [], views: [] },
    table.id,
    table.name,
    [...table.columns].reverse().map(column => ({ ...column, primary: originalPrimaryOrder.includes(column.id) })),
    nextUuid,
  );

  assert.deepEqual(reordered.columns.map(column => column.name), ["label", "member_id", "tenant_id"]);
  assert.deepEqual(reordered.keys[0].columnIds, originalPrimaryOrder);
});

test("generated columns reject dependencies on other generated columns before saving", () => {
  let value = 1;
  const nextUuid = () => `${String(value++).padStart(8, "0")}-0000-0000-0000-000000000000`;

  assert.throws(() => createDesignTable("totals", [
    { name: "quantity", dataType: "integer", nullable: false },
    { name: "subtotal", dataType: "numeric", nullable: false, generatedExpression: "quantity * 10" },
    { name: "display_total", dataType: "text", nullable: false, generatedExpression: "subtotal::text" },
  ], nextUuid), /cannot reference another generated column/);
});

test("check constraints derive stable dependencies and column renames rewrite SQL identifiers only", () => {
  let value = 1;
  const nextUuid = () => `${String(value++).padStart(8, "0")}-0000-0000-0000-000000000000`;
  const table = createDesignTable("line_items", [
    { name: "quantity", dataType: "integer", nullable: false },
    { name: "unit_price", dataType: "numeric", nullable: false },
    { name: "note", dataType: "text", nullable: true },
    { name: "total", dataType: "numeric", nullable: false, generatedExpression: "quantity * unit_price" },
  ], nextUuid);
  const base = { tables: [table], relationships: [], functions: [], views: [] };
  assert.deepEqual(expressionColumnIds("quantity > 0 AND note <> 'quantity'", table.columns), [
    table.columns[0].id,
    table.columns[2].id,
  ]);
  const saved = saveDesignCheck(base, {
    tableId: table.id,
    name: "line_items_quantity_check",
    expression: "quantity > 0 AND note <> 'quantity'",
  }, nextUuid);

  const updated = updateDesignTable(saved.content, table.id, table.name, table.columns.map(column => ({
    ...column,
    name: column.name === "quantity" ? "qty" : column.name,
    primary: false,
  })), nextUuid);

  assert.equal(updated.columns[3].generatedExpression, "qty * unit_price");
  assert.equal(updated.checks[0].expression, "qty > 0 AND note <> 'quantity'");
  assert.deepEqual(updated.checks[0].columnIds, [updated.columns[0].id, updated.columns[2].id]);
});

test("check constraints support stable create, edit, catalog projection, and delete", () => {
  let value = 1;
  const nextUuid = () => `${String(value++).padStart(8, "0")}-0000-0000-0000-000000000000`;
  const table = createDesignTable("products", [
    { name: "price", dataType: "numeric", nullable: false },
  ], nextUuid);
  const base = { tables: [table], relationships: [], functions: [], views: [] };
  const suggested = suggestDesignCheckName(base, {
    tableId: table.id,
    expression: "price >= 0",
  });
  assert.equal(suggested, "products_price_check");
  const created = saveDesignCheck(base, {
    tableId: table.id,
    name: suggested,
    expression: "price >= 0",
  }, nextUuid);
  const edited = saveDesignCheck(created.content, {
    tableId: table.id,
    checkId: created.check.id,
    name: suggested,
    expression: "price > 0",
  }, nextUuid);
  assert.equal(edited.check.id, created.check.id);
  assert.deepEqual(edited.check.columnIds, [table.columns[0].id]);

  const design = { revision: 1, fingerprint: "a".repeat(64), content: edited.content };
  const catalogCheck = designToCatalog({ name: "Products" }, design).tables[0].checks[0];
  assert.equal(catalogCheck.designId, created.check.id);
  assert.deepEqual(catalogCheck.columns, ["price"]);
  assert.equal(deleteDesignCheck(edited.content, table.id, created.check.id).content.tables[0].checks.length, 0);
});

test("table editing blocks removal of generated and check dependency columns", () => {
  let value = 1;
  const nextUuid = () => `${String(value++).padStart(8, "0")}-0000-0000-0000-000000000000`;
  const generatedTable = createDesignTable("totals", [
    { name: "amount", dataType: "numeric", nullable: false },
    { name: "doubled", dataType: "numeric", nullable: false, generatedExpression: "amount * 2" },
  ], nextUuid);
  const generatedContent = { tables: [generatedTable], relationships: [], functions: [], views: [] };
  assert.throws(() => updateDesignTable(generatedContent, generatedTable.id, generatedTable.name, [{
    ...generatedTable.columns[1], primary: false,
  }]), /generated column/);

  const checkedTable = createDesignTable("inventory", [
    { name: "quantity", dataType: "integer", nullable: false },
    { name: "sku", dataType: "text", nullable: false },
  ], nextUuid);
  const checkedBase = { tables: [checkedTable], relationships: [], functions: [], views: [] };
  const checked = saveDesignCheck(checkedBase, {
    tableId: checkedTable.id,
    name: "inventory_quantity_check",
    expression: "quantity >= 0",
  }, nextUuid);
  assert.throws(() => updateDesignTable(checked.content, checkedTable.id, checkedTable.name, [{
    ...checkedTable.columns[1], primary: false,
  }]), /check constraint/);
});

test("index authoring supports ordered, expression, partial, unique, edit, and catalog workflows", () => {
  let value = 1;
  const nextUuid = () => `${String(value++).padStart(8, "0")}-0000-0000-0000-000000000000`;
  const table = createDesignTable("accounts", [
    { name: "tenant_id", dataType: "uuid", nullable: false },
    { name: "email", dataType: "text", nullable: false },
    { name: "active", dataType: "boolean", nullable: false },
  ], nextUuid);
  const base = { tables: [table], relationships: [], functions: [], views: [] };
  const suggested = suggestDesignIndexName(base, {
    tableId: table.id,
    columnIds: [table.columns[0].id, table.columns[1].id],
  });
  assert.equal(suggested, "accounts_tenant_id_email_idx");
  const created = saveDesignIndex(base, {
    tableId: table.id,
    name: suggested,
    method: "btree",
    columnIds: [table.columns[0].id],
    expression: "lower(email)",
    predicate: "active",
    unique: true,
  }, nextUuid);

  assert.deepEqual(created.index.columnIds, [table.columns[0].id]);
  assert.deepEqual(created.index.expressionSourceColumnIds, [table.columns[1].id]);
  assert.deepEqual(created.index.predicateColumnIds, [table.columns[2].id]);
  assert.equal(created.index.unique, true);
  const edited = saveDesignIndex(created.content, {
    tableId: table.id,
    indexId: created.index.id,
    name: created.index.name,
    method: "hash",
    columnIds: [],
    expression: "lower(email)",
    predicate: "active",
    unique: false,
  }, nextUuid);
  assert.equal(edited.index.id, created.index.id);
  assert.equal(edited.index.method, "hash");
  assert.deepEqual(edited.index.columnIds, []);

  const design = { revision: 1, fingerprint: "a".repeat(64), content: edited.content };
  const catalogIndex = designToCatalog({ name: "Accounts" }, design).tables[0].indexes[0];
  assert.equal(catalogIndex.designId, created.index.id);
  assert.deepEqual(catalogIndex.columns, []);
  assert.equal(catalogIndex.expression, "lower(email)");
  assert.equal(deleteDesignIndex(edited.content, table.id, created.index.id).content.tables[0].indexes.length, 0);
  assert.throws(() => saveDesignIndex(base, {
    tableId: table.id,
    name: "empty_idx",
    method: "btree",
    columnIds: [],
    expression: "",
    predicate: "active",
    unique: false,
  }, nextUuid), /Select a column or enter an index expression/);
});

test("index column selection binds to the first chosen table and unlocks when cleared", () => {
  const first = toggleDesignIndexColumn(null, "table_accounts", "column_email");
  assert.deepEqual(first, {
    tableId: "table_accounts",
    indexId: null,
    columnIds: ["column_email"],
  });
  assert.equal(
    toggleDesignIndexColumn(first, "table_orders", "column_created_at"),
    null,
  );

  const cleared = toggleDesignIndexColumn(first, "table_accounts", "column_email");
  assert.deepEqual(cleared, {
    tableId: null,
    indexId: null,
    columnIds: [],
  });
  assert.deepEqual(
    toggleDesignIndexColumn(cleared, "table_orders", "column_created_at"),
    {
      tableId: "table_orders",
      indexId: null,
      columnIds: ["column_created_at"],
    },
  );
});

test("editing an expression index remains bound to its owning table", () => {
  const editing = {
    tableId: "table_accounts",
    indexId: "index_email",
    columnIds: [],
  };
  assert.equal(
    toggleDesignIndexColumn(editing, "table_orders", "column_created_at"),
    null,
  );
  assert.deepEqual(
    toggleDesignIndexColumn(editing, "table_accounts", "column_email"),
    {
      tableId: "table_accounts",
      indexId: "index_email",
      columnIds: ["column_email"],
    },
  );
});

test("index dependencies follow column renames and block unsafe removal", () => {
  let value = 1;
  const nextUuid = () => `${String(value++).padStart(8, "0")}-0000-0000-0000-000000000000`;
  const table = createDesignTable("accounts", [
    { name: "email", dataType: "text", nullable: false },
    { name: "active", dataType: "boolean", nullable: false },
  ], nextUuid);
  const base = { tables: [table], relationships: [], functions: [], views: [] };
  const indexed = saveDesignIndex(base, {
    tableId: table.id,
    name: "accounts_email_idx",
    method: "btree",
    columnIds: [],
    expression: "lower(email)",
    predicate: "active AND 'email' <> ''",
    unique: false,
  }, nextUuid);
  const updated = updateDesignTable(indexed.content, table.id, table.name, [
    { ...table.columns[0], name: "address", primary: false },
    { ...table.columns[1], name: "enabled", primary: false },
  ], nextUuid);
  assert.equal(updated.indexes[0].expression, "lower(address)");
  assert.equal(updated.indexes[0].predicate, "enabled AND 'email' <> ''");
  assert.deepEqual(updated.indexes[0].expressionSourceColumnIds, [table.columns[0].id]);
  assert.deepEqual(updated.indexes[0].predicateColumnIds, [table.columns[1].id]);
  assert.throws(() => updateDesignTable(indexed.content, table.id, table.name, [{
    ...table.columns[1], primary: false,
  }]), /index “accounts_email_idx”/);
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
