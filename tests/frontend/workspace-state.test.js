import assert from "node:assert/strict";
import test from "node:test";

import {
  catalogTableId,
  catalogViewId,
  createWorkspaceStateCommitter,
  selectedCatalogTable,
  selectedCatalogView,
} from "../../src/schemii/schemii/web/assets/workspace-state.js";

function projectDesign(workspace, design) {
  const views = design.content.views.map(view => ({
    designId: view.id,
    namespace: "desired",
    name: view.name,
  }));
  return {
    source: "design",
    database: workspace.name,
    tables: design.content.tables.map(table => ({
      designId: table.id,
      namespace: "desired",
      name: table.name,
    })),
    views: views.filter((_, index) => design.content.views[index].kind === "view"),
    materializedViews: views.filter((_, index) => design.content.views[index].kind === "materialized_view"),
  };
}

function design(tableName = "orders", viewName = "order_totals", viewKind = "view") {
  return {
    revision: 1,
    content: {
      tables: [{ id: "table-1", name: tableName }],
      views: [{ id: "view-1", name: viewName, kind: viewKind }],
    },
  };
}

test("one workspace commit projects a design, reconciles stable selections, and notifies once", () => {
  const notifications = [];
  const state = {
    activeWorkspace: { id: "workspace-1", name: "Design" },
    design: design(),
    designLayout: { revision: 1 },
    designHistory: { canUndo: true },
    databaseCatalog: null,
    catalog: projectDesign({ name: "Design" }, design()),
    selectedTableId: "table-1",
    selectedViewId: "view-1",
  };
  const commit = createWorkspaceStateCommitter({
    state,
    projectDesign,
    notify: transition => notifications.push(transition),
  });

  commit({
    design: design("purchases", "sales_totals", "materialized_view"),
    designLayout: { revision: 2 },
    designHistory: { canUndo: false },
  });

  assert.equal(notifications.length, 1);
  assert.equal(selectedCatalogTable(state.catalog, state.selectedTableId).name, "purchases");
  assert.equal(selectedCatalogView(state.catalog, state.selectedViewId).name, "sales_totals");
  assert.equal(selectedCatalogView(state.catalog, state.selectedViewId).catalogKind, "materialized_view");
  assert.equal(notifications[0].changed.catalog, true);
});

test("workspace commits clear only selections whose stable object disappeared", () => {
  const initial = design();
  const state = {
    activeWorkspace: { id: "workspace-1", name: "Design" },
    design: initial,
    designLayout: null,
    designHistory: null,
    databaseCatalog: null,
    catalog: projectDesign({ name: "Design" }, initial),
    selectedTableId: "table-1",
    selectedViewId: "view-1",
  };
  const commit = createWorkspaceStateCommitter({ state, projectDesign });
  commit({ design: { revision: 2, content: { tables: initial.content.tables, views: [] } } });

  assert.equal(state.selectedTableId, "table-1");
  assert.equal(state.selectedViewId, null);
});

test("live catalog selection keys remain explicit and do not collide across object kinds", () => {
  const table = { namespace: "public", name: "status" };
  const view = { namespace: "public", name: "status", catalogKind: "view" };
  assert.notEqual(catalogTableId(table), catalogViewId(view));
  assert.equal(catalogTableId({ ...table, designId: "table-1" }), "table-1");
  assert.equal(catalogViewId({ ...view, designId: "view-1" }), "view-1");
});
