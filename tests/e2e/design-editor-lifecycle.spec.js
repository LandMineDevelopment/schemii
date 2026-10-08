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
        functions: [{
          id: designId("function"),
          definition: "CREATE FUNCTION handle_draft_analysis_orders_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$;",
        }],
        views: [],
        triggers: [],
      },
    },
  }), "Seed editor lifecycle design");
}

const editors = [
  {
    name: "view",
    createButton: "#create-view-button",
    validDefinition: "SELECT id FROM draft_analysis_orders",
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
    createButton: "#create-type-button",
    validDefinition: "CREATE TYPE protected_status AS ENUM ('draft', 'saved');",
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
    createButton: "#create-function-button",
    validDefinition: "CREATE FUNCTION protected_value() RETURNS integer LANGUAGE SQL AS $$ SELECT 1 $$;",
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
    createButton: "#create-trigger-button",
    validDefinition: "CREATE TRIGGER protected_changes AFTER INSERT ON draft_analysis_orders FOR EACH ROW EXECUTE FUNCTION handle_draft_analysis_orders_change();",
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
      await page.locator("#confirm-action").click();
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
      if (editor.name === "routine") await page.locator("#confirm-action").click();
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


async function deleteWorkspace(request, workspaceId) {
  const response = await request.get(`${API_ROOT}/${workspaceId}`);
  if (response.status() === 404) return;
  const current = await responseJson(response, "Read draft-protection workspace for cleanup");
  const deleted = await request.delete(`${API_ROOT}/${workspaceId}?expectedRevision=${encodeURIComponent(current.revision)}`);
  if (!deleted.ok() && deleted.status() !== 404) {
    throw new Error(`Delete draft-protection workspace failed (${deleted.status()}): ${await deleted.text()}`);
  }
}

async function fillValidDraft(page, editor) {
  if (editor.name === "view") await page.locator("#design-view-name").fill("protected_view");
  await page.locator(editor.definition).fill(editor.validDefinition);
}

async function expectUnloadProtection(page, protectedDraft) {
  expect(await page.evaluate(() => {
    const event = new Event("beforeunload", { cancelable: true });
    return !window.dispatchEvent(event);
  })).toBe(protectedDraft);
}

for (const editor of editors) {
  test(`${editor.name} drafts protect close, Escape, reload and replacement, then clear only after a successful save`, async ({ page, request }) => {
    let workspaceId = null;
    try {
      const workspace = await createWorkspace(request);
      workspaceId = workspace.id;
      await seedWorkspace(request, workspaceId);
      await page.goto(`/?workspace=${workspaceId}&layer=${editor.name === "view" ? "views" : "tables"}`);
      await expect(page.locator("#workspace-title")).toHaveText(workspace.name);
      const dialog = page.locator(editor.dialog);
      const definition = page.locator(editor.definition);
      const confirmation = page.locator("#confirm-dialog");

      await editor.open(page);
      await expectUnloadProtection(page, false);
      await dialog.locator("[data-close-dialog]").first().click();
      await expect(dialog).toBeHidden();
      await expect(confirmation).toBeHidden();

      await editor.open(page);
      const initial = await definition.inputValue();
      await definition.fill(`${initial} temporary edit`);
      await expectUnloadProtection(page, true);
      await definition.fill(initial);
      await expectUnloadProtection(page, false);
      await page.keyboard.press("Escape");
      await expect(dialog).toBeHidden();
      await expect(confirmation).toBeHidden();

      await editor.open(page);
      await fillValidDraft(page, editor);
      await page.keyboard.press("Escape");
      await expect(confirmation).toBeVisible();
      await confirmation.getByRole("button", { name: "Keep editing", exact: true }).last().click();
      await expect(dialog).toBeVisible();
      await expect(definition).toHaveValue(editor.validDefinition);
      await page.keyboard.press("Escape");
      await expect(confirmation).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(confirmation).toBeHidden();
      await expect(dialog).toBeVisible();
      await expectUnloadProtection(page, true);

      const unloadEvent = page.waitForEvent("dialog");
      const reload = page.evaluate(() => window.location.reload());
      const warning = await unloadEvent;
      expect(warning.type()).toBe("beforeunload");
      await warning.dismiss();
      await reload;
      await expect(dialog).toBeVisible();
      await expect(definition).toHaveValue(editor.validDefinition);

      await dialog.locator("[data-close-dialog]").first().click();
      await confirmation.getByRole("button", { name: "Discard changes", exact: true }).click();
      await expect(dialog).toBeHidden();
      await expectUnloadProtection(page, false);

      await editor.open(page);
      await fillValidDraft(page, editor);
      // Exercise the existing replacement command while its prior editor is still open.
      await page.locator(editor.createButton).evaluate(button => button.click());
      await expect(confirmation).toBeVisible();
      await confirmation.getByRole("button", { name: "Keep editing", exact: true }).last().click();
      await expect(definition).toHaveValue(editor.validDefinition);
      await page.locator(editor.createButton).evaluate(button => button.click());
      await confirmation.getByRole("button", { name: "Discard changes", exact: true }).click();
      await expect(dialog).toBeVisible();
      await expect(definition).toHaveValue(initial);
      await expectUnloadProtection(page, false);
      await fillValidDraft(page, editor);

      const designRoute = `**${API_ROOT}/${workspaceId}/design`;
      await page.route(designRoute, route => route.request().method() === "PUT"
        ? route.fulfill({ status: 409, json: { error: { code: "design_conflict", message: "Controlled draft save failure" } } })
        : route.continue());
      await dialog.locator("button[type=submit]").click();
      await expect(page.locator(`#design-${editor.name}-status`)).toContainText("Controlled draft save failure");
      await expectUnloadProtection(page, true);
      await expect(dialog).toBeVisible();
      await page.unroute(designRoute);
      await dialog.locator("button[type=submit]").click();
      await expect(dialog).toBeHidden();
      await expect(confirmation).toBeHidden();
      await expectUnloadProtection(page, false);
      if (editor.name === "type" || editor.name === "routine") await page.keyboard.press("Escape");
    } finally {
      if (workspaceId) await deleteWorkspace(request, workspaceId);
    }
  });
}

test("unsaved object drafts guard workspace Back, workspace replacement and application switching without repeated prompts", async ({ page, request }) => {
  const owned = [];
  try {
    const first = await createWorkspace(request);
    owned.push(first.id);
    await seedWorkspace(request, first.id);
    const second = await createWorkspace(request);
    owned.push(second.id);
    await seedWorkspace(request, second.id);
    await page.goto(`/?workspace=${first.id}&layer=views`);
    await expect(page.locator("#workspace-title")).toHaveText(first.name);
    const openWorkspace = async workspace => {
      await page.locator("#workspaces-button").click();
      await page.locator("#workspaces-list .manager-card").filter({ hasText: workspace.name })
        .getByRole("button", { name: "Open", exact: true }).click();
      await expect(page.locator("#workspace-title")).toHaveText(workspace.name);
    };
    await openWorkspace(second);
    const editor = editors[0];
    await editor.open(page);
    await fillValidDraft(page, editor);
    const confirmation = page.locator("#confirm-dialog");
    await page.goBack();
    await expect(confirmation).toBeVisible();
    await confirmation.getByRole("button", { name: "Keep editing", exact: true }).last().click();
    await expect(page).toHaveURL(new RegExp(`workspace=${second.id}`));
    await expect(page.locator("#workspace-title")).toHaveText(second.name);
    await expect(page.locator(editor.definition)).toHaveValue(editor.validDefinition);
    await expect(confirmation).toBeHidden();
    await page.goBack();
    await confirmation.getByRole("button", { name: "Discard changes", exact: true }).click();
    await expect(page.locator("#workspace-title")).toHaveText(first.name);
    await expect(page).toHaveURL(new RegExp(`workspace=${first.id}`));
    await expect(page.locator(editor.dialog)).toBeHidden();

    await editor.open(page);
    await fillValidDraft(page, editor);
    await page.locator("#workspaces-button").evaluate(button => button.click());
    const replace = page.locator("#workspaces-list .manager-card").filter({ hasText: second.name })
      .getByRole("button", { name: "Open", exact: true });
    await replace.click();
    await confirmation.getByRole("button", { name: "Keep editing", exact: true }).last().click();
    await expect(page.locator("#workspace-title")).toHaveText(first.name);
    await expect(page.locator(editor.definition)).toHaveValue(editor.validDefinition);
    await replace.click();
    await confirmation.getByRole("button", { name: "Discard changes", exact: true }).click();
    await expect(page.locator("#workspace-title")).toHaveText(second.name);
    await expect(page.locator(editor.dialog)).toBeHidden();

    await editor.open(page);
    await fillValidDraft(page, editor);
    const link = page.locator('.ui-product-navigation a[href="/schemoo"]');
    await link.evaluate(anchor => anchor.click());
    await confirmation.getByRole("button", { name: "Keep editing", exact: true }).last().click();
    await expect(page).toHaveURL(new RegExp(`workspace=${second.id}`));
    await expect(page.locator(editor.definition)).toHaveValue(editor.validDefinition);
    let duplicateUnloadWarnings = 0;
    page.on("dialog", async warning => { duplicateUnloadWarnings++; await warning.dismiss(); });
    await link.evaluate(anchor => anchor.click());
    await confirmation.getByRole("button", { name: "Discard changes", exact: true }).click();
    await expect(page).toHaveURL(/\/schemoo$/);
    expect(duplicateUnloadWarnings).toBe(0);
  } finally {
    for (const workspaceId of owned.reverse()) await deleteWorkspace(request, workspaceId);
  }
});


for (const loadingPhase of ["initial snapshot", "readiness"]) {
  test(`manual workspace opening preserves dirty Back navigation while the ${loadingPhase} is delayed`, async ({ page, request }) => {
    const owned = [];
    const initialRequested = deferred();
    const releaseInitial = deferred();
    const initialRouteFinished = deferred();
    try {
      const first = await createWorkspace(request);
      owned.push(first.id);
      await seedWorkspace(request, first.id);
      const second = await createWorkspace(request);
      owned.push(second.id);
      await seedWorkspace(request, second.id);
      const endpoint = loadingPhase === "readiness"
        ? "**/api/v1/readiness" : `**${API_ROOT}/${first.id}/design/snapshot`;
      await page.route(endpoint, async route => {
        initialRequested.resolve();
        await releaseInitial.promise;
        try { await route.continue(); }
        finally { initialRouteFinished.resolve(); }
      }, { times: 1 });

      await page.goto(`/?workspace=${first.id}&layer=views`);
      await initialRequested.promise;
      if (loadingPhase === "initial snapshot") {
        await expect(page.locator("#workspace-title")).toHaveText(first.name);
      } else await expect(page.locator("#catalog-state")).toContainText("Loading active server state");
      await expect(page.locator("#create-view-button")).toBeDisabled();
      const initialHistory = await page.evaluate(() => ({
        length: window.history.length,
        index: window.history.state.schemiiNavigationIndex,
      }));
      await page.locator("#workspaces-button").click();
      const openSecond = page.locator("#workspaces-list .manager-card").filter({ hasText: second.name })
        .getByRole("button", { name: "Open", exact: true });
      await expect(openSecond).toBeEnabled();
      await openSecond.click();
      await expect(page).toHaveURL(new RegExp(`workspace=${second.id}`));
      await expect(page.locator("#workspace-title")).toHaveText(second.name);
      await expect(page.locator("#create-view-button")).toBeEnabled();
      if (loadingPhase === "readiness") {
        await expect(page.locator("#catalog-state")).toContainText("Loading active server state");
      }
      expect(await page.evaluate(() => ({
        length: window.history.length,
        index: window.history.state.schemiiNavigationIndex,
      }))).toEqual({ length: initialHistory.length + 1, index: initialHistory.index + 1 });

      // Finishing either startup phase must not reclaim the newer workspace's navigation.
      releaseInitial.resolve();
      await initialRouteFinished.promise;
      await expect(page.locator("#catalog-state")).toBeEmpty();
      await expect(page.locator("#workspace-title")).toHaveText(second.name);
      await expect(page).toHaveURL(new RegExp(`workspace=${second.id}.*layer=views`));
      const editor = editors[0];
      await editor.open(page);
      await fillValidDraft(page, editor);
      const confirmation = page.locator("#confirm-dialog");
      await page.goBack();
      await expect(confirmation).toBeVisible();
      await confirmation.getByRole("button", { name: "Keep editing", exact: true }).last().click();
      await expect(page).toHaveURL(new RegExp(`workspace=${second.id}`));
      await expect(page.locator("#workspace-title")).toHaveText(second.name);
      await expect(page.locator(editor.definition)).toHaveValue(editor.validDefinition);
      await expect(confirmation).toBeHidden();
      await page.goBack();
      await confirmation.getByRole("button", { name: "Discard changes", exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`workspace=${first.id}`));
      await expect(page.locator("#workspace-title")).toHaveText(first.name);
      await expect(page.locator("#create-view-button")).toBeEnabled();
      await expect(page.locator(editor.dialog)).toBeHidden();
    } finally {
      releaseInitial.resolve();
      for (const workspaceId of owned.reverse()) await deleteWorkspace(request, workspaceId);
    }
  });
}
