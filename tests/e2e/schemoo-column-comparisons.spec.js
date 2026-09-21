import { expect, test } from "@playwright/test";
import { findOrganizationConnection } from "./helpers/database-fixtures.js";
import { chooseModelOption } from "./helpers/schemoo-select.js";

const source = "pay_band_class_dim";
let modelId, dashboardId;

async function json(response) {
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}

test.afterEach(async ({ request }) => {
  if (dashboardId) {
    const response = await request.get(`/api/v1/schemer/dashboards/${dashboardId}`);
    if (response.ok()) await request.delete(`/api/v1/schemer/dashboards/${dashboardId}?expectedRevision=${(await response.json()).revision}`);
    dashboardId = null;
  }
  if (modelId) {
    const response = await request.get(`/api/v1/schemoo/models/${modelId}`);
    if (response.ok()) await request.delete(`/api/v1/schemoo/models/${modelId}?expected_revision=${(await response.json()).revision}`);
    modelId = null;
  }
});

test("author a column comparison, edit it inline, and apply it as a live dashboard slicer", async ({ page, request }) => {
  test.setTimeout(120_000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const connection = findOrganizationConnection((await json(await request.get("/api/v1/connections"))).connections);
  expect(connection, "Organization database fixture must be available").toBeTruthy();
  const fields = ["min_pay_range", "level"].map(column => ({ table: source, column, aggregate: "none" }));
  let model = await json(await request.post("/api/v1/schemoo/models", { data: {
    name: `E2E column comparison ${Date.now()}`, connectionId: connection.id, namespace: "public",
    definition: { root: source, nodes: [{ id: source, table: source, label: "Pay bands" }], edges: [], scopes: [], exposedFields: null },
    explore: { root: source, fields, limit: 100 },
  } }));
  modelId = model.id;
  // Derive expected results from real fixture rows, rather than hard-coding seed counts.
  const baseline = await json(await request.post("/api/v1/schemer/query", { data: {
    modelId, expectedRevision: model.revision, explore: { root: source, fields, limit: 100 },
  } }));
  expect(baseline.limitReached).toBe(false);
  expect(baseline.rows.length).toBeGreaterThan(0);
  const matching = baseline.rows.filter(([minimum, level]) => minimum !== null && level !== null && Number(minimum) <= Number(level)).length;
  expect(matching, "Fixture must distinguish the comparison from unrestricted rows").toBeLessThan(baseline.rows.length);

  await page.goto(`/schemoo?model=${modelId}`);
  await page.getByRole("button", { name: "Model filters", exact: true }).click();
  await page.getByRole("button", { name: "Add model filter scope", exact: true }).click();
  await page.getByRole("button", { name: /^Fixed rule/ }).click();
  await chooseModelOption(page, "Requirement for Model filter", "Optional");
  const prefix = "Model filter / Default condition 1";
  await chooseModelOption(page, `${prefix} field`, "Pay bands · min_pay_range");
  await chooseModelOption(page, `${prefix} operator`, "≤");
  await chooseModelOption(page, `${prefix} value source`, "Column");
  await chooseModelOption(page, `${prefix} comparison column`, "level");
  await expect(page.getByRole("textbox", { name: `${prefix} value`, exact: true })).toHaveCount(0);
  await expect(page.locator(".mf-dialog-summary")).toContainText("level");
  await page.locator("summary").filter({ hasText: "Offer alternative choices (optional)" }).click();
  await page.getByRole("button", { name: "Add alternative to Model filter", exact: true }).click();
  await page.getByRole("button", { name: "Apply to model", exact: true }).click();
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  model = await json(await request.get(`/api/v1/schemoo/models/${modelId}`));
  const scope = model.definition.scopes[0];
  expect(scope.requirement).toBe("optional");
  expect(scope.alternatives[0].conditions[0]).toMatchObject({ table: source, column: "min_pay_range", operator: "lte", compareColumn: "level" });
  expect(scope.alternatives[0].conditions[0].parameterId).toBeFalsy();
  expect(scope.alternatives[0].conditions[0].value).toBeNull();

  await page.reload();
  if (await page.locator("#inspector").isVisible()) await page.getByRole("button", { name: "Close inspector", exact: true }).click();
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
  await page.locator(`[data-node-id="${source}"] .sc-node-header`).click();
  const inspector = page.locator("#table-inspector");
  await inspector.getByRole("button", { name: "Show filters for Pay bands.min_pay_range", exact: true }).click();
  await inspector.getByRole("button", { name: "Edit binding Model filter for Pay bands.min_pay_range", exact: true }).click();
  const inline = "Column binding condition 1";
  await expect(page.getByRole("combobox", { name: `${inline} comparison column`, exact: true })).toHaveValue("level");
  await chooseModelOption(page, `${inline} value source`, "Literal value");
  await page.getByRole("textbox", { name: `${inline} value`, exact: true }).fill("123");
  await expect(page.getByRole("combobox", { name: `${inline} comparison column`, exact: true })).toHaveCount(0);
  await chooseModelOption(page, `${inline} value source`, "Column");
  await chooseModelOption(page, `${inline} comparison column`, "level");
  await inspector.getByRole("button", { name: "Apply edit", exact: true }).click();
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  model = await json(await request.get(`/api/v1/schemoo/models/${modelId}`));
  expect(model.definition.scopes[0].alternatives[0].conditions[0]).toMatchObject({ compareColumn: "level", value: null });

  const dashboard = await json(await request.post("/api/v1/schemer/dashboards", { data: {
    name: `E2E comparison dashboard ${Date.now()}`, modelId, modelRevision: model.revision,
    optionalFilters: [scope.id], selections: {},
    tiles: [{ id: "count", title: "Pay band count", kind: "kpi", measures: [{ table: source, column: "level", aggregate: "count" }], limit: 100 }],
  } }));
  dashboardId = dashboard.id;
  await page.goto(`/schemer?dashboard=${dashboardId}`);
  const count = page.locator(".kpi-value strong");
  await expect(count).toHaveText(String(baseline.rows.length), { timeout: 30_000 });
  await page.getByRole("checkbox", { name: "Activate Model filter", exact: true }).check();
  await page.getByRole("button", { name: "Apply filters", exact: true }).click();
  await expect(count).toHaveText(String(matching), { timeout: 30_000 });
  await chooseModelOption(page, "Alternative for Model filter", "Alternative");
  await page.getByRole("button", { name: "Apply filters", exact: true }).click();
  await expect(count).toHaveText(String(baseline.rows.length), { timeout: 30_000 });
  await chooseModelOption(page, "Alternative for Model filter", "Default");
  await page.getByRole("button", { name: "Apply filters", exact: true }).click();
  await expect(count).toHaveText(String(matching), { timeout: 30_000 });
  await page.reload();
  await expect(page.getByRole("checkbox", { name: "Activate Model filter", exact: true })).toBeChecked();
  await expect(count).toHaveText(String(matching), { timeout: 30_000 });
  await page.getByRole("checkbox", { name: "Activate Model filter", exact: true }).uncheck();
  await page.getByRole("button", { name: "Apply filters", exact: true }).click();
  await expect(count).toHaveText(String(baseline.rows.length), { timeout: 30_000 });
  expect(errors).toEqual([]);
});


test("date comparisons restrict compatible choices, clear operands on operator changes, and handle NULL", async ({ page, request }, testInfo) => {
  const connections = await json(await request.get("/api/v1/connections"));
  const connection = findOrganizationConnection(connections.connections);
  expect(connection).toBeTruthy();
  const model = await json(await request.post("/api/v1/schemoo/models", { data: {
    name: `E2E date comparison ${Date.now()}`, connectionId: connection.id, namespace: "public",
    definition: { root: "certs", nodes: [{ id: "certs", table: "personnel_certification_fact", label: "Certifications" }], edges: [], scopes: [] },
    explore: { root: "certs", fields: ["effective_date", "expiration_date"].map(column => ({ table: "certs", column })), limit: 100 },
  } }));
  modelId = model.id;
  const errors = []; page.on("pageerror", error => errors.push(error.message));
  await page.goto(`/schemoo?model=${modelId}`);
  await page.getByRole("button", { name: "Model filters", exact: true }).click();
  await page.getByRole("button", { name: "Add model filter scope", exact: true }).click();
  await page.getByRole("button", { name: /^Fixed rule/ }).click();
  const prefix = "Model filter / Default condition 1";
  await chooseModelOption(page, `${prefix} field`, "Certifications · expiration_date");
  await chooseModelOption(page, `${prefix} operator`, ">");
  await chooseModelOption(page, `${prefix} value source`, "Column");
  const second = page.getByRole("combobox", { name: `${prefix} comparison column`, exact: true });
  await second.click();
  const list = page.locator(`#${await second.getAttribute("aria-controls")}`);
  expect((await list.getByRole("option").allTextContents()).sort()).toEqual(["effective_date", "expiration_date"]);
  await list.getByRole("option", { name: "effective_date", exact: true }).click();
  await expect(page.locator(".mf-dialog-summary")).toContainText("Certifications.expiration_date > Certifications.effective_date");
  await page.screenshot({ path: testInfo.outputPath("date-comparison.png"), animations: "disabled" });

  // Operator transitions must remove the old column operand.
  await chooseModelOption(page, `${prefix} operator`, "Is one of (IN)");
  await expect(second).toHaveCount(0);
  await page.getByRole("textbox", { name: `${prefix} value`, exact: true }).fill("2025-01-01");
  await chooseModelOption(page, `${prefix} operator`, "Is null");
  await expect(page.getByRole("textbox", { name: `${prefix} value`, exact: true })).toHaveCount(0);
  await chooseModelOption(page, `${prefix} operator`, ">");
  await chooseModelOption(page, `${prefix} value source`, "Column");
  await chooseModelOption(page, `${prefix} comparison column`, "effective_date");
  await page.getByRole("button", { name: "Apply to model", exact: true }).click();
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  let saved = await json(await request.get(`/api/v1/schemoo/models/${modelId}`));
  const query = () => request.post("/api/v1/schemer/query", { data: {
    modelId, expectedRevision: saved.revision, explore: saved.explore,
  } }).then(json);
  const filtered = await query();
  expect(filtered.rows).toHaveLength(2);
  expect(filtered.rows.every(([start, end]) => end > start)).toBeTruthy();

  await page.getByRole("button", { name: "Edit filter Model filter", exact: true }).click();
  await page.getByRole("checkbox", { name: `${prefix} also accepts null`, exact: true }).check();
  await page.getByRole("button", { name: "Apply to model", exact: true }).click();
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  saved = await json(await request.get(`/api/v1/schemoo/models/${modelId}`));
  expect((await query()).rows).toHaveLength(3);
  await page.getByRole("button", { name: "Edit filter Model filter", exact: true }).click();
  await second.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("date-comparison-with-nulls.png"), animations: "disabled" });
  expect(errors).toEqual([]);
});
