import assert from "node:assert/strict";
import test from "node:test";

import { applyJsonDelta } from "../../src/schemii/common/web/assets/json-delta.js";

test("history deltas update a nested column type without mutating current state", () => {
  const current = {
    tables: [{ id: "table_1", columns: [{ id: "column_1", dataType: "text" }] }],
  };

  const preview = applyJsonDelta(current, [{
    operation: "replace",
    path: ["tables", 0, "columns", 0, "dataType"],
    value: "character varying(200)",
  }]);

  assert.equal(current.tables[0].columns[0].dataType, "text");
  assert.equal(preview.tables[0].columns[0].dataType, "character varying(200)");
});

test("history deltas apply stable-list operations in server order", () => {
  const current = { tables: [{ id: "a" }, { id: "b" }] };
  const preview = applyJsonDelta(current, [
    { operation: "remove", path: ["tables", 0] },
    { operation: "add", path: ["tables", 1], value: { id: "c" } },
  ]);

  assert.deepEqual(preview, { tables: [{ id: "b" }, { id: "c" }] });
});

test("history deltas reject prototype-polluting paths", () => {
  assert.throws(
    () => applyJsonDelta({}, [{ operation: "add", path: ["__proto__", "polluted"], value: true }]),
    /Invalid JSON delta path/,
  );
  assert.equal({}.polluted, undefined);
});
