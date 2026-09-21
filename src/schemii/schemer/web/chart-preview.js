/** Bound chart DOM work independently from the downloaded result cache. */
export function chartPreview(tile, result, markLimit = 2000) {
  if (['detail', 'aggregate', 'kpi'].includes(tile.kind)) return result;
  const perGroup = tile.kind === 'donut' ? 1 : Math.max(1, tile.measures.length);
  const maxGroups = Math.max(1, Math.floor(markLimit / perGroup));
  return result.rows.length > maxGroups ? { ...result, rows: result.rows.slice(0, maxGroups), visualTruncated: true, cachedRows: result.rows.length } : result;
}
