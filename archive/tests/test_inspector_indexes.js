const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/web/app.js", "utf8");
const keyStart = source.indexOf("function tableDatabaseObjectKey(kind)");
const keyEnd = source.indexOf("function findDatabaseObject(reference)", keyStart);
const start = source.indexOf("function describeInspectorIndex(index)");
const end = source.indexOf("function renderInspectorIndex(index)", start);
assert.notEqual(keyStart, -1, "database object collection key marker is missing");
assert.notEqual(keyEnd, -1, "database object collection key end marker is missing");
assert.notEqual(start, -1, "index description marker is missing");
assert.notEqual(end, -1, "index description end marker is missing");

const context = vm.createContext({ Boolean });
vm.runInContext(`${source.slice(keyStart, keyEnd)}\nglobalThis.tableDatabaseObjectKey = tableDatabaseObjectKey;`, context);
vm.runInContext(`${source.slice(start, end)}\nglobalThis.describeInspectorIndex = describeInspectorIndex;\nglobalThis.describeInspectorCheck = describeInspectorCheck;`, context);

assert.equal(context.tableDatabaseObjectKey("check"), "checks");
assert.equal(context.tableDatabaseObjectKey("index"), "indexes");
assert.equal(context.tableDatabaseObjectKey("trigger"), "triggers");
assert.equal(context.describeInspectorCheck({ definition: "CHECK (\n  quantity >= 0\n)" }), "CHECK ( quantity >= 0 )");

const partialUnique = context.describeInspectorIndex({
  unique: true,
  method: "btree",
  definition: "CREATE UNIQUE INDEX request_status_one_active_initial ON tag_work.request_status USING btree (is_initial) WHERE (is_initial AND is_active)"
});
assert.deepEqual(JSON.parse(JSON.stringify(partialUnique)), {
  badge: "unique",
  summary: "UNIQUE BTREE (is_initial)",
  predicate: "WHERE (is_initial AND is_active)"
});

const ordinary = context.describeInspectorIndex({
  definition: "CREATE INDEX events_created_at_idx ON public.events USING btree (created_at DESC)"
});
assert.deepEqual(JSON.parse(JSON.stringify(ordinary)), {
  badge: "index",
  summary: "BTREE (created_at DESC)",
  predicate: ""
});

console.log("Inspector index tests passed");
