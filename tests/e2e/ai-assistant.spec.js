import { expect, test } from "@playwright/test";

async function workspace(request) {
  const response = await request.get("/api/v1/schemii/workspaces");
  expect(response.ok()).toBe(true);
  const body = await response.json();
  return body.workspaces.find(item => item.name === "Test design") || body.workspaces[0];
}

test("assistant keeps proposals and outcomes with the turn that created them", async ({ page, request }) => {
  const activeWorkspace = await workspace(request);
  const chatId = `chat_${"a".repeat(32)}`;
  const firstTurn = `turn_${"b".repeat(32)}`;
  const secondTurn = `turn_${"c".repeat(32)}`;
  const firstProposal = `prop_${"d".repeat(32)}`;
  const secondProposal = `prop_${"e".repeat(32)}`;
  const createdAt = new Date().toISOString();
  let executionRequests = 0;

  await page.route("**/api/v1/schemii/ai/chats/*/messages", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ messages: [
      { id: `msg_${"1".repeat(32)}`, chatId, turnId: firstTurn, sequence: 1, role: "user", text: "Create the archive table", createdAt },
      { id: `msg_${"2".repeat(32)}`, chatId, turnId: firstTurn, sequence: 2, role: "assistant", text: "I prepared the archive table.", createdAt },
      { id: `msg_${"3".repeat(32)}`, chatId, turnId: secondTurn, sequence: 3, role: "user", text: "Delete the temporary table", createdAt },
      { id: `msg_${"4".repeat(32)}`, chatId, turnId: secondTurn, sequence: 4, role: "assistant", text: "I prepared the deletion for review.", createdAt },
    ] }),
  }));
  await page.route("**/api/v1/schemii/ai/chats/*/proposals", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ proposals: [
      { id: firstProposal, chatId, turnId: firstTurn, revision: 2, actionType: "design_change", summary: "Create the archive table", status: "succeeded", destructive: false, createdAt },
      { id: secondProposal, chatId, turnId: secondTurn, revision: 1, capability: "design_changes", actionType: "design_change", summary: "Delete the temporary table", details: { type: "delete_object", object_id: `table_${"9".repeat(32)}` }, digest: "9".repeat(64), status: "pending", destructive: true, createdAt },
    ] }),
  }));
  await page.route("**/api/v1/schemii/ai/chats/*/operations", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ operations: [
      { id: `aop_${"f".repeat(32)}`, chatId, proposalId: firstProposal, revision: 1, kind: "design_change", status: "succeeded", createdAt, updatedAt: createdAt },
    ] }),
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
  await expect(turns.nth(0)).toContainText("Create the archive table");
  await expect(turns.nth(0)).toContainText("SUCCEEDED");
  await expect(turns.nth(0)).not.toContainText("Delete the temporary table");
  await expect(turns.nth(1)).toContainText("Delete the temporary table");
  await expect(turns.nth(1)).toContainText("PROPOSED design change");
  await expect(turns.nth(1)).not.toContainText("SUCCEEDED");
  await turns.nth(1).getByRole("button", { name: "Review & apply" }).click();
  const review = page.getByRole("dialog", { name: "Review proposed action" });
  await expect(review).toBeVisible();
  await expect(review).toContainText("delete_object");
  const confirm = review.getByRole("button", { name: "Apply destructive proposal" });
  await expect(confirm).toBeDisabled();
  await expect(confirm).toBeEnabled();
  expect(executionRequests).toBe(0);
  await review.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(review).toBeHidden();
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
  await expect(settings.getByRole("checkbox")).toHaveCount(5);
  await expect(settings.getByRole("heading", { name: "Model providers" })).toBeVisible();
  await expect(settings.getByText("OpenAI", { exact: true })).toBeVisible();
  await settings.locator("details.ai-provider-card", { hasText: "OpenAI" }).locator("summary").click();
  await expect(settings.getByText("ChatGPT Pro/Plus (browser)")).toBeVisible();
  const saveSettings = settings.getByRole("button", { name: "Save settings" });
  await expect(saveSettings).toBeVisible();
  const saveBounds = await saveSettings.boundingBox();
  const viewport = page.viewportSize();
  expect(saveBounds).not.toBeNull();
  expect(viewport).not.toBeNull();
  expect(saveBounds.y + saveBounds.height).toBeLessThanOrEqual(viewport.height);
  const writePermission = settings.getByRole("checkbox", { name: "Prepare write SQL" });
  const originalWritePermission = await writePermission.isChecked();
  await writePermission.setChecked(!originalWritePermission);
  await saveSettings.click();
  await expect(settings).toBeHidden();

  await assistant.getByRole("button", { name: "Assistant settings" }).click();
  await expect(writePermission).toBeChecked({ checked: !originalWritePermission });
  await writePermission.setChecked(originalWritePermission);
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
  await input.press("Enter");
  await expect.poll(() => submitted?.text).toBe("first line\nsecond line");
  await expect(input).toBeVisible();
  await input.fill("A follow-up remains available");
  await expect(input).toHaveValue("A follow-up remains available");
});
