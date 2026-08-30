const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("src/schemii/shared_web/postgres-console.js", "utf8");
const browser = { SchemiiShared: Object.freeze({ existingSharedContract: true }) };
vm.runInNewContext(source, { window: browser, Object, URLSearchParams, encodeURIComponent });
const shared = browser.SchemiiShared;

function resource(overrides = {}) {
  return {
    consoleId: "console one",
    target: { profileId: "profile/one", database: "app db", namespace: "sales" },
    statement: {
      executionId: "execution/one", resultId: "result/one", statementIndex: 1, resultIndex: 2,
      rows: [[1]], hasMore: true, nextCursor: "cursor one", ...overrides,
    },
  };
}

(async () => {
  const first = resource();
  const url = shared.consoleResultResourceUrl(first, { cursor: first.statement.nextCursor });
  assert.match(url, /^\/api\/postgres\/profiles\/profile%2Fone\/console\/executions\/execution%2Fone\/results\/result%2Fone\?/);
  assert.match(url, /consoleId=console\+one/);
  assert.match(url, /database=app\+db/);
  assert.match(url, /namespace=sales/);
  assert.match(url, /statementIndex=1/);
  assert.match(url, /resultIndex=2/);
  assert.match(url, /cursor=cursor\+one/);

  const pageCalls = [];
  const originalRows = first.statement.rows;
  const paged = await shared.pageConsoleResultResource(first, async requestUrl => {
    pageCalls.push(requestUrl);
    return { rows: [[2]], hasMore: false, nextCursor: null, truncationEvents: [] };
  });
  assert.equal(paged.rows, originalRows, "paging must preserve the consumer-owned row array");
  assert.deepEqual(Array.from(paged.rows, row => Array.from(row)), [[1], [2]]);
  assert.equal(paged.hasMore, false);
  assert.equal(pageCalls.length, 1);

  const draining = resource();
  const pages = [
    { rows: [[2]], hasMore: true, nextCursor: "cursor two" },
    { rows: [[3]], hasMore: false, nextCursor: null },
  ];
  const drained = await shared.drainConsoleResultResource(draining, async () => pages.shift());
  assert.deepEqual(Array.from(drained.rows, row => Array.from(row)), [[1], [2], [3]]);
  assert.equal(drained.hasMore, false, "drain must exhaust retained pages without replaying SQL");

  const releasing = resource();
  const releaseCalls = [];
  await shared.releaseConsoleResultResource(releasing, async (requestUrl, options) => {
    releaseCalls.push({ requestUrl, options });
  }, { keepalive: true });
  assert.equal(releaseCalls.length, 1);
  assert.equal(releaseCalls[0].options.method, "DELETE");
  assert.equal(releaseCalls[0].options.keepalive, true);
  assert.equal(releasing.statement.hasMore, false);
  assert.equal(releasing.statement.resourceState, "closed");
  assert.deepEqual(Array.from(releasing.statement.closureEvents), ["closed"]);
  assert.throws(() => shared.consoleResultResourceUrl({}), /identity is incomplete/);
  assert.equal(shared.existingSharedContract, true, "resource registration must preserve prior shared contracts");
  assert.doesNotMatch(source, /structured-results|resultResource\.binding/, "Schemer structured results must remain a separate protocol");
  console.log("Shared retained Console result tests passed");
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
