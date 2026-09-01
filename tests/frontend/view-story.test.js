import assert from "node:assert/strict";
import test from "node:test";

import {
  resultGrain,
  transformationLabel,
  viewStorySummary,
} from "../../src/schemii/schemii/web/assets/view-story.js";

test("view story summary leads with the source-derived result grain", () => {
  const analysis = {
    sources: [{ name: "orders" }, { name: "customers" }],
    outputs: [{ name: "customer_id" }, { name: "total" }],
    grouping: [{ expression: "customers.id" }],
  };
  assert.equal(resultGrain(analysis), "One row per customers.id");
  assert.equal(viewStorySummary(analysis), "One row per customers.id, producing 2 columns from 2 source relations.");
  assert.equal(transformationLabel("aggregates"), "Calculate aggregates");
});

test("view story distinguishes pass-through and relation-free queries", () => {
  assert.equal(resultGrain({ sources: [{ name: "orders" }], outputs: [{ name: "id", derivation: "direct" }] }), "One row per matching source row");
  assert.equal(resultGrain({ sources: [{ name: "orders" }], outputs: [{ name: "total", derivation: "aggregate" }] }), "One aggregate row");
  assert.equal(resultGrain({ sources: [], outputs: [{ name: "one", derivation: "constant" }] }), "One constructed row");
});

test("view story keeps grouping expressions compact at a glance", () => {
  const analysis = {
    sources: [{ name: "orders" }],
    outputs: [{ name: "total", derivation: "aggregate" }],
    grouping: ["region", "year", "month", "channel"].map(expression => ({ expression })),
  };
  assert.equal(resultGrain(analysis), "One row per region + year + month + 1 more");
});

test("view story derives final grain without flattening CTE grouping", () => {
  const analysis = {
    sources: [{ name: "orders" }],
    outputs: [{ name: "customer_id" }, { name: "lifetime_value", derivation: "aggregate" }],
    grouping: [
      { expression: "customer_id", scope: "paid_orders" },
      { expression: "region", scope: null },
    ],
  };
  assert.equal(resultGrain(analysis), "One row per region");
  assert.equal(viewStorySummary(analysis), "One row per region, producing 2 columns from 1 source relation.");
});
