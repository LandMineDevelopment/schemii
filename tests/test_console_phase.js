const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const shared = fs.readFileSync(path.join(root, "src/schemii/shared_web/postgres-console.js"), "utf8");
const schemiiHtml = fs.readFileSync(path.join(root, "src/schemii/web/index.html"), "utf8");
const schemerHtml = fs.readFileSync(path.join(root, "src/schemii/schemer_web/index.html"), "utf8");
const schemer = fs.readFileSync(path.join(root, "src/schemii/schemer_web/app.js"), "utf8");
const readme = fs.readFileSync(path.join(root, "README.md"), "utf8");
const contracts = fs.readFileSync(path.join(root, "docs/SCHEMII_SQL_CONSOLE_AND_VIEWS_CONTRACTS.md"), "utf8");

for (const html of [schemiiHtml, schemerHtml]) {
  assert.match(html, /\/shared\/postgres-console\.js/, "both applications must mount the same Console component");
  assert.match(html, /\/shared\/postgres-console\.css/, "both applications must use shared Console styles");
}
assert.match(shared, /Managed read[\s\S]*Managed all-or-nothing[\s\S]*Explicit transaction[\s\S]*Autocommit \/ maintenance/);
assert.match(shared, /max="100" data-console-statement-limit/, "shared Console settings must allow up to 100 statements");
assert.match(shared, /data-console-profile[\s\S]*data-console-database[\s\S]*data-console-namespace/, "exact target must stay visible");
assert.match(shared, /activeTransaction[\s\S]*guardTargetChange[\s\S]*Commit or roll back/, "active transactions must guard target changes");
assert.match(shared, /Outcome unknown[\s\S]*console\/executions\/[\s\S]*Do not replay/, "transport loss must reconcile through status without replay");
assert.match(shared, /function resultUrl[\s\S]*statementIndex[\s\S]*resultIndex[\s\S]*cursor/, "result pages must retain exact cursor identity");
assert.match(shared, /console\/executions\/\$\{encodeURIComponent\(resource\.executionId\)\}\/results\/\$\{encodeURIComponent\(resource\.resultId\)\}/, "result pages must retain exact execution and result identity");
assert.match(shared, /data-console-load-more[\s\S]*data-console-export[\s\S]*data-console-close-result/, "incomplete results must expose paging, export, and close controls");
assert.match(shared, /while \(resource\?\.hasMore\) resource = await loadMore/, "export must drain the retained resource rather than rerun SQL");
assert.match(contracts, /Browser JSON export drains that retained pageable cursor or bounded spool[\s\S]*not an unbounded streaming export[\s\S]*cannot be recovered by export/, "export documentation must preserve TTL, cap, and terminal truncation limits");
assert.match(shared, /closeAllResults[\s\S]*method: "DELETE"[\s\S]*beforeunload/, "query and page cleanup must close retained results");
assert.match(shared, /truncationEvents[\s\S]*Display\/export is truncated/, "transport truncation must remain visibly distinct from pageable rows");
assert.doesNotMatch(shared, /write-grants|writeGrantId|expiresAt/, "the shared Console must not use legacy grants or expiry");
assert.match(schemer, /onCommittedWrite[\s\S]*sourceVerification\.clear\(\)[\s\S]*widgetQueryResults\.clear\(\)[\s\S]*widgetTemporalSeries\.clear\(\)/);
assert.doesNotMatch(schemer.match(/onCommittedWrite:[\s\S]*?\n  },\n}\);/)?.[0] || "", /saveDashboard|dashboardRevision\s*=|activeDashboard\s*=/, "cache invalidation must not mutate dashboard state");
assert.match(readme, /Schemer's write-capable human Console is intentional[\s\S]*durable application-scoped write intent[\s\S]*PostgreSQL role remains authoritative/, "Schemer write-capable Console policy must be explicit");
assert.doesNotMatch(contracts, /Schemer mounts only managed-read Console policy|only Schemii's human policy|Schemer only through its read-only policy/, "contracts must not retain the retired read-only Schemer Console policy");
const browser = { SchemiiShared: Object.freeze({ existingSharedContract: true }) };
vm.runInNewContext(shared, { window: browser, Object });
assert.equal(typeof browser.SchemiiShared.createPostgresConsole, "function", "Console registration must compose with an already-frozen shared contract");
assert.equal(browser.SchemiiShared.existingSharedContract, true, "Console registration must preserve existing shared contracts");
console.log("Shared Console phase contracts passed");
