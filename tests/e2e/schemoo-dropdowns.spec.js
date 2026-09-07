import { expect, test } from "@playwright/test";
import { chooseModelOption, expectDropdownWithinViewport } from "./helpers/schemoo-select.js";

test("searchable source bindings stay on screen and aliases can be created and connected", async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("dialog", dialog => dialog.accept());
  await page.goto("/schemoo");
  await expect(page.locator(".sc-node")).toHaveCount(12);
  await page.getByRole("button", { name: "Edit model", exact: true }).click();
  await page.getByRole("button", { name: "Add alias", exact: true }).click();
  await chooseModelOption(page, "Alias source", "certification_dim", "certification");
  await page.getByRole("textbox", { name: "Alias name", exact: true }).fill("Personnel credentials");
  await page.getByRole("button", { name: "Create alias table", exact: true }).click();
  await expect(page.locator(".sc-node")).toHaveCount(13);
  await expect(page.locator(".sc-node-header").filter({ hasText: "Personnel credentials" })).toHaveCount(1);
  await page.locator(".edge-name").filter({ hasText: "personnel_certification_fact.certification_id → Personnel credentials.id" }).click();
  await page.getByRole("checkbox", { name: "Relationship enabled", exact: true }).check();
  await expect(page.locator(".sc-edge").filter({ has: page.locator("title", {
    hasText: "personnel_certification_fact.certification_id to Personnel credentials.id",
  }) })).toHaveCount(1);
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  await chooseModelOption(page, "Starting model object", "Personnel credentials", "credentials");
  await chooseModelOption(page, "Starting model object", "personnel_dim", "personnel_dim");
  await chooseModelOption(page, "Aggregation for personnel_dim.name", "Count distinct", "distinct");
  await chooseModelOption(page, "Aggregation for personnel_dim.name", "Plain field", "plain");
  await page.getByRole("button", { name: "Edit model", exact: true }).click();
  await expect(page.locator("select")).toHaveCount(0);

  await page.getByRole("button", { name: "Add model filter scope", exact: true }).click();
  await page.getByRole("button", { name: "Add input to Model filter / Default", exact: true }).click();
  await page.getByRole("button", { name: "Bind source to Parameter", exact: true }).click();
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
  // Redrawing bindings and switching panels must dispose of detached popup nodes.
  for (let redraw = 0; redraw < 3; redraw += 1) {
    await chooseModelOption(page, "Model filter / Default condition 1 operator", redraw % 2 ? "Equals" : "Does not equal");
    await page.getByRole("button", { name: "Explore model", exact: true }).click();
    await page.getByRole("button", { name: "Edit model", exact: true }).click();
  }
  expect(await page.locator(".ui-searchable-select__list").count()).toBe(await page.locator(".ui-searchable-select").count());
  await page.reload();
  await expect(page.locator(".sc-node")).toHaveCount(13);
  await expect(page.locator(".sc-edge").filter({ has: page.locator("title", {
    hasText: "personnel_certification_fact.certification_id to Personnel credentials.id",
  }) })).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});
