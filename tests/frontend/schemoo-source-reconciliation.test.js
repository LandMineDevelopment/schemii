import test from "node:test";
import assert from "node:assert/strict";
import { catalogContract } from "../../src/schemii/schemoo/web/model-draft.js";
import { reconcileSourceCatalog, acceptSourceIssue } from "../../src/schemii/schemoo/web/source-reconciliation.js";
import { nodeColumns } from "../../src/schemii/schemoo/web/model-columns.js";

const relationship=(id="fk")=>({id,name:id,sourceTable:"child",sourceColumn:"parent_id",targetTable:"parent",targetColumn:"id"});
const catalog=()=>({tables:[
  {name:"parent",primaryKey:["id"],columns:[{name:"id",dataType:"uuid",nullable:false}]},
  {name:"child",primaryKey:["id"],columns:[{name:"id",dataType:"uuid",nullable:false},{name:"parent_id",dataType:"uuid",nullable:true}]},
],relationships:[relationship()],positions:[{name:"child",x:500,y:40}]});

test("moved columns disappear from physical and alias outputs without changing rules or inferring replacement selections",()=>{
  const before=catalog(), current=catalog();
  const date={name:"slot_termination_date",dataType:"date",nullable:false};
  before.tables[1].columns.push(date);current.tables[0].columns.push(date);
  const keep={table:"child",column:"id"};
  const removed={table:"child",column:date.name};
  const alias={table:"alias",column:date.name};
  const calculated={table:"summary",column:"count"};
  const draft={nodes:[{id:"parent",table:"parent"},{id:"child",table:"child"},{id:"alias",table:"child"},
    {id:"summary",table:"child",derivation:{source:"child",kind:"aggregate",outputs:[{id:"count",column:date.name}]}}],
    edges:[],scopes:[{alternatives:[{conditions:[removed]}]}],reportFilters:[{conditions:[removed]}],
    exposedFields:[removed,keep,alias,calculated],fields:[alias,keep,removed,calculated],sourceContract:catalogContract(before)};
  const rules=structuredClone({scopes:draft.scopes,reportFilters:draft.reportFilters,nodes:draft.nodes});
  reconcileSourceCatalog(draft,current);
  assert.deepEqual(draft.exposedFields,[keep,calculated]);
  assert.deepEqual(draft.fields,[keep,calculated]);
  assert.deepEqual({scopes:draft.scopes,reportFilters:draft.reportFilters,nodes:draft.nodes},rules);
  assert.equal(nodeColumns(draft,current,draft.nodes[0]).some(c=>c.name===date.name),true);
  assert.equal(nodeColumns(draft,current,draft.nodes[1]).some(c=>c.name===date.name),false);
  const once=structuredClone(draft);reconcileSourceCatalog(draft,current);assert.deepEqual(draft,once);
});

test("catalog reconciliation adds tables, columns and relationships without enabling paths",()=>{
  const original=catalog(), draft={root:"parent",nodes:[{id:"parent",table:"parent",label:"Parent"}],edges:[],scopes:[],sourceContract:catalogContract({tables:[original.tables[0]],relationships:[]})};
  const result=reconcileSourceCatalog(draft,original);
  assert.deepEqual(result.tables,["child"]);
  assert.deepEqual(result.relationships,["fk"]);
  assert.equal(draft.nodes.find(node=>node.id==="child").x,500);
  assert.deepEqual(draft.edges,[{id:"source_fk_fk",relationshipId:"fk",source:"child",target:"parent",enabled:false}]);
  assert.deepEqual(draft.sourceContract,catalogContract(original));
});

test("catalog reconciliation preserves a model that intentionally omits an existing source table",()=>{
  const current=catalog();
  const draft={root:"parent",nodes:[{id:"parent",table:"parent",label:"Parent"}],edges:[],scopes:[],sourceContract:catalogContract(current)};
  const result=reconcileSourceCatalog(draft,current);
  assert.deepEqual(result.tables,[]);
  assert.deepEqual(draft.nodes.map(node=>node.id),["parent"]);
  assert.deepEqual(draft.edges,[]);
});

test("new columns remain an additive contract update and existing relationship assumptions are preserved",()=>{
  const current=catalog(), prior=catalog();
  prior.tables[1].columns.pop();
  prior.relationships[0].sourceColumn="old_parent_id";
  const draft={root:"child",nodes:current.tables.map(table=>({id:table.name,table:table.name,label:table.name})),
    edges:[{id:"fk",relationshipId:"fk",source:"child",target:"parent",enabled:true}],scopes:[],sourceContract:catalogContract(prior)};
  const result=reconcileSourceCatalog(draft,current);
  assert.deepEqual(result.columns,["child.parent_id"]);
  assert.equal(draft.sourceContract.relationships[0].sourceColumn,"old_parent_id");
  assert.equal(draft.edges[0].enabled,true);
});

test("accepting a changed foreign key updates only its contract and disables every affected edge",()=>{
  const current=catalog(), prior=catalog();prior.relationships[0].sourceColumn="old_parent_id";
  const draft={nodes:[],edges:[{relationshipId:"fk",enabled:true},{relationshipId:"other",enabled:true}],sourceContract:catalogContract(prior)};
  assert.equal(acceptSourceIssue(draft,current,{kind:"changed_relationship",relationshipId:"fk",acknowledge:true}),true);
  assert.equal(draft.sourceContract.relationships[0].sourceColumn,"parent_id");
  assert.deepEqual(draft.edges.map(edge=>edge.enabled),[false,true]);
});

test("table drift acknowledgement changes only the reviewed assumption",()=>{
  const current=catalog(),prior=catalog();prior.tables[1].primaryKey=["old_id"];prior.tables[1].columns[1].dataType="text";
  const draft={nodes:[],edges:[],sourceContract:catalogContract(prior)};
  assert.equal(acceptSourceIssue(draft,current,{kind:"changed_primary_key",table:"child",acknowledge:true}),true);
  assert.deepEqual(draft.sourceContract.tables[1].primaryKey,["id"]);
  assert.equal(draft.sourceContract.tables[1].columns[1].dataType,"text");
  assert.equal(acceptSourceIssue(draft,current,{kind:"changed_column",table:"child",column:"parent_id",acknowledge:true}),true);
  assert.equal(draft.sourceContract.tables[1].columns[1].dataType,"uuid");
});
