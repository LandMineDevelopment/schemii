import { refreshedSelections, staleModelMessage } from './dashboard-refresh.js';
import { scrollPosition, restoreScroll, appendStreamStatus } from './scroll-results.js';
import { element } from '#common/dom.js';
import { requestJson } from '#common/http.js';
import { currentAccount, canAccessProduct, canAuthor } from '#common/accounts-session.js';
import { initializeUi, createIconButton, createIconElement } from '#common/ui.js';
import { installProductNavigation } from '#common/product-navigation.js';
import { confirmAction } from '#common/confirmation.js';
import { createProductAssistant } from '#common/model-assistant.js';
import { consumeProductContext } from '#common/product-context.js';
import { openPreviewHandoff } from './preview-handoff.js';
import { readExecution } from '#common/query-execution.js';
import { modelSelect, disposeSelects } from '#model/select.js';
import { renderParameterValues } from '#model/filter-controls.js';
import { newTile, dashboardUpdate, TILE_TYPES } from './dashboard-state.js';
import { modelUpgradeDraft, modelUpgradeIssues, repairUpgradeIssue } from './model-upgrade.js';
import { appliedFilterSummary } from './filter-summary.js';
import { ResultCache, DashboardResultGroup, CacheBudget, DrillCacheRegistry, readResultStream, freezeDisplayedResult } from './result-cache.js';
import { renderVisualization, displayedVisualization } from './visualizations.js';
import { openTileEditor } from './tile-editor.js';
import { openExpanded, openSql } from './result-viewer.js';

const $ = id => document.getElementById(id), API = '/api/v1/schemer/dashboards';
let dashboard, model, catalog, library = [], modelList = [], slicerDraft, slicersDirty = false, saving = false, epoch = 0;
let filtersExpanded = false, author = false, modelAuthor = false;
let upgradeReview;
let permissions = { edit: false, export: false, drill: false };
const selectionBody = (source = dashboard) => ({ selections: source.selections });
function renderAccess() {
  for (const id of ['create-dashboard', 'welcome-create']) $(id).hidden = !author || !modelAuthor;
  for (const id of ['rename-dashboard', 'duplicate-dashboard', 'delete-dashboard', 'add-tile', 'manage-dashboard-filters', 'update-dashboard-model', 'review-dashboard-model']) $(id).hidden = !permissions.edit;
  document.querySelector('.model-link').hidden = !modelAuthor;
  if (!author) {
    $('empty-dashboard').querySelector('small').textContent = 'YOUR REPORT LIBRARY';
    $('empty-dashboard').querySelector('h1').textContent = 'Reports shared with you';
    $('empty-dashboard').querySelector('p').textContent = 'Your administrator can assign reports through your roles. Shared dashboards will appear here when access is granted.';
  }
}
const runs = new Map(), tileStates = new Map(), streams = new Map();
let closing = Promise.resolve(), dashboardGroup;
const browserBudget = new CacheBudget();
const scheduledCards = new WeakSet();
const message = text => { $('notice').textContent = text; };
const assistant = createProductAssistant({
  trigger: $('ai-assistant-button'), getSubjectId: () => dashboard?.id || null,
  api: '/api/v1/schemer/ai', productLabel: 'Schemer', subjectLabel: 'dashboard',
  subjectKey: 'dashboardId', subjectQueryKey: 'dashboard_id', revisionKey: 'dashboardRevision',
  title: 'Dashboard assistant', emptyTitle: 'Explore this dashboard',
  emptyDescription: 'Ask about saved tiles and report results, or describe a dashboard change. Available actions follow your dashboard access.',
  examplePrompts: ['Explain this dashboard and its tiles.', 'What do the results in this dashboard show?', 'Suggest a useful new tile for this dashboard.'],
  resultOperations: ['execute_tile', 'drill_tile', 'parameter_values'],
  resultActionLabel: 'Ask about current results',
  resultPrompt: ({ payload }) => payload.operation === 'parameter_values'
    ? `Look up current values for exposed filter ${payload.action?.args?.scopeId || 'this filter'} and explain the choices. The values may have changed since the earlier lookup.`
    : `Rerun tile ${payload.action?.args?.tileId || 'the saved tile'} on this dashboard and explain its current results. The data may have changed since the earlier run.`,
  permissionScopeNote: 'Your dashboard access determines which actions appear here. Assistant settings cannot grant author, export, or drill rights.',
  getAvailableActions: actions => actions.filter(action => (permissions.edit || !action.mutates) && (permissions.export || !action.requiresExport) && (permissions.drill || !action.requiresDrill)).map(action => action.id),
  onSubjectChanged: async id => {
    if (id !== dashboard?.id) return;
    library = (await requestJson(API)).dashboards;
    renderLibrary();
    if (slicersDirty || saving || upgradeReview) return 'The saved dashboard changed. Your current changes are preserved. Finish or cancel them before reloading.';
    if (library.some(item => item.id === id)) {
      await openDashboard(id);
      return 'Dashboard refreshed with the assistant’s saved changes.';
    }
    dashboard = null; model = null; $('dashboard-content').hidden = true;
    $('ai-assistant-button').disabled = true;
    if (library.length) await openDashboard(library[0].id);
    else { $('empty-dashboard').hidden = false; history.replaceState(null, '', '/schemer'); }
    return 'The saved dashboard was deleted. Choose another dashboard to continue.';
  },
});
function icon(name, label, callback, disabled = false) { const button = createIconButton({ icon: name, label, className: 'ui-button' }); button.disabled = disabled; button.onclick = event => { event.stopPropagation(); callback(); }; return button; }
function requiredSelections(source, values = {}) { return Object.fromEntries(source.definition.scopes.filter(s => s.kind === 'required' && s.requirement !== 'optional' && values[s.id]).map(s => [s.id, structuredClone(values[s.id])])); }
function missingSlicers() {
  return slicerDraft.scopes.flatMap(scope => {
    const selection = slicerDraft.selections[scope.id], alternative = scope.alternatives.find(a => a.id === selection?.alternativeId) || scope.alternatives[0];
    if (scope.requirement === 'optional' && selection?.active !== true) return [];
    return (alternative?.inputs || []).filter(input => alternative.conditions.some(c => c.parameterId === input.id)).filter(input => {
      const entered = selection?.values?.[input.id], value = entered === '' || entered == null || Array.isArray(entered) && !entered.length ? input.defaultValue : entered;
      return value === '' || value == null || Array.isArray(value) && !value.length;
    }).map(input => `${scope.label}: ${input.label}`);
  });
}
function stopRuns({ keepCache = false } = {}) {
  for (const [id, state] of tileStates) if (state.loading) tileStates.set(id, { error: 'Query stopped. Refresh to run again.' });
  ++epoch;
  const old = [...streams.values()].flatMap(group => [group.main, ...group.drills.values()]);
  if (!keepCache) { streams.clear(); tileStates.clear(); }
  runs.clear(); $('stop-dashboard').hidden = true;
  closing = Promise.all([closing, ...(dashboardGroup ? [dashboardGroup.close()] : []), ...old.map(cache => keepCache ? cache.close() : cache.dispose())]);
  dashboardGroup = null;
  return closing;
}
async function closeTileStreams(id) {
  const group = streams.get(id); streams.delete(id);
  if (group) await Promise.all([group.main, ...group.drills.values()].map(cache => cache.dispose()));
}
function getStream(tile, selection = null, { refresh = false } = {}) {
  let group = streams.get(tile.id);
  const create = selected => {
    const origin = dashboard;
    const currentGroup = dashboardGroup;
    const cache = new ResultCache({ pageSize: tile.limit, budget: !selected && currentGroup ? currentGroup.budgetFor(tile.id) || browserBudget : browserBudget, start: (onFrame, signal) => !selected && currentGroup ? currentGroup.tile(tile.id, onFrame) : readResultStream(`${API}/${origin.id}/tiles/${encodeURIComponent(tile.id)}/executions/stream`,
      { expectedRevision: origin.revision, selection: selected, ...selectionBody(origin) }, frame => {
        if (frame.type === 'start') onFrame({ ...frame, plan: frame.tiles.find(item => item.tileId === tile.id)?.plan });
        else onFrame(frame);
      }, signal), onChange: () => {
      if (selected || streams.get(tile.id)?.main !== cache) return;
      if (cache.started || cache.error) tileStates.set(tile.id, { result: cache.snapshot() });
      const card = [...$('tile-grid').querySelectorAll('.analytics-tile')].find(card => card.dataset.tileId === tile.id);
      if (card && !scheduledCards.has(card)) {
        scheduledCards.add(card); requestAnimationFrame(() => { scheduledCards.delete(card); if (card.isConnected) renderTile(card, tile); });
      }
    } });
    cache.displayContext = JSON.parse(JSON.stringify({ dashboardId: origin.id, dashboardRevision: origin.revision,
      modelId: origin.modelId, modelRevision: origin.modelRevision, tileId: tile.id,
      selections: origin.selections, pane: selected ? 'drill' : 'main', drillSelection: selected }));
    if (permissions.export) cache.download = () => {
      if (dashboard?.id !== origin.id) { message('Reopen this dashboard before downloading its results.'); return; }
      const form = document.createElement('form'); form.method = 'POST'; form.action = `${API}/${origin.id}/tiles/${encodeURIComponent(tile.id)}/export`; form.target = '_blank';
      const input = document.createElement('input'); input.type = 'hidden'; input.name = 'payload'; input.value = JSON.stringify({ expectedRevision: dashboard.revision, selection: selected, ...selectionBody() });
      form.append(input); document.body.append(form); form.submit(); form.remove();
    };
    return cache;
  };
  if (!group) { group = { main: create(null), drills: new DrillCacheRegistry() }; streams.set(tile.id, group); }
  if (!selection) return group.main;
  const key = JSON.stringify(selection);
  if (refresh) { const previous = group.drills.get(key); group.drills.delete(key); previous?.dispose(); }
  if (!group.drills.has(key)) group.drills.set(key, create(selection));
  return group.drills.get(key);
}

function checkLeave() {
  if (upgradeReview?.dirty() && !window.confirm('Discard the dashboard upgrade repairs?')) return false;
  return !slicersDirty || window.confirm('Discard unapplied slicer changes?');
}
function setSaving(value) {
  saving = value;
  for (const id of ['create-dashboard', 'welcome-create', 'rename-dashboard', 'duplicate-dashboard', 'delete-dashboard', 'manage-dashboard-filters', 'add-tile', 'apply-slicers', 'refresh-dashboard', 'update-dashboard-model']) $(id).disabled = value;
}
async function persistDashboard(original, source, patch) {
  const optionalFilters = patch.optionalFilters || original.optionalFilters || [];
  const allowed = source ? new Set([...optionalFilters, ...source.definition.scopes.filter(scope => scope.kind === 'required' && scope.requirement !== 'optional').map(scope => scope.id)]) : null;
  const selections = Object.fromEntries(Object.entries(patch.selections || original.selections).filter(([id]) => !allowed || allowed.has(id)));
  const saved = await requestJson(`${API}/${original.id}`, { method: 'PUT', body: dashboardUpdate(original, { ...patch, optionalFilters, selections }) });
  if (dashboard !== original) return saved;
  dashboard = saved; library = library.map(d => d.id === saved.id ? saved : d); renderLibrary(); renderHeading(); return saved;
}
async function update(patch) {
  if (saving) throw new Error('A dashboard change is still saving. Try again shortly.');
  const original = dashboard, source = model;
  setSaving(true);
  try {
    return await persistDashboard(original, source, patch);
  } finally { setSaving(false); }
}
function renderLibrary() {
  $('dashboard-list').replaceChildren(...library.map(item => {
    const button = element('button', { type: 'button', className: 'dashboard-link', attrs: { 'aria-current': item.id === dashboard?.id ? 'page' : undefined } }, [element('strong', { text: item.name }), element('small', { text: `${item.tiles.length} tile${item.tiles.length === 1 ? '' : 's'}` })]);
    button.onclick = () => { if (!saving && checkLeave()) void openDashboard(item.id); }; return button;
  }));
  if (!library.length) $('dashboard-list').append(element('p', { className: 'hint', text: author ? 'Your saved dashboards appear here.' : 'No reports have been shared with you yet.' }));
}
function renderHeading() {
  renderAccess();
  $('dashboard-name').textContent = dashboard.name;
  $('dashboard-source').textContent = model ? `${model.name} · ${model.database}.${model.namespace}` : 'Model unavailable';
  $('dashboard-status').textContent = `${dashboard.tiles.length} analytics tiles · ${model && model.revision !== dashboard.modelRevision ? `model v${dashboard.modelRevision} needs updating` : 'saved on server'}`;
}
function modelNeedsUpdate() { return !!model && model.revision !== dashboard?.modelRevision; }
function renderModelUpdate() {
  const stale = modelNeedsUpdate();
  $('model-update').hidden = !stale || !permissions.edit;
  if (!stale) return;
  $('model-update-message').textContent = `This dashboard uses ${model.name} v${dashboard.modelRevision}; v${model.revision} is available. Updating validates every tile and slicer before saving.`;
  $('review-dashboard-model').href = `/schemoo?model=${encodeURIComponent(model.id)}`;
  $('update-dashboard-model').textContent = `Update to v${model.revision}`;
}
function renderSlicers() {
  disposeSelects($('slicers'));
  const configured = new Set(dashboard.optionalFilters || []);
  slicerDraft = { ...structuredClone(model.definition), fields: [], scopes: model.definition.scopes.filter(scope =>
    scope.kind === 'required' && scope.requirement !== 'optional' || scope.requirement === 'optional' && configured.has(scope.id)), selections: structuredClone(dashboard.selections) };
  renderParameterValues($('slicers'), { draft: slicerDraft, catalog, force: true, optionalSelectionLabel: 'Activate', onLoadDomain: loadDomainOptions, onChange: () => {
    slicersDirty = true; stopRuns(); tileStates.clear(); renderTiles();
    $('slicer-status').textContent = 'Filters changed. Apply to update every tile.'; $('apply-slicers').classList.add('primary');
    renderFilterDisclosure();
  } });
  $('apply-slicers').classList.remove('primary');
  const missing = missingSlicers(); $('slicer-status').textContent = missing.length ? `Choose ${missing.join(', ')} to load data.` : '';
  if (missing.length) filtersExpanded = true;
  $('apply-slicers').hidden = !slicerDraft.scopes.length;
  renderFilterDisclosure();
}
function renderFilterDisclosure() {
  $('slicer-panel').hidden = !filtersExpanded;
  $('toggle-dashboard-filters').setAttribute('aria-expanded', String(filtersExpanded));
  $('toggle-dashboard-filters').title = filtersExpanded ? 'Collapse dashboard filters' : 'Expand dashboard filters';
  $('filter-summary').hidden = filtersExpanded;
  const applied = appliedFilterSummary(slicerDraft.scopes, dashboard.selections);
  $('filter-summary').replaceChildren(element('span', { className: 'hint', text: applied.length ? 'Applied' : 'No filters applied' }),
    ...applied.slice(0, 3).map(text => element('span', { className: 'filter-summary-chip', text, attrs: { title: text } })));
  if (applied.length > 3) $('filter-summary').append(element('span', { className: 'hint', text: `+${applied.length - 3} more`, attrs: { title: applied.slice(3).join('\n') } }));
  if (slicersDirty) $('filter-summary').append(element('span', { className: 'filter-summary-pending', text: 'Unapplied changes' }));
}
async function loadDomainOptions(context, origin = model) {
  const authoring = !!context.draft, domain = context.domain || context.input?.domain;
  const binding = authoring ? { definition: origin.definition, domain } : { scopeId: context.scope.id, alternativeId: context.alternative.id, parameterId: context.input.id };
  const response = await requestJson(permissions.edit && modelAuthor ? `/api/v1/schemoo/models/${origin.id}/${authoring ? 'domain-values' : 'parameter-values'}` : `${API}/${dashboard.id}/parameter-values`, { method: 'POST', body: { expectedRevision: origin.revision, ...binding, search: context.search, consoleId: `con_${crypto.randomUUID().replaceAll('-', '')}` } });
  const result = Array.isArray(response.rows) ? response : await readExecution(response), hasLabel = domain?.labelColumn && domain.labelColumn !== domain.column;
  return result.rows.filter(row => row[0] !== null).map(row => ({ value: row[0], label: String(hasLabel ? row[1] ?? row[0] : row[0]), description: hasLabel ? String(row[0]) : '' }));
}
function tileUrl(tile, suffix) { return `${API}/${dashboard.id}/tiles/${encodeURIComponent(tile.id)}/${suffix}`; }

function renderTile(card, tile) {
  const state = tileStates.get(tile.id), cache = streams.get(tile.id)?.main, body = card.querySelector('.tile-body'), status = card.querySelector('.tile-status');
  const explain = card.querySelector('[data-explain-displayed]'); if (explain) explain.disabled = !cache?.snapshotAt;
  if (state?.loading) { body.replaceChildren(element('p', { className: 'empty-state', text: 'Loading…' })); status.textContent = 'Running…'; }
  else if (state?.error) {
    body.replaceChildren(element('p', { className: 'tile-error', text: state.error }));
    const retry = element('button', { type: 'button', className: 'ui-button', text: 'Retry' }); retry.onclick = event => { event.stopPropagation(); void runTile(tile); }; body.append(retry); status.textContent = 'Could not load';
  } else if (state?.result) {
    const position = scrollPosition(body);
    const chart = !['detail', 'aggregate'].includes(tile.kind);
    const data = state.result;
    renderVisualization(body, tile, data, { compact: true, modelId: dashboard.modelId });
    appendStreamStatus(body, data);
    restoreScroll(body, position);
    status.textContent = `${data.rows.length} ${tile.kind === 'detail' ? 'rows' : 'groups'} cached${cache?.loading ? ' · loading…' : data.limitReached ? ['dashboard_row_limit', 'dashboard_byte_limit', 'browser_dashboard_budget', 'browser_budget'].includes(data.reason) ? ' · dashboard preview capacity reached' : ' · preview limit' : data.error ? ' · interrupted' : ' · complete'}`;
  } else { body.replaceChildren(element('p', { className: 'empty-state', text: slicersDirty ? 'Apply dashboard slicers to load this tile.' : missingSlicers().length ? 'Set the dashboard slicers to load this tile.' : 'Open or refresh to load this tile.' })); status.textContent = 'Not run'; }
}
function renderTiles() {
  $('tile-grid').replaceChildren();
  for (const [index, tile] of dashboard.tiles.entries()) {
    const card = element('article', { className: 'analytics-tile', dataset: { tileId: tile.id }, attrs: { tabindex: 0, 'aria-label': `${tile.title}. Expand tile` } });
    const actions = element('div', { className: 'tile-actions' }, [icon('edit', `Edit ${tile.title}`, () => editTile(tile)), icon('sql', `SQL for ${tile.title}`, () => showTileSql(tile))]);
    const menu = element('details', { className: 'tile-menu' });
    const trigger = element('summary', { attrs: { 'aria-label': `More actions for ${tile.title}`, title: 'Tile actions' } }, [createIconElement('more')]);
    trigger.onclick = event => event.stopPropagation();
    const contents = element('div', { className: 'tile-menu-content' });
    for (const [label, callback, disabled] of [
      ['Move earlier', () => moveTile(index, -1), index === 0], ['Move later', () => moveTile(index, 1), index === dashboard.tiles.length - 1],
      ['Duplicate', () => duplicateTile(tile), false], ['Delete tile', () => deleteTile(tile), false]]) {
      const button = element('button', { type: 'button', text: label }); button.disabled = disabled; button.onclick = event => { event.stopPropagation(); menu.open = false; void callback(); }; contents.append(button);
    }
    menu.append(trigger, contents); actions.append(menu); actions.hidden = !permissions.edit;
    const explain = icon('assistant', `Explain displayed result for ${tile.title}`, () => {
      const cache = streams.get(tile.id)?.main;
      if (cache) void explainDisplayed(cache, displayedVisualization(card.querySelector('.tile-body'), tile, cache.snapshot()));
    });
    explain.dataset.explainDisplayed = '';
    card.append(element('header', { className: 'tile-header' }, [element('div', {}, [element('h2', { text: tile.title }), element('small', { text: TILE_TYPES.find(([type]) => type === tile.kind)?.[1] })]), actions]), element('div', { className: 'tile-body' }), element('footer', { className: 'tile-footer' }, [element('span', { className: 'tile-status', attrs: { role: 'status' } }), explain, element('span', { className: 'expand-hint', text: 'Click to expand' })]));
    card.onclick = event => { if (!event.target.closest('button,summary,details')) expand(tile); };
    card.onkeydown = event => { if (event.target === card && ['Enter', ' '].includes(event.key)) { event.preventDefault(); expand(tile); } };
    $('tile-grid').append(card); renderTile(card, tile);
  }
  const add = element('button', { type: 'button', className: 'add-tile-card' }, [createIconElement('add'), element('strong', { text: 'Add analytics tile' }), element('small', { text: 'Report, chart, or KPI' })]);
  add.onclick = () => editTile(newTile()); if (permissions.edit) $('tile-grid').append(add);
}
function readyToRun() {
  if (!model || model.revision !== dashboard.modelRevision) { message(staleModelMessage(permissions.edit)); return false; }
  if (slicersDirty) { message('Apply the dashboard slicers first.'); return false; }
  const missing = missingSlicers(); if (missing.length) { $('slicer-status').textContent = `Choose ${missing.join(', ')} to load data.`; return false; }
  return true;
}
async function runTile(tile, useGroup = false) {
  if (!readyToRun()) return;
  const version = epoch; await closing; await closeTileStreams(tile.id);
  if (version !== epoch) return;
  const group = dashboardGroup; if (!useGroup) dashboardGroup = null;
  const cache = getStream(tile); dashboardGroup = group; runs.set(tile.id, cache); $('stop-dashboard').hidden = false;
  tileStates.set(tile.id, { loading: true });
  const card = [...$('tile-grid').querySelectorAll('.analytics-tile')].find(card => card.dataset.tileId === tile.id); if (card) renderTile(card, tile);
  const started = Date.now(), timer = setInterval(() => { if (card?.isConnected && cache.loading && !cache.rows.length) card.querySelector('.tile-status').textContent = `Fetching · ${((Date.now() - started) / 1000).toFixed(1)} s`; }, 100);
  try { await cache.loadMore(); if (version === epoch && streams.get(tile.id)?.main === cache) tileStates.set(tile.id, { result: cache.snapshot() }); }
  catch (error) { if (version === epoch && streams.get(tile.id)?.main === cache) tileStates.set(tile.id, { error: error.message }); }
  finally { clearInterval(timer); if (runs.get(tile.id) === cache) runs.delete(tile.id); if (version === epoch && card?.isConnected) renderTile(card, tile); $('stop-dashboard').hidden = runs.size === 0; }
}

async function runAll() {
  if (!readyToRun()) return;
  await stopRuns(); const version = epoch, queue = [...dashboard.tiles], origin = dashboard;
  dashboardGroup = new DashboardResultGroup({ budget: browserBudget, tileIds: queue.map(tile => tile.id), start: (onFrame, signal) => readResultStream(`${API}/${origin.id}/executions/stream`, { expectedRevision: origin.revision, ...selectionBody(origin) }, onFrame, signal) });
  await Promise.all(queue.map(tile => runTile(tile, true)));
}
function expand(tile) {
  if (!readyToRun()) return;
  openExpanded({ tile, modelId: dashboard.modelId, canExport: permissions.export, canDrill: permissions.drill, canViewSql: permissions.edit, cache: getStream(tile), createDrillCache: (selection, options) => getStream(tile, selection, options), onRefresh: () => void runTile(tile), onExplain: explainDisplayed });
}

async function explainDisplayed(cache, result) {
  const version = epoch;
  try {
    if (cache.displayContext?.dashboardId !== dashboard?.id || cache.displayContext.dashboardRevision !== dashboard.revision) throw new Error('The dashboard changed. Refresh and attach a current displayed result.');
    const frozen = freezeDisplayedResult(result, cache.displayContext);
    await assistant.explainDisplayed(frozen);
  } catch (error) { if (version === epoch) message(error.message); }
}

async function showTileSql(tile) {
  const result = tileStates.get(tile.id)?.result;
  if (result?.plan?.sql) { openSql(result.plan, `${tile.title} · executed SQL`); return; }
  if (!readyToRun()) return;
  try { const plan = await requestJson(tileUrl(tile, 'plan'), { method: 'POST', body: { expectedRevision: dashboard.revision } }); openSql(plan, `${tile.title} · generated SQL`); }
  catch (error) { message(error.message); }
}
async function refreshDashboard() {
  const origin = dashboard, previousModel = model;
  if (!origin || saving || !checkLeave()) return;
  setSaving(true);
  $('tile-grid').inert = true;
  message('Refreshing dashboard…');
  try {
    const next = await requestJson(`${API}/${origin.id}`);
    const context = await requestJson(`${API}/${origin.id}/context`);
    if (dashboard?.id !== origin.id) return;
    const stopped = stopRuns(), refreshEpoch = epoch;
    await stopped;
    if (dashboard?.id !== origin.id || epoch !== refreshEpoch) return;
    dashboard = context.permissions.edit ? next : {
      ...next, selections: refreshedSelections(origin, previousModel, next, context.model),
    };
    model = context.model; catalog = context.catalog; permissions = context.permissions;
    library = library.map(item => item.id === next.id ? next : item);
    slicersDirty = false; tileStates.clear();
    renderLibrary(); renderHeading(); renderModelUpdate(); renderSlicers(); renderTiles();
    if (model.revision !== next.modelRevision) {
      message(staleModelMessage(permissions.edit));
      return;
    }
    message(''); await runAll();
  } catch (error) {
    if (dashboard?.id !== origin.id) return;
    if (error.status === 403 || error.status === 404) {
      // An explicit access failure invalidates these cached results. Network and
      // server failures below leave the last successful view available.
      void stopRuns();
      for (const dialog of document.querySelectorAll('.expanded-dialog, .cell-dialog, .sql-dialog')) dialog.close();
      dashboard = null; model = null; catalog = null; slicerDraft = null; slicersDirty = false;
      permissions = { edit: false, export: false, drill: false };
      $('ai-assistant-button').disabled = true;
      void assistant.subjectChanged();
      library = library.filter(item => item.id !== origin.id);
      disposeSelects($('slicers')); $('slicers').replaceChildren();
      $('tile-grid').replaceChildren(); $('filter-summary').replaceChildren();
      $('dashboard-content').hidden = true; $('empty-dashboard').hidden = false;
      renderAccess(); renderLibrary(); history.replaceState(null, '', '/schemer');
      message('This dashboard is no longer available to your account. It may have been removed or your access changed. Ask your administrator if you still need access.');
    } else message(error.message);
  }
  finally { $('tile-grid').inert = false; setSaving(false); }
}
async function updateDashboardModel() {
  const origin = dashboard;
  if (!origin || saving || upgradeReview || !checkLeave()) return;
  const dialog = element('dialog', { className: 'ui-dialog model-upgrade-dialog', attrs: { 'aria-label': 'Review dashboard model upgrade' } });
  const body = element('div', { className: 'model-upgrade-body' });
  const error = element('p', { className: 'error-message', attrs: { role: 'alert' } });
  const status = element('p', { className: 'hint', text: 'Loading the current model and checking dashboard references…', attrs: { role: 'status' } });
  const save = element('button', { type: 'button', className: 'ui-button primary', text: 'Save repairs & upgrade' });
  const cancel = element('button', { type: 'button', className: 'ui-button', text: 'Cancel' });
  const retry = element('button', { type: 'button', className: 'ui-button', text: 'Retry loading model' });
  let context, draft, initial, pending = false, blocked = false, editor;
  const review = { dirty: () => !!draft && JSON.stringify(draft) !== initial, dispose: () => { editor?.close(); dialog.close(); } };
  upgradeReview = review;
  const current = () => upgradeReview === review && dashboard === origin && dialog.open;
  const close = () => { if (!pending && (!review.dirty() || window.confirm('Discard the dashboard upgrade repairs?'))) dialog.close(); };
  function render() {
    body.replaceChildren();
    const issues = modelUpgradeIssues(draft, context.model, context.catalog);
    status.textContent = `Review v${origin.modelRevision} → v${context.model.revision}. ${issues.length ? `${issues.length} item${issues.length === 1 ? '' : 's'} need repair.` : 'All dashboard references are ready for server validation.'}`;
    body.append(element('p', { className: 'hint', text: 'Edit or remove affected tiles and review changed filters below. Everything stays in this draft until one validated save upgrades the dashboard.' }));
    body.append(element('a', { className: 'model-repair-link', text: 'Review or repair the source model in Schemoo', attrs: { href: `/schemoo?model=${encodeURIComponent(context.model.id)}`, target: '_blank', rel: 'noopener' } }));
    function showIssues(host, items) {
      for (const issue of items) {
        const row = element('div', { className: 'upgrade-issue' }, [element('p', { text: issue.message })]);
        if (['removeOptional', 'resetSelection', 'removeFilter', 'clearTime'].includes(issue.action)) {
          const labels = { removeOptional: 'Remove offered filter', removeFilter: 'Remove report filter', resetSelection: 'Reset saved choice', clearTime: 'Remove time analysis' };
          const repair = element('button', { type: 'button', className: 'ui-button', text: labels[issue.action] });
          repair.onclick = () => { repairUpgradeIssue(draft, issue); render(); }; row.append(repair);
        }
        host.append(row);
      }
    }
    const filters = element('section', { className: 'upgrade-section' }, [element('h3', { text: 'Dashboard filters & source' })]);
    const dashboardIssues = issues.filter(issue => !issue.tileId);
    if (dashboardIssues.length) showIssues(filters, dashboardIssues);
    else filters.append(element('p', { className: 'hint', text: 'Saved filter references are compatible. Missing runtime values can be chosen after upgrading.' }));
    body.append(filters);
    for (const tile of draft.tiles) {
      const card = element('section', { className: 'upgrade-section', attrs: { 'aria-label': `Upgrade tile ${tile.title}` } });
      const actions = element('div', { className: 'actions' });
      const edit = element('button', { type: 'button', className: 'ui-button', text: `Edit ${tile.title}` });
      edit.onclick = () => {
        editor = openTileEditor({ tile, model: context.model, catalog: context.catalog, saveLabel: 'Apply repair',
          onLoadDomain: options => loadDomainOptions(options, context.model), onSave: edited => {
            if (!current()) throw new Error('The upgrade review is no longer open. Reopen it to repair this dashboard.');
            draft.tiles = draft.tiles.map(item => item.id === edited.id ? structuredClone(edited) : item); render();
          } });
      };
      const remove = element('button', { type: 'button', className: 'ui-button', text: `Remove ${tile.title}` });
      remove.onclick = () => { draft.tiles = draft.tiles.filter(item => item.id !== tile.id); render(); };
      actions.append(edit, remove); card.append(element('h3', { text: tile.title }), actions);
      const tileIssues = issues.filter(issue => issue.tileId === tile.id);
      if (tileIssues.length) showIssues(card, tileIssues);
      else card.append(element('p', { className: 'hint', text: 'Fields and filter references are compatible.' }));
      body.append(card);
    }
    if (!draft.tiles.length) body.append(element('p', { className: 'empty-state', text: 'No tiles remain. Save an empty dashboard on the new model, then add tiles when ready.' }));
    save.disabled = pending || blocked || issues.length > 0;
  }
  async function load() {
    retry.hidden = true; save.disabled = true; error.textContent = '';
    try {
      const loaded = await requestJson(`${API}/${origin.id}/context`);
      if (!current()) return;
      if (!loaded.permissions.edit) throw new Error('Dashboard editing access is no longer available. Close this review and refresh the dashboard.');
      context = structuredClone(loaded); draft = modelUpgradeDraft(origin, context.model); initial = JSON.stringify(draft); render();
    } catch (failure) {
      if (!current()) return;
      status.textContent = 'The current model could not be loaded. Your saved dashboard is unchanged.';
      error.textContent = failure.message; retry.hidden = false;
    }
  }
  retry.onclick = load;
  save.onclick = async () => {
    if (pending || blocked || !draft || !current()) return;
    if (modelUpgradeIssues(draft, context.model, context.catalog).length) { render(); return; }
    const request = structuredClone(draft);
    pending = true; setSaving(true); save.disabled = true; cancel.disabled = true; body.inert = true; error.textContent = '';
    status.textContent = 'Validating and saving all repairs with the model upgrade…';
    let saved;
    try {
      await stopRuns();
      if (!current()) return;
      saved = await requestJson(`${API}/${origin.id}`, { method: 'PUT', body: request });
      if (!current()) return;
      dashboard = saved; model = context.model; catalog = context.catalog; permissions = context.permissions;
      library = library.map(item => item.id === saved.id ? saved : item);
      slicersDirty = false; tileStates.clear(); dialog.close();
      renderLibrary(); renderHeading(); renderModelUpdate(); renderSlicers(); renderTiles();
      message('Dashboard repairs and model upgrade saved.');
    } catch (failure) {
      if (!current()) return;
      blocked = ['dashboard_revision_conflict', 'model_revision_conflict', 'network_error', 'request_timeout', 'invalid_response'].includes(failure.code);
      render(); status.textContent = 'The saved dashboard has not been replaced in this view. Your repair draft is kept.';
      const details = failure.details?.issues?.map(issue => issue.message).filter(Boolean).join(' ');
      error.textContent = `${failure.message}${details ? ` ${details}` : ''}${blocked ? ' Close this review and refresh the dashboard before trying again.' : ''}`;
    } finally {
      pending = false; setSaving(false); cancel.disabled = false; body.inert = false;
      if (current()) save.disabled = blocked || modelUpgradeIssues(draft, context.model, context.catalog).length > 0;
    }
    if (saved && dashboard === saved) void runAll();
  };
  cancel.onclick = close;
  dialog.oncancel = event => { event.preventDefault(); close(); };
  dialog.onclose = () => { editor?.close(); if (upgradeReview === review) upgradeReview = null; dialog.remove(); };
  dialog.append(element('header', { className: 'ui-dialog__head' }, [element('h2', { text: 'Review dashboard model upgrade' }), icon('close', 'Close model upgrade review', close)]), status, body,
    element('footer', { className: 'ui-dialog__actions' }, [error, retry, cancel, save]));
  document.body.append(dialog); dialog.showModal(); cancel.focus();
  await load();
}
function editTile(tile) {
  if (!model || saving) return;
  openTileEditor({ tile, model, catalog, onLoadDomain: loadDomainOptions, onSave: async edited => {
    await closeTileStreams(edited.id); const tiles = dashboard.tiles.some(t => t.id === edited.id) ? dashboard.tiles.map(t => t.id === edited.id ? edited : t) : [...dashboard.tiles, edited];
    await update({ tiles }); tileStates.delete(edited.id); renderTiles(); void runTile(edited);
  } });
}
async function moveTile(index, offset) {
  try { const tiles = [...dashboard.tiles], [tile] = tiles.splice(index, 1); tiles.splice(index + offset, 0, tile); await update({ tiles }); renderTiles(); }
  catch (error) { message(error.message); }
}
async function duplicateTile(tile) {
  try { const copied = { ...structuredClone(tile), id: crypto.randomUUID(), title: `${tile.title.slice(0, 120)} copy` }; await update({ tiles: [...dashboard.tiles, copied] }); renderTiles(); void runTile(copied); }
  catch (error) { message(error.message); }
}
async function deleteTile(tile) {
  await confirmAction({ title: 'Delete tile?', message: `Remove “${tile.title}” from this dashboard?`, details: 'The model and database data are kept.', confirmLabel: 'Delete tile', onConfirm: async () => {
    await closeTileStreams(tile.id); await update({ tiles: dashboard.tiles.filter(t => t.id !== tile.id) }); tileStates.delete(tile.id); renderTiles();
  } });
}
async function openDashboard(id) {
  upgradeReview?.dispose();
  stopRuns(); const version = epoch; message('Loading dashboard…'); model = null; $('dashboard-content').hidden = true;
  $('filter-summary').hidden = true;
  $('toggle-dashboard-filters').setAttribute('aria-expanded', 'false');
  try {
    const next = await requestJson(`${API}/${id}`);
    if (version !== epoch) return;
    dashboard = next;
    const context = await requestJson(`${API}/${next.id}/context`);
    const source = context.model, sourceCatalog = context.catalog;
    permissions = { ...context.permissions, edit: author && context.permissions.edit };
    if (version !== epoch) return;
    dashboard = next; model = source; catalog = sourceCatalog; slicersDirty = false; filtersExpanded = false; tileStates.clear();
    $('ai-assistant-button').disabled = false;
    void assistant.subjectChanged();
    $('empty-dashboard').hidden = true; $('dashboard-content').hidden = false;
    history.replaceState(null, '', `/schemer?dashboard=${encodeURIComponent(id)}`);
    renderLibrary(); renderHeading(); renderModelUpdate(); renderSlicers(); renderTiles(); message('');
    if (source.revision !== next.modelRevision) message(staleModelMessage(permissions.edit));
    else void runAll();
  } catch (error) { if (version === epoch) { message(error.message); if (dashboard?.id === id) { $('empty-dashboard').hidden = true; $('dashboard-content').hidden = false; $('slicer-panel').hidden = true; renderHeading(); renderLibrary(); $('tile-grid').replaceChildren(element('p', { className: 'empty-state', text: 'The source model could not be loaded. Dashboard configuration is still saved.' })); } else $('empty-dashboard').hidden = false; } }
}
async function dashboardDialog(mode = 'create') {
  if (saving || !checkLeave()) return;
  const dialog = element('dialog', { className: 'ui-dialog dashboard-dialog', attrs: { 'aria-label': mode === 'rename' ? 'Rename dashboard' : 'Create dashboard' } });
  const title = mode === 'rename' ? 'Rename dashboard' : mode === 'duplicate' ? 'Duplicate dashboard' : 'Create dashboard';
  let modelId = mode === 'create' ? '' : dashboard.modelId;
  const name = element('input', { attrs: { 'aria-label': 'Dashboard name', maxlength: 128, placeholder: 'e.g. Workforce overview' } }); name.value = mode === 'rename' ? dashboard.name : mode === 'duplicate' ? `${dashboard.name.slice(0, 120)} copy` : '';
  const error = element('p', { className: 'error-message', attrs: { role: 'alert' } });
  const body = element('div', { className: 'dialog-form' }, [element('label', { className: 'stack' }, ['Dashboard name', name])]);
  if (mode === 'create') body.append(modelSelect('Schemoo model', modelList.map(m => [m.id, m.name]), '', value => { modelId = value; }), element('p', { className: 'hint', text: 'Every tile uses this model. Required filters become shared dashboard slicers.' }));
  const submit = element('button', { type: 'button', className: 'ui-button primary', text: mode === 'rename' ? 'Save name' : 'Create dashboard' });
  let pending = false;
  submit.onclick = async () => {
    if (!name.value.trim() || !modelId) { error.textContent = 'Enter a name and choose a model.'; return; }
    pending = true; submit.disabled = true; error.textContent = '';
    try {
      let saved;
      if (mode === 'rename') saved = await update({ name: name.value.trim() });
      else {
        const source = mode === 'duplicate' ? model : await requestJson(`/api/v1/schemoo/models/${modelId}`);
        saved = await requestJson(API, { method: 'POST', body: { name: name.value.trim(), modelId, modelRevision: source.revision,
          optionalFilters: mode === 'duplicate' ? dashboard.optionalFilters || [] : [],
          selections: mode === 'duplicate' ? dashboard.selections : requiredSelections(source, source.explore?.selections),
          tiles: mode === 'duplicate' ? dashboard.tiles : [] } });
        library.push(saved);
      }
      dialog.close(); if (mode !== 'rename') await openDashboard(saved.id);
    } catch (failure) { error.textContent = failure.message; }
    finally { pending = false; submit.disabled = false; }
  };
  dialog.append(element('header', { className: 'ui-dialog__head' }, [element('h2', { text: title }), icon('close', 'Close dashboard dialog', () => { if (!pending) dialog.close(); })]), body, element('footer', { className: 'ui-dialog__actions' }, [error, submit]));
  dialog.oncancel = event => { if (pending) event.preventDefault(); };
  dialog.onclose = () => { disposeSelects(dialog); dialog.remove(); };
  document.body.append(dialog); dialog.showModal(); name.focus();
}
function manageDashboardFilters() {
  if (!model || !checkLeave()) return;
  if (saving || modelNeedsUpdate()) { if (modelNeedsUpdate()) message('Update the dashboard model before changing its available filters.'); return; }
  const origin = dashboard, source = model;
  const available = source.definition.scopes.filter(scope => scope.requirement === 'optional');
  const selected = new Set(origin.optionalFilters || []);
  const dialog = element('dialog', { className: 'ui-dialog dashboard-dialog', attrs: { 'aria-label': 'Choose dashboard filters' } });
  const body = element('div', { className: 'dialog-form dashboard-filter-options' });
  body.append(element('p', { className: 'hint', text: 'Choose which optional model filters viewers may activate. Inactive filters do not affect dashboard queries.' }));
  if (!available.length) body.append(element('p', { className: 'empty-state', text: 'This model has no optional dashboard filters. Mark a model filter optional in Schemoo first.' }));
  for (const scope of available) {
    const checkbox = element('input', { attrs: { type: 'checkbox', 'aria-label': `Offer ${scope.label || scope.id}` } }); checkbox.checked = selected.has(scope.id);
    checkbox.onchange = () => { if (checkbox.checked) selected.add(scope.id); else selected.delete(scope.id); };
    body.append(element('label', { className: 'dashboard-filter-option' }, [checkbox, element('span', {}, [element('strong', { text: scope.label || scope.id }), element('small', { text: scope.kind === 'required' ? 'Always evaluated when active' : 'Evaluated when active and its source participates' })])]));
  }
  const error = element('p', { className: 'error-message', attrs: { role: 'alert' } });
  const save = element('button', { type: 'button', className: 'ui-button primary', text: 'Save available filters' });
  let pending = false;
  save.onclick = async () => {
    if (pending || saving) return;
    if (dashboard !== origin || model !== source) { error.textContent = 'This dashboard changed. Close this dialog and reopen its filters.'; return; }
    pending = true; setSaving(true); save.disabled = true; body.inert = true; error.textContent = '';
    let saved;
    try {
      const optionalFilters = available.map(scope => scope.id).filter(id => selected.has(id));
      const optionalIds = new Set(available.map(scope => scope.id));
      const selections = Object.fromEntries(Object.entries(origin.selections).filter(([id]) => !optionalIds.has(id) || selected.has(id)));
      await stopRuns();
      if (!dialog.open) return;
      if (dashboard !== origin || model !== source) { error.textContent = 'This dashboard changed. Close this dialog and reopen its filters.'; return; }
      saved = await persistDashboard(origin, source, { optionalFilters, selections });
      if (dashboard !== saved || !dialog.open) return;
      slicersDirty = false; tileStates.clear(); renderSlicers(); renderTiles(); dialog.close(); message('Available dashboard filters saved.');
    } catch (failure) { error.textContent = failure.message; }
    finally { pending = false; setSaving(false); save.disabled = false; body.inert = false; }
    if (saved && dashboard === saved) {
      try { await runAll(); }
      catch (failure) { if (dashboard === saved) message(failure.message); }
    }
  };
  dialog.append(element('header', { className: 'ui-dialog__head' }, [element('h2', { text: 'Dashboard filters' }), icon('close', 'Close dashboard filters', () => { if (!pending) dialog.close(); })]), body,
    element('footer', { className: 'ui-dialog__actions' }, [error, element('button', { type: 'button', className: 'ui-button', text: 'Cancel' }), save]));
  dialog.querySelector('footer .ui-button:not(.primary)').onclick = () => { if (!pending) dialog.close(); };
  dialog.oncancel = event => { if (pending) event.preventDefault(); };
  dialog.onclose = () => dialog.remove(); document.body.append(dialog); dialog.showModal();
}
$('create-dashboard').onclick = () => dashboardDialog(); $('welcome-create').onclick = () => dashboardDialog();
$('rename-dashboard').onclick = () => dashboardDialog('rename'); $('duplicate-dashboard').onclick = () => dashboardDialog('duplicate');
$('add-tile').onclick = () => editTile(newTile()); $('refresh-dashboard').onclick = () => { void refreshDashboard(); };
$('manage-dashboard-filters').onclick = manageDashboardFilters;
$('toggle-dashboard-filters').onclick = () => {
  if (!model || !slicerDraft) return;
  filtersExpanded = !filtersExpanded; renderFilterDisclosure();
};
$('update-dashboard-model').onclick = () => { void updateDashboardModel(); };
$('stop-dashboard').onclick = () => { stopRuns({ keepCache: true }); for (const [id, state] of tileStates) if (state.loading) tileStates.set(id, { error: 'Query cancelled.' }); renderTiles(); };
$('apply-slicers').onclick = async () => {
  const missing = missingSlicers(); if (missing.length) { $('slicer-status').textContent = `Choose ${missing.join(', ')}.`; return; }
  try { if (permissions.edit) await update({ selections: slicerDraft.selections }); else dashboard = { ...dashboard, selections: structuredClone(slicerDraft.selections) }; slicersDirty = false; renderSlicers(); message(''); await runAll(); }
  catch (error) { message(error.message); }
};
$('delete-dashboard').onclick = async () => {
  const item = dashboard;
  await confirmAction({ title: 'Delete dashboard?', message: `Delete “${item.name}” and its tiles?`, details: 'The Schemoo model is kept.', confirmLabel: 'Delete dashboard', onConfirm: async () => {
    await requestJson(`${API}/${item.id}?expectedRevision=${item.revision}`, { method: 'DELETE' }); stopRuns(); library = library.filter(d => d.id !== item.id); dashboard = null; model = null; slicersDirty = false; $('ai-assistant-button').disabled = true; void assistant.subjectChanged(); renderLibrary();
    if (library.length) await openDashboard(library[0].id); else { $('dashboard-content').hidden = true; $('empty-dashboard').hidden = false; history.replaceState(null, '', '/schemer'); }
  } });
};
window.addEventListener('schemii:logout', () => { upgradeReview?.dispose(); slicersDirty = false; void stopRuns(); $('tile-grid').replaceChildren(); });
window.addEventListener('pagehide', () => { upgradeReview?.dispose(); void dashboardGroup?.close(); for (const group of streams.values()) for (const cache of [group.main, ...group.drills.values()]) void cache.close(); });
window.addEventListener('beforeunload', event => { if (slicersDirty || upgradeReview?.dirty()) { event.preventDefault(); event.returnValue = ''; } });
const contextualArrival = new URLSearchParams(location.search).has('handoff');
initializeUi(); if (!contextualArrival) installProductNavigation($('product-navigation'), { activeProduct: 'schemer' });
async function initialize() {
  try {
    const account = await currentAccount();
    author = canAuthor(account); modelAuthor = canAccessProduct(account, 'schemoo'); renderAccess();
    if (contextualArrival) {
      installProductNavigation($('product-navigation'), { activeProduct: canAccessProduct(account, 'schemer') ? 'schemer' : null, account });
      const context = consumeProductContext(new URLSearchParams(location.search).get('handoff'), account, 'schemer');
      await openPreviewHandoff(context, { onCreated: async saved => {
        library = (await requestJson(API)).dashboards; renderLibrary();
        await openDashboard(saved.id);
      } });
      return;
    }
    const [dashboards, models] = await Promise.all([requestJson(API), author && modelAuthor ? requestJson('/api/v1/schemoo/models') : Promise.resolve({ models: [] })]);
    library = dashboards.dashboards; modelList = models.models; renderLibrary();
    const selected = new URLSearchParams(location.search).get('dashboard');
    if (selected || library.length) await openDashboard(selected || library[0].id);
    if (author && modelAuthor && !modelList.length) message('Create a Schemoo model before creating a dashboard.');
  } catch (error) {
    message(error.message);
    if (contextualArrival) $('notice').append(element('p', {}, [element('a', { text: 'Return to Schemoo to resend context', attrs: { href: '/schemoo' } }), ' · ', element('a', { text: 'Open dashboard library without context', attrs: { href: '/schemer' } })]));
  }
}
void initialize();
