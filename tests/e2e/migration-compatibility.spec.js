import { expect, test } from "@playwright/test";

// Mounted acceptance cases for both configured desktop and Android projects.
// Reads an existing workspace; every plan/execution mutation below is intercepted.
const viewName = 'public."Quarterly totals"';
const candidate = { viewId: "view_compatibility_fixture", name: viewName, operation: "refresh", eligible: true, populatedBefore: false, populatedAfter: true, preservedIndexes: ["quarterly_totals_unique"], lock: "ACCESS EXCLUSIVE", cachedDataConsequence: "Existing cached rows are replaced by current query results.", blockingReasons: [] };
const compatibility = { editableComplete: false, operationSafe: true, evidenceVersion: 1, impact: [{ kind: "materialized_view", namespace: "public", name: "Quarterly totals" }], protectedScope: [{ kind: "table", namespace: "public", name: "partitioned_orders" }], unknownReasons: [], blockingReasons: [] };
async function mountCompatibility(page, request) {
  const response = await request.get("/api/v1/schemii/workspaces"); expect(response.ok()).toBe(true);
  const originalList = await response.json();
  const workspace = originalList.workspaces.find(item => item.connectionId && item.database && item.namespace);
  expect(workspace, "An existing database-backed workspace is required").toBeTruthy();
  const imported = { ...workspace, importSummary: { complete: false, catalogCapturedAt: "2026-11-11T12:00:00Z", catalogFingerprint: "a".repeat(64), importedObjects: {}, safetyEvidenceVersion: 1,
    migrationGuidance: "Only changes outside the proven protected dependency scope may be reviewed.", issues: [{ objectName: "public.partitioned_orders", featureCode: "partitioning", reason: "Partition structure is protected and omitted from the editable design.", dependencyComplete: false, unknownReasons: ["Partition dependency evidence is incomplete."], protectedScope: compatibility.protectedScope }] } };
  await page.route("**/api/v1/schemii/workspaces", route => route.fulfill({ json: { ...originalList, workspaces: originalList.workspaces.map(item => item.id === workspace.id ? imported : item) } }));
  await page.route(`**/api/v1/schemii/workspaces/${workspace.id}`, route => route.fulfill({ json: imported }));
  await page.route(`**/api/v1/schemii/workspaces/${workspace.id}/migration-executions?*`, route => route.fulfill({ json: { executions: [] } }));
  const plans = [], executions = []; let failNextPlan = false;
  await page.route(`**/api/v1/schemii/workspaces/${workspace.id}/migration-plans`, route => {
    const body = route.request().postDataJSON(); plans.push(body);
    if (failNextPlan) { failNextPlan = false; return route.fulfill({ status: 409, json: { error: { code: "catalog_changed", message: "The catalog changed. Review the source again." } } }); }
    const selected = body.refreshMaterializedViewIds.length > 0;
    return route.fulfill({ json: { id: `plan_compat_${plans.length}`, reviewDigest: `digest_${plans.length}`, status: selected && !body.allowDestructive ? "blocked" : "reviewable", applyCapable: !selected || body.allowDestructive, complete: true,
      designRevision: body.expectedDesignRevision, baselineRevision: 1, driftStatus: "none", expiresAt: "2027-01-01T00:00:00Z", destructive: selected, requiresExternalChangeAcknowledgement: false, requiresMaterializedMaintenanceAcknowledgement: selected,
      compatibility, materializedRefreshCandidates: [candidate, { ...candidate, viewId: "view_blocked_fixture", name: "public.protected_totals", eligible: false, blockingReasons: ["An inbound dependent cannot be safely replayed."] }], materializedMaintenance: selected ? [candidate] : [],
      blockingDifferences: [], warnings: [], externalChanges: [], conflicts: [], columnOrderRebuilds: [], columnTypeConversions: [], steps: selected ? [{ index: 1, operation: "refresh_materialized_view", objectPath: viewName, objectKind: "materialized_view", sql: `REFRESH MATERIALIZED VIEW ${viewName};`, destructive: true, requiresLock: true, dataMovement: true }] : [],
    } });
  });
  await page.route("**/api/v1/schemii/migration-plans/*/executions", route => { executions.push(route.request().postDataJSON()); return route.fulfill({ json: { id: "exec_fixture", status: "failed", completedStepCount: 0, errorCode: "catalog_changed", errorMessage: "The target changed before execution; PostgreSQL was not modified." } }); });
  await page.goto(`/?workspace=${encodeURIComponent(workspace.id)}`);
  await expect(page.locator("#review-migration-button")).toBeEnabled();
  return { workspace, plans, executions, failNextPlan: () => { failNextPlan = true; } };
}

test("import compatibility is visible before editing with honest protected-object and recovery guidance", async ({ page, request }) => {
  await mountCompatibility(page, request);
  const panel = page.locator("#workspace-compatibility"); await expect(panel).toBeVisible();
  await expect(panel).toContainText("This workspace has protected PostgreSQL features"); await expect(panel).toContainText("omitted features are not editable or reconstructed");
  await panel.getByText("known limits and recovery", { exact: false }).click();
  await expect(panel).toContainText("baseline cannot restore omitted objects"); await expect(panel).toContainText("Partition dependency evidence is incomplete");
  await panel.getByRole("searchbox", { name: "Find protected PostgreSQL features" }).fill("partitioned_orders");
  await expect(panel.locator("article")).toHaveCount(1);
  expect(await page.locator(".app-shell").evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
  expect(await page.locator(".main-layout").evaluate(node => node.getBoundingClientRect().height)).toBeGreaterThan(200);
  await panel.getByRole("button", { name: "Review migration safety" }).click();
  const dialog = page.locator("#migration-dialog"); await expect(dialog).toContainText("Current operation safety"); await expect(dialog).toContainText("Editable import: partial");
  await expect(dialog.getByRole("checkbox", { name: "Refresh public.protected_totals once", exact: true })).toBeDisabled();
  await expect(dialog).toContainText("An inbound dependent cannot be safely replayed");
});

test("one-time refresh has a fresh digest and separate acknowledgement, cancel and rollback clear intent", async ({ page, request }) => {
  const h = await mountCompatibility(page, request); await page.locator("#review-migration-button").click();
  const dialog = page.locator("#migration-dialog"), apply = dialog.getByRole("button", { name: "Apply migration", exact: true });
  await expect(apply).toBeDisabled(); await dialog.getByRole("checkbox", { name: `Refresh ${viewName} once`, exact: true }).check();
  await expect(dialog).toContainText("not populated now → populated after Apply"); await expect(dialog).toContainText("ACCESS EXCLUSIVE"); await expect(dialog).toContainText("quarterly_totals_unique");
  const ack = dialog.getByRole("checkbox", { name: "Acknowledge materialized-view maintenance for this plan", exact: true });
  await ack.check(); await expect(apply).toBeDisabled(); await dialog.getByRole("checkbox", { name: "Include destructive changes", exact: false }).check();
  await expect(ack).not.toBeChecked(); await ack.check(); await expect(apply).toBeEnabled();
  await apply.click(); await page.locator("#confirm-dialog").getByRole("button", { name: "Cancel", exact: true }).click(); expect(h.executions).toHaveLength(0);
  await apply.click(); await page.locator("#confirm-action").click();
  await expect.poll(() => h.executions.length).toBe(1); expect(h.executions[0]).toEqual({ reviewDigest: "digest_3", confirmDestructive: true, confirmExternalChanges: false, confirmMaterializedMaintenance: true });
  await expect(dialog.getByRole("checkbox", { name: `Refresh ${viewName} once`, exact: true })).not.toBeChecked(); await expect(apply).toBeDisabled();
  expect(h.plans.at(-1).refreshMaterializedViewIds).toEqual([]);
  await dialog.getByRole("button", { name: "Close", exact: true }).click(); await page.locator("#review-migration-button").click();
  await expect(dialog.getByRole("checkbox", { name: `Refresh ${viewName} once`, exact: true })).not.toBeChecked();
  expect(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
});

test("stale source removes maintenance intent and materialized authoring explains creation-only population", async ({ page, request }) => {
  const h = await mountCompatibility(page, request); await page.locator("#review-migration-button").click(); const dialog = page.locator("#migration-dialog");
  h.failNextPlan(); await dialog.getByRole("checkbox", { name: `Refresh ${viewName} once`, exact: true }).check();
  await expect(dialog).toContainText("The catalog changed"); await expect(dialog.getByRole("button", { name: "Apply migration", exact: true })).toBeDisabled();
  await dialog.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(dialog.getByRole("checkbox", { name: `Refresh ${viewName} once`, exact: true })).not.toBeChecked(); expect(h.plans.at(-1).refreshMaterializedViewIds).toEqual([]);
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  // Use the visible Views layer action, with no save or PostgreSQL write.
  await page.locator('[data-layer="views"]').first().click(); await page.locator("#create-view-button").click();
  const editor = page.locator("#design-view-dialog"); await editor.locator("#design-view-kind").selectOption("materialized_view");
  const guidance = editor.locator("#design-view-maintenance-guidance"); await expect(guidance).toBeVisible(); await expect(guidance).toContainText("does not refresh an existing cache"); await expect(guidance).toContainText("one-time materialized-view refresh");
  await expect(editor.getByRole("button", { name: "Create view", exact: true })).toBeVisible();
});
