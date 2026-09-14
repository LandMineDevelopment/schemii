import { scrollPosition, restoreScroll, autoLoadRows } from './scroll-results.js';
import { element } from '#common/dom.js';
import { createIconButton, downloadContent } from '#common/ui.js';
import { createDataGrid } from '#common/data-grid.js';
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
  const rows = [...host.querySelectorAll('tbody tr')];
  rows.forEach((row, index) => {
    row.querySelector('th').onclick = () => { row.classList.toggle('selected-row'); };
    [...row.querySelectorAll('td')].forEach((cell, column) => {
      if (cell.classList.contains('drill-mark')) return;
      cell.tabIndex = 0; cell.setAttribute('aria-label', `${result.columns[column].name}: ${cell.textContent}. Open value`);
      cell.onclick = () => viewCell(result.rows[index][column], result.columns[column].name);
      cell.onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); cell.click(); } };
    });
  });
}
export function openExpanded({ tile, cache, createDrillCache, onRefresh }) {
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
  let detailCache, selection, result, details, busy = false, ticket = 0;
  function pane(which) { dialog.classList.toggle('show-details', which === 'details'); chartTitle.setAttribute('aria-expanded', which === 'chart'); detailTitle.setAttribute('aria-expanded', which === 'details'); }
  chartTitle.onclick = () => { pane('chart'); bindRowScrolling(); }; detailTitle.onclick = () => { pane('details'); bindRowScrolling(); };
  function footer(host, data, stream, drill) {
    host.replaceChildren(); if (!data) return;
    const first = data.rows.length ? data.pageOffset + 1 : 0, last = data.pageOffset + data.rows.length;
    host.append(element('span', { className: 'hint', text: `${!drill && chart ? `${stream.rows.length} groups cached` : `Rows ${first}–${last} · ${stream.rows.length} cached`}${stream.error ? ' · cursor closed' : stream.hasMore ? ' · more available' : ' · end of result'}${stream.error ? ' · fetch stopped' : ''}` }),
      icon('download', !drill && chart ? 'Export cached chart groups as CSV' : 'Export cached rows as CSV', () => downloadContent(csvContent(data), 'schemer-cached-rows.csv', 'text/csv;charset=utf-8')));
    if (!drill && chart) {
      if (stream.hasMore) { const more = element('button', { type: 'button', className: 'ui-button', text: stream.loading ? 'Loading…' : 'Load more groups' }); more.disabled = busy || stream.loading; more.onclick = () => load(false, true); host.append(more); }
      return;
    }
  }

  function render() {
    const chartPosition = scrollPosition(chartBody), detailPosition = scrollPosition(detailBody);
    if (result) {
      renderVisualization(chartBody, tile, result, { onDrill: tile.kind === 'detail' ? null : selected => {
        selection = selected; detailCache = createDrillCache(selected); details = null;
        chips.replaceChildren(...selected.dimensions.map(d => element('span', { text: `${d.column}: ${d.value === null ? 'NULL' : d.value}` })));
        detailPane.hidden = false; pane('details'); void load(true);
      } });
      gridInteractions(chartBody, result);
      chartStatus.textContent = `${cache.rows.length} ${chart ? 'groups' : 'rows'} cached · ${(cache.elapsedMs / 1000).toFixed(2)} s fetching${cache.hasMore ? ' · scroll to load more' : ''}`;
      restoreScroll(chartBody, chartPosition);
    }
    if (details) {
      detailBody.replaceChildren(createDataGrid({ ...details, rowOffset: details.pageOffset })); gridInteractions(detailBody, details); restoreScroll(detailBody, detailPosition);
      detailStatus.textContent = `Model detail rows · ${(detailCache.elapsedMs / 1000).toFixed(2)} s fetching${details.plan.warnings.some(w => w.includes('repeat') || w.includes('DISTINCT')) ? ' · Related or repeated rows may differ from the measure count' : ''}`;
    }
    footer(chartFooter, result, cache, false); footer(detailFooter, details, detailCache, true);
  }
  async function load(drill, more = false) {
    if (busy) return;
    const version = ++ticket, stream = drill ? detailCache : cache, status = drill ? detailStatus : chartStatus;
    busy = true; const started = Date.now();
    const ticker = setInterval(() => { status.textContent = `Fetching · ${((Date.now() - started) / 1000).toFixed(1)} s`; }, 100);
    if (!stream.rows.length) (drill ? detailBody : chartBody).replaceChildren(element('p', { className: 'empty-state', text: 'Loading rows…' }));
    renderFooters();
    try {
      if (more || !stream.started || stream.pending) await stream.loadMore();
      const data = stream.snapshot();
      if (version !== ticket || !dialog.isConnected) return;
      if (drill) details = data; else result = data; render();
    } catch (error) {
      if (version !== ticket || !dialog.isConnected) return;
      if (stream.rows.length) {
        if (drill) details = stream.snapshot(); else result = stream.snapshot();
        render();
      } else (drill ? detailBody : chartBody).replaceChildren(element('p', { className: 'empty-state error', text: error.message }));
      status.textContent = error.message;
    } finally { clearInterval(ticker); busy = false; if (dialog.isConnected) { renderFooters(); bindRowScrolling(); } }
  }
  function bindRowScrolling() {
    if (!chart) autoLoadRows(chartBody, cache, () => load(false, true), () => !busy);
    if (details) autoLoadRows(detailBody, detailCache, () => load(true, true), () => !busy);
  }
  function renderFooters() { footer(chartFooter, result, cache, false); footer(detailFooter, details, detailCache, true); }
  chartBody.onscroll = () => {
    const nearEnd = chartBody.scrollHeight - chartBody.clientHeight - chartBody.scrollTop < 80;
    const nearRight = chartBody.scrollWidth > chartBody.clientWidth && chartBody.scrollWidth - chartBody.clientWidth - chartBody.scrollLeft < 80;
    if (chart && cache.hasMore && !busy && !cache.loading && (nearEnd || nearRight)) void load(false, true);
  };
  chartActions.append(icon('sql', 'Show tile SQL', () => { if (cache.plan) openSql(cache.plan, `${tile.title} · SQL`); }), icon('refresh', 'Refresh tile', () => { dialog.close(); onRefresh(); }), icon('close', 'Close expanded tile', () => dialog.close()));
  detailActions.append(icon('sql', 'Show detail SQL', () => { if (detailCache?.plan) openSql(detailCache.plan, 'Detail rows · SQL'); }), icon('close', 'Close detail rows', () => { detailPane.hidden = true; pane('chart'); }));
  chartPane.append(element('header', { className: 'expanded-pane-header' }, [element('div', {}, [chartTitle, chartStatus]), chartActions]), chartBody, chartFooter);
  detailPane.append(element('header', { className: 'expanded-pane-header' }, [element('div', {}, [detailTitle, chips, detailStatus]), detailActions]), detailBody, detailFooter);
  dialog.append(chartPane, detailPane);
  // The dashboard owns the cache. Collapsing a tile must not re-run its query or
  // discard already-fetched drill rows when the same mark is opened again.
  dialog.onclose = () => { ++ticket; dialog.remove(); };
  document.body.append(dialog); pane('chart'); dialog.showModal(); void load(false);
}
