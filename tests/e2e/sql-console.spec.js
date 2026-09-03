import { expect, test } from "@playwright/test";

async function databaseWorkspace(request) {
  const response = await request.get("/api/v1/schemii/workspaces");
  expect(response.ok()).toBe(true);
  const body = await response.json();
  const workspace = body.workspaces.find(item => item.connectionId && item.database);
  expect(workspace, "the development seed should provide a database-backed workspace").toBeTruthy();
  return workspace;
}

test("read-only Console runs a query and renders PostgreSQL column types", async ({ page, request }) => {
  const workspace = await databaseWorkspace(request);
  await page.goto(`/?workspace=${workspace.id}&layer=sql`);

  await expect(page.getByRole("heading", { name: "Read-only SQL Console" })).toBeVisible();
  const editor = page.getByRole("textbox", { name: "Unsaved SQL draft" });
  await expect(editor).toHaveAttribute("rows", "14");
  await expect(editor).toHaveAttribute("placeholder", /Write a read-only PostgreSQL query/);
  const editorBox = await editor.boundingBox();
  expect(editorBox?.height).toBeGreaterThanOrEqual(260);
  const panelBox = await page.locator(".sql-editor-panel").boundingBox();
  const runBox = await page.getByRole("button", { name: "Run query" }).boundingBox();
  expect(panelBox?.height).toBeGreaterThanOrEqual((editorBox?.height ?? 0) + 90);
  expect(editorBox?.y + editorBox?.height).toBeLessThanOrEqual(
    (panelBox?.y ?? 0) + (panelBox?.height ?? 0),
  );
  expect(runBox?.y + runBox?.height).toBeLessThanOrEqual(
    (panelBox?.y ?? 0) + (panelBox?.height ?? 0),
  );
  await editor.fill("SELECT current_database() AS database, 42::integer AS answer;");
  await page.getByRole("button", { name: "Run query" }).click();

  const result = page.locator(".sql-result-card");
  await expect(result).toBeVisible();
  await expect(result.locator("thead")).toContainText("database");
  await expect(result.locator("thead")).toContainText("name");
  await expect(result.locator("thead")).toContainText("answer");
  await expect(result.locator("thead")).toContainText("integer");
  await expect(result.locator("tbody")).toContainText(workspace.database);
  await expect(result.locator("tbody")).toContainText("42");
});

test("read-only Console explains rejected mutation without clearing the draft", async ({ page, request }) => {
  const workspace = await databaseWorkspace(request);
  await page.goto(`/?workspace=${workspace.id}&layer=sql`);

  const editor = page.getByRole("textbox", { name: "Unsaved SQL draft" });
  const draft = "DELETE FROM definitely_not_a_console_target";
  await editor.fill(draft);
  await page.getByRole("button", { name: "Run query" }).click();

  await expect(page.locator("#sql-results")).toContainText("Query could not start");
  await expect(page.locator("#sql-results")).toContainText("Read-only Console accepts");
  await expect(editor).toHaveValue(draft);
});
