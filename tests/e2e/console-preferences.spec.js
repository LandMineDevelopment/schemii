import { expect, test } from "@playwright/test";

test("result preferences save durably without unlocking the console", async ({ page, request }) => {
  const path = "/api/v1/schemii/console/settings";
  const original = await (await request.get(path)).json();
  const workspaceList = await (await request.get("/api/v1/schemii/workspaces")).json();
  const workspace = workspaceList.workspaces.find(item => item.connectionId && item.database);
  expect(workspace).toBeTruthy();
  const pageSize = original.rowPageSize === 25 ? 10 : 25;
  try {
    await page.goto(`/?workspace=${workspace.id}&layer=sql`);
    await page.locator("#show-sql-results").click();
    const preferences = page.locator(".sql-results-panel .sql-console-preferences");
    await preferences.locator("summary").click();
    const rows = preferences.getByRole("spinbutton", { name: "Rows per page" });
    await expect(rows).toHaveValue(String(original.rowPageSize));
    await rows.fill(String(pageSize));
    await preferences.getByRole("button", { name: "Save result preferences" }).click();
    await expect(preferences.getByRole("status")).toContainText("Saved for future queries");
    await page.reload();
    await page.locator("#show-sql-results").click();
    await preferences.locator("summary").click();
    await expect(rows).toHaveValue(String(pageSize));
    const saved = await (await request.get(path)).json();
    expect(saved.rowPageSize).toBe(pageSize);
    expect(saved.writeIntent).toBe(false);
    expect(saved.defaultMode).toBe("managed_read");
    await page.screenshot({ path: `artifacts/console-preferences-${test.info().project.name}.png` });
  } finally {
    const current = await (await request.get(path)).json();
    const restored = await request.put(path, { data: { expectedRevision: current.revision, rowPageSize: original.rowPageSize } });
    expect(restored.ok()).toBe(true);
  }
});
