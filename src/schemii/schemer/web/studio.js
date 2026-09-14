import { scrollPosition, restoreScroll, autoLoadRows } from './scroll-results.js';
import { element } from '#common/dom.js';
import { requestJson } from '#common/http.js';
import { initializeUi, createIconButton, createIconElement } from '#common/ui.js';
import { installProductNavigation } from '#common/product-navigation.js';
import { confirmAction } from '#common/confirmation.js';
import { readExecution } from '#common/query-execution.js';
import { modelSelect, disposeSelects } from '#model/select.js';
import { renderParameterValues } from '#model/filter-controls.js';
import { newTile, dashboardUpdate, TILE_TYPES } from './dashboard-state.js';
import { ResultCache, DashboardResultGroup } from './result-cache.js';
import { renderVisualization } from './visualizations.js';
import { openTileEditor } from './tile-editor.js';
import { openExpanded, openSql } from './result-viewer.js';

const $ = id => document.getElementById(id), API = '/api/v1/schemer/dashboards';
let dashboard, model, catalog, library = [], modelList = [], slicerDraft, slicersDirty = false, saving = false, epoch = 0;
const runs = new Map(), tileStates = new Map(), streams = new Map();
let closing = Promise.resolve(), dashboardGroup;
const message = text => { $('notice').textContent = text; };
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
function stopRuns() {
  for (const [id, state] of tileStates) if (state.loading) tileStates.set(id, { error: 'Query stopped. Refresh to run again.' });
  ++epoch;
  const old = [...streams.values()].flatMap(group => [group.main, ...group.drills.values()]);
  streams.clear(); runs.clear(); $('stop-dashboard').hidden = true;
  closing = Promise.all([closing, ...(dashboardGroup ? [dashboardGroup.close()] : []), ...old.map(cache => cache.close())]);
  dashboardGroup = null;
  return closing;
}
async function closeTileStreams(id) {
  const group = streams.get(id); streams.delete(id);
  if (group) await Promise.all([group.main, ...group.drills.values()].map(cache => cache.close()));
}
function getStream(tile, selection = null) {
  let group = streams.get(tile.id);
  const create = selected => {
    const origin = dashboard;
    const currentGroup = dashboardGroup;
    const cache = new ResultCache({ pageSize: tile.limit, start: () => !selected && currentGroup ? currentGroup.tile(tile.id) : requestJson(`${API}/${origin.id}/tiles/${encodeURIComponent(tile.id)}/executions`, {
      method: 'POST', body: { expectedRevision: origin.revision, selection: selected }, timeoutMs: 900000 }), onChange: () => {
      if (selected || streams.get(tile.id)?.main !== cache) return;
      if (cache.started) tileStates.set(tile.id, { result: cache.snapshot() });
      const card = [...$('tile-grid').querySelectorAll('.analytics-tile')].find(card => card.dataset.tileId === tile.id);
      if (card) renderTile(card, tile);
    } });
    return cache;
  };
  if (!group) { group = { main: create(null), drills: new Map() }; streams.set(tile.id, group); }
  if (!selection) return group.main;
  const key = JSON.stringify(selection);
  if (!group.drills.has(key)) group.drills.set(key, create(selection));
  return group.drills.get(key);
}

function checkLeave() { return !slicersDirty || window.confirm('Discard unapplied slicer changes?'); }
function setSaving(value) {
  saving = value;
  for (const id of ['create-dashboard', 'welcome-create', 'rename-dashboard', 'duplicate-dashboard', 'delete-dashboard', 'manage-dashboard-filters', 'add-tile', 'apply-slicers', 'refresh-dashboard', 'update-dashboard-model']) $(id).disabled = value;
}
async function update(patch) {
  if (saving) throw new Error('A dashboard change is still saving. Try again shortly.');
  const original = dashboard;
  setSaving(true);
  try {
    const optionalFilters = patch.optionalFilters || original.optionalFilters || [];
    const allowed = model ? new Set([...optionalFilters, ...model.definition.scopes.filter(scope => scope.kind === 'required' && scope.requirement !== 'optional').map(scope => scope.id)]) : null;
    const selections = Object.fromEntries(Object.entries(patch.selections || original.selections).filter(([id]) => !allowed || allowed.has(id)));
    const saved = await requestJson(`${API}/${original.id}`, { method: 'PUT', body: dashboardUpdate(original, { ...patch, optionalFilters, selections }) });
    if (dashboard?.id !== original.id) return saved;
    dashboard = saved; library = library.map(d => d.id === saved.id ? saved : d); renderLibrary(); renderHeading(); return saved;
  } finally { setSaving(false); }
}
function renderLibrary() {
  $('dashboard-list').replaceChildren(...library.map(item => {
    const button = element('button', { type: 'button', className: 'dashboard-link', attrs: { 'aria-current': item.id === dashboard?.id ? 'page' : undefined } }, [element('strong', { text: item.name }), element('small', { text: `${item.tiles.length} tile${item.tiles.length === 1 ? '' : 's'}` })]);
    button.onclick = () => { if (!saving && checkLeave()) void openDashboard(item.id); }; return button;
  }));
  if (!library.length) $('dashboard-list').append(element('p', { className: 'hint', text: 'Your saved dashboards appear here.' }));
}
function renderHeading() {
  $('dashboard-name').textContent = dashboard.name;
  $('dashboard-source').textContent = model ? `${model.name} · ${model.database}.${model.namespace}` : 'Model unavailable';
  $('dashboard-status').textContent = `${dashboard.tiles.length} analytics tiles · ${model && model.revision !== dashboard.modelRevision ? `model v${dashboard.modelRevision} needs updating` : 'saved on server'}`;
}
function modelNeedsUpdate() { return !!model && model.revision !== dashboard?.modelRevision; }
function renderModelUpdate() {
  const stale = modelNeedsUpdate();
  $('model-update').hidden = !stale;
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
  $('slicer-panel').hidden = !slicerDraft.scopes.length;
  renderParameterValues($('slicers'), { draft: slicerDraft, catalog, force: true, optionalSelectionLabel: 'Activate', onLoadDomain: loadDomainOptions, onChange: () => {
    slicersDirty = true; stopRuns(); tileStates.clear(); renderTiles();
    $('slicer-status').textContent = 'Filters changed. Apply to update every tile.'; $('apply-slicers').classList.add('primary');
  } });
  $('apply-slicers').classList.remove('primary');
  const missing = missingSlicers(); $('slicer-status').textContent = missing.length ? `Choose ${missing.join(', ')} to load data.` : '';
}
async function loadDomainOptions(context) {
  const origin = model;
  const authoring = !!context.draft, domain = context.domain || context.input?.domain;
  const binding = authoring ? { definition: origin.definition, domain } : { scopeId: context.scope.id, alternativeId: context.alternative.id, parameterId: context.input.id };
  const response = await requestJson(`/api/v1/schemoo/models/${origin.id}/${authoring ? 'domain-values' : 'parameter-values'}`, { method: 'POST', body: { expectedRevision: origin.revision, ...binding, search: context.search, consoleId: `con_${crypto.randomUUID().replaceAll('-', '')}` } });
  const result = await readExecution(response), hasLabel = domain?.labelColumn && domain.labelColumn !== domain.column;
  return result.rows.filter(row => row[0] !== null).map(row => ({ value: row[0], label: String(hasLabel ? row[1] ?? row[0] : row[0]), description: hasLabel ? String(row[0]) : '' }));
}
function tileUrl(tile, suffix) { return `${API}/${dashboard.id}/tiles/${encodeURIComponent(tile.id)}/${suffix}`; }

function renderTile(card, tile) {
  const state = tileStates.get(tile.id), cache = streams.get(tile.id)?.main, body = card.querySelector('.tile-body'), status = card.querySelector('.tile-status');
  if (state?.loading) { body.replaceChildren(element('p', { className: 'empty-state', text: 'Loading…' })); status.textContent = 'Running…'; }
  else if (state?.error) {
    body.replaceChildren(element('p', { className: 'tile-error', text: state.error }));
    const retry = element('button', { type: 'button', className: 'ui-button', text: 'Retry' }); retry.onclick = event => { event.stopPropagation(); void runTile(tile); }; body.append(retry); status.textContent = 'Could not load';
  } else if (state?.result) {
    const position = scrollPosition(body);
    const chart = !['detail', 'aggregate'].includes(tile.kind);
    const data = state.result;
    renderVisualization(body, tile, data, { compact: true });
    if (chart && cache?.hasMore) { const more = element('button', { type: 'button', className: 'ui-button load-chart-more', text: cache.loading ? 'Loading…' : 'Load more groups' }); more.disabled = cache.loading; more.onclick = event => { event.stopPropagation(); void cache.loadMore().catch(error => message(error.message)); }; body.append(more); }
    restoreScroll(body, position);
    if (!chart && cache) autoLoadRows(body, cache, () => cache.loadMore().catch(error => message(error.message)));
    body.onscroll = () => { if (chart && cache?.hasMore && !cache.loading && (body.scrollHeight - body.scrollTop - body.clientHeight < 60 || body.scrollWidth > body.clientWidth && body.scrollWidth - body.scrollLeft - body.clientWidth < 60)) void cache.loadMore().catch(error => message(error.message)); };
    status.textContent = `${state.result.rows.length} ${tile.kind === 'detail' ? 'rows' : 'groups'} cached${cache?.loading ? ' · loading…' : ''}${state.result.hasMore ? ' · scroll for more' : ''}${state.result.error ? ' · fetch stopped' : ''}`;
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
    menu.append(trigger, contents); actions.append(menu);
    card.append(element('header', { className: 'tile-header' }, [element('div', {}, [element('h2', { text: tile.title }), element('small', { text: TILE_TYPES.find(([type]) => type === tile.kind)?.[1] })]), actions]), element('div', { className: 'tile-body' }), element('footer', { className: 'tile-footer' }, [element('span', { className: 'tile-status', attrs: { role: 'status' } }), element('span', { className: 'expand-hint', text: 'Click to expand' })]));
    card.onclick = event => { if (!event.target.closest('button,summary,details')) expand(tile); };
    card.onkeydown = event => { if (event.target === card && ['Enter', ' '].includes(event.key)) { event.preventDefault(); expand(tile); } };
    $('tile-grid').append(card); renderTile(card, tile);
  }
  const add = element('button', { type: 'button', className: 'add-tile-card' }, [createIconElement('add'), element('strong', { text: 'Add analytics tile' }), element('small', { text: 'Report, chart, or KPI' })]);
  add.onclick = () => editTile(newTile()); $('tile-grid').append(add);
}
function readyToRun() {
  if (!model || model.revision !== dashboard.modelRevision) { message('Review the updated source model before running this dashboard.'); return false; }
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
  const started = Date.now(), timer = setInterval(() => { if (card?.isConnected && cache.loading) card.querySelector('.tile-status').textContent = `Fetching · ${((Date.now() - started) / 1000).toFixed(1)} s`; }, 100);
  try { await cache.loadMore(); if (version === epoch && streams.get(tile.id)?.main === cache) tileStates.set(tile.id, { result: cache.snapshot() }); }
  catch (error) { if (version === epoch && streams.get(tile.id)?.main === cache) tileStates.set(tile.id, { error: error.message }); }
  finally { clearInterval(timer); if (runs.get(tile.id) === cache) runs.delete(tile.id); if (version === epoch && card?.isConnected) renderTile(card, tile); $('stop-dashboard').hidden = runs.size === 0; }
}

async function runAll() {
  if (!readyToRun()) return;
  await stopRuns(); const version = epoch, queue = [...dashboard.tiles], origin = dashboard;
  dashboardGroup = new DashboardResultGroup({ start: () => requestJson(`${API}/${origin.id}/executions`, { method: 'POST', body: { expectedRevision: origin.revision }, timeoutMs: 900000 }) });
  const worker = async () => { while (queue.length && epoch === version) await runTile(queue.shift(), true); };
  await Promise.all([worker(), worker()]);
}
function expand(tile) {
  if (!readyToRun()) return;
  openExpanded({ tile, cache: getStream(tile), createDrillCache: selection => getStream(tile, selection), onRefresh: () => void runTile(tile) });
}

async function showTileSql(tile) {
  const result = tileStates.get(tile.id)?.result;
  if (result) { openSql(result.plan, `${tile.title} · executed SQL`); return; }
  if (!readyToRun()) return;
  try { const plan = await requestJson(tileUrl(tile, 'plan'), { method: 'POST', body: { expectedRevision: dashboard.revision } }); openSql(plan, `${tile.title} · generated SQL`); }
  catch (error) { message(error.message); }
}
async function loadCurrentModel({ catalogRequired = false } = {}) {
  const source = await requestJson(`/api/v1/schemoo/models/${dashboard.modelId}`);
  if (catalogRequired || source.revision !== model?.revision) {
    catalog = await requestJson(`/api/v1/schemoo/catalog?connection_id=${encodeURIComponent(source.connectionId)}&namespace=${encodeURIComponent(source.namespace)}`);
  }
  model = source;
  return source;
}
async function refreshDashboard() {
  const origin = dashboard;
  if (!origin || saving) return;
  message('Checking the current Schemoo model…');
  try {
    const source = await loadCurrentModel();
    if (dashboard?.id !== origin.id) return;
    if (source.revision !== origin.modelRevision) {
      await stopRuns(); tileStates.clear(); renderHeading(); renderModelUpdate(); renderSlicers(); renderTiles();
      message('The source model changed. Update this dashboard after reviewing the model in Schemoo.');
      return;
    }
    renderHeading(); renderModelUpdate(); message(''); await runAll();
  } catch (error) { if (dashboard?.id === origin.id) message(error.message); }
}
async function updateDashboardModel() {
  const origin = dashboard;
  if (!origin || saving) return;
  setSaving(true); message('Validating this dashboard against the current model…');
  try {
    const source = await loadCurrentModel({ catalogRequired: true });
    if (dashboard?.id !== origin.id) return;
    if (source.revision !== origin.modelRevision) {
      await stopRuns();
      const eligible = new Set(source.definition.scopes.filter(scope => scope.requirement === 'optional').map(scope => scope.id));
      const optionalFilters = (origin.optionalFilters || []).filter(id => eligible.has(id));
      const allowedSelections = new Set([...optionalFilters, ...source.definition.scopes.filter(scope => scope.kind === 'required' && scope.requirement !== 'optional').map(scope => scope.id)]);
      const selections = Object.fromEntries(Object.entries(origin.selections).filter(([id]) => allowedSelections.has(id)));
      const saved = await requestJson(`${API}/${origin.id}`, { method: 'PUT', body: dashboardUpdate(origin, { modelRevision: source.revision, optionalFilters, selections }) });
      if (dashboard?.id !== origin.id) return;
      dashboard = saved; library = library.map(item => item.id === saved.id ? saved : item);
    }
    slicersDirty = false; tileStates.clear(); renderLibrary(); renderHeading(); renderModelUpdate(); renderSlicers(); renderTiles();
    message('Dashboard updated to the current model.'); await runAll();
  } catch (error) {
    if (dashboard?.id === origin.id) {
      renderHeading(); renderModelUpdate();
      message(`Dashboard was not updated. ${error.message}`);
    }
  } finally { setSaving(false); }
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
  stopRuns(); const version = epoch; message('Loading dashboard…'); model = null; $('dashboard-content').hidden = true;
  try {
    const next = await requestJson(`${API}/${id}`);
    if (version !== epoch) return;
    dashboard = next;
    const source = await requestJson(`/api/v1/schemoo/models/${next.modelId}`);
    const sourceCatalog = await requestJson(`/api/v1/schemoo/catalog?connection_id=${encodeURIComponent(source.connectionId)}&namespace=${encodeURIComponent(source.namespace)}`);
    if (version !== epoch) return;
    dashboard = next; model = source; catalog = sourceCatalog; slicersDirty = false; tileStates.clear();
    $('empty-dashboard').hidden = true; $('dashboard-content').hidden = false;
    history.replaceState(null, '', `/schemer?dashboard=${encodeURIComponent(id)}`);
    renderLibrary(); renderHeading(); renderModelUpdate(); renderSlicers(); renderTiles(); message('');
    if (source.revision !== next.modelRevision) message('The source model changed. Update this dashboard after reviewing the model in Schemoo.');
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
  if (saving || modelNeedsUpdate()) { if (modelNeedsUpdate()) message('Update the dashboard model before changing its available filters.'); return; }
  const available = model.definition.scopes.filter(scope => scope.requirement === 'optional');
  const selected = new Set(dashboard.optionalFilters || []);
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
  save.onclick = async () => {
    save.disabled = true; body.inert = true; error.textContent = '';
    try {
      await stopRuns();
      const optionalFilters = available.map(scope => scope.id).filter(id => selected.has(id));
      const optionalIds = new Set(available.map(scope => scope.id));
      const selections = Object.fromEntries(Object.entries(dashboard.selections).filter(([id]) => !optionalIds.has(id) || selected.has(id)));
      await update({ optionalFilters, selections }); slicersDirty = false; tileStates.clear(); renderSlicers(); renderTiles(); dialog.close(); message('Available dashboard filters saved.'); await runAll();
    } catch (failure) { error.textContent = failure.message; }
    finally { save.disabled = false; body.inert = false; }
  };
  dialog.append(element('header', { className: 'ui-dialog__head' }, [element('h2', { text: 'Dashboard filters' }), icon('close', 'Close dashboard filters', () => dialog.close())]), body,
    element('footer', { className: 'ui-dialog__actions' }, [error, element('button', { type: 'button', className: 'ui-button', text: 'Cancel' }), save]));
  dialog.querySelector('footer .ui-button:not(.primary)').onclick = () => dialog.close();
  dialog.onclose = () => dialog.remove(); document.body.append(dialog); dialog.showModal();
}
$('create-dashboard').onclick = () => dashboardDialog(); $('welcome-create').onclick = () => dashboardDialog();
$('rename-dashboard').onclick = () => dashboardDialog('rename'); $('duplicate-dashboard').onclick = () => dashboardDialog('duplicate');
$('add-tile').onclick = () => editTile(newTile()); $('refresh-dashboard').onclick = () => { void refreshDashboard(); };
$('manage-dashboard-filters').onclick = manageDashboardFilters;
$('update-dashboard-model').onclick = () => { void updateDashboardModel(); };
$('stop-dashboard').onclick = () => { stopRuns(); for (const [id, state] of tileStates) if (state.loading) tileStates.set(id, { error: 'Query cancelled.' }); renderTiles(); };
$('apply-slicers').onclick = async () => {
  const missing = missingSlicers(); if (missing.length) { $('slicer-status').textContent = `Choose ${missing.join(', ')}.`; return; }
  try { await update({ selections: slicerDraft.selections }); slicersDirty = false; renderSlicers(); message(''); await runAll(); }
  catch (error) { message(error.message); }
};
$('delete-dashboard').onclick = async () => {
  const item = dashboard;
  await confirmAction({ title: 'Delete dashboard?', message: `Delete “${item.name}” and its tiles?`, details: 'The Schemoo model is kept.', confirmLabel: 'Delete dashboard', onConfirm: async () => {
    await requestJson(`${API}/${item.id}?expectedRevision=${item.revision}`, { method: 'DELETE' }); stopRuns(); library = library.filter(d => d.id !== item.id); dashboard = null; model = null; slicersDirty = false; renderLibrary();
    if (library.length) await openDashboard(library[0].id); else { $('dashboard-content').hidden = true; $('empty-dashboard').hidden = false; history.replaceState(null, '', '/schemer'); }
  } });
};
window.addEventListener('pagehide', () => { if (dashboardGroup?.base) void fetch(dashboardGroup.base, { method: 'DELETE', credentials: 'same-origin', keepalive: true }).catch(() => {}); for (const group of streams.values()) for (const cache of [group.main, ...group.drills.values()]) if (cache.base) void fetch(cache.base, { method: 'DELETE', credentials: 'same-origin', keepalive: true }).catch(() => {}); });
window.addEventListener('beforeunload', event => { if (slicersDirty) { event.preventDefault(); event.returnValue = ''; } });
initializeUi(); installProductNavigation($('product-navigation'), { activeProduct: 'schemer' });
async function initialize() {
  try {
    const [dashboards, models] = await Promise.all([requestJson(API), requestJson('/api/v1/schemoo/models')]);
    library = dashboards.dashboards; modelList = models.models; renderLibrary();
    const selected = new URLSearchParams(location.search).get('dashboard');
    if (selected || library.length) await openDashboard(selected || library[0].id);
    if (!modelList.length) message('Create a Schemoo model before creating a dashboard.');
  } catch (error) { message(error.message); }
}
void initialize();
