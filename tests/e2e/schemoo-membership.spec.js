import { findOrganizationConnection } from "./helpers/database-fixtures.js";
import { expect, test } from "@playwright/test";
import { chooseModelOption, expectDropdownWithinViewport } from "./helpers/schemoo-select.js";
import { importedDraft } from "../../src/schemii/schemoo/web/model-draft.js";
import { splitDraft } from "../../src/schemii/schemoo/web/model-state.js";

let modelId;
test.beforeEach(async ({ request }) => {
  const connection = findOrganizationConnection((await (await request.get("/api/v1/connections")).json()).connections);
  expect(connection).toBeTruthy();
  const catalog = await (await request.get(`/api/v1/schemoo/catalog?connection_id=${connection.id}&namespace=public`)).json();
  const draft = importedDraft(catalog);
  draft.edges.forEach(e => { e.enabled = false; });
  draft.root = "slate_fact"; draft.fields = [{ table: "slate_fact", column: "slate_type" }]; draft.scopes = [];
  const created = await request.post("/api/v1/schemoo/models", { data: { name: `E2E membership ${Date.now()}`, connectionId: connection.id, namespace: "public", catalogFingerprint: catalog.fingerprint, ...splitDraft(draft) } });
  expect(created.ok(), await created.text()).toBeTruthy(); modelId = (await created.json()).id;
});
test.afterEach(async ({ request }) => {
  if (!modelId) return;
  const response = await request.get(`/api/v1/schemoo/models/${modelId}`);
  if (response.ok()) await request.delete(`/api/v1/schemoo/models/${modelId}?expected_revision=${(await response.json()).revision}`);
  modelId = null;
});

async function selectTwo(page, label, screenshot) {
  const input = page.getByRole("combobox", { name: label, exact: typeof label === "string" });
  await input.click();
  const list = page.locator(`#${await input.getAttribute("aria-controls")}`);
  await expect(list.getByRole("option").nth(1)).toBeVisible({ timeout: 30000 });
  const values = await list.getByRole("option").locator("strong").allTextContents();
  await list.getByRole("option").nth(0).click();
  await list.getByRole("option").nth(1).click();
  await expect(list.locator('[aria-selected="true"]')).toHaveCount(2);
  await expectDropdownWithinViewport(input);
  if (screenshot) await page.screenshot({ path: `artifacts/${screenshot}-${test.info().project.name}.png` });
  await input.press("Escape");
  return values.slice(0, 2);
}

async function save(page) {
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
}

async function previewValues(page) {
  await page.getByRole("button", { name: "Run preview", exact: true }).click();
  await expect(page.locator("#results table")).toBeVisible({ timeout: 30000 });
  return page.locator("#results table tbody tr td:last-child").allTextContents();
}

test("fixed IN uses its own source dropdown, persists and constrains preview", async ({ page, request }) => {
  await page.goto(`/schemoo?model=${modelId}`);
  await page.getByRole("button", { name: "Model filters", exact: true }).click();
  await page.getByRole("button", { name: "Add model filter scope", exact: true }).click();
  await page.getByRole("button", { name: /^Fixed rule/ }).click();
  await chooseModelOption(page, "Model filter / Default condition 1 field", "slate_fact · slate_type");
  await chooseModelOption(page, "Model filter / Default condition 1 operator", "Is one of (IN)");
  await page.getByRole("checkbox", { name: "Model filter / Default condition 1 choose fixed value from domain", exact: true }).check();
  await expect(page.getByRole("combobox", { name: /condition 1 domain value column$/ })).toHaveCount(0);
  const selected = await selectTwo(page, "Model filter / Default condition 1 value", "membership-fixed-domain");
  await page.getByRole("button", { name: "Apply to model", exact: true }).click();
  await save(page);
  const model = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  expect(model.definition.scopes[0].alternatives[0].conditions[0].value).toEqual(selected);
  await page.reload();
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  await expect(page.locator("#sql")).toContainText(" IN (");
  const values = await previewValues(page);
  expect(values.length).toBeGreaterThan(0);
  expect(values.every(v => selected.includes(v.trim()))).toBe(true);
});

test("report NOT IN has the same multi-value domain interaction", async ({ page }) => {
  await page.goto(`/schemoo?model=${modelId}`);
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  await page.getByRole("button", { name: "Add report filter group", exact: true }).click();
  await chooseModelOption(page, "Report filter 1 condition 1 field", "slate_fact · slate_type");
  await chooseModelOption(page, "Report filter 1 condition 1 operator", "Is not one of (NOT IN)");
  await page.getByRole("checkbox", { name: "Report filter 1 condition 1 choose fixed value from domain", exact: true }).check();
  const excluded = await selectTwo(page, "Report filter 1 condition 1 value", "membership-report-domain");
  await expect(page.locator("#sql")).toContainText(" NOT IN (");
  const values = await previewValues(page);
  expect(values.every(v => !excluded.includes(v.trim()))).toBe(true);
});

test("IN parameter defaults and Explore input accept multiple domain values", async ({ page, request }) => {
  await page.goto(`/schemoo?model=${modelId}`);
  await page.getByRole("button", { name: "Model filters", exact: true }).click();
  await page.getByRole("button", { name: "Add model filter scope", exact: true }).click();
  await page.getByRole("button", { name: /^Report parameter/ }).click();
  await chooseModelOption(page, "Model filter / Default condition 1 field", "slate_fact · slate_type");
  await page.getByRole("checkbox", { name: "Allow multiple values for Parameter", exact: true }).check();
  await expect(page.getByRole("combobox", { name: "Model filter / Default condition 1 operator", exact: true })).toHaveValue("Is one of (IN)");
  await page.getByRole("button", { name: "Bind source to Parameter", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Model filter / Default condition 2 operator", exact: true })).toHaveValue("Is one of (IN)");
  await page.getByRole("button", { name: "Remove Model filter / Default condition 2", exact: true }).click();
  await chooseModelOption(page, /^Model filter \/ Default input .+ type$/, "Choose from source");
  const defaults = await selectTwo(page, /^Model filter \/ Default input .+ default$/, "membership-parameter-defaults");
  await page.getByRole("button", { name: "Apply to model", exact: true }).click();
  await save(page);
  const model = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  expect(model.definition.scopes[0].alternatives[0].inputs[0].defaultValue).toEqual(defaults);
  await page.reload();
  await page.getByRole("button", { name: "Model filters", exact: true }).click();
  await page.getByRole("button", { name: "Edit filter Model filter", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "Allow multiple values for Parameter", exact: true })).toBeChecked();
  await page.getByRole("checkbox", { name: "Allow multiple values for Parameter", exact: true }).uncheck();
  await expect(page.getByRole("combobox", { name: "Model filter / Default condition 1 operator", exact: true })).toHaveValue("Equals");
  await expect(page.getByRole("combobox", { name: /^Model filter \/ Default input .+ default$/ })).toHaveValue("");
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  const input = page.getByRole("combobox", { name: "Model filter: Parameter", exact: true });
  await input.click();
  const list = page.locator(`#${await input.getAttribute("aria-controls")}`);
  await expect(list.locator('[aria-selected="true"]')).toHaveCount(2, { timeout: 30000 });
  await list.getByRole("option").filter({ has: page.locator('[aria-hidden="true"]') }).first().click();
  await expect(list.locator('[aria-selected="true"]')).toHaveCount(1);
  const remaining = await list.locator('[aria-selected="true"] strong').innerText();
  await page.screenshot({ path: `artifacts/membership-explore-${test.info().project.name}.png` });
  await input.press("Escape");
  const values = await previewValues(page);
  expect(values.every(v => v.trim() === remaining)).toBe(true);
});
