import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { reportDraft, reportExplore, availableFields, aggregateChoices, csvContent } from '../../src/schemii/schemer/web/report-state.js';
import { ICONS } from '../../src/schemii/common/web/assets/ui.js';
const model = { definition: {root:'person',nodes:[{id:'person',table:'people',label:'Personnel'}, {id:'manager',table:'people',label:'Manager'},
  {id:'calc',table:'people',label:'Calculated',derivation:{kind:'scalar',source:'person',outputs:[{id:'double',label:'Double salary',operation:'multiply',column:'salary'}]}}],edges:[],scopes:[]}, explore:{root:'manager',fields:[{table:'manager',column:'name',aggregate:'none'}],selections:{},reportFilters:[],limit:50} };
const catalog = {tables:[{name:'people',columns:[{name:'name',dataType:'text'},{name:'salary',dataType:'numeric'}]}]};
test('report always starts at authored root and copies independent query state', () => {
  const draft = reportDraft(model);
  assert.equal(draft.root,'person'); draft.fields[0].column='salary';
  assert.equal(model.explore.fields[0].column,'name');
  const explore=reportExplore(draft); explore.fields.pop(); assert.equal(draft.fields.length,1);
  assert.equal(explore.nodes,undefined);
});
test('all exposed physical, alias, and calculated fields can be searched', () => {
  const draft=reportDraft(model);
  assert.equal(availableFields(draft,catalog).length,5);
  assert.equal(availableFields(draft,catalog,'manager').length,2);
  assert.equal(availableFields(draft,catalog,'double salary')[0].column,'double');
  draft.exposedFields=[{table:'calc',column:'double'}];
  assert.deepEqual(availableFields(draft,catalog).map(f=>f.column),['double']);
});
test('measures reflect model data types', () => {
  const draft=reportDraft(model);
  assert.ok(aggregateChoices(draft,catalog,{table:'person',column:'salary'}).some(([key])=>key==='sum'));
  assert.ok(!aggregateChoices(draft,catalog,{table:'person',column:'name'}).some(([key])=>key==='sum'));
});
test('CSV preserves quotes, commas, and multiline values', () => {
  assert.equal(csvContent({columns:[{name:'name'}],rows:[['A,"B"\nC'],[null]]}), '"name"\r\n"A,""B""\nC"\r\n""');
});
test('Schemer controls use registered shared icons', () => {
  const html=readFileSync(new URL('../../src/schemii/schemer/web/index.html',import.meta.url),'utf8');
  for(const [,icon] of html.matchAll(/data-ui-icon(?:-leading)?="([^"]+)"/g)) assert.ok(ICONS[icon],`Unsupported ${icon}`);
});
