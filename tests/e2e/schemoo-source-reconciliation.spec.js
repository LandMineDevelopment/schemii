import { findOrganizationConnection } from "./helpers/database-fixtures.js";
import {expect,test} from "@playwright/test";
import {importedDraft} from "../../src/schemii/schemoo/web/model-draft.js";
import {splitDraft} from "../../src/schemii/schemoo/web/model-state.js";
import {catalogContract} from "../../src/schemii/schemoo/web/model-draft.js";

let modelId;
let edgeId;
test.beforeEach(async({request})=>{
  const {connections}=await(await request.get("/api/v1/connections")).json();
  const connection=findOrganizationConnection(connections);
  expect(connection).toBeTruthy();
  const catalog=await(await request.get(`/api/v1/schemoo/catalog?connection_id=${connection.id}&namespace=public`)).json();
  const draft=importedDraft(catalog),parts=splitDraft(draft);
  const created=await request.post("/api/v1/schemoo/models",{data:{name:`E2E source drift ${Date.now()}`,connectionId:connection.id,namespace:"public",...parts}});
  expect(created.ok(),await created.text()).toBeTruthy();
  const model=await created.json();modelId=model.id;
  const relation=model.definition.sourceContract.relationships[0];
  edgeId=relation.id;
  const source=model.definition.sourceContract.tables.find(table=>table.name===relation.sourceTable);
  source.primaryKey=["previous_primary_key"];
  source.columns.find(column=>column.name===relation.sourceColumn).dataType="previous_type";
  relation.sourceColumn="previous_foreign_key_column";
  const changed=await request.put(`/api/v1/schemoo/models/${modelId}`,{data:{expectedRevision:model.revision,name:model.name,definition:model.definition,catalogFingerprint:model.catalogFingerprint}});
  expect(changed.ok(),await changed.text()).toBeTruthy();
});

test.afterEach(async({request})=>{
  if(!modelId)return;
  const response=await request.get(`/api/v1/schemoo/models/${modelId}`);
  if(response.ok())await request.delete(`/api/v1/schemoo/models/${modelId}?expected_revision=${(await response.json()).revision}`);
  modelId=null;
});

test("reload removes deleted output selections and saves the repaired model",async({page,request})=>{
  const model=await(await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  const catalog=await(await request.get(`/api/v1/schemoo/catalog?connection_id=${model.connectionId}&namespace=public`)).json();
  model.definition.sourceContract=catalogContract(catalog);
  model.definition.sourceContract.tables.find(t=>t.name==="job_slot_fact").columns.push({name:"previous_slot_date",dataType:"date",nullable:false});
  model.definition.root="job_slot_dim";
  model.definition.edges.forEach(edge=>edge.enabled=false);
  model.definition.exposedFields=[{table:"job_slot_fact",column:"previous_slot_date",aggregate:"none"},{table:"job_slot_dim",column:"id",aggregate:"none"}];
  const updated=await request.put(`/api/v1/schemoo/models/${modelId}`,{data:{expectedRevision:model.revision,name:model.name,definition:model.definition,catalogFingerprint:model.catalogFingerprint}});
  expect(updated.ok(),await updated.text()).toBeTruthy();
  const explored=await request.put(`/api/v1/schemoo/models/${modelId}/explore`,{data:{expectedRevision:model.exploreRevision,explore:{...model.explore,root:"job_slot_dim",fields:model.definition.exposedFields}}});
  expect(explored.ok(),await explored.text()).toBeTruthy();
  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.getByRole("button",{name:"Run preview",exact:true})).toBeEnabled();
  await page.getByRole("button",{name:"Explore model",exact:true}).click();
  await expect(page.locator("#fields .preview-output")).toHaveCount(1);
  await expect(page.locator("#fields")).not.toContainText("previous_slot_date");
  await page.getByRole("button",{name:"Save model",exact:true}).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  const saved=await(await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  expect(saved.definition.exposedFields.map(f=>f.column)).toEqual(["id"]);
  expect(saved.explore.fields.map(f=>f.column)).toEqual(["id"]);
  await page.reload();
  await expect(page.getByRole("button",{name:"Run preview",exact:true})).toBeEnabled();
});

test("changed keys and types are labeled, reviewed, and changed foreign keys become disabled",async({page})=>{
  await page.goto(`/schemoo?model=${modelId}`);
  await expect(page.locator("#graph-status")).toContainText("3 source changes need review");
  const changedNode=page.locator(".sc-node.sc-source-changed").first();
  await expect(changedNode.getByText("SOURCE CHANGED",{exact:true})).toBeVisible();
  await changedNode.locator(".sc-node-header").click();
  const sourcePanel=page.locator("#object-source-issues");
  await expect(sourcePanel).toContainText("Primary key changed");
  await expect(sourcePanel).toContainText("Referenced column");
  page.once("dialog",dialog=>dialog.accept());
  await sourcePanel.getByRole("button",{name:"Accept current source",exact:true}).first().click();
  await expect(page.locator("#graph-status")).toContainText("2 source changes need review");
  page.once("dialog",dialog=>dialog.accept());
  await sourcePanel.getByRole("button",{name:"Accept current source",exact:true}).click();
  await expect(page.locator("#graph-status")).toContainText("1 source change needs review");
  if(await page.locator("#table-inspector").isVisible())await page.getByRole("button",{name:"Close table inspector",exact:true}).click();
  const changedEdge=page.locator(".sc-edge.sc-source-changed").first();
  // A curved edge's bounding-box center can lie underneath an unrelated card.
  // Exercise its supported keyboard interaction instead of that empty midpoint.
  await expect(changedEdge).toHaveAttribute("role", "button");
  await changedEdge.focus();
  await expect(changedEdge).toBeFocused();
  await changedEdge.press("Enter");
  await expect(sourcePanel).toContainText("Foreign key");
  await page.screenshot({path:`artifacts/source-reconciliation-${test.info().project.name}.png`});
  page.once("dialog",dialog=>dialog.accept());
  await sourcePanel.getByRole("button",{name:"Accept current source",exact:true}).click();
  await expect(page.locator("#graph-status")).not.toContainText("source change");
  await expect(page.locator("#selection-inspector input[type=checkbox]")).not.toBeChecked();
  await expect(page.locator(`#relationships input[aria-label="Enable ${edgeId}"]`)).not.toBeChecked();
  await page.getByRole("button",{name:"Save model",exact:true}).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  await page.reload();
  await expect(page.locator("#graph-status")).not.toContainText("source change");
  await expect(page.locator(".sc-source-changed")).toHaveCount(0);
});
