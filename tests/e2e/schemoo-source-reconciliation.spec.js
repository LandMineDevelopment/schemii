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
  await expect(page.locator("#graph-status")).toContainText("1 source change needs review");
  await expect(page.getByRole("button", { name: "Run preview", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Explore model", exact: true }).click();
  await expect(page.locator("#fields .preview-output")).toHaveCount(1);
  await expect(page.locator("#fields")).not.toContainText("previous_slot_date");
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  await expect(page.getByRole("button", { name: "Run preview", exact: true })).toBeEnabled();
  await expect(page.locator("#graph-status")).not.toContainText("source change");
  const saved = await (await request.get(url)).json();
  expect(saved.definition.exposedFields.map(field => field.column)).toEqual(["id"]);
  expect(saved.explore.fields.map(field => field.column)).toEqual(["id"]);
  expect(saved.definition.sourceContract.tables.flatMap(table => table.columns.map(column => [table.name, column.name])))
    .not.toContainEqual(["child", "previous_slot_date"]);
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
  await test.info().attach(`source-reconciliation-${test.info().project.name}`, { body: await page.screenshot(), contentType: "image/png" });
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
  contract.relationships[0].columns[0].source = "invented";
  // Keep the fabricated history structurally valid under the complete mapping
  // contract so this tests server canonicalization, rather than alias conflicts.
  delete contract.relationships[0].sourceColumn; delete contract.relationships[0].targetColumn;
  const response = await request.put(`/api/v1/schemoo/models/${model.id}`, { data: { expectedRevision: model.revision, name: model.name, definition } });
  expect(response.ok(), await response.text()).toBeTruthy();
  expect((await response.json()).definition.sourceContract).toEqual(model.definition.sourceContract);
});

test("qualified duplicate source names retain complete FK pairs and author a composite logical repair", async ({page,request})=>{
  test.setTimeout(90_000);
  const {chooseModelOption}=await import("./helpers/schemoo-select.js");
  const target=await ownedSchemooSource(request);
  try {
    await target.sql(`CREATE TABLE "${target.namespace}"."same.name" (tenant integer, id integer, label text, PRIMARY KEY(tenant,id))`);
    await source.sql(`CREATE TABLE "${source.namespace}"."same.name" (tenant integer, id integer, label text, PRIMARY KEY(tenant,id), CONSTRAINT exact_pair FOREIGN KEY(tenant,id) REFERENCES "${target.namespace}"."same.name"(tenant,id))`);
    await page.goto("/schemoo");
    const library=page.locator("#model-library");await expect(library).toBeVisible();
    await library.getByRole("textbox",{name:"Model name",exact:true}).fill(source.name("qualified"));
    await chooseModelOption(page,"Source connection",`${source.connection.name} · ${source.connection.database} · ${source.connection.username}`);
    await chooseModelOption(page,"Source schema",source.namespace);
    await library.getByRole("checkbox",{name:`Include schema ${target.namespace}`,exact:true}).check();
    const picker=library.getByRole("region",{name:"Model sources and fields"});
    const leftLabel='"same.name"',rightLabel=`${target.namespace}."same.name"`;
    await expect(picker.getByRole("checkbox",{name:`Include source ${rightLabel}`,exact:true})).toBeVisible();
    await picker.getByRole("button",{name:"Clear selection",exact:true}).click();
    await picker.getByRole("searchbox",{name:"Search sources",exact:true}).fill("same.name");
    await picker.getByRole("checkbox",{name:`Include source ${leftLabel}`,exact:true}).check();
    await picker.getByRole("checkbox",{name:`Include source ${rightLabel}`,exact:true}).check();
    const posted=page.waitForResponse(response=>response.url().endsWith("/api/v1/schemoo/models")&&response.request().method()==="POST");
    await library.getByRole("button",{name:"Create model",exact:true}).click();
    const created=await posted;expect(created.status()).toBe(201);const saved=await created.json();source.trackModel(saved);
    expect(saved.definition.nodes.map(node=>node.namespace).sort()).toEqual([source.namespace,target.namespace].sort());
    expect(saved.definition.sourceContract.relationships[0].columns).toEqual([{source:"tenant",target:"tenant"},{source:"id",target:"id"}]);
    await expect(library).toBeHidden();await expect(page.locator(".sc-node")).toHaveCount(2);
    await page.getByRole("button",{name:"Edit model",exact:true}).click();
    await page.locator("#relationships input[type=checkbox]").uncheck();
    await page.getByRole("button",{name:"Add logical relationship",exact:true}).click();
    await chooseModelOption(page,"source object",`${leftLabel} · ${leftLabel}`);
    await chooseModelOption(page,"target object",`${rightLabel} · ${rightLabel}`);
    await chooseModelOption(page,"source column 1","tenant");await chooseModelOption(page,"target column 1","tenant");
    await page.getByRole("button",{name:"Add column pair",exact:true}).click();
    await chooseModelOption(page,"source column 2","id");await chooseModelOption(page,"target column 2","id");
    const pair=page.getByRole("group",{name:"Column pair 2",exact:true});await expect(pair).toBeVisible();
    await test.info().attach(`qualified-composite-model-${test.info().project.name}`,{body:await page.screenshot(),contentType:"image/png"});
    await page.getByRole("button",{name:"Add relationship",exact:true}).click();
    await page.getByRole("button",{name:"Save model",exact:true}).click();await expect(page.locator("#draft-status")).toContainText("Saved");
    await page.reload();await expect(page.locator(".sc-node")).toHaveCount(2);
    const read=await(await request.get(`/api/v1/schemoo/models/${saved.id}`)).json();
    const logical=read.definition.edges.find(edge=>edge.kind==="logical");expect(logical.columns).toEqual([{source:"tenant",target:"tenant"},{source:"id",target:"id"}]);
    expect(read.definition.nodes.map(node=>node.id)).toEqual(saved.definition.nodes.map(node=>node.id));
  }finally{await target.cleanup();}
});
