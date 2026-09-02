function owns(value, key) {
  return Object.prototype.hasOwnProperty.call(value, key);
}

function encoded(value) {
  return encodeURIComponent(String(value || ""));
}

export function catalogTableId(table) {
  if (!table) return null;
  if (table.designId) return table.designId;
  return `catalog-table:${encoded(table.namespace)}:${encoded(table.name)}`;
}

export function catalogViewId(view) {
  if (!view) return null;
  if (view.designId) return view.designId;
  return `catalog-view:${encoded(view.catalogKind)}:${encoded(view.namespace)}:${encoded(view.name)}`;
}

export function catalogViews(catalog) {
  if (!catalog) return [];
  return [
    ...(catalog.views || []).map(view => ({ ...view, catalogKind: "view" })),
    ...(catalog.materializedViews || []).map(view => ({ ...view, catalogKind: "materialized_view" })),
  ];
}

export function selectedCatalogTable(catalog, selectionId) {
  if (!catalog || !selectionId) return null;
  return (catalog.tables || []).find(table => catalogTableId(table) === selectionId) || null;
}

export function selectedCatalogView(catalog, selectionId) {
  if (!catalog || !selectionId) return null;
  return catalogViews(catalog).find(view => catalogViewId(view) === selectionId) || null;
}

export function navigatedCatalogTable(catalog, navigation) {
  const tables = catalog?.tables || [];
  return tables.find(table => navigation?.tableId && table.designId === navigation.tableId)
    || tables.find(table => table.name === navigation?.table)
    || null;
}

export function navigatedCatalogView(catalog, navigation) {
  const views = catalogViews(catalog);
  return views.find(view => navigation?.viewId && view.designId === navigation.viewId)
    || views.find(view => (
      view.name === navigation?.view && view.catalogKind === navigation?.viewKind
    ))
    || null;
}

export function transitionWorkspaceState(current, patch, { projectDesign }) {
  const activeWorkspace = owns(patch, "activeWorkspace")
    ? patch.activeWorkspace
    : current.activeWorkspace;
  const design = owns(patch, "design") ? patch.design : current.design;
  const designLayout = owns(patch, "designLayout") ? patch.designLayout : current.designLayout;
  const designHistory = owns(patch, "designHistory") ? patch.designHistory : current.designHistory;
  const databaseCatalog = owns(patch, "databaseCatalog")
    ? patch.databaseCatalog
    : current.databaseCatalog;

  let catalog = current.catalog;
  if (owns(patch, "catalog")) {
    catalog = patch.catalog;
  } else if (owns(patch, "design")) {
    catalog = design && activeWorkspace ? projectDesign(activeWorkspace, design) : null;
  }

  const requestedTableId = owns(patch, "selectedTableId")
    ? patch.selectedTableId
    : current.selectedTableId;
  const requestedViewId = owns(patch, "selectedViewId")
    ? patch.selectedViewId
    : current.selectedViewId;
  const selectedTableId = selectedCatalogTable(catalog, requestedTableId)
    ? requestedTableId
    : null;
  const selectedViewId = selectedCatalogView(catalog, requestedViewId)
    ? requestedViewId
    : null;

  const snapshot = {
    activeWorkspace,
    design,
    designLayout,
    designHistory,
    databaseCatalog,
    catalog,
    selectedTableId,
    selectedViewId,
  };
  const changed = Object.fromEntries(Object.keys(snapshot).map(key => [
    key,
    current[key] !== snapshot[key],
  ]));
  return { snapshot, changed };
}

export function createWorkspaceStateCommitter({ state, projectDesign, notify = () => {} }) {
  return function commitWorkspaceState(patch, metadata = {}) {
    const transition = transitionWorkspaceState(state, patch, { projectDesign });
    Object.assign(state, transition.snapshot);
    notify(transition, metadata);
    return transition;
  };
}
