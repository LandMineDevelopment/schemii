import assert from "node:assert/strict";
import test from "node:test";

import { syncWorkspaceToolbar } from "../../src/schemii/schemii/web/assets/workspace-toolbar.js";

function control(layers) {
  const attributes = new Set();
  return {
    dataset: { toolLayers: layers },
    toggleAttribute(name, force) {
      if (force) attributes.add(name);
      else attributes.delete(name);
    },
    hasAttribute(name) { return attributes.has(name); },
  };
}

function toolbar(controls) {
  return {
    dataset: {},
    label: null,
    setAttribute(name, value) { if (name === "aria-label") this.label = value; },
    querySelectorAll() { return controls; },
  };
}

test("workspace toolbar exposes only controls owned by the active layer", () => {
  const tables = control("tables");
  const views = control("views");
  const sql = control("sql");
  const sharedDesign = control("tables views");
  const sharedApplication = control("tables views sql");
  const root = toolbar([tables, views, sql, sharedDesign, sharedApplication]);

  syncWorkspaceToolbar(root, "views");

  assert.equal(root.dataset.workspace, "views");
  assert.equal(root.label, "Views tools");
  assert.equal(tables.hasAttribute("data-tool-active"), false);
  assert.equal(views.hasAttribute("data-tool-active"), true);
  assert.equal(sql.hasAttribute("data-tool-active"), false);
  assert.equal(sharedDesign.hasAttribute("data-tool-active"), true);
  assert.equal(sharedApplication.hasAttribute("data-tool-active"), true);
});

test("workspace toolbar rejects unknown contexts", () => {
  assert.throws(() => syncWorkspaceToolbar(toolbar([]), "migrations"), /Unknown workspace toolbar layer/);
});

