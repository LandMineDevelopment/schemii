import test from "node:test";
import assert from "node:assert/strict";
import { placeTurnActivity } from "../../src/schemii/common/web/assets/ai-timeline.js";

const message = (messageRole, messageTurnId) => ({ dataset: { messageRole, messageTurnId } });

test("tracker precedes the completed answer without moving earlier turns", () => {
  const first = message("assistant", "first"), user = message("user", "second");
  const tool = {}, answer = message("assistant", "second"), tracker = {};
  assert.deepEqual(placeTurnActivity([first, user, tool, answer], tracker, { turnId: "second" }),
    [first, user, tracker, tool, answer]);
});

test("streaming and completed responses use the same placement and tracker node", () => {
  const user = message("user", "turn"), tracker = {}, stream = message("assistant", "turn");
  const nodes = placeTurnActivity([user, stream, tracker], tracker, { turnId: "turn" });
  assert.deepEqual(nodes, [user, tracker, stream]);
  assert.deepEqual(placeTurnActivity(nodes, tracker, { turnId: "turn" }), nodes);
});

test("old messages without turn IDs anchor the tracker after the latest prompt", () => {
  const first = message("user"), reply = message("assistant"), next = message("user"), answer = message("assistant"), tracker = {};
  assert.deepEqual(placeTurnActivity([first, reply, next, answer], tracker, { turnId: "new" }),
    [first, reply, next, tracker, answer]);
});

test("explicit turn IDs keep a tracker with its own turn even if later messages exist", () => {
  const first = message("user", "first"), reply = message("assistant", "first"), next = message("user", "next"), tracker = {};
  assert.deepEqual(placeTurnActivity([first, reply, next], tracker, { turnId: "first" }), [first, tracker, reply, next]);
});

test("a transcript with a pruned user prompt still puts the tracker before its response", () => {
  const older = message("assistant", "old"), answer = message("assistant", "turn"), tracker = {};
  assert.deepEqual(placeTurnActivity([older, answer], tracker, { turnId: "turn" }), [older, tracker, answer]);
  assert.deepEqual(placeTurnActivity([], tracker), [tracker]);
});
