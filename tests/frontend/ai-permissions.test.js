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
  assert.equal(permissionSummary({ designChanges: true }, actions), "Explain only");
  assert.equal(permissionSummary(permissionValues({ "indexes.create": "automatic" }, actions), actions), "1 of 2 actions");
  assert.equal(permissionSummary(permissionValues({ "indexes.create": "ask", "tables.delete": "ask" }, actions), actions), "2 of 2 actions");
});
