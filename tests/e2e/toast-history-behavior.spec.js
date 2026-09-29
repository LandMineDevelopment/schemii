import { randomUUID } from "node:crypto";
import { expect, request as requestFactory, test } from "@playwright/test";

const API = "/api/v1/schemii/workspaces";
const cleanupIds = new Set();

async function json(response) {
  expect(response.ok(), `Fixture request failed: HTTP ${response.status()}`).toBe(true);
  return response.json();
}

test.afterEach(async ({}, testInfo) => {
  if (!cleanupIds.size) return;
  // The test's request fixture may already be closed after a timeout. Reuse its
  // private authenticated storage state, never a fresh unauthenticated context.
  const baseURL = testInfo.project.use.baseURL;
  const cleanup = await requestFactory.newContext({
    baseURL, ignoreHTTPSErrors: true, storageState: testInfo.project.use.storageState,
    extraHTTPHeaders: { Origin: new URL(baseURL).origin },
  });
  try {
    for (const workspaceId of cleanupIds) {
      const response = await cleanup.get(`${API}/${workspaceId}`);
      if (response.status() !== 404) {
        const current = await json(response);
        const deleted = await cleanup.delete(`${API}/${workspaceId}?expectedRevision=${current.revision}`);
        expect(deleted.status(), "delete only the owned history fixture").toBe(204);
      }
      cleanupIds.delete(workspaceId);
    }
  } finally {
    await cleanup.dispose();
  }
});

async function historyFixture(request) {
  const workspace = await json(await request.post(API, {
    data: { name: `E2E history confirmation ${randomUUID()}` },
  }));
  cleanupIds.add(workspace.id);
  const content = {
    types: [], tables: [{ id: `table_${randomUUID().replaceAll("-", "")}`, name: "history_people",
      columns: [{ id: `column_${randomUUID().replaceAll("-", "")}`, name: "quantity", dataType: "integer", nullable: true }],
      keys: [], checks: [], indexes: [] }],
    relationships: [], functions: [], views: [], triggers: [],
  };
  let design = await json(await request.get(`${API}/${workspace.id}/design`));
  design = await json(await request.put(`${API}/${workspace.id}/design`, {
    data: { expectedDesignRevision: design.revision, content },
  }));
  const renamed = structuredClone(content);
  renamed.tables[0].columns[0].name = "amount";
  await json(await request.put(`${API}/${workspace.id}/design`, {
    data: { expectedDesignRevision: design.revision, content: renamed },
  }));
  return workspace;
}

async function openHistoryFixture(page, workspace) {
  await page.goto(`/?workspace=${workspace.id}&layer=tables`);
  await expect(page.locator("#workspace-title")).toHaveText(workspace.name);
  await expect(page.locator("#undo-design-button")).toBeEnabled();
  await page.locator('.table-card[data-table-name="history_people"] header').click();
  if (await page.locator("#inspector").getAttribute("data-ui-dock-state") === "minimized") {
    await page.locator("#table-inspector-toggle").click();
  }
  const field = page.locator("#inspector-design-columns").getByRole("textbox", { name: "Column name", exact: true });
  await expect(field).toHaveValue("amount");
  return field;
}

test("visible status toasts leave desktop and mobile tools clear and clickable", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#runtime-status")).not.toHaveText("Checking active server");
  const toast = page.locator("#toast");
  await toast.evaluate(node => {
    node.textContent = "Owned status fixture · the workspace operation completed successfully.";
    node.hidden = false;
  });
  await expect(toast).toBeVisible();
  await expect(toast).toHaveCSS("pointer-events", "none");
  const geometry = await toast.evaluate(node => {
    const bounds = node.getBoundingClientRect();
    const overlap = other => {
      const rect = other.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && bounds.left < rect.right && bounds.right > rect.left
        && bounds.top < rect.bottom && bounds.bottom > rect.top;
    };
    return {
      withinViewport: bounds.left >= 0 && bounds.top >= 0 && bounds.right <= innerWidth && bounds.bottom <= innerHeight,
      overlapsTools: [...document.querySelectorAll('.topbar button, .topbar summary, .layer-switch button, #tool-rail button:not([hidden])')]
        .some(overlap),
    };
  });
  expect(geometry).toEqual({ withinViewport: true, overlapsTools: false });
  await page.getByRole("button", { name: "Views", exact: true }).click();
  await expect(page.getByRole("button", { name: "Views", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.locator('summary[aria-label="Help"]').click();
  await expect(page.getByRole("button", { name: "Quick start guide", exact: true })).toBeVisible();
  await expect(toast).toBeVisible();
});

test("deferred matching history retains mounted detail, focus, selection and the existing draft contract", async ({ page, request }) => {
  const workspace = await historyFixture(request);
  const field = await openHistoryFixture(page, workspace);
  for (const direction of ["undo", "redo"]) {
    let release;
    const held = new Promise(resolve => { release = resolve; });
    let received;
    const requested = new Promise(resolve => { received = resolve; });
    const routePath = `**/api/v1/schemii/workspaces/${workspace.id}/design/${direction}`;
    const handler = async route => {
      const response = await route.fetch();
      received();
      await held;
      await route.fulfill({ response });
    };
    await page.route(routePath, handler);
    try {
      await page.locator(`#${direction}-design-button`).click();
      const optimisticValue = direction === "undo" ? "quantity" : "amount";
      await expect(field).toHaveValue(optimisticValue);
      await requested;
      // Undo checks an untouched field, which a redundant repaint would replace.
      // Redo also exercises the current table editor's pending-response draft
      // retention. This does not promise a new server/client conflict policy.
      const value = direction === "redo" ? "amount_draft" : optimisticValue;
      if (direction === "redo") await field.fill(value);
      await field.focus();
      const mounted = await field.elementHandle();
      await mounted.evaluate(input => input.setSelectionRange(1, 5, "forward"));
      await expect(field).toBeFocused();
      release();
      await expect(page.locator("#toast")).toContainText(`${direction} complete.`);
      expect(await mounted.evaluate(input => ({
        connected: input.isConnected, focused: document.activeElement === input,
        value: input.value, start: input.selectionStart, end: input.selectionEnd, direction: input.selectionDirection,
      }))).toEqual({ connected: true, focused: true, value, start: 1, end: 5, direction: "forward" });
      if (direction === "redo") {
        await expect(page.locator("#inspector-table-status")).toContainText("Unsaved changes");
        await expect(field).toHaveValue("amount_draft");
      } else {
        await expect(page.locator("#redo-design-button")).toBeEnabled();
      }
      await mounted.dispose();
    } finally {
      release();
      await page.unroute(routePath, handler);
    }
  }
});

test("history conflict rolls back the preview then reloads the authoritative design", async ({ page, request }) => {
  const workspace = await historyFixture(request);
  const field = await openHistoryFixture(page, workspace);
  let release;
  const held = new Promise(resolve => { release = resolve; });
  let reloads = 0;
  await page.route(`**/api/v1/schemii/workspaces/${workspace.id}/design/snapshot`, async route => {
    reloads++;
    await route.continue();
  });
  await page.route(`**/api/v1/schemii/workspaces/${workspace.id}/design/undo`, async route => {
    await held;
    await route.fulfill({ status: 409, json: { error: {
      code: "design_changed", message: "Owned history conflict fixture", retryable: false, details: {},
    } } });
  });
  try {
    await page.locator("#undo-design-button").click();
    await expect(field).toHaveValue("quantity");
    release();
    await expect(page.locator("#toast.error")).toContainText("Owned history conflict fixture");
    await expect(field).toHaveValue("amount");
    await expect(page.locator("#undo-design-button")).toBeEnabled();
    expect(reloads).toBe(1);
  } finally {
    release();
  }
});
