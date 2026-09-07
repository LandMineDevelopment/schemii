import { randomUUID } from "node:crypto";
import { test, expect } from "@playwright/test";

test("workspace labels rename without changing the design and survive reload", async ({ page, request }) => {
  const base = "/api/v1/schemii/workspaces";
  const name = `Rename ${randomUUID()}`;
  const created = await request.post(base, { data: { name } });
  expect(created.ok()).toBeTruthy();
  const workspace = await created.json();
  const path = `${base}/${workspace.id}`;
  try {
    await page.goto(`/?workspace=${workspace.id}`);
    await expect(page.locator("#workspace-title")).toHaveText(name);
    await page.getByRole("button", { name: "Workspaces", exact: true }).click();
    const manager = page.locator("#workspaces-dialog");
    await manager.getByRole("button", { name: `Rename ${name}`, exact: true }).click();
    const input = manager.getByRole("textbox", { name: "Workspace name", exact: true });
    await expect(input).toHaveValue(name);
    await input.fill("Cancelled rename");
    await manager.getByRole("button", { name: "Cancel rename", exact: true }).click();
    expect((await (await request.get(path)).json()).name).toBe(name);
    await manager.getByRole("button", { name: `Rename ${name}`, exact: true }).click();
    await input.fill(`${name} updated`);
    await page.screenshot({ path: "/tmp/schemii-workspace-rename.png" });
    await manager.getByRole("button", { name: "Save workspace name", exact: true }).click();
    await expect(manager.getByText(`${name} updated`, { exact: true })).toBeVisible();
    await expect(page.locator("#workspace-title")).toHaveText(`${name} updated`);
    await page.reload();
    await expect(page.locator("#workspace-title")).toHaveText(`${name} updated`);
  } finally {
    const current = await (await request.get(path)).json();
    expect((await request.delete(`${path}?expectedRevision=${current.revision}`)).ok()).toBeTruthy();
  }
});
