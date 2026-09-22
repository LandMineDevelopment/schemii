/** JSON tuples preserve component boundaries, types, and SQL NULLs. */
export const dimensionKey = values => JSON.stringify(values);
const valueLabel = value => value === null ? 'NULL' : String(value);

export function dimensionLabel(tile, row, start = 0) {
  const fields = tile.dimensions.slice(start);
  if (tile.dimensions.length === 1) return valueLabel(row[start]);
  return fields.map((field, index) => {
    const name = fields.filter(candidate => candidate.column === field.column).length > 1 ? `${field.table}.${field.column}` : field.column;
    const value = row[start + index];
    const quote = typeof value === 'string' && (/^[+-]?(?:\d|\.)/.test(value) || ['NULL', 'true', 'false', ''].includes(value) || /[·"\\\r\n]/.test(value));
    return `${name}: ${quote ? JSON.stringify(value) : valueLabel(value)}`;
  }).join(' · ');
}

/** Keep sparse points and original rows: grouping must not expand the preview budget. */
export function lineSeries(tile, result, measures) {
  const axis = [], positions = new Map(), groups = new Map();
  for (const row of result.rows) {
    const key = dimensionKey([row[0]]);
    if (!positions.has(key)) { positions.set(key, axis.length); axis.push(row[0]); }
    const groupKey = dimensionKey(row.slice(1, tile.dimensions.length));
    if (!groups.has(groupKey)) groups.set(groupKey, { label: tile.dimensions.length > 1 ? dimensionLabel(tile, row, 1) : '', rows: [] });
    groups.get(groupKey).rows.push({ index: positions.get(key), row });
  }
  const series = [];
  for (const group of groups.values()) {
    group.rows.sort((a, b) => a.index - b.index);
    measures.forEach((measure, index) => {
      const name = result.columns[measure.column]?.name || `Measure ${index + 1}`;
      series.push({ ...measure, label: group.label ? `${group.label} · ${name}` : name, points: group.rows });
    });
  }
  return { axis, series };
}

/** A missing category or NULL value breaks a line instead of inventing continuity. */
export function linePath(points, column, x, y) {
  let path = '', previous = -2;
  for (const { row, index } of points) {
    const value = row[column];
    if (value === null || value === '' || !Number.isFinite(Number(value))) { previous = -2; continue; }
    path += `${index === previous + 1 ? 'L' : 'M'}${x(index)},${y(Number(value))} `;
    previous = index;
  }
  return path;
}
