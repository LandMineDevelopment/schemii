import assert from "node:assert/strict";
import test from "node:test";

import {
  createViewAnalysisCollection,
  createViewAnalysisController,
  desiredViewColumnSummary,
} from "../../src/schemii/schemii/web/assets/view-analysis.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolveValue, rejectValue) => {
    resolve = resolveValue;
    reject = rejectValue;
  });
  return { promise, resolve, reject };
}

test("view analysis starts from selection and deduplicates the selected key", async () => {
  const pending = deferred();
  const calls = [];
  const changes = [];
  const controller = createViewAnalysisController({
    keyOf: context => context.key,
    load: (context, options) => {
      calls.push({ context, signal: options.signal });
      return pending.promise;
    },
    onChange: value => changes.push(value),
  });

  const first = controller.select({ key: "view-1" });
  const second = controller.select({ key: "view-1" });
  await Promise.resolve();
  assert.equal(calls.length, 1);
  assert.equal(controller.snapshot().status, "loading");
  pending.resolve({ outputs: ["total"] });
  await Promise.all([first, second]);

  assert.deepEqual(controller.snapshot(), {
    key: "view-1",
    status: "ready",
    analysis: { outputs: ["total"] },
    error: null,
  });
  assert.equal(changes.at(-1).status, "ready");
});

test("changing selection aborts the stale request and ignores a late result", async () => {
  const requests = new Map();
  const controller = createViewAnalysisController({
    keyOf: context => context.key,
    load: (context, { signal }) => {
      const pending = deferred();
      requests.set(context.key, { ...pending, signal });
      return pending.promise;
    },
  });

  const stale = controller.select({ key: "view-1" });
  await Promise.resolve();
  const current = controller.select({ key: "view-2" });
  await Promise.resolve();
  assert.equal(requests.get("view-1").signal.aborted, true);
  requests.get("view-1").resolve({ name: "stale" });
  requests.get("view-2").resolve({ name: "current" });
  await Promise.all([stale, current]);

  assert.equal(controller.snapshot().key, "view-2");
  assert.deepEqual(controller.snapshot().analysis, { name: "current" });
});

test("forced refresh retains cached analysis while the replacement is loading", async () => {
  const first = deferred();
  const refresh = deferred();
  let call = 0;
  const controller = createViewAnalysisController({
    keyOf: context => context.key,
    load: () => (++call === 1 ? first.promise : refresh.promise),
  });

  const initial = controller.select({ key: "view-1" });
  first.resolve({ revision: 1 });
  await initial;
  const refreshing = controller.retry({ key: "view-1" });
  await Promise.resolve();
  assert.deepEqual(controller.snapshot(), {
    key: "view-1",
    status: "loading",
    analysis: { revision: 1 },
    error: null,
  });
  refresh.resolve({ revision: 2 });
  await refreshing;
  assert.deepEqual(controller.snapshot().analysis, { revision: 2 });
});

test("visible view analyses share requests and retain independent results", async () => {
  const requests = new Map();
  const changes = [];
  const collection = createViewAnalysisCollection({
    load: (context, { signal }) => {
      const pending = deferred();
      requests.set(context.key, { ...pending, signal });
      return pending.promise;
    },
    onChange: value => changes.push(value),
  });
  const ordinary = { key: "workspace:ordinary:design" };
  const materialized = { key: "workspace:materialized:design" };
  collection.sync([ordinary, materialized]);
  const first = collection.ensure(ordinary);
  const second = collection.ensure(materialized);
  const inspector = collection.ensure(ordinary);
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(requests.size, 2);
  assert.equal(requests.get(ordinary.key).signal.aborted, false);
  requests.get(materialized.key).resolve({ outputs: [{ name: "total" }] });
  await second;
  assert.equal(collection.snapshot(ordinary).status, "loading");
  assert.equal(desiredViewColumnSummary(collection.snapshot(materialized)), "1 column");
  requests.get(ordinary.key).resolve({ outputs: [{ name: "id" }, { name: "name" }] });
  await Promise.all([first, inspector]);
  assert.equal(desiredViewColumnSummary(collection.snapshot(ordinary)), "2 columns");
  assert.equal(changes.length, 2);
});

test("context replacement aborts obsolete analyses and ignores late success or failure", async () => {
  const requests = new Map();
  const changes = [];
  const collection = createViewAnalysisCollection({
    load: (context, { signal }) => {
      const pending = deferred();
      requests.set(context.key, { ...pending, signal });
      return pending.promise;
    },
    onChange: value => changes.push(value),
  });
  const first = { key: "workspace:view:before" };
  const other = { key: "workspace:other:before" };
  const current = { key: "workspace:view:after" };
  collection.sync([first, other]);
  const staleSuccess = collection.ensure(first);
  const staleFailure = collection.ensure(other);
  await new Promise(resolve => setImmediate(resolve));
  collection.sync([current]);
  const pending = collection.ensure(current);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests.get(first.key).signal.aborted, true);
  assert.equal(requests.get(other.key).signal.aborted, true);
  assert.equal(desiredViewColumnSummary(collection.snapshot(current)), "Analyzing columns…");

  requests.get(current.key).resolve({ outputs: [{ name: "new" }] });
  requests.get(first.key).resolve({ outputs: ["old", "old", "old"] });
  requests.get(other.key).reject(new Error("old failure"));
  await Promise.all([pending, staleSuccess, staleFailure]);
  assert.equal(desiredViewColumnSummary(collection.snapshot(current)), "1 column");
  assert.equal(collection.snapshot(first).analysis, null);
  assert.deepEqual(changes.map(change => change.key), [current.key]);
});

test("view collection bounds requests and removes obsolete queued work", async () => {
  const calls = [];
  const pending = deferred();
  const collection = createViewAnalysisCollection({
    maximumConcurrent: 1,
    load: (context, { signal }) => {
      calls.push({ key: context.key, signal });
      return context.key === "first" ? pending.promise : { outputs: [context.key] };
    },
  });
  const contexts = ["first", "obsolete", "current"].map(key => ({ key }));
  collection.sync(contexts);
  const requests = contexts.map(context => collection.ensure(context));
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls.map(call => call.key), ["first"]);
  collection.sync([contexts[2]]);
  assert.equal(calls[0].signal.aborted, true);
  pending.resolve({ outputs: ["stale"] });
  await Promise.all(requests);
  assert.deepEqual(calls.map(call => call.key), ["first", "current"]);
  assert.equal(desiredViewColumnSummary(collection.snapshot(contexts[2])), "1 column");
});

test("analysis errors stay unknown until an explicit retry succeeds", async () => {
  let calls = 0;
  const collection = createViewAnalysisCollection({
    load: () => {
      calls += 1;
      if (calls === 1) throw new Error("Analysis unavailable");
      return { outputs: [{ name: "id" }] };
    },
  });
  const context = { key: "workspace:view:design" };
  collection.sync([context]);
  await collection.ensure(context);
  assert.equal(desiredViewColumnSummary(collection.snapshot(context)), "Column count unavailable");
  await collection.ensure(context);
  assert.equal(calls, 1);
  await collection.ensure(context, { force: true });
  assert.equal(calls, 2);
  assert.equal(desiredViewColumnSummary(collection.snapshot(context)), "1 column");
});

test("column summaries distinguish unknown shape from an analyzed empty result", () => {
  assert.equal(desiredViewColumnSummary(null), "Columns not analyzed");
  assert.equal(desiredViewColumnSummary({ status: "loading" }), "Analyzing columns…");
  assert.equal(desiredViewColumnSummary({ status: "ready", analysis: null }), "Column count unavailable");
  assert.equal(desiredViewColumnSummary({ status: "error" }), "Column count unavailable");
  assert.equal(desiredViewColumnSummary({ status: "ready", analysis: { outputs: [] } }), "0 columns");
  assert.equal(desiredViewColumnSummary({
    status: "ready", analysis: { outputs: [{ name: "*" }], warnings: ["unresolved_wildcard"] },
  }), "Column count unavailable");
  assert.equal(desiredViewColumnSummary({
    status: "ready", analysis: { outputs: [{ name: "id" }], warnings: ["too_many_outputs"] },
  }), "Column count unavailable");
  assert.equal(desiredViewColumnSummary({ status: "ready", analysis: { outputs: [], status: "partial" } }), "Column count unavailable");
  assert.equal(desiredViewColumnSummary({ status: "loading", analysis: { outputs: ["id"] } }), "1 column · refreshing…");
  assert.equal(desiredViewColumnSummary({ status: "error", analysis: { outputs: ["id"] } }), "1 column · analysis unavailable");
});
