import { randomUUID } from "node:crypto";
import { expect, request as requestFactory, test } from "@playwright/test";

const WORKSPACES = "/api/v1/schemii/workspaces";
const id = prefix => `${prefix}_${randomUUID().replaceAll("-", "")}`;

async function json(response) {
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

// Explicit opt-in: invokes a real authenticated model. The entire fixture is
// detached and disposable; it cannot migrate or modify any live database.
test("live assistant saves two indexes with one batch approval", async ({ page, request }, testInfo) => {
  test.skip(process.env.SCHEMII_LIVE_AI !== "1", "Requires explicit real-provider test opt-in");
  test.setTimeout(180_000);
  let workspace;
  let chat;
  try {
    workspace = await json(await request.post(WORKSPACES, { data: {
      name: `E2E design batch ${randomUUID()}`,
    } }));
    expect(workspace.connectionId).toBeFalsy();
    const path = `${WORKSPACES}/${workspace.id}`;
    const empty = await json(await request.get(`${path}/design`));
    const seeded = await json(await request.put(`${path}/design`, { data: {
      expectedDesignRevision: empty.revision,
      content: { tables: [{
        id: id("table"), name: "batch_projects",
        columns: [
          { id: id("column"), name: "owner_id", dataType: "bigint", nullable: true },
          { id: id("column"), name: "status", dataType: "text", nullable: true },
        ], keys: [], checks: [], indexes: [],
      }], types: [], relationships: [], functions: [], views: [], triggers: [] },
    } }));
    chat = await json(await request.post(`${path}/ai/chats`, { data: {
      title: "E2E real model design batch", providerId: "openai-codex", modelId: "gpt-5.4-mini",
      capabilities: { actionModes: { "indexes.create": "ask" } },
    } }));
    const api = `/api/v1/schemii/ai/chats/${chat.id}`;
    await page.goto(`/?workspace=${workspace.id}`);
    await page.getByRole("button", { name: "AI schema assistant" }).click();
    await expect(page.locator("#ai-assistant-status")).toHaveText("Ready");
    await page.locator("#ai-assistant-input").fill(
      "Add two ordinary btree indexes to the saved design for batch_projects: batch_projects_owner_idx on owner_id and batch_projects_status_idx on status. I want to approve these together once. Use one schemii_design_change call with action type batch containing the two put_table_member index actions. Do not prepare SQL or request migration; this is a detached design. Explain that the proposal is pending my approval and does not change a live database."
    );
    await page.locator("#ai-assistant-input").press("Enter");
    await expect(page.getByRole("button", { name: "Review design changes" })).toBeVisible({ timeout: 100_000 });
    await expect.poll(async () => (await json(await request.get(api))).status, { timeout: 100_000 }).toBe("waiting_approval");
    const proposals = (await json(await request.get(`${api}/proposals`))).proposals;
    expect(proposals).toHaveLength(1);
    expect(proposals[0].actionType).toBe("design_change");
    expect(proposals[0].details.type).toBe("batch");
    expect(proposals[0].details.actions).toHaveLength(2);
    expect((await json(await request.get(`${path}/design`))).revision).toBe(seeded.revision);

    await page.getByRole("button", { name: "Review design changes" }).click();
    const review = page.getByRole("dialog", { name: "Review proposed action" });
    await expect(review).toContainText("2 changes saved together in one design revision");
    await expect(review).toContainText("batch_projects_owner_idx");
    await expect(review).toContainText("batch_projects_status_idx");
    await expect(review).toContainText("Live migration is a separate action");
    await review.getByRole("button", { name: "Save to design" }).click();
    await expect(review).toBeHidden();
    await expect(page.locator("#ai-assistant-messages")).toContainText("SAVED TO DESIGN");
    const applied = await json(await request.get(`${path}/design`));
    expect(applied.revision).toBe(seeded.revision + 1);
    expect(applied.content.tables[0].indexes.map(index => index.name).sort()).toEqual([
      "batch_projects_owner_idx", "batch_projects_status_idx",
    ]);
    const operations = (await json(await request.get(`${api}/operations`))).operations;
    expect(operations).toHaveLength(1);
    expect(operations[0].status).toBe("succeeded");
    expect(operations[0].resultSummary.liveDatabaseChanged).toBe(false);
    await expect.poll(async () => (await json(await request.get(api))).status, { timeout: 100_000 }).toBe("idle");
    await expect(page.locator("#ai-assistant-messages .ai-message.assistant").last()).toBeVisible();
    await page.mouse.move(1, 1);
    await page.screenshot({ path: testInfo.outputPath("live-design-batch.png"), fullPage: true, animations: "disabled" });
  } finally {
    // Independent cleanup survives a browser/test timeout closing its fixtures.
    const cleanup = await requestFactory.newContext({
      baseURL: process.env.SCHEMII_E2E_BASE_URL || "https://localhost:8001",
      ignoreHTTPSErrors: true,
    });
    try {
      if (chat) expect((await cleanup.delete(`/api/v1/schemii/ai/chats/${chat.id}`)).ok()).toBe(true);
      if (workspace) {
        const path = `${WORKSPACES}/${workspace.id}`;
        const current = await json(await cleanup.get(path));
        expect((await cleanup.delete(`${path}?expectedRevision=${current.revision}`)).ok()).toBe(true);
      }
    } finally {
      await cleanup.dispose();
    }
  }
});
