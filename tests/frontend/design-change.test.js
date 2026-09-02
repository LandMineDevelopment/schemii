import assert from "node:assert/strict";
import test from "node:test";

import {
  designChangePresentation,
  designChangeTargets,
  viewAnalysisContextSignature,
} from "../../src/schemii/schemii/web/assets/design-change.js";

function designContent() {
  return {
    tables: [{
      id: "table_orders",
      name: "orders",
      columns: [
        { id: "column_id", name: "id", dataType: "uuid", nullable: false },
        { id: "column_status", name: "status", dataType: "text", nullable: true },
      ],
      keys: [{ id: "key_orders_pk", name: "orders_pk", kind: "primary", columnIds: ["column_id"] }],
      checks: [],
      indexes: [],
    }],
    relationships: [],
    views: [{ id: "view_order_totals", name: "order_totals", kind: "view", definition: "SELECT id FROM orders" }],
    functions: [],
    types: [],
    triggers: [],
  };
}

test("design changes target the exact stable column field in the schema tone", () => {
  const before = designContent();
  const after = structuredClone(before);
  after.tables[0].columns[1].dataType = "character varying(80)";

  assert.deepEqual(designChangeTargets(before, after), [{
    objectId: "column_status",
    fallbackId: "table_orders",
    scope: "schema",
    kind: "column",
    name: "status",
    parentName: "orders",
    operation: "update",
    fields: ["dataType"],
    tone: "amber",
  }]);
});

test("view source changes use the purple view tone", () => {
  const before = designContent();
  const after = structuredClone(before);
  after.views[0].definition = "SELECT id, status FROM orders";

  assert.deepEqual(designChangeTargets(before, after), [{
    objectId: "view_order_totals",
    fallbackId: null,
    scope: "views",
    kind: "view",
    name: "order_totals",
    parentName: null,
    operation: "update",
    fields: ["definition"],
    tone: "purple",
  }]);
});

test("column order changes target moved rows without treating the table as changed", () => {
  const before = designContent();
  const after = structuredClone(before);
  after.tables[0].columns.reverse();

  const targets = designChangeTargets(before, after);
  assert.equal(targets.length, 2);
  assert.deepEqual(targets.map(target => [target.objectId, target.fields]), [
    ["column_id", ["order"]],
    ["column_status", ["order"]],
  ]);
});

test("removed nested objects fall back to their surviving parent", () => {
  const before = designContent();
  const after = structuredClone(before);
  after.tables[0].columns = after.tables[0].columns.filter(column => column.id !== "column_status");

  assert.deepEqual(designChangeTargets(before, after), [{
    objectId: "column_status",
    fallbackId: "table_orders",
    scope: "schema",
    kind: "column",
    name: "status",
    parentName: "orders",
    operation: "remove",
    fields: [],
    tone: "amber",
  }]);
});

test("key changes also cue the affected column constraint field", () => {
  const before = designContent();
  const after = structuredClone(before);
  after.tables[0].keys[0].name = "orders_primary";

  assert.deepEqual(designChangeTargets(before, after), [
    {
      objectId: "key_orders_pk",
      fallbackId: "table_orders",
      scope: "schema",
      kind: "key",
      name: "orders_primary",
      parentName: "orders",
      operation: "update",
      fields: ["name"],
      tone: "amber",
    },
    {
      objectId: "column_id",
      fallbackId: "table_orders",
      scope: "schema",
      kind: "column",
      name: "id",
      parentName: "orders",
      operation: "related",
      fields: ["primary"],
      tone: "amber",
    },
  ]);
});

test("change presentation identifies the affected layer and concrete result", () => {
  const before = designContent();
  const removed = structuredClone(before);
  removed.views = [];
  const removedPresentation = designChangePresentation(designChangeTargets(before, removed));

  assert.equal(removedPresentation.layer, "views");
  assert.equal(removedPresentation.headline, "Removed view order_totals");
  assert.equal(removedPresentation.primary.objectId, "view_order_totals");

  const restoredPresentation = designChangePresentation(designChangeTargets(removed, before));
  assert.equal(restoredPresentation.layer, "views");
  assert.equal(restoredPresentation.headline, "Created view order_totals");
});

test("top-level database objects retain their owning relation for visibility routing", () => {
  const before = designContent();
  before.relationships = [{
    id: "relationship_orders_customer",
    name: "orders_customer_fk",
    sourceTableId: "table_orders",
    sourceColumnIds: ["column_id"],
    targetTableId: "table_customers",
    targetColumnIds: ["column_customer_id"],
  }];
  before.triggers = [{
    id: "trigger_orders_audit",
    name: "orders_audit",
    relationName: "orders",
    definition: "CREATE TRIGGER orders_audit AFTER UPDATE ON orders EXECUTE FUNCTION audit();",
  }];
  const after = structuredClone(before);
  after.relationships = [];
  after.triggers = [];

  const targets = designChangeTargets(before, after);
  assert.deepEqual(
    targets.filter(target => target.operation === "remove").map(target => ({
      objectId: target.objectId,
      fallbackId: target.fallbackId,
      relationName: target.relationName,
    })),
    [
      {
        objectId: "relationship_orders_customer",
        fallbackId: "table_orders",
        relationName: "orders",
      },
      {
        objectId: "trigger_orders_audit",
        fallbackId: "table_orders",
        relationName: "orders",
      },
    ],
  );
});

test("view analysis context follows query-relevant design source and survives an exact restore", () => {
  const original = designContent();
  const restored = structuredClone(original);
  const changedType = structuredClone(original);
  changedType.tables[0].columns[1].dataType = "character varying(80)";
  const changedConsumer = structuredClone(original);
  changedConsumer.views.push({
    id: "view_open_orders",
    name: "open_orders",
    kind: "view",
    definition: "SELECT * FROM order_totals",
  });

  assert.equal(viewAnalysisContextSignature(restored), viewAnalysisContextSignature(original));
  assert.notEqual(viewAnalysisContextSignature(changedType), viewAnalysisContextSignature(original));
  assert.notEqual(viewAnalysisContextSignature(changedConsumer), viewAnalysisContextSignature(original));
});
