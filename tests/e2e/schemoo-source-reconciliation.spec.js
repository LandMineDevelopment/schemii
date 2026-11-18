import { expect, test } from "@playwright/test";
import { ownedSchemooSource } from "./helpers/schemoo-source-fixtures.js";

let source, model;
test.beforeEach(async ({ request }) => {
  source = await ownedSchemooSource(request);
  await source.sql(`CREATE TABLE "${source.namespace}".parent (id integer PRIMARY KEY, alternate_id integer UNIQUE NOT NULL);
    CREATE TABLE "${source.namespace}".child (id integer PRIMARY KEY, parent_id integer, previous_slot_date date,
      CONSTRAINT child_parent FOREIGN KEY (parent_id) REFERENCES "${source.namespace}".parent(id));`);
  model = await source.createModel("drift");
});
test.afterEach(async () => { if (source) await source.cleanup(); source = model = null; });

test("reload removes deleted output selections and saves the repaired model", async ({ page, request }) => {
  // Establish a valid saved model, then change only this test's real source.
  model.definition.root = "parent"; model.definition.edges.forEach(edge => { edge.enabled = false; });
  model.definition.exposedFields = [{ table: "child", column: "previous_slot_date", aggregate: "none" }, { table: "parent", column: "id", aggregate: "none" }];
  const url = `/api/v1/schemoo/models/${model.id}`;
  const updated = await request.put(url, { data: { expectedRevision: model.revision, name: model.name, definition: model.definition } });
  expect(updated.ok(), await updated.text()).toBeTruthy();
  const explored = await request.put(url + "/explore", { data: { expectedRevision: model.exploreRevision, explore: { ...model.explore, root: "parent", fields: model.definition.exposedFields } } });
  expect(explored.ok(), await explored.text()).toBeTruthy();
  await source.sql(`ALTER TABLE "${source.namespace}".child DROP COLUMN previous_slot_date`);
  await page.goto(`/schemoo?model=${model.id}`);
  await expect(page.getByRole("button", { name: "Run preview", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  await expect(page.locator("#fields .preview-output")).toHaveCount(1);
  await expect(page.locator("#fields")).not.toContainText("previous_slot_date");
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  const saved = await (await request.get(url)).json();
  expect(saved.definition.exposedFields.map(field => field.column)).toEqual(["id"]);
  expect(saved.explore.fields.map(field => field.column)).toEqual(["id"]);
  await page.reload(); await expect(page.getByRole("button", { name: "Run preview", exact: true })).toBeEnabled();
});

test("changed keys and types are labeled, reviewed, and changed foreign keys become disabled", async ({ page, request }) => {
  const relation = model.definition.sourceContract.relationships[0], edgeId = relation.id;
  await source.sql(`ALTER TABLE "${source.namespace}".child DROP CONSTRAINT child_pkey;
    ALTER TABLE "${source.namespace}".child ALTER COLUMN parent_id TYPE bigint;
    ALTER TABLE "${source.namespace}".child ADD PRIMARY KEY (id, parent_id);
    ALTER TABLE "${source.namespace}".child DROP CONSTRAINT child_parent;
    ALTER TABLE "${source.namespace}".child ADD CONSTRAINT child_parent FOREIGN KEY (parent_id) REFERENCES "${source.namespace}".parent(alternate_id);`);
  await page.goto(`/schemoo?model=${model.id}`);
  await expect(page.locator("#graph-status")).toContainText("3 source changes need review");
  const changedNode = page.locator(".sc-node.sc-source-changed").first();
  await expect(changedNode.getByText("SOURCE CHANGED", { exact: true })).toBeVisible();
  await changedNode.locator(".sc-node-header").click();
  const sourcePanel = page.locator("#object-source-issues");
  await expect(sourcePanel).toContainText("Primary key changed"); await expect(sourcePanel).toContainText("Referenced column");
  page.once("dialog", dialog => dialog.accept()); await sourcePanel.getByRole("button", { name: "Accept current source", exact: true }).first().click();
  await expect(page.locator("#graph-status")).toContainText("2 source changes need review");
  page.once("dialog", dialog => dialog.accept()); await sourcePanel.getByRole("button", { name: "Accept current source", exact: true }).click();
  await expect(page.locator("#graph-status")).toContainText("1 source change needs review");
  if (await page.locator("#table-inspector").isVisible()) await page.getByRole("button", { name: "Close table inspector", exact: true }).click();
  const changedEdge = page.locator(".sc-edge.sc-source-changed").first();
  await expect(changedEdge).toHaveAttribute("role", "button"); await changedEdge.focus(); await expect(changedEdge).toBeFocused(); await changedEdge.press("Enter");
  await expect(sourcePanel).toContainText("Foreign key");
  await page.screenshot({ path: `artifacts/source-reconciliation-${test.info().project.name}.png` });
  page.once("dialog", dialog => dialog.accept()); await sourcePanel.getByRole("button", { name: "Accept current source", exact: true }).click();
  await expect(page.locator("#graph-status")).not.toContainText("source change");
  await expect(page.locator(`#relationships input[aria-label="Enable ${edgeId}"]`)).not.toBeChecked();
  await page.getByRole("button", { name: "Save model", exact: true }).click(); await expect(page.locator("#draft-status")).toContainText("Saved");
  const saved = await (await request.get(`/api/v1/schemoo/models/${model.id}`)).json();
  expect(saved.definition.edges.find(edge => edge.relationshipId === edgeId).enabled).toBe(false);
  await page.reload(); await expect(page.locator("#graph-status")).not.toContainText("source change"); await expect(page.locator(".sc-source-changed")).toHaveCount(0);
});

test("clients cannot replace the canonical live source contract with fabricated historical drift", async ({ request }) => {
  const definition = structuredClone(model.definition), contract = definition.sourceContract;
  contract.tables[0].primaryKey = ["invented"]; contract.tables[0].columns[0].dataType = "invented";
  contract.relationships[0].sourceColumn = "invented";
  const response = await request.put(`/api/v1/schemoo/models/${model.id}`, { data: { expectedRevision: model.revision, name: model.name, definition } });
  expect(response.ok(), await response.text()).toBeTruthy();
  expect((await response.json()).definition.sourceContract).toEqual(model.definition.sourceContract);
});
