import assert from "node:assert/strict";
import test from "node:test";

import {
  changeTransitionKey,
  createChangeTransitionManager,
  elementFullyVisible,
  resolveChangeTargetElements,
} from "../../src/schemii/common/web/assets/change-transition.js";

function fakeElement({ id, className, rect }) {
  const animations = [];
  return {
    animations,
    className,
    dataset: { changeObjectId: id, changeRoot: "" },
    tagName: "DIV",
    closest: () => null,
    checkVisibility: () => true,
    getBoundingClientRect: () => ({ ...rect }),
    animate: (frames, options) => {
      const animation = {
        frames,
        options,
        cancelled: false,
        finished: Promise.resolve(),
        cancel() { this.cancelled = true; },
      };
      animations.push(animation);
      return animation;
    },
  };
}

function fakeRoot(elements, exact = new Map()) {
  return {
    defaultView: { matchMedia: () => ({ matches: false }) },
    querySelectorAll(selector) {
      if (selector === "[data-change-object-id][data-change-root]") return elements;
      return exact.get(selector) || [];
    },
  };
}

test("removal transitions fade only the exact object and capture survivors", async () => {
  const removed = fakeElement({ id: "column_removed", className: "column-row", rect: { left: 0, top: 10, width: 100, height: 20 } });
  const survivor = fakeElement({ id: "column_kept", className: "column-row selected", rect: { left: 0, top: 30, width: 100, height: 20 } });
  const exact = new Map([
    ['[data-change-object-id="column_removed"][data-change-root]', [removed]],
  ]);
  const manager = createChangeTransitionManager({ root: fakeRoot([removed, survivor], exact) });

  const prepared = await manager.prepare([{
    objectId: "column_removed",
    fallbackId: "table_parent",
    operation: "remove",
    tone: "amber",
  }]);

  assert.equal(prepared.removalCount, 1);
  assert.equal(removed.animations.length, 1);
  assert.equal(removed.animations[0].options.duration, 210);
  assert.equal(removed.animations[0].frames.at(-1).opacity, 0);
  assert.equal(prepared.snapshot.has(changeTransitionKey(survivor)), true);
});

test("a missing removed object never falls back to flashing its parent", async () => {
  const parent = fakeElement({ id: "table_parent", className: "table-card", rect: { left: 0, top: 0, width: 100, height: 100 } });
  const exact = new Map([
    ['[data-change-object-id="table_parent"][data-change-root]', [parent]],
  ]);
  const manager = createChangeTransitionManager({ root: fakeRoot([parent], exact) });

  const prepared = await manager.prepare([{
    objectId: "column_missing",
    fallbackId: "table_parent",
    operation: "remove",
    tone: "amber",
  }]);

  assert.equal(prepared.removalCount, 0);
  assert.equal(parent.animations.length, 0);
});

test("surviving objects animate from their old position into the compacted order", async () => {
  const removed = fakeElement({ id: "column_removed", className: "column-row", rect: { left: 0, top: 10, width: 100, height: 20 } });
  const survivorRect = { left: 0, top: 40, width: 100, height: 20 };
  const survivor = fakeElement({ id: "column_kept", className: "column-row selected", rect: survivorRect });
  const exact = new Map([
    ['[data-change-object-id="column_removed"][data-change-root]', [removed]],
  ]);
  const root = fakeRoot([removed, survivor], exact);
  const manager = createChangeTransitionManager({ root });
  const prepared = await manager.prepare([{ objectId: "column_removed", operation: "remove" }]);

  survivorRect.top = 10;
  root.querySelectorAll = selector => (
    selector === "[data-change-object-id][data-change-root]" ? [survivor] : []
  );
  const count = manager.reflow(prepared);

  assert.equal(count, 1);
  assert.equal(survivor.animations[0].frames[0].transform, "translate3d(0px, 30px, 0)");
  assert.equal(survivor.animations[0].options.duration, 180);
});

test("completed removal transitions release fill-forwards state from persistent surfaces", async () => {
  const persistent = fakeElement({
    id: "view_removed",
    className: "view-detail",
    rect: { left: 0, top: 40, width: 300, height: 400 },
  });
  const exact = new Map([
    ['[data-change-object-id="view_removed"][data-change-root]', [persistent]],
  ]);
  const root = fakeRoot([persistent], exact);
  const manager = createChangeTransitionManager({ root });
  const prepared = await manager.prepare([{
    objectId: "view_removed",
    operation: "remove",
    tone: "purple",
  }]);

  assert.equal(persistent.animations[0].cancelled, false);
  manager.reflow(prepared);
  assert.equal(persistent.animations[0].cancelled, true);
});

test("a throttled animation timeline can never block the state change", async () => {
  const removed = fakeElement({ id: "view_removed", className: "view-list-button", rect: { left: 0, top: 0, width: 100, height: 30 } });
  removed.animate = (frames, options) => {
    const animation = {
      frames,
      options,
      finished: new Promise(() => {}),
      cancel() {},
    };
    removed.animations.push(animation);
    return animation;
  };
  const exact = new Map([
    ['[data-change-object-id="view_removed"][data-change-root]', [removed]],
  ]);
  let scheduledDelay = null;
  const manager = createChangeTransitionManager({
    root: fakeRoot([removed], exact),
    schedule: (callback, delay) => {
      scheduledDelay = delay;
      callback();
      return 1;
    },
  });

  const prepared = await manager.prepare([{ objectId: "view_removed", operation: "remove" }]);

  assert.equal(scheduledDelay, 240);
  assert.equal(prepared.removalCount, 1);
});

test("change reveal resolves the exact changed field before a generic object copy", () => {
  const generic = fakeElement({ id: "column_1", className: "column-row", rect: { left: 0, top: 0, width: 100, height: 30 } });
  const dataType = fakeElement({ id: "column_1", className: "type-select", rect: { left: 0, top: 0, width: 100, height: 30 } });
  const exact = new Map([
    ['[data-change-object-id="column_1"][data-change-field~="dataType"]', [dataType]],
    ['[data-change-object-id="column_1"][data-change-root]', [generic]],
  ]);

  assert.deepEqual(resolveChangeTargetElements(fakeRoot([generic], exact), {
    objectId: "column_1",
    fields: ["dataType"],
  }), [dataType]);
});

test("change reveal treats content clipped by an inner scroll area as off screen", () => {
  const documentRef = {
    body: {},
    documentElement: {},
    defaultView: {
      innerWidth: 400,
      innerHeight: 800,
      getComputedStyle: element => element.computedStyle || {},
    },
  };
  const scrollArea = {
    parentElement: null,
    computedStyle: { overflowX: "hidden", overflowY: "auto" },
    getBoundingClientRect: () => ({ left: 0, right: 400, top: 100, bottom: 200 }),
  };
  const rect = { left: 20, right: 380, top: 190, bottom: 220, width: 360, height: 30 };
  const field = {
    ownerDocument: documentRef,
    parentElement: scrollArea,
    closest: () => null,
    checkVisibility: () => true,
    getBoundingClientRect: () => ({ ...rect }),
  };

  assert.equal(elementFullyVisible(field), false);
  rect.top = 140;
  rect.bottom = 170;
  assert.equal(elementFullyVisible(field), true);
});
