import { expect, test } from "@playwright/test";

async function databaseDesignWorkspace(request) {
  const response = await request.get("/api/v1/schemii/workspaces");
  expect(response.ok()).toBe(true);
  const body = await response.json();
  const workspace = body.workspaces.find(item => item.connectionId);
  expect(workspace, "the demo should provide a database-derived design workspace").toBeTruthy();
  return workspace;
}

test("inspector ports database rows and the console workflow while migration stays separate", async ({ page, request }, testInfo) => {
  const desktop = testInfo.project.name === "desktop-chromium";
  const workspace = await databaseDesignWorkspace(request);
  await page.goto(`/?workspace=${workspace.id}&layer=tables`);

  await page.locator(".table-card").filter({ hasText: "tasks" }).first().click({ force: true });
  await page.getByRole("button", { name: "Open table rows and console" }).click();

  await expect(page.locator("#inspector-data-workspace")).toBeVisible();
  await expect(page.locator("#inspector-data-workspace")).toHaveClass(/open/);
  if (desktop) await expect(page.locator("#inspector")).toBeVisible();
  else await expect(page.locator("#inspector")).toBeHidden();
  await expect(page.locator("#inspector-rows-body .data-grid")).toBeVisible();
  await expect(page.locator("#inspector-rows-body tbody tr")).toHaveCount(5);
  await expect(page.getByRole("button", { name: "Open full row preview" })).toBeEnabled();

  await page.locator("#show-inspector-console").click();
  const switchingPaneState = await page.locator("#inspector-data-workspace").evaluate(workspace => ({
    rowsHidden: workspace.querySelector("#inspector-rows-content").hidden,
    consoleHidden: workspace.querySelector("#inspector-console-content").hidden,
  }));
  expect(switchingPaneState).toEqual({ rowsHidden: false, consoleHidden: false });
  await expect(page.locator("#inspector-rows-content")).toHaveJSProperty("hidden", true);
  await expect(page.locator("#inspector-console-content")).toHaveJSProperty("hidden", false);
  const draft = page.getByRole("textbox", { name: "Table-scoped read-only SQL query" });
  await expect(draft).toHaveValue('SELECT *\nFROM "public"."tasks"\nLIMIT 100;');
  await page.locator("#run-inspector-sql").click();
  await expect(page.locator("#inspector-data-workspace")).toHaveAttribute("data-active-pane", "rows");
  await expect(page.locator("#inspector-sql-results .sql-result-card")).toBeVisible();
  await expect(page.locator("#inspector-sql-results tbody tr")).toHaveCount(5);

  if (desktop) {
    for (const [hoverTarget, header] of [
      ["#table-inspector-toggle", "#inspector > .ui-dock-pane__header"],
      ["#show-inspector-rows", "#inspector-rows-header"],
      ["#show-inspector-console", "#show-inspector-console"],
    ]) {
      await page.locator(hoverTarget).hover();
      await expect.poll(() => page.locator(header).evaluate(element => getComputedStyle(element).backgroundColor))
        .toBe("rgb(23, 29, 37)");
    }
  }

  await page.locator("#inspector-rows-header").click({ button: "right" });
  await expect(page.locator("#main-layout")).toHaveClass(/inspector-data-maximized/);
  await expect(page.locator("#inspector")).toBeHidden();
  await page.locator("#inspector-rows-header").click({ button: "right" });
  await expect(page.locator("#main-layout")).not.toHaveClass(/inspector-data-maximized/);
  if (desktop) await expect(page.locator("#inspector")).toBeVisible();
  else await expect(page.locator("#inspector")).toBeHidden();

  if (desktop) await page.locator("#table-inspector-toggle").click();
  else await page.locator("#minimize-inspector-data").click();
  await expect(page.locator("#inspector-data-workspace")).toBeHidden();
  await page.locator("#table-inspector-toggle").click();
  await expect(page.locator("#inspector")).toHaveAttribute("data-ui-dock-state", "minimized");
  await page.locator("#table-inspector-toggle").click({ button: "right" });
  await expect(page.locator("#inspector")).toHaveAttribute("data-ui-dock-state", "expanded");
  await expect(page.locator("#inspector-data-workspace")).toBeVisible();

  await expect(page.locator(".tool-rail #review-migration-button")).toBeEnabled();
  await page.locator("#objects-button").click();
  await expect(page.locator("#objects-dialog")).toBeVisible();
  await expect(page.locator("#objects-dialog #review-migration-button")).toHaveCount(0);
});
