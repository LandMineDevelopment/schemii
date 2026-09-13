import assert from "node:assert/strict";
import test from "node:test";
import { permissionMode, permissionValues, permissionSummary } from "../../src/schemii/common/web/assets/ai-permissions.js";

const actions = [
  { id: "tables.delete", label: "Delete tables", group: "Tables" },
  { id: "indexes.create", label: "Create indexes", group: "Indexes" },
];

test("individual actions round-trip independently without broad flags", () => {
  for (const mode of ["disabled", "ask", "automatic"]) {
    const values = permissionValues({ "tables.delete": mode, "indexes.create": "automatic" }, actions);
    assert.equal(permissionMode(values, "tables.delete"), mode);
    assert.equal(permissionMode(values, "indexes.create"), "automatic");
    assert.deepEqual(Object.keys(values), ["actionModes"]);
  }
});

test("missing and invalid values fail closed and unknown actions are excluded", () => {
  const values = permissionValues({ "tables.delete": "unexpected", "unknown.action": "automatic" }, actions);
  assert.deepEqual(values, { actionModes: { "tables.delete": "disabled", "indexes.create": "disabled" } });
  assert.equal(permissionMode({}, "tables.delete"), "disabled");
  assert.equal(permissionMode({ actionModes: { "tables.delete": "unexpected" } }, "tables.delete"), "disabled");
});

test("summary counts actual advertised executable actions, not broad capability flags", () => {
  assert.equal(permissionSummary({ designChanges: true }, actions), "No actions enabled");
  assert.equal(permissionSummary(permissionValues({ "indexes.create": "automatic" }, actions), actions), "1 of 2 actions");
  assert.equal(permissionSummary(permissionValues({ "indexes.create": "ask", "tables.delete": "ask" }, actions), actions), "2 of 2 actions");
});

const { permissionBundle, permissionDraft } = await import('../../src/schemii/common/web/assets/ai-permissions.js');

test('bundles split design object actions while retaining advertised operational groups', () => {
  assert.equal(permissionBundle({ id: 'tables.create', group: 'Design' }), 'Tables');
  assert.equal(permissionBundle({ id: 'columns.delete', group: 'Design' }), 'Columns');
  assert.equal(permissionBundle({ id: 'query.write', group: 'Queries' }), 'Queries');
});

test('selection never grants permission and subset application retains independent modes', () => {
  const initial = { 'tables.delete': 'disabled', 'indexes.create': 'ask' };
  const draft = permissionDraft(actions, initial);
  draft.select(['tables.delete'], true);
  assert.deepEqual(draft.values, initial);
  assert.deepEqual(draft.state(actions.map(a => a.id)), { checked: false, indeterminate: true });
  draft.apply('automatic');
  assert.deepEqual(draft.values, { 'tables.delete': 'automatic', 'indexes.create': 'ask' });
  assert.deepEqual(initial, { 'tables.delete': 'disabled', 'indexes.create': 'ask' });
  draft.values['tables.delete'] = 'ask';
  assert.equal(draft.values['tables.delete'], 'ask');
});

test('select all, deselect subset, and reopen preserve modes but reset selection', () => {
  const ids = actions.map(a => a.id), draft = permissionDraft(actions);
  draft.select(ids, true);
  assert.deepEqual(draft.state(ids), { checked: true, indeterminate: false });
  draft.select(['tables.delete'], false);
  draft.apply('automatic');
  assert.deepEqual(draft.values, { 'tables.delete': 'disabled', 'indexes.create': 'automatic' });
  const saved = permissionValues(draft.values, actions);
  const reopened = permissionDraft(actions, saved.actionModes);
  assert.deepEqual(reopened.values, draft.values);
  assert.deepEqual(reopened.state(ids), { checked: false, indeterminate: false });
});

test('unknown selections and invalid bulk modes cannot expand advertised permissions', () => {
  const draft = permissionDraft(actions);
  draft.select(['unknown.action', 'tables.delete'], true);
  assert.deepEqual([...draft.selected], ['tables.delete']);
  draft.apply('unexpected');
  assert.deepEqual(draft.values, { 'tables.delete': 'disabled', 'indexes.create': 'disabled' });
});
