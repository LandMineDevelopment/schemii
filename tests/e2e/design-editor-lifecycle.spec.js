import { randomUUID } from "node:crypto";

import { expect, test } from "@playwright/test";

const API_ROOT = "/api/v1/schemii/workspaces";

function designId(prefix) {
  return `${prefix}_${randomUUID().replaceAll("-", "")}`;
}

async function responseJson(response, context) {
  if (!response.ok()) {
    throw new Error(`${context} failed (${response.status()}): ${await response.text()}`);
  }
  return response.json();
}

async function createWorkspace(request) {
  return responseJson(await request.post(API_ROOT, {
    data: { name: `E2E draft analysis ${randomUUID()}` },
  }), "Create editor lifecycle workspace");
}

async function seedWorkspace(request, workspaceId) {
  const current = await responseJson(await request.get(`${API_ROOT}/${workspaceId}/design`), "Read empty design");
  const tableId = designId("table");
  await responseJson(await request.put(`${API_ROOT}/${workspaceId}/design`, {
    data: {
      expectedDesignRevision: current.revision,
      content: {
        types: [],
        tables: [{
          id: tableId,
          name: "draft_analysis_orders",
          columns: [{ id: designId("column"), name: "id", dataType: "bigint", nullable: false }],
          keys: [],
          checks: [],
          indexes: [],
        }],
        relationships: [],
        functions: [],
        views: [],
        triggers: [],
      },
    },
  }), "Seed editor lifecycle design");
}

const editors = [
  {
    name: "view",
    endpoint: "view-analysis",
    dialog: "#design-view-dialog",
    definition: "#design-view-definition",
    preview: "#design-view-preview",
    initialDefinition: null,
    async open(page) {
      const create = page.locator("#create-view-button");
      await expect(create).toBeEnabled();
      await create.click();
    },
  },
  {
    name: "type",
    endpoint: "type-analysis",
    dialog: "#design-type-dialog",
    definition: "#design-type-definition",
    preview: "#design-type-preview",
    initialDefinition: null,
    async open(page) {
      await page.locator("#types-button").click();
      await page.locator("#create-type-button").click();
    },
  },
  {
    name: "routine",
    endpoint: "routine-analysis",
    dialog: "#design-routine-dialog",
    definition: "#design-routine-definition",
    preview: "#design-routine-preview",
    initialDefinition: "CREATE FUNCTION analysis_baseline() RETURNS integer LANGUAGE SQL AS $$ SELECT 1 $$;",
    async open(page) {
      await page.locator("#functions-button").click();
      await page.locator("#create-function-button").click();
    },
  },
  {
    name: "trigger",
    endpoint: "trigger-analysis",
    dialog: "#design-trigger-dialog",
    definition: "#design-trigger-definition",
    preview: "#design-trigger-preview",
    initialDefinition: null,
    async open(page) {
      await page.locator("#objects-button").click();
      await page.locator("#create-trigger-button").click();
    },
  },
];

function deferred() {
  let resolve;
  const promise = new Promise(resolvePromise => { resolve = resolvePromise; });
  return { promise, resolve };
}

test("all four design editors fence rapid, stale, and post-close draft analyses", async ({ page, request }) => {
  let workspaceId = null;
  const releasePendingResponses = [];

  try {
    const workspace = await createWorkspace(request);
    workspaceId = workspace.id;
    await seedWorkspace(request, workspaceId);

    for (const editor of editors) {
      const requests = [];
      const staleStarted = deferred();
      const staleResponse = deferred();
      const reopenedStarted = deferred();
      const reopenedResponse = deferred();
      releasePendingResponses.push(staleResponse.resolve, reopenedResponse.resolve);
      let closedAndReopening = false;

      await page.route(`**${API_ROOT}/${workspaceId}/design/${editor.endpoint}`, async route => {
        const definition = route.request().postDataJSON().definition;
        requests.push(definition);

        if (definition.includes("slow_old_draft")) {
          staleStarted.resolve();
          await staleResponse.promise;
          await route.fulfill({
            status: 422,
            json: { error: { message: `${editor.name} stale response` } },
          });
          return;
        }

        if (closedAndReopening) {
          reopenedStarted.resolve();
          await reopenedResponse.promise;
          await route.fulfill({
            status: 422,
            json: { error: { message: `${editor.name} reopened response` } },
          });
          return;
        }

        await route.fulfill({
          status: 422,
          json: {
            error: {
              message: definition.includes("rapid_latest_draft")
                ? `${editor.name} latest response`
                : `${editor.name} baseline response`,
            },
          },
        });
      });

      await page.goto(`/?workspace=${workspaceId}&layer=${editor.name === "view" ? "views" : "tables"}`);
      await expect(page.locator("#workspace-title")).toHaveText(/E2E draft analysis/);
      await editor.open(page);

      const dialog = page.locator(editor.dialog);
      const definition = page.locator(editor.definition);
      const preview = page.locator(editor.preview);
      await expect(dialog).toBeVisible();

      if (editor.initialDefinition) {
        await definition.fill(editor.initialDefinition);
      }
      await expect.poll(() => requests.length).toBe(1);
      await expect(preview).toContainText(`${editor.name} baseline response`);

      const previousCount = requests.length;
      await definition.fill(`${editor.name} first rapid draft`);
      await definition.fill(`${editor.name} rapid_latest_draft`);
      await expect.poll(() => requests.length).toBe(previousCount + 1, { timeout: 5000 });
      expect(requests.at(-1)).toContain("rapid_latest_draft");
      expect(requests.slice(previousCount)).not.toContain(`${editor.name} first rapid draft`);
      await expect(preview).toContainText(`${editor.name} latest response`);

      await definition.fill(`${editor.name} slow_old_draft`);
      await staleStarted.promise;
      await dialog.locator("[data-close-dialog]").first().click();
      await expect(dialog).toBeHidden();
      closedAndReopening = true;
      await editor.open(page);
      await expect(dialog).toBeVisible();
      if (editor.name === "routine") {
        await definition.fill("CREATE FUNCTION analysis_reopened() RETURNS integer LANGUAGE SQL AS $$ SELECT 1 $$;");
      }
      await reopenedStarted.promise;
      await expect(preview).not.toContainText(`${editor.name} latest response`);

      staleResponse.resolve();
      await expect(preview).not.toContainText(`${editor.name} stale response`);
      reopenedResponse.resolve();
      await expect(preview).toContainText(`${editor.name} reopened response`);

      await dialog.locator("[data-close-dialog]").first().click();
      await expect(dialog).toBeHidden();
      await page.unroute(`**${API_ROOT}/${workspaceId}/design/${editor.endpoint}`);
    }
  } finally {
    releasePendingResponses.forEach(release => release());
    if (workspaceId) {
      const currentResponse = await request.get(`${API_ROOT}/${workspaceId}`);
      if (currentResponse.status() !== 404) {
        if (!currentResponse.ok()) {
          throw new Error(`Read editor lifecycle workspace for cleanup failed (${currentResponse.status()}): ${await currentResponse.text()}`);
        }
        const current = await currentResponse.json();
        const deleted = await request.delete(`${API_ROOT}/${workspaceId}?expectedRevision=${encodeURIComponent(current.revision)}`);
        if (!deleted.ok() && deleted.status() !== 404) {
          throw new Error(`Delete editor lifecycle workspace failed (${deleted.status()}): ${await deleted.text()}`);
        }
      }
    }
  }
});
