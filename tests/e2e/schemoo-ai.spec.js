import { expect, test } from "@playwright/test";
import { importedDraft } from "../../src/schemii/schemoo/web/model-draft.js";
import { splitDraft } from "../../src/schemii/schemoo/web/model-state.js";

const modelId = "model_assistant_fixture", chatId = "chat_assistant_fixture";
async function fixture(page, { pending = false, shell = false, modelDenied = false, emptyModels = false,
  completed = false, zenConnect = false } = {}) {
  const actions = [
    { id: "read_model", label: "Inspect model", group: "Read", description: "Read the saved model." },
    { id: "update_model", label: "Change model", group: "Write", description: "Save semantic model changes." },
  ];
  let settings = { actions, modes: { read_model: "automatic", update_model: "ask" }, providerId: "openai", aiModelId: "fixture-model" };
  let chat = { id: chatId, modelId, title: "Model review", providerId: "openai", aiModelId: "fixture-model", modes: { ...settings.modes }, revision: 1, modelRevision: 1, status: pending ? "waiting_approval" : "idle", messages: [{ id: "msg_1", role: "assistant", text: "## Model review\n\nUse **relationships** and `filters`.\n\n- First point\n- Second point\n\n```sql\nSELECT 1;\n```", createdAt: "2026-09-08T12:00:00Z" }], activity: [], stream: "", pending: pending ? { id: "pending_1", actions: [{ operation: "update_model", args: { label: "Staffing" } }] } : null };
  if (completed) {
    chat.messages.push({ id: "question", role: "user", text: "Check this model", createdAt: "2026-09-08T12:01:00Z" },
      { id: "answer", role: "assistant", text: "The model is valid.", createdAt: "2026-09-08T12:01:35Z" });
    chat.progress = { turnId: "completed_turn", startedAt: "2026-09-08T12:01:00Z", finishedAt: "2026-09-08T12:01:35Z", state: "completed", stages: [{ id: "validation", label: "Validated model", state: "completed" }] };
  }
  const requests = [];
  await page.route("**/api/v1/ai/status*", route => route.fulfill({ json: { healthy: true, providers: [{ id: "openai", name: "OpenAI", available: !emptyModels, authenticated: true, models: [{ id: "fixture-model", name: "Fixture model", reasoningLevels: ["low", "medium", "high", "max"], status: modelDenied && chat.status === "failed" ? "unavailable" : "active" }, { id: "second-model", name: "Second model", status: "active" }] }, { id: "openai-codex", name: "Codex", available: false, authenticated: false, models: [] },
    ...(zenConnect ? [{ id: "opencode", name: "OpenCode Zen", available: false, authenticated: false, privacy: "Free Zen models may use prompts for training.", models: [{ id: "big-pickle", name: "Big Pickle", status: "active" }] }] : [])] } }));
  await page.route("**/api/v1/schemoo/ai/**", async route => {
    const url = new URL(route.request().url()), method = route.request().method();
    const body = method === "GET" || method === "DELETE" ? null : route.request().postDataJSON();
    requests.push({ path: url.pathname, method, body });
    if (url.pathname.endsWith("/settings")) {
      if (method === "PUT") settings = { ...settings, ...body };
      return route.fulfill({ json: settings });
    }
    if (url.pathname.endsWith("/chats")) {
      if (method === "POST") { chat = { ...chat, id: "chat_new", messages: [], status: "idle", ...body }; return route.fulfill({ json: chat }); }
      return route.fulfill({ json: { chats: [{ ...chat, updatedAt: "2026-09-08T12:00:00Z" }] } });
    }
    if (url.pathname.endsWith("/preferences")) chat = { ...chat, ...body, status: "idle", pending: null, stream: "", revision: chat.revision + 1 };
    if (url.pathname.endsWith("/messages")) chat = { ...chat, status: "working", revision: chat.revision + 1, messages: [...chat.messages, { id: `msg_${chat.messages.length + 1}`, role: "user", text: body.text }], stream: "Looking at the **saved model**…", progress: { turnId: "turn_fixture", startedAt: new Date().toISOString(), state: "working", stages: [{ id: "context", label: "Model context ready", state: "completed" }, { id: "provider", label: "Writing response", state: "running" }] } };
    if (url.pathname.endsWith("/cancel")) chat = { ...chat, status: "idle", stream: "", pending: null, progress: chat.progress ? { ...chat.progress, state: "cancelled", finishedAt: new Date().toISOString(), stages: chat.progress.stages.map(stage => ({ ...stage, state: stage.state === "running" ? "cancelled" : stage.state })) } : null, revision: chat.revision + 1 };
    if (url.pathname.endsWith("/approval")) chat = { ...chat, status: "idle", pending: null, revision: chat.revision + 1, modelRevision: body.approved ? 2 : 1, activity: [{ id: "action_1", operation: "update_model", status: "succeeded", message: "Saved model revision 2." }] };
    if (modelDenied && method === "GET" && chat.status === "working") chat = { ...chat, status: "failed", stream: "", progress: null, revision: chat.revision + 1,
      error: "This model is not available through the connected provider account. Choose another model; your conversation is kept." };
    return route.fulfill({ json: chat });
  });
  await page.route("**/assistant-fixture", route => route.fulfill({ contentType: "text/html", body: `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/assets/common/ui.css"><link rel="stylesheet" href="/assets/common/searchable-select.css"><link rel="stylesheet" href="/assets/common/model-assistant.css"></head><body><button id="open">Open model assistant</button><script type="module">import { createModelAssistant } from '/assets/common/model-assistant.js'; window.modelRefreshes = []; window.currentModelId = '${modelId}'; window.assistant = createModelAssistant({trigger:document.querySelector('#open'),getModelId:()=> window.currentModelId,onModelChanged:async(id,revision)=>{window.modelRefreshes.push({id,revision});return 'Saved model refreshed.';}});</script></body></html>` }));
  if (shell) {
    const catalog = { database: "fixture", namespace: "public", fingerprint: "fixture-v1", notice: "Isolated browser fixture", tables: [{ name: "people", primaryKey: ["id"], columns: [{ name: "id", dataType: "integer", nullable: false }, { name: "name", dataType: "text", nullable: false }] }], relationships: [], positions: [{ name: "people", x: 80, y: 80 }] };
    const saved = { id: modelId, connectionId: "pg_fixture", namespace: "public", name: "Assistant shell fixture", revision: 1, layoutRevision: 1, exploreRevision: 1, catalogFingerprint: catalog.fingerprint, ...splitDraft(importedDraft(catalog)) };
    await page.route(`**/api/v1/schemoo/models/${modelId}`, route => route.fulfill({ json: { ...saved, revision: chat.modelRevision } }));
    await page.route("**/api/v1/schemoo/catalog?*", route => route.fulfill({ json: catalog }));
    await page.route(`**/api/v1/schemoo/models/${modelId}/validate`, route => route.fulfill({ json: { sql: 'SELECT name FROM public.people;', usedRelationships: [], grain: "one row per person", warnings: [], sourceIssues: [], activeScopes: [] } }));
  }
  await page.goto(shell ? `/schemoo?model=${modelId}` : "/assistant-fixture");
  if (shell) await expect(page.locator(".sc-node")).toHaveCount(1);
  await page.getByRole("button", { name: "Open model assistant", exact: true }).click();
  await expect(page.locator(".model-ai-status")).toHaveText(pending ? "Review batch" : "Ready");
  await expect.poll(() => page.locator(".model-ai").evaluate(node => Math.abs(new DOMMatrixReadOnly(getComputedStyle(node).transform).m41))).toBeLessThan(.1);
  return requests;
}

test("shared model assistant shows administrator-managed Zen access without a user key form", async ({ page }) => {
  const scopes = [];
  page.on("request", request => {
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/ai/status") scopes.push({ product: url.searchParams.get("product"), resourceId: url.searchParams.get("resourceId") });
  });
  await fixture(page, { zenConnect: true });
  expect(scopes).toContainEqual({ product: "schemoo", resourceId: modelId });
  await page.getByRole("button", { name: "Assistant settings", exact: true }).click();
  const provider = page.locator(".model-ai-provider").filter({ hasText: "OpenCode Zen" });
  if (!(await provider.evaluate(card => card.open))) await provider.locator("summary").click();
  await expect(provider).toContainText("An administrator manages Zen access for each person, app, and database.");
  await expect(provider).toContainText("Unavailable");
  await expect(provider.getByRole("textbox")).toHaveCount(0);
  await expect(provider.getByRole("button", { name: /Connect|Disconnect/ })).toHaveCount(0);
});

test("completed tracker stays between its prompt and answer, including reopened history", async ({ page }) => {
  await fixture(page, { completed: true });
  // Scope by the actual message parent, independent of product container classes.
  const actualOrder = () => page.locator('[data-message-id="answer"]').evaluate(node => [...node.parentElement.children].map(child => child.classList.contains("ai-run") ? "tracker" : child.dataset.messageId));
  expect(await actualOrder()).toEqual(["msg_1", "question", "tracker", "answer"]);
  await page.locator(".ai-run").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `artifacts/tracker-order-${test.info().project.name}.png` });
  await page.reload();
  await page.getByRole("button", { name: "Open model assistant", exact: true }).click();
  await expect(page.locator('[data-message-id="answer"]')).toBeVisible();
  expect(await actualOrder()).toEqual(["msg_1", "question", "tracker", "answer"]);
});

test("shared model assistant formats messages, sends with Enter, streams, and cancels", async ({ page }) => {
  const requests = await fixture(page);
  await expect(page.locator(".model-ai-markdown h4")).toHaveText("Model review");
  await expect(page.locator(".model-ai-markdown strong")).toHaveText("relationships");
  await expect(page.locator(".model-ai-markdown pre code")).toHaveText("SELECT 1;");
  await expect(page.locator(".model-ai-message .ai-message__surface")).toHaveCount(1);
  await expect(page.locator(".model-ai-message time")).not.toBeEmpty();
  await page.evaluate(() => { navigator.clipboard.writeText = async text => { window.copiedMessage = text; }; });
  await page.locator(".ai-message__copy").click();
  await expect.poll(() => page.evaluate(() => window.copiedMessage)).toContain("## Model review");
  await page.locator('[data-message-id="msg_1"]').evaluate(node => { window.originalMessageNode = node; });
  const input = page.getByRole("textbox", { name: "Message to model assistant" });
  await input.fill("First line"); await input.press("Shift+Enter"); await input.press("a");
  await expect(input).toHaveValue("First line\na");
  expect(requests.filter(item => item.path.endsWith("/messages"))).toHaveLength(0);
  await input.press("Enter");
  await expect(page.locator(".model-ai-status")).toHaveText("Working");
  expect(await page.locator('[data-message-id="msg_1"]').evaluate(node => node === window.originalMessageNode)).toBe(true);
  await expect(page.locator('[data-message-id="msg_1"]')).toHaveCSS("animation-name", "none");
  await expect(page.locator(".model-ai-stream strong")).toHaveText("saved model");
  await expect(page.getByRole("button", { name: "Send", exact: true, includeHidden: true })).toBeHidden();
  await expect(page.locator(".ai-run.working")).toContainText("Writing response");
  await expect(page.locator(".ai-progress-grid i")).toHaveCount(25);
  expect(await page.locator(".ai-run").evaluate(node => Boolean(node.compareDocumentPosition(document.querySelector(".model-ai-stream")) & Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);
  await expect(page.locator(".ai-progress-grid i").first()).toHaveCSS("animation-name", "ai-dot-wave");
  await expect(page.locator(".ai-run-title")).toHaveCSS("animation-name", "ai-text-shimmer");
  await expect(page.locator(".ai-run-step.running .ai-run-step-marker")).toHaveCSS("animation-name", "ai-stage-pulse");
  await page.locator(".ai-run").evaluate(node => { window.originalActivity = node; window.originalDots = node.querySelector(".ai-progress-grid"); });
  await page.locator(".ai-run summary").click();
  await expect.poll(() => requests.filter(item => item.method === "GET" && item.path.endsWith(chatId)).length).toBeGreaterThan(2);
  expect(await page.locator(".ai-run").evaluate(node => node === window.originalActivity && node.querySelector(".ai-progress-grid") === window.originalDots)).toBe(true);
  await expect(page.locator(".ai-run")).not.toHaveAttribute("open");
  await page.locator(".ai-run summary").click();
  await page.screenshot({ path: `artifacts/schemoo-ai-working-${test.info().project.name}.png` });
  expect(requests.find(item => item.path.endsWith("/messages")).body.text).toBe("First line\na");
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await expect(page.locator(".model-ai-status")).toHaveText("Ready");
  await expect(page.getByRole("button", { name: "Stop", exact: true })).toBeHidden();
  await expect(page.locator(".ai-run.cancelled")).toContainText("Turn stopped");
  await expect(page.locator(".ai-run-title")).toHaveCSS("animation-name", "none");
  await expect.poll(() => page.locator(".model-ai").evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  await page.screenshot({ path: `artifacts/schemoo-ai-${test.info().project.name}.png` });
  await page.getByRole("button", { name: "Close assistant", exact: true }).click();
  await expect(page.getByRole("button", { name: "Open model assistant", exact: true })).toBeFocused();
});

test("provider denial refreshes availability without changing the selected model or losing the chat", async ({ page }) => {
  await fixture(page, { modelDenied: true });
  await expect(page.locator(".model-ai-markdown h4")).toHaveText("Model review");
  await page.locator(".model-ai-composer textarea").fill("Check the model");
  await page.locator(".model-ai-composer textarea").press("Enter");
  await expect(page.locator(".model-ai-failure")).toContainText("not available through the connected provider account");
  const picker = page.getByRole("combobox", { name: "Assistant model", exact: true });
  await expect(picker).toHaveValue(/fixture-model · unavailable/);
  await expect(page.locator(".model-ai-message--user")).toContainText("Check the model");
  await picker.click();
  await page.getByRole("option", { name: "Second model · OpenAI" }).click();
  await expect(picker).toHaveValue("Second model · OpenAI");
  await expect(page.locator(".model-ai-message--user")).toContainText("Check the model");
});

test("opening model choices checks availability once, preserves selection, and refreshes each reopening", async ({ page, isMobile }) => {
  const requests = await fixture(page);
  let checks = 0, finish;
  const checked = { healthy: true, providers: [{ id: "openai", name: "OpenAI", available: true, models: [{ id: "second-model", name: "Second model", status: "active" }] }] };
  await page.route(/\/api\/v1\/ai\/status\?.*refresh=true/, async route => {
    checks += 1;
    if (checks === 1) await new Promise(resolve => { finish = resolve; });
    await route.fulfill({ json: checked });
  });
  const picker = page.getByRole("combobox", { name: "Assistant model", exact: true });
  if (isMobile) await picker.tap(); else await picker.click();
  await expect(page.getByText("Checking available models…", { exact: true })).toBeVisible();
  await picker.press("ArrowDown");
  await picker.press("Enter");
  expect(checks).toBe(1);
  expect(requests.filter(item => item.path.endsWith("/preferences"))).toHaveLength(0);
  finish();
  await expect(page.getByRole("option", { name: "Second model · OpenAI" })).toBeVisible();
  await expect(picker).toHaveValue(/fixture-model · unavailable/);
  await expect(page.getByRole("option", { name: /fixture-model · unavailable/ })).toBeDisabled();
  expect(requests.filter(item => item.path.endsWith("/preferences"))).toHaveLength(0);
  await picker.press("Escape");
  await picker.press("ArrowDown");
  await expect.poll(() => checks).toBe(2);
  await expect(page.getByRole("option", { name: "Second model · OpenAI" })).toBeVisible();
  await picker.press("ArrowDown");
  await picker.press("Enter");
  await expect.poll(() => requests.filter(item => item.path.endsWith("/preferences")).at(-1)?.body.aiModelId).toBe("second-model");
  await expect(picker).toHaveValue("Second model · OpenAI");
  expect(checks).toBe(2);
});

test("model choices recover an empty catalog and keep prior choices on a failed check", async ({ page }) => {
  const requests = await fixture(page, { emptyModels: true });
  let checks = 0;
  await page.route(/\/api\/v1\/ai\/status\?.*refresh=true/, route => {
    checks += 1;
    if (checks === 2) return route.fulfill({ status: 503, json: { error: { message: "Provider check unavailable." } } });
    return route.fulfill({ json: { healthy: true, message: checks === 4 ? "Account catalog check failed." : null,
      providers: [{ id: "openai", name: "OpenAI", available: true, catalogError: checks === 4 ? "Account catalog check failed." : null,
        models: [{ id: "second-model", name: "Second model", status: "active" }] }] } });
  });
  const picker = page.getByRole("combobox", { name: "Assistant model", exact: true });
  await expect(picker).toBeEnabled();
  await picker.click();
  await expect(page.getByRole("option", { name: "Second model · OpenAI" })).toBeVisible();
  await picker.press("Escape");
  await picker.press("Enter");
  await expect(page.getByText(/Provider check unavailable.*Showing the last available model list/)).toBeVisible();
  await expect(page.getByRole("option", { name: "Second model · OpenAI" })).toBeEnabled();
  await expect(picker).toHaveValue(/fixture-model · unavailable/);
  await picker.press("Tab");
  await expect(page.getByRole("button", { name: "Retry model check" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect.poll(() => checks).toBe(3);
  await expect(page.getByRole("button", { name: "Retry model check" })).toHaveCount(0);
  await expect(page.getByRole("option", { name: "Second model · OpenAI" })).toBeVisible();
  expect(requests.filter(item => item.path.endsWith("/preferences"))).toHaveLength(0);
  await picker.press("Escape");
  await picker.press("Enter");
  await expect(page.getByText(/Account catalog check failed.*Showing the last available model list/)).toBeVisible();
  await page.getByRole("option", { name: "Second model · OpenAI" }).click();
  await expect.poll(() => requests.filter(item => item.path.endsWith("/preferences")).at(-1)?.body.aiModelId).toBe("second-model");
  expect(checks).toBe(4);
});

test("batch review names every action and refreshes the saved model after approval", async ({ page }) => {
  const requests = await fixture(page, { pending: true });
  const approval = page.getByRole("region", { name: "Action batch awaiting approval" });
  await expect(approval).toContainText("Change model");
  await expect(approval.locator("details")).not.toHaveAttribute("open");
  await approval.locator("summary").click();
  await expect(approval).toContainText("Staffing");
  await expect(page.getByRole("button", { name: "Send", exact: true, includeHidden: true })).toBeHidden();
  await page.screenshot({ path: `artifacts/schemoo-ai-approval-${test.info().project.name}.png` });
  await page.getByRole("button", { name: "Approve batch", exact: true }).click();
  await expect(approval).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => window.modelRefreshes)).toEqual([{ id: modelId, revision: 2 }]);
  expect(requests.find(item => item.path.endsWith("/approval")).body).toEqual({ pendingId: "pending_1", approved: true, expectedRevision: 1 });
});

test("shared activity respects reduced motion without hiding work status", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await fixture(page);
  await page.getByRole("textbox", { name: "Message to model assistant" }).fill("Inspect the model");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator(".ai-run.working")).toContainText("Writing response");
  await expect(page.locator(".ai-progress-grid i").first()).toHaveCSS("animation-name", "none");
  await expect(page.locator(".ai-run-title")).toHaveCSS("animation-name", "none");
  await expect(page.locator(".ai-run-step.running .ai-run-step-marker")).toHaveCSS("animation-name", "none");
  await page.getByRole("button", { name: "Stop assistant turn", exact: true }).click();
  await expect(page.locator(".ai-run.cancelled")).toBeVisible();
});

test("permissions, provider selection, and history retain conversation context", async ({ page }) => {
  const requests = await fixture(page);
  const summary = page.getByRole("button", { name: "Assistant permissions", exact: true });
  await expect(summary).toContainText("2 of 2 actions");
  await summary.click();
  const settings = page.getByRole("dialog", { name: "Assistant settings" });
  await expect(settings.getByRole("button", { name: "Connect Codex", exact: true })).toBeVisible();
  await settings.getByRole("combobox", { name: "Change model", exact: true }).selectOption("disabled");
  await settings.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(settings).toBeHidden();
  expect(requests.find(item => item.path.endsWith("/preferences")).body.modes.update_model).toBe("disabled");
  await page.getByRole("combobox", { name: "Assistant model", exact: true }).click();
  await page.getByRole("option", { name: "Second model · OpenAI" }).click();
  await expect.poll(() => requests.filter(item => item.path.endsWith("/preferences")).at(-1)?.body.aiModelId).toBe("second-model");
  await page.getByRole("button", { name: "Conversation history", exact: true }).click();
  const history = page.getByRole("dialog", { name: "Conversation history" });
  await expect(history).toContainText("Model review");
  await history.getByRole("button", { name: "Model review", exact: true }).click();
  await expect(history).toBeHidden();
  await expect(page.locator(".model-ai-markdown strong")).toHaveText("relationships");
});

test("switching the selected subject clears the previous conversation notice", async ({ page }) => {
  await fixture(page);
  await page.getByRole("button", { name: "New conversation" }).click();
  await expect(page.locator(".model-ai-notice")).toContainText("Started a new conversation");
  await page.evaluate(async () => {
    window.currentModelId = "model_another_fixture";
    await window.assistant.modelChanged();
  });
  await expect(page.locator(".model-ai-notice")).toBeHidden();
  await expect(page.locator(".model-ai-empty")).toBeVisible();
});

test("permissions can revoke a pending batch and model selection can stop an active turn", async ({ page }) => {
  const requests = await fixture(page, { pending: true });
  await page.getByRole("button", { name: "Assistant settings", exact: true }).click();
  const settings = page.getByRole("dialog", { name: "Assistant settings" });
  await expect(settings).toContainText("Saving permissions stops the current turn");
  await settings.getByRole("combobox", { name: "Change model", exact: true }).selectOption("disabled");
  await settings.getByRole("button", { name: "Save settings", exact: true }).click();
  await expect(page.getByRole("region", { name: "Action batch awaiting approval" })).toHaveCount(0);
  await expect(page.locator(".model-ai-status")).toHaveText("Ready");
  const input = page.getByRole("textbox", { name: "Message to model assistant" });
  await input.fill("Explain the model"); await input.press("Enter");
  await expect(page.locator(".model-ai-status")).toHaveText("Working");
  await page.getByRole("combobox", { name: "Assistant model", exact: true }).click();
  await page.getByRole("option", { name: "Second model · OpenAI" }).click();
  await expect(page.locator(".model-ai-status")).toHaveText("Ready");
  await expect(page.locator(".model-ai-stream")).toHaveCount(0);
  expect(requests.filter(item => item.path.endsWith("/preferences")).at(-1).body.aiModelId).toBe("second-model");
  await expect(page.locator(".model-ai-message--user")).toContainText("Explain the model");
});

test("production Schemoo toolbar opens the assistant and preserves a dirty draft after approval", async ({ page }) => {
  const errors = []; page.on("pageerror", error => errors.push(error.message));
  await fixture(page, { pending: true, shell: true });
  await page.getByRole("button", { name: "Close assistant", exact: true }).click();
  await page.getByRole("textbox", { name: "Model name", exact: true }).fill("My unsaved model name");
  await expect(page.getByRole("button", { name: "Save model", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Open model assistant", exact: true }).click();
  await page.getByRole("button", { name: "Approve batch", exact: true }).click();
  await expect(page.locator(".model-ai-notice")).toContainText("Your local edits are preserved");
  await page.getByRole("button", { name: "Close assistant", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Model name", exact: true })).toHaveValue("My unsaved model name");
  await expect(page.getByRole("button", { name: "Save model", exact: true })).toBeDisabled();
  await expect(page.locator(".sc-node")).toHaveCount(1);
  expect(errors).toEqual([]);
});


test('reasoning level persists independently and resets for an unsupported model', async ({ page }) => {
  const requests = await fixture(page);
  const reasoning = page.getByRole('combobox', { name: 'Assistant reasoning level', exact: true });
  await expect(reasoning).toHaveValue('default');
  await reasoning.selectOption('high');
  await expect(reasoning).toHaveValue('high');
  expect(requests.filter(item => item.path.endsWith('/preferences')).at(-1).body.reasoningEffort).toBe('high');
  await page.getByRole('button', { name: 'Assistant settings', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Assistant settings', exact: true });
  await expect(dialog.getByRole('combobox', { name: 'Reasoning level', exact: true })).toHaveValue('high');
  await dialog.getByRole('combobox', { name: 'Reasoning level', exact: true }).selectOption('low');
  await dialog.getByRole('button', { name: 'Save settings', exact: true }).click();
  await expect(reasoning).toHaveValue('low');
  await page.getByRole('combobox', { name: 'Assistant model', exact: true }).click();
  await page.getByRole('option', { name: 'Second model · OpenAI', exact: true }).click();
  await expect(reasoning).toHaveValue('default');
  await expect(reasoning).toBeDisabled();
  expect(requests.filter(item => item.path.endsWith('/preferences')).at(-1).body.reasoningEffort).toBe('default');
});
