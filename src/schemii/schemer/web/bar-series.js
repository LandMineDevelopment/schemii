import { dimensionKey } from './chart-dimensions.js';

const palette = ['#65a9ff', '#f4b942', '#71d49a', '#ba92f6', '#f7828c', '#55c5c2', '#efa66b', '#98b4ee', '#c3ce71', '#e49cca', '#8cd1ee', '#bba784'];
export const barColor = index => palette[index] || `hsl(${(index * 137.508) % 360} 65% 68%)`;
const humanize = value => value.replace(/_/g, ' ').replace(/^./, letter => letter.toUpperCase());
export function dimensionName(tile, index) {
  const field = tile.dimensions[index];
  const name = humanize(field.column);
  return tile.dimensions.some((other, i) => i !== index && other.column === field.column) ? `${humanize(field.table)} · ${name}` : name;
}
export function barDimensionValue(tile, index, value) {
  if (value === null) return 'NULL';
  const field = tile.dimensions[index], time = tile.timeAnalysis;
  if (time && time.table === field.table && time.column === field.column && typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const date = new Date(`${value}T00:00:00Z`);
    if (Number.isFinite(date.getTime())) {
      const options = time.granularity === 'year' ? { year: 'numeric' } : time.granularity === 'month' ? { month: 'short', year: 'numeric' } : { month: 'short', day: 'numeric', year: 'numeric' };
      return new Intl.DateTimeFormat(undefined, { ...options, timeZone: 'UTC' }).format(date);
    }
  }
  if (typeof value === 'object') return JSON.stringify(value);
  // Keep NULL and literal "NULL", and numeric and text categories distinguishable.
  if (typeof value === 'string' && (['NULL', '', 'true', 'false'].includes(value) || /^[-+]?\d+(\.\d+)?$/.test(value) || /[·"\n\r]/.test(value))) return JSON.stringify(value);
  return String(value);
}
export function barMeasureName(tile, column) {
  const offset = tile.dimensions.length, count = tile.measures.length;
  const comparison = tile.timeAnalysis && tile.timeAnalysis.comparison !== 'none';
  const width = (comparison ? 3 : 0) + (tile.timeAnalysis?.runningTotal ? 1 : 0);
  const computed = column >= offset + count;
  const index = computed ? Math.floor((column - offset - count) / width) : column - offset;
  const measure = tile.measures[index];
  const operation = { count: 'Count', count_distinct: 'Distinct count', sum: 'Sum', avg: 'Average', min: 'Minimum', max: 'Maximum', none: 'Value' }[measure.aggregate] || humanize(measure.aggregate);
  const qualified = tile.measures.some(other => other.column === measure.column && other.aggregate === measure.aggregate && other.table !== measure.table);
  const name = `${operation} · ${qualified ? `${humanize(measure.table)} · ` : ''}${humanize(measure.column)}`;
  if (!computed) return name;
  const part = (column - offset - count) % width;
  const label = comparison ? [tile.timeAnalysis.comparison === 'prior_year' ? 'Prior year' : 'Previous period', 'Change', 'Change (%)', 'Running total'][part] : 'Running total';
  return `${name} · ${label}`;
}

/** Group without aggregating again; every mark retains its original query row. */
export function barGroups(tile, result, measures, groupIndex = 0) {
  const seriesDimensions = tile.dimensions.map((_, index) => index).filter(index => index !== groupIndex);
  const groups = new Map(), series = new Map();
  for (const row of result.rows) {
    const key = dimensionKey([row[groupIndex]]);
    if (!groups.has(key)) groups.set(key, { key, label: barDimensionValue(tile, groupIndex, row[groupIndex]), bars: [] });
    const tuple = seriesDimensions.map(index => row[index]);
    const tupleLabel = seriesDimensions.map(index => barDimensionValue(tile, index, row[index])).join(' · ');
    for (const measure of measures) {
      const seriesKey = dimensionKey([tuple, measure.column]);
      if (!series.has(seriesKey)) {
        const name = barMeasureName(tile, measure.column);
        series.set(seriesKey, { key: seriesKey, color: barColor(series.size), label: tupleLabel ? `${tupleLabel}${measures.length > 1 ? ` · ${name}` : ''}` : name, ...measure });
      }
      groups.get(key).bars.push({ row, series: series.get(seriesKey) });
    }
  }
  // Series retain their first-seen order and colors as streamed rows append.
  const order = new Map([...series.keys()].map((key, index) => [key, index]));
  for (const group of groups.values()) group.bars.sort((a, b) => order.get(a.series.key) - order.get(b.series.key));
  return { groups: [...groups.values()], series: [...series.values()], seriesDimensions };
}
