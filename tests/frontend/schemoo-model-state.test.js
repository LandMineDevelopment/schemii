import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { splitDraft, joinModel, changedParts, exposedFields, setFieldExposure, inheritAliasExposure, initializeExposureFromPreview } from "../../src/schemii/schemoo/web/model-state.js";
import { importedDraft } from "../../src/schemii/schemoo/web/model-draft.js";
import { ICONS } from "../../src/schemii/common/web/assets/ui.js";

test("Schemoo toolbar uses supported shared icons so initialization cannot abort", () => {
  const html=readFileSync(new URL("../../src/schemii/schemoo/web/index.html",import.meta.url),"utf8");
  for(const [,icon] of html.matchAll(/data-ui-icon(?:-leading)?="([^"]+)"/g)) assert.ok(ICONS[icon],`Unsupported icon ${icon}`);
});

const draft = () => ({root:"people",nodes:[{id:"people",table:"people",label:"People",x:10,y:20}],edges:[],scopes:[],fields:[{table:"people",column:"name",aggregate:"none"}],selections:{},reportFilters:[],limit:100});
const catalog = { tables: [{name:"people",columns:[{name:"id"},{name:"name"}]}], relationships: [] };
test("existing canvas selections become exposure independently of preview aggregates", () => {
  const value=draft(); value.fields.push({table:"people",column:"name",aggregate:"count"});
  assert.equal(initializeExposureFromPreview(value),true);
  assert.deepEqual(value.exposedFields,[{table:"people",column:"name",aggregate:"none"}]);
  assert.equal(value.fields.length,2);
  value.fields=[];
  assert.equal(initializeExposureFromPreview(value),false);
  assert.equal(value.exposedFields.length,1);
});
test("new imported models expose all columns but preview starts with one independent field", () => {
  const value=importedDraft(catalog);
  assert.equal(value.exposedFields.length,2);
  assert.equal(value.fields.length,1);
  value.fields=[];
  assert.equal(value.exposedFields.length,2);
});
test("exposure toggles prune preview outputs but preserve report restrictions", () => {
  const value=draft();
  value.fields.push({table:"people",column:"name",aggregate:"count"});
  value.reportFilters=[{conditions:[{table:"people",column:"name",operator:"not_null"}]}];
  assert.equal(exposedFields(value,catalog).length,2);
  setFieldExposure(value,catalog,"people","name",false);
  assert.deepEqual(value.exposedFields,[{table:"people",column:"id",aggregate:"none"}]);
  assert.deepEqual(value.fields,[]);
  assert.equal(value.reportFilters[0].conditions.length,1);
  setFieldExposure(value,catalog,"people","name",true);
  setFieldExposure(value,catalog,"people","name",true);
  assert.equal(value.exposedFields.length,2);
  assert.deepEqual(value.fields,[]);
  assert.throws(()=>setFieldExposure(value,catalog,"people","missing",true),/no longer exists/);
});
test("alias exposure inherits the chosen role without copying its preview selection", () => {
  const value=draft(); value.exposedFields=[{table:"people",column:"name",aggregate:"none"}];
  inheritAliasExposure(value,"people","manager");
  inheritAliasExposure(value,"people","manager");
  assert.deepEqual(value.exposedFields,[{table:"people",column:"name",aggregate:"none"},{table:"manager",column:"name",aggregate:"none"}]);
  assert.equal(value.fields.length,1);
});
test("model persistence separates rules, layout, and exploratory data without rows", () => {
  const value=draft(), parts=splitDraft({...value,rows:[["not metadata"]]});
  assert.equal(parts.definition.nodes[0].x,undefined);
  assert.deepEqual(parts.layout.positions,[{id:"people",x:10,y:20}]);
  assert.equal(parts.definition.fields,undefined);
  assert.equal(parts.explore.rows,undefined);
  assert.deepEqual(joinModel(parts),{...value,defaultRoot:value.root});
});
test("opening persisted model is independent and changes target only their owned part", () => {
  const model={...splitDraft(draft()),name:"HR"};
  const editing=joinModel(model);
  assert.deepEqual(changedParts(model,editing,"HR"),{definition:false,layout:false,explore:false});
  editing.nodes[0].x=50;
  assert.deepEqual(changedParts(model,editing,"HR"),{definition:false,layout:true,explore:false});
  editing.fields=[];
  assert.deepEqual(changedParts(model,editing,"HR"),{definition:false,layout:true,explore:true});
  assert.equal(model.layout.positions[0].x,10);
  assert.equal(model.explore.fields.length,1);
  assert.equal(changedParts(model,editing,"New name").definition,true);
  editing.root="another_node";
  assert.equal(splitDraft(editing).definition.root,"people");
  assert.equal(splitDraft(editing).explore.root,"another_node");
});
test("generated layout is clean until the author moves a source", () => {
  const model={...splitDraft(draft()),name:"HR"};
  model.layout={positions:[]};
  const editing=joinModel(model);
  editing.nodes[0].x=50; editing.nodes[0].y=60;
  const initialLayout=splitDraft(editing).layout;
  assert.deepEqual(changedParts(model,editing,"HR",initialLayout),{definition:false,layout:false,explore:false});
  editing.nodes[0].x=70;
  assert.equal(changedParts(model,editing,"HR",initialLayout).layout,true);
});
test("missing source metadata does not discard model nodes or alias layout", () => {
  const model={...splitDraft(draft()),name:"HR"};
  model.definition.nodes.push({id:"role_alias",table:"deleted_table",label:"Past role"});
  model.layout.positions.push({id:"role_alias",x:-40,y:300});
  const editing=joinModel(model);
  assert.equal(editing.nodes[1].table,"deleted_table");
  assert.equal(editing.nodes[1].x,-40);
});

test("server-normalized defaults and reordered JSON keys remain clean after save", () => {
  const model={...splitDraft(draft()),name:"HR"};
  model.definition={...model.definition,scopes:[{id:"time",kind:"conditional",label:"Time",alternatives:[{id:"all",label:"All",inputs:[{id:"day",label:"Day",type:"date",defaultValue:"",domain:null}],conditions:[{table:"people",column:"id",operator:"eq",parameterId:"day",value:null,allowNull:false}]}]}],exposedFields:null};
  model.definition.nodes=[{label:"People",table:"people",id:"people"}];
  assert.deepEqual(changedParts(model,joinModel(model),"HR"),{definition:false,layout:false,explore:false});
});
