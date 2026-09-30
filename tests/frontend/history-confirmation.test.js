import assert from "node:assert/strict";
import test from "node:test";
import { performDesignHistoryMove } from "../../src/schemii/schemii/web/assets/design-history.js";
import { applyJsonDelta } from "../../src/schemii/common/web/assets/json-delta.js";

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function fixture({ direction = "undo", delta = true } = {}) {
  const response = deferred(), requested = deferred();
  const before = { tables: [{ id: "people", name: "people", columns: [{ name: "amount" }] }] };
  const change = [{ operation: "replace", path: ["tables", 0, "columns", 0, "name"], value: "quantity" }];
  const state = {
    activeWorkspace: { id: "owned_workspace" }, design: { revision: 8, content: before },
    designLayout: { revision: 2 }, designHistory: { [direction]: { title: "Rename column", delta: delta ? change : [] } },
    activeLayer: "tables", selectedTableId: "people", selectedViewId: null,
  };
  const saved = { design: state.design, layout: state.designLayout, history: state.designHistory };
  const calls = [], signal = new AbortController().signal;
  let current = true;
  const args = {
    state, direction, operation: { signal, isCurrent: () => current },
    applyPreview: async changes => {
      state.design = { ...state.design, content: applyJsonDelta(state.design.content, changes) };
      calls.push("preview");
      return { presentation: { headline: "Column renamed" } };
    },
    requestMove: (move, workspaceId, body, options) => {
      calls.push("request");
      assert.equal(move, direction);
      assert.equal(workspaceId, "owned_workspace");
      assert.deepEqual(body, { expectedDesignRevision: 8 });
      assert.equal(options.signal, signal);
      requested.resolve();
      return response.promise;
    },
    applyMutation: (mutation, options) => {
      calls.push({ apply: options });
      state.design = mutation.design;
      state.designLayout = mutation.layout;
      state.designHistory = mutation.history;
    },
    restorePreview: rollback => {
      calls.push("rollback");
      assert.equal(rollback.design, saved.design);
      assert.equal(rollback.layout, saved.layout);
      assert.equal(rollback.history, saved.history);
      Object.assign(state, {
        design: rollback.design, designLayout: rollback.layout, designHistory: rollback.history,
        activeLayer: rollback.activeLayer, selectedTableId: rollback.selectedTableId, selectedViewId: rollback.selectedViewId,
      });
    },
  };
  const mutation = content => ({ design: { revision: 9, content }, layout: { revision: 3 }, history: { canRedo: true } });
  return { args, state, before, calls, response, requested, mutation, supersede: () => { current = false; } };
}

for (const direction of ["undo", "redo"]) {
  test(`matching deferred ${direction} confirmation commits metadata once without repainting`, async () => {
    const f = fixture({ direction });
    const pending = performDesignHistoryMove(f.args);
    await f.requested.promise;
    assert.deepEqual(f.calls, ["preview", "request"]);
    assert.equal(f.state.design.content.tables[0].columns[0].name, "quantity");
    assert.equal(f.before.tables[0].columns[0].name, "amount");
    f.response.resolve(f.mutation(structuredClone(f.state.design.content)));
    const result = await pending;
    assert.deepEqual(f.calls, ["preview", "request", { apply: { cue: false, render: false } }]);
    assert.equal(f.state.design.revision, 9);
    assert.equal(f.state.designLayout.revision, 3);
    assert.equal(f.state.designHistory.canRedo, true);
    assert.equal(result.presentation.headline, "Column renamed");
  });
}

test("a nonmatching authoritative confirmation replaces the preview exactly once", async () => {
  const f = fixture(), authoritative = { tables: [{ id: "people", columns: [{ name: "server_quantity" }] }] };
  const pending = performDesignHistoryMove(f.args);
  await f.requested.promise;
  f.response.resolve(f.mutation(authoritative));
  await pending;
  assert.deepEqual(f.calls, ["preview", "request", { apply: { cue: true, render: true } }]);
  assert.equal(f.state.design.content, authoritative);
});

test("a history error rolls back once and returns the original error for application reload policy", async () => {
  const f = fixture(), error = Object.assign(new Error("Design changed"), { code: "design_changed" });
  const pending = performDesignHistoryMove(f.args);
  await f.requested.promise;
  f.state.selectedTableId = "preview_selection";
  f.state.activeLayer = "views";
  f.response.reject(error);
  assert.deepEqual(await pending, { error });
  assert.deepEqual(f.calls, ["preview", "request", "rollback"]);
  assert.equal(f.state.design.content, f.before);
  assert.equal(f.state.selectedTableId, "people");
  assert.equal(f.state.activeLayer, "tables");
});

test("history without a delta renders the confirmation once and does not invent a rollback", async () => {
  const f = fixture({ delta: false });
  const pending = performDesignHistoryMove(f.args);
  await f.requested.promise;
  f.response.resolve(f.mutation(f.before));
  await pending;
  assert.deepEqual(f.calls, ["request", { apply: { cue: true, render: true } }]);
  const failed = fixture({ delta: false }), error = new Error("Connection lost");
  const failure = performDesignHistoryMove(failed.args);
  failed.response.reject(error);
  assert.deepEqual(await failure, { error });
  assert.deepEqual(failed.calls, ["request"]);
});

for (const ownerChange of ["operation", "workspace", "both"]) {
for (const outcome of ["confirmation", "error"]) {
  test(`${ownerChange} superseded ${outcome} cannot commit or roll back stale state`, async () => {
    const f = fixture(), pending = performDesignHistoryMove(f.args);
    await f.requested.promise;
    if (ownerChange !== "workspace") f.supersede();
    if (ownerChange !== "operation") f.state.activeWorkspace = { id: "other_workspace" };
    if (outcome === "confirmation") f.response.resolve(f.mutation(f.before));
    else f.response.reject(new Error("Late response"));
    assert.equal(await pending, null);
    assert.deepEqual(f.calls, ["preview", "request"]);
  });
}
}
