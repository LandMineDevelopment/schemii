import assert from "node:assert/strict";
import test from "node:test";
import { createDraftAnalysisLifecycle } from "../../src/schemii/schemii/web/assets/draft-analysis.js";

function fakeTimers() {
  let nextId = 0;
  const callbacks = new Map();
  return {
    setTimer(callback, delay) {
      const id = ++nextId;
      callbacks.set(id, { callback, delay });
      return id;
    },
    clearTimer(id) {
      callbacks.delete(id);
    },
    delays() {
      return [...callbacks.values()].map(({ delay }) => delay);
    },
    fireAll() {
      const pending = [...callbacks.values()];
      callbacks.clear();
      pending.forEach(({ callback }) => callback());
    },
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const settle = () => new Promise(resolve => setImmediate(resolve));

test("successive schedules cancel the old debounce and run only the newest draft", async () => {
  const timers = fakeTimers();
  const calls = [];
  const lifecycle = createDraftAnalysisLifecycle({
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });

  lifecycle.schedule(async () => {
    calls.push("first");
    return "first result";
  }, { wait: 280 });
  lifecycle.schedule(async () => {
    calls.push("latest");
    return "latest result";
  }, { wait: 120 });

  assert.deepEqual(timers.delays(), [120]);
  timers.fireAll();
  await settle();

  assert.deepEqual(calls, ["latest"]);
  assert.deepEqual(lifecycle.snapshot(), { result: "latest result", error: null, loading: false });
});

test("a new edit fences an in-flight result before its debounce expires", async () => {
  const timers = fakeTimers();
  const first = deferred();
  const second = deferred();
  const lifecycle = createDraftAnalysisLifecycle({
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });

  lifecycle.schedule(() => first.promise);
  timers.fireAll();
  await settle();
  assert.equal(lifecycle.snapshot().loading, true);

  lifecycle.schedule(() => second.promise);
  first.resolve("stale result");
  await settle();
  assert.deepEqual(lifecycle.snapshot(), { result: null, error: null, loading: true });

  timers.fireAll();
  second.resolve("latest result");
  await settle();
  assert.deepEqual(lifecycle.snapshot(), { result: "latest result", error: null, loading: false });
});

test("failed analysis clears loading and a later successful analysis recovers", async () => {
  const timers = fakeTimers();
  const error = new Error("invalid draft");
  const lifecycle = createDraftAnalysisLifecycle({
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });

  lifecycle.schedule(async () => { throw error; });
  timers.fireAll();
  await settle();
  assert.deepEqual(lifecycle.snapshot(), { result: null, error, loading: false });

  lifecycle.schedule(async () => "recovered result");
  timers.fireAll();
  await settle();
  assert.deepEqual(lifecycle.snapshot(), { result: "recovered result", error: null, loading: false });
});

test("a draft that is no longer analyzable clears old state without showing loading", async () => {
  const timers = fakeTimers();
  const lifecycle = createDraftAnalysisLifecycle({
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });

  lifecycle.schedule(async () => "previous result");
  timers.fireAll();
  await settle();
  assert.equal(lifecycle.snapshot().result, "previous result");

  lifecycle.schedule(async () => assert.fail("empty drafts must not issue requests"), { canAnalyze: () => false });
  assert.equal(lifecycle.snapshot().result, "previous result");
  timers.fireAll();
  assert.deepEqual(lifecycle.snapshot(), { result: null, error: null, loading: false });
});

test("closing invalidates a late response and reopening starts a clean lifecycle", async () => {
  const timers = fakeTimers();
  const late = deferred();
  let active = true;
  const lifecycle = createDraftAnalysisLifecycle({
    isActive: () => active,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });

  lifecycle.schedule(() => late.promise);
  timers.fireAll();
  await settle();
  assert.equal(lifecycle.snapshot().loading, true);

  active = false;
  lifecycle.reset();
  assert.deepEqual(lifecycle.snapshot(), { result: null, error: null, loading: false });
  late.resolve("closed editor result");
  await settle();
  assert.deepEqual(lifecycle.snapshot(), { result: null, error: null, loading: false });

  active = true;
  lifecycle.schedule(async () => "reopened editor result");
  timers.fireAll();
  await settle();
  assert.deepEqual(lifecycle.snapshot(), { result: "reopened editor result", error: null, loading: false });
});
