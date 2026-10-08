import { expect, test } from "@playwright/test";
import { ensureAliasConnections } from "../../src/schemii/schemoo/web/alias-model.js";
import { chooseModelOption } from "./helpers/schemoo-select.js";
import { createOrganizationModel, deleteModel } from "./helpers/schemoo-model.js";

test.use({ viewport: { width: 1280, height: 800 } });

let modelId;
test.beforeEach(async ({ request }) => {
  modelId = await createOrganizationModel(request, "E2E canvas overlap");
});
test.afterEach(async ({ request }) => {
  await deleteModel(request, modelId);
  modelId = null;
});

async function cardGeometry(page, expectedCount) {
  let geometry;
  // Wait only for measurable input; overlap and repeated-Fit assertions stay immediate.
  await expect.poll(async () => {
    geometry = await page.locator(".sc-node").evaluateAll((cards, count) => {
      if (cards.length !== count) return null;
      const sample = [];
      for (const card of cards) {
        const rect = card.getBoundingClientRect();
        if (!card.isConnected || rect.width <= 0 || rect.height <= 0
            || ![rect.x, rect.y, rect.right, rect.bottom, rect.width, rect.height].every(Number.isFinite)) return null;
        sample.push({ id: card.dataset.nodeId, x: rect.x, y: rect.y, right: rect.right, bottom: rect.bottom });
      }
      return sample;
    }, expectedCount);
    return geometry !== null;
  }, { message: `Wait for ${expectedCount} connected, measurable canvas cards` }).toBe(true);
  return geometry;
}

function expectNoOverlaps(cards) {
  for (let i = 0; i < cards.length; i++) for (let j = i + 1; j < cards.length; j++) {
    const a = cards[i], b = cards[j];
    const overlap = a.x < b.right - 1 && b.x < a.right - 1 && a.y < b.bottom - 1 && b.y < a.bottom - 1;
    expect(overlap, `${a.id} covers ${b.id}`).toBe(false);
  }
}

test("Fit repairs saved overlap, keeps target clickable, and persists layout", async ({ page, request }) => {
  const saved = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  // Imported catalog fixtures omit positions for some nodes. Give the target
  // an explicit saved position so the alias starts at that exact same spot.
  const target = { id: "certification_dim", x: 440, y: 90 };
  const aliasId = "qa_overlap_alias";
  const definition = { ...saved.definition, nodes: [...saved.definition.nodes,
    { id: aliasId, table: "certification_dim", label: "QA overlap alias" }] };
  const catalogResponse = await request.get(`/api/v1/schemoo/catalog?connection_id=${saved.connectionId}&namespace=${saved.namespace}`);
  expect(catalogResponse.ok(), await catalogResponse.text()).toBeTruthy();
  ensureAliasConnections(definition, await catalogResponse.json());
  const updated = await request.put(`/api/v1/schemoo/models/${modelId}`, { data: {
    expectedRevision: saved.revision, name: saved.name, definition,
    catalogFingerprint: saved.catalogFingerprint,
  } });
  expect(updated.ok(), await updated.text()).toBeTruthy();
  const response = await request.put(`/api/v1/schemoo/models/${modelId}/layout`, { data: {
    expectedRevision: saved.layoutRevision,
    layout: { positions: [...saved.layout.positions.filter(position => position.id !== target.id),
      target, { id: aliasId, x: target.x, y: target.y }] },
  } });
  expect(response.ok(), await response.text()).toBeTruthy();
  const beforeRepair = await response.json();

  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.locator("#workbench")).toHaveJSProperty("inert", false);
  await expect(page.locator(`[data-node-id="${aliasId}"]`)).toHaveCount(1);
  await expect(page.locator(`[data-node-id="${aliasId}"]`)).toBeVisible();
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
  const fitted = await cardGeometry(page, 13);
  expectNoOverlaps(fitted);
  const stage = page.locator(".sc-stage");
  const view = await stage.getAttribute("style");
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
  expect(await cardGeometry(page, 13)).toEqual(fitted);
  await expect(stage).toHaveAttribute("style", view);

  await expect(page.locator("#draft-status")).toContainText("Unsaved changes");
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  const persisted = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  expect(persisted.layoutRevision).toBe(beforeRepair.layoutRevision + 1);
  expect(persisted.revision).toBe(beforeRepair.revision);
  const alias = persisted.layout.positions.find(position => position.id === aliasId);
  expect(alias).toBeTruthy();
  expect([alias.x, alias.y]).not.toEqual([target.x, target.y]);
  await page.reload();
  await expect(page.locator("#workbench")).toHaveJSProperty("inert", false);
  await expect(page.locator(`[data-node-id="${aliasId}"]`)).toBeVisible();
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
  expectNoOverlaps(await cardGeometry(page, 13));
  await expect(page.locator("#draft-status")).toContainText("Saved");

  // Probe the exact column hit targets after persistence without changing the saved model.
  await page.getByRole("button", { name: "Draw connection", exact: true }).click();
  await page.locator('[data-node-id="personnel_certification_fact"] .sc-column[data-column-name="certification_id"]').click();
  await expect(page.locator(".sc-connection-status")).toContainText("certification_id selected");
  const count = await page.locator(".sc-edge").count();
  await page.locator('[data-node-id="certification_dim"] .sc-column[data-column-name="id"]').click();
  await expect(page.locator(".sc-edge")).toHaveCount(count + 1);
});

test("new alias and calculated source land clear of existing cards and save", async ({ page, request }) => {
  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.locator("#workbench")).toHaveJSProperty("inert", false);
  await expect(page.locator(".sc-node")).toHaveCount(12);
  await expect(page.locator('[data-node-id="certification_dim"]')).toBeVisible();
  await page.locator('[data-node-id="certification_dim"] .sc-node-header').click();
  await page.getByRole("button", { name: "Create alias", exact: true }).click();
  await page.getByRole("textbox", { name: "Alias name for certification_dim", exact: true }).fill("QA placement alias");
  await page.getByRole("button", { name: "Add alias to model", exact: true }).click();
  await expect(page.locator('.sc-node[aria-label="QA placement alias model source"]')).toBeVisible();
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
  expectNoOverlaps(await cardGeometry(page, 13));

  await page.locator('[data-node-id="pay_band_class_dim"] .sc-node-header').click();
  await page.getByRole("button", { name: "Add calculated source", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Add calculated source", exact: true });
  await dialog.getByRole("textbox", { name: "Calculated source name", exact: true }).fill("QA placement calculation");
  await dialog.getByRole("textbox", { name: "Field 1 name", exact: true }).fill("Range sum");
  await chooseModelOption(page, "Field 1 column", "min_pay_range · integer");
  await chooseModelOption(page, "Field 1 second column", "level · integer");
  await dialog.getByRole("button", { name: "Apply to model", exact: true }).click();
  await expect(page.locator(".sc-derived")).toHaveCount(1);
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
  expectNoOverlaps(await cardGeometry(page, 14));
  const fitted = await cardGeometry(page, 14);
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
  expect(await cardGeometry(page, 14)).toEqual(fitted);
  await expect(page.locator(".sc-derived-edge")).toHaveCount(1);
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  const persisted = await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  expect(persisted.definition.nodes).toHaveLength(14);
  expect(persisted.definition.nodes.every(node => !Object.hasOwn(node, "distance"))).toBe(true);
  await page.reload();
  await expect(page.locator(".sc-node")).toHaveCount(14);
});
