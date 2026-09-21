import { findOrganizationConnection } from "./helpers/database-fixtures.js";
import { expect, test } from "@playwright/test";
import { importedDraft } from "../../src/schemii/schemoo/web/model-draft.js";
import { splitDraft } from "../../src/schemii/schemoo/web/model-state.js";
import { chooseModelOption } from "./helpers/schemoo-select.js";
import { deleteModel } from "./helpers/schemoo-model.js";

let modelId, copiedModelId;
test.beforeEach(async ({ request }) => {
  const { connections } = await (await request.get("/api/v1/connections")).json();
  const connection = findOrganizationConnection(connections);
  expect(connection).toBeTruthy();
  const catalog = await (await request.get(`/api/v1/schemoo/catalog?connection_id=${connection.id}&namespace=public`)).json();
  const draft = importedDraft(catalog);
  // Persist a real layout so opening the model does not generate unsaved
  // positions and require a discard confirmation when switching to its copy.
  draft.nodes.forEach((node, index) => {
    node.x = 50 + (index % 4) * 430;
    node.y = 50 + Math.floor(index / 4) * 650;
  });
  draft.root = "personnel_dim"; draft.edges.forEach(edge => { edge.enabled = false; });
  draft.fields = [{ table: "personnel_dim", column: "name", aggregate: "none" }];
  const response = await request.post("/api/v1/schemoo/models", { data: { name: `E2E saved previews ${Date.now()}`,
    connectionId: connection.id, namespace: "public", catalogFingerprint: catalog.fingerprint, ...splitDraft(draft) } });
  expect(response.ok(), await response.text()).toBeTruthy(); modelId = (await response.json()).id;
});
test.afterEach(async ({ request }) => { await deleteModel(request, copiedModelId); copiedModelId = null; await deleteModel(request, modelId); modelId = null; });

for (const failed of [false, true]) {
  test(`model library actions recover when preview loading ${failed ? "fails" : "finishes"}`, async ({ page, request }) => {
    const source = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
    let release;
    const paused = new Promise(resolve => { release = resolve; });
    await page.route(`**/api/v1/schemoo/models/${modelId}/previews`, async route => {
      await paused;
      await route.fulfill(failed
        ? { status: 503, json: { error: { message: "Preview list unavailable" } } }
        : { json: { previews: [] } });
    });
    try {
      await page.goto(`/schemoo?model=${modelId}`);
      await expect(page.locator("#workbench")).not.toHaveAttribute("inert", "");
      await page.getByRole("button", { name: "Open models", exact: true }).click();
      const duplicate = page.getByRole("button", { name: `Duplicate model ${source.name}`, exact: true });
      const remove = page.getByRole("button", { name: `Delete model ${source.name}`, exact: true });
      await expect(duplicate).toBeDisabled();
      await expect(remove).toBeDisabled();
      await page.getByRole("button", { name: "Refresh models", exact: true }).focus();
      release();
      await expect(duplicate).toBeEnabled();
      await expect(remove).toBeEnabled();
      await expect(page.getByRole("button", { name: "Refresh models", exact: true })).toBeFocused();
      await duplicate.click();
      const dialog = page.getByRole("dialog", { name: "Duplicate model", exact: true });
      await expect(dialog).toBeVisible();
      await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    } finally {
      release();
      await page.unrouteAll({ behavior: "wait" });
    }
  });
}

test("duplicate a saved model with independent layout, rules and named previews", async ({ page, request }, info) => {
  const url = `/api/v1/schemoo/models/${modelId}`;
  const source = await (await request.get(url)).json();
  const preview = await request.post(`${url}/previews`, { data: { name: "Names", explore: source.explore } });
  expect(preview.ok(), await preview.text()).toBe(true);
  const sourcePreview = await preview.json();
  await page.goto(`/schemoo?model=${modelId}`);
  await page.getByRole("button", { name: "Open models", exact: true }).click();
  await page.getByRole("button", { name: `Duplicate model ${source.name}`, exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Duplicate model", exact: true });
  const name = dialog.getByRole("textbox", { name: "New model name", exact: true });
  await expect(name).toHaveValue(`${source.name} copy`);
  await expect(name).toBeFocused();
  await expect(dialog).toContainText("Save pending edits first");
  const box = await dialog.boundingBox(), viewport = page.viewportSize();
  expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
  expect(box.y).toBeGreaterThanOrEqual(0); expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
  await page.screenshot({ path: `artifacts/model-duplicate-${info.project.name}.png` });
  const response = page.waitForResponse(r => r.url().endsWith(`${modelId}/duplicate`) && r.request().method() === "POST");
  await name.fill("Independent test model");
  await name.press("Enter");
  const result = await response;
  expect(result.status(), await result.text()).toBe(201);
  const copied = await result.json(); copiedModelId = copied.id;
  expect(copied.id).not.toBe(modelId);
  expect(copied.definition).toEqual(source.definition);
  expect(copied.layout).toEqual(source.layout);
  expect(copied.explore).toEqual(source.explore);
  expect(copied.connectionId).toBe(source.connectionId);
  await expect(page).toHaveURL(new RegExp(copied.id));
  const previews = (await (await request.get(`/api/v1/schemoo/models/${copied.id}/previews`)).json()).previews;
  expect(previews).toHaveLength(1);
  expect(previews[0].id).not.toBe(sourcePreview.id);
  expect(previews[0].modelId).toBe(copied.id);
  expect(previews[0].explore).toEqual(sourcePreview.explore);
  await page.locator("#model-name").fill("Edited independent model");
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  expect(await (await request.get(url)).json()).toEqual(source);
  expect((await (await request.get(`${url}/previews`)).json()).previews).toEqual([sourcePreview]);
  await deleteModel(request, copiedModelId); copiedModelId = null;
  expect((await request.get(url)).ok()).toBe(true);
});

async function saveAs(page, name) {
  await page.getByRole("button", { name: "Save preview as", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Save preview as", exact: true });
  await dialog.getByRole("textbox", { name: "Preview name", exact: true }).fill(name);
  await dialog.getByRole("button", { name: "Save preview", exact: true }).click();
  await expect(dialog).toHaveCount(0);
}

test("named previews persist, switch independent query choices, and delete with confirmation", async ({ page, request }, info) => {
  const url = `/api/v1/schemoo/models/${modelId}`;
  const before = await (await request.get(url)).json();
  await page.goto(`/schemoo?model=${modelId}`);
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  await expect(page.getByRole("button", { name: "Save preview as", exact: true })).toBeEnabled();
  await saveAs(page, "Names");
  await chooseModelOption(page, "Preview column 1 aggregation", "Count");
  await saveAs(page, "Headcount");
  await chooseModelOption(page, "Saved preview", "Names");
  await expect(page.getByRole("combobox", { name: "Preview column 1 aggregation", exact: true })).toHaveValue("Plain field");
  await chooseModelOption(page, "Saved preview", "Headcount");
  await expect(page.getByRole("combobox", { name: "Preview column 1 aggregation", exact: true })).toHaveValue("Count");
  await chooseModelOption(page, "Preview column 1 aggregation", "Count distinct");
  await chooseModelOption(page, "Saved preview", "Names");
  const switching = page.getByRole("dialog", { name: "Switch preview?", exact: true });
  await switching.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Preview column 1 aggregation", exact: true })).toHaveValue("Count distinct");
  await chooseModelOption(page, "Saved preview", "Names");
  await switching.getByRole("button", { name: "Switch preview", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Preview column 1 aggregation", exact: true })).toHaveValue("Plain field");
  await chooseModelOption(page, "Saved preview", "Headcount");
  const records = (await (await request.get(url + "/previews")).json()).previews;
  expect(records.map(p => p.name)).toEqual(["Names", "Headcount"]);
  expect(records.map(p => p.explore.fields[0].aggregate)).toEqual(["none", "count"]);
  const after = await (await request.get(url)).json();
  expect(after).toEqual(before);
  await page.screenshot({ path: `artifacts/schemoo-saved-previews-${info.project.name}.png` });
  await page.getByRole("button", { name: "Run preview", exact: true }).click();
  await expect(page.locator("#result-status")).toContainText("row", { timeout: 30_000 });
  await expect(page.locator("#results thead")).toContainText("count");
  page.on("dialog", dialog => dialog.accept());
  await page.reload();
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  await chooseModelOption(page, "Saved preview", "Headcount");
  await expect(page.getByRole("combobox", { name: "Preview column 1 aggregation", exact: true })).toHaveValue("Count");
  await page.getByRole("button", { name: "Delete selected preview", exact: true }).click();
  const confirm = page.getByRole("dialog", { name: "Delete saved preview?", exact: true });
  await confirm.getByRole("button", { name: "Cancel", exact: true }).click();
  expect((await (await request.get(url + "/previews")).json()).previews).toHaveLength(2);
  await page.getByRole("button", { name: "Delete selected preview", exact: true }).click();
  await confirm.getByRole("button", { name: "Delete preview", exact: true }).click();
  await expect(confirm).toHaveCount(0);
  expect((await (await request.get(url + "/previews")).json()).previews).toHaveLength(1);
  await expect(page.getByRole("combobox", { name: "Preview column 1 aggregation", exact: true })).toHaveValue("Count");
});

test("removed output columns reconcile without dropping restrictions; stale saves keep local edits", async ({ page, request }) => {
  const url = `/api/v1/schemoo/models/${modelId}`;
  const model = await (await request.get(url)).json();
  const create = await request.post(url + "/previews", { data: { name: "Older source", explore: { ...model.explore,
    fields: [...model.explore.fields, { table: "personnel_dim", column: "removed_column", aggregate: "none" }],
    reportFilters: [{ id: "obsolete-rule", mode: "rows", conditions: [{ table: "personnel_dim", column: "removed_column", operator: "not_null" }] }] } } });
  expect(create.ok(), await create.text()).toBeTruthy(); const preset = await create.json();
  await page.goto(`/schemoo?model=${modelId}`);
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  await chooseModelOption(page, "Saved preview", "Older source");
  await expect(page.locator("#fields .preview-output")).toHaveCount(1);
  await expect(page.getByRole("region", { name: "Saved previews", exact: true })).toContainText("1 unavailable output removed");
  await expect(page.locator("#report-filters")).toContainText("removed_column");
  await expect(page.getByRole("combobox", { name: "Report filter 1 condition 1 field", exact: true })).toHaveValue("personnel_dim · removed_column (unavailable)");
  const update = await request.put(url + `/previews/${preset.id}`, { data: { expectedRevision: preset.revision, name: "Changed in another tab", explore: model.explore } });
  expect(update.ok(), await update.text()).toBeTruthy();
  await page.getByRole("button", { name: "Save changes to selected preview", exact: true }).click();
  await expect(page.getByRole("region", { name: "Saved previews", exact: true })).toContainText("changed in another request");
  await expect(page.locator("#fields .preview-output")).toHaveCount(1);
  expect((await (await request.get(url + "/previews")).json()).previews[0].name).toBe("Changed in another tab");
});
