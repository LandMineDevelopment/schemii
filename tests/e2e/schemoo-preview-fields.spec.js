import { expect, test } from "@playwright/test";
import { importedDraft } from "../../src/schemii/schemoo/web/model-draft.js";
import { splitDraft } from "../../src/schemii/schemoo/web/model-state.js";
import { chooseModelOption } from "./helpers/schemoo-select.js";

let modelId;
test.beforeEach(async ({ request }) => {
  const { connections } = await (await request.get("/api/v1/connections")).json();
  const connection = connections.find(item => item.database === "organization");
  expect(connection).toBeTruthy();
  const catalog = await (await request.get(`/api/v1/schemoo/catalog?connection_id=${connection.id}&namespace=public`)).json();
  const draft = importedDraft(catalog);
  draft.root = "personnel_dim";
  draft.edges.forEach(edge => { edge.enabled = false; });
  draft.fields = [];
  const response = await request.post("/api/v1/schemoo/models", { data: {
    name: `E2E preview exposure ${Date.now()}`, connectionId: connection.id, namespace: "public",
    catalogFingerprint: catalog.fingerprint, ...splitDraft(draft),
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

async function addOutput(page, column, aggregate = "Plain field") {
  await chooseModelOption(page, "Preview source field", `personnel_dim · ${column}`);
  await chooseModelOption(page, "Preview aggregation", aggregate);
  await page.getByRole("button", { name: "Add preview output", exact: true }).click();
}

test("ordered preview measures are independent of exposure and persist without a model revision", async ({ page, request }, testInfo) => {
  test.setTimeout(90_000);
  const before = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  await page.goto(`/schemoo?model=${modelId}`);
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  await expect(page.locator("#fields .preview-output")).toHaveCount(0);
  await addOutput(page, "name", "Count");
  await addOutput(page, "name", "Count distinct");
  await expect(page.getByRole("button", { name: "Add preview output", exact: true })).toBeDisabled();
  await addOutput(page, "pay_band_class_id");
  await page.getByRole("button", { name: "Move preview column 3 earlier", exact: true }).click();
  await page.getByRole("button", { name: "Move preview column 2 earlier", exact: true }).click();
  await expect(page.locator("#fields .preview-output .field-name")).toHaveText([
    "personnel_dim · pay_band_class_id", "personnel_dim · name", "personnel_dim · name",
  ]);
  await expect(page.getByRole("button", { name: "Run preview", exact: true })).toBeEnabled();
  await page.getByRole("combobox", { name: "Preview source field", exact: true }).scrollIntoViewIfNeeded();
  await page.mouse.move(0, 0);
  await page.screenshot({ path: `artifacts/schemoo-preview-fields-${testInfo.project.name}.png` });
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  const saved = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  expect(saved.revision).toBe(before.revision);
  expect(saved.definition).toEqual(before.definition);
  expect(saved.explore.fields.map(field => field.aggregate)).toEqual(["none", "count", "count_distinct"]);
  await page.reload();
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  await expect(page.locator("#fields .preview-output")).toHaveCount(3);
  await page.getByRole("button", { name: "Run preview", exact: true }).click();
  await expect(page.locator("#result-status")).toContainText("rows", { timeout: 30_000 });
  await expect(page.locator("#error")).toBeHidden();
  const headers = await page.locator("#results thead th").allTextContents();
  expect(headers.join(" ")).toContain("pay_band_class_id");
  expect(headers.join(" ")).toContain("count");
  expect(headers.findIndex(value => value.includes("pay_band_class_id"))).toBeLessThan(headers.findIndex(value => value.includes("count")));
  await page.screenshot({ path: `artifacts/schemoo-preview-measures-${testInfo.project.name}.png` });
});

test("exposure toggles constrain preview options without adding outputs and aliases inherit exposure", async ({ page, request }) => {
  await page.goto(`/schemoo?model=${modelId}`);
  if (await page.locator("#inspector").isVisible()) await page.getByRole("button", { name: "Close inspector", exact: true }).click();
  await page.locator('[data-node-id="personnel_dim"] .sc-node-header').click();
  const name = page.locator("#table-inspector").getByRole("checkbox", { name: "Expose personnel_dim.name", exact: true });
  await name.uncheck();
  await expect(page.locator('[data-node-id="personnel_dim"] input[aria-label="Expose personnel_dim.name"]')).not.toBeChecked();
  await page.getByRole("button", { name: "Create alias", exact: true }).click();
  await page.getByRole("textbox", { name: "Alias name for personnel_dim", exact: true }).fill("Preview people alias");
  await page.getByRole("button", { name: "Add alias to model", exact: true }).click();
  await expect(page.locator("#table-inspector").getByRole("checkbox", { name: "Expose Preview people alias.name", exact: true })).not.toBeChecked();
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  await expect(page.locator("#fields .preview-output")).toHaveCount(0);
  const source = page.getByRole("combobox", { name: "Preview source field", exact: true });
  await source.click();
  await source.fill("personnel_dim · name");
  const list = page.locator(`#${await source.getAttribute("aria-controls")}`);
  await expect(list.getByRole("option", { name: "personnel_dim · name", exact: true })).toHaveCount(0);
  await source.press("Escape");
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  const saved = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  const alias = saved.definition.nodes.find(node => node.label === "Preview people alias");
  expect(saved.explore.fields).toEqual([]);
  expect(saved.definition.exposedFields.some(field => field.table === alias.id && field.column === "name")).toBe(false);
  expect(saved.definition.exposedFields.some(field => field.table === alias.id && field.column === "id")).toBe(true);
});
