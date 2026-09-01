import assert from "node:assert/strict";
import test from "node:test";

import { reorderedValues } from "../../src/schemii/schemii/web/assets/sortable.js";

test("sortable values move in either direction without mutating editor state", () => {
  const original = ["id", "tenant_id", "created_at"];

  assert.deepEqual(reorderedValues(original, 0, 2), ["tenant_id", "created_at", "id"]);
  assert.deepEqual(reorderedValues(original, 2, 0), ["created_at", "id", "tenant_id"]);
  assert.deepEqual(original, ["id", "tenant_id", "created_at"]);
});

test("sortable values ignore unavailable and no-op destinations", () => {
  const original = ["a", "b"];

  assert.deepEqual(reorderedValues(original, 0, 0), original);
  assert.deepEqual(reorderedValues(original, -1, 1), original);
  assert.deepEqual(reorderedValues(original, 0, 4), original);
  assert.notEqual(reorderedValues(original, 0, 0), original);
  assert.throws(() => reorderedValues("not-an-array", 0, 1), /array/);
});
