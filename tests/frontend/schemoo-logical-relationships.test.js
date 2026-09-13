import test from "node:test";
import assert from "node:assert/strict";
import {comparableTypes,edgeRelationship,validateLogicalRelationship} from "../../src/schemii/schemoo/web/logical-relationships.js";
import {reconcileSourceCatalog} from "../../src/schemii/schemoo/web/source-reconciliation.js";
import {summaryLookup} from "../../src/schemii/schemoo/web/summary-lookup.js";
function fixture(){
  const catalog={tables:[{name:"facts",primaryKey:["id"],columns:[{name:"id",dataType:"uuid"},{name:"org_id",dataType:"uuid"}]},{name:"hier",primaryKey:["id"],columns:[{name:"id",dataType:"uuid"},{name:"child_id",dataType:"uuid"}]}],relationships:[]};
  const edge={id:"direct",kind:"logical",source:"facts",sourceColumn:"org_id",target:"hier",targetColumn:"child_id",enabled:true};
  const draft={root:"facts",nodes:catalog.tables.map(t=>({id:t.name,table:t.name,label:t.name})),edges:[edge],fields:[]};return {catalog,draft,edge};
}
test("column compatibility supports equality without guessed casts",()=>{
  for(const pair of [["uuid","uuid"],["numeric(10, 2)","int8"],["varchar(50)","text"],["timestamp(6) with time zone","timestamptz"]])assert.equal(comparableTypes(...pair),true,pair.join(" / "));
  for(const pair of [["uuid","text"],["date","timestamp"],["json","json"],["",""],["integer[]","integer[]"],["custom","custom"]])assert.equal(comparableTypes(...pair),false,pair.join(" / "));
});
test("logical connections validate live columns and reject reverse duplicates",()=>{
  const {catalog,draft,edge}=fixture();assert.equal(validateLogicalRelationship(edge,draft,catalog),"");assert.equal(edgeRelationship(edge,catalog),edge);
  assert.match(validateLogicalRelationship({...edge,id:"other",source:"hier",sourceColumn:"child_id",target:"facts",targetColumn:"org_id"},draft,catalog),/already exists/);
  assert.match(validateLogicalRelationship({...edge,targetColumn:"missing"},draft,catalog),/available target/);
  catalog.tables[1].columns[1].dataType="text";assert.match(validateLogicalRelationship(edge,draft,catalog),/comparable types/);
});
test("source refresh retains authored connections and the query starting object",()=>{
  const {catalog,draft,edge}=fixture();reconcileSourceCatalog(draft,catalog);assert.deepEqual(draft.edges,[edge]);assert.equal(draft.root,"facts");
});
test("logical summary lookups use actual keys rather than author cardinality",()=>{
  const {catalog,draft,edge}=fixture();edge.cardinality="many_to_one";
  assert.equal(summaryLookup(draft,catalog,"facts","hier").status,"multiplying");
  catalog.tables[1].uniqueKeys=[["child_id"]];assert.equal(summaryLookup(draft,catalog,"facts","hier").status,"many_to_one");
});
