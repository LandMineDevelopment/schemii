import { randomUUID } from "node:crypto";

import { expect, test } from "@playwright/test";

const API_ROOT = "/api/v1/schemii/workspaces";
const designId = prefix => `${prefix}_${randomUUID().replaceAll("-", "")}`;

async function responseJson(response, context) {
  expect(response.ok(), `${context}: ${response.status()} ${await response.text()}`).toBe(true);
  return response.json();
}

async function saveDesign(request, workspaceId, content) {
  const current = await responseJson(await request.get(`${API_ROOT}/${workspaceId}/design`), "Read owned design");
  await responseJson(await request.put(`${API_ROOT}/${workspaceId}/design`, {
    data: { expectedDesignRevision: current.revision, content },
  }), "Save owned design");
}

async function openObjects(page) {
  await page.getByRole("button", { name: "Browse database objects", exact: true }).click();
  await expect(page.locator("#objects-dialog")).toBeVisible();
}

async function inspectView(page, viewId, count) {
  const row = page.locator(`#objects-list [data-change-object-id="${viewId}"]`);
  await row.locator("summary").click();
  await row.getByRole("button", { name: "Open view definition", exact: true }).click();
  await expect(page.locator("#objects-dialog")).toBeHidden();
  await expect(page.locator("#view-detail .query-story-head p")).toContainText(`producing ${count} columns`);
  await expect(page.locator("#view-detail .query-result.final .query-step-output")).toHaveCount(count);
}

test("desired ordinary and materialized column summaries agree with the inspector after edits and reload", async ({ page, request }, testInfo) => {
  let workspaceId = null;
  try {
    const workspace = await responseJson(await request.post(API_ROOT, {
      data: { name: `E2E view columns ${testInfo.project.name} ${randomUUID()}` },
    }), "Create owned view-column workspace");
    workspaceId = workspace.id;
    const content = {
      types: [], functions: [], triggers: [], relationships: [],
      tables: [
        {
          id: designId("table"), name: "customers",
          columns: [
            { id: designId("column"), name: "id", dataType: "integer", nullable: false },
            { id: designId("column"), name: "name", dataType: "text", nullable: false },
            { id: designId("column"), name: "region", dataType: "text", nullable: true },
          ],
          keys: [], checks: [], indexes: [],
        },
        {
          id: designId("table"), name: "orders",
          columns: [
            { id: designId("column"), name: "id", dataType: "integer", nullable: false },
            { id: designId("column"), name: "customer_id", dataType: "integer", nullable: false },
            { id: designId("column"), name: "amount", dataType: "numeric", nullable: false },
          ],
          keys: [], checks: [], indexes: [],
        },
      ],
      views: [
        {
          id: designId("view"), name: "customer_totals", kind: "view",
          definition: "SELECT c.id, c.name, c.region, COUNT(o.id) AS order_count, SUM(o.amount) AS total FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.id, c.name, c.region",
        },
        {
          id: designId("view"), name: "stored_customers", kind: "materialized_view",
          definition: "SELECT * FROM customers", populateOnCreate: false,
        },
      ],
    };
    await saveDesign(request, workspaceId, content);
    await page.goto(`/?workspace=${workspaceId}`);
    await expect(page.locator("#workspace-title")).toHaveText(workspace.name);
    await openObjects(page);
    const ordinary = page.locator(`#objects-list [data-change-object-id="${content.views[0].id}"]`);
    const materialized = page.locator(`#objects-list [data-change-object-id="${content.views[1].id}"]`);
    await expect(ordinary.locator("summary small")).toHaveText("5 columns");
    await expect(materialized.locator("summary small")).toHaveText("3 columns");
    await inspectView(page, content.views[0].id, 5);

    await page.reload();
    await expect(page.locator("#view-detail .query-story-head p")).toContainText("producing 5 columns");
    await openObjects(page);
    await expect(ordinary.locator("summary small")).toHaveText("5 columns");
    await inspectView(page, content.views[1].id, 3);

    // Refresh the mounted saved design after a source edit; SELECT * must expand
    // again instead of retaining the old projected output count.
    content.tables[0].columns.push({ id: designId("column"), name: "tier", dataType: "text", nullable: true });
    await saveDesign(request, workspaceId, content);
    await page.locator("#refresh-views-button").click();
    await expect(page.locator("#view-detail .query-story-head p")).toContainText("producing 4 columns");
    await openObjects(page);
    await expect(materialized.locator("summary small")).toHaveText("4 columns");
    await expect(ordinary.locator("summary small")).toHaveText("5 columns");
    await inspectView(page, content.views[0].id, 5);

    await page.getByRole("button", { name: "Edit customer_totals", exact: true }).click();
    const editor = page.locator("#design-view-dialog");
    await expect(editor).toBeVisible();
    await editor.locator("#design-view-definition").fill("SELECT id, name FROM customers");
    await editor.getByRole("button", { name: "Save view", exact: true }).click();
    await expect(editor).toBeHidden();
    await expect(page.locator("#view-detail .query-story-head p")).toContainText("producing 2 columns");
    await openObjects(page);
    await expect(ordinary.locator("summary small")).toHaveText("2 columns");
    await inspectView(page, content.views[0].id, 2);
    await page.reload();
    await expect(page.locator("#view-detail .query-story-head p")).toContainText("producing 2 columns");
    await openObjects(page);
    await expect(ordinary.locator("summary small")).toHaveText("2 columns");
    await expect(materialized.locator("summary small")).toHaveText("4 columns");
  } finally {
    if (workspaceId) {
      const response = await request.get(`${API_ROOT}/${workspaceId}`);
      if (response.status() !== 404) {
        const workspace = await responseJson(response, "Read owned workspace for cleanup");
        const deleted = await request.delete(`${API_ROOT}/${workspaceId}?expectedRevision=${workspace.revision}`);
        expect(deleted.ok() || deleted.status() === 404, "Delete owned view-column workspace").toBe(true);
      }
    }
  }
});
