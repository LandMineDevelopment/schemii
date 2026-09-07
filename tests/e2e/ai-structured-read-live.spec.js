import { expect, test } from "@playwright/test";

// Explicit opt-in. Reads only the seeded bookstore; creates and removes its own
// conversation. Raw SQL and all mutation capabilities remain disabled.
test("live assistant discovers and counts table rows without raw SQL permission", async ({ page, request }, testInfo) => {
  test.skip(process.env.SCHEMII_LIVE_AI !== "1", "Requires explicit real-provider test opt-in");
  test.setTimeout(180_000);
  const spaces = await (await request.get("/api/v1/schemii/workspaces")).json();
  const workspace = spaces.workspaces.find(item => item.database === "schemii_test" && item.namespace === "bookstore");
  expect(workspace).toBeTruthy();
  const created = await request.post(`/api/v1/schemii/workspaces/${workspace.id}/ai/chats`, { data: {
    title: "E2E live structured table count", providerId: "openai-codex", modelId: "gpt-5.4-mini",
    capabilities: { structuredQuery: true, structuredQueryApprovalRequired: false, structuredDataRead: true, rawSqlRead: false },
  } });
  expect(created.ok(), await created.text()).toBe(true);
  const chat = await created.json();
  const api = `/api/v1/schemii/ai/chats/${chat.id}`;
  try {
    await page.goto(`/?workspace=${workspace.id}`);
    await page.getByRole("button", { name: "AI schema assistant" }).click();
    await expect(page.locator("#ai-assistant-status")).toHaveText("Ready");
    await page.locator("#ai-assistant-input").fill("Use schemii_list_relations to discover the authors table and its exact live reference. Then use schemii_browse_rows with countOnly true to count its rows. Explain the returned author count automatically. Do not write or request raw SQL; structured table browsing is permitted automatically, raw SQL is disabled.");
    await page.locator("#ai-assistant-input").press("Enter");
    await expect.poll(async () => (await (await request.get(`${api}/messages`)).json()).messages.filter(message => message.role === "user").length).toBe(1);
    await expect.poll(async () => (await (await request.get(api)).json()).status, { timeout: 120_000 }).toBe("idle");
    const proposals = (await (await request.get(`${api}/proposals`)).json()).proposals;
    expect(proposals.length).toBeGreaterThan(0);
    expect(proposals.every(proposal => proposal.capability === "structured_query")).toBe(true);
    expect(proposals.every(proposal => proposal.status === "succeeded")).toBe(true);
    expect(proposals.some(proposal => proposal.details.structuredQueries?.some(query => query.count_only || query.countOnly))).toBe(true);
    const operations = (await (await request.get(`${api}/operations`)).json()).operations;
    expect(operations.length).toBeGreaterThan(0);
    expect(operations.every(operation => operation.kind === "data_read" && operation.status === "succeeded")).toBe(true);
    const result = await (await request.get(`${api}/operations/${operations.at(-1).id}/query-result`)).json();
    const rows = result.results.flatMap(item => item.rows || []);
    expect(rows).toHaveLength(1);
    const count = Array.isArray(rows[0]) ? rows[0][0] : rows[0].row_count;
    expect(Number(count)).toBeGreaterThan(0);
    const answer = page.locator("#ai-assistant-messages .ai-message.assistant").last();
    await expect(answer).toContainText(/author/i, { timeout: 10_000 });
    await expect(answer).toContainText(String(count));
    await expect(page.getByRole("button", { name: "Approve reads", exact: true })).toHaveCount(0);
    const current = await (await request.get(api)).json();
    expect(current.capabilities.rawSqlRead).toBe(false);
    await answer.scrollIntoViewIfNeeded();
    await page.mouse.move(1, 1);
    await page.screenshot({ path: testInfo.outputPath("live-structured-read-feedback.png"), fullPage: true, animations: "disabled" });
  } finally {
    const deleted = await request.delete(api);
    expect(deleted.ok(), await deleted.text()).toBe(true);
  }
});
