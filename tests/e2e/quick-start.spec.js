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

for (const product of [
  { name: "Schemoo", slug: "schemoo", target: "create-model", state: "created" },
  { name: "Schemer", slug: "schemer", target: "save-dashboard", state: "saved" },
]) {
  test.describe(`${product.name} disappearing guide target`, () => {
    let actions;
    test.beforeEach(async ({ page }) => {
      // Installing before navigation avoids mixing native and fake timer handles.
      // Navigation/fixture work is a Before Hook, separate from playback timing.
      await page.clock.install();
      await page.goto(`/${product.slug}`);
      if (product.name === "Schemoo") await page.getByRole("button", { name: "Close model library" }).click();
      actions = await page.evaluate(async slug => {
        const { QUICK_STARTS } = await import("/assets/common/quick-start.js");
        return QUICK_STARTS[slug].steps[0].actions.map(action => ({
          target: action.target, delay: action.delay ?? 900,
        }));
      }, product.slug);
      await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
      await page.locator("#quick-start-button").click();
      await expect(page.getByRole("dialog", { name: `Welcome to ${product.name}` })).toBeVisible();
    });

    test("cursor clears immediately when the clicked control disappears", async ({ page }) => {
      const scene = page.getByRole("dialog", { name: `Welcome to ${product.name}` })
        .locator(".quick-start-page:visible .quick-start-scene");
      await test.step("controlled playback and disappearing-target assertions", async () => {
        await page.clock.runFor(500);
        for (const [index, action] of actions.entries()) {
          const target = scene.locator(`[data-quick-start-target="${action.target}"]`);
          await expect(target).toBeVisible();
          await expect(scene.locator(".quick-start-cursor")).toHaveClass(/visible/);
          await page.clock.runFor(650);
          await expect(scene.locator(".quick-start-cursor")).toHaveClass(/clicking/);
          await page.clock.runFor(700);
          if (index < actions.length - 1) {
            // The fake clock advances JS timers, while CSS transitions use
            // browser time. Let the newly revealed control finish entering
            // before scheduling the next cursor; locator visibility alone
            // allows a control whose ancestor is still transparent.
            await scene.locator(":scope > :first-child").evaluate(async mock => {
              await Promise.all(mock.getAnimations({ subtree: true }).map(animation => animation.finished));
            });
            await page.clock.runFor(action.delay);
          }
        }
        expect(actions.at(-1).target).toBe(product.target);
        await expect(scene.locator(":scope > :first-child")).toHaveClass(new RegExp(`demo-${product.state}`));
        await expect(scene.locator(`[data-quick-start-target="${product.target}"]`)).toBeHidden();
        // Do not advance the later completion timer: it also clears the cursor
        // and could conceal broken disappearing-control handling.
        await expect(scene.locator(".quick-start-cursor")).not.toHaveClass(/visible/);
        if (product.name === "Schemoo") {
          expect(await scene.locator(".smq-top-target")
            .evaluate(element => getComputedStyle(element, "::after").content))
            .toContain("Bookstore DB · schemii_test.bookstore");
        }
      }, { timeout: 3000 });
    });
  });
}

test("mobile and reduced-motion guides expose a readable action list", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/schemoo");
  await page.getByRole("button", { name: "Close model library" }).click();
  await page.locator("#quick-start-button").click();
  const dialog = page.getByRole("dialog", { name: "Welcome to Schemoo" });
  for (const [index, count] of [5, 5, 6, 5].entries()) {
    const summary = dialog.locator(".quick-start-page:visible .quick-start-action-summary");
    await expect(summary).toBeVisible();
    await expect(summary.locator("li")).toHaveCount(count);
    const size = await summary.locator("li").first().evaluate(item => parseFloat(getComputedStyle(item).fontSize));
    expect(size).toBeGreaterThanOrEqual(11);
    if (index < 3) await dialog.getByRole("button", { name: "Next" }).click();
  }
  await expect(dialog.locator(".quick-start-page:visible .quick-start-action-summary"))
    .toContainText("Show query preview");
  const previewStage = dialog.locator(".quick-start-page:visible .smq-stage-preview");
  await previewStage.evaluate(stage => stage.classList.add("demo-preview", "demo-query", "demo-results"));
  const results = previewStage.locator(".smq-results");
  await expect(results).toBeVisible();
  expect(await results.evaluate(element => getComputedStyle(element).animationName)).toBe("none");
  const resultBounds = await results.evaluate(element => ({
    visibleBottom: element.getBoundingClientRect().bottom,
    lastRowBottom: element.lastElementChild.getBoundingClientRect().bottom,
  }));
  expect(resultBounds.lastRowBottom).toBeLessThanOrEqual(resultBounds.visibleBottom + 1);
});

test("guides use one bookstore orders model and show the extra Schemer filter setup", async ({ page }) => {
  await page.goto("/schemoo");
  await page.getByRole("button", { name: "Close model library" }).click();
  await page.locator("#quick-start-button").click();
  const schemoo = page.getByRole("dialog", { name: "Welcome to Schemoo" });
  await expect(schemoo.locator(".quick-start-page:visible .smq-schema")).toContainText("bookstore");
  await schemoo.getByRole("button", { name: "Next" }).click();
  await expect(schemoo.locator(".quick-start-page:visible .smq-table-inspector"))
    .toContainText("Imported fields start exposed");
  await schemoo.getByRole("button", { name: "Next" }).click();
  await expect(schemoo.locator(".quick-start-page:visible .quick-start-tip"))
    .toContainText("optional Order status report parameter");
  await schemoo.getByRole("button", { name: "Next" }).click();
  await expect(schemoo.locator(".quick-start-page:visible .smq-output-count")).toHaveText("1");
  await expect(schemoo.locator(".quick-start-page:visible .smq-output-list"))
    .toContainText("authors · name");
  await expect(schemoo.locator(".quick-start-page:visible .smq-add-by-table"))
    .toContainText("4 exposed columns");
  await expect(schemoo.locator(".quick-start-page:visible .smq-new-output")).toHaveCount(4);
  await expect(schemoo.locator(".quick-start-page:visible .quick-start-action-summary"))
    .toContainText("Show query preview");

  await page.goto("/schemer");
  await page.locator("#quick-start-button").click();
  const schemer = page.getByRole("dialog", { name: "Welcome to Schemer" });
  await schemer.getByRole("button", { name: "Next" }).click();
  await expect(schemer.locator(".quick-start-page:visible .qs-r-editor-fields"))
    .toContainText("orders.status");
  await schemer.getByRole("button", { name: "Next" }).click();
  await expect(schemer.locator(".quick-start-page:visible .quick-start-tip"))
    .toContainText("Add a separate optional Order status report parameter");
  await expect(schemer.locator(".quick-start-page:visible .qs-r-filter-value"))
    .toContainText("shipped");
  await schemer.getByRole("button", { name: "Next" }).click();
  await expect(schemer.locator(".quick-start-page:visible .qs-r-detail-rows thead th"))
    .toHaveText(["orders.id"]);
  await expect(schemer.locator(".quick-start-page:visible .qs-r-detail-rows .qs-r-chip"))
    .toHaveText("status: shipped");
});
