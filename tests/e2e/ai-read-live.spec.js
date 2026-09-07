import { expect, test } from "@playwright/test";

test("live assistant summarizes after exhausting tool rounds without another user prompt", async ({ page, request }) => {
  test.skip(process.env.SCHEMII_LIVE_AI !== "1", "Requires explicit real-provider test opt-in");
  test.setTimeout(240_000);
  const spaces = await (await request.get("/api/v1/schemii/workspaces")).json();
  const workspace = spaces.workspaces.find(item => item.database === "schemii_test" && item.namespace === "bookstore");
  expect(workspace).toBeTruthy();
  const response = await request.post(`/api/v1/schemii/workspaces/${workspace.id}/ai/chats`, { data: {
    title: "E2E tool limit summary", providerId: "openai-codex", modelId: "gpt-5.4-mini",
    capabilities: { actionModes: { "query.read": "automatic" }, structuredDataRead: true },
  } });
  expect(response.ok()).toBe(true);
  const chat = await response.json();
  const api = `/api/v1/schemii/ai/chats/${chat.id}`;
  try {
    await page.goto(`/?workspace=${workspace.id}`);
    await page.getByRole("button", { name: "AI schema assistant" }).click();
    await expect(page.locator("#ai-assistant-status")).toHaveText("Ready");
    await page.locator("#ai-assistant-input").fill("Test only; never inspect tables or change anything. Run 12 sequential diagnostic steps. Each step must use a separate schemii_read_query call containing exactly one literal SELECT N AS step_number (N starts at 1), wait for its result, then run the next step. Do not batch steps. Afterward summarize how many steps completed. If the server stops tools early, summarize the returned steps and clearly explain that the investigation is incomplete.");
    await page.locator("#ai-assistant-input").press("Enter");
    await expect.poll(async () => (await (await request.get(`${api}/operations`)).json()).operations.length, { timeout: 100_000 }).toBeGreaterThan(0);
    await expect.poll(async () => (await (await request.get(api)).json()).status, { timeout: 180_000 }).toBe("idle");
    await expect(page.locator("#ai-assistant-status")).toHaveText("Ready");
    const events = (await (await request.get(`${api}/activity`)).json()).events;
    expect(events.some(event => event.payload.code === "ai_tool_round_limit")).toBe(true);
    expect(events.some(event => event.kind === "error")).toBe(false);
    const messages = (await (await request.get(`${api}/messages`)).json()).messages;
    expect(messages.filter(message => message.role === "user")).toHaveLength(1);
    expect(messages.filter(message => message.role === "assistant")).toHaveLength(1);
    const answer = page.locator("#ai-assistant-messages .ai-message.assistant").last();
    await expect(answer).toContainText(/limit|incomplete/i);
    const transient = (await (await request.get(`${api}/transient-responses`)).json()).responses;
    expect(transient).toHaveLength(1);
  } finally {
    await request.delete(api);
  }
});

// Explicit opt-in: this uses a real authenticated model. Only literal SELECTs
// are requested; the fixture never changes a target database or user settings.
test("live assistant batches reads, analyzes them, and resumes approval", async ({ page, request }, testInfo) => {
  test.skip(process.env.SCHEMII_LIVE_AI !== "1", "Requires explicit real-provider test opt-in");
  test.setTimeout(240_000);
  const spaces = await (await request.get("/api/v1/schemii/workspaces")).json();
  const workspace = spaces.workspaces.find(item => item.database === "schemii_test" && item.namespace === "bookstore");
  expect(workspace).toBeTruthy();
  const created = await request.post(`/api/v1/schemii/workspaces/${workspace.id}/ai/chats`, { data: {
    title: "E2E live read workflow", providerId: "openai-codex", modelId: "gpt-5.4-mini",
    capabilities: { rawSqlRead: true, structuredDataRead: true, readApprovalRequired: false },
  } });
  expect(created.ok(), await created.text()).toBe(true);
  let chat = await created.json();
  const api = `/api/v1/schemii/ai/chats/${chat.id}`;
  let sent = 0;
  const send = async text => {
    await expect(page.locator("#ai-assistant-status")).toHaveText("Ready");
    await page.locator("#ai-assistant-input").fill(text);
    await page.locator("#ai-assistant-input").press("Enter");
    sent += 1;
    await expect.poll(async () => (await (await request.get(`${api}/messages`)).json()).messages.filter(message => message.role === "user").length).toBe(sent);
  };
  const waitIdle = async () => {
    await expect.poll(async () => (await (await request.get(api)).json()).status, { timeout: 100_000 }).toBe("idle");
    await expect(page.locator("#ai-assistant-status")).toHaveText("Ready");
    await expect(page.locator("#ai-assistant-messages .ai-message.assistant")).toHaveCount(sent, { timeout: 10_000 });
    await expect(page.locator("#ai-assistant-messages .ai-message.assistant").last()).toContainText(/42|84/);
  };
  try {
    await page.goto(`/?workspace=${workspace.id}`);
    await page.getByRole("button", { name: "AI schema assistant" }).click();
    await send("Test only, do not inspect any tables. Use exactly ONE schemii_read_query tool call containing these TWO labeled queries: first: SELECT 21 * 2 AS answer; second: SELECT 42 * 2 AS answer. After both results return, compare the values in your answer.");
    await waitIdle();
    const operations = (await (await request.get(`${api}/operations`)).json()).operations;
    expect(operations).toHaveLength(1);
    expect(operations[0].resultSummary.results).toHaveLength(2);
    expect(operations[0].resultSummary.results.every(result => result.runId)).toBe(true);
    // The second turn must reference the prior runs without a separate prompt/dialog.
    await send("Use schemii_list_read_runs then schemii_get_read_results to retrieve both previous runs together. Do not execute new SQL. What is the difference between their answer values?");
    await waitIdle();
    expect((await (await request.get(`${api}/operations`)).json()).operations).toHaveLength(1);
    chat = await (await request.get(api)).json();
    const policy = await request.put(`${api}/policy`, { data: { expectedRevision: chat.revision,
      capabilities: { ...chat.capabilities, actionModes: { ...chat.capabilities.actionModes, "query.read": "ask" } } } });
    expect(policy.ok()).toBe(true);
    await page.reload();
    await page.getByRole("button", { name: "AI schema assistant" }).click();
    await send("Run one batch with first: SELECT 42 AS answer; second: SELECT 84 AS answer. Request approval, then automatically compare the returned values.");
    await expect(page.getByRole("button", { name: "Approve reads" })).toBeVisible({ timeout: 100_000 });
    await page.getByRole("button", { name: "Approve reads" }).click();
    await waitIdle();
    expect((await (await request.get(`${api}/operations`)).json()).operations).toHaveLength(2);
    await page.locator("#ai-assistant-messages .ai-message.assistant").last().scrollIntoViewIfNeeded();
    await page.mouse.move(1, 1);
    await page.screenshot({ path: testInfo.outputPath("live-read-feedback.png"), fullPage: true, animations: "disabled" });
  } finally {
    await request.delete(api);
  }
});
