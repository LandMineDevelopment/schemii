import test from "node:test";
import assert from "node:assert/strict";
import { nodeColumns, fieldLabel } from "../../src/schemii/schemoo/web/model-columns.js";
import { exposedFields, setFieldExposure, splitDraft, joinModel, changedParts } from "../../src/schemii/schemoo/web/model-state.js";
import { ensureAliasConnections, isAlias, removeModelNode } from "../../src/schemii/schemoo/web/alias-model.js";
import { conditionSources, conditionSummary } from "../../src/schemii/schemoo/web/derived-conditions.js";
import { suggestSummaryConnection } from "../../src/schemii/schemoo/web/summary-connection.js";

function fixture() {
  const catalog={tables:[{name:"people",columns:[{name:"id",dataType:"uuid"},{name:"salary",dataType:"numeric"},{name:"bonus",dataType:"numeric"}]}],relationships:[]};
  const draft={root:"people",nodes:[{id:"people",table:"people",label:"People",x:0,y:0},{id:"calc",table:"people",label:"Compensation",x:320,y:0,derivation:{kind:"row",source:"people",groupBy:[],outputs:[{id:"total",label:"Total pay",operation:"add",column:"salary",operand:"bonus"}]}}],edges:[],scopes:[],fields:[],selections:{},reportFilters:[],limit:100};
  return {catalog,draft};
}
test("virtual sources expose authored outputs instead of duplicating physical columns",()=>{
  const {catalog,draft}=fixture();
  assert.deepEqual(nodeColumns(draft,catalog,draft.nodes[1]).map(c=>c.name),["total"]);
  assert.equal(fieldLabel(draft,catalog,{table:"calc",column:"total"}),"Compensation · Total pay");
  assert.ok(exposedFields(draft,catalog).some(f=>f.table==="calc"&&f.column==="total"));
  assert.throws(()=>setFieldExposure(draft,catalog,"calc","salary",true),/no longer exists/);
  setFieldExposure(draft,catalog,"calc","total",true);
  assert.equal(draft.fields.length,0,"exposing fields must not change preview outputs");
});
test("virtual metadata persists without layout or results leaking into definitions",()=>{
  const {draft}=fixture();const model={name:"Example",...splitDraft(draft)};
  const reopened=joinModel(model);
  assert.equal(reopened.nodes[1].derivation.outputs[0].operand,"bonus");
  assert.equal(reopened.nodes[1].x,320);
  assert.deepEqual(changedParts(model,reopened,"Example"),{definition:false,explore:false,layout:false});
});
test("derived associations are not ordinary alias foreign keys and protect owners",()=>{
  const {draft,catalog}=fixture();
  assert.equal(isAlias(draft.nodes[1]),false);
  ensureAliasConnections(draft,catalog);assert.deepEqual(draft.edges,[]);
  assert.throws(()=>removeModelNode(draft,"people"),/depend/);
  draft.fields=[{table:"calc",column:"total",aggregate:"none"}];
  removeModelNode(draft,"calc");assert.equal(draft.nodes.length,1);assert.deepEqual(draft.fields,[]);
});
test("summary columns include owner keys and readable authored labels",()=>{
  const {draft,catalog}=fixture();
  draft.nodes[1].derivation={kind:"aggregate",source:"people",groupBy:["id"],outputs:[{id:"n",label:"Count",operation:"count",column:"id"},{id:"names",label:"List",operation:"list",column:"id"}]};
  assert.deepEqual(nodeColumns(draft,catalog,draft.nodes[1]).map(c=>[c.name,c.dataType]),[["id","uuid"],["n","bigint"],["names","text"]]);
});
test("field conditions only offer owner and existing contributor path roles",()=>{
  const draft={nodes:["person","certs","definition_alias","jobs"].map(id=>({id,table:id,label:id})),edges:[
    {source:"certs",target:"person",enabled:true},{source:"certs",target:"definition_alias",enabled:true},{source:"jobs",target:"person",enabled:true}]};
  const definition={kind:"aggregate",source:"person"};
  assert.deepEqual([...conditionSources(draft,definition,{nodeId:"definition_alias"})].sort(),["certs","definition_alias","person"]);
  assert.deepEqual([...conditionSources(draft,{...definition,kind:"row"},{})],["person"]);
  draft.edges[1].enabled=false;
  assert.deepEqual([...conditionSources(draft,definition,{nodeId:"definition_alias"})],["person"]);
});
test("field condition metadata retains dynamic today and protects referenced aliases",()=>{
  const {draft}=fixture();
  draft.nodes.push({id:"alias",table:"people",label:"Historical role"});
  const condition={table:"alias",column:"end_date",operator:"gte",valueSource:"today",allowNull:true};
  draft.nodes[1].derivation.outputs[0].conditions=[condition];
  assert.equal(conditionSummary(condition,draft),"Historical role.end_date ≥ Today (UTC) OR NULL");
  const reopened=joinModel(splitDraft(draft));
  assert.deepEqual(reopened.nodes[1].derivation.outputs[0].conditions,[condition]);
  assert.throws(()=>removeModelNode(draft,"alias"),/depend/);
});

test("summary grouping and model connection are separate and protect both endpoints",()=>{
  const {draft,catalog}=fixture();
  draft.nodes.push({id:"certs",table:"cert",label:"Certifications"});
  catalog.tables.push({name:"cert",columns:[{name:"person_id",dataType:"uuid"}]});
  const definition={kind:"aggregate",source:"certs",groupBy:["person_id"],connection:{target:"people",columns:[{source:"person_id",target:"id"}]},outputs:[{id:"count",label:"Count",operation:"count",column:"person_id"}]};
  draft.nodes[1]={...draft.nodes[1],table:"cert",derivation:definition};
  assert.deepEqual(nodeColumns(draft,catalog,draft.nodes[1]).map(c=>c.name),["person_id","count"]);
  assert.deepEqual(joinModel(splitDraft(draft)).nodes[1].derivation.connection,definition.connection);
  assert.throws(()=>removeModelNode(draft,"people"),/depend/);
  assert.throws(()=>removeModelNode(draft,"certs"),/depend/);
});

test("summary mapping suggestions follow one explicit FK without choosing between roles",()=>{
  const draft={nodes:[{id:"people",table:"people"},{id:"certs",table:"cert"}],edges:[{source:"certs",target:"people",relationshipId:"person_fk",enabled:false}]};
  const catalog={tables:[{name:"people",primaryKey:["id"]}],relationships:[{id:"person_fk",sourceColumn:"person_id",targetColumn:"id"}]};
  assert.deepEqual(suggestSummaryConnection(draft,catalog,"certs","people"),[{source:"person_id",target:"id"}]);
  assert.deepEqual(suggestSummaryConnection(draft,catalog,"people","people"),[{source:"id",target:"id"}]);
  draft.edges.push({source:"certs",target:"people",relationshipId:"approver_fk"});
  catalog.relationships.push({id:"approver_fk",sourceColumn:"approver_id",targetColumn:"id"});
  assert.deepEqual(suggestSummaryConnection(draft,catalog,"certs","people"),[]);
});
