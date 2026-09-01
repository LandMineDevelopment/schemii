import assert from "node:assert/strict";
import test from "node:test";

import { transformationLabel, viewStorySummary } from "../../src/schemii/schemii/web/assets/view-story.js";

test("view story summary is derived from analysis counts", () => {
  const analysis = {
    sources: [{ name: "orders" }, { name: "customers" }],
    outputs: [{ name: "customer_id" }, { name: "total" }],
    transformations: [{ kind: "joins", count: 1 }, { kind: "groups", count: 2 }],
  };
  assert.equal(viewStorySummary(analysis), "2 inputs pass through 3 operations to produce 2 outputs.");
  assert.equal(transformationLabel("aggregates"), "Calculate aggregates");
});

test("view story distinguishes pass-through and relation-free queries", () => {
  assert.equal(viewStorySummary({ sources: [{ name: "orders" }], outputs: [{ name: "id" }], transformations: [] }), "1 input passed directly into 1 output.");
  assert.equal(viewStorySummary({ sources: [{ name: "orders" }], outputs: [{ name: "total" }], transformations: [{ kind: "aggregates", count: 1 }] }), "1 input passes through 1 operation to produce 1 output.");
  assert.equal(viewStorySummary({ sources: [], outputs: [{ name: "one" }], transformations: [] }), "1 output produced without a relation input.");
});
