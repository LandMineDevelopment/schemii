import test from 'node:test';
import assert from 'node:assert/strict';
import {calculationErrors,visibleMeasures,calculationRef,outputKey} from '../../src/schemii/schemer/web/measures.js';
import {reportOutputs,tileErrors} from '../../src/schemii/schemer/web/dashboard-state.js';
const source={table:'people',column:'amount',aggregate:'sum',kind:'value',dataType:'numeric',label:'Amount'};
const operand=scope=>({output:{table:'people',column:'amount',aggregate:'sum'},scope});
const calc=(id='share')=>({id,label:id,operation:'percent_of',left:operand('group'),right:operand('source_population'),visible:true});
const tile=()=>({kind:'aggregate',title:'Shares',dimensions:[],measures:[],hiddenMeasures:[{table:'people',column:'amount',aggregate:'sum'}],calculations:[calc()],detailFields:[{table:'people',column:'id'}],sort:[],aggregateFilters:[]});
test('named calculation identity survives labels and authored ordering',()=>{
  const current=tile();const key=outputKey(calculationRef(current.calculations[0]));
  current.calculations[0].label='Share of source population';assert.equal(outputKey(calculationRef(current.calculations[0])),key);
  assert.equal(visibleMeasures(current)[0].label,'Share of source population');
  assert.deepEqual(calculationErrors(current,[source,...reportOutputs(current).filter(output=>output.kind==='calculated')]),[]);
});
test('dependency cycles and missing operands remain actionable instead of disappearing',()=>{
  const current=tile();current.calculations[0].left={output:calculationRef(current.calculations[0]),scope:'group'};
  assert.match(calculationErrors(current,[source,...reportOutputs(current)]).join(' '),/cycle.*share/);
  current.calculations[0].left.output.column='removed';
  assert.match(calculationErrors(current,[source,...reportOutputs(current)]).join(' '),/removed/);
  assert.equal(current.calculations[0].left.output.column,'removed');
});
test('qualifying denominator filters reject circular membership, while source scope remains independent',()=>{
  const current=tile();current.calculations[0].right.scope='qualifying_groups';current.aggregateFilters=[{...calculationRef(current.calculations[0]),operator:'gt',value:1}];
  assert.match(calculationErrors(current,[source,...reportOutputs(current)]).join(' '),/own denominator/);
  current.calculations[0].right.scope='source_population';assert.deepEqual(calculationErrors(current,[source,...reportOutputs(current)]),[]);
});
test('exact numeric author tokens remain unchanged through validation',()=>{
  const current=tile();const value='0.10000000000000000000001';current.calculations[0].right={constant:value,scope:'group'};
  assert.deepEqual(calculationErrors(current,[source,...reportOutputs(current)]),[]);assert.equal(current.calculations[0].right.constant,value);
  current.calculations[0].right.constant='not numeric';assert.match(calculationErrors(current,[source,...reportOutputs(current)]).join(' '),/numeric constant/);
});
test('hidden unused definitions do not activate an unrelated report branch',()=>{
  const current=tile();current.hiddenMeasures.push({table:'other',column:'id',aggregate:'count'});
  assert.match(calculationErrors(current,[source,...reportOutputs(current)]).join(' '),/unused/);
  assert.equal(current.hiddenMeasures.length,2);
});

test('a legal model output matching the report token stays selectable and distinct from a calculation',async()=>{
  const {measureEditor}=await import('../../src/schemii/schemer/web/measures.js');
  const current=tile();current.hiddenMeasures=[{table:'@report',column:'share',aggregate:'none'}];
  current.calculations[0].left={output:{table:'@report',column:'share',aggregate:'none',kind:'value'},scope:'group'};
  current.calculations[0].right={constant:'1',scope:'group'};
  const physical={table:'@report',column:'share',aggregate:'none',kind:'value',dataType:'numeric',label:'Actual model output'};
  const outputs=[physical,{...calculationRef(current.calculations[0]),label:'Calculated share',dataType:'numeric'}];
  assert.deepEqual(calculationErrors(current,outputs),[]);
  const selectors=[];
  const element=()=>({append(){}});
  measureEditor(current,{outputs,onChange(){},element,modelSelect:(label,choices)=>{selectors.push({label,choices});return {};}});
  const choices=selectors.find(item=>item.label==='share Left / numerator').choices;
  assert.ok(choices.some(([key])=>key===outputKey(physical)));
  assert.ok(!choices.some(([key])=>key===outputKey(calculationRef(current.calculations[0]))));
});
