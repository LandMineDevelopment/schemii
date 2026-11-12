import test from "node:test";
import assert from "node:assert/strict";
import { nodeColumns } from "../../src/schemii/schemoo/web/model-columns.js";

function model(dataType) {
  const catalog={tables:[{name:"events",columns:[{name:"id",dataType:"integer"},{name:"value",dataType}]}]};
  const source={id:"events",table:"events"};
  const summary={id:"summary",table:"events",derivation:{kind:"aggregate",source:"events",groupBy:["id"],outputs:[
    {id:"first",operation:"min",column:"value"},{id:"last",operation:"max",column:"value"}]}};
  return {catalog,draft:{nodes:[summary,source]},summary};
}
for(const [input,expected] of [
  ["timestamp(6) with time zone","timestamp with time zone"],["timestamp(6) without time zone","timestamp without time zone"],
  ["timestamp(6)","timestamp"],["timestamptz","timestamp with time zone"],
  ["time(3) with time zone","time with time zone"],["time(3) without time zone","time without time zone"],
  ["time(3)","time"],["timetz","time with time zone"],["numeric(12, 2)","numeric"],
]) test(`extrema retain the complete type of ${input}`,()=>{
  const {catalog,draft,summary}=model(input);
  assert.deepEqual(nodeColumns(draft,catalog,summary).slice(1).map(column=>column.dataType),[expected,expected]);
});
test("dotted and quoted field tuples resolve independently of their readable path",()=>{
  const {catalog,draft}=model("integer");
  const inner={id:"a",table:"events",derivation:{kind:"row",source:"events",outputs:[
    {id:"b.c",operation:"add",column:"value",operand:"id"}]}};
  const outer={id:"a.b",table:"events",derivation:{kind:"row",source:"events",outputs:[
    {id:"c",operation:"add",nodeId:"a",column:"b.c",operand:"id"}]}};
  draft.nodes.unshift(outer,inner);
  assert.equal(nodeColumns(draft,catalog,outer)[0].dataType,"integer");
  assert.equal(nodeColumns(draft,catalog,outer)[0].issue,undefined);
  inner.id='a"quoted';outer.derivation.outputs[0].nodeId=inner.id;
  assert.equal(nodeColumns(draft,catalog,outer)[0].dataType,"integer");
  inner.derivation.outputs[0].nodeId=outer.id;inner.derivation.outputs[0].column="c";
  const invalid=nodeColumns(draft,catalog,outer)[0];
  assert.equal(invalid.name,"c");
  assert.ok(invalid.issue.includes(JSON.stringify([outer.id,"c"])));
  assert.ok(invalid.issue.includes(JSON.stringify([inner.id,"b.c"])));
  assert.match(invalid.issue,/dependency cycle/);
});
