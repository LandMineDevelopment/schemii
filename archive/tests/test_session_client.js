const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function response(ok, status, payload) {
  return {
    ok,
    status,
    json: async () => payload,
    clone() { return response(ok, status, payload); }
  };
}

async function main() {
  const calls = [];
  const queue = [
    response(true, 200, { token: "first", serverId: "server-one" }),
    response(false, 403, { error: { code: "invalid_session", message: "expired" } }),
    response(true, 200, { token: "second", serverId: "server-two" }),
    response(true, 200, { profiles: [] })
  ];
  let token = null;
  const context = vm.createContext({
    window: {},
    fetch: async (path, options = {}) => {
      calls.push([path, options]);
      return queue.shift();
    },
    Error,
    TypeError
  });
  vm.runInContext(fs.readFileSync("src/schemii/shared_web/api-contracts.js", "utf8"), context);
  vm.runInContext(fs.readFileSync("src/schemii/shared_web/session-client.js", "utf8"), context);
  vm.runInContext(fs.readFileSync("src/schemii/shared_web/postgres-client.js", "utf8"), context);
  const session = context.window.SchemiiShared.createSessionClient({
    getToken: () => token,
    setToken: value => { token = value; }
  });
  const postgres = context.window.SchemiiShared.createPostgresClient({ sessionClient: session });
  assert.deepEqual(await postgres.request("/api/postgres/profiles", { headers: { "X-Test": "yes" } }), { profiles: [] });
  assert.deepEqual(calls.map(call => call[0]), ["/api/session", "/api/postgres/profiles", "/api/session", "/api/postgres/profiles"]);
  assert.equal(calls[3][1].headers["X-Schemii-Token"], "second");
  assert.equal(calls[3][1].headers["X-Test"], "yes");
  assert.equal(token, "second");
  await assert.rejects(() => postgres.request("https://example.com"), /allowed local application API/);
  await assert.rejects(() => postgres.request("/api/postgres-evil/profiles"), /allowed local application API/);
  assert.equal(calls.length, 4, "disallowed paths must be rejected before fetch");

  let releaseBootstrap;
  let bootstrapCalls = 0;
  let concurrentToken = null;
  const concurrentContext = vm.createContext({
    window: {}, Error, TypeError, Promise,
    fetch: async () => {
      bootstrapCalls += 1;
      await new Promise(resolve => { releaseBootstrap = resolve; });
      return response(true, 200, { token: "shared" });
    }
  });
  vm.runInContext(fs.readFileSync("src/schemii/shared_web/session-client.js", "utf8"), concurrentContext);
  const concurrent = concurrentContext.window.SchemiiShared.createSessionClient({
    getToken: () => concurrentToken,
    setToken: value => { concurrentToken = value; }
  });
  const firstBootstrap = concurrent.ensureToken();
  const secondBootstrap = concurrent.ensureToken();
  await Promise.resolve();
  assert.equal(bootstrapCalls, 1, "concurrent callers must share one bootstrap");
  releaseBootstrap();
  assert.deepEqual(await Promise.all([firstBootstrap, secondBootstrap]), ["shared", "shared"]);

  const controller = new AbortController();
  let releaseAbortBootstrap;
  const abortContext = vm.createContext({
    window: {}, Error, TypeError, Promise,
    fetch: async () => {
      await new Promise(resolve => { releaseAbortBootstrap = resolve; });
      return response(true, 200, { token: "survived" });
    }
  });
  vm.runInContext(fs.readFileSync("src/schemii/shared_web/session-client.js", "utf8"), abortContext);
  let abortToken = null;
  const abortClient = abortContext.window.SchemiiShared.createSessionClient({
    getToken: () => abortToken,
    setToken: value => { abortToken = value; }
  });
  const aborted = abortClient.ensureToken({ signal: controller.signal });
  const survivor = abortClient.ensureToken();
  controller.abort();
  await assert.rejects(aborted, error => error.name === "AbortError");
  releaseAbortBootstrap();
  assert.equal(await survivor, "survived", "one caller aborting must not cancel the shared bootstrap");

  let malformedToken = null;
  const malformedContext = vm.createContext({
    window: {}, Error, TypeError,
    fetch: async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError("bad json"); } })
  });
  vm.runInContext(fs.readFileSync("src/schemii/shared_web/session-client.js", "utf8"), malformedContext);
  const malformed = malformedContext.window.SchemiiShared.createSessionClient({
    getToken: () => malformedToken,
    setToken: value => { malformedToken = value; }
  });
  await assert.rejects(() => malformed.ensureToken(), error => error.code === "invalid_json_response");

  let validToken = "valid";
  const malformedSuccessContext = vm.createContext({
    window: {}, Error, TypeError,
    fetch: async () => ({ ok: true, status: 200, json: async () => { throw new SyntaxError("bad json"); } })
  });
  vm.runInContext(fs.readFileSync("src/schemii/shared_web/session-client.js", "utf8"), malformedSuccessContext);
  const malformedSuccess = malformedSuccessContext.window.SchemiiShared.createSessionClient({
    getToken: () => validToken,
    setToken: value => { validToken = value; }
  });
  await assert.rejects(
    () => malformedSuccess.json("/api/test", {}, { allowPath: value => value === "/api/test" }),
    error => error.code === "invalid_json_response"
  );

  let staleToken = "old";
  let releaseStale;
  let staleCalls = 0;
  const staleContext = vm.createContext({
    window: {}, Error, TypeError,
    fetch: async () => {
      staleCalls += 1;
      if (staleCalls > 1) return response(true, 200, { ok: true });
      await new Promise(resolve => { releaseStale = resolve; });
      return response(false, 403, { error: { code: "invalid_session", message: "expired" } });
    }
  });
  vm.runInContext(fs.readFileSync("src/schemii/shared_web/session-client.js", "utf8"), staleContext);
  const staleClient = staleContext.window.SchemiiShared.createSessionClient({
    getToken: () => staleToken,
    setToken: value => { staleToken = value; }
  });
  const staleRequest = staleClient.json("/api/test", {}, { allowPath: value => value === "/api/test" });
  await Promise.resolve();
  staleToken = "newer";
  releaseStale();
  assert.deepEqual(await staleRequest, { ok: true });
  assert.equal(staleToken, "newer", "a stale invalid-session response must not clear a newer token");

  const existing = { previous: true };
  const orderContext = vm.createContext({
    window: { SchemiiShared: existing },
    document: {},
    HTMLElement: class {},
    fetch: async () => response(true, 200, {}),
    setTimeout,
    clearTimeout,
    requestAnimationFrame() {}
  });
  vm.runInContext(fs.readFileSync("src/schemii/shared_web/ui-components.js", "utf8"), orderContext);
  vm.runInContext(fs.readFileSync("src/schemii/shared_web/session-client.js", "utf8"), orderContext);
  vm.runInContext(fs.readFileSync("src/schemii/shared_web/postgres-client.js", "utf8"), orderContext);
  assert.equal(orderContext.window.SchemiiShared.previous, true);
  assert.equal(typeof orderContext.window.SchemiiShared.createIconButton, "function");
  assert.equal(typeof orderContext.window.SchemiiShared.createPostgresClient, "function");
  console.log("Shared session client tests passed");
}

main().catch(error => { console.error(error); process.exitCode = 1; });
