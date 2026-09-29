import { expect, test } from "@playwright/test";

test("zero-change migration review gives Apply a clear disabled appearance", async ({ page, request }) => {
  const response = await request.get("/api/v1/schemii/workspaces");
  expect(response.ok()).toBe(true);
  const { workspaces } = await response.json();
  const workspace = workspaces.find(item => item.database === "schemii_migration_demo" && item.namespace === "public");
  expect(workspace, "the resettable migration demo workspace is required").toBeTruthy();

  await page.goto(`/?workspace=${workspace.id}&layer=tables`);
  await page.getByRole("button", { name: "Review migration" }).click();
  const dialog = page.locator("#migration-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("PostgreSQL is up to date");
  await expect(dialog).toContainText("Planned changes");

  const apply = dialog.getByRole("button", { name: "Apply migration" });
  await expect(apply).toBeDisabled();
  const colors = await apply.evaluate(button => {
    const disabled = getComputedStyle(button);
    const result = { disabledBackground: disabled.backgroundColor, disabledOpacity: disabled.opacity };
    button.disabled = false;
    result.enabledBackground = getComputedStyle(button).backgroundColor;
    button.disabled = true;
    return result;
  });
  expect(colors).toEqual({
    disabledBackground: "rgb(29, 38, 48)",
    disabledOpacity: "1",
    enabledBackground: "rgb(244, 185, 66)",
  });
});
