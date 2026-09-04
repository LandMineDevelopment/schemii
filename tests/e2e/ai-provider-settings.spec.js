import { expect, test } from "@playwright/test";

async function setup(page, request, { providerId = "opencode", working = false } = {}) {
  const { workspaces } = await (await request.get("/api/v1/schemii/workspaces")).json();
  const workspace = workspaces.find(item => item.connectionId);
  const createdAt = new Date().toISOString();
  const state = { connected: false, apiConnected: false, apiKey: null, sent: [], cancelled: 0, working, final: false, login: "pending" };
  const chat = { id: `chat_${"3".repeat(32)}`, workspaceId: workspace.id, title: "Provider test", revision: 1,
    providerId, modelId: "test-model", capabilities: {}, status: working ? "working" : "idle", createdAt, updatedAt: createdAt };
  await page.route("**/api/v1/ai/status", route => route.fulfill({ json: { enabled: true, healthy: true, providers: [
    { id: "opencode", name: "OpenCode Zen free", available: true, privacy: "Free models may train on your prompts. Do not send confidential data.", models: [{ id: "test-model", name: "Free model", status: "active" }] },
    { id: "openai-codex", name: "Codex", available: state.connected, authenticated: state.connected, models: [{ id: "test-model", name: "Codex model", status: "active" }] },
    { id: "openai", name: "OpenAI API", available: state.apiConnected, authenticated: state.apiConnected, models: [] },
  ] } }));
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
  await expect(page.getByRole("button", { name: "Assistant settings", exact: true })).toBeDisabled();
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
