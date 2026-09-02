import assert from "node:assert/strict";
import test from "node:test";

import {
  createLatestRequestController,
  createWorkspaceOperationController,
} from "../../src/schemii/schemii/web/assets/request-coordinator.js";

test("a workspace mutation invalidates and aborts an older refresh", () => {
  const operations = createWorkspaceOperationController();
  const refresh = operations.beginRead();

  const mutation = operations.beginMutation();

  assert.equal(refresh.signal.aborted, true);
  assert.equal(refresh.isCurrent(), false);
  assert.equal(mutation.interruptedRead, true);
  assert.equal(mutation.isCurrent(), true);
  assert.equal(operations.isMutating(), true);
  mutation.finish();
  assert.equal(operations.isMutating(), false);
});

test("switching workspaces invalidates every in-flight workspace operation", () => {
  const operations = createWorkspaceOperationController();
  const mutation = operations.beginMutation();

  operations.invalidate();

  assert.equal(mutation.signal.aborted, true);
  assert.equal(mutation.isCurrent(), false);
  assert.equal(operations.isMutating(), false);
  mutation.finish();
});

test("latest request controller rejects a delayed older list response", () => {
  const requests = createLatestRequestController();
  const first = requests.begin();
  const second = requests.begin();

  assert.equal(first.signal.aborted, true);
  assert.equal(first.isCurrent(), false);
  assert.equal(second.isCurrent(), true);
  second.finish();
});
