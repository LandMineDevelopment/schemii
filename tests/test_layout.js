const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
assert.match(source, /"X-Schemii-Layout-Token": record\.layoutToken/);
assert.match(source, /"X-Schemii-Layout-Protocol": "2"/);
assert.match(source, /layoutToken: record\?\.layoutToken/);
assert.match(source, /table\.x = Number\.isFinite\(layout\?\.x\) \? layout\.x : Number\.isFinite\(table\.x\)/);
assert.match(source, /table\.y = Number\.isFinite\(layout\?\.y\) \? layout\.y : Number\.isFinite\(table\.y\)/);
const storageStart = source.indexOf("function clone(value)");
const storageEnd = source.indexOf("function readSchemaLibrary()");
const migrationStart = source.indexOf("function migrateSchema(schema)");
const migrationEnd = source.indexOf("function sqlName(value)");
const preserveStart = source.indexOf("function preserveTableLayout(importedSchema, previousSchema)");
const preserveEnd = source.indexOf("async function importPostgresSchema()", preserveStart);
const initializationStart = source.indexOf("async function initializeSchemaLibrary()");
const initializationEnd = source.indexOf("function uid(prefix)", initializationStart);
const activationStart = source.indexOf("function activateSchemaRecord(");
const activationEnd = source.indexOf("async function reloadActiveSchemaRecord(", activationStart);
const openSchemaStart = source.indexOf("async function openSchema(");
const openSchemaEnd = source.indexOf("async function deleteSavedSchema(", openSchemaStart);
for (const [name, marker] of Object.entries({ storageStart, storageEnd, migrationStart, migrationEnd, preserveStart, preserveEnd, initializationStart, initializationEnd, activationStart, activationEnd, openSchemaStart, openSchemaEnd })) {
  assert.notEqual(marker, -1, `${name} marker is missing`);
}
assert.doesNotMatch(source.slice(initializationStart, initializationEnd), /fitDiagram\s*\(/, "startup must preserve the saved viewport");
const openSchemaSource = source.slice(openSchemaStart, openSchemaEnd);
const activationSource = source.slice(activationStart, activationEnd);
assert.match(openSchemaSource, /\{ fit = false \}/, "opening a saved schema must preserve its viewport by default");
assert.match(activationSource, /nextTableView = clone\(nextSchema\.layout\.layers\.tables\.viewport\)/, "activating a saved schema must capture its table viewport");
assert.match(activationSource, /view = nextTableView/, "activating a saved schema must restore its table viewport");
assert.match(activationSource, /viewsView = nextViewsView/, "activating a saved schema must restore its Views viewport");
assert.match(activationSource, /viewsObjects = nextViewsObjects/, "activating a saved schema must restore its Views objects");
assert.doesNotMatch(activationSource, /fitViewsCanvas\s*\(/, "activating a saved schema must not fit the Views canvas");
assert.match(source.slice(initializationStart, initializationEnd), /restoreViewsRuntimeLayout\(schema\)/, "startup must restore the Views layout without fitting it");
assert.match(source, /function persistSchemaRecord[\s\S]*schemaForStorage\([\s\S]*\{ views: \{ viewport: viewsView, objects: viewsObjects \} \}/, "the revision/layout-token queue must persist the active Views runtime overlay");

const context = vm.createContext({ JSON });
vm.runInContext(`
  const COLORS = ["#f4b942", "#65a9ff"];
  let nextId = 0;
  function uid(prefix) { nextId += 1; return prefix + "_" + nextId; }
  function defaultPrimaryKeyName(tableName) { return tableName + "_pkey"; }
  ${source.slice(storageStart, storageEnd)}
  ${source.slice(migrationStart, migrationEnd)}
  ${source.slice(preserveStart, preserveEnd)}
  globalThis.schemaForStorage = schemaForStorage;
  globalThis.migrateSchema = migrateSchema;
  globalThis.preserveTableLayout = preserveTableLayout;
`, context);

const runtime = {
  projectName: "Layout test",
  postgres: { namespace: "public" },
  tables: [{
    id: "table_accounts",
    name: "accounts",
    namespace: "public",
    x: 321,
    y: 654,
    color: "#65a9ff",
    postgres: { liveOid: 42 },
    columns: [
      { id: "column_created", name: "created_at", type: "timestamp", ordinal: 3 },
      { id: "column_id", name: "id", type: "uuid", primary: true, ordinal: 1 },
      { id: "column_email", name: "email", type: "text", ordinal: 2 }
    ],
    uniqueConstraints: []
  }],
  relationships: [],
  functions: [],
  views: []
};

const stored = context.schemaForStorage(runtime, { x: 11, y: 22, zoom: 1.25 });
assert.equal(stored.tables[0].x, undefined);
assert.equal(stored.tables[0].y, undefined);
assert.equal(stored.tables[0].color, undefined);
assert.deepEqual(JSON.parse(JSON.stringify(stored.layout.layers.tables.objects.table_accounts)), {
  x: 321,
  y: 654,
  color: "#65a9ff",
  namespace: "public",
  name: "accounts",
  liveOid: 42
});

const hydrated = context.migrateSchema(JSON.parse(JSON.stringify(stored)));
assert.equal(hydrated.tables[0].x, 321);
assert.equal(hydrated.tables[0].y, 654);
assert.equal(hydrated.tables[0].color, "#65a9ff");
assert.deepEqual(JSON.parse(JSON.stringify(hydrated.layout.layers.tables.viewport)), { x: 11, y: 22, zoom: 1.25 });

const versionOne = JSON.parse(JSON.stringify(runtime));
versionOne.layout = {
  version: 1,
  customLayout: { retained: true },
  tables: { table_accounts: { x: 12, y: 34, color: "#f4b942", customObject: "retained" } },
  view: { x: 5, y: 6, zoom: 0.75, grid: "retained" }
};
const storedV1 = context.schemaForStorage(context.migrateSchema(versionOne));
assert.equal(storedV1.layout.version, 2);
assert.deepEqual(JSON.parse(JSON.stringify(storedV1.layout.customLayout)), { retained: true });
assert.equal(storedV1.layout.layers.tables.objects.table_accounts.customObject, "retained");
assert.deepEqual(JSON.parse(JSON.stringify(storedV1.layout.layers.tables.viewport)), { x: 5, y: 6, zoom: 0.75, grid: "retained" });

const versionTwo = JSON.parse(JSON.stringify(runtime));
versionTwo.layout = {
  version: 2,
  customLayout: { retained: "v2" },
  layers: {
    tables: {
      customLayer: 1,
      objects: { table_accounts: { x: 21, y: 43, color: "#65a9ff", customObject: 2 } },
      viewport: { x: 7, y: 8, zoom: 1.5, customViewport: 3 }
    },
    views: {
      customLayer: 4,
      objects: { view_summary: { x: 88, y: 99, color: "#123456", customObject: 5 } },
      viewport: { x: 9, y: 10, zoom: 0.9, customViewport: 6 }
    },
    customLayer: { retained: 7 }
  }
};
const migratedV2 = context.migrateSchema(versionTwo);
assert.equal(migratedV2.tables[0].x, 21);
assert.equal(migratedV2.tables[0].y, 43);
assert.equal(migratedV2.tables[0].color, "#65a9ff");
const storedV2 = context.schemaForStorage(migratedV2, { x: 70, y: 80, zoom: 2 });
assert.deepEqual(JSON.parse(JSON.stringify(storedV2.layout.customLayout)), { retained: "v2" });
assert.deepEqual(JSON.parse(JSON.stringify(storedV2.layout.layers.customLayer)), { retained: 7 });
assert.equal(storedV2.layout.layers.tables.customLayer, 1);
assert.equal(storedV2.layout.layers.tables.objects.table_accounts.customObject, 2);
assert.equal(storedV2.layout.layers.tables.viewport.customViewport, 3);
assert.deepEqual(JSON.parse(JSON.stringify(storedV2.layout.layers.views)), JSON.parse(JSON.stringify(versionTwo.layout.layers.views)));

const viewsOnly = context.schemaForStorage(migratedV2, null, {
  views: {
    viewport: { x: 101, y: 202, zoom: 1.1 },
    objects: { view_summary: { x: 303, y: 404 } }
  }
});
const tablesBeforeViewsWrite = context.schemaForStorage(migratedV2).layout.layers.tables;
assert.deepEqual(JSON.parse(JSON.stringify(viewsOnly.layout.layers.tables)), JSON.parse(JSON.stringify(tablesBeforeViewsWrite)), "Views writes preserve the complete Tables layer");
assert.deepEqual(JSON.parse(JSON.stringify(viewsOnly.layout.layers.views.viewport)), { x: 101, y: 202, zoom: 1.1, customViewport: 6 });
assert.deepEqual(JSON.parse(JSON.stringify(viewsOnly.layout.layers.views.objects.view_summary)), {
  x: 303, y: 404, color: "#123456", customObject: 5
});
assert.equal(viewsOnly.layout.layers.views.customLayer, 4);

const both = context.schemaForStorage(migratedV2, { x: 501, y: 502, zoom: .8 }, {
  tables: { customLayerUpdate: true },
  views: { viewport: { x: 601, y: 602, zoom: .7 }, objects: { stage_one: { x: 700, y: 800, custom: true } } }
});
const roundTrip = context.migrateSchema(JSON.parse(JSON.stringify(both)));
assert.deepEqual(JSON.parse(JSON.stringify(roundTrip.layout.layers.tables.viewport)), { x: 501, y: 502, zoom: .8, customViewport: 3 });
assert.equal(roundTrip.layout.layers.tables.customLayerUpdate, true);
assert.deepEqual(JSON.parse(JSON.stringify(roundTrip.layout.layers.views.viewport)), { x: 601, y: 602, zoom: .7, customViewport: 6 });
assert.deepEqual(JSON.parse(JSON.stringify(roundTrip.layout.layers.views.objects.stage_one)), { x: 700, y: 800, custom: true });
assert.equal(roundTrip.layout.layers.views.objects.view_summary.customObject, 5);

const previousWithViews = JSON.parse(JSON.stringify(runtime));
previousWithViews.layout = JSON.parse(JSON.stringify(migratedV2.layout));
const renamed = context.preserveTableLayout({
  postgres: { namespace: "public" },
  tables: [{
    name: "customers", namespace: "public", x: 0, y: 0, color: "#f4b942", postgres: { liveOid: 42 },
    columns: [
      { id: "fresh_id", name: "id", type: "uuid", ordinal: 1, refreshed: true },
      { id: "fresh_email", name: "contact_email", type: "text", ordinal: 2, refreshed: true },
      { id: "fresh_created", name: "created_at", type: "timestamp", ordinal: 3, refreshed: true },
      { id: "fresh_extra", name: "external_column", type: "text", ordinal: 4, refreshed: true }
    ]
  }]
}, previousWithViews);
assert.equal(renamed.tables[0].x, 321);
assert.equal(renamed.tables[0].y, 654);
assert.equal(renamed.tables[0].color, "#65a9ff");
assert.deepEqual(Array.from(renamed.tables[0].columns, column => column.name), [
  "created_at", "id", "contact_email", "external_column"
]);
assert.equal(renamed.tables[0].columns.every(column => column.refreshed), true);
assert.deepEqual(JSON.parse(JSON.stringify(renamed.layout.layers.views)), JSON.parse(JSON.stringify(migratedV2.layout.layers.views)), "PostgreSQL semantic refresh preserves the complete Views layer exactly");

console.log("Layout overlay tests passed");
