import { randomUUID } from "node:crypto";
import { expect, request as requestFactory, test } from "@playwright/test";

const API = "/api/v1/schemii/workspaces";
const id = prefix => `${prefix}_${randomUUID().replaceAll("-", "")}`;
const cleanupIds = new Set();

test.afterEach(async () => {
  // A timed-out test can already have closed its request fixture. Cleanup owns
  // an independent context so failures do not leave test workspaces behind.
  const cleanup = await requestFactory.newContext({
    baseURL: process.env.SCHEMII_E2E_BASE_URL || "https://localhost:8001",
    ignoreHTTPSErrors: true,
  });
  try {
    for (const workspaceId of cleanupIds) {
      const response = await cleanup.get(`${API}/${workspaceId}`);
      if (response.status() !== 404) {
        const current = await json(response);
        expect((await cleanup.delete(`${API}/${workspaceId}?expectedRevision=${current.revision}`)).ok()).toBe(true);
      }
      cleanupIds.delete(workspaceId);
    }
  } finally {
    await cleanup.dispose();
  }
});

async function json(response) {
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

async function withDesign(request, run) {
  const workspace = await json(await request.post(API, {
    data: { name: `E2E inspector ${randomUUID()}` },
  }));
  cleanupIds.add(workspace.id);
  const columnId = id("column");
  const tables = ["editor_alpha", "editor_beta"].map((name, index) => ({
    id: id("table"), name,
    columns: [{ id: index === 0 ? columnId : id("column"), name: "quantity", dataType: "integer", nullable: true }],
    keys: [], checks: [], indexes: [],
  }));
  const design = await json(await request.get(`${API}/${workspace.id}/design`));
  await json(await request.put(`${API}/${workspace.id}/design`, {
    data: { expectedDesignRevision: design.revision, content: {
      types: [], tables, relationships: [], functions: [], views: [], triggers: [],
    } },
  }));
  await run({ workspace, columnId });
}

async function selectTable(page, name) {
  const inspector = page.locator("#inspector");
  if (await inspector.isVisible() && await inspector.getAttribute("data-ui-dock-state") === "expanded") {
    await page.locator("#table-inspector-toggle").click();
    await expect(inspector).toHaveAttribute("data-ui-dock-state", "minimized");
    await expect.poll(async () => {
      const pane = await inspector.boundingBox();
      const header = await inspector.locator(":scope > header").boundingBox();
      return pane && header ? Math.abs(pane.height - header.height) : Infinity;
    }).toBeLessThanOrEqual(4);
  }
  const card = page.locator(`.table-card[data-table-name="${name}"]`);
  await card.locator("header").click();
  if (await inspector.getAttribute("data-ui-dock-state") === "minimized") {
    await page.locator("#table-inspector-toggle").click();
  }
}

test("inspector preserves drafts until discard and removes unsaved columns locally", async ({ page, request }) => {
  await withDesign(request, async ({ workspace }) => {
    await page.goto(`/?workspace=${workspace.id}&layer=tables`);
    await selectTable(page, "editor_alpha");
    const name = page.locator("#inspector-table-name");
    const save = page.locator("#save-inspector-table-button");
    await expect(name).toHaveValue("editor_alpha");
    await name.fill("draft_name");
    await expect(save).toBeEnabled();
    await selectTable(page, "editor_beta");
    await expect(name).toHaveValue("draft_name");
    await expect(page.locator("#inspector-table-status")).toContainText("Unsaved changes");

    await page.locator("#discard-inspector-table-button").click();
    await expect(name).toHaveValue("editor_alpha");
    await expect(save).toBeDisabled();
    await selectTable(page, "editor_beta");
    await expect(name).toHaveValue("editor_beta");

    const rows = page.locator("#inspector-design-columns .design-column-row");
    await page.locator("#add-inspector-column-button").click();
    await expect(rows).toHaveCount(2);
    const draft = rows.last();
    await draft.getByRole("textbox", { name: "Column name", exact: true }).fill("scratch");
    await draft.getByRole("button", { name: "Remove scratch", exact: true }).click();
    await expect(rows).toHaveCount(1);
    await expect(save).toBeEnabled();
    await page.locator("#discard-inspector-table-button").click();
    await expect(save).toBeDisabled();
    await expect(rows).toHaveCount(1);
  });
});

test("saving inspector name and type edits survives undo redo and reload", async ({ page, request }) => {
  await withDesign(request, async ({ workspace, columnId }) => {
    await page.goto(`/?workspace=${workspace.id}&layer=tables`);
    await selectTable(page, "editor_alpha");
    const name = page.locator("#inspector-table-name");
    const type = page.locator(`#inspector-design-columns [data-design-column-id="${columnId}"]`).getByRole("combobox", { name: "PostgreSQL type for quantity", exact: true });
    await expect(type).toHaveValue("integer");
    await name.fill("editor_renamed");
    await type.fill("bigint");
    await page.getByRole("option").filter({ has: page.locator("strong", { hasText: /^bigint$/ }) }).click();
    await expect(type).toHaveValue("bigint");
    await page.locator("#save-inspector-table-button").click();

    const canvasType = page.locator(`.table-card [data-change-object-id="${columnId}"][data-change-field="dataType"]`);
    await expect(page.locator("#save-inspector-table-button")).toBeDisabled();
    await expect(page.locator('.table-card[data-table-name="editor_renamed"]')).toBeVisible();
    await expect(canvasType).toHaveText(/bigint/i);

    await page.locator("#undo-design-button").click();
    await expect(name).toHaveValue("editor_alpha");
    await expect(type).toHaveValue("integer");
    await expect(canvasType).toHaveText(/integer/i);
    await page.locator("#redo-design-button").click();
    await expect(name).toHaveValue("editor_renamed");
    await expect(type).toHaveValue("bigint");
    await expect(canvasType).toHaveText(/bigint/i);

    await page.reload();
    await expect(name).toHaveValue("editor_renamed");
    await expect(type).toHaveValue("bigint");
    await expect(page.locator("#save-inspector-table-button")).toBeDisabled();
  });
});

test("a rejected save retains reordered column drafts for retry and discard", async ({ page, request }) => {
  await withDesign(request, async ({ workspace }) => {
    await page.goto(`/?workspace=${workspace.id}&layer=tables`);
    await selectTable(page, "editor_alpha");
    const rows = page.locator("#inspector-design-columns .design-column-row");
    const names = rows.getByRole("textbox", { name: "Column name", exact: true });
    const save = page.locator("#save-inspector-table-button");
    const discard = page.locator("#discard-inspector-table-button");
    await page.locator("#add-inspector-column-button").click();
    await rows.last().getByRole("textbox", { name: "Column name", exact: true }).fill("scratch");
    await rows.last().locator("[data-sort-handle]").press("ArrowUp");
    await expect(names.nth(0)).toHaveValue("scratch");
    await expect(names.nth(1)).toHaveValue("quantity");

    // Reject only the first real browser save at the server boundary. Subsequent
    // saves go to the application, so retry verifies the retained draft end to end.
    await page.route(`${API}/${workspace.id}/design`, async route => {
      if (route.request().method() !== "PUT") return route.continue();
      await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({
        error: { code: "design_conflict", message: "The design changed while this draft was being saved." },
      }) });
    }, { times: 1 });
    await save.click();
    await expect(page.locator("#inspector-table-status")).toContainText("The design changed while this draft was being saved.");
    await expect(names.nth(0)).toHaveValue("scratch");
    await expect(names.nth(1)).toHaveValue("quantity");
    await expect(save).toBeEnabled();
    await expect(discard).toBeEnabled();
    await expect(page.locator('.table-card[data-table-name="editor_alpha"] .table-column')).toHaveCount(1);

    await save.click();
    await expect(save).toBeDisabled();
    const canvasColumns = page.locator('.table-card[data-table-name="editor_alpha"] .table-column');
    await expect(canvasColumns).toHaveCount(2);
    await expect(canvasColumns.nth(0)).toHaveAttribute("data-column-name", "scratch");
    await expect(canvasColumns.nth(1)).toHaveAttribute("data-column-name", "quantity");

    await rows.first().locator("[data-sort-handle]").press("ArrowDown");
    await expect(names.nth(0)).toHaveValue("quantity");
    await expect(save).toBeEnabled();
    await discard.click();
    await expect(names.nth(0)).toHaveValue("scratch");
    await expect(names.nth(1)).toHaveValue("quantity");
    await expect(save).toBeDisabled();
  });
});

test("create-table dialog shares column controls and opens the saved inspector", async ({ page, request }) => {
  await withDesign(request, async ({ workspace }) => {
    await page.goto(`/?workspace=${workspace.id}&layer=tables`);
    await page.locator("#create-table-button").click();
    const dialog = page.locator("#design-table-dialog");
    await expect(dialog).toBeVisible();
    const rows = dialog.locator("#design-columns .design-column-row");
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0).getByRole("textbox", { name: "Column name", exact: true })).toHaveValue("id");
    await expect(rows.nth(0).getByRole("combobox", { name: "PostgreSQL type for id", exact: true })).toHaveValue("bigint");
    await expect(rows.nth(1).getByRole("textbox", { name: "Column name", exact: true })).toHaveValue("name");
    await dialog.locator("#design-table-name").fill("editor_created");
    await dialog.locator("#save-design-table-button").click();
    await expect(dialog).not.toBeVisible();
    await selectTable(page, "editor_created");
    await expect(page.locator("#inspector-table-name")).toHaveValue("editor_created");
    await expect(page.locator("#inspector-design-columns .design-column-row")).toHaveCount(2);
    await expect(page.locator("#save-inspector-table-button")).toBeDisabled();
  });
});
