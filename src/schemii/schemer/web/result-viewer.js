import { scrollPosition, restoreScroll, createResultGrid, appendStreamStatus } from './scroll-results.js';
import { element } from '#common/dom.js';
import { createIconButton, downloadContent } from '#common/ui.js';
import { renderVisualization } from './visualizations.js';
import { csvContent } from './report-state.js';
function icon(name, label, callback) { const button = createIconButton({ icon: name, label, className: 'ui-button' }); button.onclick = callback; return button; }
export function openSql(plan, title = 'Executed SQL') {
  const dialog = element('dialog', { className: 'ui-dialog sql-dialog', attrs: { 'aria-label': title } });
  const status = element('p', { className: 'hint', attrs: { role: 'status' } });
  const copy = icon('copy', 'Copy SQL', async () => {
    try { await navigator.clipboard.writeText(plan.sql); status.textContent = 'SQL copied.'; }
    catch { status.textContent = 'Copy was unavailable. Select the SQL below to copy it.'; }
  });
  const code = element('pre', { attrs: { tabindex: 0 } }, [element('code', { text: plan.sql })]);
  dialog.append(element('header', { className: 'ui-dialog__head' }, [element('h2', { text: title }), element('div', { className: 'actions' }, [copy, icon('close', 'Close SQL', () => dialog.close())])]),
    element('div', { className: 'sql-body' }, [code, element('div', { className: 'query-notes' }, (plan.warnings || []).map(text => element('p', { text }))), status]));
  dialog.onclose = () => dialog.remove(); document.body.append(dialog); dialog.showModal();
}
function viewCell(value, name) {
  const dialog = element('dialog', { className: 'ui-dialog cell-dialog', attrs: { 'aria-label': `${name} value` } });
  const text = typeof value === 'object' && value !== null ? JSON.stringify(value, null, 2) : value === null ? 'NULL' : String(value);
  const content = element('textarea', { attrs: { readonly: '', 'aria-label': 'Full cell value' } }); content.value = text;
  const status = element('span', { attrs: { role: 'status' } });
  dialog.append(element('header', { className: 'ui-dialog__head' }, [element('h2', { text: name }), icon('close', 'Close value', () => dialog.close())]), content,
    element('footer', { className: 'ui-dialog__actions' }, [status, icon('copy', 'Copy cell value', async () => { try { await navigator.clipboard.writeText(text); status.textContent = 'Copied'; } catch { content.select(); status.textContent = 'Select and copy the value'; } })]));
  dialog.onclose = () => dialog.remove(); document.body.append(dialog); dialog.showModal();
}
function gridInteractions(host, result) {
  const viewport = host.querySelector('.data-grid-viewport'); if (!viewport) return;
  viewport.addEventListener('click', event => {
    const row = event.target.closest('tr[data-result-index]'); if (!row) return;
    if (event.target.closest('th')) { row.classList.toggle('selected-row'); return; }
    const cell = event.target.closest('td'); if (!cell || cell.classList.contains('drill-mark')) return;
    const column = [...row.querySelectorAll('td')].indexOf(cell);
    viewCell(result.rows[Number(row.dataset.resultIndex)][column], result.columns[column].name);
  });
  viewport.addEventListener('keydown', event => { if (event.key === 'Enter' && event.target.matches('td:not(.drill-mark)')) { event.preventDefault(); event.target.click(); } });
}
export function openExpanded({ tile, modelId, cache, createDrillCache, onRefresh, canExport = true, canDrill = true, canViewSql = true }) {
  const dialog = element('dialog', { className: 'expanded-dialog', attrs: { 'aria-label': tile.title } });
  const chartPane = element('section', { className: 'expanded-chart-pane' });
  const chartBody = element('div', { className: 'expanded-chart-body' });
  const detailPane = element('section', { className: 'drill-pane', hidden: true });
  const detailBody = element('div', { className: 'drill-body' });
  const chartTitle = element('button', { type: 'button', className: 'pane-title', text: tile.title });
  const detailTitle = element('button', { type: 'button', className: 'pane-title', text: 'Model detail rows' });
  const chartFooter = element('footer', { className: 'viewer-footer' });
  const detailFooter = element('footer', { className: 'viewer-footer' });
  const chips = element('div', { className: 'filter-chips' });
  const chartStatus = element('span', { className: 'viewer-status', attrs: { role: 'status' } });
  const detailStatus = element('span', { className: 'viewer-status', attrs: { role: 'status' } });
  const chartActions = element('div', { className: 'actions' });
  const detailActions = element('div', { className: 'actions' });
  const chart = !['detail', 'aggregate'].includes(tile.kind);
  let detailCache, unsubscribeDrill, mainScheduled = false, detailScheduled = false;
  const scheduleMain = () => { if (!mainScheduled) { mainScheduled = true; requestAnimationFrame(() => { mainScheduled = false; renderMain(); }); } };
  const scheduleDetails = () => { if (!detailScheduled) { detailScheduled = true; requestAnimationFrame(() => { detailScheduled = false; renderDetails(); }); } };
  function pane(which) { dialog.classList.toggle('show-details', which === 'details'); chartTitle.setAttribute('aria-expanded', which === 'chart'); detailTitle.setAttribute('aria-expanded', which === 'details'); }
  chartTitle.onclick = () => pane('chart'); detailTitle.onclick = () => pane('details');
  function footer(host, stream) {
    host.replaceChildren(); if (!stream) return;
    const data = stream.snapshot();
    host.append(element('span', { className: 'hint', text: `${data.rows.length} cached · ${data.loading ? 'receiving rows' : data.error ? 'interrupted' : data.limitReached ? 'Preview limit reached' : 'end of result'}` }),
      ...(canExport ? [icon('download', 'Export cached rows as CSV', () => downloadContent(csvContent(data), 'schemer-cached-rows.csv', 'text/csv;charset=utf-8'))] : []));
    if (!canExport) return;
    const download = element('button', { type: 'button', className: 'ui-button', text: 'Download full results', attrs: { title: 'Runs the full query against a fresh snapshot. Results may differ from this preview.' } });
    download.onclick = () => stream.download?.(); download.disabled = !stream.download; host.append(download);
    host.append(element('small', { className: 'export-snapshot-note', text: 'Full download uses a fresh snapshot.' }));
  }
  function renderMain() {
    if (!dialog.isConnected) return;
    const position = scrollPosition(chartBody), result = cache.snapshot();
    renderVisualization(chartBody, tile, result, { modelId, onDrill: !canDrill || tile.kind === 'detail' ? null : selected => {
      unsubscribeDrill?.(); detailCache = createDrillCache(selected); unsubscribeDrill = detailCache.subscribe(scheduleDetails);
      chips.replaceChildren(...selected.dimensions.map(d => element('span', { text: `${d.column}: ${d.value === null ? 'NULL' : d.value}` })));
      detailPane.hidden = false; pane('details'); renderDetails(); void detailCache.loadMore();
    } });
    gridInteractions(chartBody, result); appendStreamStatus(chartBody, result); restoreScroll(chartBody, position);
    chartStatus.textContent = `${cache.rows.length} ${chart ? 'groups' : 'rows'} cached${cache.snapshotAt ? ` · Started ${new Date(cache.snapshotAt).toLocaleString()}` : ''}`;
    footer(chartFooter, cache);
  }
  function renderDetails() {
    if (!dialog.isConnected || !detailCache) return;
    const position = scrollPosition(detailBody), data = detailCache.snapshot();
    detailBody.replaceChildren(createResultGrid(data)); gridInteractions(detailBody, data); appendStreamStatus(detailBody, data); restoreScroll(detailBody, position);
    detailStatus.textContent = `Model detail rows · ${data.rows.length} cached${data.plan?.warnings?.some(w => w.includes('repeat') || w.includes('DISTINCT')) ? ' · Related or repeated rows may differ from the measure count' : ''}`;
    footer(detailFooter, detailCache);
  }
  if (canViewSql) chartActions.append(icon('sql', 'Show tile SQL', () => { if (cache.plan) openSql(cache.plan, `${tile.title} · SQL`); }));
  chartActions.append( icon('refresh', 'Refresh tile', () => { dialog.close(); onRefresh(); }), icon('close', 'Close expanded tile', () => dialog.close()));
  if (canViewSql) detailActions.append(icon('sql', 'Show detail SQL', () => { if (detailCache?.plan) openSql(detailCache.plan, 'Detail rows · SQL'); }));
  detailActions.append( icon('close', 'Close detail rows', () => { detailPane.hidden = true; pane('chart'); }));
  chartPane.append(element('header', { className: 'expanded-pane-header' }, [element('div', {}, [chartTitle, chartStatus]), chartActions]), chartBody, chartFooter);
  detailPane.append(element('header', { className: 'expanded-pane-header' }, [element('div', {}, [detailTitle, chips, detailStatus]), detailActions]), detailBody, detailFooter);
  dialog.append(chartPane, detailPane);
  const unsubscribe = cache.subscribe(scheduleMain);
  dialog.onclose = () => { unsubscribe(); unsubscribeDrill?.(); dialog.remove(); };
  document.body.append(dialog); pane('chart'); dialog.showModal(); renderMain(); void cache.loadMore();
}
