import assert from "node:assert/strict";
import test from "node:test";
import { createDesignEditorControllers } from "../../src/schemii/schemii/web/assets/design-editors.js";
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


function editorFixture() {
  const confirmations = [];
  const saves = [];
  const node = () => ({ value: "", checked: false, open: false, reset() {}, append() {}, focus() {}, close() { this.open = false; } });
  const elements = new Proxy({}, { get(target, key) { return target[key] ||= node(); } });
  const state = {
    activeWorkspace: { id: "workspace" },
    design: { content: { tables: [{ id: "table", name: "orders" }], views: [], types: [], functions: [], triggers: [] } },
  };
  let saveResult = async () => ({ revision: 2 });
  let flushResult = async () => true;
  const helpers = new Proxy({
    isDesignWorkspace: () => true,
    quoteSqlIdentifier: value => value,
    openDialog: dialog => { dialog.open = true; },
    element: node,
    askConfirmation: options => confirmations.push(options),
    selectedDesignTable: () => null,
    flushLayoutBeforeTransition: () => flushResult(),
    replaceActiveDesign: content => saveResult(content),
  }, { get(target, key) {
    if (key.startsWith("saveDesign")) return (content, values) => {
      saves.push(values);
      const object = { id: "saved-object", name: "saved", definition: values.definition };
      return { content, view: object, designType: object, routine: object, trigger: object };
    };
    return target[key] || (() => {});
  } });
  const api = new Proxy({}, { get: () => async () => ({
    kind: "enum", name: "saved", enumValues: [], identityArguments: "", language: "sql",
    relationName: "orders", timing: "after", events: [], functionArguments: [],
    referencedColumns: [], transitionRelations: [],
  }) });
  const controller = createDesignEditorControllers({ api, state, elements, helpers });
  return { controller, state, elements, confirmations, saves, setFlush: callback => { flushResult = callback; }, setSave: callback => { saveResult = callback; } };
}

for (const kind of ["View", "Type", "Routine", "Trigger"]) {
  test(`${kind} drafts ignore untouched and reverted fields, preserve cancelled closes and discard once`, async () => {
    const { controller, elements, confirmations } = editorFixture();
    try {
      await controller[`openDesign${kind}Editor`]();
      const definition = elements[`design${kind}Definition`];
      const dialog = elements[`design${kind}Dialog`];
      const initial = definition.value;
      assert.equal(controller.hasDraft, false);
      definition.value += " edited";
      assert.equal(controller.hasDraft, true);
      definition.value = initial;
      assert.equal(controller.hasDraft, false);
      definition.value += " retained";
      const close = controller.requestDiscardDrafts();
      assert.equal(confirmations.length, 1);
      assert.equal(confirmations[0].cancelLabel, "Keep editing");
      assert.equal(await controller.requestDiscardDrafts(), false);
      assert.equal(confirmations.length, 1, "a pending decision must not create another prompt");
      confirmations[0].onCancel();
      assert.equal(await close, false);
      assert.equal(dialog.open, true);
      assert.equal(definition.value, initial + " retained");
      assert.equal(controller.hasDraft, true);
      const discard = controller.requestDiscardDrafts();
      confirmations[1].callback();
      assert.equal(await discard, true);
      assert.equal(dialog.open, false);
      assert.equal(controller.hasDraft, false);
      assert.equal(await controller.requestDiscardDrafts(), true);
      assert.equal(confirmations.length, 2);
    } finally { controller.discardDrafts(); }
  });

  test(`${kind} editor replacement waits for discard and a delayed close cannot reset the new editor`, async () => {
    const { controller, elements, confirmations } = editorFixture();
    try {
      await controller[`openDesign${kind}Editor`]();
      const definition = elements[`design${kind}Definition`];
      definition.value += " old draft";
      const cancelledReplacement = controller[`openDesign${kind}Editor`]();
      confirmations[0].onCancel();
      await cancelledReplacement;
      assert.match(definition.value, /old draft$/);
      const replacement = controller[`openDesign${kind}Editor`]();
      confirmations[1].callback();
      await replacement;
      assert.doesNotMatch(definition.value, /old draft$/);
      controller[`closeDesign${kind}Editor`]();
      definition.value += " new draft";
      assert.equal(controller.hasDraft, true);
    } finally { controller.discardDrafts(); }
  });

  test(`${kind} failed saves retain dirty state and successful retries clear it without confirmation`, async () => {
    const { controller, elements, confirmations, setSave } = editorFixture();
    try {
      await controller[`openDesign${kind}Editor`]();
      elements[`design${kind}Definition`].value += " changed";
      setSave(async () => { throw new Error("controlled failure"); });
      await controller[`submitDesign${kind}`]({ preventDefault() {} });
      assert.equal(elements[`design${kind}Dialog`].open, true);
      assert.equal(controller.hasDraft, true);
      assert.equal(confirmations.length, 0);
      setSave(async () => ({ revision: 2 }));
      await controller[`submitDesign${kind}`]({ preventDefault() {} });
      assert.equal(elements[`design${kind}Dialog`].open, false);
      assert.equal(controller.hasDraft, false);
      assert.equal(confirmations.length, 0);
    } finally { controller.discardDrafts(); }
  });

  test(`${kind} edits during save remain protected and retry updates the saved object's identity`, async () => {
    const { controller, elements, confirmations, saves, setSave } = editorFixture();
    try {
      await controller[`openDesign${kind}Editor`]();
      const definition = elements[`design${kind}Definition`];
      definition.value += " submitted";
      const saved = deferred();
      setSave(() => saved.promise);
      const submit = controller[`submitDesign${kind}`]({ preventDefault() {} });
      await settle();
      assert.equal(await controller.requestDiscardDrafts(), false);
      assert.equal(confirmations.length, 0);
      definition.value += " newer";
      saved.resolve({ revision: 2 });
      await submit;
      assert.equal(elements[`design${kind}Dialog`].open, true);
      assert.equal(controller.hasDraft, true);
      setSave(async () => ({ revision: 3 }));
      await controller[`submitDesign${kind}`]({ preventDefault() {} });
      assert.equal(saves.at(-1)[kind === "Routine" ? "routineId" : `${kind.toLowerCase()}Id`], "saved-object");
      assert.equal(controller.hasDraft, false);
    } finally { controller.discardDrafts(); }
  });
}

test("view name, kind and materialized-view population participate in dirty tracking", async () => {
  const { controller, elements, state } = editorFixture();
  try {
    await controller.openDesignViewEditor();
    elements.designViewName.value = "renamed";
    assert.equal(controller.hasDraft, true);
    elements.designViewName.value = "";
    assert.equal(controller.hasDraft, false);
    elements.designViewKind.value = "materialized_view";
    assert.equal(controller.hasDraft, true);
    controller.discardDrafts();
    await controller.openDesignViewEditor();
    elements.designViewPopulate.checked = !elements.designViewPopulate.checked;
    assert.equal(controller.hasDraft, false, "population does not affect an ordinary view");
    elements.designViewKind.value = "materialized_view";
    const saved = controller.submitDesignView({ preventDefault() {} });
    await saved;
    assert.equal(controller.hasDraft, false);
    state.design.content.views.push({ id: "materialized", name: "saved_view", kind: "materialized_view", definition: "SELECT 1", populateOnCreate: true });
    await controller.openDesignViewEditor("materialized");
    elements.designViewPopulate.checked = false;
    assert.equal(controller.hasDraft, true);
    elements.designViewPopulate.checked = true;
    assert.equal(controller.hasDraft, false);
  } finally { controller.discardDrafts(); }
});


test("view submission owns the draft before its layout flush can yield to a discard", async () => {
  const { controller, state, elements, confirmations, setFlush, setSave } = editorFixture();
  const layout = deferred();
  let writes = 0;
  try {
    await controller.openDesignViewEditor();
    elements.designViewDefinition.value += " submitted";
    setFlush(() => layout.promise);
    setSave(async () => { writes++; return { revision: 2 }; });
    const submitted = controller.submitDesignView({ preventDefault() {} });
    assert.equal(state.designSubmitting, true, "submitting must be set before the first asynchronous boundary");
    assert.equal(await controller.requestDiscardDrafts(), false);
    assert.equal(elements.designViewDialog.open, true);
    assert.equal(confirmations.length, 0);
    assert.equal(writes, 0, "layout is still pending");
    layout.resolve(true);
    await submitted;
    assert.equal(writes, 1);
    assert.equal(controller.hasDraft, false);
  } finally { layout.resolve(true); controller.discardDrafts(); }
});
