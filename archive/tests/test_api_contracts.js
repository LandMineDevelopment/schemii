const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const context = vm.createContext({ window: {}, Error, TypeError, Object, Array });
vm.runInContext(fs.readFileSync("src/schemii/shared_web/api-contracts.js", "utf8"), context);
const contracts = context.window.SchemiiShared;

assert.equal(contracts.validateSessionResponse({ token: "token", serverId: "server" }).token, "token");
assert.equal(contracts.validateProfilesResponse({ profiles: [{ id: "local" }] }).profiles.length, 1);
const page = { pageSize: 2, returned: 1, hasMore: false, nextCursor: null };
const resultResource = {
  version: 1, id: "retained_result_123", kind: "detail", binding: "opaque-binding",
  state: "retained", processLocal: true, expiresAt: "2026-01-01T00:00:00Z",
  page: { offset: 0, returnedRows: 0, hasNext: false, hasPrevious: false, nextCursor: null, previousCursor: null },
  export: { formats: ["json", "csv"], persistentUntilExpiry: true },
};
const aggregateResultResource = { ...resultResource, kind: "aggregate" };
const execution = { effectiveQuery: {}, slicerLineage: [] };
const identity = { profileId: "local", profileFingerprint: "a".repeat(64), database: "demo", catalogFingerprint: "b".repeat(64), page };
const legacyPreview = {
  dashboardId: "dashboard", expectedRevision: 2, widgetIds: ["compatible", "incompatible"],
  deferredWidgetIds: ["next-batch"], maximumUniqueProfileDatabases: 4, maximumDigestLength: 28604,
  results: [
    {
      widgetId: "compatible", title: "Compatible", status: "compatible",
      source: { profileId: "local", database: "demo", namespace: "public", relation: "orders", kind: "table" },
      profileFingerprint: "9".repeat(64),
      savedLegacyFingerprint: "a".repeat(64), currentLegacyFingerprint: "a".repeat(64), currentFingerprint: "b".repeat(64),
      columnCount: 2, columns: "exact", query: "valid",
    },
    { widgetId: "incompatible", title: "Incompatible", status: "incompatible", error: { code: "legacy_source_changed", message: "Reselect this source" } },
  ],
  compatibleWidgetIds: ["compatible"], incompatibleWidgetIds: ["incompatible"], digest: "signed-review", expiresAt: "2026-08-24T00:05:00Z",
};
assert.equal(contracts.validateCatalogResponse({ ...identity, scope: "user", entries: [{ name: "public", classification: "user", system: false }], namespaces: ["public"] }, "namespaces").namespaces[0], "public");
const relation = { profileId: "local", database: "demo", namespace: "public", relation: "orders", name: "orders", kind: "foreign_table" };
assert.equal(contracts.validateCatalogResponse({ ...identity, namespace: "public", entries: [relation], relations: [relation] }, "relations").relations[0].name, "orders");
assert.equal(contracts.validatePlanResponse({ id: "plan", steps: [], warnings: [], destructive: false }).id, "plan");
const blockedPreview = { id: null, previewOnly: true, applyCapable: false, complete: false, steps: [], warnings: [], destructive: false, blockingDifferences: [{ code: "destructive_omitted", message: "Omitted type change", nextAction: "Enable destructive changes." }] };
assert.equal(contracts.validatePlanResponse(blockedPreview).blockingDifferences[0].code, "destructive_omitted");
assert.equal(contracts.validatePlanResponse({ ...blockedPreview, applyCapable: true, applyPlanId: "plan" }).applyPlanId, "plan");
assert.equal(contracts.validateOperationResponse({ operation: { id: "operation", state: "running" } }).operation.state, "running");
assert.equal(contracts.validateResourceSummariesResponse({ resources: [{ id: "schema" }] }).resources.length, 1);
assert.equal(contracts.validateResourceSummariesResponse({ summaries: [{ id: "dashboard" }] }).summaries.length, 1);
assert.equal(contracts.validateDashboardSummariesResponse({ summaries: [{ id: "dashboard", title: "Demo", revision: 1, archived: false }], page }).page.returned, 1);
assert.equal(contracts.validateSchemaRecord({ id: "schema", revision: 1, layoutToken: "a".repeat(64), schema: {} }).id, "schema");
assert.equal(contracts.validateQueryResultResponse({ ...execution, columns: [], rows: [], sql: "SELECT 1", parameters: [], resultResource: aggregateResultResource }).resultResource.kind, "aggregate");
assert.equal(contracts.validateQueryResultResponse({ ...execution, columns: [], rows: [], sql: "SELECT 1", parameters: [], truncated: false }).resultResource, undefined);
assert.equal(contracts.validateDetailResultResponse({ ...execution, columns: [], rows: [], sql: "SELECT 1", parameters: [], matchingRowCount: 0, offset: 0, nextOffset: 0, hasMore: false, resultResource }).nextOffset, 0);
assert.equal(contracts.validateDeleteResponse({ deleted: "schema" }).deleted, "schema");
assert.equal(contracts.validateShutdownResponse({ shuttingDown: true }).shuttingDown, true);
assert.equal(contracts.validateLegacySourcePreviewResponse(legacyPreview).compatibleWidgetIds[0], "compatible");
const legacyApply = {
  dashboardId: "dashboard", previousRevision: 2, revision: 3,
  upgradedWidgetIds: ["compatible"], incompatibleWidgetIds: ["incompatible"],
  postWriteVerification: { status: "current", changedWidgetIds: [], unavailableWidgetIds: [] },
};
assert.equal(contracts.validateLegacySourceApplyResponse(legacyApply).revision, 3);

for (const [validator, payload] of [
  [contracts.validateSessionResponse, { token: "" }],
  [contracts.validateSessionResponse, { token: "token" }],
  [contracts.validateProfilesResponse, { profiles: null }],
  [value => contracts.validateCatalogResponse(value, "relations"), { relations: [{ kind: "table" }] }],
  [contracts.validatePlanResponse, { id: "plan", steps: [] }],
  [contracts.validatePlanResponse, { ...blockedPreview, previewOnly: false }],
  [contracts.validatePlanResponse, { ...blockedPreview, applyCapable: true }],
  [contracts.validateOperationResponse, { operation: { id: "operation" } }],
  [contracts.validateResourceSummariesResponse, { resources: [{}] }],
  [contracts.validateSchemaRecord, { id: "schema", revision: 1, layoutToken: "not-a-layout-token", schema: {} }],
  [contracts.validateDashboardSummariesResponse, { summaries: [{ id: "dashboard", title: "Demo", revision: 1, archived: false }], page: { ...page, nextCursor: 1 } }],
  [contracts.validateDetailResultResponse, { ...execution, columns: [], rows: [], sql: "SELECT 1", parameters: [], matchingRowCount: 1, offset: 0, hasMore: true, resultResource }],
  [contracts.validateDetailResultResponse, { ...execution, columns: [], rows: [], sql: "SELECT 1", parameters: [], matchingRowCount: 0, offset: 0, nextOffset: 0, hasMore: false, resultResource: { ...resultResource, binding: "" } }],
  [contracts.validateQueryResultResponse, { ...execution, columns: [], rows: [], sql: "SELECT 1", parameters: [], resultResource: { ...aggregateResultResource, page: { ...aggregateResultResource.page, nextCursor: 1 } } }],
  [contracts.validateQueryResultResponse, { ...execution, columns: [], rows: [], sql: "SELECT 1", parameters: [], truncated: true }],
  [contracts.validateDeleteResponse, { deleted: "" }],
  [contracts.validateShutdownResponse, { shuttingDown: false }],
  [contracts.validateLegacySourcePreviewResponse, { ...legacyPreview, deferredWidgetIds: ["compatible"] }],
  [contracts.validateLegacySourcePreviewResponse, { ...legacyPreview, compatibleWidgetIds: ["incompatible"], incompatibleWidgetIds: ["compatible"] }],
  [contracts.validateLegacySourcePreviewResponse, { ...legacyPreview, widgetIds: ["compatible", "compatible"] }],
  [contracts.validateLegacySourcePreviewResponse, { ...legacyPreview, results: [{ ...legacyPreview.results[0], currentFingerprint: "invalid" }, legacyPreview.results[1]] }],
  [contracts.validateLegacySourcePreviewResponse, { ...legacyPreview, results: [{ ...legacyPreview.results[0], profileFingerprint: "invalid" }, legacyPreview.results[1]] }],
  [contracts.validateLegacySourcePreviewResponse, { ...legacyPreview, results: [{ ...legacyPreview.results[0], currentLegacyFingerprint: "c".repeat(64) }, legacyPreview.results[1]] }],
  [contracts.validateLegacySourcePreviewResponse, { ...legacyPreview, digest: "x".repeat(legacyPreview.maximumDigestLength + 1) }],
  [contracts.validateLegacySourceApplyResponse, { ...legacyApply, incompatibleWidgetIds: ["compatible"] }],
  [contracts.validateLegacySourceApplyResponse, { ...legacyApply, postWriteVerification: { status: "changed", changedWidgetIds: [], unavailableWidgetIds: [] } }],
  [contracts.validateLegacySourceApplyResponse, { ...legacyApply, postWriteVerification: { status: "unavailable", changedWidgetIds: ["compatible"], unavailableWidgetIds: [] } }],
]) {
  assert.throws(() => validator(payload), error => error.code === "invalid_api_response");
}

const postgresPath = contracts.createApiPathPredicate("/api/postgres");
assert.equal(postgresPath("/api/postgres/profiles"), true);
assert.equal(postgresPath("/api/postgres/profiles?active=true"), true);
assert.equal(postgresPath("/api/postgresql/profiles"), false);
assert.equal(postgresPath("/api/postgres-evil/profiles"), false);
assert.equal(postgresPath("https://example.com/api/postgres/profiles"), false);

assert.equal(contracts.postgresResponseValidator("/api/postgres/profiles"), contracts.validateProfilesResponse);
assert.equal(typeof contracts.postgresResponseValidator("/api/postgres/profiles/id/relations?namespace=public"), "function");
assert.equal(typeof contracts.postgresResponseValidator("/api/postgres/profiles/id/structured-results/result", "GET"), "function");
assert.equal(contracts.postgresResponseValidator("/api/postgres/profiles/id/structured-results/result", "DELETE"), null, "structured-result DELETE has its own terminal response contract");
assert.equal(contracts.postgresResponseValidator("/api/postgres/profiles-extra"), null);

console.log("Shared API contract tests passed");
