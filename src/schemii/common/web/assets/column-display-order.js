function physicalColumns(table) {
  return [...(table?.columns || [])].sort((left, right) => left.ordinal - right.ordinal);
}

export function reconcileColumnDisplayOrder(table, preferredNames = []) {
  const physical = physicalColumns(table);
  const byName = new Map(physical.map(column => [column.name, column]));
  const ordered = [];
  const included = new Set();
  for (const name of preferredNames) {
    const column = byName.get(name);
    if (!column || included.has(name)) continue;
    ordered.push(column);
    included.add(name);
  }
  for (const column of physical) {
    if (included.has(column.name)) continue;
    ordered.push(column);
    included.add(column.name);
  }
  return ordered;
}

export function applyColumnDisplayOrders(catalog, columnOrders = [], modes = new Map()) {
  if (!catalog) return null;
  const preferences = new Map(columnOrders.map(order => [order.name, order.columns]));
  return {
    ...catalog,
    tables: catalog.tables.map(table => ({
      ...table,
      columns: modes.get(table.name) === "custom"
        ? reconcileColumnDisplayOrder(table, preferences.get(table.name))
        : physicalColumns(table),
    })),
  };
}

export function replaceColumnDisplayOrder(columnOrders, tableName, columns) {
  const replacement = { name: tableName, columns: [...columns] };
  const retained = columnOrders.filter(order => order.name !== tableName);
  return [...retained, replacement].sort((left, right) => left.name.localeCompare(right.name));
}

export function removeColumnDisplayOrder(columnOrders, tableName) {
  return columnOrders.filter(order => order.name !== tableName);
}
