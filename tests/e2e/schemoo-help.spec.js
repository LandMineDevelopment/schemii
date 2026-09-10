import { expect, test } from "@playwright/test";
import { createOrganizationModel, deleteModel } from "./helpers/schemoo-model.js";

let modelId;
test.beforeEach(async ({ request }) => { modelId = await createOrganizationModel(request, "E2E help"); });
test.afterEach(async ({ request }) => { await deleteModel(request, modelId); modelId = null; });

test("context help is readable by tap and keyboard, and toolbar tooltips explain actions", async ({ page }, testInfo) => {
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("dialog", d => d.accept());
  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.locator(".sc-node")).toHaveCount(12);
  const overview = page.getByRole("button", { name: "About Building and exploring a model", exact: true });
  await overview.click();
  await expect(page.getByRole("dialog")).toContainText("It does not copy or change");
  await page.keyboard.press("Escape");
  await expect(overview).toBeFocused();
  if (testInfo.project.name === "desktop-chromium") {
    await page.locator("#show-explore").hover();
    await expect(page.getByRole("tooltip")).toContainText("parameters");
  }
  await page.getByRole("button", { name: "Edit model", exact: true }).click();
  await page.locator("#example").click();
  await page.locator("#show-filters").click();
  await page.getByRole("button", { name: "Edit filter Time scope", exact: true }).click();
  const bindings = page.getByRole("button", { name: "About Binding source fields to parameters", exact: true }).first();
  await bindings.click();
  const help=page.locator(".sm-help-dialog");
  await expect(help).toContainText("Bind source");
  await expect(help).toContainText("slate_fact.start_date");
  await expect(help).toContainText("Source · column");
  await expect(help).toContainText("Comparison");
  await page.screenshot({ path: `artifacts/schemoo-binding-help-${testInfo.project.name}.png` });
  await page.getByRole("button", { name: "Close information", exact: true }).click();
  await expect(bindings).toBeFocused();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});
