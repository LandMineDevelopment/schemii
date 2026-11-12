import { measureEditor, calculationErrors } from "../../src/schemii/schemer/web/measures.js";
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
test('cached result CSV preserves JSON values and quotes multiline text', () => {
  assert.equal(csvContent({
    columns:[{name:'payload'},{name:'items'},{name:'name'}],
    rows:[[{a:1},[1,2],'A,"B"\nC'],[null,null,null]],
  }), '"payload","items","name"\r\n"{""a"":1}","[1,2]","A,""B""\nC"\r\n"","",""');
});
test('Schemer controls use registered shared icons', () => {
  const html=readFileSync(new URL('../../src/schemii/schemer/web/index.html',import.meta.url),'utf8');
  for(const [,icon] of html.matchAll(/data-ui-icon(?:-leading)?="([^"]+)"/g)) assert.ok(ICONS[icon],`Unsupported ${icon}`);
});


import { dashboardUpdate, reportOutputs, reportOutputKey, reportControlErrors, numericFilterValue, validTimeFilterValue, tileDimensions, pivotDefaults, tileErrors, TILE_TYPES, fieldChoices, tileAggregateChoices } from '../../src/schemii/schemer/web/dashboard-state.js';
import vm from 'node:vm';
const reportTile = () => ({ id: 'report', title: 'Revenue', kind: 'bar', dimensions: [{ table: 'person', column: 'name', aggregate: 'none' }],
  measures: [{ table: 'person', column: 'salary', aggregate: 'sum' }], detailFields: [{ table: 'person', column: 'name', aggregate: 'none' }], reportFilters: [], selections: {}, limit: 2 });
const metric = (extra = {}) => ({ table: 'person', column: 'salary', aggregate: 'sum', kind: 'value', ...extra });

test('semantic output references survive field reordering and configuration cloning', () => {
  const tile = reportTile(); tile.sort = [metric({ direction: 'desc', nulls: 'last' })]; tile.topN = 100;
  tile.aggregateFilters = [metric({ operator: 'gte', value: 20 })];
  const saved = { id: 'd', name: 'D', modelId: 'm', modelRevision: 1, revision: 4, tiles: [tile], selections: {} };
  const update = dashboardUpdate(saved);
  assert.deepEqual(update.tiles[0].sort, tile.sort); assert.equal(update.tiles[0].limit, 2); assert.equal(update.tiles[0].topN, 100);
  update.tiles[0].sort[0].direction = 'asc'; assert.equal(tile.sort[0].direction, 'desc');
  assert.equal(reportOutputKey(tile.sort[0]), reportOutputKey(reportOutputs(tile, model, catalog)[1]));
  assert.deepEqual(reportControlErrors(tile, model, catalog), []);
});

for (const topN of [0, -1, 1.5, true, '10', NaN, Infinity, 2147483648]) {
  test(`invalid semantic topN ${String(topN)} blocks Apply independently of batch size`, () => {
    assert(reportControlErrors({ ...reportTile(), topN }, model, catalog).some(error => /positive whole/.test(error)));
  });
}

test('sort priorities reject duplicates or unavailable semantic outputs without pruning them', () => {
  const tile = reportTile(); tile.sort = [metric({ direction: 'desc' }), metric({ direction: 'asc' })];
  assert(reportControlErrors(tile).some(error => /already sorted/.test(error)));
  tile.measures = [];
  assert(reportControlErrors(tile).some(error => /selected output/.test(error)));
  assert.equal(tile.sort.length, 2);
});

test('aggregate-result filters require selected aggregate outputs and typed values or NULL operators', () => {
  const tile = reportTile();
  for (const value of ['20', true, null, NaN]) {
    tile.aggregateFilters = [metric({ operator: 'gt', value })];
    assert(reportControlErrors(tile, model, catalog).length);
  }
  tile.aggregateFilters = [metric({ operator: 'gt', value: 20 }), metric({ operator: 'not_null', value: null })];
  assert.deepEqual(reportControlErrors(tile, model, catalog), []);
  tile.aggregateFilters.push({ table: 'person', column: 'name', operator: 'eq', value: 'A' });
  assert(reportControlErrors(tile, model, catalog).some(error => /selected measure/.test(error)));
});

test('only enabled time outputs can be used and their order matches complete window metadata', () => {
  const tile = reportTile(); tile.timeAnalysis = { table: 'person', column: 'name', comparison: 'previous_period', runningTotal: true };
  assert.deepEqual(reportOutputs(tile).map(output => output.kind), ['value', 'value', 'comparison', 'change', 'percent_change', 'running_total']);
  tile.sort = [metric({ kind: 'running_total', direction: 'desc', nulls: 'last' })];
  tile.aggregateFilters = [metric({ kind: 'change', operator: 'gt', value: 0 })];
  assert.deepEqual(reportControlErrors(tile, model, catalog), []);
  tile.timeAnalysis = null;
  assert.equal(reportControlErrors(tile, model, catalog).length, 2);
  assert.equal(tile.sort[0].kind, 'running_total');
});

const tileEditorSource = readFileSync(new URL('../../src/schemii/schemer/web/tile-editor.js', import.meta.url), 'utf8');
function editorFixture({ tile = reportTile(), sourceModel = model, schema = catalog, pivot = false } = {}) {
  const nodes = [], saves = [];
  const element = (tag, options = {}, children = []) => {
    const node = { tag, ...options, textContent: options.text || '', children: [...children], open: false,
      append(...items) { this.children.push(...items); }, replaceChildren(...items) { this.children = items; }, setAttribute() {},
      addEventListener() {}, remove() {}, focus() {}, showModal() { this.open = true; }, close() { this.open = false; },
      querySelector() { return nodes.findLast(node => node.textContent === 'Cancel'); },
    }; nodes.push(node); return node;
  };
  const context = vm.createContext({ element, document: { body: element('body') }, window: { confirm: () => true }, structuredClone,
    createIconButton: ({ label }) => element('button', { text: label }),
    modelSelect: (label, choices, selected, change) => element('select', { text: label, choices, selected, change }),
    disposeSelects() {}, timeDimensions: () => [], fieldLabel: (_draft, _catalog, field) => field.column,
    aggregateChoices, fieldChoices: (...args) => pivot ? fieldChoices(...args) : [], fieldKey: field => JSON.stringify([field.table, field.column]),
    measureEditor, TILE_TYPES, tileErrors: (tile, source, schema) => pivot ? tileErrors(tile, source, schema) : [...reportControlErrors(tile, source, schema),...calculationErrors(tile,reportOutputs(tile,source,schema))], reportOutputs, reportOutputKey, numericFilterValue, tileDimensions, pivotDefaults, tileAggregateChoices,
    tile, model: sourceModel, catalog: schema, onSave: async tile => saves.push(structuredClone(tile)),
  });
  vm.runInContext(tileEditorSource.replace(/^import .*;\n/gm, '').replace('export function openTileEditor', 'function openTileEditor'), context);
  vm.runInContext('openTileEditor({ tile, model, catalog, onSave });', context);
  const node = label => nodes.findLast(node => node.textContent === label || node.attrs?.['aria-label'] === label);
  return { node, saves };
}

test('actual tile editor authors sort, NULL placement, typed predicate and topN in one local save', async () => {
  const f = editorFixture(); f.node('Sort & result filters').onclick();
  f.node('Add sort priority').onclick();
  f.node('Sort 1 output').change(reportOutputKey(metric()));
  f.node('Sort 1 direction').change('desc'); f.node('Sort 1 NULL placement').change('first');
  f.node('Top N results').value = '10'; f.node('Top N results').oninput();
  f.node('Add aggregate-result filter').onclick();
  f.node('Result filter 1 value').value = '20'; f.node('Result filter 1 value').oninput();
  // Re-rendering another control keeps every authored value and batch size.
  f.node('Sort 1 direction').change('desc');
  await f.node('Apply & run').onclick();
  assert.equal(f.saves.length, 1); const saved = f.saves[0];
  assert.equal(saved.topN, 10); assert.equal(saved.limit, 2);
  assert.deepEqual(saved.sort, [metric({ direction: 'desc', nulls: 'first' })]);
  assert.deepEqual(saved.aggregateFilters, [metric({ operator: 'gt', value: 20 })]);
});

test('actual tile editor reports invalid topN and retains the draft for correction', async () => {
  const f = editorFixture(); f.node('Sort & result filters').onclick();
  const input = f.node('Top N results'); input.value = '2.5'; input.oninput();
  await f.node('Apply & run').onclick(); assert.equal(f.saves.length, 0);
  input.value = '3'; input.oninput(); await f.node('Apply & run').onclick();
  assert.equal(f.saves[0].topN, 3);
});


for (const [text, value] of [['9007199254740991', 9007199254740991], ['-9007199254740991', -9007199254740991], ['0.1', 0.1], ['-12.3400', -12.34], ['1e3', 1000], ['-1.25e-3', -0.00125], ['.50', 0.5], ['', null]]) {
  test(`numeric authoring accepts a lossless decimal round trip for ${text || 'blank'}`, () => {
    assert.equal(numericFilterValue(text), value);
  });
}

for (const text of ['9007199254740993', '-9007199254740993', '9007199254740992', '0.10000000000000001', '-1.0000000000000001', '9.007199254740993e15', '1e-400', '1e400', 'NaN', 'Infinity', '0x10', 'not a number']) {
  test(`actual editor retains ${text} for correction and never saves a rounded threshold`, async () => {
    const f = editorFixture(); f.node('Sort & result filters').onclick(); f.node('Add aggregate-result filter').onclick();
    const input = f.node('Result filter 1 value');
    assert.equal(input.attrs.type, 'text'); assert.equal(input.attrs.inputmode, 'decimal');
    input.value = text; input.oninput();
    // Another render must preserve the original token rather than a rounded Number.
    f.node('Result filter 1 operator').change('gte');
    assert.equal(String(f.node('Result filter 1 value').value), text);
    await f.node('Apply & run').onclick(); assert.equal(f.saves.length, 0);
    const corrected = f.node('Result filter 1 value'); corrected.value = '-0.125'; corrected.oninput();
    await f.node('Apply & run').onclick(); assert.equal(f.saves[0].aggregateFilters[0].value, -0.125);
  });
}

test('an explicit NULL predicate can replace an invalid numeric draft without numeric coercion', async () => {
  const f = editorFixture(); f.node('Sort & result filters').onclick(); f.node('Add aggregate-result filter').onclick();
  const input = f.node('Result filter 1 value'); input.value = '9007199254740993'; input.oninput();
  f.node('Result filter 1 operator').change('is_null'); await f.node('Apply & run').onclick();
  assert.equal(f.saves[0].aggregateFilters[0].operator, 'is_null'); assert.equal(f.saves[0].aggregateFilters[0].value, null);
});

test('Top N also rejects a fractional token rounded into an integer and retains it for correction', async () => {
  const f = editorFixture(); f.node('Sort & result filters').onclick();
  f.node('Top N results').value = '1.0000000000000001'; f.node('Top N results').oninput();
  await f.node('Apply & run').onclick(); assert.equal(f.saves.length, 0);
  f.node('Add sort priority').onclick(); assert.equal(f.node('Top N results').value, '1.0000000000000001');
});

for (const [type, value, valid] of [
  ['time', '09:00:00', true], ['time(3) without time zone', '09:00:00.1234', true],
  ['time without time zone', '090000', true], ['time', '24:00:00', true],
  ['time with time zone', '09:00:00+02:00', true], ['timetz(6)', '09:00:00.123456-05:30', true],
  ['timetz', '09:00Z', true], ['time with time zone', '090000+0730', true],
  ['time', '09:00:00Z', false], ['timetz', '09:00:00', false], ['time', '24:01:00', false],
  ['time', '09:60:00', false], ['time', '09:00:60', false], ['time', '09:0000', false],
  ['time', '09:00:00.1234567', false], ['timetz', '09:00:00+16:00', false],
  ['timetz', '09:00:00+01:99', false], ['timetz', '09:00:00+02:3000', false],
  ['time', 'not a time', false],
]) {
  test(`time predicate authoring validates ${type} ${value}`, () => {
    assert.equal(validTimeFilterValue(value, type), valid);
    const tile = reportTile(); tile.kind = 'aggregate'; tile.measures = [{ table: 'person', column: 'salary', aggregate: 'min' }];
    tile.aggregateFilters = [{ table: 'person', column: 'salary', aggregate: 'min', kind: 'value', operator: 'gte', value }];
    const schema = { tables: [{ name: 'people', columns: [{ name: 'name', dataType: 'text' }, { name: 'salary', dataType: type }] }] };
    assert.equal(reportControlErrors(tile, model, schema).length === 0, valid);
    assert(aggregateChoices(model.definition, schema, tile.measures[0]).some(([aggregate]) => aggregate === 'min'));
  });
}

test('actual editor persists independent pivot axes, explicit measures and bounded total options', async () => {
  const f = editorFixture({ tile: { ...reportTile(), kind: 'pivot', dimensions: [], rowDimensions: [{ table: 'person', column: 'name', aggregate: 'none' }], columnDimensions: [{ table: 'person', column: 'salary', aggregate: 'none' }] }, pivot: true });
  const cap = f.node('Maximum pivot column groups'); cap.value = '4'; cap.oninput();
  const subtotal = f.node('Row subtotals'); subtotal.checked = false; subtotal.onchange();
  const aggregation = f.node('Measure 1 aggregation'); assert.ok(aggregation.choices.every(([value]) => value !== 'none'));
  aggregation.change('avg'); await f.node('Apply & run').onclick();
  assert.equal(f.saves[0].kind, 'pivot'); assert.equal(f.saves[0].rowDimensions[0].column, 'name');
  assert.equal(f.saves[0].columnDimensions[0].column, 'salary'); assert.equal(f.saves[0].measures[0].aggregate, 'avg');
  assert.equal(f.saves[0].pivotTotals.maximumColumns, 4); assert.equal(f.saves[0].pivotTotals.rowSubtotals, false);
});

function summaryFixture(type = 'numeric') {
  const sourceModel = { definition: { root: 'owner', nodes: [{ id: 'owner', table: 'owners', label: 'Owners' },
    { id: 'summary', table: 'owners', label: 'Per owner', derivation: { kind: 'aggregate', source: 'owner', groupBy: ['id'], outputs: [{ id: 'value', label: 'Summary', operation: type === 'numeric' ? 'avg' : 'max', column: 'value' }] } }], edges: [], scopes: [] } };
  const schema = { tables: [{ name: 'owners', primaryKey: ['id'], columns: [{ name: 'id', dataType: 'integer' }, { name: 'region', dataType: 'text' }, { name: 'category', dataType: 'text' }, { name: 'value', dataType: type }] }], relationships: [] };
  const f = column => ({ table: 'owner', column, aggregate: 'none' });
  const tile = { id: 'pivot', title: 'Per-owner values', kind: 'pivot', dimensions: [], rowDimensions: [f('region')], columnDimensions: [f('category')], measures: [], detailFields: [f('id')], reportFilters: [], selections: {}, limit: 100 };
  return { sourceModel, schema, tile, pivot: true };
}

test('actual editor requires deliberate numeric per-owner pivot aggregation, saves and reopens without rewriting it', async () => {
  const config = summaryFixture(), f = editorFixture(config);
  const choices = fieldChoices(config.sourceModel, config.schema, '', true), index = choices.findIndex(field => field.table === 'summary' && field.column === 'value');
  assert.ok(index >= 0); f.node('Add measures').change(String(index));
  let selector = f.node('Measure 1 aggregation');
  assert.ok(selector.choices.some(([value]) => value === 'avg'));
  assert.ok(selector.choices.some(([value, label]) => value === 'avg' && /per-owner/.test(label)));
  assert.equal(selector.selected, 'none');
  await f.node('Apply & run').onclick(); assert.equal(f.saves.length, 0);
  selector.change('avg'); await f.node('Apply & run').onclick();
  assert.equal(f.saves[0].measures[0].aggregate, 'avg');
  const reopened = editorFixture({ ...config, tile: f.saves[0] });
  assert.equal(reopened.node('Measure 1 aggregation').selected, 'avg');
  reopened.node('Measure 1 aggregation').change('sum'); await reopened.node('Apply & run').onclick();
  assert.equal(reopened.saves[0].measures[0].aggregate, 'sum');
});

for (const type of ['date', 'timestamp with time zone', 'text']) {
  test(`actual editor offers only compatible explicit ${type} summary extrema/count and preserves nonpivot Summary value`, async () => {
    const config = summaryFixture(type), f = editorFixture(config);
    const choices = fieldChoices(config.sourceModel, config.schema, '', true), index = choices.findIndex(field => field.table === 'summary' && field.column === 'value');
    f.node('Add measures').change(String(index));
    const selector = f.node('Measure 1 aggregation');
    assert.ok(selector.choices.some(([value]) => value === 'min'));
    assert.ok(selector.choices.some(([value]) => value === 'count_distinct'));
    assert.ok(selector.choices.every(([value]) => !['sum', 'avg'].includes(value)));
    selector.change('max'); await f.node('Apply & run').onclick(); assert.equal(f.saves[0].measures[0].aggregate, 'max');
    const ordinary = { ...f.saves[0], kind: 'aggregate', dimensions: [...config.tile.rowDimensions, ...config.tile.columnDimensions], rowDimensions: [], columnDimensions: [], measures: [{ table: 'summary', column: 'value', aggregate: 'none' }] };
    const legacy = editorFixture({ ...config, tile: ordinary });
    assert.deepEqual(Array.from(legacy.node('Measure 1 aggregation').choices, pair => Array.from(pair)), [['none', 'Summary value']]);
    await legacy.node('Apply & run').onclick(); assert.equal(legacy.saves[0].measures[0].aggregate, 'none');
  });
}

test('actual pivot editor retains an incompatible saved summary aggregation until explicitly repaired', async () => {
  const config = summaryFixture('date');
  config.tile.measures = [{ table: 'summary', column: 'value', aggregate: 'avg' }];
  const f = editorFixture(config);
  await f.node('Apply & run').onclick(); assert.equal(f.saves.length, 0);
  assert.equal(f.node('Measure 1 aggregation').selected, 'avg');
  f.node('Measure 1 aggregation').change('min'); await f.node('Apply & run').onclick();
  assert.equal(f.saves[0].measures[0].aggregate, 'min');
  assert.equal(config.tile.measures[0].aggregate, 'avg');
});

test('actual tile editor saves a named calculated measure through typed operand controls',async()=>{
  const original=reportTile();const f=editorFixture({tile:original});
  f.node('Add calculated measure').onclick();
  const name=f.node('Calculation 1 name');name.value='Salary ratio';name.oninput();
  f.node('Calculation Left / numerator').change(reportOutputKey({table:'person',column:'salary',aggregate:'sum',kind:'value'}));
  const value=f.node('Right / denominator exact numeric value');value.value='0.100000000000000000001';value.oninput();
  await f.node('Apply & run').onclick();
  assert.equal(f.saves.length,1);
  assert.equal(f.saves[0].calculations[0].label,'Salary ratio');
  assert.deepEqual(f.saves[0].calculations[0].left.output,{table:'person',column:'salary',aggregate:'sum',kind:'value'});
  assert.equal(f.saves[0].calculations[0].right.constant,'0.100000000000000000001');
  assert.equal(original.calculations,undefined);
});
test('KPI editor retains exact baseline input and explicit favorable direction',async()=>{
  const original={...reportTile(),kind:'kpi',dimensions:[]};const f=editorFixture({tile:original});
  const toggle=f.node('Compare KPI to target or baseline');toggle.checked=true;toggle.onchange();
  const input=f.node('KPI baseline exact numeric value');input.value='9007199254740993.00001';input.oninput();
  f.node('KPI favorable direction').change('lower');
  await f.node('Apply & run').onclick();
  assert.equal(f.saves.length,1);assert.equal(f.saves[0].kpiComparison.baseline.constant,'9007199254740993.00001');
  assert.equal(f.saves[0].kpiComparison.favorable,'lower');assert.equal(original.kpiComparison,undefined);
});
