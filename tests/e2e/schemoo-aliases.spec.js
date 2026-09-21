import { expect, test } from "@playwright/test";
import { chooseModelOption } from "./helpers/schemoo-select.js";
import { createOrganizationModel, deleteModel } from "./helpers/schemoo-model.js";

let modelId;
test.beforeEach(async ({ request }) => {
  modelId = await createOrganizationModel(request, "E2E aliases");
});

test.afterEach(async ({ request }) => {
  await deleteModel(request, modelId);
  modelId = null;
});

async function inspectCertification(page) {
  if (await page.locator("#inspector").isVisible()) {
    await page.getByRole("button", { name: "Close inspector", exact: true }).click();
  }
  await page.locator('[data-node-id="certification_dim"] .sc-node-header').click();
}

async function createAlias(page, label = "Personnel credentials") {
  await page.getByRole("button", { name: "Edit model", exact: true }).click();
  if (await page.locator("#inspector").isVisible()) {
    await page.getByRole("button", { name: "Close inspector", exact: true }).click();
  }
  await page.locator('[data-node-id="certification_dim"] .sc-node-header').click();
  await expect(page.getByRole("complementary", { name: "Table inspector", exact: true })).toBeVisible();
  await expect(page.locator("#inspector")).toBeHidden();
  await page.getByRole("button", { name: "Create alias", exact: true }).click();
  const name = page.getByRole("textbox", { name: "Alias name for certification_dim", exact: true });
  await expect(name).toHaveValue("certification_dim (alias)");
  await name.fill(label);
  await page.getByRole("button", { name: "Add alias to model", exact: true }).click();
  await expect(page.locator(".sc-node")).toHaveCount(13);
}

function relation(page, endpoint) {
  return page.locator("#relationships .relationship").filter({ has: page.locator(".edge-name").filter({
    hasText: new RegExp(`^personnel_certification_fact\\.certification_id → ${endpoint}\\.id(?: · CYCLE)?$`),
  }) });
}

test("creating and deleting aliases preserves the user's zoom and pan", async ({ page }) => {
  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.locator(".sc-node")).toHaveCount(12);
  if (await page.locator("#inspector").isVisible()) await page.getByRole("button", { name: "Close inspector", exact: true }).click();
  await page.locator('[data-node-id="certification_dim"] .sc-node-header').click();
  await page.getByRole("button", { name: "Close table inspector", exact: true }).click();
  await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  await page.getByRole("button", { name: "Zoom in", exact: true }).click();
  const stage = page.locator(".sc-stage");
  const beforePan = await stage.getAttribute("style");
  const box = await page.locator("#canvas-host").boundingBox();
  await page.mouse.move(box.x + 12, box.y + 120);
  await page.mouse.down();
  await page.mouse.move(box.x + 48, box.y + 160, { steps: 5 });
  await page.mouse.up();
  await expect(stage).not.toHaveAttribute("style", beforePan);
  await inspectCertification(page);
  const view = await stage.getAttribute("style");
  for (let index = 0; index < 2; index++) {
    await page.getByRole("button", { name: "Create alias", exact: true }).click();
    await page.getByRole("textbox", { name: /^Alias name for / }).fill(`Viewport alias ${index}`);
    await page.getByRole("button", { name: "Add alias to model", exact: true }).click();
    await expect(page.locator(".sc-node")).toHaveCount(13 + index);
    await expect(page.locator("#plan-status")).not.toHaveText("Checking model and required parameters…");
    await expect(stage).toHaveAttribute("style", view);
  }
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "Remove from model Viewport alias 1", exact: true }).click();
  await expect(page.locator(".sc-node")).toHaveCount(13);
  await expect(page.locator("#plan-status")).not.toHaveText("Checking model and required parameters…");
  await expect(stage).toHaveAttribute("style", view);
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
  await expect(stage).not.toHaveAttribute("style", view);
});

test("table inspector owns columns and aliases while Model and Filters have separate panels", async ({ page }) => {
  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.locator(".sc-node")).toHaveCount(12);
  if (await page.locator("#inspector").isVisible()) await page.getByRole("button", {name:"Close inspector",exact:true}).click();
  await page.locator('[data-node-id="certification_dim"] .sc-node-header').click();
  await expect(page.locator("#table-inspector-title")).toHaveText("certification_dim");
  await expect(page.locator("#inspector")).toBeHidden();
  const toggle=page.locator("#table-inspector").getByRole("checkbox",{name:"Expose certification_dim.id",exact:true});
  await toggle.uncheck();
  await toggle.check();
  await expect(page.locator('[data-node-id="certification_dim"] input[aria-label="Expose certification_dim.id"]')).toBeChecked();
  await page.getByRole("button",{name:"Explore model",exact:true}).click();
  await expect(page.locator("#table-inspector")).toBeHidden();
  await expect(page.locator("#fields .preview-output")).toHaveCount(1);
  await expect(page.locator("#explore-pane #root")).toHaveCount(0);
  await page.getByRole("button",{name:"Edit model",exact:true}).click();
  await expect(page.getByRole("combobox",{name:"Starting model object",exact:true})).toBeVisible();
  await expect(page.getByRole("button",{name:"Add model filter scope",exact:true})).toBeHidden();
  await page.getByRole("button",{name:"Model filters",exact:true}).click();
  await expect(page.getByRole("button",{name:"Add model filter scope",exact:true})).toBeVisible();
  await inspectCertification(page);
  await expect(toggle).toBeChecked();
  await toggle.uncheck();
  await expect(page.locator('[data-node-id="certification_dim"] input[aria-label="Expose certification_dim.id"]')).not.toBeChecked();
  await expect(page.locator("#table-inspector").getByRole("button",{name:"Create alias",exact:true})).toBeVisible();
  await page.screenshot({path:`artifacts/schemoo-table-inspector-${test.info().project.name}.png`});
});

async function selectAlias(page) {
  if (await page.locator("#table-inspector").isVisible()) await page.getByRole("button", { name: "Close table inspector", exact: true }).click();
  if (await page.locator("#inspector").isVisible()) await page.getByRole("button", { name: "Close inspector", exact: true }).click();
  await page.getByRole("button", { name: "Fit model", exact: true }).click();
  await page.locator(".sc-node-header").filter({ hasText: "Personnel credentials" }).click();
}

test("alias connections are independent, persist, show cycles and disappear only after confirmed removal", async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.locator(".sc-edge")).toHaveCount(18);
  await createAlias(page);
  const alias = page.locator(".sc-node").filter({ has: page.locator(".sc-node-name", { hasText: "Personnel credentials" }) });
  await expect(alias).toContainText("ALIAS");
  await expect(page.locator(".sc-edge")).toHaveCount(21);
  const aliasRows = page.locator("#relationships .relationship").filter({ hasText: "→ Personnel credentials.id" });
  await expect(aliasRows).toHaveCount(3);
  await expect(aliasRows.getByRole("checkbox", { checked: true })).toHaveCount(0);
  const original = relation(page, "certification_dim");
  const own = relation(page, "Personnel credentials");
  await expect(original.locator('input[type="checkbox"]')).toBeChecked();
  await page.getByRole("checkbox", { name: "Enable connection personnel_certification_fact.certification_id → Personnel credentials.id", exact: true }).check();
  await expect(own.locator('input[type="checkbox"]')).toBeChecked();
  await expect(original.locator('input[type="checkbox"]')).toBeChecked();
  await page.getByRole("button", { name: "Edit model", exact: true }).click();
  await original.getByRole("checkbox").uncheck();
  await expect(own.getByRole("checkbox")).toBeChecked();
  await original.getByRole("checkbox").check();
  // A second alias path closes a cycle. Both participating alias edges must be marked.
  await aliasRows.filter({ hasText: "required_certification_1_id" }).getByRole("checkbox").check();
  await expect(own).toHaveClass(/cyclic/);
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  await page.reload();
  await page.getByRole("button", { name: "Edit model", exact: true }).click();
  await expect(own.getByRole("checkbox")).toBeChecked();
  await expect(original.getByRole("checkbox")).toBeChecked();
  await expect(page.locator(".sc-edge")).toHaveCount(21);
  await expect(own).toHaveClass(/cyclic/);
  await selectAlias(page);
  await page.locator("#node-connections input").last().scrollIntoViewIfNeeded();
  await page.screenshot({ path: `artifacts/schemoo-alias-controls-${testInfo.project.name}.png` });
  page.once("dialog", dialog => dialog.dismiss());
  await page.getByRole("button", { name: "Remove from model Personnel credentials", exact: true }).click();
  await expect(page.locator(".sc-node")).toHaveCount(13);
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "Remove from model Personnel credentials", exact: true }).click();
  await expect(page.locator(".sc-node")).toHaveCount(12);
  await expect(page.locator(".sc-edge")).toHaveCount(18);
  await expect(page.locator('[data-node-id="certification_dim"]')).toHaveCount(1);
  await expect(original.locator('input[type="checkbox"]')).toBeChecked();
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  await page.reload();
  await expect(page.locator(".sc-node")).toHaveCount(12);
  await expect(page.locator(".sc-edge")).toHaveCount(18);
  expect(errors).toEqual([]);
});

test("deleting a bound alias invalidates its binding instead of silently deleting the restriction", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.locator(".sc-node")).toHaveCount(12);
  await page.getByRole("button", { name: "Edit model", exact: true }).click();
  await expect(page.locator("#relationships input")).toHaveCount(18);
  const connections = await page.locator("#relationships input").evaluateAll(inputs => inputs.map(input => input.getAttribute("aria-label")));
  for (const name of connections) await page.getByRole("checkbox", { name, exact: true }).uncheck();
  await expect(page.getByRole("button", { name: "Run preview", exact: true })).toBeEnabled();
  await createAlias(page);
  await page.getByRole("button", { name: "Model filters", exact: true }).click();
  await page.getByRole("button", { name: "Add model filter scope", exact: true }).click();
  await page.getByRole("button", { name: /^Report parameter/ }).click();
  await chooseModelOption(page, "Model filter / Default condition 1 field", "Personnel credentials · name", "credentials");
  await page.getByRole("button", { name: "Apply to model", exact: true }).click();
  await selectAlias(page);
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "Remove from model Personnel credentials", exact: true }).click();
  await page.getByRole("button", { name: "Model filters", exact: true }).click();
  await page.getByRole("button", { name: "Edit filter Model filter", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Model filter / Default condition 1 field", exact: true })).toHaveValue("");
  await expect(page.getByRole("button", { name: "Bind source to Parameter", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByRole("button", { name: "Run preview", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  await page.reload();
  await page.getByRole("button", { name: "Model filters", exact: true }).click();
  await page.getByRole("button", { name: "Edit filter Model filter", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Model filter / Default condition 1 field", exact: true })).toHaveValue("");
});
