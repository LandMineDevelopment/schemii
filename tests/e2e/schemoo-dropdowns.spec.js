import { expect, test } from "@playwright/test";
import { chooseModelOption, expectDropdownWithinViewport } from "./helpers/schemoo-select.js";
import { createOrganizationModel, deleteModel } from "./helpers/schemoo-model.js";

let modelId;
test.beforeEach(async ({ request }) => { modelId = await createOrganizationModel(request, "E2E dropdowns"); });
test.afterEach(async ({ request }) => { await deleteModel(request, modelId); modelId = null; });

test("searchable source bindings stay on screen and aliases can be created and connected", async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("dialog", dialog => dialog.accept());
  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.locator(".sc-node")).toHaveCount(12);
  if (await page.locator("#inspector").isVisible()) await page.getByRole("button", { name: "Close inspector", exact: true }).click();
  await page.locator('[data-node-id="certification_dim"] .sc-node-header').click();
  await page.getByRole("button", { name: "Create alias", exact: true }).click();
  await page.getByRole("textbox", { name: "Alias name for certification_dim", exact: true }).fill("Personnel credentials");
  await page.getByRole("button", { name: "Add alias to model", exact: true }).click();
  await expect(page.locator(".sc-node")).toHaveCount(13);
  await expect(page.locator(".sc-node-header").filter({ hasText: "Personnel credentials" })).toHaveCount(1);
  await page.getByRole("button", { name: "Edit model", exact: true }).click();
  await page.locator(".edge-name").filter({ hasText: "personnel_certification_fact.certification_id → Personnel credentials.id" }).click();
  await page.getByRole("checkbox", { name: "Relationship enabled", exact: true }).check();
  await expect(page.locator(".sc-edge").filter({ has: page.locator("title", {
    hasText: "personnel_certification_fact.certification_id to Personnel credentials.id",
  }) })).toHaveCount(1);
  await page.getByRole("button", { name: "Edit model", exact: true }).click();
  await chooseModelOption(page, "Starting model object", "Personnel credentials", "credentials");
  await chooseModelOption(page, "Starting model object", "personnel_dim", "personnel_dim");
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  await chooseModelOption(page, "Preview column 1 aggregation", "Count distinct", "distinct");
  await chooseModelOption(page, "Preview column 1 aggregation", "Plain field", "plain");
  await page.getByRole("button", { name: "Model filters", exact: true }).click();
  // Model/filter editors use searchable controls; the separate, closed AI
  // assistant has its own provider-model selector and is outside this check.
  await expect(page.locator("#inspector select")).toHaveCount(0);

  await page.getByRole("button", { name: "Add model filter scope", exact: true }).click();
  await page.getByRole("button", { name: /^Report parameter/ }).click();
  const binding = page.getByRole("combobox", { name: "Model filter / Default condition 1 field", exact: true });
  await binding.click();
  await binding.fill("credentials");
  const list = page.locator(`#${await binding.getAttribute("aria-controls")}`);
  await expect(list.getByRole("option")).toHaveCount(4);
  await expectDropdownWithinViewport(binding);
  await expect(list.getByRole("option", { name: "Personnel credentials · name", exact: true })).toBeVisible();
  await page.screenshot({ path: `artifacts/schemoo-searchable-binding-${testInfo.project.name}.png` });
  await list.getByRole("option", { name: "Personnel credentials · name", exact: true }).click();
  await expect(binding).toHaveValue("Personnel credentials · name");

  // No free-text value can silently turn into an invalid source binding.
  await binding.fill("there-is-no-such-source");
  await expect(page.getByRole("listbox", { name: "Model filter / Default condition 1 field options", exact: true })).toContainText("No matching");
  await page.keyboard.press("Escape");
  await expect(binding).toHaveValue("Personnel credentials · name");
  // Redrawing the dialog must dispose of detached popup nodes.
  for (let redraw = 0; redraw < 3; redraw += 1) {
    await chooseModelOption(page, "Model filter / Default condition 1 operator", redraw % 2 ? "Equals" : "Does not equal");
  }
  expect(await page.locator(".ui-searchable-select__list").count()).toBe(await page.locator(".ui-searchable-select").count());
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  await page.reload();
  await expect(page.locator(".sc-node")).toHaveCount(13);
  await expect(page.locator(".sc-edge").filter({ has: page.locator("title", {
    hasText: "personnel_certification_fact.certification_id to Personnel credentials.id",
  }) })).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});
