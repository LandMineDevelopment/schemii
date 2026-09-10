import { expect, test } from "@playwright/test";

test("application menu stays visible near either viewport edge and after resizing", async ({ page }) => {
  await page.goto("/schemoo");
  await page.getByRole("button", { name: "Close model library", exact: true }).click();
  const trigger = page.locator(".ui-product-navigation > summary");
  const surface = page.getByRole("navigation", { name: "Schemii applications" });
  for (const width of [412, 320, 1280]) {
    await page.setViewportSize({ width, height: 850 });
    if (!await surface.isVisible()) await trigger.click();
    await expect(surface.getByRole("link", { name: "Schemii Schema design", exact: true })).toBeVisible();
    await expect.poll(async () => {
      const box = await surface.boundingBox();
      return box && box.x >= 7 && box.x + box.width <= width - 7;
    }).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(surface).toBeHidden();
  await expect(trigger).toBeFocused();
  await trigger.click();
  await surface.getByRole("link", { name: "Schemii Schema design", exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
});
