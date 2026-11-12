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

test("catalog reconciliation offers omitted tables without adding objects or expanding contracts",()=>{
  const original=catalog(), draft={root:"parent",nodes:[{id:"parent",table:"parent",label:"Parent"}],edges:[],scopes:[],sourceContract:catalogContract({tables:[original.tables[0]],relationships:[]})};
  const result=reconcileSourceCatalog(draft,original);
  assert.deepEqual(result.tables,["child"]);
  assert.deepEqual(result.relationships,[]);
  assert.deepEqual(draft.nodes.map(node=>node.id),["parent"]);
  assert.deepEqual(draft.edges,[]);
  assert.deepEqual(draft.sourceContract,catalogContract({tables:[original.tables[0]],relationships:[]}));
});

test("catalog reconciliation preserves a model that intentionally omits an existing source table",()=>{
  const current=catalog();
  const draft={root:"parent",nodes:[{id:"parent",table:"parent",label:"Parent"}],edges:[],scopes:[],sourceContract:catalogContract(current)};
  const result=reconcileSourceCatalog(draft,current);
  assert.deepEqual(result.tables,["child"]);
  assert.deepEqual(draft.nodes.map(node=>node.id),["parent"]);
  assert.deepEqual(draft.edges,[]);
});

test("new columns are offered and existing relationship assumptions are preserved",()=>{
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


test("legacy unmarked contracts remain reviewable without guessing source scope or expanding",()=>{
  const current=catalog(),baseline=catalogContract({tables:[current.tables[0]],relationships:[]});delete baseline.scope;
  const draft={root:"parent",nodes:[{id:"parent",table:"parent"}],edges:[],sourceContract:baseline};
  const before=structuredClone(draft);reconcileSourceCatalog(draft,current);assert.deepEqual(draft,before);
});

test("reopening new sources and relationships cannot push a model beyond its bounds",()=>{
  const current=catalog();current.relationships.push(...Array.from({length:201},(_,i)=>relationship(`new${i}`)));
  const draft={nodes:current.tables.map(table=>({id:table.name,table:table.name})),edges:[],sourceContract:catalogContract(catalog(),"model")};
  const before=structuredClone(draft);const result=reconcileSourceCatalog(draft,current);
  assert.equal(result.relationships.length,202);assert.deepEqual(draft,before);
});

test("legacy FK hashes preserve authored refs and cannot duplicate or acknowledge another namespace",()=>{
  const catalog={namespace:"one",tables:[{namespace:"one",name:"left",columns:[{name:"id"}]},{namespace:"one",name:"right",columns:[{name:"id"}]},{namespace:"two",name:"right",columns:[{name:"id",dataType:"changed"}]}],relationships:[{id:"qualified",legacyId:"legacy",name:"fk",sourceNamespace:"one",sourceTable:"left",sourceColumn:"id",targetNamespace:"one",targetTable:"right",targetColumn:"id",columns:[{source:"id",target:"id"}]}]};
  const draft={root:"left",nodes:[{id:"left",table:"left"},{id:"right",table:"right"}],edges:[{id:"old",relationshipId:"legacy",source:"left",target:"right",enabled:true}],exposedFields:[{table:"left",column:"id"}],sourceContract:{scope:"model",tables:[{namespace:"one",name:"right",columns:[{name:"id",dataType:"old"}]}],relationships:[]}};
  assert.equal(acceptSourceIssue(draft,catalog,{kind:"changed_column",namespace:"two",table:"right",column:"id",acknowledge:true}),false);
  assert.equal(reconcileSourceCatalog(draft,catalog).relationships.length,0);
});
