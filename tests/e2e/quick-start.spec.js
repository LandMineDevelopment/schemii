import { expect, test } from "@playwright/test";

for (const product of [
  { path: "/", name: "Schemii", steps: 5 },
  { path: "/schemoo", name: "Schemoo", steps: 4 },
  { path: "/schemer", name: "Schemer", steps: 4 },
]) {
  test(`${product.name} quick start can be completed and reopened`, async ({ page }) => {
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(product.path);
    if (product.name === "Schemoo") await page.getByRole("button", { name: "Close model library" }).click();
    const trigger = product.name === "Schemii"
      ? page.getByRole("button", { name: "Quick start guide" })
      : page.locator("#quick-start-button");
    const helpMenu = page.locator('summary[aria-label="Help"]');
    if (product.name === "Schemii") await helpMenu.click();
    await trigger.click();
    const dialog = page.getByRole("dialog", { name: `Welcome to ${product.name}` });
    await expect(dialog).toBeVisible();
    const bounds = await dialog.boundingBox();
    const viewport = page.viewportSize();
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(viewport.width);
    await expect(dialog.locator(".quick-start-page:visible")).toHaveCount(1);
    await expect(dialog.locator(".quick-start-count")).toHaveText(`1 of ${product.steps}`);
    await expect(dialog.getByRole("button", { name: "Previous step" })).toBeDisabled();
    for (let step = 2; step <= product.steps; step++) {
      await dialog.getByRole("button", { name: "Next" }).click();
      await expect(dialog.locator(".quick-start-count")).toHaveText(`${step} of ${product.steps}`);
      await expect(dialog.locator(".quick-start-page:visible")).toHaveCount(1);
    }
    await expect(dialog.getByRole("button", { name: "Finish" })).toBeVisible();
    await dialog.getByRole("button", { name: "Previous step" }).click();
    await expect(dialog.locator(".quick-start-count")).toHaveText(`${product.steps - 1} of ${product.steps}`);
    await dialog.getByRole("button", { name: "Next" }).click();
    await dialog.getByRole("button", { name: "Finish" }).click();
    await expect(dialog).toBeHidden();
    await expect(product.name === "Schemii" ? helpMenu : trigger).toBeFocused();
    if (product.name === "Schemii") await helpMenu.click();
    await trigger.click();
    await expect(dialog.locator(".quick-start-count")).toHaveText(`1 of ${product.steps}`);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    expect(errors).toEqual([]);
  });
}
