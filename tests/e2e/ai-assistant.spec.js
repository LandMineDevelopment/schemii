import { expect, test } from "@playwright/test";

async function workspace(request) {
  const response = await request.get("/api/v1/schemii/workspaces");
  expect(response.ok()).toBe(true);
  const body = await response.json();
  return body.workspaces.find(item => item.name === "Test design") || body.workspaces[0];
}

test("header and settings model pickers refresh availability without changing conversation or draft selections", async ({ page, request }) => {
  const activeWorkspace = await workspace(request);
  const chat = { id: "chat_model_picker_fixture", workspaceId: activeWorkspace.id, providerId: "openai", modelId: "old-model", title: "Retained conversation", status: "idle", capabilities: {}, revision: 1 };
  const writes = [];
  let checks = 0;
  await page.route("**/api/v1/ai/status*", route => {
    const refresh = new URL(route.request().url()).searchParams.get("refresh") === "true";
    if (refresh) checks += 1;
    return route.fulfill({ json: { healthy: true, providers: [{ id: "openai", name: "OpenAI", available: true, authenticated: true,
      models: [...(refresh ? [] : [{ id: "old-model", name: "Old model", status: "active" }]), { id: "new-model", name: "New model", status: "active" }] }] } });
  });
  await page.route("**/api/v1/schemii/ai/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET") writes.push(path);
    const json = path.endsWith("/settings") ? { defaultProviderId: "openai", defaultModelId: "old-model", defaultCapabilities: {}, permissionActions: [], revision: 1 }
      : path.endsWith("/chats") ? { chats: [chat] }
      : path.endsWith("/messages") ? { messages: [] }
      : path.endsWith("/proposals") ? { proposals: [] }
      : path.endsWith("/operations") ? { operations: [] }
      : path.endsWith("/activity") ? { events: [], nextSequence: 0 }
      : path.endsWith("/transient-responses") ? { responses: [] } : chat;
    return route.fulfill({ json });
  });
  await page.goto(`/?workspace=${activeWorkspace.id}`);
  await page.getByRole("button", { name: "AI schema assistant" }).click();
  const headerPicker = page.getByRole("complementary", { name: "Schemii AI" }).getByRole("combobox", { name: "AI model" });
  await expect(headerPicker).toHaveValue("Old model · OpenAI");
  await headerPicker.click();
  await expect(page.getByRole("option", { name: "New model · OpenAI" })).toBeVisible();
  await expect(headerPicker).toHaveValue(/old-model · unavailable/);
  expect(checks).toBe(1);
  await headerPicker.press("Escape");
  await page.getByRole("button", { name: "Assistant settings", exact: true }).click();
  const settings = page.getByRole("dialog", { name: "Model & permissions" });
  const settingsPicker = settings.getByRole("combobox", { name: "AI model", exact: true });
  await settingsPicker.click();
  await expect(page.getByRole("option", { name: "New model · OpenAI" })).toBeVisible();
  await expect.poll(() => checks).toBe(2);
  await page.getByRole("option", { name: "New model · OpenAI" }).click();
  await expect(settingsPicker).toHaveValue("New model · OpenAI");
  await settingsPicker.press("ArrowDown");
  await expect.poll(() => checks).toBe(3);
  await expect(page.getByRole("option", { name: "New model · OpenAI" })).toBeVisible();
  await expect(settingsPicker).toHaveValue("New model · OpenAI");
  await expect(page.locator("#ai-assistant-model")).toHaveValue("openai\u0000old-model");
  expect(writes).toHaveLength(0);
});

test("assistant keeps proposals and outcomes with the turn that created them", async ({ page, request }) => {
  const activeWorkspace = await workspace(request);
  const chatId = `chat_${"a".repeat(32)}`;
  const firstTurn = `turn_${"b".repeat(32)}`;
  const secondTurn = `turn_${"c".repeat(32)}`;
  const firstProposal = `prop_${"d".repeat(32)}`;
  const secondProposal = `prop_${"e".repeat(32)}`;
  const createdAt = new Date().toISOString();
  let executionRequests = 0;
  let batchRequest = null;
  let analysisUpdated = false;

  await page.route("**/api/v1/schemii/ai/chats/*/messages", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ messages: [
      { id: `msg_${"1".repeat(32)}`, chatId, turnId: firstTurn, sequence: 1, role: "user", text: "Create the archive table", createdAt },
      { id: `msg_${"2".repeat(32)}`, chatId, turnId: firstTurn, sequence: 2, role: "assistant", text: analysisUpdated ? "The comparison is ready." : "I prepared the archive table.", createdAt: analysisUpdated ? new Date(Date.parse(createdAt) + 1000).toISOString() : createdAt },
      { id: `msg_${"3".repeat(32)}`, chatId, turnId: secondTurn, sequence: 3, role: "user", text: "Delete the temporary table", createdAt },
      { id: `msg_${"4".repeat(32)}`, chatId, turnId: secondTurn, sequence: 4, role: "assistant", text: "I prepared the deletion for review.", createdAt },
    ] }),
  }));
  await page.route("**/api/v1/schemii/ai/chats/*/proposals", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ proposals: [
      { id: firstProposal, chatId, turnId: firstTurn, revision: 2, actionType: "design_change", summary: "Create the archive table", status: "succeeded", destructive: false, createdAt },
      { id: secondProposal, chatId, turnId: secondTurn, revision: 1, capability: "design_changes", actionType: "design_change", summary: "Delete the temporary table", details: { type: "batch", actions: [{ type: "delete_object", object_id: `table_${"9".repeat(32)}` }, { type: "put_table_member", table_id: "table_projects", collection: "indexes", object: { id: "idx_project", name: "idx_project_team", columns: ["column_team"], unique: false, method: "btree" } }] }, digest: "9".repeat(64), status: "pending", destructive: true, createdAt },
      { id: "prop_draft", chatId, turnId: firstTurn, revision: 1, capability: "raw_sql_write", actionType: "console_script", summary: "Prepare an index script", details: { sql: "CREATE INDEX idx_example ON projects (team_id);" }, digest: "8".repeat(64), status: "pending", destructive: false, createdAt },
      { id: "prop_batch_draft", chatId, turnId: secondTurn, revision: 3, capability: "raw_sql_write", actionType: "console_script", summary: "Prepare follow-up SQL", details: { sql: "SELECT 1;" }, digest: "7".repeat(64), status: "pending", destructive: false, createdAt },
    ] }),
  }));
  await page.route("**/api/v1/schemii/ai/chats/*/proposal-batch/executions", async route => {
    batchRequest = route.request().postDataJSON();
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ operations: [], status: "failed", message: "Fixture batch stopped before execution.", notAttempted: [secondProposal, "prop_batch_draft"] }) });
  });
  const readOperation = `aop_${"f".repeat(32)}`;
  await page.route("**/api/v1/schemii/ai/chats/*/operations", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ operations: [
      { id: readOperation, chatId, proposalId: firstProposal, revision: 1, kind: "data_read", status: "succeeded", createdAt, updatedAt: createdAt },
      { id: "aop_draft", chatId, proposalId: firstProposal, revision: 1, kind: "console_script", status: "succeeded", createdAt, updatedAt: createdAt },
      { id: "aop_design", chatId, proposalId: firstProposal, revision: 1, kind: "design_change", status: "succeeded", createdAt, updatedAt: createdAt },
      { id: "aop_review", chatId, proposalId: firstProposal, revision: 1, kind: "migration_review", status: "succeeded", createdAt, updatedAt: createdAt },
    ] }),
  }));
  await page.route("**/api/v1/schemii/ai/chats/*/operations/*/query-result", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      operationId: readOperation,
      results: [{
        label: "Current departments",
        columns: [{ name: "an_intentionally_long_identifier_that_requires_horizontal_scrolling", type: "text" }, { name: "another_intentionally_long_identifier", type: "text" }],
        rows: [["first value", "second value"]],
        sampled: true,
      }, {
        label: "Earlier departments",
        columns: [{ name: "department", type: "text" }],
        rows: [["Engineering"]],
        rerun: true,
        freshnessNotice: "Earlier query rerun; data may have changed.",
      }, {
        label: "Released departments",
        released: true,
        message: "These results expired.",
      }, {
        label: "Empty departments",
        columns: [{ name: "department", type: "text" }],
        rows: [],
      }],
      columns: [
        { name: "an_intentionally_long_identifier_that_requires_horizontal_scrolling", type: "text" },
        { name: "another_intentionally_long_identifier", type: "text" },
      ],
      rows: [["first value", "second value"]],
      rowCount: 1,
      rerun: false,
    }),
  }));
  await page.route("**/api/v1/schemii/ai/chats/*/proposals/*/executions", route => {
    executionRequests += 1;
    return route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "The review test must not execute the proposal." }) });
  });
  await page.route("**/api/v1/schemii/ai/chats/*/activity?*", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ events: [], nextSequence: 0 }),
  }));

  await page.goto(`/?workspace=${activeWorkspace.id}`);
  await page.getByRole("button", { name: "AI schema assistant" }).click();
  const turns = page.locator("#ai-assistant-messages > .ai-turn");
  await expect(turns).toHaveCount(2);
  for (const turn of await turns.all()) {
    expect(await turn.evaluate(node => {
      const prompt = node.querySelector('.ai-message.user'), tracker = node.querySelector('.ai-turn__activity'), answer = node.querySelector('.ai-message.assistant');
      return Boolean(prompt.compareDocumentPosition(tracker) & Node.DOCUMENT_POSITION_FOLLOWING)
        && Boolean(tracker.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING);
    })).toBe(true);
  }
  await expect(turns.nth(0)).toContainText("Create the archive table");
  await expect(turns.nth(0)).toContainText("READ COMPLETED");
  await expect(turns.nth(0)).toContainText("DRAFT READY");
  await expect(turns.nth(0)).toContainText("SAVED TO DESIGN");
  await expect(turns.nth(0)).toContainText("REVIEW READY");
  await expect(turns.nth(0)).toContainText("No SQL is executed");
  await turns.nth(0).getByRole("button", { name: "Review draft" }).click();
  const draftReview = page.getByRole("dialog", { name: "Review proposed action" });
  await expect(draftReview).toContainText("CREATE INDEX idx_example");
  await expect(draftReview.getByRole("button", { name: "Open Console" })).toBeVisible();
  await draftReview.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(turns.nth(0)).not.toContainText("Delete the temporary table");
  await expect(turns.nth(1)).toContainText("Delete the temporary table");
  await expect(turns.nth(1)).toContainText("PROPOSED design change");
  await expect(turns.nth(1)).not.toContainText("SUCCEEDED");
  await expect(turns.nth(0).getByRole("button", { name: /Review pending batch/ })).toHaveCount(0);
  await turns.nth(1).getByRole("button", { name: "Review pending batch (2)" }).click();
  await expect(draftReview).toContainText("Separate service actions are not one transaction");
  await expect(draftReview).toContainText("Delete the temporary table");
  await expect(draftReview).toContainText("Prepare follow-up SQL");
  await draftReview.getByRole("button", { name: "Approve 2 actions", exact: true }).click();
  await expect(draftReview).toBeHidden();
  expect(batchRequest.items.map(item => item.proposalId)).toEqual([secondProposal, "prop_batch_draft"]);
  expect(batchRequest.items.map(item => item.proposalDigest)).toEqual(["9".repeat(64), "7".repeat(64)]);
  expect(batchRequest.items.every(item => item.confirmed)).toBe(true);
  const showRows = turns.nth(0).getByRole("button", { name: "Show rows" });
  await showRows.scrollIntoViewIfNeeded();
  await showRows.click();
  await expect(turns.nth(0).locator(".ai-query-result")).toHaveCount(2);
  await expect(turns.nth(0)).toContainText("Earlier query rerun; data may have changed.");
  await expect(turns.nth(0)).toContainText("partial result");
  await expect(turns.nth(0)).toContainText("These results expired.");
  await expect(turns.nth(0)).toContainText("Query returned no rows.");
  await expect(turns.nth(0).getByRole("button", { name: "Ask about rows" })).toHaveCount(0);
  analysisUpdated = true;
  await page.locator("#ai-assistant-close").click();
  await page.getByRole("button", { name: "AI schema assistant" }).click();
  await expect(turns.nth(0)).toContainText("The comparison is ready.");
  await expect(turns.nth(0).locator(".ai-query-result")).toHaveCount(2);
  await expect(turns.nth(0)).toContainText("Engineering");
  const operationBeforeAnalysis = await turns.nth(0).evaluate(turn => Boolean(turn.querySelector(".ai-operation").compareDocumentPosition(turn.querySelector(".ai-message.assistant")) & Node.DOCUMENT_POSITION_FOLLOWING));
  expect(operationBeforeAnalysis).toBe(true);
  const assistantBody = page.locator("#ai-assistant-body");
  await expect.poll(() => assistantBody.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  const [bodyBounds, buttonBounds] = await Promise.all([assistantBody.boundingBox(), showRows.boundingBox()]);
  expect(bodyBounds).not.toBeNull();
  expect(buttonBounds).not.toBeNull();
  expect(buttonBounds.x + buttonBounds.width).toBeLessThanOrEqual(bodyBounds.x + bodyBounds.width);
  await turns.nth(1).getByRole("button", { name: "Review design changes" }).click();
  const review = page.getByRole("dialog", { name: "Review proposed action" });
  await expect(review).toBeVisible();
  await expect(review).toContainText("delete_object");
  await expect(review).toContainText("2 changes saved together in one design revision");
  await expect(review).toContainText("idx_project_team");
  await expect(review).toContainText("Live migration is a separate action");
  const confirm = review.getByRole("button", { name: "Save to design" });
  await expect(confirm).toBeDisabled();
  await expect(confirm).toBeEnabled();
  expect(executionRequests).toBe(0);
  await review.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(review).toBeHidden();
});

test("migration recovery approval explains bundled conflict choices and outcome checks", async ({ page, request }) => {
  const activeWorkspace = await workspace(request);
  const createdAt = new Date().toISOString();
  const turnId = `turn_${"9".repeat(32)}`;
  let submitted = null;
  const proposals = [
    { id: `prop_${"1".repeat(32)}`, turnId, revision: 1, capability: "migration_apply", actionType: "migration_resolve", summary: "Resolve the two external changes", digest: "1".repeat(64), status: "pending", destructive: true, createdAt, details: {
      plan_id: "mpl_review", expected_design_revision: 8, review_digest: "2".repeat(64),
      resolutions: [{ conflict_id: "conflict_one", resolution: "pull_live" }, { conflict_id: "conflict_two", resolution: "keep_design" }],
      reviewContext: [{ id: "conflict_one", path: "tables.projects.columns.title", reason: "The column type was changed externally." }, { id: "conflict_two", path: "tables.tasks.indexes.idx_status", reason: "The index was removed externally." }],
    } },
    { id: `prop_${"2".repeat(32)}`, turnId, revision: 2, capability: "migration_apply", actionType: "migration_reconcile", summary: "Check the earlier uncertain migration", digest: "3".repeat(64), status: "pending", destructive: false, createdAt, details: { execution_id: "mexe_uncertain", expected_execution_revision: 4 } },
  ];
  await page.route("**/api/v1/schemii/ai/chats/*/messages", route => route.fulfill({ json: { messages: [{ id: "question", turnId, sequence: 1, role: "user", text: "Resolve these conflicts and check the earlier migration", createdAt }] } }));
  await page.route("**/api/v1/schemii/ai/chats/*/proposals", route => route.fulfill({ json: { proposals } }));
  await page.route("**/api/v1/schemii/ai/chats/*/operations", route => route.fulfill({ json: { operations: [{
    id: "aop_prior_recovery", proposalId: "prop_prior_recovery", turnId, revision: 1, kind: "migration_reconcile", status: "succeeded", createdAt,
    resultSummary: { commitOutcome: "uncertain", syncStatus: "pending", reconcileRequired: true },
  }] } }));
  await page.route("**/api/v1/schemii/ai/chats/*/activity?*", route => route.fulfill({ json: { events: [], nextSequence: 0 } }));
  await page.route("**/api/v1/schemii/ai/chats/*/proposal-batch/executions", route => {
    submitted = route.request().postDataJSON();
    return route.fulfill({ json: { operations: [], status: "failed", message: "Fixture does not execute migrations.", notAttempted: proposals.map(item => item.id) } });
  });
  await page.goto(`/?workspace=${activeWorkspace.id}`);
  await page.getByRole("button", { name: "AI schema assistant" }).click();
  const turn = page.locator(`[data-turn-id="${turnId}"]`);
  await expect(page.locator("#ai-assistant-body")).toContainText("OUTCOME CHECKED");
  await expect(page.locator("#ai-assistant-body")).toContainText("Further reconciliation is required; do not assume the migration committed.");
  await turn.getByRole("button", { name: "Review pending batch (2)" }).click();
  const dialog = page.getByRole("dialog", { name: "Review proposed action" });
  for (const text of ["Pull live database change", "Keep workspace design", "tables.projects.columns.title", "tables.tasks.indexes.idx_status", "The column type was changed externally.", "mexe_uncertain", "without replaying migration SQL", "no live data is deleted"]) await expect(dialog).toContainText(text);
  await expect(dialog.locator("pre")).toHaveCount(0);
  await page.screenshot({ path: test.info().outputPath("migration-recovery-review.png"), fullPage: true, animations: "disabled" });
  await dialog.getByRole("button", { name: "Approve 2 actions", exact: true }).click();
  await expect(dialog).toBeHidden();
  expect(submitted.items.map(item => item.proposalDigest)).toEqual(proposals.map(item => item.digest));
  expect(submitted.items.every(item => item.confirmed)).toBe(true);
});

test("read batch approval resumes the answer inline", async ({ page, request }) => {
  const activeWorkspace = await workspace(request);
  const turnId = `turn_${"8".repeat(32)}`;
  const proposalId = `prop_${"7".repeat(32)}`;
  const operationId = `aop_${"6".repeat(32)}`;
  const createdAt = new Date().toISOString();
  let approved = false;
  let terminalObserved = false;
  let approvalCount = 0;
  const proposal = { id: proposalId, turnId, revision: 1, actionType: "data_read", capability: "raw_sql_read", digest: "7".repeat(64), summary: "Compare department totals", destructive: false, createdAt, details: { queries: [{ label: "Current", sql: "SELECT 1 AS current_total" }, { label: "Earlier", sql: "SELECT 2 AS earlier_total" }] } };
  await page.route("**/api/v1/schemii/ai/chats/*/messages", route => route.fulfill({ json: { messages: [
    { id: "question", turnId, sequence: 1, role: "user", text: "Compare the totals", createdAt },
    ...(approved && terminalObserved ? [{ id: "answer", turnId, sequence: 2, role: "assistant", text: "The current total is one lower than the earlier total.", createdAt: new Date(Date.parse(createdAt) + 1000).toISOString() }] : []),
  ] } }));
  await page.route("**/api/v1/schemii/ai/chats/*", async route => {
    const response = await route.fetch();
    const currentChat = await response.json();
    // A transcript fetched before terminal status still contains no answer.
    // The browser must observe completion before requesting that transcript.
    terminalObserved = approved;
    return route.fulfill({ json: { ...currentChat, status: "idle" } });
  });
  await page.route("**/api/v1/schemii/ai/chats/*/proposals", route => route.fulfill({ json: { proposals: [{ ...proposal, status: approved ? "succeeded" : "pending" }] } }));
  await page.route("**/api/v1/schemii/ai/chats/*/operations", route => route.fulfill({ json: { operations: approved ? [{ id: operationId, proposalId, revision: 1, kind: "data_read", status: "succeeded", createdAt }] : [] } }));
  await page.route("**/api/v1/schemii/ai/chats/*/activity?*", route => route.fulfill({ json: { events: [], nextSequence: 0 } }));
  await page.route("**/api/v1/schemii/ai/chats/*/proposals/*/executions", route => {
    approvalCount += 1;
    expect(route.request().postDataJSON().confirmed).toBe(true);
    approved = true;
    return route.fulfill({ json: { id: operationId, kind: "data_read", status: "succeeded" } });
  });
  await page.goto(`/?workspace=${activeWorkspace.id}`);
  await page.getByRole("button", { name: "AI schema assistant" }).click();
  const turn = page.locator(`[data-turn-id="${turnId}"]`);
  await turn.locator("summary").filter({ hasText: "Read queries" }).click();
  await expect(turn).toContainText("SELECT 1 AS current_total");
  await expect(turn).toContainText("SELECT 2 AS earlier_total");
  await turn.getByRole("button", { name: "Approve reads" }).click();
  await expect(turn).toContainText("The current total is one lower");
  await expect(page.getByRole("dialog", { name: "Review proposed action" })).toBeHidden();
  await expect(turn.getByRole("button", { name: "Show rows" })).toBeVisible();
  expect(approvalCount).toBe(1);
  expect(await turn.evaluate(node => Boolean(node.querySelector(".ai-operation").compareDocumentPosition(node.querySelector(".ai-message.assistant")) & Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);
  await expect.poll(() => turn.locator(".ai-message.assistant p").evaluate(node => node.scrollWidth <= node.clientWidth && node.getBoundingClientRect().right <= node.closest(".ai-message__surface").getBoundingClientRect().right)).toBe(true);
  await page.mouse.move(0, 0);
  await page.screenshot({ path: test.info().outputPath("continuous-read-success.png"), fullPage: true, animations: "disabled" });
});

test("assistant provides formatted messages, model, permissions, and history", async ({ page, request }) => {
  const activeWorkspace = await workspace(request);
  let submitted = null;
  await page.route("**/api/v1/schemii/ai/chats/*/messages", async route => {
    if (route.request().method() === "POST") {
      submitted = route.request().postDataJSON();
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          id: `turn_${"1".repeat(32)}`,
          chatId: `chat_${"1".repeat(32)}`,
          status: "queued",
          resultContextOperationId: null,
          resultContextRerun: false,
          errorCode: null,
          errorMessage: null,
          createdAt: new Date().toISOString(),
          startedAt: null,
          completedAt: null,
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        messages: [{
          id: `msg_${"2".repeat(32)}`,
          chatId: `chat_${"1".repeat(32)}`,
          turnId: `turn_${"1".repeat(32)}`,
          sequence: 1,
          role: "assistant",
          text: "## Core tables\n\n**Customers** are linked with `customer_id`.\n\n| Table | Purpose |\n| --- | --- |\n| customers | Customer records |",
          createdAt: new Date().toISOString(),
        }],
      }),
    });
  });

  await page.goto(`/?workspace=${activeWorkspace.id}`);
  await page.getByRole("button", { name: "AI schema assistant" }).click();

  const assistant = page.getByRole("complementary", { name: "Schemii AI" });
  await expect(assistant).toBeVisible();
  await expect(assistant.getByRole("combobox", { name: "AI model" })).toHaveValue(/.+/);
  await expect(assistant.getByRole("button", { name: "Assistant settings" })).toBeVisible();
  await expect(assistant.getByRole("button", { name: "Conversation history" })).toBeVisible();
  await expect(assistant.getByRole("button", { name: "New conversation" })).toBeVisible();

  const message = assistant.locator(".ai-message__content");
  await expect(message.getByRole("heading", { name: "Core tables" })).toBeVisible();
  await expect(message.locator("strong")).toContainText("Customers");
  await expect(message.locator("code")).toContainText("customer_id");
  await expect(message.locator("table")).toContainText("Customer records");
  await expect(message).not.toContainText("**Customers**");

  await assistant.getByRole("button", { name: "Assistant settings" }).click();
  const settings = page.getByRole("dialog", { name: "Model & permissions" });
  await expect(settings).toBeVisible();
  for (const name of ["liveCatalog", "structuredDataRead", "monitorQueries"]) {
    await expect(settings.locator(`input[type="checkbox"][name="${name}"]`)).toBeVisible();
  }
  await expect(settings.getByRole("checkbox", { name: "Select all actions", exact: true })).toBeVisible();
  const permissionResponse = await request.get("/api/v1/schemii/ai/settings");
  expect(permissionResponse.ok()).toBe(true);
  const descriptors = (await permissionResponse.json()).permissionActions;
  await expect(settings.locator("select[data-permission-action]")).toHaveCount(descriptors.length);
  await expect(settings.getByRole("heading", { name: "Model providers" })).toBeVisible();
  await expect(settings.getByText("OpenAI", { exact: true })).toBeVisible();
  const openAiProvider = settings.locator("details.ai-provider-card", { hasText: "OpenAI" });
  await openAiProvider.locator("summary").click();
  await expect(openAiProvider.getByRole("button", { name: /Connect|Disconnect/ })).toBeVisible();
  const saveSettings = settings.getByRole("button", { name: "Save settings" });
  await expect(saveSettings).toBeVisible();
  const saveBounds = await saveSettings.boundingBox();
  const viewport = page.viewportSize();
  expect(saveBounds).not.toBeNull();
  expect(viewport).not.toBeNull();
  expect(saveBounds.y + saveBounds.height).toBeLessThanOrEqual(viewport.height);
  const writePermission = settings.locator('select[name="query.draft"]');
  const originalWritePermission = await writePermission.inputValue();
  const changedWritePermission = originalWritePermission === "disabled" ? "ask" : "disabled";
  const approvalPreference = settings.locator('select[name="query.read"]');
  const originalApprovalPreference = await approvalPreference.inputValue();
  const changedApprovalPreference = originalApprovalPreference === "automatic" ? "ask" : "automatic";
  const createIndex = settings.locator('select[name="indexes.create"]');
  const deleteTable = settings.locator('select[name="tables.delete"]');
  const originalCreateIndex = await createIndex.inputValue();
  const originalDeleteTable = await deleteTable.inputValue();
  await createIndex.selectOption("automatic");
  await deleteTable.selectOption("disabled");
  await approvalPreference.selectOption(changedApprovalPreference);
  await writePermission.selectOption(changedWritePermission);
  const preferenceRequest = page.waitForRequest(request => request.method() === "PUT" && request.url().endsWith("/preferences"));
  await saveSettings.click();
  const savedCapabilities = (await preferenceRequest).postDataJSON().capabilities;
  expect(Object.keys(savedCapabilities).sort()).toEqual(["actionModes", "liveCatalog", "monitorQueries", "structuredDataRead"]);
  expect(savedCapabilities.actionModes["indexes.create"]).toBe("automatic");
  expect(savedCapabilities.actionModes["tables.delete"]).toBe("disabled");
  await expect(settings).toBeHidden();

  await assistant.getByRole("button", { name: "Assistant settings" }).click();
  await expect(writePermission).toHaveValue(changedWritePermission);
  await expect(approvalPreference).toHaveValue(changedApprovalPreference);
  await expect(createIndex).toHaveValue("automatic");
  await expect(deleteTable).toHaveValue("disabled");
  await createIndex.selectOption(originalCreateIndex);
  await deleteTable.selectOption(originalDeleteTable);
  await approvalPreference.selectOption(originalApprovalPreference);
  await writePermission.selectOption(originalWritePermission);
  await saveSettings.click();
  await expect(settings).toBeHidden();

  await assistant.getByRole("button", { name: "Conversation history" }).click();
  const history = page.getByRole("dialog", { name: "Conversation history" });
  await expect(history).toBeVisible();
  await expect(history.getByRole("button", { name: /Rename/ }).first()).toBeVisible();
  await history.getByRole("button", { name: "Done" }).click();

  const input = assistant.getByRole("textbox", { name: "Ask about this workspace" });
  await input.fill("first line");
  await input.press("Shift+Enter");
  await input.type("second line");
  await expect(input).toHaveValue("first line\nsecond line");
  expect(submitted).toBeNull();
  page.once("dialog", dialog => dialog.accept());
  await input.press("Enter");
  await expect.poll(() => submitted?.text).toBe("first line\nsecond line");
  await expect(input).toBeVisible();
  await input.fill("A follow-up remains available");
  await expect(input).toHaveValue("A follow-up remains available");
});
