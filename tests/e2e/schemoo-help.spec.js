import { expect, test } from "@playwright/test";

test("context help is readable by tap and keyboard, and toolbar tooltips explain actions", async ({ page }, testInfo) => {
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("dialog", d => d.accept());
  await page.goto("/schemoo");
  await expect(page.locator(".sc-node")).toHaveCount(12);
  const overview = page.getByRole("button", { name: "About Building and exploring a model", exact: true });
  await overview.click();
  await expect(page.getByRole("dialog")).toContainText("It does not copy or change");
  await page.keyboard.press("Escape");
  await expect(overview).toBeFocused();
  if (testInfo.project.name === "desktop-chromium") {
    await page.locator("#show-explore").hover();
    await expect(page.getByRole("tooltip")).toContainText("parameter values");
  }
  await page.locator("#example").click();
  await page.locator("#show-model").click();
  const bindings = page.getByRole("button", { name: "About Binding source fields to parameters", exact: true }).first();
  await bindings.click();
  await expect(page.getByRole("dialog")).toContainText("Bind source");
  await expect(page.getByRole("dialog")).toContainText("slate_fact.start_date");
  await expect(page.getByRole("dialog")).toContainText("Compare against");
  await page.screenshot({ path: `artifacts/schemoo-binding-help-${testInfo.project.name}.png` });
  await page.getByRole("button", { name: "Close information", exact: true }).click();
  await expect(bindings).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});
