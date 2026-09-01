import assert from "node:assert/strict";
import test from "node:test";

import {
  readWorkspaceNavigation,
  readWorkspacePreferences,
  updateWorkspacePreferences,
  workspaceNavigationHref,
} from "../../src/schemii/schemii/web/assets/workspace-navigation.js";

const WORKSPACE = `ws_${"a".repeat(32)}`;

class MemoryStorage {
  constructor() { this.values = new Map(); }
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, value); }
}

test("workspace navigation round-trips the active table and preserves unrelated parameters", () => {
  const href = workspaceNavigationHref("https://example.test/?campaign=preview#top", {
    workspaceId: WORKSPACE,
    layer: "tables",
    table: "order items",
  });

  assert.equal(href, `/?campaign=preview&workspace=${WORKSPACE}&table=order+items#top`);
  assert.deepEqual(readWorkspaceNavigation(`https://example.test${href}`), {
    workspaceId: WORKSPACE,
    layer: "tables",
    table: "order items",
    view: null,
    viewKind: null,
  });
});

test("workspace navigation represents view kind and removes stale object parameters", () => {
  const href = workspaceNavigationHref(
    `https://example.test/?workspace=${WORKSPACE}&table=orders`,
    {
      workspaceId: WORKSPACE,
      layer: "views",
      view: "monthly_sales",
      viewKind: "materialized_view",
    },
  );

  assert.equal(
    href,
    `/?workspace=${WORKSPACE}&layer=views&view=monthly_sales&viewKind=materialized_view`,
  );
  assert.deepEqual(readWorkspaceNavigation(`https://example.test${href}`), {
    workspaceId: WORKSPACE,
    layer: "views",
    table: null,
    view: "monthly_sales",
    viewKind: "materialized_view",
  });
});

test("invalid or absent workspace navigation safely returns the landing state", () => {
  assert.deepEqual(readWorkspaceNavigation("https://example.test/?workspace=not-a-workspace&layer=sql&table=orders"), {
    workspaceId: null,
    layer: "sql",
    table: null,
    view: null,
    viewKind: null,
  });
  assert.equal(
    workspaceNavigationHref(`https://example.test/?workspace=${WORKSPACE}&layer=views&view=orders`, {}),
    "/",
  );
});

test("workspace preferences retain only bounded camera and dock state", () => {
  const storage = new MemoryStorage();

  assert.equal(updateWorkspacePreferences(storage, WORKSPACE, {
    camera: { x: 125.5, y: -90, zoom: 1.25 },
  }), true);
  assert.equal(updateWorkspacePreferences(storage, WORKSPACE, { inspector: "minimized" }), true);
  assert.deepEqual(readWorkspacePreferences(storage, WORKSPACE), {
    camera: { x: 125.5, y: -90, zoom: 1.25 },
    inspector: "minimized",
  });

  updateWorkspacePreferences(storage, WORKSPACE, {
    camera: { x: Number.POSITIVE_INFINITY, y: 0, zoom: 4 },
    inspector: "floating",
  });
  assert.deepEqual(readWorkspacePreferences(storage, WORKSPACE), {
    camera: { x: 125.5, y: -90, zoom: 1.25 },
    inspector: "minimized",
  });
});

test("corrupt or unavailable browser storage never blocks startup", () => {
  assert.deepEqual(readWorkspacePreferences({ getItem() { throw new Error("blocked"); } }, WORKSPACE), {});
  assert.equal(updateWorkspacePreferences({
    getItem: () => null,
    setItem() { throw new Error("full"); },
  }, WORKSPACE, { inspector: "expanded" }), false);
});
