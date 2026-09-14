import { element } from '#common/dom.js';
import { createIconButton } from '#common/ui.js';
import { modelSelect, disposeSelects } from '#model/select.js';
import { renderReportFilters, renderParameterValues } from '#model/filter-controls.js';
import { fieldLabel } from '#model/model-columns.js';
import { aggregateChoices } from './report-state.js';
import { TILE_TYPES, tileErrors, fieldChoices, fieldKey } from './dashboard-state.js';

function icon(name, label, onClick, disabled = false) {
  const button = createIconButton({ icon: name, label, className: 'ui-button' }); button.onclick = onClick; button.disabled = disabled; return button;
}
export function openTileEditor({ tile, model, catalog, onSave, onLoadDomain }) {
  const current = structuredClone(tile), original = JSON.stringify(tile);
  current.selections ||= {};
  const modelDraft = { ...structuredClone(model.definition), fields: [], selections: current.selections, reportFilters: current.reportFilters };
  const dialog = element('dialog', { className: 'ui-dialog tile-editor', attrs: { 'aria-label': 'Configure analytics tile' } });
  const body = element('div', { className: 'tile-editor-body' });
  const error = element('p', { className: 'error-message', attrs: { role: 'alert' } });
  const name = element('input', { attrs: { maxlength: 128, 'aria-label': 'Tile title', placeholder: 'Name this view', autofocus: '' } }); name.value = current.title;
  name.oninput = () => { current.title = name.value; };
  const type = modelSelect('Analytics view', TILE_TYPES, current.kind, value => {
    current.kind = value;
    if (value === 'detail') { current.dimensions = []; current.measures = []; }
    if (value === 'kpi') { current.dimensions = []; current.measures = current.measures.slice(0, 1); }
    if (['bar', 'line', 'donut'].includes(value)) current.dimensions = current.dimensions.slice(0, 1);
    if (value === 'donut') current.measures = current.measures.slice(0, 1);
    render();
  });
  const tabbar = element('div', { className: 'editor-tabs', attrs: { role: 'tablist', 'aria-label': 'Tile configuration' } });
  let tab = 'fields', pending = false;
  const panes = new Map();
  function fieldList(title, key, measure = false) {
    const host = element('section', { className: 'field-section' });
    host.append(element('h3', { text: title }));
    const choices = fieldChoices(model, catalog, '', measure);
    const selectHost = element('div');
    const select = modelSelect(`Add ${title.toLowerCase()}`, choices.map((field, index) => [String(index), field.label]), '', value => {
      const field = choices[Number(value)]; if (!field || current[key].length >= 64) return;
      const options = aggregateChoices(modelDraft, catalog, field);
      const aggregate = measure ? (options.find(([value]) => value === 'count')?.[0] || 'none') : 'none';
      if (current[key].some(f => fieldKey(f) === fieldKey(field) && f.aggregate === aggregate)) { error.textContent = 'That output is already selected. Choose another aggregation on the existing output before adding it again.'; return; }
      current[key].push({ table: field.table, column: field.column, aggregate }); render();
    });
    selectHost.append(select); host.append(selectHost);
    current[key].forEach((field, index) => {
      const row = element('div', { className: 'field-output' });
      row.append(element('span', { text: fieldLabel(modelDraft, catalog, field) }));
      if (measure) {
        const opts = aggregateChoices(modelDraft, catalog, field).filter(([value]) => value !== 'none' || model.definition.nodes.find(n => n.id === field.table)?.derivation?.kind === 'aggregate');
        row.append(modelSelect(`Measure ${index + 1} aggregation`, opts, field.aggregate, value => { field.aggregate = value; }));
      }
      const move = direction => { const [field] = current[key].splice(index, 1); current[key].splice(index + direction, 0, field); render(); };
      row.append(element('div', { className: 'actions' }, [icon('earlier', `Move ${title} ${index + 1} earlier`, () => move(-1), index === 0), icon('later', `Move ${title} ${index + 1} later`, () => move(1), index === current[key].length - 1), icon('close', `Remove ${title} ${index + 1}`, () => { current[key].splice(index, 1); render(); })]));
      host.append(row);
    });
    return host;
  }
  function render() {
    disposeSelects(body); body.replaceChildren();
    tabbar.replaceChildren();
    const tabs = [['fields', 'View & fields'], ['filters', 'Optional filters'], ...(current.kind === 'detail' ? [] : [['drill', 'Drill-through columns']])];
    if (!tabs.some(([id]) => id === tab)) tab = 'fields';
    for (const [id, label] of tabs) {
      const button = element('button', { type: 'button', className: 'ui-button', text: label, attrs: { role: 'tab', 'aria-selected': id === tab, 'aria-controls': `tile-${id}-pane` } });
      button.onclick = () => { tab = id; render(); }; tabbar.append(button);
    }
    body.id = `tile-${tab}-pane`; body.setAttribute('role', 'tabpanel');
    if (tab === 'fields') {
      if (current.kind === 'detail') body.append(element('p', { className: 'hint', text: 'Return raw joined rows using these columns. No grouping or aggregation is applied.' }), fieldList('Detail columns', 'detailFields'));
      else {
        body.append(element('p', { className: 'hint', text: current.kind === 'aggregate' ? 'Dimensions become GROUP BY columns. Measures calculate one value per group.' : current.kind === 'kpi' ? 'Choose one measure across all matching records.' : 'Choose one dimension and numeric measures. Click a mark in the expanded view to inspect its contributing records.' }));
        if (current.kind !== 'kpi') body.append(fieldList('Dimensions', 'dimensions'));
        body.append(fieldList('Measures', 'measures', true));
      }
      const limit = element('input', { attrs: { type: 'number', min: 1, max: 100, 'aria-label': 'Rows or groups per page' } }); limit.value = current.limit;
      limit.onchange = () => { current.limit = Math.max(1, Math.min(100, Number(limit.value) || 100)); limit.value = current.limit; };
      body.append(element('label', { className: 'inline-label' }, ['Rows / groups per batch', limit]), element('p', { className: 'hint', text: 'Tables page through cached rows. Charts fetch the next batch as you scroll. Each query runs once; previously loaded rows are reused.' }));
    } else if (tab === 'filters') {
      const parameters = element('section'), filters = element('section'); body.append(parameters, filters);
      const optionalDraft = { ...modelDraft, scopes: modelDraft.scopes.filter(scope => scope.kind !== 'required' && scope.requirement !== 'optional') };
      if (optionalDraft.scopes.length) renderParameterValues(parameters, { draft: optionalDraft, catalog, force: true, onChange: () => { current.selections = optionalDraft.selections; }, onLoadDomain });
      renderReportFilters(filters, { draft: modelDraft, catalog, onChange: () => { current.reportFilters = modelDraft.reportFilters; }, onLoadDomain });
      body.prepend(element('p', { className: 'hint', text: 'These filters apply only to this tile. Required model slicers are controlled by the dashboard.' }));
    } else body.append(element('p', { className: 'hint', text: 'Choose the raw columns shown after selecting a chart mark or aggregate value. Schemer adds the clicked group and measure filters automatically. Related detail fields can produce multiple rows per contributing record.' }), fieldList('Drill-through columns', 'detailFields'));
  }
  function close() {
    if (pending) return;
    if (JSON.stringify(current) !== original && !window.confirm('Discard changes to this tile?')) return;
    dialog.close();
  }
  const save = element('button', { type: 'button', className: 'ui-button primary', text: 'Apply & run' });
  save.onclick = async () => {
    if (pending) return;
    current.title = current.title.trim(); const errors = tileErrors(current);
    if (errors.length) { error.textContent = errors.join(' '); return; }
    pending = true; save.disabled = true; body.inert = true; error.textContent = '';
    try { await onSave(current); dialog.close(); }
    catch (failure) { error.textContent = failure.message; }
    finally { pending = false; save.disabled = false; body.inert = false; }
  };
  dialog.append(element('header', { className: 'ui-dialog__head' }, [element('h2', { text: 'Configure tile' }), icon('close', 'Close tile editor', close)]),
    element('div', { className: 'tile-basics' }, [element('label', { className: 'stack' }, ['Title', name]), type]), tabbar, body,
    element('footer', { className: 'ui-dialog__actions' }, [error, element('button', { type: 'button', className: 'ui-button', text: 'Cancel', attrs: { id: 'cancel-tile-edit' } }), save]));
  dialog.querySelector('#cancel-tile-edit').onclick = close;
  dialog.oncancel = event => { event.preventDefault(); close(); };
  dialog.addEventListener('close', () => { disposeSelects(dialog); dialog.remove(); }, { once: true });
  document.body.append(dialog); render(); dialog.showModal(); name.focus();
}
