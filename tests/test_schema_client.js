const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "src/schemii/web/app.js"), "utf8");

assert.doesNotMatch(source, /fetch\([^\n]*\/api\/schemas|fetch\(`\/api\/schemas/, "schema requests must not bypass the authenticated client");

const reload = source.slice(source.indexOf("async function reloadActiveSchemaRecord"), source.indexOf("function standaloneSqlTarget"));
const fetchRecord = source.slice(source.indexOf("async function fetchSchemaRecord"), source.indexOf("function activateSchemaRecord"));
assert.match(fetchRecord, /const path = `\/api\/schemas\/\$\{encodeURIComponent\(schemaId\)\}`[\s\S]*sharedSessionClient\.json\(path/, "schema refresh must use an encoded exact-resource session route");
assert.match(fetchRecord, /allowPath: candidate => candidate === path/, "schema refresh must allow only its exact resource path");
assert.match(fetchRecord, /record\.id !== schemaId[\s\S]*invalid_api_response/, "schema refresh must reject a response for a different saved design");
assert.match(reload, /const schemaId = activeSchemaId[\s\S]*fetchSchemaRecord\(schemaId\)[\s\S]*activeSchemaId !== schemaId[\s\S]*activateSchemaRecord\(record\)/, "active schema refresh must abandon stale responses before authoritative activation");

const activation = source.slice(source.indexOf("function activateSchemaRecord"), source.indexOf("async function reloadActiveSchemaRecord"));
assert.match(activation, /const nextSchema = migrateSchema\(clone\(record\.schema\)\)[\s\S]*activeSchemaId = record\.id[\s\S]*schema = nextSchema[\s\S]*resetSchemaSession\(\)[\s\S]*render\(\)/, "authoritative records must be validated and migrated before replacing active schema state");

const save = source.slice(source.indexOf("async function putRecordFile"), source.indexOf("function saveRecordFile"));
assert.match(save, /sharedSessionClient\.json\(path/, "schema saves must use the session client");
assert.match(save, /allowPath: candidate => candidate === path/, "schema saves must allow only their exact encoded path");
assert.match(save, /"X-Schemii-Layout-Protocol": "2"/, "schema saves must preserve the layout protocol header");
assert.match(save, /"X-Schemii-Layout-Token": record\.layoutToken/, "schema saves must preserve the layout token header");

const initialize = source.slice(source.indexOf("async function initializeSchemaLibrary"), source.indexOf("async function persistSchemaRecord"));
assert.match(initialize, /sharedSessionClient\.json\("\/api\/schemas"/, "schema initialization must use the session client");
assert.match(initialize, /allowPath: path => path === "\/api\/schemas"/, "schema initialization must allow only the exact list path");

const deletion = source.slice(source.indexOf("async function deleteSavedSchema"), source.indexOf("function formatSavedDate"));
assert.match(deletion, /sharedSessionClient\.json\(path, \{ method: "DELETE", body: JSON\.stringify\(\{ expectedRevision: record\.revision, layoutToken: record\.layoutToken \}\) \}/, "schema deletion must carry revision and layout preconditions");
assert.match(deletion, /allowPath: candidate => candidate === path/, "schema deletion must allow only its exact encoded path");

const quarantine = source.slice(source.indexOf("function reportSaveError"), source.indexOf("function captureHistoryState"));
assert.match(quarantine, /schemaSaveQuarantine = \{[\s\S]*schemaId: activeSchemaId,[\s\S]*schema: schemaForStorage\(schema, view, \{ views: \{ viewport: viewsView, objects: viewsObjects \} \}\)/, "a schema conflict must preserve one complete local semantic and layout projection");
assert.match(quarantine, /clearTimeout\(saveTimer\)[\s\S]*schema-conflict-banner/, "a schema conflict must freeze scheduled autosave and expose recovery");
assert.match(quarantine, /schemaSaveQuarantine\?\.schemaId === schemaId[\s\S]*schema_save_quarantined/, "queued saves must not replay a stale schema after quarantine");
assert.match(source, /export-conflicted-schema[\s\S]*clone\(schemaSaveQuarantine\.schema\)[\s\S]*refresh-conflicted-schema[\s\S]*await saveQueue\.catch[\s\S]*fetchSchemaRecord\(schemaId\)[\s\S]*activateSchemaRecord\(record\)[\s\S]*schemaSaveQuarantine = null/, "quarantined local edits must remain immutable and exportable until authoritative activation succeeds");
assert.doesNotMatch(source, /openSavedSchema/, "conflict recovery must not call a nonexistent legacy schema opener");

const clientDeclaration = source.indexOf("const sharedSessionClient =");
assert.ok(clientDeclaration > source.indexOf("async function putRecordFile"), "schema functions may be declared before the session client");
assert.ok(clientDeclaration < source.lastIndexOf("initializeSchemaLibrary().finally"), "the session client must initialize before schema startup invokes those functions");

console.log("Authenticated schema client contract tests passed");
