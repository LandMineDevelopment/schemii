import { createDataGrid } from '#common/data-grid.js';
import { element } from '#common/dom.js';

export function scrollPosition(host) { const viewport = host.querySelector('.data-grid-viewport') || host; return { top: viewport.scrollTop, left: viewport.scrollLeft }; }
export function restoreScroll(host, position) { const viewport = host.querySelector('.data-grid-viewport') || host; viewport.scrollTop = position.top; viewport.scrollLeft = position.left; viewport.dispatchEvent(new Event('scroll')); }

/** Only a small window of cached rows enters the DOM, regardless of result size. */
export function createResultGrid(result, decorate = () => {}) {
  const viewport = createDataGrid({ columns: result.columns, rows: [] });
  viewport.classList.add('virtual-result-grid'); viewport.tabIndex = 0;
  viewport.setAttribute('aria-label', 'Cached report rows');
  const body = viewport.querySelector('tbody'), height = 34, windowSize = 100; let previous = -1;
  const spacer = size => { const tr = element('tr', { className: 'result-spacer', attrs: { 'aria-hidden': 'true' } }); const td = element('td', { attrs: { colspan: result.columns.length + 1 } }); td.style.height = `${size}px`; tr.append(td); return tr; };
  function draw() {
    const first = Math.max(0, Math.min(Math.max(0, result.rows.length - windowSize), Math.floor(viewport.scrollTop / height) - 20));
    if (first === previous) return; previous = first;
    const end = Math.min(result.rows.length, first + windowSize);
    const chunk = createDataGrid({ columns: result.columns, rows: result.rows.slice(first, end), rowOffset: first });
    const rows = [...chunk.querySelectorAll('tbody tr')];
    rows.forEach((row, index) => { row.dataset.resultIndex = String(first + index); for (const cell of row.querySelectorAll('td')) cell.tabIndex = 0; decorate(row, first + index); });
    body.replaceChildren(...(first ? [spacer(first * height)] : []), ...rows, ...(end < result.rows.length ? [spacer((result.rows.length - end) * height)] : []));
  }
  viewport.addEventListener('scroll', draw, { passive: true }); draw(); return viewport;
}
export function appendStreamStatus(host, result) {
  const text = result.error || (result.limitReached ? 'Preview limit reached · download full results' : result.loading ? 'Loading more rows…' : 'End of result');
  const status = element('div', { className: `stream-progress${result.loading ? ' is-loading' : ''}${result.error ? ' is-error' : ''}`, attrs: { role: 'status' }, text });
  const viewport = host.querySelector('.data-grid-viewport'); (viewport || host).append(status);
}
