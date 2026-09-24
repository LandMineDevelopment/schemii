import { expect, test } from "@playwright/test";

for (const product of [
  { path: "/", slug: "schemii", name: "Schemii", steps: 6 },
  { path: "/schemoo", slug: "schemoo", name: "Schemoo", steps: 4 },
  { path: "/schemer", slug: "schemer", name: "Schemer", steps: 4 },
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
    await expect(dialog.locator(".quick-start-page:visible .quick-start-scene [data-quick-start-target]").first()).toBeAttached();
    await expect(dialog.getByRole("button", { name: "Pause demo" })).toBeVisible();
    await dialog.getByRole("button", { name: "Pause demo" }).click();
    await expect(dialog.getByRole("button", { name: "Play demo" })).toBeVisible();
    await expect(dialog.locator(".quick-start-page:visible .quick-start-status")).toContainText("paused");
    await dialog.getByRole("button", { name: "Replay" }).click();
    await expect(dialog.getByRole("button", { name: "Pause demo" })).toBeVisible();
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

  test(`${product.name} quick start respects reduced motion and can be replayed`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(product.path);
    if (product.name === "Schemoo") await page.getByRole("button", { name: "Close model library" }).click();
    if (product.name === "Schemii") await page.locator('summary[aria-label="Help"]').click();
    const trigger = product.name === "Schemii"
      ? page.getByRole("button", { name: "Quick start guide" })
      : page.locator("#quick-start-button");
    await trigger.click();
    const dialog = page.getByRole("dialog", { name: `Welcome to ${product.name}` });
    const scene = dialog.locator(".quick-start-page:visible .quick-start-scene");
    await expect(dialog.getByRole("button", { name: "Play demo" })).toBeVisible();
    await expect(scene.locator(".quick-start-cursor")).not.toHaveClass(/visible/);
    await expect(dialog.locator(".quick-start-page:visible .quick-start-status")).not.toBeEmpty();
    await dialog.getByRole("button", { name: "Play demo" }).click();
    await expect(dialog.getByRole("button", { name: "Pause demo" })).toBeVisible();
    await expect(scene.locator(".quick-start-cursor")).toHaveClass(/visible/, { timeout: 3000 });
    await dialog.getByRole("button", { name: "Replay" }).click();
    await expect(dialog.getByRole("button", { name: "Pause demo" })).toBeVisible();
  });

  test(`${product.name} demo points to controls visible in each workflow state`, async ({ page }) => {
    await page.goto(product.path);
    if (product.name === "Schemoo") await page.getByRole("button", { name: "Close model library" }).click();
    if (product.name === "Schemii") await page.locator('summary[aria-label="Help"]').click();
    await (product.name === "Schemii"
      ? page.getByRole("button", { name: "Quick start guide" })
      : page.locator("#quick-start-button")).click();
    const dialog = page.getByRole("dialog", { name: `Welcome to ${product.name}` });
    const steps = await page.evaluate(async slug => {
      const { QUICK_STARTS } = await import("/assets/common/quick-start.js");
      return QUICK_STARTS[slug].steps.map(step => step.actions.map(action => ({ target: action.target, state: action.state })));
    }, product.slug);
    for (const [stepIndex, actions] of steps.entries()) {
      if (stepIndex) await dialog.getByRole("button", { name: "Next" }).click();
      await dialog.getByRole("button", { name: "Pause demo" }).click();
      const scene = dialog.locator(".quick-start-page:visible .quick-start-scene");
      const sceneBounds = await scene.boundingBox();
      for (const action of actions) {
        const control = scene.locator(`[data-quick-start-target="${action.target}"]`);
        await expect(control, `${product.name}: ${action.target}`).toBeVisible();
        await expect.poll(async () => {
          const bounds = await control.boundingBox();
          const x = bounds.x + bounds.width / 2;
          const y = bounds.y + bounds.height / 2;
          return x > sceneBounds.x && x < sceneBounds.x + sceneBounds.width
            && y > sceneBounds.y && y < sceneBounds.y + sceneBounds.height;
        }, { message: `${product.name}: ${action.target} stays inside the illustration` }).toBe(true);
        await scene.locator(":scope > :first-child").evaluate((mock, state) => mock.classList.add(`demo-${state}`), action.state);
      }
    }
  });
}

test("animated cursor follows its control when the guide is resized", async ({ page }) => {
  await page.goto("/");
  await page.locator('summary[aria-label="Help"]').click();
  await page.getByRole("button", { name: "Quick start guide" }).click();
  const scene = page.getByRole("dialog", { name: "Welcome to Schemii" }).locator(".quick-start-page:visible .quick-start-scene");
  await expect(scene.locator(".quick-start-cursor")).toHaveClass(/visible/);
  const viewport = page.viewportSize();
  await page.setViewportSize({ width: viewport.width > 600 ? 900 : 500, height: viewport.height });
  await expect.poll(() => scene.evaluate(stage => {
    const cursor = stage.querySelector(".quick-start-cursor");
    const target = stage.querySelector('[data-quick-start-target="connections"]');
    const stageBounds = stage.getBoundingClientRect();
    const targetBounds = target.getBoundingClientRect();
    const x = targetBounds.left - stageBounds.left + targetBounds.width / 2;
    const y = targetBounds.top - stageBounds.top + targetBounds.height / 2;
    return Math.hypot(cursor.offsetLeft - x, cursor.offsetTop - y);
  })).toBeLessThan(3);
});

test("Schemoo teaches the model starting object before preview outputs", async ({ page }) => {
  await page.goto("/schemoo");
  await page.getByRole("button", { name: "Close model library" }).click();
  await page.locator("#quick-start-button").click();
  const dialog = page.getByRole("dialog", { name: "Welcome to Schemoo" });
  await dialog.getByRole("button", { name: "Next" }).click();
  await expect(dialog.locator(".quick-start-page:visible .smq-pane-model [data-quick-start-target='model-start']")).toBeVisible();
  await dialog.getByRole("button", { name: "Next" }).click();
  await dialog.getByRole("button", { name: "Next" }).click();
  await expect(dialog.locator(".quick-start-page:visible .smq-pane-preview")).toContainText("Preview outputs");
  await expect(dialog.locator(".quick-start-page:visible .smq-pane-preview")).not.toContainText("Start from");
});
