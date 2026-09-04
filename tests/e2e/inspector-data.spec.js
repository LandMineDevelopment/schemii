import { expect, test } from "@playwright/test";

async function databaseDesignWorkspace(request) {
  const response = await request.get("/api/v1/schemii/workspaces");
  expect(response.ok()).toBe(true);
  const body = await response.json();
  const workspace = body.workspaces.find(item => (
    item.database === "schemii_migration_demo" && item.namespace === "public"
  ));
  expect(workspace, "the resettable demo workspace should target schemii_migration_demo.public").toBeTruthy();
  return workspace;
}

async function bookstoreWorkspace(request) {
  const response = await request.get("/api/v1/schemii/workspaces");
  expect(response.ok()).toBe(true);
  const body = await response.json();
  const workspace = body.workspaces.find(item => (
    item.database === "schemii_test" && item.namespace === "bookstore"
  ));
  expect(workspace, "the seeded bookstore workspace should be available").toBeTruthy();
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

test("inspector and full previews append rows automatically from the shared cursor pager", async ({ page, request }) => {
  const workspace = await bookstoreWorkspace(request);
  await page.goto(`/?workspace=${workspace.id}&layer=tables`);

  await page.locator('.table-card[data-table-name="orders"]').click({ force: true });
  await page.getByRole("button", { name: "Open table rows and console" }).click();
  const inspectorRows = page.locator("#inspector-rows-body tbody tr");
  await expect(inspectorRows).toHaveCount(100);
  await expect(page.getByRole("button", { name: "Load next page" })).toHaveCount(0);

  await page.locator("#inspector-rows-body").evaluate(container => {
    container.scrollTop = container.scrollHeight;
    container.dispatchEvent(new Event("scroll"));
  });
  await expect(inspectorRows).toHaveCount(200);
  await expect(page.locator("#inspector-rows-status")).toContainText("scroll for more");

  await page.getByRole("button", { name: "Open full row preview" }).click();
  const dialog = page.locator("#relation-preview-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.locator("tbody tr")).toHaveCount(200);
  await dialog.locator("#relation-preview-body").evaluate(container => {
    container.scrollTop = container.scrollHeight;
    container.dispatchEvent(new Event("scroll"));
  });
  await expect(dialog.locator("tbody tr")).toHaveCount(300);
  await expect(dialog.locator("#relation-preview-status")).toContainText("scroll for more");
});
