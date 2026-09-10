import test from "node:test";
import assert from "node:assert/strict";
import {summaryLookup} from "../../src/schemii/schemoo/web/summary-lookup.js";

function fixture(){
  const draft={nodes:[{id:"facts",table:"facts",label:"Certifications"},{id:"definitions_alias",table:"definitions",label:"Definitions"},{id:"people",table:"people",label:"Personnel"}],edges:[{id:"a",relationshipId:"definition_fk",source:"facts",target:"definitions_alias",enabled:true},{id:"b",relationshipId:"person_fk",source:"facts",target:"people",enabled:true}]};
  const catalog={relationships:[{id:"definition_fk",sourceTable:"facts",sourceColumn:"definition_id",targetTable:"definitions",targetColumn:"id"},{id:"person_fk",sourceTable:"facts",sourceColumn:"person_id",targetTable:"people",targetColumn:"id"}]};
  return {draft,catalog};
}
test("lookup explains FK direction and authored alias labels",()=>{
  const {draft,catalog}=fixture();
  const result=summaryLookup(draft,catalog,"facts","definitions_alias");
  assert.equal(result.status,"many_to_one");
  assert.deepEqual(result.path,["Certifications.definition_id → Definitions.id"]);
  assert.match(result.message,/at most one/);
});
test("source values need no lookup",()=>{
  const {draft,catalog}=fixture();assert.equal(summaryLookup(draft,catalog,"facts","facts").status,"source");
});
test("reverse or mixed direction paths warn about multiplication",()=>{
  const {draft,catalog}=fixture();
  const result=summaryLookup(draft,catalog,"people","definitions_alias");
  assert.equal(result.status,"multiplying");assert.equal(result.path.length,2);
});
test("disabled relationships are disconnected rather than safe",()=>{
  const {draft,catalog}=fixture();draft.edges[0].enabled=false;
  assert.equal(summaryLookup(draft,catalog,"facts","definitions_alias").status,"disconnected");
});
test("ambiguous parallel paths are never shown as validated",()=>{
  const {draft,catalog}=fixture();draft.edges.push({...draft.edges[0],id:"second"});
  assert.equal(summaryLookup(draft,catalog,"facts","definitions_alias").status,"ambiguous");
});
test("missing and mismatched schema relationships need refresh",()=>{
  const {draft,catalog}=fixture();catalog.relationships[0].targetTable="other";
  assert.equal(summaryLookup(draft,catalog,"facts","definitions_alias").status,"unknown");
  assert.equal(summaryLookup(draft,catalog,"facts","missing").status,"unknown");
});
