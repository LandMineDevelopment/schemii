import { expect, test } from "@playwright/test";

test.use({ trace: "off", video: "off" });

async function setup(page, request, { providerId = "opencode", working = false, zenConnected = true } = {}) {
  const { workspaces } = await (await request.get("/api/v1/schemii/workspaces")).json();
  const workspace = workspaces.find(item => item.connectionId);
  const createdAt = new Date().toISOString();
  const state = { connected: false, apiConnected: false, zenConnected, apiKey: null,
    sent: [], cancelled: 0, working, final: false, login: "pending", statusScopes: [] };
  const chat = { id: `chat_${"3".repeat(32)}`, workspaceId: workspace.id, title: "Provider test", revision: 1,
    providerId, modelId: "test-model", capabilities: {}, status: working ? "working" : "idle", createdAt, updatedAt: createdAt };
  await page.route("**/api/v1/ai/status*", route => { const url = new URL(route.request().url()); state.statusScopes.push({ product: url.searchParams.get("product"), resourceId: url.searchParams.get("resourceId") }); return route.fulfill({ json: { enabled: true, healthy: true, providers: [
    { id: "opencode", name: "OpenCode Zen", available: state.zenConnected, authenticated: state.zenConnected,
      privacy: "Free models may train on your prompts. Do not send confidential data.",
      models: [{ id: "test-model", name: "Free model", status: "active" }] },
    { id: "openai-codex", name: "Codex", available: state.connected, authenticated: state.connected, models: [{ id: "test-model", name: "Codex model", status: "active" }] },
    { id: "openai", name: "OpenAI API", available: state.apiConnected, authenticated: state.apiConnected, models: [] },
  ] } }); });
  await page.route("**/api/v1/ai/prototype/**", route => {
    if (route.request().method() === "DELETE") { state.cancelled++; return route.fulfill({ json: {} }); }
    return route.fulfill({ json: route.request().method() === "POST" ? { id: "login-test" } : {
      status: state.login, userCode: "TEST-1234", verificationUrl: "https://auth.openai.com/codex/device",
    } });
  });
  await page.route("**/api/v1/ai/credentials/openai-codex", route => {
    state.connected = false; return route.fulfill({ json: {} });
  });
  await page.route("**/api/v1/ai/credentials/openai", route => {
    state.apiKey = route.request().postDataJSON().apiKey;
    state.apiConnected = true; return route.fulfill({ json: {} });
  });
  await page.route("**/api/v1/schemii/ai/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let json;
    if (path.endsWith("/settings")) json = { revision: 1, defaultProviderId: providerId, defaultModelId: "test-model", defaultCapabilities: {} };
    else if (path.endsWith("/chats")) json = { chats: [{ ...chat, status: state.working ? "working" : "idle" }] };
    else if (path.endsWith("/messages") && route.request().method() === "POST") {
      state.sent.push(route.request().postDataJSON()); json = { id: "turn-test" };
    } else if (path.endsWith("/messages")) json = { messages: [
      { id: "message-user", turnId: "turn-test", role: "user", sequence: 1, text: "Existing question", createdAt },
      ...(state.final ? [{ id: "message-final", turnId: "turn-test", role: "assistant", sequence: 2, text: "Complete streamed answer", createdAt }] : []),
    ] };
    else if (path.endsWith("/stream")) json = { turnId: "turn-test", text: "Partial streamed answer" };
    else if (path.endsWith("/activity")) json = { events: [], nextSequence: 0 };
    else if (path.endsWith("/proposals")) json = { proposals: [] };
    else if (path.endsWith("/operations")) json = { operations: [] };
    else if (path.endsWith("/transient-responses")) json = { responses: [] };
    else json = { ...chat, status: state.working ? "working" : "idle" };
    await route.fulfill({ json });
  });
  await page.goto(`/?workspace=${workspace.id}`);
  await page.getByRole("button", { name: "AI schema assistant" }).click();
  await expect(page.locator("#ai-assistant-messages")).toContainText("Existing question");
  return state;
}

test("free provider requires acknowledgement for every send and preserves cancelled input", async ({ page, request }) => {
  const state = await setup(page, request);
  await expect(page.locator("#ai-assistant-disclosure")).toContainText("Do not send confidential data");
  await page.locator("#ai-assistant-input").fill("Test prompt");
  page.once("dialog", dialog => dialog.dismiss());
  await page.locator("#ai-assistant-input").press("Enter");
  await expect(page.locator("#ai-assistant-input")).toHaveValue("Test prompt");
  expect(state.sent).toHaveLength(0);
  page.once("dialog", dialog => dialog.accept());
  await page.locator("#ai-assistant-input").press("Enter");
  await expect.poll(() => state.sent.length).toBe(1);
  expect(state.sent[0].acknowledgeProviderDataPolicy).toBe(true);
  await page.locator("#ai-assistant-input").fill("Another prompt");
  page.once("dialog", dialog => dialog.dismiss());
  await page.locator("#ai-assistant-input").press("Enter");
  expect(state.sent).toHaveLength(1);
});

test("Codex device connection can cancel, reconnect, complete, and disconnect", async ({ page, request }) => {
  const state = await setup(page, request);
  await page.getByRole("button", { name: "Assistant settings", exact: true }).click();
  const provider = page.locator(".ai-provider-card").filter({ hasText: "Codex" });
  await provider.locator("summary").click();
  await provider.getByRole("button", { name: "Connect Codex" }).click();
  await expect(provider).toContainText("TEST-1234");
  await expect(provider.getByRole("link", { name: "Continue at OpenAI" })).toHaveAttribute("href", "https://auth.openai.com/codex/device");
  await provider.getByRole("button", { name: "Cancel sign-in" }).click();
  await expect.poll(() => state.cancelled).toBe(1);
  await provider.locator("summary").click();
  await provider.getByRole("button", { name: "Connect Codex" }).click();
  state.connected = true; state.login = "succeeded";
  await expect(page.locator("#ai-settings-status")).toHaveText("Codex connected to your account.");
  await provider.locator("summary").click();
  await provider.getByRole("button", { name: "Disconnect", exact: true }).click();
  await expect.poll(() => state.connected).toBe(false);
});

test("transient stream text is replaced by one completed response", async ({ page, request }) => {
  const state = await setup(page, request, { working: true });
  await expect(page.locator("#ai-assistant-model")).toBeDisabled();
  // Settings remain available to inspect permissions while a turn runs.
  await expect(page.getByRole("button", { name: "Assistant settings", exact: true })).toBeEnabled();
  await expect(page.locator(".ai-message.assistant")).toContainText("Partial streamed answer");
  await expect(page.locator(".ai-message.assistant")).toContainText("Temporary · not saved");
  state.final = true; state.working = false;
  await expect(page.locator(".ai-message.assistant")).toHaveCount(1);
  await expect(page.locator(".ai-message.assistant")).toContainText("Complete streamed answer");
  await expect(page.locator("#ai-assistant-messages")).not.toContainText("Partial streamed answer");
  await expect(page.locator("#ai-assistant-model")).toBeEnabled();
});

test("API key input is masked and removed after connection", async ({ page, request }) => {
  const state = await setup(page, request);
  await page.getByRole("button", { name: "Assistant settings", exact: true }).click();
  const provider = page.locator(".ai-provider-card").filter({ hasText: "OpenAI API" });
  await provider.locator("summary").click();
  const key = provider.getByLabel("API key");
  await expect(key).toHaveAttribute("type", "password");
  await key.fill("test-secret-never-render");
  await provider.getByRole("button", { name: "Connect", exact: true }).click();
  await expect.poll(() => state.apiKey).toBe("test-secret-never-render");
  await expect(key).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("test-secret-never-render");
});

test("Zen settings explain administrator-managed access without a user key form", async ({ page, request }) => {
  const state = await setup(page, request, { zenConnected: false });
  expect(state.statusScopes).toContainEqual({ product: "schemii", resourceId: new URL(page.url()).searchParams.get("workspace") });
  await page.getByRole("button", { name: "Assistant settings", exact: true }).click();
  const provider = page.locator(".ai-provider-card").filter({ hasText: "OpenCode Zen" });
  if (!(await provider.evaluate(card => card.open))) await provider.locator("summary").click();
  await expect(provider).toContainText("An administrator manages Zen access for each person, app, and database.");
  await expect(provider).toContainText("Unavailable");
  await expect(provider.getByRole("textbox")).toHaveCount(0);
  await expect(provider.getByRole("button", { name: /Connect|Disconnect/ })).toHaveCount(0);
  state.zenConnected = true;
  await page.locator("[data-ai-settings-close]").first().click();
  await page.getByRole("button", { name: "Assistant settings", exact: true }).click();
  await expect(provider).toContainText("Available");
});

test("administrator saves an installation Zen key and grants one app and database", async ({ page, request }) => {
  const authStatus = await (await request.get("/api/v1/auth/status")).json();
  test.skip(!authStatus.enabled, "Account administration is disabled for this installation.");
  const users = await (await request.get("/api/v1/admin/accounts")).json();
  const person = users.find(user => user.is_admin && !user.disabled);
  const connectionId = `pg_${"a".repeat(32)}`;
  const connection = { userId: person.id, product: "schemoo", connectionOwnerId: person.id,
    connectionId, ownership: "user", name: "Browser database", database: "reports", username: "browser" };
  const state = { connected: false, generation: 0, grants: [], connections: [connection], keySent: null };
  await page.route("**/api/v1/admin/ai/zen**", route => {
    const url = new URL(route.request().url()), method = route.request().method();
    if (url.pathname.endsWith("/zen") && method === "GET") return route.fulfill({ json: {
      connected: state.connected, generation: state.generation, grants: state.grants, connections: state.connections,
    } });
    if (url.pathname.endsWith("/credential") && method === "PUT") {
      state.keySent = route.request().postDataJSON().apiKey; state.connected = true; state.generation++;
      return route.fulfill({ json: { connected: true, generation: state.generation } });
    }
    if (url.pathname.endsWith("/credential") && method === "DELETE") {
      state.connected = false; state.generation++;
      return route.fulfill({ json: { connected: false, generation: state.generation } });
    }
    if (url.pathname.endsWith("/grants")) {
      const grant = route.request().postDataJSON();
      if (method === "PUT") state.grants = [grant];
      else state.grants = [];
      return route.fulfill({ json: method === "PUT" ? grant : { deleted: true } });
    }
    throw new Error(`Unexpected Zen admin request: ${method} ${url.pathname}`);
  });
  await page.goto("/admin");
  const panel = page.getByRole("region", { name: "OpenCode Zen for this installation" });
  await expect(panel).toContainText("No installation key stored");
  const key = panel.getByLabel("Zen API key");
  await expect(key).toHaveAttribute("type", "password");
  await key.fill("browser-test-key");
  await panel.getByRole("button", { name: "Save installation key" }).click();
  await expect.poll(() => state.keySent).toBe("browser-test-key");
  await expect(panel).toContainText("Installation key stored");
  await expect(page.locator("body")).not.toContainText("browser-test-key");
  await expect(panel.getByLabel("Replace Zen API key")).toBeEmpty();

  await panel.getByRole("button", { name: "Grant Zen access" }).click();
  const editor = page.getByRole("dialog", { name: "Grant Zen access" });
  await editor.getByLabel("Person").selectOption(person.id);
  await editor.getByLabel("Application").selectOption("schemoo");
  await expect(editor.getByLabel("Database profile")).toHaveValue("0");
  await editor.getByRole("button", { name: "Add grant" }).click();
  await expect.poll(() => state.grants).toEqual([{ userId: person.id, product: "schemoo",
    connectionOwnerId: person.id, connectionId }]);
  await expect(panel).toContainText("Browser database · reports");
  await panel.getByRole("button", { name: "Revoke" }).click();
  await page.getByRole("dialog", { name: "Revoke Zen access?" }).getByRole("button", { name: "Revoke access" }).click();
  await expect.poll(() => state.grants).toHaveLength(0);
  await panel.getByRole("button", { name: "Remove installation key" }).click();
  await page.getByRole("dialog", { name: "Remove installation Zen key?" }).getByRole("button", { name: "Remove key" }).click();
  await expect(panel).toContainText("No installation key stored");
});
