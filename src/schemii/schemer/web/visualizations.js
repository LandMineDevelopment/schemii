import { element } from '#common/dom.js';
import { createResultGrid } from './scroll-results.js';
import { dimensionLabel, lineSeries, linePath } from './chart-dimensions.js';
import { barGroups, dimensionName, barMeasureName } from './bar-series.js';
import { chartPreview } from './chart-preview.js';
import { timeSeries } from './time-analysis.js';
import { markSelection } from './dashboard-state.js';
import { repetitionNotice } from '#model/repetition.js';
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
export function renderVisualization(host, tile, result, { onDrill, compact = false, modelId } = {}) {
  const fullResult = result;
  const options = [['values', 'Period values'], ...(tile.timeAnalysis && tile.timeAnalysis.comparison !== 'none' ? [['change', 'Absolute change'], ['percent', 'Percentage change']] : []), ...(tile.timeAnalysis?.runningTotal ? [['running', 'Running totals']] : [])];
  const metric = options.some(([value]) => value === host.dataset.timeMetric) ? host.dataset.timeMetric : 'values';
  host.dataset.timeMetric = metric;
  const selectedSeries = timeSeries(tile, metric);
  const series = tile.kind === 'donut' ? selectedSeries.slice(0, 1) : selectedSeries;
  result = chartPreview({ ...tile, measures: series }, result);
  const redraw = focusLabel => {
    const status = host.querySelector(':scope > .stream-progress');
    renderVisualization(host, tile, fullResult, { onDrill, compact, modelId });
    if (status) host.append(status);
    if (focusLabel) [...host.querySelectorAll('select')].find(node => node.getAttribute('aria-label') === focusLabel)?.focus();
  };
  host.replaceChildren();
  const repetition = repetitionNotice(result?.plan, { modelId });
  if (repetition) host.append(repetition);
  if (result.visualTruncated) host.append(element('p', { className: 'chart-partial-notice', text: `Chart shows the first ${result.rows.length} of ${result.cachedRows} cached groups. Export cached rows or download full results to see more.` }));
  if (!['detail', 'aggregate', 'kpi'].includes(tile.kind) && (result?.loading || result?.limitReached || result?.error || result?.visualTruncated)) host.append(element('p', { className: 'chart-partial-notice', text: tile.kind === 'donut' ? 'Partial result · percentages reflect displayed groups only.' : 'Partial result · showing received groups.' }));
  if (!result?.rows.length) { host.append(element('p', { className: 'empty-state', text: result?.loading ? 'Waiting for the first rows…' : result?.error ? 'The query did not complete.' : 'No matching rows for these slicers and filters.' })); return; }
  const drill = (row, index) => onDrill && index !== null ? () => onDrill(markSelection(tile, row, index)) : null;
  if (tile.kind === 'detail' || tile.kind === 'aggregate') {
    const grid = createResultGrid(result, (tr, rowIndex) => {
      if (onDrill && tile.kind === 'aggregate') [...tr.querySelectorAll('td')].forEach((td, columnIndex) => {
        if (columnIndex >= tile.dimensions.length && columnIndex < tile.dimensions.length + tile.measures.length) actionable(td, `View records for ${td.textContent}`, drill(result.rows[rowIndex], columnIndex - tile.dimensions.length));
      });
    });
    if (tile.timeAnalysis) host.append(element('p', { className: 'hint', text: 'Select a period’s base measure to see its contributing records. Comparison and running-total columns combine periods and do not drill through.' }));
    host.append(grid); return;
  }
  if (tile.kind === 'kpi') {
    const value = element('div', { className: 'kpi-value' }, [element('strong', { text: format(result.rows[0][0]) }), element('span', { text: result.columns[0].name })]);
    actionable(value, 'View contributing records', drill(result.rows[0], 0)); host.append(value); return;
  }
  if (tile.timeAnalysis && options.length > 1) {
    const select = element('select', { attrs: { 'aria-label': 'Displayed time measure' } });
    options.forEach(([value, text]) => select.append(element('option', { text, attrs: { value } })));
    select.value = metric;
    select.onclick = event => event.stopPropagation();
    select.onchange = () => { host.dataset.timeMetric = select.value; redraw('Displayed time measure'); };
    host.append(element('label', { className: 'time-chart-control' }, ['Show', select]));
    if (!compact) host.append(element('p', { className: 'hint', text: 'Only current-period values drill into records. Missing comparisons have no chart mark. Partial periods use the available filtered data.' }));
  }
  if (result.rows.some(row => series.some(({ column }) => row[column] !== null && !numeric(row[column])))) {
    host.append(element('p', { className: 'empty-state error', text: 'This chart needs numeric measures. Edit the tile to choose a numeric measure.' })); return;
  }
  const legend = element('div', { className: 'chart-legend' });
  const lines = tile.kind === 'line' ? lineSeries(tile, result, series) : null;
  (lines?.series || series).forEach(({ column, label: name }, index) => {
    const item = element('span', { text: name || result.columns[column]?.name || `Measure ${index + 1}` });
    item.style.setProperty('--series-color', colors[index % colors.length]); legend.append(item);
  });
  if (tile.kind === 'bar') {
    const groupIndex = Math.min(Math.max(0, Number(host.dataset.barGroupIndex) || 0), tile.dimensions.length - 1);
    const grouped = barGroups(tile, result, series, groupIndex);
    const multidimensional = tile.dimensions.length > 1;
    const toolbar = element('div', { className: 'bar-toolbar' });
    toolbar.onclick = event => event.stopPropagation();
    if (multidimensional) {
      const select = element('select', { attrs: { 'aria-label': 'Group bars by' } });
      tile.dimensions.forEach((_, index) => select.append(element('option', { text: dimensionName(tile, index), attrs: { value: index } })));
      select.value = String(groupIndex);
      select.onclick = event => event.stopPropagation();
      select.onchange = () => { host.dataset.barGroupIndex = select.value; redraw('Group bars by'); };
      toolbar.append(element('label', { className: 'bar-group-control' }, ['Group by', select]));
    }
    if (series.length === 1) toolbar.append(element('span', { className: 'bar-measure-caption', text: barMeasureName(tile, series[0].column) }));
    legend.replaceChildren();
    legend.classList.add('bar-series-legend');
    legend.setAttribute('aria-label', 'Bar series');
    grouped.series.forEach(item => {
      const entry = element('span', { text: item.label, title: item.label, dataset: { seriesKey: item.key } });
      entry.style.setProperty('--series-color', item.color); legend.append(entry);
    });
    const header = element('div', { className: 'bar-chart-header' }, [toolbar]);
    if (multidimensional || series.length > 1) {
      header.append(element('span', { className: 'bar-color-caption', text: grouped.seriesDimensions.map(index => dimensionName(tile, index)).join(' · ') || 'Measures' }), legend);
    }
    const chart = element('div', { className: `bar-chart${multidimensional ? ' grouped-bars' : ''}` });
    const values = result.rows.flatMap(row => series.map(({ column }) => Number(row[column] ?? 0)));
    const min = values.reduce((a, b) => Math.min(a, b), 0), max = values.reduce((a, b) => Math.max(a, b), 0), span = max - min || 1;
    for (const { label: groupLabel, bars } of grouped.groups) {
      const group = element('section', { className: 'bar-group', attrs: { 'aria-label': groupLabel } }, [element('div', { className: 'bar-label', text: groupLabel })]);
      for (const { row, series: item } of bars) {
        const { column, drill: measureIndex } = item;
        const n = Number(row[column] ?? 0);
        const bar = element('div', { className: 'bar-mark' });
        bar.style.left = `${(Math.min(0, n) - min) / span * 100}%`; bar.style.width = `${Math.abs(n) / span * 100}%`; if (!numeric(row[column])) bar.hidden = true; bar.style.background = item.color;
        const axis = element('span', { className: 'zero-axis' }); axis.style.left = `${-min / span * 100}%`;
        const track = element('div', { className: 'bar-track' }, [axis, bar]);
        const text = `${dimensionLabel(tile, row)}: ${format(row[column])}`;
        const rowNode = element('div', { className: 'bar-value-row', title: `${text} · ${barMeasureName(tile, column)}`, dataset: { seriesKey: item.key } });
        if (multidimensional || series.length > 1) rowNode.append(element('span', { className: 'bar-series-label', text: item.label, title: item.label }));
        rowNode.append(track, element('span', { className: 'bar-number', text: format(row[column]) }));
        rowNode.style.setProperty('--series-color', item.color);
        actionable(rowNode, `${text}${series.length > 1 ? ` · ${barMeasureName(tile, column)}` : ''}. View records`, drill(row, measureIndex)); group.append(rowNode);
      }
      chart.append(group);
    }
    host.append(header, chart); return;
  }
  if (tile.kind === 'line') {
    const width = compact ? Math.max(360, lines.axis.length * 28) : Math.max(760, lines.axis.length * 40), height = compact ? 270 : 450, pad = compact ? 34 : 52;
    const values = result.rows.flatMap(row => series.map(({ column }) => row[column])).filter(numeric).map(Number);
    const low = values.reduce((a, b) => Math.min(a, b), 0), high = values.reduce((a, b) => Math.max(a, b), 1), span = high - low || 1;
    const chart = svg('svg', { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'none', class: 'line-chart', role: 'img', 'aria-label': tile.title });
    chart.style.minWidth = `${width}px`;
    const x = index => pad + index / Math.max(1, lines.axis.length - 1) * (width - pad * 2);
    const y = n => height - pad - (n - low) / span * (height - pad * 2);
    for (let i = 0; i <= 4; i++) {
      const value = low + span * i / 4;
      chart.append(svg('line', { x1: pad, x2: width - pad, y1: y(value), y2: y(value), class: 'chart-gridline' }));
      const text = svg('text', { x: pad - 8, y: y(value) + 4, 'text-anchor': 'end' }); text.textContent = format(value); chart.append(text);
    }
    lines.series.forEach(({ column, drill: measureIndex, points, label: name }, seriesIndex) => {
      const path = linePath(points, column, x, y);
      chart.append(svg('path', { d: path, fill: 'none', stroke: colors[seriesIndex % colors.length], 'stroke-width': 2.5 }));
      points.forEach(({ row, index }) => {
        if (!numeric(row[column])) return;
        const point = svg('circle', { cx: x(index), cy: y(Number(row[column])), r: compact ? 4 : 6, fill: colors[seriesIndex % colors.length] });
        const title = svg('title'); title.textContent = `${dimensionLabel(tile, row)} · ${name}: ${format(row[column])}`; point.append(title);
        actionable(point, title.textContent, drill(row, measureIndex)); chart.append(point);
      });
    });
    lines.axis.forEach((value, index) => { if (index % Math.max(1, Math.ceil(lines.axis.length / 6)) !== 0) return; const text = svg('text', { x: x(index), y: height - 17, 'text-anchor': 'middle' }); text.textContent = label(value).slice(0, 18); chart.append(text); });
    host.append(legend, chart); return;
  }
  if (tile.kind === 'donut') {
    const { column, drill: measureIndex } = series[0];
    const values = result.rows.map(row => Number(row[column] ?? 0)), total = values.reduce((a, b) => a + b, 0);
    if (values.some(v => v < 0) || total <= 0) { host.append(element('p', { className: 'empty-state', text: 'A donut chart requires nonnegative values and a positive total. Use a bar chart for signed values.' })); return; }
    const chart = svg('svg', { viewBox: '0 0 300 300', class: 'donut-chart', role: 'img', 'aria-label': tile.title });
    const radius = 105, circumference = 2 * Math.PI * radius;
    let offset = 0;
    const entries = element('div', { className: 'donut-legend' });
    result.rows.forEach((row, index) => {
      const fraction = values[index] / total;
      if (fraction > 0) {
        const mark = svg('circle', { cx: 150, cy: 150, r: radius, fill: 'none', stroke: colors[index % colors.length], 'stroke-width': 42, 'stroke-dasharray': `${fraction * circumference} ${circumference}`, 'stroke-dashoffset': -offset * circumference, transform: 'rotate(-90 150 150)' });
        const title = svg('title'); title.textContent = `${dimensionLabel(tile, row)}: ${format(row[column])}`; mark.append(title); actionable(mark, title.textContent, drill(row, measureIndex)); chart.append(mark);
      }
      offset += fraction;
      const entry = element('div', { className: 'donut-entry', text: `${dimensionLabel(tile, row)} · ${format(row[column])} · ${(fraction * 100).toFixed(1)}%` });
      entry.style.setProperty('--series-color', colors[index % colors.length]); actionable(entry, entry.textContent, drill(row, measureIndex)); entries.append(entry);
    });
    const text = svg('text', { x: 150, y: 156, 'text-anchor': 'middle', class: 'donut-total' }); text.textContent = format(total); chart.append(text);
    host.append(element('div', { className: 'donut-layout' }, [chart, entries]));
  }
}
