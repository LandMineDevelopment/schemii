import { findOrganizationConnection } from "./helpers/database-fixtures.js";
import { expect, test } from "@playwright/test";
import { chooseModelOption } from "./helpers/schemoo-select.js";

let modelId;
test.beforeEach(async ({request})=>{
  const {connections}=await (await request.get("/api/v1/connections")).json();
  const connection=findOrganizationConnection(connections);expect(connection).toBeTruthy();
  const response=await request.post("/api/v1/schemoo/models",{data:{name:`E2E calculations ${Date.now()}`,connectionId:connection.id,namespace:"public",
    definition:{root:"pay_band_class_dim",nodes:[{id:"pay_band_class_dim",table:"pay_band_class_dim",label:"Pay bands"}],edges:[],scopes:[],exposedFields:null},
    layout:{positions:[{id:"pay_band_class_dim",x:40,y:40}]},
    explore:{root:"pay_band_class_dim",fields:[{table:"pay_band_class_dim",column:"level",aggregate:"none"}],limit:100}}});
  expect(response.ok(),await response.text()).toBeTruthy();modelId=(await response.json()).id;
});
test.afterEach(async ({request})=>{
  if(!modelId)return;const response=await request.get(`/api/v1/schemoo/models/${modelId}`);
  if(response.ok())await request.delete(`/api/v1/schemoo/models/${modelId}?expected_revision=${(await response.json()).revision}`);
  modelId=null;
});

test("summary field explains safe, source-only and multiplying lookup paths",async({page,request},testInfo)=>{
  const model=await(await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  const catalog=await(await request.get(`/api/v1/schemoo/catalog?connection_id=${model.connectionId}&namespace=public`)).json();
  const source="personnel_certification_fact",person="personnel_dim",lookup="certification_dim";
  const nodes=[{id:source,table:source,label:"Employee certifications"},{id:person,table:person,label:"Personnel"},{id:lookup,table:lookup,label:"Certification definitions"}];
  const edges=catalog.relationships.filter(r=>r.sourceTable===source&&[person,lookup].includes(r.targetTable)).map(r=>({id:r.id,relationshipId:r.id,source:r.sourceTable,target:r.targetTable,enabled:true}));
  nodes.push({id:"summary",table:source,label:"Summary",derivation:{kind:"aggregate",source,groupBy:["personnel_id"],connection:{target:person,columns:[{source:"personnel_id",target:"id"}]},outputs:[{id:"names",label:"Names",operation:"list",nodeId:lookup,column:"name",distinct:true}]}});
  const response=await request.put(`/api/v1/schemoo/models/${modelId}`,{data:{expectedRevision:model.revision,name:model.name,definition:{root:person,nodes,edges,scopes:[],exposedFields:null}}});
  expect(response.ok(),await response.text()).toBeTruthy();
  await page.goto(`/schemoo?model=${modelId}`);
  if(await page.locator("#inspector").isVisible())await page.getByRole("button",{name:"Close inspector",exact:true}).click();
  await page.locator('[data-node-id="summary"] .sc-node-header').click();
  await page.getByRole("button",{name:"Edit calculated source",exact:true}).click();
  const dialog=page.getByRole("dialog",{name:"Edit calculated source",exact:true});
  const status=dialog.getByRole("status",{name:"Field 1 lookup",exact:true});
  await expect(status).toContainText("Many-to-one lookup");
  await expect(status).toContainText("Employee certifications.certification_id → Certification definitions.id");
  await expect(status).toContainText("at most one");
  await status.scrollIntoViewIfNeeded();
  await page.screenshot({path:`artifacts/schemoo-lookup-${testInfo.project.name}.png`,animations:"disabled"});
  await chooseModelOption(page,"Field 1 source","Employee certifications");
  await expect(status).toContainText("Source field · no lookup join");
  await chooseModelOption(page,"Summarize records from","Personnel");
  await chooseModelOption(page,"Field 1 source","Certification definitions");
  await expect(status).toContainText("Lookup can multiply source records");
  await expect(status).toHaveClass(/derived-lookup--warning/);
  await dialog.getByRole("button",{name:"Cancel",exact:true}).click();
});

for(const kind of ["row","aggregate"]) test(`${kind} source creation, preview and persisted definitions`,async ({page,request},testInfo)=>{
  await page.goto(`/schemoo?model=${modelId}`);
  if(await page.locator("#inspector").isVisible())await page.getByRole("button",{name:"Close inspector",exact:true}).click();
  await page.locator('[data-node-id="pay_band_class_dim"] .sc-node-header').click();
  await page.getByRole("button",{name:"Add calculated source",exact:true}).click();
  const dialog=page.getByRole("dialog",{name:"Add calculated source",exact:true});
  await expect(dialog.locator(".warning")).toBeHidden();
  await expect(dialog.getByRole("combobox",{name:"Calculate using",exact:true})).toHaveValue("Pay bands");
  await dialog.getByRole("textbox",{name:"Calculated source name",exact:true}).fill("Example calculation");
  if(kind==="aggregate"){
    await chooseModelOption(page,"Calculation kind","Grouped summary · one row per group");
    await dialog.getByRole("checkbox",{name:"id",exact:true}).check();
  }
  await dialog.getByRole("textbox",{name:"Field 1 name",exact:true}).fill("Example value");
  await chooseModelOption(page,"Field 1 column","min_pay_range · integer");
  if(kind==="row")await chooseModelOption(page,"Field 1 second column","level · integer");
  await dialog.getByRole("button",{name:"Add Field 1 condition",exact:true}).click();
  await chooseModelOption(page,"Field 1 condition 1 field","Pay bands · level");
  await chooseModelOption(page,"Field 1 condition 1 operator","≥");
  await dialog.getByRole("textbox",{name:"Field 1 condition 1 value",exact:true}).fill("4");
  await dialog.evaluate(el=>Promise.all(el.getAnimations().map(a=>a.finished)));
  await page.screenshot({path:`artifacts/schemoo-derived-${kind}-${testInfo.project.name}.png`,animations:"disabled"});
  await dialog.getByRole("button",{name:"Apply to model",exact:true}).click();
  await expect(page.locator("#table-inspector-title")).toHaveText("Example calculation");
  await page.getByRole("button",{name:"Save model",exact:true}).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  const saved=await (await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  expect(saved.definition.exposedFields.some(f=>f.table==="pay_band_class_dim"&&f.column==="level")).toBeTruthy();
  expect(saved.explore.fields).toHaveLength(1);
  expect(saved.definition.nodes.find(n=>n.derivation).derivation.kind).toBe(kind);
  expect(saved.definition.nodes.find(n=>n.derivation).derivation.outputs[0].conditions[0]).toMatchObject({table:"pay_band_class_dim",column:"level",operator:"gte",value:"4"});
  await page.getByRole("button",{name:"Explore model",exact:true}).click();
  await chooseModelOption(page,"Preview source field","Example calculation · Example value");
  await page.getByRole("button",{name:"Add preview output",exact:true}).click();
  await expect(page.getByRole("button",{name:"Run preview",exact:true})).toBeEnabled();
  await page.getByRole("button",{name:"Run preview",exact:true}).click();
  await expect(page.locator("#results table")).toBeVisible({timeout:30000});
  await expect(page.locator("#results thead")).toContainText("Example calculation.Example value");
  await expect(page.locator("#error")).toBeHidden();
  const sql=await page.locator("#sql").textContent();
  if(kind==="row") {expect(sql).toContain('"min_pay_range" +');expect(sql).not.toContain("JOIN");expect(sql).toContain("CASE WHEN");}
  else {expect(sql).toContain("STRING_AGG");expect(sql).toContain("GROUP BY");expect(sql).toContain("FILTER (WHERE");}
  await page.reload();
  await expect(page.locator('[data-node-id] .sc-node-name').filter({hasText:"Example calculation"})).toBeVisible();
});

test("child-source summary groups before its Personnel connection",async({page,request},testInfo)=>{
  const model=await(await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  const catalog=await(await request.get(`/api/v1/schemoo/catalog?connection_id=${model.connectionId}&namespace=public`)).json();
  const source="personnel_certification_fact",target="personnel_dim";
  const relationship=catalog.relationships.find(r=>r.sourceTable===source&&r.targetTable===target);
  expect(relationship).toBeTruthy();
  const definition={root:target,nodes:[{id:target,table:target,label:"Personnel"},{id:source,table:source,label:"Employee certifications"}],
    edges:[{id:relationship.id,relationshipId:relationship.id,source,target,enabled:true}],scopes:[],exposedFields:null};
  const updated=await request.put(`/api/v1/schemoo/models/${modelId}`,{data:{expectedRevision:model.revision,name:model.name,definition}});
  expect(updated.ok(),await updated.text()).toBeTruthy();
  const explored=await request.put(`/api/v1/schemoo/models/${modelId}/explore`,{data:{expectedRevision:model.exploreRevision,
    explore:{root:target,fields:[{table:target,column:"name",aggregate:"none"}],limit:100}}});
  expect(explored.ok(),await explored.text()).toBeTruthy();
  await page.goto(`/schemoo?model=${modelId}`);
  if(await page.locator("#inspector").isVisible())await page.getByRole("button",{name:"Close inspector",exact:true}).click();
  await page.getByRole("button",{name:"Fit model",exact:true}).click();
  await page.locator(`[data-node-id="${target}"] .sc-node-header`).click();
  await page.getByRole("button",{name:"Add calculated source",exact:true}).click();
  const dialog=page.getByRole("dialog",{name:"Add calculated source",exact:true});
  await dialog.getByRole("textbox",{name:"Calculated source name",exact:true}).fill("Certification summary");
  await chooseModelOption(page,"Calculation kind","Grouped summary · one row per group");
  await chooseModelOption(page,"Summarize records from","Employee certifications");
  await expect(dialog.getByRole("checkbox",{name:"personnel_id",exact:true})).toBeChecked();
  await expect(dialog.getByRole("checkbox",{name:"id",exact:true})).not.toBeChecked();
  await page.screenshot({path:`artifacts/schemoo-summary-source-${testInfo.project.name}.png`,animations:"disabled"});
  await expect(dialog.getByRole("combobox",{name:"Connect summary to",exact:true})).toHaveValue("Personnel");
  await expect(dialog.getByRole("combobox",{name:"Connect personnel_id to column",exact:true})).toHaveValue("id · uuid");
  await dialog.getByRole("textbox",{name:"Field 1 name",exact:true}).fill("Non-expired count");
  await chooseModelOption(page,"Field 1 operation","Count distinct values");
  await chooseModelOption(page,"Field 1 column","certification_id · uuid");
  await dialog.getByRole("button",{name:"Add Field 1 condition",exact:true}).click();
  await chooseModelOption(page,"Field 1 condition 1 field","Employee certifications · expiration_date");
  await chooseModelOption(page,"Field 1 condition 1 operator","≥");
  await chooseModelOption(page,"Field 1 condition 1 value source","Today (UTC) · evaluated when run");
  await dialog.getByRole("checkbox",{name:"Field 1 condition 1 also accepts null",exact:true}).check();
  await dialog.getByRole("combobox",{name:"Connect summary to",exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:`artifacts/schemoo-summary-connection-${testInfo.project.name}.png`,animations:"disabled"});
  await dialog.getByRole("button",{name:"Apply to model",exact:true}).click();
  await expect(page.locator("#table-inspector-title")).toHaveText("Certification summary");
  await page.getByRole("button",{name:"Save model",exact:true}).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  const saved=await(await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  const summary=saved.definition.nodes.find(n=>n.derivation);
  expect(summary.derivation).toMatchObject({source,groupBy:["personnel_id"],connection:{target,columns:[{source:"personnel_id",target:"id"}]}});
  await page.reload();
  await page.getByRole("button",{name:"Explore model",exact:true}).click();
  await chooseModelOption(page,"Preview source field","Certification summary · Non-expired count");
  await page.getByRole("button",{name:"Add preview output",exact:true}).click();
  await page.getByRole("button",{name:"Run preview",exact:true}).click();
  await expect(page.locator("#results table")).toBeVisible({timeout:30000});
  await expect(page.locator("#results thead")).toContainText("Certification summary.Non-expired count");
  await expect(page.locator("#error")).toBeHidden();
  const sql=await page.locator("#sql").textContent();
  expect(sql.match(/"public"\."personnel_dim"/g)).toHaveLength(1);
  expect(sql.match(/\bJOIN\b/g)).toHaveLength(1);
  expect(sql).toContain('"personnel_certification_fact"');
  expect(sql).toContain("GROUP BY");
  expect(sql).toContain("FILTER (WHERE");
  expect(sql).toContain("COALESCE");
});

test("date conditions distinguish today from fixed values and survive cancel and reload",async({page,request},testInfo)=>{
  const model=await(await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  const source="personnel_certification_fact";
  model.definition={root:source,nodes:[{id:source,table:source,label:"Employee certifications"},{id:"summary",table:source,label:"Validity",derivation:{kind:"aggregate",source,groupBy:["id"],outputs:[
    {id:"total",label:"Total",operation:"count",column:"certification_id"},
    {id:"current",label:"Non-expired",operation:"count",column:"certification_id"},
  ]}}],edges:[],scopes:[],exposedFields:null};
  const updated=await request.put(`/api/v1/schemoo/models/${modelId}`,{data:{expectedRevision:model.revision,name:model.name,definition:model.definition}});
  expect(updated.ok(),await updated.text()).toBeTruthy();
  await request.put(`/api/v1/schemoo/models/${modelId}/explore`,{data:{expectedRevision:model.exploreRevision,explore:{root:source,fields:[{table:source,column:"id",aggregate:"none"},{table:"summary",column:"total",aggregate:"none"},{table:"summary",column:"current",aggregate:"none"}],limit:100}}});
  await page.goto(`/schemoo?model=${modelId}`);
  if(await page.locator("#inspector").isVisible())await page.getByRole("button",{name:"Close inspector",exact:true}).click();
  await page.locator('[data-node-id="summary"] .sc-node-header').click();
  await page.getByRole("button",{name:"Edit calculated source",exact:true}).click();
  const dialog=page.getByRole("dialog",{name:"Edit calculated source",exact:true});
  await dialog.getByRole("button",{name:"Add Field 2 condition",exact:true}).click();
  await chooseModelOption(page,"Field 2 condition 1 field","Employee certifications · expiration_date");
  await chooseModelOption(page,"Field 2 condition 1 operator","≥");
  await chooseModelOption(page,"Field 2 condition 1 value source","Today (UTC) · evaluated when run");
  await dialog.getByRole("checkbox",{name:"Field 2 condition 1 also accepts null",exact:true}).check();
  await expect(dialog.getByRole("textbox",{name:"Field 2 condition 1 value",exact:true})).toHaveCount(0);
  await page.screenshot({path:`artifacts/schemoo-field-conditions-${testInfo.project.name}.png`,animations:"disabled"});
  await dialog.getByRole("button",{name:"Apply to model",exact:true}).click();
  await page.getByRole("button",{name:"Save model",exact:true}).click();
  await expect(page.locator("#draft-status")).toContainText("Saved");
  const saved=await(await request.get(`/api/v1/schemoo/models/${modelId}`)).json();
  const outputs=saved.definition.nodes.find(n=>n.id==="summary").derivation.outputs;
  expect(outputs[0].conditions).toEqual([]);
  expect(outputs[1].conditions[0]).toMatchObject({column:"expiration_date",operator:"gte",valueSource:"today",allowNull:true});
  expect(outputs[1].conditions[0].value).toBeNull();
  await page.getByRole("button",{name:"Edit calculated source",exact:true}).click();
  await dialog.getByRole("button",{name:"Remove Field 2 condition 1",exact:true}).click();
  await dialog.getByRole("button",{name:"Cancel",exact:true}).click();
  await expect(page.locator("#selection-inspector")).toContainText("Today (UTC)");
  await page.getByRole("button",{name:"Run preview",exact:true}).click();
  await expect(page.locator("#results table")).toBeVisible({timeout:30000});
  const sql=await page.locator("#sql").textContent();
  expect(sql.match(/FILTER \(WHERE/g)).toHaveLength(1);
  expect(sql).toContain('"expiration_date" IS NULL');
  await page.reload();
  await expect(page.getByRole("button",{name:"Save model",exact:true})).toBeDisabled();
});
