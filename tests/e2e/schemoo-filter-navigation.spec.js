import { expect, test } from "@playwright/test";
import { chooseModelOption } from "./helpers/schemoo-select.js";

let modelId;
test.beforeEach(async ({ request }) => {
  const { connections } = await (await request.get("/api/v1/connections")).json();
  const connection = connections.find(item => item.database === "organization");
  expect(connection, "Organization connection is required for the filter navigation fixture").toBeTruthy();
  const catalog = await (await request.get(`/api/v1/schemoo/catalog?connection_id=${connection.id}&namespace=public`)).json();
  const source = "personnel_certification_fact";
  const relationship = catalog.relationships.find(item => item.sourceTable === source && item.targetTable === "personnel_dim");
  expect(relationship).toBeTruthy();
  const response = await request.post("/api/v1/schemoo/models", { data: {
    name: `E2E filter navigation ${Date.now()}`, connectionId: connection.id, namespace: "public",
    definition: {
      root: "personnel_dim", exposedFields: null,
      nodes: [
        { id: "personnel_dim", table: "personnel_dim", label: "Personnel" },
        { id: source, table: source, label: "Certifications" },
        { id: "summary", table: source, label: "Certification summary", derivation: {
          kind: "aggregate", source, groupBy: ["personnel_id"],
          connection: { target: "personnel_dim", columns: [{ source: "personnel_id", target: "id" }] },
          outputs: [{ id: "count", label: "Count", operation: "count_distinct", nodeId: source, column: "certification_id" }],
        } },
      ],
      edges: [{ id: relationship.id, relationshipId: relationship.id, source, target: "personnel_dim", enabled: true }],
      scopes: [{ id: "dated", label: "Dated certifications", kind: "conditional", alternatives: [{
        id: "default", label: "Default", inputs: [],
        conditions: [{ table: source, column: "effective_date", operator: "not_null" }],
      }] }],
    },
    layout: { positions: [{ id: "personnel_dim", x: 40, y: 40 }, { id: source, x: 380, y: 40 }, { id: "summary", x: 40, y: 450 }] },
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

async function inspectNode(page, id) {
  if (await page.locator("#inspector").isVisible()) await page.getByRole("button", { name: "Close inspector", exact: true }).click();
  if (await page.locator("#table-inspector").isVisible()) await page.getByRole("button", { name: "Close table inspector", exact: true }).click();
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
  await page.locator(`[data-node-id="${id}"] .sc-node-header`).click();
}

test("Filters is a dedicated authoring panel and keeps dialog cancel, save and reload behavior", async ({ page }, testInfo) => {
  const errors = []; page.on("pageerror", error => errors.push(error.message));
  await page.goto(`/schemoo?model=${modelId}`);
  await page.getByRole("button", { name: "Edit model", exact: true }).click();
  await expect(page.locator("#model-pane #model-filters")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Add model filter scope", exact: true })).toBeHidden();
  await page.getByRole("button", { name: "Model filters", exact: true }).click();
  await expect(page.locator("#show-filters")).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#filters-pane #model-filters")).toBeVisible();
  await expect(page.locator("#model-pane")).toBeHidden();
  await page.locator("#model-tab").click();
  await expect(page.locator("#show-filters")).toHaveAttribute("aria-pressed", "false");
  await page.locator("#filters-tab").click();
  await expect(page.locator("#show-filters")).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Add model filter scope", exact: true }).click();
  await page.getByRole("button", { name: /^Fixed rule/ }).click();
  await chooseModelOption(page, "Model filter / Default condition 1 field", "Personnel · name");
  await page.getByRole("textbox", { name: /^Scope .+ name$/ }).fill("Named personnel");
  await page.getByRole("button", { name: "Apply to model", exact: true }).click();
  await page.getByRole("button", { name: "Edit filter Named personnel", exact: true }).click();
  await page.getByRole("textbox", { name: /^Scope .+ name$/ }).fill("Cancelled name");
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByRole("button", { name: "Edit filter Named personnel", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  await page.reload();
  await page.getByRole("button", { name: "Model filters", exact: true }).click();
  await expect(page.locator("#model-filters")).toContainText("Named personnel");
  await expect(page.locator("#plan-status")).not.toContainText(/Loading|Checking/);
  await page.screenshot({ path: `artifacts/schemoo-filter-panel-${testInfo.project.name}.png`, animations: "disabled" });
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  await expect(page.locator("#show-filters")).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator("#explore-pane")).toBeVisible();
  await expect(page.locator("#parameter-values")).toContainText("Named personnel");
  expect(errors).toEqual([]);
});

test("table and summary expose applicable filters and scoped canvas highlighting", async ({ page }, testInfo) => {
  await page.goto(`/schemoo?model=${modelId}`);
  await inspectNode(page, "personnel_certification_fact");
  const inspector = page.locator("#table-inspector");
  await expect(inspector).toContainText("Filters affecting this object");
  await expect(inspector.getByRole("button", { name: "Add filter for Certifications", exact: true })).toHaveCount(0);
  await expect(inspector.getByRole("button", { name: "Add filter to Certifications.effective_date", exact: true })).toBeVisible();
  await inspectNode(page, "summary");
  await expect(inspector).toContainText("Dated certifications");
  await inspector.getByRole("button", { name: /Dated certifications/ }).click();
  await expect(page.getByRole("dialog", { name: "Edit model filter", exact: true })).toBeVisible();
  await expect(page.locator('[data-node-id="personnel_certification_fact"]')).toHaveClass(/sc-filter-bound/);
  await expect(page.locator('[data-node-id="personnel_dim"]')).not.toHaveClass(/sc-filter-bound/);
  await page.screenshot({ path: `artifacts/schemoo-filter-context-${testInfo.project.name}.png`, animations: "disabled" });
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.locator("#filters-pane")).toBeVisible();
  await expect(page.locator(".sc-filter-bound")).toHaveCount(0);
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  await expect(page.locator(".sc-filter-bound")).toHaveCount(0);
  await page.getByRole("button", { name: "Model filters", exact: true }).click();
  await page.getByRole("button", { name: "Close inspector", exact: true }).click();
  await expect(page.locator("#show-filters")).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator(".sc-filter-bound")).toHaveCount(0);
});

test("warning indicator distinguishes a broken binding from a missing preview parameter value", async ({ page, request }) => {
  let model = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  const scope = model.definition.scopes[0];
  scope.kind = "required";
  scope.alternatives[0].inputs = [{ id: "date", label: "As of date", type: "date", defaultValue: "" }];
  scope.alternatives[0].conditions[0].operator = "lte";
  scope.alternatives[0].conditions[0].parameterId = "date";
  let response = await request.put(`/api/v1/schemoo/models/${modelId}`, { data: { expectedRevision: model.revision, name: model.name, definition: model.definition } });
  expect(response.ok(), await response.text()).toBeTruthy();
  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.locator("#plan-status")).toContainText("requires As of date");
  await expect(page.locator("#show-filters")).not.toHaveClass(/has-warning/);
  model = await response.json();
  model.definition.scopes[0].alternatives[0].conditions[0].column = "removed_column";
  response = await request.put(`/api/v1/schemoo/models/${modelId}`, { data: { expectedRevision: model.revision, name: model.name, definition: model.definition } });
  expect(response.ok(), await response.text()).toBeTruthy();
  await page.reload();
  await expect(page.locator("#show-filters")).toHaveClass(/has-warning/);
  await page.getByRole("button", { name: "Model filters", exact: true }).click();
  await expect(page.locator("#model-filters")).toContainText(/valid source column|removed_column|attention/i);
});
