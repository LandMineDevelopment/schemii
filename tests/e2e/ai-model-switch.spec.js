import { expect, test } from "@playwright/test";

test("switch providers in the current conversation without losing history", async ({ page, request }, testInfo) => {
  const { workspaces } = await (await request.get("/api/v1/schemii/workspaces")).json();
  const workspace = workspaces.find(item => item.name === "schemii_migration_demo.public") || workspaces.find(item => item.connectionId);
  const createdAt = new Date().toISOString();
  let chat = { id: `chat_${"1".repeat(32)}`, workspaceId: workspace.id, title: "Model switch test", revision: 1,
    providerId: "first", modelId: "one", capabilities: {}, status: "idle", createdAt, updatedAt: createdAt };
  let settings = { revision: 1, defaultProviderId: "first", defaultModelId: "one", defaultCapabilities: {} };
  let switches = 0;
  await page.route("**/api/v1/ai/status*", route => route.fulfill({ json: { healthy: true, enabled: true,
    providers: ["first", "second"].map((id, i) => ({ id, name: id, available: true,
      models: [{ id: i ? "two" : "one", name: i ? "Second model" : "First model", status: "active" }] })) } }));
  await page.route("**/api/v1/schemii/ai/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let json;
    if (path.endsWith("/settings")) json = settings;
    else if (path.endsWith("/preferences")) {
      const body = route.request().postDataJSON();
      expect(body.expectedChatRevision).toBe(chat.revision);
      chat = { ...chat, providerId: body.providerId, modelId: body.modelId, revision: chat.revision + 1 };
      settings = { ...settings, revision: settings.revision + 1 };
      switches++; json = { chat, settings, startedNewConversation: false };
    } else if (path.endsWith("/chats")) {
      expect(route.request().method()).toBe("GET"); json = { chats: [chat] };
    } else if (path.endsWith("/messages")) json = { messages: [{ id: "msg_test", chatId: chat.id, turnId: "turn_test",
      role: "user", sequence: 1, text: "Keep this conversation", createdAt }] };
    else if (path.endsWith("/activity")) json = { events: [], nextSequence: 0 };
    else if (path.endsWith("/proposals")) json = { proposals: [] };
    else if (path.endsWith("/operations")) json = { operations: [] };
    else if (path.endsWith("/transient-responses")) json = { responses: [] };
    else json = chat;
    await route.fulfill({ json });
  });
  await page.goto(`/?workspace=${workspace.id}`);
  await page.getByRole("button", { name: "AI schema assistant" }).click();
  await expect(page.locator("#ai-assistant-messages")).toContainText("Keep this conversation");
  await page.getByRole("combobox", { name: "AI model", exact: true }).click();
  await page.getByRole("option", { name: "Second model · second" }).click();
  await expect(page.locator("#ai-assistant-model option:checked")).toHaveText("Second model · second");
  await expect(page.locator("#ai-assistant-messages")).toContainText("Keep this conversation");
  await expect.poll(() => switches).toBe(1);
  await page.screenshot({ path: testInfo.outputPath("model-switch.png"), animations: "disabled" });
});
