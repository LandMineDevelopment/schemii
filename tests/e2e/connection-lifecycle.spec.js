import { randomUUID } from "node:crypto";

import { expect, test } from "@playwright/test";

const CONNECTIONS = "/api/v1/connections";
const WORKSPACES = "/api/v1/schemii/workspaces";

let fixture = null;

async function createConnectionWorkspace(request, projectName) {
  const suffix = randomUUID().slice(0, 8);
  const name = `E2E lifecycle ${projectName} ${suffix}`;
  const connectionResponse = await request.post(CONNECTIONS, {
    data: {
      name,
      host: "postgres",
      port: 5432,
      database: "schemii_migration_demo",
      username: "schemii",
      password: "schemii-local-test",
      sslMode: "disable",
    },
  });
  expect(connectionResponse.ok()).toBe(true);
  const connection = await connectionResponse.json();
  const workspaceResponse = await request.post(`${WORKSPACES}/postgres`, {
    data: { connectionId: connection.id, namespace: "public" },
  });
  expect(workspaceResponse.ok()).toBe(true);
  const workspace = (await workspaceResponse.json()).workspace;
  return { connection, name, workspace };
}

test.afterEach(async ({ request }) => {
  if (!fixture) return;
  const { connection, workspace } = fixture;
  fixture = null;
  const currentWorkspace = await request.get(`${WORKSPACES}/${workspace.id}`);
  if (currentWorkspace.ok()) {
    const document = await currentWorkspace.json();
    await request.delete(`${WORKSPACES}/${workspace.id}?expectedRevision=${document.revision}`);
  }
  const currentConnection = await request.get(`${CONNECTIONS}/${connection.id}`);
  if (currentConnection.ok()) {
    const document = await currentConnection.json();
    await request.delete(`${CONNECTIONS}/${connection.id}?expectedRevision=${document.revision}`);
  }
});

test("connection review exposes a navigable child workspace with direct deletion", async ({ page, request }, testInfo) => {
  fixture = await createConnectionWorkspace(request, testInfo.project.name);
  const { connection, name, workspace } = fixture;

  await page.goto("/");
  await page.getByRole("button", { name: "PostgreSQL connections", exact: true }).first().click();
  const connections = page.locator("#connections-dialog");
  const connectionCard = connections.locator(".manager-card").filter({ hasText: name });
  await expect(connectionCard).toBeVisible();
  await connectionCard.getByRole("button", { name: "Delete", exact: true }).click();

  const review = page.locator("#connection-impact-dialog");
  await expect(review).toBeVisible();
  const workspaceRow = review.locator(".dependency-impact-row");
  await expect(workspaceRow).toContainText(workspace.name);
  await expect(workspaceRow.locator(".dependency-impact-toggle")).toBeDisabled();
  await expect(review.locator("#delete-reviewed-connection")).toBeDisabled();

  await workspaceRow.locator(".dependency-impact-select").click();
  await expect(page).toHaveURL(new RegExp(`[?&]workspace=${workspace.id}(?:&|$)`));
  await expect(page.locator("#workspace-title")).toHaveText(workspace.name);

  await page.getByRole("button", { name: "PostgreSQL connections", exact: true }).first().click();
  await connections.locator(".manager-card").filter({ hasText: name }).getByRole("button", { name: "Delete", exact: true }).click();
  await review.getByRole("button", { name: `Delete workspace ${workspace.name}` }).click();
  const confirmation = page.locator("#confirm-dialog");
  await expect(confirmation).toContainText("PostgreSQL schemas and data will not be changed");
  await confirmation.locator("#confirm-action").click();

  await expect(review).toContainText("No saved resources use this connection");
  await expect(review.locator("#delete-reviewed-connection")).toBeEnabled();
  await review.locator("#delete-reviewed-connection").click();
  await expect(review).toBeHidden();
  await expect(connections.locator(".manager-card").filter({ hasText: name })).toHaveCount(0);

  fixture = null;
  expect((await request.get(`${WORKSPACES}/${workspace.id}`)).status()).toBe(404);
  expect((await request.get(`${CONNECTIONS}/${connection.id}`)).status()).toBe(404);
});
