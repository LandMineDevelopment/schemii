import assert from "node:assert/strict";
import test from "node:test";

import { createViewAnalysisController } from "../../src/schemii/schemii/web/assets/view-analysis.js";

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
