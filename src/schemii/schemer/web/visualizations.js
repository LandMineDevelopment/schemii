import { createNumberPresentation, decorateNumber, formatKey, sumDecimalText, exactDecimal, numberFormatLegend } from './number-format.js';
import { visibleMeasures, chooseConstituent } from "./measures.js";
import { renderPivot, displayedPivot } from './pivot.js';
import { displayedColumns } from './result-cache.js';
import { element } from '#common/dom.js';
import { createResultGrid } from './scroll-results.js';
import { dimensionLabel, lineSeries, linePath } from './chart-dimensions.js';
import { barGroups, dimensionName, barMeasureName } from './bar-series.js';
import { chartPreview } from './chart-preview.js';
import { timeSeries } from './time-analysis.js';
import { markSelection } from './dashboard-state.js';
import { repetitionNotice } from '#model/repetition.js';
const colors = ['#f4b942', '#65a9ff', '#9b82f4', '#71d49a', '#f36b74', '#55c5c2'];
const displayedCharts = new WeakMap();
const label = value => value === null ? 'NULL' : String(value);
const numeric = value => value !== null && value !== '' && Number.isFinite(Number(value)) && (Number(value)!==0 || exactDecimal(value)?.coefficient===0n);
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
/** One selection boundary for marks and deliberate displayed-result capture.
 * Query output indices stay intact until the attachment projects its columns. */
export function visualizationSelection(host, tile, result) {
  const calculated = result?.plan?.calculatedMeasures || [];
  const options = [['values', 'Period values'], ...(tile.measures.length && tile.timeAnalysis && tile.timeAnalysis.comparison !== 'none' ? [['change', 'Absolute change'], ['percent', 'Percentage change']] : []), ...(tile.measures.length && tile.timeAnalysis?.runningTotal ? [['running', 'Running totals']] : [])];
  const metric = options.some(([value]) => value === host.dataset.timeMetric) ? host.dataset.timeMetric : 'values';
  const selected = [...timeSeries(tile, metric), ...(metric === 'values' ? calculated.filter(item => item.index != null).map(item => ({ column: item.index, drill: visibleMeasures(tile).findIndex(output => output.kind === 'calculated' && output.column === item.id) })) : [])];
  const series = tile.kind === 'donut' ? selected.slice(0, 1) : selected;
  const presentationTile = { ...tile, outputMeasureNames: Object.fromEntries(calculated.filter(item => item.index != null).map(item => [item.index, item.displayLabel || item.label])) };
  return { calculated, options, metric, series, tile: presentationTile, preview: chartPreview({ ...presentationTile, measures: series }, result) };
}
export function rememberDisplayedVisualization(host, tile, result, present = createNumberPresentation(tile, result)) {
  const selection = visualizationSelection(host, tile, result);
  displayedCharts.set(host, { tile, result, selection, present, rowCount: selection.preview.rows.length });
  return selection;
}
export function displayedVisualization(host, tile, result) {
  if (tile.kind === 'pivot') return displayedPivot(host, { ...tile, measures: visibleMeasures(tile) }, result);
  const mounted = displayedCharts.get(host);
  if (mounted) { tile = mounted.tile; result = mounted.result; }
  const { metric, series, preview } = mounted?.selection || visualizationSelection(host, tile, result);
  const present = mounted?.present || createNumberPresentation(tile, result);
  const visibleRows = preview.rows.slice(0, mounted?.rowCount ?? preview.rows.length);
  if (!result.columns.length) return { ...preview, metric };
  if (['detail', 'aggregate'].includes(tile.kind)) return { ...preview, columns: displayedColumns(result.columns, present), rows: visibleRows, metric: 'values' };
  const comparison = result.plan?.kpiComparison;
  const indices = tile.kind === 'kpi' ? [0, ...(comparison?.indices || [])] : [...tile.dimensions.map((_, index) => index), ...series.map(item => item.column)];
  const names = comparison ? [comparison.label, 'Absolute variance', 'Percentage variance', `KPI assessment (${({ higher: 'higher is better', lower: 'lower is better', equal: 'exactly at baseline is better' })[comparison.favorable]})`] : [];
  const rows = tile.kind === 'kpi' ? visibleRows.slice(0, 1) : visibleRows;
  return { ...preview, metric, cachedRows: result.rows.length, barGroupIndex: Math.max(0, Math.min(Number(host.dataset.barGroupIndex) || 0, tile.dimensions.length - 1)),
    columns: displayedColumns(result.columns, present, indices).map((column, position) => ({ ...column, ...(tile.kind === 'kpi' && position ? { name: names[position - 1] } : {}) })), rows: rows.map(row => indices.map(index => row[index])) };
}
export function renderVisualization(host, tile, result, { onDrill, compact = false, modelId } = {}) {
  result={...result,rows:result?.rows || [],columns:result?.columns || []};
  const present=createNumberPresentation(tile,result), format=(value,index)=>present(value,index).text;
  const formattedDimensions=new Set((tile.numberFormats || []).map(setting=>formatKey(setting.output)));
  const rowLabel=row=>tile.dimensions.map((field,index)=>formattedDimensions.has(formatKey({...field,kind:'value'}))?present(row[index],index).text:dimensionLabel({...tile,dimensions:[field]},[row[index]])).join(' · ');
  const originalTile=tile, calculated=result?.plan?.calculatedMeasures || [];
  const select=(selection)=>{
    const item=calculated.find(item=>item.id===visibleMeasures(originalTile)[selection.measureIndex]?.column && visibleMeasures(originalTile)[selection.measureIndex]?.kind==='calculated');
    if(item) chooseConstituent(item,operand=>onDrill?.({...selection,constituent:operand}),element);
    else onDrill?.(selection);
  };
  if (tile.kind === 'pivot') { renderPivot(host,{...tile,measures:visibleMeasures(tile)},result,{onDrill:onDrill?select:null,compact}); return; }
  const fullResult = result;
  const selection = rememberDisplayedVisualization(host, tile, result, present);
  const { options, metric, series, tile: presentationTile, preview } = selection;
  host.dataset.timeMetric = metric;
  tile = { ...presentationTile, formatDimension: (index, value) => formattedDimensions.has(formatKey({ ...originalTile.dimensions[index], kind: 'value' })) ? present(value, index).text : undefined };
  result = preview;
  const redraw = focusLabel => {
    const status = host.querySelector(':scope > .stream-progress');
    renderVisualization(host, tile, fullResult, { onDrill, compact, modelId });
    if (status) host.append(status);
    if (focusLabel) [...host.querySelectorAll('select')].find(node => node.getAttribute('aria-label') === focusLabel)?.focus();
  };
  host.replaceChildren();
  const formatLegend=numberFormatLegend(tile,result,element);if(formatLegend) host.append(formatLegend);
  const repetition = repetitionNotice(result?.plan, { modelId });
  if (repetition) host.append(repetition);
  if(calculated.length && (result?.loading || result?.limitReached || result?.visualTruncated)) host.append(element('p',{className:'chart-partial-notice',text:'Calculated values use the complete query population, including groups outside this preview. Chart geometry and cached-row exports show received groups only.'}));
  if (result.visualTruncated) host.append(element('p', { className: 'chart-partial-notice', text: `Chart shows the first ${result.rows.length} of ${result.cachedRows} cached groups. Export cached rows or download full results to see more.` }));
  if (!['detail', 'aggregate', 'kpi'].includes(tile.kind) && (result?.loading || result?.limitReached || result?.error || result?.visualTruncated)) host.append(element('p', { className: 'chart-partial-notice', text: tile.kind === 'donut' ? 'Partial result · percentages reflect displayed groups only.' : 'Partial result · showing received groups.' }));
  if (!result?.rows.length) { host.append(element('p', { className: 'empty-state', text: result?.loading ? 'Waiting for the first rows…' : result?.error ? 'The query did not complete.' : 'No matching rows for these slicers and filters.' })); return; }
  const drill = (row, index) => onDrill && index !== null ? () => select(markSelection(tile, row, index)) : null;
  if (tile.kind === 'detail' || tile.kind === 'aggregate') {
    const grid = createResultGrid(result, (tr, rowIndex) => {
      [...tr.querySelectorAll('td')].forEach((td,index)=>decorateNumber(td,present(result.rows[rowIndex][index],index)));
      if (onDrill && tile.kind === 'aggregate') [...tr.querySelectorAll('td')].forEach((td, columnIndex) => {
        const baseIndex=columnIndex-tile.dimensions.length;
        const calculation=calculated.find(item=>item.index===columnIndex);
        const index=calculation?visibleMeasures(originalTile).findIndex(item=>item.kind==='calculated'&&item.column===calculation.id):baseIndex>=0&&baseIndex<originalTile.measures.length?baseIndex:null;
        if(index!==null) actionable(td, `View records for ${td.getAttribute("aria-label") || td.textContent}`, drill(result.rows[rowIndex],index));
      });
    });
    if (tile.timeAnalysis) host.append(element('p', { className: 'hint', text: 'Select a period’s base measure to see its contributing records. Comparison and running-total columns combine periods and do not drill through.' }));
    host.append(grid); return;
  }
  if (tile.kind === 'kpi') {
    const value = element('div', { className: 'kpi-value' }, [element('strong', { text: format(result.rows[0][0],0) }), element('span', { text: result.columns[0].name })]);
    decorateNumber(value.querySelector('strong'),present(result.rows[0][0],0));
    actionable(value, tile.numberFormats?.length?`${present(result.rows[0][0],0).text} · ${result.columns[0].name}. View contributing records`:'View contributing records', drill(result.rows[0], 0)); host.append(value);
    const comparison=result.plan?.kpiComparison;
    if(comparison) {
      const [baseline,absolute,percent,status]=comparison.indices.map(index=>result.rows[0][index]);
      const meaning={higher:'higher is better',lower:'lower is better',equal:'exactly at baseline is better'}[comparison.favorable];
      const comparisonValue=(label,raw,index)=>{const number=element('span');decorateNumber(number,present(raw,index));return element('p',{},[label+': ',number]);};
      host.append(element('section',{className:'kpi-comparison',attrs:{'aria-label':'KPI target and variance'}},[
        comparisonValue(comparison.label,baseline,comparison.indices[0]),
        comparisonValue('Absolute variance',absolute,comparison.indices[1]),
        comparisonValue('Percentage variance',percent,comparison.indices[2]),
        element('strong',{text:`${status==='favorable'?'✓ ':status==='unfavorable'?'! ':''}${status || 'unavailable'} · ${meaning}`}),
      ]));
    }
    return;
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
    host.append(element('p', { className: 'empty-state error', text: 'This chart needs numeric measures. Very large exact values can exceed finite chart geometry; use a table or RAW CSV, or edit the measure.' })); return;
  }
  const legend = element('div', { className: 'chart-legend' });
  const lines = tile.kind === 'line' ? lineSeries(tile, result, series) : null;
  (lines?.series || series).forEach(({ column, label: name }, index) => {
    const item = element('span', { text: (name || result.columns[column]?.name || `Measure ${index + 1}`)+(present(0,column).description?` · ${present(0,column).description}`:'') });
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
    if (series.length === 1) toolbar.append(element('span', { className: 'bar-measure-caption', text: barMeasureName(tile, series[0].column)+(present(0,series[0].column).description?` · ${present(0,series[0].column).description}`:'') }));
    legend.replaceChildren();
    legend.classList.add('bar-series-legend');
    legend.setAttribute('aria-label', 'Bar series');
    grouped.series.forEach(item => {
      const seriesLabel=item.label+(present(0,item.column).description?` · ${present(0,item.column).description}`:'');
      const entry = element('span', { text: seriesLabel, title: seriesLabel, dataset: { seriesKey: item.key } });
      entry.style.setProperty('--series-color', item.color); legend.append(entry);
    });
    const header = element('div', { className: 'bar-chart-header' }, [toolbar]);
    if (multidimensional || series.length > 1) {
      header.append(element('span', { className: 'bar-color-caption', text: grouped.seriesDimensions.map(index => dimensionName(tile, index)).join(' · ') || 'Measures' }), legend);
    }
    const chart = element('div', { className: `bar-chart${multidimensional ? ' grouped-bars' : ''}` });
    const values = result.rows.flatMap(row => series.map(({ column }) => Number(row[column] ?? 0)));
    const scale=values.reduce((max,value)=>Math.max(max,Math.abs(value)),1);
    const min = values.reduce((a,b)=>Math.min(a,b/scale),0),max=values.reduce((a,b)=>Math.max(a,b/scale),0),span=max-min || 1;
    for (const { label: groupLabel, bars } of grouped.groups) {
      const group = element('section', { className: 'bar-group', attrs: { 'aria-label': groupLabel } }, [element('div', { className: 'bar-label', text: groupLabel })]);
      for (const { row, series: item } of bars) {
        const { column, drill: measureIndex } = item;
        const n = Number(row[column] ?? 0)/scale;
        const bar = element('div', { className: 'bar-mark' });
        bar.style.left = `${(Math.min(0, n) - min) / span * 100}%`; bar.style.width = `${Math.abs(n) / span * 100}%`; if (!numeric(row[column])) bar.hidden = true; bar.style.background = item.color;
        const axis = element('span', { className: 'zero-axis' }); axis.style.left = `${-min / span * 100}%`;
        const track = element('div', { className: 'bar-track' }, [axis, bar]);
        const text = `${rowLabel(row)}: ${format(row[column],column)}`;
        const rowNode = element('div', { className: 'bar-value-row', title: `${text} · ${barMeasureName(tile, column)}`, dataset: { seriesKey: item.key } });
        if (multidimensional || series.length > 1) rowNode.append(element('span', { className: 'bar-series-label', text: item.label, title: item.label }));
        const number=element('span',{className:'bar-number'});decorateNumber(number,present(row[column],column));rowNode.append(track,number);
        rowNode.style.setProperty('--series-color', item.color);
        actionable(rowNode, `${text}${series.length > 1 ? ` · ${barMeasureName(tile, column)}` : ''}. View records`, drill(row, measureIndex)); group.append(rowNode);
      }
      chart.append(group);
    }
    host.append(header, chart); return;
  }
  if (tile.kind === 'line') {
    const width = compact ? Math.max(360, lines.axis.length * 28) : Math.max(760, lines.axis.length * 40), height = compact ? 270 : 450, pad = Math.min(190,Math.max(compact?48:60,...series.map(item=>present(result.rows[0][item.column],item.column,{suppressRules:true}).text.length*6)));
    const values = result.rows.flatMap(row => series.map(({ column }) => row[column])).filter(numeric).map(Number);
    const scale=values.reduce((max,value)=>Math.max(max,Math.abs(value)),1);
    const low = values.reduce((a,b)=>Math.min(a,b/scale),0),high=values.reduce((a,b)=>Math.max(a,b/scale),1/scale),span=high-low || 1;
    const chart = svg('svg', { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'none', class: 'line-chart', role: 'img', 'aria-label': tile.title });
    chart.style.minWidth = `${width}px`;
    const x = index => pad + index / Math.max(1, lines.axis.length - 1) * (width - pad * 2);
    const y = n => height - pad - (n/scale - low) / span * (height - pad * 2);
    for (let i = 0; i <= 4; i++) {
      const value = (low + span * i / 4)*scale;
      chart.append(svg('line', { x1: pad, x2: width - pad, y1: y(value), y2: y(value), class: 'chart-gridline' }));
      const text = svg('text', { x: pad - 8, y: y(value) + 4, 'text-anchor': 'end' }); text.textContent = present(value,series.every(item=>present(0,item.column).description===present(0,series[0].column).description)?series[0].column:-1,{suppressRules:true}).text; chart.append(text);
    }
    lines.series.forEach(({ column, drill: measureIndex, points, label: name }, seriesIndex) => {
      const path = linePath(points, column, x, y);
      chart.append(svg('path', { d: path, fill: 'none', stroke: colors[seriesIndex % colors.length], 'stroke-width': 2.5 }));
      points.forEach(({ row, index }) => {
        if (!numeric(row[column])) return;
        const point = svg('circle', { cx: x(index), cy: y(Number(row[column])), r: compact ? 4 : 6, fill: colors[seriesIndex % colors.length] });
        const title = svg('title'); title.textContent = `${rowLabel(row)} · ${name}: ${format(row[column],column)}`; point.append(title);
        const indicator=present(row[column],column);if(indicator.indicator){const badge=svg('text',{x:x(index)+8,y:y(Number(row[column]))-8,class:'chart-condition'});badge.textContent=indicator.indicator.split(' ')[0];badge.setAttribute('aria-label',indicator.text);badge.setAttribute('fill',{positive:'#91d7ae',negative:'#f3a4ad',caution:'#f1ce83',neutral:'#c4c9d2'}[indicator.tone]);chart.append(badge);}
        actionable(point, title.textContent, drill(row, measureIndex)); chart.append(point);
      });
    });
    lines.axis.forEach((value, index) => { if (index % Math.max(1, Math.ceil(lines.axis.length / 6)) !== 0) return; const text = svg('text', { x: x(index), y: height - 17, 'text-anchor': 'middle' }); text.textContent = (tile.formatDimension?.(0,value) ?? label(value)).slice(0,24); chart.append(text); });
    if(new Set(series.map(item=>present(0,item.column).description)).size>1) host.append(element('p',{className:'hint',text:'Mixed units · axis uses raw-scale coordinates; each series keeps its own formatted labels.'}));
    host.append(legend, chart); return;
  }
  if (tile.kind === 'donut') {
    const { column, drill: measureIndex } = series[0];
    const rawNumbers=result.rows.map(row=>Number(row[column] ?? 0)),scale=rawNumbers.reduce((max,value)=>Math.max(max,value),1);
    const values=rawNumbers.map(value=>value/scale),total=values.reduce((a,b)=>a+b,0);
    const totalText=sumDecimalText(result.rows.map(row=>row[column]));
    if (values.some(v => v < 0) || total <= 0) { host.append(element('p', { className: 'empty-state', text: 'A donut chart requires nonnegative values and a positive total. Use a bar chart for signed values.' })); return; }
    const chart = svg('svg', { viewBox: '0 0 300 300', class: 'donut-chart', role: 'img', 'aria-label': tile.title });
    const radius = 105, circumference = 2 * Math.PI * radius;
    let offset = 0;
    const entries = element('div', { className: 'donut-legend' });
    result.rows.forEach((row, index) => {
      const fraction = values[index] / total;
      if (fraction > 0) {
        const mark = svg('circle', { cx: 150, cy: 150, r: radius, fill: 'none', stroke: colors[index % colors.length], 'stroke-width': 42, 'stroke-dasharray': `${fraction * circumference} ${circumference}`, 'stroke-dashoffset': -offset * circumference, transform: 'rotate(-90 150 150)' });
        const title = svg('title'); title.textContent = `${rowLabel(row)}: ${format(row[column],column)}`; mark.append(title); actionable(mark, title.textContent, drill(row, measureIndex)); chart.append(mark);
      }
      offset += fraction;
      const entry = element('div', { className: 'donut-entry', text: `${rowLabel(row)} · ${format(row[column],column)} · ${(fraction * 100).toFixed(1)}%` });
      entry.style.setProperty('--series-color', colors[index % colors.length]); actionable(entry, entry.textContent, drill(row, measureIndex)); entries.append(entry);
    });
    const text = svg('text', { x: 150, y: 156, 'text-anchor': 'middle', class: 'donut-total' }); text.textContent = totalText===null?'Total unavailable':present(totalText,column,{suppressRules:true}).text; chart.append(text);const totalTitle=svg('title');totalTitle.textContent='Sum of displayed groups, not a full-query total.';text.append(totalTitle);
    host.append(element('div', { className: 'donut-layout' }, [chart, entries]));
  }
}
