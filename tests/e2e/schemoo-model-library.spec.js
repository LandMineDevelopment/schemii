import { expect, test } from "@playwright/test";

async function fixture(page, { conflict = false, blocked = false, openCancelled = false, dashboards = [], dependencyFailure = false, racedDashboard = null } = {}) {
  let models = [{ id: "model_fixture", name: "Temporary staffing model", database: "organization", namespace: "public", revision: 7, layoutRevision: 3, exploreRevision: 4 }];
  const deletions = [];
  await page.route("**/api/v1/connections", route => route.fulfill({ json: { connections: [] } }));
  await page.route("**/api/v1/schemoo/models", route => route.fulfill({ json: { models } }));
  await page.route("**/api/v1/schemoo/models/model_fixture/dependencies", route =>
    route.fulfill(dependencyFailure
      ? { status: 503, json: { error: { message: "Dependency storage unavailable" } } }
      : { json: { dashboards } }));
  await page.route("**/api/v1/schemoo/models/model_fixture?*", route => {
    expect(route.request().method()).toBe("DELETE");
    deletions.push(new URL(route.request().url()).searchParams.get("expected_revision"));
    if (racedDashboard) return route.fulfill({ status: 409, json: { error: {
      code: "model_in_use", message: "Model has dependent dashboards", details: { dashboards: [racedDashboard] },
    } } });
    if (conflict && deletions.length === 1) {
      models[0].revision = 8;
      return route.fulfill({ status: 409, json: { error: { code: "model_revision_conflict", message: "Model changed" } } });
    }
    models = [];
    return route.fulfill({ status: 204 });
  });
  await page.route("**/model-library-fixture", route => route.fulfill({ contentType: "text/html", body: `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><script type="importmap">{"imports":{"#common/":"/assets/common/"}}</script><link rel="stylesheet" href="/assets/common/ui.css"><link rel="stylesheet" href="/schemoo-assets/prototype.css"></head><body><button id="open">Open models</button><dialog id="model-library"><header class="dock-header"><h2>Semantic models</h2></header><div id="library-content"></div></dialog><script type="module">import { openModelLibrary } from '/schemoo-assets/model-library.js'; window.deletedModels=[]; window.openedModels=[]; document.querySelector('#open').onclick=()=>openModelLibrary(async(id)=>{window.openedModels.push(id); return ${!openCancelled};},{onDeleted:async(model)=>window.deletedModels.push(model.id),canDelete:()=>${!blocked}});</script></body></html>` }));
  await page.goto("/model-library-fixture");
  await page.getByRole("button", { name: "Open models", exact: true }).click();
  await expect(page.locator(".model-list-row")).toHaveCount(1);
  return deletions;
}

test("model picker confirms exact model, supports cancel and Escape, then deletes once", async ({ page }) => {
  const deletions = await fixture(page);
  const remove = page.getByRole("button", { name: "Delete model Temporary staffing model", exact: true });
  await remove.click();
  const dialog = page.getByRole("dialog", { name: "Delete model?", exact: true });
  await expect(dialog).toContainText("Are you sure you want to delete “Temporary staffing model”?");
  await expect(dialog).toContainText("PostgreSQL tables and data are not changed");
  await expect(dialog.getByRole("button", { name: "Cancel", exact: true })).toBeFocused();
  const box = await dialog.boundingBox(), viewport = page.viewportSize();
  expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
  expect(box.y).toBeGreaterThanOrEqual(0); expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
  await page.screenshot({ path: `artifacts/model-delete-${test.info().project.name}.png` });
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toHaveCount(0); await expect(remove).toBeFocused();
  expect(deletions).toEqual([]);
  await remove.click(); await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0); expect(deletions).toEqual([]);
  await remove.click();
  await expect(dialog.getByRole("button", { name: "Delete model", exact: true })).toBeEnabled();
  // Two synchronous activations cannot submit duplicate destructive requests.
  await dialog.getByRole("button", { name: "Delete model", exact: true }).evaluate(button => { button.click(); button.click(); });
  await expect(dialog).toHaveCount(0); await expect(page.locator(".model-list-row")).toHaveCount(0);
  expect(deletions).toEqual(["7"]);
  expect(await page.evaluate(() => window.deletedModels)).toEqual(["model_fixture"]);
  await expect(page.locator("#library-content [role=status]")).toContainText("Deleted “Temporary staffing model”");
});

test("stale model deletion requires reviewing refreshed revision, never automatic retry", async ({ page }) => {
  const deletions = await fixture(page, { conflict: true });
  const remove = page.getByRole("button", { name: "Delete model Temporary staffing model", exact: true });
  await remove.click();
  const dialog = page.getByRole("dialog", { name: "Delete model?", exact: true });
  await dialog.getByRole("button", { name: "Delete model", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("refresh the model list");
  await expect(dialog.getByRole("button", { name: "Delete model", exact: true })).toBeDisabled();
  expect(deletions).toEqual(["7"]);
  expect(await page.evaluate(() => window.deletedModels)).toEqual([]);
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("button", { name: "Refresh models", exact: true }).click();
  await expect(page.locator(".model-option small")).toContainText("revision 8");
  await remove.click(); await dialog.getByRole("button", { name: "Delete model", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(deletions).toEqual(["7", "8"]);
});

test("model picker honors mutation guard and cancelled model selection", async ({ page }) => {
  const deletions = await fixture(page, { blocked: true, openCancelled: true });
  await expect(page.getByRole("button", { name: "Delete model Temporary staffing model", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Duplicate model Temporary staffing model", exact: true })).toBeDisabled();
  await page.locator(".model-option").click();
  await expect(page.locator("#model-library")).toBeVisible();
  expect(await page.evaluate(() => window.openedModels)).toEqual(["model_fixture"]);
  expect(deletions).toEqual([]);
});

test("duplicate naming supports cancel and submits once with all saved revisions", async ({ page }) => {
  await fixture(page);
  const requests = [];
  await page.route("**/api/v1/schemoo/models/model_fixture/duplicate", route => {
    requests.push(route.request().postDataJSON());
    return route.fulfill({ status: 201, json: { id: "model_copy" } });
  });
  const duplicate = page.getByRole("button", { name: "Duplicate model Temporary staffing model", exact: true });
  const dialog = page.getByRole("dialog", { name: "Duplicate model", exact: true });
  await duplicate.click();
  await expect(dialog.getByRole("textbox", { name: "New model name" })).toHaveValue("Temporary staffing model copy");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(duplicate).toBeFocused();
  await duplicate.click(); await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0); expect(requests).toEqual([]);
  await duplicate.click();
  await dialog.getByRole("button", { name: "Create copy", exact: true }).evaluate(button => { button.click(); button.click(); });
  await expect(dialog).toHaveCount(0);
  await expect(page.locator("#model-library")).not.toBeVisible();
  expect(requests).toEqual([{ name: "Temporary staffing model copy", expectedRevision: 7, expectedLayoutRevision: 3, expectedExploreRevision: 4 }]);
  expect(await page.evaluate(() => window.openedModels)).toEqual(["model_copy"]);
});

test("failed duplication requires refresh instead of blindly repeating a create", async ({ page }) => {
  await fixture(page);
  let requests = 0;
  await page.route("**/api/v1/schemoo/models/model_fixture/duplicate", route => {
    requests += 1;
    return route.fulfill({ status: 409, json: { error: { code: "model_revision_conflict", message: "Model changed" } } });
  });
  await page.getByRole("button", { name: "Duplicate model Temporary staffing model", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Duplicate model", exact: true });
  await dialog.getByRole("button", { name: "Create copy", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("refresh the model list");
  await expect(dialog.getByRole("button", { name: "Create copy", exact: true })).toBeDisabled();
  expect(requests).toBe(1);
  expect(await page.evaluate(() => window.openedModels)).toEqual([]);
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.locator("#model-library")).toBeVisible();
});

test("dependent dashboards block deletion, remain readable and link to exact dashboard", async ({ page }) => {
  const dashboards = Array.from({ length: 20 }, (_, index) => ({ id: `dashboard_${index}`, name: `Current strength dashboard ${index + 1}` }));
  dashboards[0].name = '<img src=x onerror=alert(1)> Staffing & readiness';
  const deletions = await fixture(page, { dashboards });
  const remove = page.getByRole("button", { name: "Delete model Temporary staffing model", exact: true });
  await remove.click();
  const dialog = page.getByRole("dialog", { name: "Delete model?", exact: true });
  await expect(dialog.getByRole("status")).toContainText("20 saved dashboards depend on this model. Deletion is blocked.");
  await expect(dialog.getByRole("button", { name: "Delete model", exact: true })).toBeDisabled();
  await expect(dialog.getByRole("list", { name: "Dependent dashboards" }).getByRole("listitem")).toHaveCount(20);
  const first = dialog.getByRole("link").first();
  await expect(first).toHaveText(dashboards[0].name);
  await expect(first).toHaveAttribute("href", "/schemer?dashboard=dashboard_0");
  await expect(first).toHaveAttribute("target", "_blank");
  await expect(dialog.locator("img")).toHaveCount(0);
  const box = await dialog.boundingBox(), viewport = page.viewportSize();
  expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
  expect(box.y).toBeGreaterThanOrEqual(0); expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
  await expect(dialog.getByRole("button", { name: "Close", exact: true })).toBeInViewport();
  await first.focus();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("link").nth(1)).toBeFocused();
  await page.screenshot({ path: `artifacts/model-dependencies-${test.info().project.name}.png` });
  await dialog.getByRole("link").last().focus();
  await expect(dialog.getByRole("link").last()).toBeInViewport();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0); await expect(remove).toBeFocused();
  expect(deletions).toEqual([]);
  expect(await page.evaluate(() => window.deletedModels)).toEqual([]);
});

test("failed dependency checks prevent deletion and allow a fresh review", async ({ page }) => {
  const deletions = await fixture(page, { dependencyFailure: true });
  const remove = page.getByRole("button", { name: "Delete model Temporary staffing model", exact: true });
  await remove.click();
  const dialog = page.getByRole("dialog", { name: "Delete model?", exact: true });
  await expect(dialog.getByRole("alert")).toContainText("Close and reopen this dialog to try again");
  await expect(dialog.getByRole("button", { name: "Delete model", exact: true })).toBeDisabled();
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await page.route("**/api/v1/schemoo/models/model_fixture/dependencies", route => route.fulfill({ json: { dashboards: [] } }));
  await remove.click();
  await expect(dialog.getByRole("status")).toHaveText("No saved dashboards depend on this model.");
  await expect(dialog.getByRole("button", { name: "Delete model", exact: true })).toBeEnabled();
  expect(deletions).toEqual([]);
});

test("a dashboard created after review is shown without deleting or retrying", async ({ page }) => {
  const deletions = await fixture(page, { racedDashboard: { id: "dashboard_new", name: "New staffing dashboard" } });
  await page.getByRole("button", { name: "Delete model Temporary staffing model", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Delete model?", exact: true });
  await dialog.getByRole("button", { name: "Delete model", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("Nothing was deleted");
  await expect(dialog.getByRole("status")).toContainText("1 saved dashboard depends on this model");
  await expect(dialog.getByRole("link", { name: "New staffing dashboard (opens in a new tab)", exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Delete model", exact: true })).toBeDisabled();
  await expect(dialog.getByRole("button", { name: "Close", exact: true })).toBeFocused();
  expect(deletions).toEqual(["7"]);
  expect(await page.evaluate(() => window.deletedModels)).toEqual([]);
  await expect(page.locator(".model-list-row")).toHaveCount(1);
});

test("dependency review can be cancelled while loading without reopening the dialog", async ({ page }) => {
  const deletions = await fixture(page);
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  await page.route("**/api/v1/schemoo/models/model_fixture/dependencies", async route => {
    await pending;
    await route.fulfill({ json: { dashboards: [] } }).catch(() => {});
  });
  const remove = page.getByRole("button", { name: "Delete model Temporary staffing model", exact: true });
  await remove.click();
  const dialog = page.getByRole("dialog", { name: "Delete model?", exact: true });
  await expect(dialog.getByRole("status")).toContainText("Checking dependent dashboards");
  await expect(dialog.getByRole("button", { name: "Delete model", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape"); release();
  await expect(dialog).toHaveCount(0); await expect(remove).toBeFocused();
  expect(deletions).toEqual([]);
});
