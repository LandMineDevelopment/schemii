const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "src/schemii/web/app.js"), "utf8");
const html = fs.readFileSync(path.join(root, "src/schemii/web/index.html"), "utf8");

assert.match(html, /id="restore-examples-button"[^>]*>Restore examples<\/button>/, "the Help menu must expose example restoration");
const start = source.indexOf("async function restoreExamples");
const end = source.indexOf("async function checkPostgresDrift", start);
const restore = source.slice(start, end);
assert.match(restore, /await flushPendingSave\(\)/, "restoration must save active user work first");
assert.match(restore, /sharedSessionClient\.json\("\/api\/examples\/restore"/, "restoration must use the narrow local endpoint");
assert.match(restore, /allowPath: path => path === "\/api\/examples\/restore"/, "restoration must use the authenticated local session client");
assert.match(restore, /library\.schemas = schemasPayload\.schemas/, "restoration must refresh the saved design library");
assert.doesNotMatch(restore, /openSchema\(|persistSchemaRecord\(/, "restoration must not replace or switch the active design");

console.log("Example restoration browser contract tests passed");
