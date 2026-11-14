import assert from "node:assert/strict";
import test from "node:test";
import { columnTypeKey, createColumnTypeAnalysis } from "../../src/schemii/schemii/web/assets/column-type-analysis.js";

const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function harness({ equivalent = true, analyze = async (_, body) => ({ sourceType: "uuid", targetType: body.targetType, requiresConversion: true, reason: "Needs review" }), loadHistory } = {}) {
  const calls = [], changes = [], statuses = [], timers = new Map();
  let active = true, sequence = 0;
  const design = { revision: 1, fingerprint: "baseline" };
  const historyCalls = [];
  const session = createColumnTypeAnalysis({
    workspaceId: "ws", design, isActive: () => active,
    loadHistory: (...args) => {
      historyCalls.push(args);
      return loadHistory ? loadHistory(...args) : Promise.resolve({ designRevision: 1, baseline: { kind: "postgresql", fingerprint: equivalent ? "baseline" : "other" } });
    },
    analyze: (...args) => { calls.push(args); return analyze(...args); },
    onStatus: state => statuses.push(state),
    setTimer: callback => { timers.set(++sequence, callback); return sequence; },
    clearTimer: id => timers.delete(id),
  });
  return {
    session, calls, changes, statuses, historyCalls,
    deactivate() { active = false; },
    add(id = "column", type = "uuid", rowActive = () => true) {
      return session.add({ columnId: id, targetType: type, isActive: rowActive, onChange: value => changes.push({ id, ...value }) });
    },
    async tick() { const pending = [...timers.values()]; timers.clear(); pending.forEach(fn => fn()); await flush(); },
    get timers() { return timers.size; },
  };
}

test("canonical keys preserve domains, enums, quotes, arrays and narrowing modifiers", () => {
  for (const [a, b] of [[" UUID ", "uuid"], ["int4", "integer"], ["decimal(10)", "numeric(10, 0)"], ["varchar (40)", "character varying(40)"], ["timestamptz", "timestamp(6) with time zone"], ["int4 []", "integer[]"]]) {
    assert.equal(columnTypeKey(a), columnTypeKey(b), `${a} = ${b}`);
  }
  for (const [a, b] of [["uuid", "public.uuid_domain"], ['"Enum"', '"enum"'], ["public.status", "status"], ["varchar(40)", "varchar(20)"], ["numeric(10,2)", "numeric(10,3)"], ["timestamp(3)", "timestamp(6)"], ["time", "timetz"], ["uuid", "uuid[]"], ['"uuid"', "uuid"]]) {
    assert.notEqual(columnTypeKey(a), columnTypeKey(b));
  }
});

test("eleven unchanged UUID rows use one history proof and zero analysis requests", async () => {
  const h = harness({ analyze: () => assert.fail("unchanged UUID must not request analysis") });
  Array.from({ length: 11 }, (_, i) => h.add(`c${i}`));
  await h.tick();
  assert.equal(h.historyCalls.length, 1);
  assert.equal(h.calls.length, 0);
  assert.equal(h.changes.filter(s => s.status === "ready").length, 11);
  h.session.dispose();
});

test("real conversion is pending then conversion-required; equivalent selection is deduplicated", async () => {
  const response = deferred();
  const h = harness({ analyze: () => response.promise });
  const row = h.add();
  await h.tick();
  row.select("text");
  row.select("TEXT");
  await h.tick();
  assert.equal(h.calls.length, 1);
  assert.equal(h.changes.at(-1).status, "pending");
  response.resolve({ sourceType: "uuid", requiresConversion: true, reason: "Review existing values" });
  await flush();
  assert.equal(h.changes.at(-1).status, "conversion-required");
  row.select(" UUID ");
  await h.tick();
  assert.equal(h.changes.at(-1).status, "ready");
  assert.equal(h.calls.length, 1);
  h.session.dispose();
});

test("saved unapplied changes discover baseline serially and retain real warnings", async () => {
  const pending = [];
  const h = harness({ equivalent: false, analyze: () => { const p = deferred(); pending.push(p); return p.promise; } });
  Array.from({ length: 11 }, (_, i) => h.add(`c${i}`, i === 0 ? "text" : "uuid"));
  await h.tick();
  assert.equal(h.calls.length, 1, "no initial request fanout");
  for (let i = 0; i < 11; i += 1) {
    assert.equal(h.calls.length, i + 1);
    pending[i].resolve({ sourceType: "uuid", requiresConversion: i === 0, reason: "Needs conversion" });
    await flush();
  }
  assert.equal(h.changes.filter(s => s.status === "conversion-required").length, 1);
  assert.equal(h.changes.filter(s => s.status === "ready").length, 10);
  h.session.dispose();
});

test("503 or permission failure stops the discovery queue, reports once, and requires explicit retry", async () => {
  for (const error of [{ status: 503, code: "migration_metadata_unavailable" }, { status: 403, code: "product_access_denied" }]) {
    let failed = true;
    const h = harness({ equivalent: false, analyze: async () => {
      if (failed) throw error;
      return { sourceType: "uuid", requiresConversion: false };
    } });
    Array.from({ length: 11 }, (_, i) => h.add(`c${i}`));
    await h.tick();
    assert.equal(h.calls.length, 1);
    assert.deepEqual(h.statuses, [{ error }]);
    assert.equal(h.changes.filter(s => s.status === "unavailable").length, 11);
    await h.tick();
    assert.equal(h.calls.length, 1, "no silent retries");
    failed = false;
    h.session.retry();
    await h.tick();
    assert.equal(h.calls.length, 12);
    assert.equal(h.changes.at(-1).status, "ready");
    h.session.dispose();
  }
});

test("history failure does not guess unchanged baseline or start column requests", async () => {
  const h = harness({ loadHistory: async () => { throw { code: "metadata_capacity_exceeded", status: 503 }; } });
  h.add(); await h.tick();
  assert.equal(h.calls.length, 0);
  assert.equal(h.statuses[0].error.code, "metadata_capacity_exceeded");
  assert.equal(h.changes.at(-1).status, "unavailable");
  h.session.dispose();
});

test("revision mismatch cannot establish unchanged baseline", async () => {
  const h = harness({ loadHistory: async () => ({ designRevision: 2, baseline: { kind: "postgresql", fingerprint: "baseline" } }) });
  h.add(); await h.tick();
  assert.equal(h.calls.length, 1);
  h.session.dispose();
});

test("rapid changes abort old requests and fence late success and failure", async () => {
  for (const lateError of [false, true]) {
    const old = deferred(), latest = deferred();
    let count = 0;
    const h = harness({ analyze: () => ++count === 1 ? old.promise : latest.promise });
    const row = h.add(); await h.tick();
    row.select("text"); await h.tick();
    row.select("integer");
    assert.equal(h.calls[0][2].signal.aborted, true);
    if (lateError) old.reject({ status: 503, code: "migration_metadata_unavailable" });
    else old.resolve({ sourceType: "uuid", requiresConversion: true, reason: "STALE" });
    await flush(); await h.tick();
    latest.resolve({ sourceType: "uuid", requiresConversion: true, reason: "CURRENT" });
    await flush();
    assert.equal(h.calls.length, 2);
    assert.equal(h.statuses.length, 0);
    assert.equal(h.changes.at(-1).analysis.reason, "CURRENT");
    h.session.dispose();
  }
});

test("detach, workspace change and disposal fence late errors without queued churn", async () => {
  for (const boundary of ["detach", "workspace", "dispose"]) {
    const pending = deferred();
    let connected = true;
    const h = harness({ equivalent: false, analyze: () => pending.promise });
    const row = h.add("c", "text", () => connected);
    await h.tick();
    const before = h.changes.length;
    if (boundary === "detach") connected = false;
    if (boundary === "workspace") h.deactivate();
    if (boundary === "dispose") row.dispose();
    pending.reject({ status: 503, code: "migration_metadata_unavailable" });
    await flush();
    assert.equal(h.changes.length, before);
    assert.equal(h.statuses.length, 0);
    assert.equal(h.timers, 0);
    h.session.dispose();
  }
});

test("a safe type change completes without a conversion warning and unchanged aliases need no request", async () => {
  const h = harness({ analyze: async () => ({ sourceType: "integer", requiresConversion: false }) });
  const row = h.add("integer-column", "int4"); await h.tick();
  row.select("integer"); await h.tick();
  assert.equal(h.calls.length, 0);
  row.select("bigint"); await h.tick();
  assert.equal(h.calls.length, 1);
  assert.equal(h.changes.at(-1).status, "ready");
  h.session.dispose();
});

test("reverting a failed real edit clears the shared failure when no checks remain", async () => {
  const h = harness({ analyze: async () => { throw { code: "request_timeout" }; } });
  const row = h.add(); await h.tick();
  row.select("text"); await h.tick();
  assert.equal(h.statuses.at(-1).error.code, "request_timeout");
  row.select("uuid");
  assert.equal(h.changes.at(-1).status, "ready");
  assert.equal(h.statuses.at(-1).error, null);
  h.session.dispose();
});

test("cancelled transport stays neutral; disposed baseline discovery cannot launch requests", async () => {
  const h = harness({ equivalent: false, analyze: async () => { throw { code: "request_cancelled" }; } });
  h.add(); await h.tick();
  assert.equal(h.changes.at(-1).status, "idle");
  assert.equal(h.statuses.length, 0);
  h.session.dispose();
  const history = deferred();
  const stale = harness({ loadHistory: () => history.promise });
  const row = stale.add(); await stale.tick();
  row.dispose();
  assert.equal(stale.historyCalls[0][1].signal.aborted, true);
  history.resolve({ designRevision: 1, baseline: { kind: "postgresql", fingerprint: "baseline" } });
  await flush();
  assert.equal(stale.calls.length, 0);
  assert.equal(stale.changes.some(value => value.status === "ready"), false);
});
