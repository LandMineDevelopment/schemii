import { element } from '#common/dom.js';
import { createIconButton } from '#common/ui.js';
import { modelSelect, disposeSelects } from '#model/select.js';
import { renderReportFilters, renderParameterValues } from '#model/filter-controls.js';
import { fieldLabel } from '#model/model-columns.js';
import { aggregateChoices } from './report-state.js';
import { timeDimensions } from './time-analysis.js';
import { renderTimeAnalysis } from './time-analysis-editor.js';
import { TILE_TYPES, tileErrors, fieldChoices, fieldKey, reportOutputs, reportOutputKey } from './dashboard-state.js';

function icon(name, label, onClick, disabled = false) {
  const button = createIconButton({ icon: name, label, className: 'ui-button' }); button.onclick = onClick; button.disabled = disabled; return button;
}
export function openTileEditor({ tile, model, catalog, onSave, onLoadDomain, saveLabel = 'Apply & run' }) {
  const current = structuredClone(tile), original = JSON.stringify(tile);
  current.selections ||= {};
  current.sort ||= []; current.aggregateFilters ||= []; current.topN ??= null;
  const modelDraft = { ...structuredClone(model.definition), fields: [], selections: current.selections, reportFilters: current.reportFilters };
  const dialog = element('dialog', { className: 'ui-dialog tile-editor', attrs: { 'aria-label': 'Configure analytics tile' } });
  const body = element('div', { className: 'tile-editor-body' });
  const error = element('p', { className: 'error-message', attrs: { role: 'alert' } });
  const name = element('input', { attrs: { maxlength: 128, 'aria-label': 'Tile title', placeholder: 'Name this view', autofocus: '' } }); name.value = current.title;
  name.oninput = () => { current.title = name.value; };
  const type = modelSelect('Analytics view', TILE_TYPES, current.kind, value => {
    current.kind = value;
    if (value === 'detail') { current.dimensions = []; current.measures = []; current.timeAnalysis = null; }
    if (value === 'kpi') { current.timeAnalysis = null; current.dimensions = []; current.measures = current.measures.slice(0, 1); }
    clearMissingTimeDimension();
    if (value === 'donut') current.measures = current.measures.slice(0, 1);
    render();
  });
  const tabbar = element('div', { className: 'editor-tabs', attrs: { role: 'tablist', 'aria-label': 'Tile configuration' } });
  let tab = 'fields', pending = false;
  function clearMissingTimeDimension() {
    const time = current.timeAnalysis;
    if (time && !current.dimensions.some(field => field.table === time.table && field.column === time.column)) current.timeAnalysis = null;
  }
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
    if (key === 'dimensions' && timeDimensions(current, model, catalog).length > 1) host.append(element('p', { className: 'hint', text: 'Group one date dimension by a calendar period. Choosing another date returns the previous date to exact values.' }));
    current[key].forEach((field, index) => {
      const row = element('div', { className: 'field-output' });
      row.append(element('span', { text: fieldLabel(modelDraft, catalog, field) }));
      const dateDimension = key === 'dimensions' && timeDimensions({ dimensions: [field] }, model, catalog).length > 0;
      const grouped = current.timeAnalysis?.table === field.table && current.timeAnalysis?.column === field.column;
      if (dateDimension) {
        const grouping = modelSelect(`Dimension ${index + 1} group by`, [['none', 'Exact date / time'], ['day', 'Day'], ['week', 'Week'], ['month', 'Month'], ['year', 'Year']], grouped ? current.timeAnalysis.granularity : 'none', value => {
          if (value === 'none') { if (grouped) current.timeAnalysis = null; }
          else current.timeAnalysis = { timezone: 'UTC', weekStart: 'monday', comparison: 'none', runningTotal: false, ...current.timeAnalysis, table: field.table, column: field.column, granularity: value };
          render();
        });
        row.append(grouping);
      }
      if (measure) {
        const opts = aggregateChoices(modelDraft, catalog, field).filter(([value]) => value !== 'none' || model.definition.nodes.find(n => n.id === field.table)?.derivation?.kind === 'aggregate');
        row.append(modelSelect(`Measure ${index + 1} aggregation`, opts, field.aggregate, value => { field.aggregate = value; }));
      }
      const move = direction => { const [field] = current[key].splice(index, 1); current[key].splice(index + direction, 0, field); render(); };
      row.append(element('div', { className: 'actions' }, [icon('earlier', `Move ${title} ${index + 1} earlier`, () => move(-1), index === 0), icon('later', `Move ${title} ${index + 1} later`, () => move(1), index === current[key].length - 1), icon('close', `Remove ${title} ${index + 1}`, () => { current[key].splice(index, 1); clearMissingTimeDimension(); render(); })]));
      host.append(row);
      if (dateDimension && grouped) host.append(renderTimeAnalysis(current));
    });
    return host;
  }
  function render() {
    disposeSelects(body); body.replaceChildren();
    tabbar.replaceChildren();
    const tabs = [['fields', 'View & fields'], ['filters', 'Optional filters'], ['results', 'Sort & result filters'], ...(current.kind === 'detail' ? [] : [['drill', 'Drill-through columns']])];
    if (!tabs.some(([id]) => id === tab)) tab = 'fields';
    for (const [id, label] of tabs) {
      const button = element('button', { type: 'button', className: 'ui-button', text: label, attrs: { role: 'tab', 'aria-selected': id === tab, 'aria-controls': `tile-${id}-pane` } });
      button.onclick = () => { tab = id; render(); }; tabbar.append(button);
    }
    body.id = `tile-${tab}-pane`; body.setAttribute('role', 'tabpanel');
    if (tab === 'fields') {
      if (current.kind === 'detail') body.append(element('p', { className: 'hint', text: 'Return raw joined rows using these columns. No grouping or aggregation is applied.' }), fieldList('Detail columns', 'detailFields'));
      else {
        body.append(element('p', { className: 'hint', text: current.kind === 'aggregate' ? 'Dimensions become GROUP BY columns. Measures calculate one value per group.' : current.kind === 'kpi' ? 'Choose one measure across all matching records.' : current.kind === 'line' ? 'The first dimension is the horizontal axis; remaining dimensions define separate lines for each measure. Move dimensions to change their roles. Put the date first for time trends. Missing points break lines.' : current.kind === 'donut' ? 'Choose one or more dimensions and one numeric measure. Each complete dimension combination becomes a slice.' : 'Choose one or more dimensions and numeric measures. The first dimension groups the bars; remaining dimensions become colored series. Move dimensions to set the default grouping.' }));
        if (current.kind !== 'kpi') body.append(fieldList('Dimensions', 'dimensions'));
        body.append(fieldList('Measures', 'measures', true));
      }
      body.append(element('p', { className: 'hint', text: 'Results stream automatically into a bounded browser cache. Scroll to browse received rows or groups; scrolling does not run another query. Previews stop at row or memory limits, and charts may display fewer groups. Refresh runs the query again. Expand a tile and choose Download full results to run the full query against a fresh snapshot.' }));

    } else if (tab === 'results') renderResultControls();
    else if (tab === 'filters') {
      const parameters = element('section'), filters = element('section'); body.append(parameters, filters);
      const optionalDraft = { ...modelDraft, scopes: modelDraft.scopes.filter(scope => scope.kind !== 'required' && scope.requirement !== 'optional') };
      if (optionalDraft.scopes.length) renderParameterValues(parameters, { draft: optionalDraft, catalog, force: true, onChange: () => { current.selections = optionalDraft.selections; }, onLoadDomain });
      renderReportFilters(filters, { draft: modelDraft, catalog, onChange: () => { current.reportFilters = modelDraft.reportFilters; }, onLoadDomain });
      body.prepend(element('p', { className: 'hint', text: 'These filters apply only to this tile. Required model slicers are controlled by the dashboard.' }));
    } else body.append(element('p', { className: 'hint', text: 'Choose the raw columns shown after selecting a chart mark or aggregate value. Schemer adds the clicked group and measure filters automatically. Related detail fields can produce multiple rows per contributing record.' }), fieldList('Drill-through columns', 'detailFields'));
  }
  function renderResultControls() {
    const outputs = reportOutputs(current, model, catalog), reference = output => ({ table: output.table, column: output.column, aggregate: output.aggregate, kind: output.kind });
    const measures = new Set(current.measures.map(field => JSON.stringify([field.table, field.column, field.aggregate])));
    const aggregateOutputs = outputs.filter(output => measures.has(JSON.stringify([output.table, output.column, output.aggregate])));
    body.append(element('p', { className: 'hint', text: 'Source filters run first. Result filters run after aggregation and complete time comparisons or running totals. Sorting and Top N then choose the final results, before browser delivery limits. Remaining outputs break sort ties in ascending order, with NULLs last.' }));
    const top = element('input', { attrs: { type: 'number', min: 1, max: 2147483647, step: 1, placeholder: 'All qualifying results', 'aria-label': 'Top N results' } });
    top.value = current.topN ?? ''; top.oninput = () => { current.topN = top.value === '' ? null : Number(top.value); };
    body.append(element('label', { className: 'stack' }, ['Top N results (optional)', top]));
    function chooseOutput(label, options, selected, change) {
      const key = reportOutputKey(selected), choices = options.map(output => [reportOutputKey(output), output.label]);
      if (!choices.some(([value]) => value === key)) choices.unshift([key, `${selected.table} · ${selected.column} (unavailable output)`]);
      return modelSelect(label, choices, key, value => { const [table, column, aggregate, kind] = JSON.parse(value); change({ table, column, aggregate, kind }); render(); });
    }
    const sorting = element('section', { className: 'field-section' }, [element('h3', { text: 'Ordered sort priorities' })]);
    current.sort.forEach((item, index) => {
      const card = element('section', { className: 'upgrade-section' });
      card.append(chooseOutput(`Sort ${index + 1} output`, outputs, item, ref => Object.assign(item, ref)),
        modelSelect(`Sort ${index + 1} direction`, [['asc', 'Ascending'], ['desc', 'Descending']], item.direction, value => { item.direction = value; }),
        modelSelect(`Sort ${index + 1} NULL placement`, [['last', 'NULLs last'], ['first', 'NULLs first']], item.nulls || 'last', value => { item.nulls = value; }));
      const move = offset => { current.sort.splice(index, 1); current.sort.splice(index + offset, 0, item); render(); };
      card.append(element('div', { className: 'actions' }, [icon('earlier', `Move sort ${index + 1} earlier`, () => move(-1), index === 0), icon('later', `Move sort ${index + 1} later`, () => move(1), index === current.sort.length - 1), icon('close', `Remove sort ${index + 1}`, () => { current.sort.splice(index, 1); render(); })]));
      sorting.append(card);
    });
    const addSort = element('button', { type: 'button', className: 'ui-button', text: 'Add sort priority' });
    const nextSort = outputs.find(output => !current.sort.some(item => reportOutputKey(item) === reportOutputKey(output)));
    addSort.disabled = !nextSort || current.sort.length >= 32;
    addSort.onclick = () => { if (nextSort) { current.sort.push({ ...reference(nextSort), direction: 'asc', nulls: 'last' }); render(); } };
    sorting.append(addSort); body.append(sorting);
    const predicates = element('section', { className: 'field-section' }, [element('h3', { text: 'Aggregate-result filters (all must match)' })]);
    if (current.kind === 'detail') predicates.append(element('p', { className: 'hint', text: 'Detail reports have no aggregate outputs. Use source-row filters on the Optional filters tab.' }));
    current.aggregateFilters.forEach((item, index) => {
      const card = element('section', { className: 'upgrade-section' });
      card.append(chooseOutput(`Result filter ${index + 1} output`, aggregateOutputs, item, ref => Object.assign(item, ref)),
        modelSelect(`Result filter ${index + 1} operator`, [['eq', 'Equals'], ['ne', 'Does not equal'], ['gt', 'Greater than'], ['gte', 'At least'], ['lt', 'Less than'], ['lte', 'At most'], ['is_null', 'Is NULL'], ['not_null', 'Is not NULL']], item.operator, value => { item.operator = value; if (['is_null', 'not_null'].includes(value)) item.value = null; render(); }));
      if (!['is_null', 'not_null'].includes(item.operator)) {
        const output = aggregateOutputs.find(output => reportOutputKey(output) === reportOutputKey(item)), type = output?.dataType || '';
        if (type === 'boolean') card.append(modelSelect(`Result filter ${index + 1} value`, [['', 'Choose a value'], ['true', 'True'], ['false', 'False']], item.value == null ? '' : String(item.value), value => { item.value = value === '' ? null : value === 'true'; }));
        else {
          const numeric = /^(smallint|integer|bigint|numeric|decimal|real|double precision|int[248]|float[48])(?:\b|\()/i.test(type);
          const input = element('input', { attrs: { type: numeric ? 'number' : type === 'date' ? 'date' : 'text', step: 'any', maxlength: 4000, 'aria-label': `Result filter ${index + 1} value` } });
          input.value = item.value ?? ''; input.oninput = () => { item.value = numeric ? input.value === '' ? null : Number(input.value) : input.value; }; card.append(input);
        }
      }
      card.append(icon('close', `Remove result filter ${index + 1}`, () => { current.aggregateFilters.splice(index, 1); render(); })); predicates.append(card);
    });
    const addFilter = element('button', { type: 'button', className: 'ui-button', text: 'Add aggregate-result filter' });
    addFilter.disabled = !aggregateOutputs.length || current.aggregateFilters.length >= 32;
    addFilter.onclick = () => { current.aggregateFilters.push({ ...reference(aggregateOutputs[0]), operator: 'gt', value: null }); render(); };
    predicates.append(addFilter); body.append(predicates);
  }
  function close() {
    if (pending) return;
    if (JSON.stringify(current) !== original && !window.confirm('Discard changes to this tile?')) return;
    dialog.close();
  }
  const save = element('button', { type: 'button', className: 'ui-button primary', text: saveLabel });
  save.onclick = async () => {
    if (pending) return;
    current.title = current.title.trim(); const errors = tileErrors(current, model, catalog);
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
  return dialog;
}
