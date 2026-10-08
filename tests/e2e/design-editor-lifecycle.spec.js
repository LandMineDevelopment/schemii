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

test("Fit rejects pointer and callback actions while a real saved-design response is held, then fits the ready viewport", async ({ page, request }, testInfo) => {
  let workspaceId = null;
  let routeStarted = false;
  let responseDelivered = false;
  let released = false;
  let heldRequests = 0;
  let heldSnapshot = null;
  const releaseResponse = deferred();
  const routeFinished = deferred();
  let endpoint = null;
  let holdSnapshot = null;
  const fit = page.locator("#fit-button");
  const tableCard = page.locator('.table-card[data-table-name="qa_pilot_items"]');
  const inspector = page.locator("#inspector");
  const uncaughtErrors = [];
  const recordPageError = error => uncaughtErrors.push({ name: error.name, message: error.message });
  const emptyToast = /No (?:live|desired) tables are available to fit\./;
  page.on("pageerror", recordPageError);

  const camera = () => page.locator("#canvas-stage").evaluate((stage, id) => ({
    transform: stage.style.transform,
    saved: JSON.parse(localStorage.getItem(`schemii.workspace-view.v1.${id}`) || "null")?.camera ?? null,
  }), workspaceId);
  const expectNoEmptyToast = async () => {
    await expect(page.locator("#toast")).not.toContainText(emptyToast);
    await expect(page.locator("#toast")).toBeHidden();
  };
  const attachImage = async name => testInfo.attach(name, {
    body: await page.screenshot(), contentType: "image/png",
  });
  const expectFittedGeometry = async mobile => {
    const geometry = await page.evaluate(isMobile => {
      const rect = selector => {
        const { left, right, top, bottom } = document.querySelector(selector).getBoundingClientRect();
        return { left, right, top, bottom };
      };
      const canvas = rect("#canvas");
      return {
        card: rect('.table-card[data-table-name="qa_pilot_items"]'),
        usable: {
          left: canvas.left + (isMobile ? 18 : 75),
          right: isMobile ? canvas.right - 18 : Math.min(canvas.right - 20, rect("#inspector").left - 20),
          top: canvas.top + 65,
          bottom: canvas.bottom - (isMobile ? 75 : 35),
        },
      };
    }, mobile);
    expect(geometry.card.left).toBeGreaterThanOrEqual(geometry.usable.left - 1);
    expect(geometry.card.right).toBeLessThanOrEqual(geometry.usable.right + 1);
    expect(geometry.card.top).toBeGreaterThanOrEqual(geometry.usable.top - 1);
    expect(geometry.card.bottom).toBeLessThanOrEqual(geometry.usable.bottom + 1);
    await expect(page.locator("#zoom-output")).toHaveText("125%");
    await expectNoEmptyToast();
    return geometry;
  };

  try {
    const workspace = await createWorkspace(request);
    workspaceId = workspace.id;
    const current = await responseJson(await request.get(`${API_ROOT}/${workspaceId}/design`), "Read empty Fit design");
    const idColumn = designId("column");
    const table = {
      id: designId("table"), name: "qa_pilot_items",
      columns: [
        { id: idColumn, name: "id", dataType: "integer", nullable: false },
        { id: designId("column"), name: "label", dataType: "text", nullable: false },
        { id: designId("column"), name: "qty", dataType: "integer", nullable: true },
      ],
      keys: [{ id: designId("key"), name: "qa_pilot_items_pk", kind: "primary", columnIds: [idColumn] }],
      checks: [], indexes: [],
    };
    await responseJson(await request.put(`${API_ROOT}/${workspaceId}/design`, {
      data: {
        expectedDesignRevision: current.revision,
        content: { types: [], tables: [table], relationships: [], functions: [], views: [], triggers: [] },
      },
    }), "Save owned Fit design");
    const saved = await responseJson(await request.get(`${API_ROOT}/${workspaceId}/design/snapshot`), "Read saved Fit snapshot");
    expect(saved.design.content.tables).toHaveLength(1);
    expect(saved.design.content.tables[0]).toMatchObject(table);
    await testInfo.attach("fit-owned-fixture", {
      body: Buffer.from(JSON.stringify({ project: testInfo.project.name, workspaceId, tableId: table.id, revision: saved.design.revision })),
      contentType: "application/json",
    });

    // Prime the actual mobile camera before recreating the original desktop trigger.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/?workspace=${workspaceId}`);
    await expect(page.locator("#workspace-title")).toHaveText(workspace.name);
    await expect(tableCard).toBeVisible();
    await expect(fit).toBeEnabled();
    await fit.click();
    await expectFittedGeometry(true);
    await page.setViewportSize({ width: 1280, height: 800 });
    await tableCard.click();
    await expect(inspector).toBeVisible();

    endpoint = `**${API_ROOT}/${workspaceId}/design/snapshot`;
    holdSnapshot = async route => {
      routeStarted = true;
      heldRequests++;
      try {
        expect(route.request().method()).toBe("GET");
        expect(new URL(route.request().url()).pathname).toBe(`${API_ROOT}/${workspaceId}/design/snapshot`);
        // Fetch real server data; only its delivery to the mounted application is held.
        const response = await route.fetch({ maxRetries: 0 });
        const snapshot = await responseJson(response, "Fetch held real Fit snapshot");
        expect(snapshot.design.revision).toBe(saved.design.revision);
        expect(snapshot.design.content.tables).toHaveLength(1);
        expect(snapshot.design.content.tables[0]).toMatchObject(table);
        heldSnapshot = { revision: snapshot.design.revision };
        await releaseResponse.promise;
        await route.fulfill({ response });
        responseDelivered = true;
      } catch (error) {
        heldSnapshot = { error };
        await route.abort().catch(() => {});
      } finally {
        routeFinished.resolve();
      }
    };
    await page.route(endpoint, holdSnapshot, { times: 1 });
    await page.goto(`/?workspace=${workspaceId}`);
    await expect.poll(() => heldSnapshot, { message: "The real owned saved-design response is fetched and held" }).not.toBeNull();
    if (heldSnapshot.error) throw heldSnapshot.error;
    await expect(page.locator("#catalog-state")).toContainText("Loading saved design");
    await expect(page.locator("#workspace-title")).toHaveText(workspace.name);
    await expect(fit).toBeVisible();
    await expect(fit).toBeDisabled();
    const heldCamera = await camera();
    await attachImage("fit-held-loading");

    // A disabled locator.click() would wait for readiness and miss this window.
    const box = await fit.boundingBox();
    expect(box).not.toBeNull();
    const point = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    expect(released || responseDelivered).toBe(false);
    if (testInfo.project.use.hasTouch) await page.touchscreen.tap(point.x, point.y);
    else await page.mouse.click(point.x, point.y);
    await expect(fit).toBeDisabled();
    await expect(page.locator("#catalog-state")).toContainText("Loading saved design");
    await expectNoEmptyToast();
    expect(await camera()).toEqual(heldCamera);

    // Separate callback oracle: observe DOM synchronously after the live listener runs.
    const callback = await fit.evaluate((button, id) => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      const toast = document.querySelector("#toast");
      return {
        disabled: button.disabled,
        toast: { hidden: toast.hidden, text: toast.textContent },
        camera: {
          transform: document.querySelector("#canvas-stage").style.transform,
          saved: JSON.parse(localStorage.getItem(`schemii.workspace-view.v1.${id}`) || "null")?.camera ?? null,
        },
      };
    }, workspaceId);
    // DOM listener exceptions do not reject dispatchEvent/evaluate. Query Playwright's
    // server-recorded errors after that browser round trip as well as the event collector.
    const recordedErrors = (await page.pageErrors({ filter: "all" })).map(error => ({ name: error.name, message: error.message }));
    await testInfo.attach("fit-held-callback-errors", {
      body: Buffer.from(JSON.stringify({ callback, recordedErrors, uncaughtErrors })), contentType: "application/json",
    });
    expect(recordedErrors, "No uncaught page error before or during the held Fit callback").toEqual([]);
    expect(uncaughtErrors, "The scoped pageerror collector records no uncaught listener errors").toEqual([]);
    expect(callback.disabled).toBe(true);
    expect(callback.toast.hidden).toBe(true);
    expect(callback.toast.text).not.toMatch(emptyToast);
    expect(callback.camera).toEqual(heldCamera);
    await expect(fit).toBeDisabled();
    await expectNoEmptyToast();
    expect(await camera()).toEqual(heldCamera);
    expect(released || responseDelivered).toBe(false);
    expect(heldRequests).toBe(1);
    await testInfo.attach("fit-held-admission", {
      body: Buffer.from(JSON.stringify({ workspaceId, revision: heldSnapshot.revision, heldRequests, pointer: testInfo.project.use.hasTouch ? "touch" : "mouse", heldCamera })),
      contentType: "application/json",
    });

    released = true;
    releaseResponse.resolve();
    await routeFinished.promise;
    expect(responseDelivered).toBe(true);
    await expect(tableCard).toBeVisible();
    await expect(page.locator("#catalog-state")).toBeEmpty();
    await expect(fit).toBeEnabled();
    await tableCard.click();
    await expect(inspector).toBeVisible();
    const beforeReadyFit = await camera();
    await fit.click();
    expect((await camera()).transform).not.toBe(beforeReadyFit.transform);
    const desktopGeometry = await expectFittedGeometry(false);
    await attachImage("fit-ready-desktop-inspector");
    await page.locator("#table-inspector-close").click();
    await page.setViewportSize({ width: 390, height: 844 });
    const beforeMobileFit = await camera();
    await fit.click();
    expect((await camera()).transform).not.toBe(beforeMobileFit.transform);
    const mobileGeometry = await expectFittedGeometry(true);
    await attachImage("fit-ready-mobile");
    await testInfo.attach("fit-ready-geometry", {
      body: Buffer.from(JSON.stringify({ workspaceId, desktopGeometry, mobileGeometry })), contentType: "application/json",
    });
  } finally {
    page.off("pageerror", recordPageError);
    released = true;
    releaseResponse.resolve();
    try {
      if (routeStarted) await routeFinished.promise;
      if (endpoint && holdSnapshot) await page.unroute(endpoint, holdSnapshot);
    } finally {
      if (workspaceId) await deleteWorkspace(request, workspaceId);
    }
  }
});
