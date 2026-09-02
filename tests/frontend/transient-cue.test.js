import assert from "node:assert/strict";
import test from "node:test";

import { createTransientCueManager } from "../../src/schemii/common/web/assets/transient-cue.js";

function fakeElement() {
  const classes = new Set();
  const changes = { added: 0, removed: 0 };
  const properties = new Map();
  return {
    changes,
    computedStyle: { borderRadius: "0px" },
    dataset: {},
    ownerDocument: {
      defaultView: {
        getComputedStyle: element => element.computedStyle,
      },
    },
    querySelector: () => null,
    style: {
      getPropertyValue: name => properties.get(name) || "",
      removeProperty: name => properties.delete(name),
      setProperty: (name, value) => properties.set(name, value),
    },
    classList: {
      add: value => {
        changes.added += 1;
        classes.add(value);
      },
      remove: value => {
        changes.removed += 1;
        classes.delete(value);
      },
      contains: value => classes.has(value),
    },
    closest: () => null,
    checkVisibility: () => true,
  };
}

test("transient cues prefer exact fields and apply the supplied surface tone", () => {
  const wholeColumn = fakeElement();
  const typeControl = fakeElement();
  const selectors = new Map([
    ['[data-change-object-id="column_1"][data-change-field~="dataType"]', [typeControl]],
    ['[data-change-object-id="column_1"]', [wholeColumn, typeControl]],
  ]);
  const root = {
    querySelectorAll: selector => selectors.get(selector) || [],
  };
  const manager = createTransientCueManager({
    root,
    schedule: () => 1,
    cancel: () => {},
    MutationObserverClass: null,
  });

  manager.show([{ objectId: "column_1", fields: ["dataType"], tone: "amber" }]);

  assert.equal(typeControl.classList.contains("change-cue-active"), true);
  assert.equal(typeControl.dataset.changeCueTone, "amber");
  assert.equal(typeControl.style.getPropertyValue("--change-cue-radius"), "5px");
  assert.equal(wholeColumn.classList.contains("change-cue-active"), false);
  manager.dispose();
});

test("transient cues preserve a target radius and borrow it from wrapped controls", () => {
  const roundedCard = fakeElement();
  roundedCard.computedStyle.borderRadius = "9px";
  const wrappedControl = fakeElement();
  const input = fakeElement();
  input.computedStyle.borderRadius = "6px";
  wrappedControl.querySelector = () => input;
  const root = {
    querySelectorAll: selector => selector === '[data-change-object-id="rounded"]'
      ? [roundedCard]
      : selector === '[data-change-object-id="wrapped"]'
        ? [wrappedControl]
        : [],
  };
  const manager = createTransientCueManager({
    root,
    schedule: () => 1,
    cancel: () => {},
    MutationObserverClass: null,
  });

  manager.show([
    { objectId: "rounded", tone: "amber" },
    { objectId: "wrapped", tone: "amber" },
  ]);

  assert.equal(roundedCard.style.getPropertyValue("--change-cue-radius"), "9px");
  assert.equal(wrappedControl.style.getPropertyValue("--change-cue-radius"), "6px");
  manager.clear();
  assert.equal(roundedCard.style.getPropertyValue("--change-cue-radius"), "");
  assert.equal(wrappedControl.style.getPropertyValue("--change-cue-radius"), "");
});

test("transient cues use a surviving parent when a deleted object is absent", () => {
  const table = fakeElement();
  const root = {
    querySelectorAll: selector => selector === '[data-change-object-id="table_1"]' ? [table] : [],
  };
  const manager = createTransientCueManager({
    root,
    schedule: () => 1,
    cancel: () => {},
    MutationObserverClass: null,
  });

  manager.show([{
    objectId: "column_deleted",
    fallbackId: "table_1",
    scope: "schema",
    tone: "amber",
  }]);

  assert.equal(table.classList.contains("change-cue-active"), true);
  manager.clear();
  assert.equal(table.classList.contains("change-cue-active"), false);
  assert.equal(table.dataset.changeCueTone, undefined);
});

test("whole-object cues avoid outlining nested field targets", () => {
  const card = fakeElement();
  const name = fakeElement();
  const root = {
    querySelectorAll: selector => {
      if (selector === '[data-change-object-id="table_1"][data-change-root]') return [card];
      if (selector === '[data-change-object-id="table_1"]') return [card, name];
      return [];
    },
  };
  const manager = createTransientCueManager({
    root,
    schedule: () => 1,
    cancel: () => {},
    MutationObserverClass: null,
  });

  manager.show([{ objectId: "table_1", tone: "amber" }]);

  assert.equal(card.classList.contains("change-cue-active"), true);
  assert.equal(name.classList.contains("change-cue-active"), false);
});

test("transient cues use one short animation window", () => {
  const card = fakeElement();
  let scheduledDelay = null;
  const manager = createTransientCueManager({
    root: {
      querySelectorAll: selector => selector === '[data-change-object-id="view_1"]'
        ? [card]
        : [],
    },
    schedule: (_callback, delay) => {
      scheduledDelay = delay;
      return 1;
    },
    cancel: () => {},
    MutationObserverClass: null,
  });

  manager.show([{ objectId: "view_1", tone: "purple" }]);

  assert.equal(scheduledDelay, 850);
  manager.dispose();
});

test("rerender observation does not restart a cue on the same element", () => {
  const card = fakeElement();
  const manager = createTransientCueManager({
    root: {
      querySelectorAll: selector => selector === '[data-change-object-id="view_1"]'
        ? [card]
        : [],
    },
    schedule: () => 1,
    cancel: () => {},
    MutationObserverClass: null,
  });

  manager.show([{ objectId: "view_1", tone: "purple" }]);
  manager.replay();

  assert.equal(card.changes.added, 1);
  assert.equal(card.changes.removed, 0);
  manager.dispose();
});
