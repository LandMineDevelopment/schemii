import { element } from '#common/dom.js';
import { createDataGrid } from '#common/data-grid.js';
import { markSelection } from './dashboard-state.js';
const colors = ['#f4b942', '#65a9ff', '#9b82f4', '#71d49a', '#f36b74', '#55c5c2'];
const label = value => value === null ? 'NULL' : String(value);
const numeric = value => value !== null && value !== '' && Number.isFinite(Number(value));
const format = value => numeric(value) ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 }).format(Number(value)) : label(value);
function svg(tag, attributes = {}) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  return node;
}
function actionable(node, text, callback) {
  if (!callback) return;
  node.setAttribute('tabindex', '0'); node.setAttribute('role', 'button'); node.setAttribute('aria-label', text);
  node.classList.add('drill-mark');
  node.onclick = event => { event.stopPropagation(); callback(); };
  node.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); callback(); } };
}
export function renderVisualization(host, tile, result, { onDrill, compact = false } = {}) {
  host.replaceChildren();
  const warnings = (result?.plan?.warnings || []).filter(text => text.includes('repeated rows from joins'));
  if (warnings.length) {
    const notice = element('details', { className: 'aggregation-warning' }, [
      element('summary', { text: 'Aggregation warning' }),
      ...warnings.map(text => element('p', { text })),
    ]);
    notice.onclick = event => event.stopPropagation();
    notice.onkeydown = event => event.stopPropagation();
    host.append(notice);
  }
  if (!result?.rows.length) { host.append(element('p', { className: 'empty-state', text: 'No matching rows for these slicers and filters.' })); return; }
  const drill = (row, index) => onDrill ? () => onDrill(markSelection(tile, row, index)) : null;
  if (tile.kind === 'detail' || tile.kind === 'aggregate') {
    const grid = createDataGrid({ ...result, rowOffset: result.pageOffset || 0 });
    if (onDrill && tile.kind === 'aggregate') {
      [...grid.querySelectorAll('tbody tr')].forEach((tr, rowIndex) => {
        [...tr.querySelectorAll('td')].forEach((td, columnIndex) => {
          if (columnIndex >= tile.dimensions.length) actionable(td, `View records for ${td.textContent}`, drill(result.rows[rowIndex], columnIndex - tile.dimensions.length));
        });
      });
    }
    host.append(grid); return;
  }
  if (tile.kind === 'kpi') {
    const value = element('div', { className: 'kpi-value' }, [element('strong', { text: format(result.rows[0][0]) }), element('span', { text: result.columns[0].name })]);
    actionable(value, 'View contributing records', drill(result.rows[0], 0)); host.append(value); return;
  }
  if (result.rows.some(row => tile.measures.some((_, index) => row[tile.dimensions.length + index] !== null && !numeric(row[tile.dimensions.length + index])))) {
    host.append(element('p', { className: 'empty-state error', text: 'This chart needs numeric measures. Edit the tile to choose a numeric measure.' })); return;
  }
  const legend = element('div', { className: 'chart-legend' });
  tile.measures.forEach((_, index) => {
    const item = element('span', { text: result.columns[tile.dimensions.length + index]?.name || `Measure ${index + 1}` });
    item.style.setProperty('--series-color', colors[index % colors.length]); legend.append(item);
  });
  if (tile.kind === 'bar') {
    const chart = element('div', { className: 'bar-chart' });
    const values = result.rows.flatMap(row => tile.measures.map((_, index) => Number(row[1 + index] ?? 0)));
    const min = Math.min(0, ...values), max = Math.max(0, ...values), span = max - min || 1;
    for (const row of result.rows) {
      const group = element('div', { className: 'bar-group' }, [element('div', { className: 'bar-label', text: label(row[0]) })]);
      tile.measures.forEach((_, index) => {
        const n = Number(row[index + 1] ?? 0);
        const bar = element('div', { className: 'bar-mark' });
        bar.style.left = `${(Math.min(0, n) - min) / span * 100}%`; bar.style.width = `${Math.abs(n) / span * 100}%`; bar.style.background = colors[index % colors.length];
        const axis = element('span', { className: 'zero-axis' }); axis.style.left = `${-min / span * 100}%`;
        const track = element('div', { className: 'bar-track' }, [axis, bar]);
        const rowNode = element('div', { className: 'bar-value-row', title: `${label(row[0])}: ${format(row[index + 1])}` }, [track, element('span', { text: format(row[index + 1]) })]);
        actionable(rowNode, `${label(row[0])}: ${format(row[index + 1])}. View records`, drill(row, index)); group.append(rowNode);
      });
      chart.append(group);
    }
    host.append(legend, chart); return;
  }
  if (tile.kind === 'line') {
    const width = compact ? Math.max(360, result.rows.length * 28) : Math.max(760, result.rows.length * 40), height = compact ? 270 : 450, pad = compact ? 34 : 52;
    const values = result.rows.flatMap(row => tile.measures.map((_, index) => row[index + 1])).filter(numeric).map(Number);
    const low = Math.min(0, ...values), high = Math.max(1, ...values), span = high - low || 1;
    const chart = svg('svg', { viewBox: `0 0 ${width} ${height}`, class: 'line-chart', role: 'img', 'aria-label': tile.title });
    chart.style.minWidth = `${width}px`;
    const x = index => pad + index / Math.max(1, result.rows.length - 1) * (width - pad * 2);
    const y = n => height - pad - (n - low) / span * (height - pad * 2);
    for (let i = 0; i <= 4; i++) {
      const value = low + span * i / 4;
      chart.append(svg('line', { x1: pad, x2: width - pad, y1: y(value), y2: y(value), class: 'chart-gridline' }));
      const text = svg('text', { x: pad - 8, y: y(value) + 4, 'text-anchor': 'end' }); text.textContent = format(value); chart.append(text);
    }
    tile.measures.forEach((_, series) => {
      let path = '', connected = false;
      result.rows.forEach((row, index) => { if (!numeric(row[series + 1])) { connected = false; return; } path += `${connected ? 'L' : 'M'}${x(index)},${y(Number(row[series + 1]))} `; connected = true; });
      chart.append(svg('path', { d: path, fill: 'none', stroke: colors[series % colors.length], 'stroke-width': 2.5 }));
      result.rows.forEach((row, index) => {
        if (!numeric(row[series + 1])) return;
        const point = svg('circle', { cx: x(index), cy: y(Number(row[series + 1])), r: compact ? 4 : 6, fill: colors[series % colors.length] });
        const title = svg('title'); title.textContent = `${label(row[0])}: ${format(row[series + 1])}`; point.append(title);
        actionable(point, title.textContent, drill(row, series)); chart.append(point);
      });
    });
    result.rows.forEach((row, index) => { if (index % Math.max(1, Math.ceil(result.rows.length / 6)) !== 0) return; const text = svg('text', { x: x(index), y: height - 17, 'text-anchor': 'middle' }); text.textContent = label(row[0]).slice(0, 18); chart.append(text); });
    host.append(legend, chart); return;
  }
  if (tile.kind === 'donut') {
    const values = result.rows.map(row => Number(row[1] ?? 0)), total = values.reduce((a, b) => a + b, 0);
    if (values.some(v => v < 0) || total <= 0) { host.append(element('p', { className: 'empty-state', text: 'A donut chart requires nonnegative values and a positive total. Use a bar chart for signed values.' })); return; }
    const chart = svg('svg', { viewBox: '0 0 300 300', class: 'donut-chart', role: 'img', 'aria-label': tile.title });
    const radius = 105, circumference = 2 * Math.PI * radius;
    let offset = 0;
    const entries = element('div', { className: 'donut-legend' });
    result.rows.forEach((row, index) => {
      const fraction = values[index] / total;
      if (fraction > 0) {
        const mark = svg('circle', { cx: 150, cy: 150, r: radius, fill: 'none', stroke: colors[index % colors.length], 'stroke-width': 42, 'stroke-dasharray': `${fraction * circumference} ${circumference}`, 'stroke-dashoffset': -offset * circumference, transform: 'rotate(-90 150 150)' });
        const title = svg('title'); title.textContent = `${label(row[0])}: ${format(row[1])}`; mark.append(title); actionable(mark, title.textContent, drill(row, 0)); chart.append(mark);
      }
      offset += fraction;
      const entry = element('div', { className: 'donut-entry', text: `${label(row[0])} · ${format(row[1])} · ${(fraction * 100).toFixed(1)}%` });
      entry.style.setProperty('--series-color', colors[index % colors.length]); actionable(entry, entry.textContent, drill(row, 0)); entries.append(entry);
    });
    const text = svg('text', { x: 150, y: 156, 'text-anchor': 'middle', class: 'donut-total' }); text.textContent = format(total); chart.append(text);
    host.append(element('div', { className: 'donut-layout' }, [chart, entries]));
  }
}
