import { expect, test } from "@playwright/test";

test("Schemii relationship stays attached and the migration changes can be inspected", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await page.locator('summary[aria-label="Help"]').click();
  await page.getByRole("button", { name: "Quick start guide" }).click();
  const dialog = page.getByRole("dialog", { name: "Welcome to Schemii" });

  for (let index = 0; index < 3; index++) await dialog.getByRole("button", { name: "Next" }).click();
  const relationship = dialog.locator(".quick-start-page:visible .quick-start-scene");
  await expect(relationship).toContainText("wishlists");
  await expect(relationship).toContainText("customer_id");
  await expect(relationship).toContainText("Live customers rows");

  for (const state of ["linked", "inspector", "rows"]) {
    await relationship.locator(".qs-s-app").evaluate((app, state) => {
      app.classList.toggle("demo-inspector", state !== "linked");
      app.classList.toggle("demo-rows", state === "rows");
    }, state);
    const geometry = await relationship.evaluate(scene => {
      const [source, line, target] = [
        scene.querySelector(".qs-s-diagram .qs-s-table:first-child"),
        scene.querySelector(".qs-s-relationship-line"),
        scene.querySelector(".qs-s-diagram .qs-s-table:last-child"),
      ].map(element => element.getBoundingClientRect());
      return {
        sourceGap: Math.abs(source.right - line.left),
        targetGap: Math.abs(line.right - target.left),
        width: line.width,
        visible: getComputedStyle(scene.querySelector(".qs-s-relationship-line")).visibility === "visible",
      };
    });
    expect(geometry, `${state} relationship connector`).toMatchObject({ visible: true });
    expect(geometry.sourceGap).toBeLessThan(1);
    expect(geometry.targetGap).toBeLessThan(1);
    expect(geometry.width).toBeGreaterThan(10);
  }

  for (let index = 0; index < 2; index++) await dialog.getByRole("button", { name: "Next" }).click();
  const migration = dialog.locator(".quick-start-page:visible .quick-start-scene");
  if (page.viewportSize().width <= 600) {
    await dialog.locator(".quick-start-page:visible .quick-start-toggle").click();
    await page.waitForFunction(() => {
      const guide = document.querySelector(".quick-start-page:not([hidden])");
      const mock = guide?.querySelector(".qs-s-migration-demo");
      const cursor = guide?.querySelector(".quick-start-cursor");
      return mock?.classList.contains("demo-expanded")
        && cursor?.querySelector("span")?.textContent === "Relationship change";
    });
    const clearance = await migration.evaluate(scene => {
      const cursor = scene.querySelector(".quick-start-cursor");
      const label = cursor.querySelector("span").getBoundingClientRect();
      const sql = scene.querySelector(".qs-s-relationship-sql").getBoundingClientRect();
      return { high: cursor.classList.contains("tooltip-high"), gap: sql.top - label.bottom };
    });
    expect(clearance.high).toBe(true);
    expect(clearance.gap).toBeGreaterThan(0);
  }
  await expect(migration.locator(".qs-s-create-sql")).toBeHidden();
  await expect(migration.locator(".qs-s-relationship-sql")).toBeVisible();
  await expect(migration.locator("[data-quick-start-target='migration-relationship-change']")).toBeInViewport();
  await expect(migration.locator("[data-quick-start-target='apply-migration']")).toBeInViewport();
});
