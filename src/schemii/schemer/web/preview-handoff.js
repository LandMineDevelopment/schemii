import { element } from '#common/dom.js';
import { requestJson } from '#common/http.js';
import { assertProductContext, requireContextAccount, validatePreviewContext } from '#common/product-context.js';

const clone = value => JSON.parse(JSON.stringify(value));
const problem = message => { throw new Error(message); };
const fieldKey = field => JSON.stringify([field.table, field.column]);
export function previewMeasure(field, model) {
  const node = model.definition.nodes.find(item => item.id === field.table);
  return (field.aggregate || 'none') !== 'none' || node?.derivation?.kind === 'aggregate'
    && node.derivation.outputs.some(output => output.id === field.column);
}

export function previewDashboard(model, payload, { name, title, kind, tileId }) {
  validatePreviewContext(payload);
  if (model.id !== payload.modelId || model.revision !== payload.modelRevision) problem('The saved model changed. Return to Schemoo, reload and send the current preview again.');
  const explore = clone(payload.explore);
  if (explore.root !== model.definition.root) problem('The preview starts from a different object. In Schemoo, save that object as the model starting object or choose a preview with the saved starting object.');
  if (!['detail', 'aggregate'].includes(kind)) problem('Choose a detail report or an aggregation report.');
  if (!name?.trim() || name.length > 128 || !title?.trim() || title.length > 128) problem('Enter a dashboard name and a tile name, up to 128 characters each.');
  const nodes = new Set(model.definition.nodes.map(node => node.id));
  if (explore.fields.some(field => !nodes.has(field.table))) problem('A preview source or alias is no longer available. Repair and validate the preview in Schemoo.');
  const measures = explore.fields.filter(field => previewMeasure(field, model));
  if (kind === 'detail' && explore.fields.some(field => (field.aggregate || 'none') !== 'none')) problem('A detail report requires unaggregated preview fields. Choose Aggregation report or change and save the preview in Schemoo.');
  if (kind === 'aggregate' && !measures.length) problem('This preview has no measures. Choose Detail report or add and save a measure in Schemoo.');
  const scopes = new Map(model.definition.scopes.map(scope => [scope.id, scope]));
  const selections = {}, tileSelections = {}, optionalFilters = [];
  for (const [id, choice] of Object.entries(explore.selections)) {
    const scope = scopes.get(id);
    if (!scope) problem('A preview filter was removed from this model. Reload and repair the preview in Schemoo.');
    if (scope.requirement === 'optional') { optionalFilters.push(id); selections[id] = choice; }
    else if (scope.kind === 'required') selections[id] = choice;
    else tileSelections[id] = choice;
  }
  const detailFields = [...new Map(explore.fields.map(field => [fieldKey(field), { ...field, aggregate: 'none' }])).values()];
  const tile = { id: tileId, title: title.trim(), kind,
    dimensions: kind === 'detail' ? [] : explore.fields.filter(field => !previewMeasure(field, model)),
    measures: kind === 'detail' ? [] : measures, detailFields,
    selections: tileSelections, reportFilters: explore.reportFilters, limit: explore.limit };
  return { name: name.trim(), modelId: model.id, modelRevision: model.revision, optionalFilters, selections, tiles: [tile] };
}

export async function loadPreviewContext(context, request = requestJson) {
  let account = await request('/api/v1/auth/me');
  assertProductContext(context, account, 'schemer');
  const payload = validatePreviewContext(context.payload);
  const model = await request(`/api/v1/schemoo/models/${encodeURIComponent(payload.modelId)}`);
  if (model.id !== payload.modelId || model.revision !== payload.modelRevision) problem('The saved model changed. Reload it in Schemoo and send a new preview context.');
  if (payload.explore.root !== model.definition.root) problem('The preview root differs from the saved model starting object. Save the intended model starting object in Schemoo or select a matching preview.');
  await request('/api/v1/schemer/plan', { method: 'POST', body: { modelId: model.id, expectedRevision: model.revision, explore: payload.explore }, timeoutMs: 30000 });
  account = await request('/api/v1/auth/me');
  assertProductContext(context, account, 'schemer');
  return { model, account };
}

/** Review is local. The only resource write is the explicit confirmation. */
export async function openPreviewHandoff(context, { onCreated, request = requestJson } = {}) {
  const dialog = element('dialog', { className: 'ui-dialog dashboard-dialog', attrs: { 'aria-label': 'Create dashboard from saved preview' } });
  const body = element('form', { className: 'dialog-form' });
  const status = element('p', { className: 'hint', text: 'Checking the saved model, destination access and preview…', attrs: { role: 'status' } });
  const error = element('p', { className: 'error-message', attrs: { role: 'alert' } });
  const name = element('input', { attrs: { 'aria-label': 'Dashboard name', maxlength: '128', required: '' } });
  const title = element('input', { attrs: { 'aria-label': 'Tile name', maxlength: '128', required: '' } });
  const kind = element('select', { attrs: { 'aria-label': 'Initial report type' } });
  const submit = element('button', { type: 'submit', className: 'ui-button primary', text: 'Create dashboard' });
  const reconcile = element('button', { type: 'button', className: 'ui-button', text: 'Check saved dashboards', hidden: true });
  const close = element('button', { type: 'button', className: 'ui-button', text: 'Cancel' });
  let pending = false, closed = false, blocked = false, uncertain = null, source;
  const tileId = crypto.randomUUID();
  const current = () => !closed && dialog.open;
  const setPending = value => { pending = value; body.inert = value; submit.disabled = value || blocked || !!uncertain || !source; };
  close.onclick = () => { if (!pending) dialog.close(); };
  dialog.oncancel = event => { if (pending) event.preventDefault(); };
  dialog.onclose = () => { closed = true; dialog.remove(); };
  dialog.append(element('header', { className: 'ui-dialog__head' }, [element('h2', { text: 'Create dashboard from saved preview' })]), body);
  body.append(status, error);
  submit.disabled = true;
  document.body.append(dialog); dialog.showModal();
  try {
    source = (await loadPreviewContext(context, request)).model;
    if (!current()) return dialog;
    name.value = `${source.name} dashboard`.slice(0, 128); title.value = `${source.name} preview`.slice(0, 128);
    const fields = context.payload.explore.fields, hasMeasures = fields.some(field => previewMeasure(field, source));
    for (const [value, label] of [['detail', 'Detail report'], ['aggregate', 'Aggregation report']]) {
      const option = element('option', { text: label, attrs: { value } });
      option.disabled = value === 'detail' ? fields.some(field => (field.aggregate || 'none') !== 'none') : !hasMeasures;
      kind.append(option);
    }
    kind.value = hasMeasures ? 'aggregate' : 'detail';
    status.textContent = `${source.name} · saved model revision ${source.revision}. Review the names, report type, fields and filters. No dashboard exists until you confirm.`;
    const review = element('section', { className: 'stack', attrs: { 'aria-label': 'Transferred preview configuration' } });
    review.append(element('p', { text: `Starting object: ${context.payload.explore.root}. Preview limit: ${context.payload.explore.limit}. Group fields appear before measures in an aggregation report.` }));
    const fieldsList = element('ul');
    for (const field of fields) {
      const node = source.definition.nodes.find(node => node.id === field.table);
      fieldsList.append(element('li', { text: `${node?.label || field.table} [${field.table}] · ${field.column} · ${field.aggregate || 'none'}` }));
    }
    review.append(fieldsList, element('pre', { text: JSON.stringify({ selections: context.payload.explore.selections, reportFilters: context.payload.explore.reportFilters }, null, 2) }));
    body.append(element('label', { className: 'stack' }, ['Dashboard name', name]), element('label', { className: 'stack' }, ['Tile name', title]),
      element('label', { className: 'stack' }, ['Initial report type', kind]), review);
    submit.disabled = false;
  } catch (failure) {
    if (current()) { blocked = true; status.textContent = 'This saved context cannot create a dashboard.'; error.textContent = `${failure.message} Return to Schemoo to review and resend the preview.`; }
  }
  body.append(element('a', { text: 'Return to this model in Schemoo', attrs: { href: `/schemoo?model=${encodeURIComponent(context.payload.modelId)}` } }),
    element('footer', { className: 'ui-dialog__actions' }, [close, reconcile, submit]));
  reconcile.onclick = async () => {
    if (!uncertain || pending) return;
    setPending(true); error.textContent = '';
    try {
      const account = await request('/api/v1/auth/me'); requireContextAccount(account, 'schemer', context.ownerId);
      const { dashboards } = await request('/api/v1/schemer/dashboards');
      if (!current()) return;
      const matches = dashboards.filter(item => item.ownerId === context.ownerId && !uncertain.beforeIds.includes(item.id)
        && item.name === uncertain.body.name && item.modelId === uncertain.body.modelId
        && item.modelRevision === uncertain.body.modelRevision && item.tiles?.some(tile => tile.id === tileId));
      for (const item of matches) {
        const open = element('button', { type: 'button', className: 'ui-button', text: `Open saved dashboard ${item.name}` });
        open.onclick = () => { dialog.close(); void onCreated(item); }; body.append(open);
      }
      status.textContent = matches.length ? 'A saved dashboard matches this create attempt. Open it to inspect the saved result; the request will not be repeated.' : 'No matching saved dashboard is visible yet. The original request may still finish. Check again or close and inspect your dashboard library; this context will not repeat creation.';
    } catch (failure) { if (current()) error.textContent = failure.message; }
    finally { setPending(false); }
  };
  body.onsubmit = async event => {
    event.preventDefault(); if (pending || blocked || uncertain || !source) return;
    let reviewed;
    try { reviewed = previewDashboard(source, context.payload, { name: name.value, title: title.value, kind: kind.value, tileId }); }
    catch (failure) { error.textContent = failure.message; return; }
    setPending(true); error.textContent = '';
    let posted = false, saved;
    try {
      const fresh = await loadPreviewContext(context, request);
      if (!current()) return;
      previewDashboard(fresh.model, context.payload, { name: name.value, title: title.value, kind: kind.value, tileId });
      const { dashboards } = await request('/api/v1/schemer/dashboards');
      const account = await request('/api/v1/auth/me'); assertProductContext(context, account, 'schemer');
      if (!current()) return;
      uncertain = { body: reviewed, beforeIds: dashboards.map(item => item.id) };
      posted = true;
      saved = await request('/api/v1/schemer/dashboards', { method: 'POST', body: reviewed, timeoutMs: 30000 });
      if (!current()) return;
      dialog.close();
    } catch (failure) {
      if (!current()) return;
      error.textContent = failure.message;
      if (!posted || failure.status === 403 || failure.status === 404 || failure.status === 409) blocked = true;
      if (posted && (!failure.status || failure.status >= 500)) {
        reconcile.hidden = false;
        error.textContent += ' The create outcome is uncertain. Check saved dashboards before any new create attempt.';
      } else uncertain = null;
    } finally { setPending(false); }
    if (saved) await onCreated(saved);
  };
  return dialog;
}
