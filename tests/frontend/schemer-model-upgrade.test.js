import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { dashboardUpdate, fieldChoices } from '../../src/schemii/schemer/web/dashboard-state.js';
import { modelUpgradeDraft, modelUpgradeIssues, repairUpgradeIssue } from '../../src/schemii/schemer/web/model-upgrade.js';

const field = (column, aggregate = 'none') => ({ table: 'people', column, aggregate });
function data() {
  const scopes = [
    { id: 'required', label: 'Organization', kind: 'required', requirement: 'required', alternatives: [{ id: 'choose', inputs: [{ id: 'value' }], conditions: [] }] },
    { id: 'optional', label: 'Region', kind: 'conditional', requirement: 'optional', alternatives: [{ id: 'choose', inputs: [{ id: 'value' }], conditions: [] }] },
    { id: 'conditional', label: 'Date', kind: 'conditional', requirement: 'required', alternatives: [{ id: 'choose', inputs: [{ id: 'value' }], conditions: [] }] },
  ];
  const model = { id: 'model', revision: 2, definition: { root: 'people', nodes: [{ id: 'people', table: 'person', label: 'People' }], scopes, edges: [] } };
  const catalog = { tables: [{ name: 'person', columns: [{ name: 'id', dataType: 'integer' }, { name: 'name', dataType: 'text' }] }], relationships: [] };
  const tile = { id: 'tile', title: 'People', kind: 'bar', dimensions: [field('name')], measures: [field('id', 'count')], detailFields: [field('id')], reportFilters: [], selections: {}, limit: 100 };
  const dashboard = { id: 'dashboard', name: 'Overview', revision: 7, modelId: 'model', modelRevision: 1, optionalFilters: ['optional'], selections: {}, tiles: [tile] };
  return { model, catalog, dashboard, tile };
}

test('upgrade draft snapshots every configuration part without pruning or mutating saved state', () => {
  const { dashboard, model } = data();
  dashboard.selections.removed = { active: false, values: { old: 'keep for review' } };
  const before = structuredClone(dashboard), draft = modelUpgradeDraft(dashboard, model);
  assert.equal(draft.expectedRevision, 7); assert.equal(draft.modelRevision, 2);
  assert.deepEqual(draft.selections, dashboard.selections);
  draft.tiles[0].title = 'Repair'; draft.optionalFilters.pop(); delete draft.selections.removed;
  assert.deepEqual(dashboard, before);
});

for (const owner of ['dashboard', 'tile']) {
  test(`upgrade preserves ${owner} input values from other valid options`, () => {
    const { dashboard, model, catalog, tile } = data();
    const id = owner === 'dashboard' ? 'optional' : 'conditional';
    model.definition.scopes.find(scope => scope.id === id).alternatives.push({ id: 'other', inputs: [{ id: 'retained' }], conditions: [] });
    (owner === 'dashboard' ? dashboard.selections : tile.selections)[id] = {
      alternativeId: 'choose', values: { value: 'chosen', retained: 'other option' },
    };
    assert.deepEqual(modelUpgradeIssues(modelUpgradeDraft(dashboard, model), model, catalog), []);
  });
}

test('upgrade reviews dimensions, measures, details and report comparison operands against current exposure', () => {
  const { dashboard, model, catalog, tile } = data();
  model.definition.exposedFields = [field('id')];
  tile.detailFields.push(field('gone'));
  tile.measures.push(field('gone', 'sum'));
  tile.reportFilters = [{ label: 'Comparison', conditions: [{ ...field('id'), compareColumn: 'name' }] }];
  const issues = modelUpgradeIssues(modelUpgradeDraft(dashboard, model), model, catalog);
  assert(issues.some(issue => /Dimension:.*no longer exposed/.test(issue.message)));
  assert(issues.some(issue => /Measure:.*unavailable/.test(issue.message)));
  assert(issues.some(issue => /Detail column:.*unavailable/.test(issue.message)));
  assert(issues.some(issue => /Comparison:.*no longer exposed/.test(issue.message)));
  assert.deepEqual(fieldChoices(model, catalog).map(item => item.column), ['id']);
  model.definition.exposedFields.push(field('gone'));
  assert.deepEqual(fieldChoices(model, catalog).map(item => item.column), ['id']);
});

test('removed model aliases are repairable locally, while missing physical sources direct authors to Schemoo', () => {
  const { dashboard, model, catalog, tile } = data();
  tile.dimensions[0].table = 'removed';
  let issues = modelUpgradeIssues(modelUpgradeDraft(dashboard, model), model, catalog);
  assert(issues.some(issue => /source was removed/.test(issue.message)));
  catalog.tables = [];
  issues = modelUpgradeIssues(modelUpgradeDraft(dashboard, model), model, catalog);
  assert(issues.some(issue => issue.action === 'repairModel' && /Schemoo/.test(issue.message)));
  dashboard.tiles = [];
  assert.deepEqual(modelUpgradeIssues(modelUpgradeDraft(dashboard, model), model, catalog), []);
});

for (const owner of ['dashboard', 'tile']) {
  test(`inactive ${owner} choices still review removed scopes, alternatives and inputs`, () => {
    const { dashboard, model, catalog, tile } = data();
    const selections = owner === 'dashboard' ? dashboard.selections : tile.selections;
    const id = owner === 'dashboard' ? 'optional' : 'conditional';
    selections[id] = { active: false, alternativeId: 'removed', values: {} };
    const draft = modelUpgradeDraft(dashboard, model);
    assert(modelUpgradeIssues(draft, model, catalog).some(issue => /option was removed/.test(issue.message)));
    const selected = owner === 'dashboard' ? draft.selections : draft.tiles[0].selections;
    selected[id] = { active: false, alternativeId: 'choose', values: { old: 'x' } };
    assert(modelUpgradeIssues(draft, model, catalog).some(issue => /input was removed/.test(issue.message)));
    selected[id] = { active: false, values: {} };
    assert.deepEqual(modelUpgradeIssues(draft, model, catalog), []);
    selected.removed = { active: false };
    const issue = modelUpgradeIssues(draft, model, catalog).find(issue => issue.scopeId === 'removed');
    assert(issue); repairUpgradeIssue(draft, issue);
    assert(!Object.hasOwn(selected, 'removed'));
  });
}

test('filter removals are explicit draft actions and preserve untouched configuration', () => {
  const { dashboard, model, catalog, tile } = data();
  dashboard.optionalFilters.push('removed'); dashboard.selections.removed = { active: false };
  tile.reportFilters.push({ label: 'Old restriction', conditions: [field('gone')] });
  const draft = modelUpgradeDraft(dashboard, model);
  let issues = modelUpgradeIssues(draft, model, catalog);
  repairUpgradeIssue(draft, issues.find(issue => issue.action === 'removeOptional'));
  issues = modelUpgradeIssues(draft, model, catalog);
  repairUpgradeIssue(draft, issues.find(issue => issue.action === 'removeFilter'));
  assert.deepEqual(modelUpgradeIssues(draft, model, catalog), []);
  assert.equal(dashboard.optionalFilters.length, 2); assert.equal(tile.reportFilters.length, 1);
  assert.deepEqual(draft.optionalFilters, ['optional']);
});

test('changed measure, comparison and time types receive actionable compatibility diagnostics', () => {
  const { dashboard, model, catalog, tile } = data();
  tile.measures = [field('name', 'min')];
  tile.timeAnalysis = { table: 'people', column: 'name', timezone: 'UTC', granularity: 'month' };
  tile.reportFilters = [{ label: 'Comparison', conditions: [{ ...field('id'), operator: 'eq', compareColumn: 'name' }] }];
  const draft = modelUpgradeDraft(dashboard, model), issues = modelUpgradeIssues(draft, model, catalog);
  assert(issues.some(issue => /aggregation is no longer compatible/.test(issue.message)));
  assert(issues.some(issue => /compatible comparison column/.test(issue.message)));
  const time = issues.find(issue => issue.action === 'clearTime');
  assert(time); repairUpgradeIssue(draft, time); assert.equal(draft.tiles[0].timeAnalysis, null);
  assert(tile.timeAnalysis);
});

const source = readFileSync(new URL('../../src/schemii/schemer/web/studio.js', import.meta.url), 'utf8');
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function studio() {
  const { dashboard, model, catalog } = data(), nodes = [], ids = new Map(), writes = [], renders = [], notices = [];
  function element(tag, options = {}, children = []) {
    const node = { tag, ...options, textContent: options.text || '', children: [...children], open: false,
      append(...items) { this.children.push(...items); }, replaceChildren(...items) { this.children = items; },
      setAttribute() {}, classList: { add() {}, remove() {} }, focus() {},
      showModal() { this.open = true; }, close() { this.open = false; this.onclose?.(); }, remove() { this.removed = true; },
    };
    nodes.push(node); return node;
  }
  const events = new Map(), document = { getElementById(id) { if (!ids.has(id)) ids.set(id, element('div')); return ids.get(id); }, querySelector() { return element('div'); }, body: element('body') };
  Object.defineProperty(document.getElementById('notice'), 'textContent', { set: value => notices.push(value) });
  const initialModel = { ...model, revision: 1 }, initialCatalog = { old: true };
  const state = { get: async () => ({ model, catalog, permissions: { edit: true, export: true, drill: true } }), put: async body => ({ ...dashboard, ...body, revision: 8 }), stop: async () => {} };
  const context = vm.createContext({ document, window: { confirm: () => true, addEventListener: (name, fn) => events.set(name, fn) }, location: { search: '' }, structuredClone, URLSearchParams,
    element, dashboardUpdate, modelUpgradeDraft, modelUpgradeIssues, repairUpgradeIssue,
    currentAccount: () => new Promise(() => {}), createProductAssistant: () => ({}), CacheBudget: class {}, initializeUi() {}, installProductNavigation() {},
    createIconButton: ({ label }) => element('button', { text: label }),
    requestJson: async (url, options) => { if (!options) return state.get(); writes.push({ url, ...structuredClone(options) }); return state.put(options.body); },
    original: dashboard, initialModel, initialCatalog, state, renders,
    openTileEditor: options => { state.editor = options; return { close() {} }; },
  });
  vm.runInContext(source.replace(/^import .*;\n/gm, ''), context);
  vm.runInContext(`dashboard = original; model = initialModel; catalog = initialCatalog; library = [original];
    renderLibrary = () => renders.push('library'); renderHeading = () => renders.push('heading');
    renderModelUpdate = () => renders.push('update'); renderSlicers = () => renders.push('slicers'); renderTiles = () => renders.push('tiles');
    runAll = async () => renders.push('run'); stopRuns = () => state.stop();`, context);
  const button = label => nodes.findLast(node => node.textContent === label);
  const dialog = () => nodes.findLast(node => node.tag === 'dialog');
  const snapshot = name => JSON.parse(vm.runInContext(`JSON.stringify(${name})`, context));
  return { context, dashboard, model, catalog, state, writes, renders, notices, nodes, events, button, dialog, snapshot,
    open: () => vm.runInContext('updateDashboardModel()', context), save: () => button('Save repairs & upgrade').onclick(),
    replace: () => vm.runInContext('dashboard = { ...dashboard, id: "other", revision: 1 };', context) };
}

test('review loads a separate target context and cancel sends no save or active-context mutation', async () => {
  const f = studio(); await f.open();
  assert.equal(f.snapshot('model').revision, 1); assert.deepEqual(f.snapshot('catalog'), { old: true });
  assert.equal(f.writes.length, 0); f.button('Cancel').onclick();
  assert.equal(f.dialog().open, false); assert.deepEqual(f.snapshot('dashboard'), f.dashboard);
});

test('tile repair and model revision are saved together once using the original dashboard CAS revision', async () => {
  const f = studio(); f.model.definition.exposedFields = [field('id')]; await f.open();
  assert.equal(f.button('Save repairs & upgrade').disabled, true);
  f.button('Edit People').onclick();
  assert.equal(f.state.editor.saveLabel, 'Apply repair');
  const edited = structuredClone(f.dashboard.tiles[0]); edited.dimensions = [field('id')];
  f.state.editor.onSave(edited);
  assert.equal(f.writes.length, 0); assert.deepEqual(f.snapshot('dashboard'), f.dashboard);
  await f.save(); assert.equal(f.writes.length, 1);
  assert.equal(f.writes[0].body.expectedRevision, 7); assert.equal(f.writes[0].body.modelRevision, 2);
  assert.deepEqual(f.writes[0].body.tiles[0].dimensions, [field('id')]);
  assert.equal(f.snapshot('dashboard').revision, 8); assert.equal(f.snapshot('model').revision, 2);
  assert.equal(f.dialog().open, false);
});

test('removing an incompatible tile can save an empty dashboard without intermediate writes', async () => {
  const f = studio(); await f.open(); f.button('Remove People').onclick();
  assert.equal(f.writes.length, 0); await f.save();
  assert.deepEqual(f.writes[0].body.tiles, []); assert.equal(f.dashboard.tiles.length, 1);
});

for (const code of ['server_error', 'dashboard_revision_conflict', 'model_revision_conflict', 'network_error']) {
  test(`${code} preserves saved dashboard, active context and repair draft`, async () => {
    const f = studio(); await f.open(); f.button('Remove People').onclick();
    f.state.put = async () => { throw Object.assign(new Error('Could not save'), { code }); };
    await f.save();
    assert.deepEqual(f.snapshot('dashboard'), f.dashboard); assert.equal(f.snapshot('model').revision, 1);
    assert.deepEqual(f.snapshot('catalog'), { old: true }); assert.equal(f.dialog().open, true);
    assert.deepEqual(f.writes[0].body.tiles, []); assert.deepEqual(f.renders, []);
    assert.equal(f.button('Save repairs & upgrade').disabled, code !== 'server_error');
  });
}

test('cancel while target context is loading ignores its late response', async () => {
  const f = studio(), response = deferred(); f.state.get = () => response.promise;
  const opening = f.open(); f.button('Cancel').onclick();
  response.resolve({ model: f.model, catalog: f.catalog, permissions: { edit: true } }); await opening;
  assert.equal(f.dialog().open, false); assert.equal(f.writes.length, 0); assert.equal(f.snapshot('model').revision, 1);
});

test('loading failure exposes recovery without changing the saved dashboard', async () => {
  const f = studio(); f.state.get = async () => { throw new Error('Catalog unavailable'); }; await f.open();
  assert.equal(f.button('Retry loading model').hidden, false);
  assert.equal(f.button('Save repairs & upgrade').disabled, true);
  f.state.get = async () => ({ model: f.model, catalog: f.catalog, permissions: { edit: true } });
  await f.button('Retry loading model').onclick();
  assert.equal(f.button('Save repairs & upgrade').disabled, false); assert.equal(f.writes.length, 0);
});

test('lost editing access cannot load a writable repair draft', async () => {
  const f = studio(); f.state.get = async () => ({ model: f.model, catalog: f.catalog, permissions: { edit: false } });
  await f.open(); assert.equal(f.button('Save repairs & upgrade').disabled, true);
  assert.equal(f.writes.length, 0); assert.equal(f.snapshot('model').revision, 1);
});

test('same-dashboard revision replacement invalidates an open repair draft before save', async () => {
  const f = studio(); await f.open();
  vm.runInContext('dashboard = { ...dashboard, revision: 9 };', f.context);
  await f.save(); assert.equal(f.writes.length, 0); assert.equal(f.snapshot('dashboard').revision, 9);
});

test('saving locks cancel, Escape and repeated submit until the atomic write settles', async () => {
  const f = studio(), response = deferred(); await f.open(); f.state.put = () => response.promise;
  const pending = f.save(); await new Promise(resolve => setImmediate(resolve));
  f.button('Cancel').onclick(); f.button('Close model upgrade review').onclick({ stopPropagation() {} });
  let prevented = false; f.dialog().oncancel({ preventDefault() { prevented = true; } });
  await f.save(); assert.equal(f.writes.length, 1); assert.equal(f.dialog().open, true); assert(prevented);
  response.resolve({ ...f.dashboard, revision: 8, modelRevision: 2 }); await pending;
  assert.equal(f.dialog().open, false);
});

test('stream shutdown failure preserves repairs and never sends a save', async () => {
  const f = studio(); await f.open(); f.button('Remove People').onclick();
  f.state.stop = async () => { throw new Error('Could not stop existing stream'); };
  await f.save(); assert.equal(f.writes.length, 0); assert.equal(f.dialog().open, true);
  assert.deepEqual(f.snapshot('dashboard'), f.dashboard); assert.equal(f.button('Save repairs & upgrade').disabled, false);
});

for (const phase of ['shutdown', 'request']) {
  test(`navigation during ${phase} cannot overwrite or redraw the replacement dashboard`, async () => {
    const f = studio(), response = deferred(); await f.open();
    if (phase === 'shutdown') f.state.stop = () => response.promise;
    else f.state.put = () => response.promise;
    const pending = f.save(); await new Promise(resolve => setImmediate(resolve)); f.replace();
    response.resolve({ ...f.dashboard, revision: 8 }); await pending;
    assert.equal(f.snapshot('dashboard').id, 'other'); assert.equal(f.snapshot('model').revision, 1);
    assert.deepEqual(f.renders, []); assert.equal(f.writes.length, phase === 'shutdown' ? 0 : 1);
  });
}

test('pagehide disposes an upgrade review and ignores pending context responses', async () => {
  const f = studio(), response = deferred(); f.state.get = () => response.promise;
  const opening = f.open(); f.events.get('pagehide')();
  response.resolve({ model: f.model, catalog: f.catalog, permissions: { edit: true } }); await opening;
  assert.equal(f.dialog().open, false); assert.equal(f.snapshot('model').revision, 1);
});


test('upgrade preserves stale report sort and aggregate-filter references for explicit tile repair', () => {
  const { dashboard, model, catalog, tile } = data();
  tile.sort = [{ table: 'people', column: 'id', aggregate: 'count', kind: 'running_total', direction: 'desc', nulls: 'last' }];
  tile.aggregateFilters = [{ table: 'people', column: 'gone', aggregate: 'sum', operator: 'gt', value: 10 }];
  const before = structuredClone(tile), draft = modelUpgradeDraft(dashboard, model);
  const issues = modelUpgradeIssues(draft, model, catalog);
  assert(issues.some(issue => /Sort 1/.test(issue.message)));
  assert(issues.some(issue => /Result filter 1/.test(issue.message)));
  assert.deepEqual(draft.tiles[0].sort, before.sort); assert.deepEqual(draft.tiles[0].aggregateFilters, before.aggregateFilters);
});

test('stale pivot upgrade reviews both axes and preserves every grouping and total until explicit repair', () => {
  const { dashboard, model, catalog, tile } = data();
  Object.assign(tile, { kind: 'pivot', dimensions: [], rowDimensions: [field('name')], columnDimensions: [field('gone')], pivotTotals: { maximumColumns: 5, grandTotal: false } });
  const draft = modelUpgradeDraft(dashboard, model), before = structuredClone(tile);
  assert.ok(modelUpgradeIssues(draft, model, catalog).some(issue => /Pivot column dimension:.*unavailable/.test(issue.message)));
  assert.deepEqual(tile, before); assert.equal(draft.tiles[0].pivotTotals.maximumColumns, 5);
  draft.tiles[0].columnDimensions = [field('id')];
  assert.deepEqual(modelUpgradeIssues(draft, model, catalog), []);
  assert.equal(tile.columnDimensions[0].column, 'gone');
});

for (const aggregate of ['avg', 'sum']) {
  test(`saved explicit per-owner pivot ${aggregate} survives atomic upgrade while stale types require repair`, () => {
    const { model, catalog, dashboard, tile } = data();
    model.definition.nodes.push({ id: 'summary', table: 'person', label: 'Per owner', derivation: { kind: 'aggregate', source: 'people', groupBy: ['id'], outputs: [{ id: 'value', label: 'Owner maximum', operation: 'max', column: 'id' }] } });
    Object.assign(tile, { kind: 'pivot', dimensions: [], rowDimensions: [field('name')], columnDimensions: [field('id')], measures: [{ table: 'summary', column: 'value', aggregate }] });
    const draft = modelUpgradeDraft(dashboard, model);
    assert.deepEqual(modelUpgradeIssues(draft, model, catalog), []);
    assert.equal(draft.tiles[0].measures[0].aggregate, aggregate);
    catalog.tables[0].columns.find(column => column.name === 'id').dataType = 'date';
    assert.ok(modelUpgradeIssues(draft, model, catalog).some(issue => /compatible.*aggregation|aggregation.*compatible/.test(issue.message)));
    draft.tiles[0].measures[0].aggregate = 'max';
    assert.deepEqual(modelUpgradeIssues(draft, model, catalog), []);
    assert.equal(tile.measures[0].aggregate, aggregate); // Repair is local until the existing atomic save.
    Object.assign(draft.tiles[0], { kind: 'aggregate', dimensions: [field('name')], rowDimensions: [], columnDimensions: [], measures: [{ table: 'summary', column: 'value', aggregate: 'none' }] });
    assert.deepEqual(modelUpgradeIssues(draft, model, catalog), []);
    assert.equal(draft.tiles[0].measures[0].aggregate, 'none');
  });
}

test('upgrade retains calculation identities and hidden dependencies for explicit repair',()=>{
  const {dashboard,model,catalog,tile}=data();
  tile.hiddenMeasures=[{table:'people',column:'removed',aggregate:'sum'}];
  tile.calculations=[{id:'ratio',label:'Ratio',operation:'divide',left:{output:field('id','count'),scope:'group'},right:{output:tile.hiddenMeasures[0],scope:'source_population'},visible:true}];
  tile.kpiComparison=null;
  const before=structuredClone(dashboard),draft=modelUpgradeDraft(dashboard,model);
  assert.deepEqual(draft.tiles[0].calculations,tile.calculations);
  const issues=modelUpgradeIssues(draft,model,catalog);
  assert.ok(issues.some(issue=>/Hidden measure input.*removed/.test(issue.message)));
  assert.equal(draft.tiles[0].calculations[0].right.output.column,'removed');
  assert.deepEqual(dashboard,before);
});

test('upgrade blocks a stale KPI baseline and preserves it until explicit rebind',()=>{
  const {dashboard,model,catalog,tile}=data();tile.kind='kpi';tile.dimensions=[];
  tile.calculations=[];tile.hiddenMeasures=[];
  tile.kpiComparison={label:'Target',baseline:{output:{table:'@report',column:'deleted',aggregate:'none',kind:'calculated'},scope:'group'},favorable:'higher'};
  const before=structuredClone(dashboard),draft=modelUpgradeDraft(dashboard,model);
  const issues=modelUpgradeIssues(draft,model,catalog);
  assert.ok(issues.some(issue=>/KPI Target:.*removed/.test(issue.message)));
  assert.deepEqual(draft.tiles[0].kpiComparison,tile.kpiComparison);
  assert.deepEqual(dashboard,before);
  draft.tiles[0].kpiComparison.baseline.output={table:'people',column:'id',aggregate:'count',kind:'value'};
  assert.deepEqual(modelUpgradeIssues(draft,model,catalog),[]);
});
