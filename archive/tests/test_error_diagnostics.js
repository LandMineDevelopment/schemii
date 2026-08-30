const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const context = vm.createContext({ window: {}, Object, Array, Set, Number });
vm.runInContext(fs.readFileSync("src/schemii/shared_web/error-diagnostics.js", "utf8"), context);
const diagnostics = context.window.SchemiiShared;

const postgresError = {
  message: "Console SQL statement failed",
  code: "sql_query_failed",
  payload: { error: { code: "sql_query_failed", message: "Console SQL statement failed", details: { postgres: {
    sqlstate: "42703", message: "column missing does not exist", detail: "Column is unavailable.",
    hint: "Check the alias.", position: 18,
  } } } },
};
const rendered = diagnostics.formatApiError(postgresError);
assert.equal(rendered, "column missing does not exist\nSQLSTATE 42703 · position 18\nDetail: Column is unavailable.\nHint: Check the alias.");
assert.equal((rendered.match(/Console SQL statement failed/g) || []).length, 0);

const limitation = {
  code: "capability_unavailable",
  payload: { error: { code: "capability_unavailable", message: "Unavailable here", details: {
    requiredCapability: "relation_query", reason: "Application policy excludes it",
    safeAlternative: "Use Schemer.", settingsAction: { type: "navigate", path: "https://example.test" },
  } } },
};
assert.match(diagnostics.formatApiError(limitation), /Required: relation_query[\s\S]*Reason: Application policy excludes it[\s\S]*Alternative: Use Schemer\./);
assert.equal(diagnostics.allowedLocalErrorAction(limitation), null);
limitation.payload.error.details.settingsAction = { type: "open_local_settings", path: "/api/ai/settings", extra: true };
assert.equal(diagnostics.allowedLocalErrorAction(limitation), null);
limitation.payload.error.details.settingsAction = { type: "open_local_settings", path: "/api/ai/settings" };
assert.equal(diagnostics.allowedLocalErrorAction(limitation).path, "/api/ai/settings");

console.log("Shared error diagnostic tests passed");
