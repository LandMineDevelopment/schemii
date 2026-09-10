import { expect, test } from "@playwright/test";
import { chooseModelOption } from "./helpers/schemoo-select.js";

let modelId;
test.beforeEach(async ({ request }) => {
  const { connections } = await (await request.get("/api/v1/connections")).json();
  const connection = connections.find(item => item.database === "organization");
  expect(connection, "Organization connection is required for the column binding fixture").toBeTruthy();
  const catalog = await (await request.get(`/api/v1/schemoo/catalog?connection_id=${connection.id}&namespace=public`)).json();
  const relation = catalog.relationships.find(item => item.sourceTable === "personnel_certification_fact" && item.targetTable === "personnel_dim");
  const response = await request.post("/api/v1/schemoo/models", { data: {
    name: `E2E column filters ${Date.now()}`, connectionId: connection.id, namespace: "public",
    definition: {
      root: "personnel_dim", exposedFields: null,
      nodes: [
        { id: "personnel_dim", table: "personnel_dim", label: "Personnel" },
        { id: "certs", table: "personnel_certification_fact", label: "Certifications" },
        { id: "history", table: "personnel_certification_fact", label: "Certification history" },
        { id: "summary", table: "personnel_certification_fact", label: "Certification summary", derivation: {
          kind: "aggregate", source: "certs", groupBy: ["personnel_id"],
          connection: { target: "personnel_dim", columns: [{ source: "personnel_id", target: "id" }] },
          outputs: [{ id: "count", label: "Count", operation: "count_distinct", nodeId: "certs", column: "certification_id" }],
        } },
      ],
      edges: ["certs", "history"].map(source => ({ id: `${source}_person`, relationshipId: relation.id, source, target: "personnel_dim", enabled: true })),
      scopes: [{ id: "asof", label: "Certification date", kind: "conditional", alternatives: [
        { id: "date", label: "As of date", inputs: [{ id: "asof", label: "As of", type: "date", defaultValue: "2026-01-01" }], conditions: [
          { table: "certs", column: "effective_date", operator: "lte", parameterId: "asof" },
          { table: "certs", column: "certification_id", operator: "not_null" },
        ] },
        { id: "all", label: "All history", inputs: [], conditions: [] },
      ] }],
    },
    layout: { positions: [{ id: "personnel_dim", x: 40, y: 40 }, { id: "certs", x: 380, y: 40 }, { id: "history", x: 740, y: 40 }, { id: "summary", x: 40, y: 450 }] },
    explore: { root: "personnel_dim", fields: [{ table: "personnel_dim", column: "name", aggregate: "none" }], limit: 100 },
  } });
  expect(response.ok(), await response.text()).toBeTruthy();
  modelId = (await response.json()).id;
});

test.afterEach(async ({ request }) => {
  if (!modelId) return;
  const response = await request.get(`/api/v1/schemoo/models/${modelId}`);
  if (response.ok()) await request.delete(`/api/v1/schemoo/models/${modelId}?expected_revision=${(await response.json()).revision}`);
  modelId = null;
});

async function inspect(page, id) {
  if (await page.locator("#inspector").isVisible()) await page.getByRole("button", { name: "Close inspector", exact: true }).click();
  if (await page.locator("#table-inspector").isVisible()) await page.getByRole("button", { name: "Close table inspector", exact: true }).click();
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
  await page.locator(`[data-node-id="${id}"] .sc-node-header`).click();
}

test("column bindings show only exact column predicates and survive save without changing exposure", async ({ page, request }, testInfo) => {
  const errors = []; page.on("pageerror", error => errors.push(error.message));
  await page.goto(`/schemoo?model=${modelId}`);
  await inspect(page, "certs");
  const canvasColumn = (id, label) => page.locator(`[data-node-id="${id}"] .sc-column`).filter({ has: page.getByRole("checkbox", { name: `Expose ${label}`, exact: true }) });
  await expect(canvasColumn("certs", "Certifications.effective_date").locator(".sc-column-filter")).toHaveAttribute("title", "Model filters: Certification date");
  const markerIcon = canvasColumn("certs", "Certifications.effective_date").locator(".sc-column-filter svg");
  await expect(markerIcon).toHaveCSS("fill", "none");
  await expect(markerIcon).not.toHaveCSS("stroke", "none");
  await expect(markerIcon).toHaveCSS("stroke", "rgb(101, 169, 255)");
  const nameStarts = await page.locator('[data-node-id="certs"] .sc-column-name').evaluateAll(names => names.map(name => name.getBoundingClientRect().left));
  expect(Math.max(...nameStarts) - Math.min(...nameStarts)).toBeLessThan(1);
  await expect(page.locator('[data-node-id="history"] .sc-column-filter')).toHaveCount(0);
  await expect(canvasColumn("certs", "Certifications.expiration_date").locator(".sc-column-filter")).toHaveCount(0);
  const table = page.locator("#table-inspector");
  const before = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  const plus = table.getByRole("button", { name: "Add filter to Certifications.expiration_date", exact: true });
  await expect(table.getByRole("button", { name: "Add filter for Certifications", exact: true })).toHaveCount(0);
  await plus.click();
  await expect(page.locator("dialog:visible")).toHaveCount(0);
  await expect(table).toBeVisible();
  await chooseModelOption(page, "Model filter for column", "Certification date");
  await expect(page.getByRole("combobox", { name: "Filter option for column", exact: true })).toHaveValue("As of date");
  await chooseModelOption(page, "Column binding condition 1 operator", "≥");
  await expect(page.getByRole("combobox", { name: "Column binding condition 1 value source", exact: true })).toHaveValue("As of");
  await page.locator(".column-filter-editor").scrollIntoViewIfNeeded();
  const editor = table.locator(".column-filter-editor");
  await expect(editor).not.toContainText("Source · column");
  await expect(editor).not.toContainText("Bound to parameter");
  await expect(editor).toContainText("Include NULL values");
  await expect(editor.locator(".mf-binding-summary")).toHaveCount(0);
  expect((await editor.boundingBox()).height).toBeLessThan(550);
  await page.screenshot({ path: `artifacts/schemoo-column-binding-editor-${testInfo.project.name}.png`, animations: "disabled" });
  await table.getByRole("button", { name: "Apply binding", exact: true }).click();
  await expect(canvasColumn("certs", "Certifications.expiration_date").locator(".sc-column-filter")).toHaveAttribute("title", "Model filters: Certification date");
  const expanded = table.getByRole("button", { name: "Show filters for Certifications.expiration_date", exact: true });
  await expect(expanded).toHaveAttribute("aria-expanded", "true");
  await expect(table.locator(".column-filter-predicate.matches-column:visible")).toHaveText("≥ [As of]");
  await expect(table.locator(".column-filter-details:visible")).not.toContainText("effective_date");
  await expect(table.locator(".column-filter-details:visible")).not.toContainText("certification_id");
  await expect(table.locator(".column-filter-details:visible")).not.toContainText("Certifications.");
  await expect(table.locator(".column-filter-details:visible")).not.toContainText("All history");
  await expect(table.locator(".column-filter-option:visible")).toHaveCount(1);
  await expect(table.locator(".column-filter-details:visible")).not.toContainText("applies when");
  const edit = table.getByRole("button", { name: "Edit binding Certification date for Certifications.expiration_date", exact: true });
  await edit.click();
  await expect(table.locator('.column-filter-editor[aria-label="Edit filter binding for expiration_date"]')).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Column binding condition 1 operator", exact: true })).toHaveValue("≥");
  await chooseModelOption(page, "Column binding condition 1 operator", ">");
  await table.getByRole("button", { name: "Apply edit", exact: true }).click();
  await expect(table.locator(".column-filter-predicate.matches-column:visible")).toHaveText("> [As of]");
  const exposure = table.getByRole("checkbox", { name: "Expose Certifications.expiration_date", exact: true });
  await expect(exposure).toBeChecked();
  await exposure.uncheck();
  await expect(expanded).toHaveAttribute("aria-expanded", "true");
  await exposure.check();
  await page.screenshot({ path: `artifacts/schemoo-column-filter-${testInfo.project.name}.png`, animations: "disabled" });
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  const saved = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  expect(saved.definition.scopes).toHaveLength(1);
  expect(saved.definition.scopes[0].alternatives[0].inputs).toEqual(before.definition.scopes[0].alternatives[0].inputs);
  expect(saved.definition.scopes[0].alternatives[0].conditions).toEqual([
    ...before.definition.scopes[0].alternatives[0].conditions,
    expect.objectContaining({ table: "certs", column: "expiration_date", operator: "gt", parameterId: "asof" }),
  ]);
  await page.reload();
  await inspect(page, "certs");
  await expanded.click();
  await expect(expanded).toHaveAttribute("aria-expanded", "true");
  await expect(table.locator(".column-filter-predicate.matches-column:visible")).toHaveText("> [As of]");
  await edit.click();
  page.once("dialog", dialog => dialog.dismiss());
  await table.getByRole("button", { name: "Delete column binding", exact: true }).click();
  await expect(table.locator(".column-filter-editor")).toBeVisible();
  page.once("dialog", dialog => dialog.accept());
  await table.getByRole("button", { name: "Delete column binding", exact: true }).click();
  await expect(table.locator(".column-filter-editor")).toHaveCount(0);
  await expect(canvasColumn("certs", "Certifications.expiration_date").locator(".sc-column-filter")).toHaveCount(0);
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  const deleted = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  expect(deleted.definition.scopes).toEqual(before.definition.scopes);
  expect(errors).toEqual([]);
});

test("deleting the last binding of an input removes only that unused input", async ({ page, request }) => {
  const before = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  await page.goto(`/schemoo?model=${modelId}`);
  await inspect(page, "certs");
  const table = page.locator("#table-inspector");
  await table.getByRole("button", { name: "Show filters for Certifications.effective_date", exact: true }).click();
  await table.getByRole("button", { name: "Edit binding Certification date for Certifications.effective_date", exact: true }).click();
  page.once("dialog", async dialog => {
    expect(dialog.message()).toContain("now-unused input");
    await dialog.accept();
  });
  await table.getByRole("button", { name: "Delete column binding", exact: true }).click();
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  const saved = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  expect(saved.definition.scopes[0].alternatives[0].inputs).toEqual([]);
  expect(saved.definition.scopes[0].alternatives[0].conditions).toEqual(before.definition.scopes[0].alternatives[0].conditions.filter(condition => condition.column !== "effective_date"));
});

test("inline cancel leaves rules unchanged, alias bindings stay separate and virtual outputs cannot accept source bindings", async ({ page, request }) => {
  await page.goto(`/schemoo?model=${modelId}`);
  await inspect(page, "history");
  const table = page.locator("#table-inspector");
  const chevron = table.getByRole("button", { name: "Show filters for Certification history.effective_date", exact: true });
  await chevron.click();
  await expect(table).not.toContainText("Certifications.effective_date");
  await table.getByRole("button", { name: "Add filter to Certification history.effective_date", exact: true }).click();
  await chooseModelOption(page, "Model filter for column", "Certification date");
  await chooseModelOption(page, "Column binding condition 1 operator", "≤");
  await table.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(chevron).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("button", { name: "Save model", exact: true })).toBeDisabled();
  const model = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  expect(model.definition.scopes[0].alternatives[0].conditions.some(condition => condition.table === "history")).toBe(false);
  await inspect(page, "summary");
  await expect(table.getByRole("button", { name: "Add filter to Certification summary.count", exact: true })).toBeDisabled();
  await expect(table).toContainText(/source|calculated/i);
});
