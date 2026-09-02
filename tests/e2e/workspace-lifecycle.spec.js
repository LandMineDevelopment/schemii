import { randomUUID } from "node:crypto";

import { expect, test } from "@playwright/test";

const API_ROOT = "/api/v1/schemii/workspaces";

function designId(prefix) {
  return `${prefix}_${randomUUID().replaceAll("-", "")}`;
}

function workspaceIdFrom(page) {
  return new URL(page.url()).searchParams.get("workspace");
}

function designFixture(tableName, viewName) {
  const tableId = designId("table");
  const idColumnId = designId("column");
  const totalColumnId = designId("column");
  return {
    types: [],
    tables: [
      {
        id: tableId,
        name: tableName,
        columns: [
          { id: idColumnId, name: "id", dataType: "bigint", nullable: false },
          { id: totalColumnId, name: "total", dataType: "numeric(12, 2)", nullable: false },
        ],
        keys: [],
        checks: [],
        indexes: [],
      },
    ],
    relationships: [],
    functions: [],
    views: [
      {
        id: designId("view"),
        name: viewName,
        kind: "view",
        definition: `SELECT id, total FROM ${tableName}`,
      },
    ],
    triggers: [],
  };
}

async function responseJson(response, context) {
  if (!response.ok()) {
    throw new Error(`${context} failed (${response.status()}): ${await response.text()}`);
  }
  return response.json();
}

async function seedDesign(request, workspaceId, content) {
  const current = await responseJson(
    await request.get(`${API_ROOT}/${workspaceId}/design`),
    "Read workspace design",
  );
  await responseJson(
    await request.put(`${API_ROOT}/${workspaceId}/design`, {
      data: { expectedDesignRevision: current.revision, content },
    }),
    "Seed workspace design",
  );
}

async function cleanupWorkspaces(request, name) {
  const listed = await responseJson(await request.get(API_ROOT), "List workspaces for cleanup");
  for (const item of listed.workspaces.filter(workspace => workspace.name === name)) {
    const currentResponse = await request.get(`${API_ROOT}/${item.id}`);
    if (currentResponse.status() === 404) continue;
    const current = await responseJson(currentResponse, "Read workspace for cleanup");
    const deleted = await request.delete(
      `${API_ROOT}/${current.id}?expectedRevision=${encodeURIComponent(current.revision)}`,
    );
    if (!deleted.ok() && deleted.status() !== 404) {
      throw new Error(`Delete test workspace failed (${deleted.status()}): ${await deleted.text()}`);
    }
  }
}

async function createDetachedWorkspace(page, workspaceName) {
  await page.goto("/");
  await expect(page).toHaveTitle("Schemii");
  await expect(page.locator("#workspace-title")).toHaveText("No workspace open");

  await page.getByRole("button", { name: "Open workspaces", exact: true }).click();
  const manager = page.locator("#workspaces-dialog");
  await expect(manager).toBeVisible();
  await manager.locator("#workspace-name").fill(workspaceName);
  await manager.locator("#workspace-mode").selectOption("detached");
  await manager.getByRole("button", { name: "Create empty design" }).click();

  await expect(manager).not.toBeVisible();
  await expect(page.locator("#workspace-title")).toHaveText(workspaceName);
  const workspaceId = workspaceIdFrom(page);
  expect(workspaceId).toMatch(/^ws_[0-9a-f]{32}$/);
  return workspaceId;
}

let workspaceNameToCleanup = null;

test.afterEach(async ({ request }) => {
  if (!workspaceNameToCleanup) return;
  const workspaceName = workspaceNameToCleanup;
  workspaceNameToCleanup = null;
  await cleanupWorkspaces(request, workspaceName);
});

test("detached workspace survives navigation and keeps its table/view surfaces assembled", async ({ page, request }, testInfo) => {
  const suffix = randomUUID().slice(0, 8);
  const workspaceName = `E2E ${testInfo.project.name} ${suffix}`;
  const tableName = `orders_${suffix.replaceAll("-", "")}`;
  const viewName = `order_totals_${suffix.replaceAll("-", "")}`;
  workspaceNameToCleanup = workspaceName;

  const workspaceId = await createDetachedWorkspace(page, workspaceName);

  await seedDesign(request, workspaceId, designFixture(tableName, viewName));

  await page.goto("/");
  await expect(page.locator("#workspace-title")).toHaveText("No workspace open");
  await page.getByRole("button", { name: "Workspaces", exact: true }).click();
  const manager = page.locator("#workspaces-dialog");
  const card = manager.locator(".manager-card").filter({ hasText: workspaceName });
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Open", exact: true }).click();

  await expect(page).toHaveURL(new RegExp(`[?&]workspace=${workspaceId}(?:&|$)`));
  await expect(page.locator("#workspace-title")).toHaveText(workspaceName);
  const tableCard = page.locator(".table-card").filter({ hasText: tableName });
  await expect(tableCard).toBeVisible();

  await page.reload();
  await expect(page.locator("#workspace-title")).toHaveText(workspaceName);
  await expect(tableCard).toBeVisible();

  await page.getByRole("button", { name: "Views", exact: true }).click();
  const viewButton = page.locator(".view-list-button").filter({ hasText: viewName });
  await expect(viewButton).toBeVisible();
  await viewButton.click();

  const detail = page.locator("#view-detail");
  await expect(detail.locator(".query-story h2")).toHaveText(viewName);
  await expect(detail.locator(".query-sql-panel")).toBeVisible();
  await expect(detail.locator(".query-story-loading")).toHaveCount(0);

  await page.evaluate(() => {
    const detailNode = document.querySelector("#view-detail");
    window.__schemiiE2eBlankViewSeen = false;
    window.__schemiiE2eViewObserver = new MutationObserver(() => {
      const text = detailNode?.textContent || "";
      if (text.includes("No view selected") || text.includes("Reading the query structure")) {
        window.__schemiiE2eBlankViewSeen = true;
      }
    });
    window.__schemiiE2eViewObserver.observe(detailNode, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  });

  let releaseDesignRequest;
  const designRequestPaused = new Promise(resolve => {
    releaseDesignRequest = resolve;
  });
  await page.route(new RegExp(`${API_ROOT}/${workspaceId}/design/snapshot$`), async route => {
    releaseDesignRequest(route);
  }, { times: 1 });

  const designResponse = page.waitForResponse(response => (
    response.request().method() === "GET"
      && response.url().endsWith(`${API_ROOT}/${workspaceId}/design/snapshot`)
  ));
  await page.locator("#refresh-views-button").click();
  const pausedRoute = await designRequestPaused;
  await expect(detail.locator(".query-story h2")).toHaveText(viewName);
  await expect(detail.locator(".query-sql-panel")).toBeVisible();
  await pausedRoute.continue();

  expect((await designResponse).ok()).toBe(true);
  await expect(detail.locator(".query-story h2")).toHaveText(viewName);
  await expect.poll(() => page.evaluate(() => window.__schemiiE2eBlankViewSeen)).toBe(false);
  expect(new URL(page.url()).searchParams.get("view")).toBe(viewName);
});

test("redo restores a removed view as one fully assembled analyzed surface", async ({ page, request }, testInfo) => {
  const suffix = randomUUID().slice(0, 8);
  const workspaceName = `E2E history ${testInfo.project.name} ${suffix}`;
  const tableName = `ledger_${suffix.replaceAll("-", "")}`;
  const viewName = `ledger_totals_${suffix.replaceAll("-", "")}`;
  workspaceNameToCleanup = workspaceName;

  const workspaceId = await createDetachedWorkspace(page, workspaceName);
  const complete = designFixture(tableName, viewName);
  await seedDesign(request, workspaceId, { ...complete, views: [] });
  await seedDesign(request, workspaceId, complete);
  await page.reload();

  await page.getByRole("button", { name: "Views", exact: true }).click();
  const viewButton = page.locator(".view-list-button").filter({ hasText: viewName });
  await expect(viewButton).toBeVisible();
  await viewButton.click();
  const detail = page.locator("#view-detail");
  await expect(detail.locator(".query-story h2")).toHaveText(viewName);
  await expect(detail.locator(".query-sql-panel")).toBeVisible();

  await page.locator("#undo-design-button").click();
  await expect(viewButton).toHaveCount(0);
  await expect(detail).toContainText("No view selected");
  await expect(page.locator("#redo-design-button")).toBeEnabled();

  await page.evaluate(() => {
    const detailNode = document.querySelector("#view-detail");
    window.__schemiiE2eRedoBlankSeen = false;
    window.__schemiiE2eRedoObserver = new MutationObserver(() => {
      const text = detailNode?.textContent || "";
      if (text.includes("No view selected") || text.includes("Reading the query structure")) {
        window.__schemiiE2eRedoBlankSeen = true;
      }
    });
    window.__schemiiE2eRedoObserver.observe(detailNode, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  });

  await page.locator("#redo-design-button").click();
  await expect(viewButton).toBeVisible();
  await expect(detail.locator(".query-story h2")).toHaveText(viewName);
  await expect(detail.locator(".query-sql-panel")).toBeVisible();
  await expect(detail.locator(".query-story-loading")).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => window.__schemiiE2eRedoBlankSeen)).toBe(false);
  expect(new URL(page.url()).searchParams.get("view")).toBe(viewName);
});
