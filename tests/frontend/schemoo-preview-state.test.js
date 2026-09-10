import { test } from "node:test";
import assert from "node:assert/strict";
import { importedDraft } from "../../src/schemii/schemoo/web/model-draft.js";
import { previewState, reconcilePreview, samePreview, tablePreviewAdditions } from "../../src/schemii/schemoo/web/preview-state.js";

const catalog = { tables: [{ name: "people", columns: [{ name: "id", dataType: "integer" }, { name: "name", dataType: "text" }], primaryKey: ["id"] }], relationships: [] };
test("table additions respect exposure, table order, existing details and measures without mutating the model", () => {
  const draft = importedDraft(catalog);
  draft.exposedFields = [{ table: "people", column: "name" }, { table: "people", column: "id" }];
  draft.fields = [{ table: "people", column: "name", aggregate: "count" }];
  const before = structuredClone(draft);
  const additions = tablePreviewAdditions(draft, catalog, "people");
  assert.equal(additions.canAdd, true);
  assert.deepEqual(additions.fields, [{ table: "people", column: "id", aggregate: "none" }, { table: "people", column: "name", aggregate: "none" }]);
  assert.deepEqual(draft, before);
  draft.fields.push(...additions.fields);
  assert.equal(tablePreviewAdditions(draft, catalog, "people").canAdd, false);
  assert.deepEqual(tablePreviewAdditions(draft, catalog, "people").fields, []);
});
test("table additions keep aliases separate and omit hidden or removed columns", () => {
  const draft = importedDraft(catalog);
  draft.nodes.push({ id: "alias", table: "people", label: "Historical people" });
  draft.exposedFields = [{ table: "alias", column: "name" }, { table: "alias", column: "gone" }];
  draft.fields = [{ table: "people", column: "name" }];
  assert.deepEqual(tablePreviewAdditions(draft, catalog, "alias").fields, [{ table: "alias", column: "name", aggregate: "none" }]);
  assert.equal(tablePreviewAdditions(draft, catalog, "people").total, 0);
  assert.equal(tablePreviewAdditions(draft, catalog, "missing").canAdd, false);
});
test("table additions cannot silently add a partial table beyond the preview limit", () => {
  const draft = importedDraft(catalog);
  draft.exposedFields = null;
  draft.fields = Array.from({ length: 63 }, (_, i) => ({ table: "other", column: `column${i}` }));
  const additions = tablePreviewAdditions(draft, catalog, "people");
  assert.equal(additions.remaining, 1);
  assert.equal(additions.fields.length, 2);
  assert.equal(additions.canAdd, false);
  assert.equal(draft.fields.length, 63);
});
test("saved preview reconciles physical and aliased columns without copying or altering the model", () => {
  const draft = importedDraft(catalog);
  draft.nodes.push({ id: "history", table: "people", label: "Historical people" });
  draft.exposedFields = null;
  const original = structuredClone(draft);
  const saved = { root: "people", fields: [{ table: "history", column: "removed", aggregate: "count" }, { table: "history", column: "name", aggregate: "count_distinct" }],
    selections: {}, reportFilters: [{ id: "restrict", conditions: [{ table: "history", column: "removed", operator: "not_null" }] }], limit: 100 };
  const result = reconcilePreview(saved, draft, catalog);
  assert.equal(result.removedOutputs, 1);
  assert.deepEqual(result.explore.fields, [saved.fields[1]]);
  assert.deepEqual(result.explore.reportFilters, saved.reportFilters);
  assert.deepEqual(draft, original);
  assert.equal(saved.fields.length, 2);
  assert.equal(result.explore.definition, undefined);
  assert.equal(result.explore.sourceContract, undefined);
});
test("new schema fields do not silently expand saved output order or enable joins", () => {
  const draft = importedDraft(catalog);
  const saved = previewState(draft);
  saved.fields = [{ table: "people", column: "name", aggregate: "count" }, { table: "people", column: "id", aggregate: "none" }];
  const nextCatalog = structuredClone(catalog);
  nextCatalog.tables[0].columns.push({ name: "email", dataType: "text" });
  const result = reconcilePreview(saved, draft, nextCatalog);
  assert.deepEqual(result.explore.fields, saved.fields);
  assert.equal(result.changed, false);
});
test("removed exposure is pruned but report restrictions and missing roots require explicit repair", () => {
  const draft = importedDraft(catalog);
  draft.exposedFields = [{ table: "people", column: "id" }];
  const saved = { root: "removed_alias", fields: [{ table: "people", column: "name" }, { table: "people", column: "id" }], selections: { deleted_scope: { values: { p: "yesterday" } } }, reportFilters: [{ id: "restrict", conditions: [] }], limit: 100 };
  const result = reconcilePreview(saved, draft, catalog);
  assert.deepEqual(result.explore.fields, [saved.fields[1]]);
  assert.equal(result.explore.root, "removed_alias");
  assert.deepEqual(result.explore.selections, {});
  assert.deepEqual(result.explore.reportFilters, saved.reportFilters);
  assert.ok(samePreview(result.explore, structuredClone(result.explore)));
});
