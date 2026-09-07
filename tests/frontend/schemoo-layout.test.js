import test from "node:test";
import assert from "node:assert/strict";
import { importedDraft } from "../../src/schemii/schemoo/web/model-draft.js";

test("new models copy Schemii positions by table name and own subsequent changes", () => {
  const catalog = { tables: ["people", "org", "new_table"].map(name => ({ name, columns: [{ name: "id" }] })),
    relationships: [], positions: [{ name: "org", x: -250, y: 0 }, { name: "people", x: 800, y: 120 }] };
  const draft = importedDraft(catalog);
  assert.deepEqual(draft.nodes.map(({ x, y }) => [x, y]), [[800, 120], [-250, 0], [undefined, undefined]]);
  draft.nodes[0].x = 999;
  const restored = JSON.parse(JSON.stringify(draft));
  assert.equal(restored.nodes[0].x, 999);
  assert.equal(catalog.positions[1].x, 800);
  assert.equal(importedDraft(catalog).nodes[0].x, 800);
});
