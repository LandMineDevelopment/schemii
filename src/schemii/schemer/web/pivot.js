import { element } from '#common/dom.js';
import { pivotDefaults } from './dashboard-state.js';

let renderSequence = 0;
// Only the mounted host retains its existing bounded sparse matrix and current
// window. Capture reads that view; it never rebuilds a second result cache.
const displayedPivots = new WeakMap();
const text = value => value === null ? 'NULL' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const key = axis => JSON.stringify([axis.depth, ...axis.values]);
const axisCompare = (a, b) => {
  if (!a.depth || !b.depth) return b.depth - a.depth;
  for (let i = 0; i < Math.min(a.depth, b.depth); i++) {
    const x = a.values[i], y = b.values[i];
    if (x === y) continue;
    if (x === null || y === null) return x === null ? 1 : -1;
    const order = typeof x === 'number' && typeof y === 'number' ? x - y : text(x).localeCompare(text(y), undefined, { numeric: true });
    if (order) return order;
  }
  return b.depth - a.depth;
};
export function pivotScopeKind(tile, rowDepth, columnDepth) {
  if (rowDepth === tile.rowDimensions.length && columnDepth === tile.columnDimensions.length) return 'cell';
  if (!rowDepth && !columnDepth) return 'grand_total';
  if (!columnDepth && rowDepth === tile.rowDimensions.length) return 'row';
  if (!rowDepth && columnDepth === tile.columnDimensions.length) return 'column';
  return 'subtotal';
}
export function pivotSelection(tile, row, column, measureIndex) {
  return { dimensions: [...tile.rowDimensions.slice(0, row.depth).map((field, index) => ({ table: field.table, column: field.column, value: row.values[index] })),
    ...tile.columnDimensions.slice(0, column.depth).map((field, index) => ({ table: field.table, column: field.column, value: column.values[index] }))],
    measureIndex, pivot: { kind: pivotScopeKind(tile, row.depth, column.depth), rowDepth: row.depth, columnDepth: column.depth } };
}

/** Sparse, bounded metadata interpretation; never infer totals from received cells. */
export function pivotMatrix(tile, result) {
  const rows = tile.rowDimensions || [], columns = tile.columnDimensions || [], measures = tile.measures || [];
  const limits = { ...pivotDefaults(), ...tile.pivotTotals }, offset = rows.length + columns.length + measures.length;
  if (!rows.length || rows.length > 4 || !columns.length || columns.length > 3 || !measures.length || measures.length > 8
      || !Number.isInteger(limits.maximumColumns) || limits.maximumColumns < 1 || limits.maximumColumns > 24
      || !Number.isInteger(limits.maximumRows) || limits.maximumRows < 1 || limits.maximumRows > 200) return { error: 'This pivot configuration is unavailable. Edit the tile to repair its axes or capacity.' };
  const source = (result?.rows || []).slice(0, 20000), fullColumns = new Set();
  for (const row of source) {
    if (row[offset + 2] === 'column_capacity') return { capacity: true, count: row[offset + 3], maximum: limits.maximumColumns };
    if (row[offset + 2] !== 'data' || !Number.isInteger(row[offset]) || !Number.isInteger(row[offset + 1])
        || row[offset] < 0 || row[offset] > rows.length || row[offset + 1] < 0 || row[offset + 1] > columns.length) return { error: 'The pivot result has invalid grouping metadata. Refresh the tile.' };
    if (row[offset + 1] === columns.length) {
      fullColumns.add(JSON.stringify(row.slice(rows.length, rows.length + columns.length)));
      if (fullColumns.size > limits.maximumColumns) return { capacity: true, count: fullColumns.size, maximum: limits.maximumColumns };
    }
  }
  const rowKeys = new Map(), columnKeys = new Map(), cells = new Map();
  let rowsOmitted = false, qualifyingGroups = null;
  for (const values of source) {
    const r = { depth: values[offset], values: values.slice(0, values[offset]) };
    const c = { depth: values[offset + 1], values: values.slice(rows.length, rows.length + values[offset + 1]) };
    rowKeys.set(key(r), r); columnKeys.set(key(c), c);
    if (rowKeys.size > limits.maximumRows * rows.length + 1 || columnKeys.size > limits.maximumColumns * columns.length + 1) return { error: 'The pivot exceeded its bounded grouping capacity. Filter the report and refresh.' };
    cells.set(JSON.stringify([key(r), key(c)]), values);
    rowsOmitted ||= Boolean(values[offset + 4]); qualifyingGroups = values[offset + 5];
  }
  return { rows: [...rowKeys.values()].sort(axisCompare), columns: [...columnKeys.values()].sort(axisCompare), cells,
    rowsOmitted, qualifyingGroups, partial: Boolean(result?.loading || result?.limitReached || result?.error || result?.rows?.length > source.length) };
}

function caption(axis, count, all) {
  return axis.depth ? `${axis.values.map(text).join(' › ')}${axis.depth < count ? ' · subtotal' : ''}` : all;
}
function action(node, label, invoke) {
  const button = element('button', { type: 'button', className: 'pivot-drill', text: node.textContent, attrs: { 'aria-label': label } });
  button.onclick = event => { event.stopPropagation(); invoke(); };
  node.replaceChildren(button);
}
/** Exact sparse groups in the rendered row window, including column totals.
 * Depths and scope distinguish a real SQL NULL from a rollup placeholder. */
export function displayedPivot(host, tile, result) {
  const mounted = displayedPivots.get(host);
  const view = mounted || { tile, result, matrix: pivotMatrix(tile, result), first: 0, end: 50 };
  const { matrix } = view;
  tile = view.tile; result = view.result;
  const rowAxes = tile.rowDimensions.length, columnAxes = tile.columnDimensions.length;
  const offset = rowAxes + columnAxes + tile.measures.length;
  const projection = { kind: 'pivot', status: matrix.capacity ? 'capacity' : matrix.error ? 'invalid' : 'data',
    rowStart: view.first, rowEnd: Math.min(view.end, matrix.rows?.length || 0), rowGroups: matrix.rows?.length || 0,
    columnGroups: matrix.columns?.length || 0, rowAxes, columnAxes, rowsOmitted: Boolean(matrix.rowsOmitted),
    notice: matrix.error || (matrix.capacity ? `Pivot column capacity reached (at least ${matrix.count} groups; maximum ${matrix.maximum}). No numeric cells are displayed.` : 'Grouping depths count included axis prefixes. Scope marks cell, row, column, subtotal or grand_total. NULL at an included depth is SQL NULL. Totals use the complete qualifying population before Top N; absent cells are not zero.') };
  if (matrix.capacity || matrix.error) return { ...result, columns: [], rows: [], projection, cachedRows: result.rows.length, visualTruncated: true };
  const selected = [];
  for (const row of matrix.rows.slice(projection.rowStart, projection.rowEnd)) for (const column of matrix.columns) {
    const values = matrix.cells.get(JSON.stringify([key(row), key(column)]));
    if (values) selected.push([...values.slice(0, offset + 6), pivotScopeKind(tile, row.depth, column.depth)]);
  }
  const metadata = ['Row grouping depth', 'Column grouping depth', 'Pivot record type', 'Column group count', 'Preview row cap reached', 'Qualifying source groups'];
  return { ...result, projection, cachedRows: result.rows.length,
    visualTruncated: Boolean(result.visualTruncated || projection.rowStart || projection.rowEnd < projection.rowGroups),
    partial: Boolean(matrix.partial || matrix.rowsOmitted),
    columns: [...Array.from({ length: offset }, (_, index) => result.columns[index]), ...metadata.map(name => ({ name })), { name: 'Pivot scope' }], rows: selected };
}
export function renderPivot(host, tile, result, { onDrill, compact = false } = {}) {
  host.replaceChildren();
  const matrix = pivotMatrix(tile, result);
  const view = { tile, result: { ...result, cachedRows: result.rows.length }, matrix, first: 0, end: 0 };
  displayedPivots.set(host, view);
  if (matrix.capacity || matrix.error) {
    host.append(element('p', { className: 'pivot-notice error', attrs: { role: 'alert' }, text: matrix.error || `Pivot has at least ${matrix.count} column groups; its limit is ${matrix.maximum}. Add a source or aggregate-result filter, change column dimensions, or increase the bound in Edit tile. Full pivot CSV remains available.` }));
    return;
  }
  host.append(element('p', { className: 'pivot-notice', attrs: { role: 'status' }, text: `Totals: complete qualifying population before Top N.${tile.topN ? ` Top ${tile.topN} cell groups are displayed; totals can include other cells.` : ''}${matrix.rowsOmitted ? ' Preview row cap reached; totals include omitted rows. Download full pivot groups for every selected row.' : ''}${matrix.partial ? ' Partial pivot: showing received groups; absent cells are not zero.' : ''}` }));
  for (const warning of result?.plan?.warnings || []) host.append(element('p', { className: 'hint', text: warning }));
  if (!matrix.rows.length) {
    host.append(element('p', { className: 'empty-state', text: result?.loading ? 'Waiting for pivot groups…' : result?.error ? 'The pivot did not complete. Refresh to try again.' : 'No qualifying groups. Change the report filters and refresh.' })); return;
  }
  if (matrix.qualifyingGroups === 0) host.append(element('p', { className: 'empty-state', text: 'No qualifying source rows. Counts are zero; other grand-total measures are NULL.' }));
  const viewport = element('div', { className: 'pivot-viewport data-grid-viewport', attrs: { tabindex: 0, role: 'region', 'aria-label': `${tile.title} pivot. Scroll horizontally for columns and vertically for rows.` } });
  const table = element('table', { className: 'pivot-table', attrs: { 'aria-label': tile.title, 'aria-rowcount': matrix.rows.length + tile.columnDimensions.length + 1 } });
  const head = element('thead'), body = element('tbody'), measureCount = tile.measures.length;
  const measureLabel = index => result.columns?.[tile.rowDimensions.length + tile.columnDimensions.length + index]?.name || `${tile.measures[index].aggregate.toUpperCase()} · ${tile.measures[index].column}`;
  const instance = ++renderSequence;
  const id = suffix => `pivot-${instance}-${tile.id.replace(/[^a-z0-9_-]/gi, '_')}-${suffix}`;
  const columnHeaders = matrix.columns.map(() => []);
  for (let level = 0; level < tile.columnDimensions.length; level++) {
    const tr = element('tr');
    if (!level) tr.append(element('th', { className: 'pivot-corner', text: tile.rowDimensions.map(field => field.column).join(' › '), attrs: { rowspan: tile.columnDimensions.length + 1, scope: 'col' } }));
    for (let start = 0; start < matrix.columns.length;) {
      const column = matrix.columns[start], groupKey = JSON.stringify([column.depth <= level ? column.depth : level + 1, ...column.values.slice(0, level + 1)]);
      let end = start + 1;
      while (end < matrix.columns.length && JSON.stringify([matrix.columns[end].depth <= level ? matrix.columns[end].depth : level + 1, ...matrix.columns[end].values.slice(0, level + 1)]) === groupKey) end++;
      const title = column.depth > level ? `${tile.columnDimensions[level].column}: ${text(column.values[level])}` : column.depth ? 'Subtotal' : 'All columns';
      const headerId = id(`column-${level}-${start}`);
      for (let column = start; column < end; column++) columnHeaders[column].push(headerId);
      tr.append(element('th', { text: title, attrs: { id: headerId, colspan: (end - start) * measureCount, scope: 'colgroup' } })); start = end;
    }
    head.append(tr);
  }
  const metrics = element('tr');
  matrix.columns.forEach((column, columnIndex) => tile.measures.forEach((_, measureIndex) => {
    const th = element('th', { text: measureLabel(measureIndex), attrs: { id: id(`measure-${columnIndex}-${measureIndex}`), scope: 'col', headers: columnHeaders[columnIndex].join(' '), title: caption(column, tile.columnDimensions.length, 'All columns') } });
    metrics.append(th);
  }));
  head.append(metrics);
  const windowSize = compact ? 12 : 50, rowHeight = 44, headerHeight = rowHeight * (tile.columnDimensions.length + 1);
  let previous = -1;
  function draw() {
    const first = Math.max(0, Math.min(Math.max(0, matrix.rows.length - windowSize), Math.floor(Math.max(0, viewport.scrollTop - headerHeight) / rowHeight) - 5));
    if (first === previous) return; previous = first;
    const end = Math.min(matrix.rows.length, first + windowSize), children = [];
    view.first = first; view.end = end;
    const spacer = count => { const tr = element('tr', { attrs: { 'aria-hidden': 'true' } }), td = element('td', { attrs: { colspan: matrix.columns.length * measureCount + 1 } }); td.style.height = `${count * rowHeight}px`; tr.append(td); return tr; };
    if (first) children.push(spacer(first));
    for (let rowIndex = first; rowIndex < end; rowIndex++) {
      const row = matrix.rows[rowIndex], title = caption(row, tile.rowDimensions.length, 'All rows');
      const tr = element('tr', { className: row.depth < tile.rowDimensions.length ? 'pivot-total-row' : '', attrs: { 'aria-rowindex': rowIndex + tile.columnDimensions.length + 2 } });
      tr.append(element('th', { className: 'pivot-row-label', text: title, attrs: { id: id(`row-${rowIndex}`), scope: 'row', title, 'aria-label': row.depth ? tile.rowDimensions.slice(0, row.depth).map((field, index) => `${field.column}: ${text(row.values[index])}`).join(' · ') + (row.depth < tile.rowDimensions.length ? ' · subtotal' : '') : 'All rows' } }));
      matrix.columns.forEach((column, columnIndex) => {
        const values = matrix.cells.get(JSON.stringify([key(row), key(column)]));
        tile.measures.forEach((_, measureIndex) => {
          const value = values?.[tile.rowDimensions.length + tile.columnDimensions.length + measureIndex], label = `${title} · ${caption(column, tile.columnDimensions.length, 'All columns')} · ${measureLabel(measureIndex)}: ${values ? text(value) : 'not displayed or no qualifying cell'}`;
          const td = element('td', { className: !row.depth || !column.depth ? 'pivot-total' : '', text: values ? text(value) : '—', attrs: { headers: `${id(`row-${rowIndex}`)} ${columnHeaders[columnIndex].join(' ')} ${id(`measure-${columnIndex}-${measureIndex}`)}`, title: label } });
          if (values && onDrill) action(td, `${label}. View contributing records`, () => onDrill(pivotSelection(tile, row, column, measureIndex)));
          tr.append(td);
        });
      });
      children.push(tr);
    }
    if (end < matrix.rows.length) children.push(spacer(matrix.rows.length - end));
    body.replaceChildren(...children);
  }
  table.append(head, body); viewport.append(table); viewport.addEventListener('scroll', draw, { passive: true }); draw(); host.append(viewport);
}
