import assert from "node:assert/strict";
import test from "node:test";

import {
  expressionSegments,
  queryStoryPhases,
  resultGrain,
  selectProjectionMappings,
  selectProjectionsForColumn,
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

test("view story keeps intermediate outputs in their operation and renders only the final result card", () => {
  const analysis = {
    querySteps: [
      { ordinal: 1, kind: "cte", resultName: "paid_orders" },
      { ordinal: 2, kind: "final", resultName: "view result" },
    ],
  };
  assert.deepEqual(
    queryStoryPhases(analysis).map(phase => [phase.kind, phase.step.resultName]),
    [
      ["operation", "paid_orders"],
      ["operation", "view result"],
      ["result", "view result"],
    ],
  );
});

test("view story distinguishes output aliases from source-column references", () => {
  const step = {
    participants: [{ name: "payments", reference: "p" }],
    outputs: [{ ordinal: 1, name: "captured_revenue" }],
  };
  const segments = expressionSegments("p.amount + captured_revenue DESC", step);
  assert.equal(segments.find(segment => segment.text === "p.amount")?.className, "view-accent-text-0");
  assert.equal(segments.find(segment => segment.text === "captured_revenue")?.className, "view-output-reference");
  assert.equal(
    expressionSegments("'captured_revenue' = captured_revenue", step)
      .filter(segment => segment.className === "view-output-reference").length,
    1,
  );
});

test("view story defines selected output aliases from their source expressions", () => {
  const step = {
    outputs: [
      { ordinal: 1, name: "customer_id", expression: "o.customer_id", derivation: "direct", inputs: [{ source: "orders", column: "customer_id" }] },
      { ordinal: 2, name: "captured_revenue", expression: "SUM(p.amount)", derivation: "aggregate", inputs: [{ source: "payments", column: "amount" }] },
    ],
  };
  assert.deepEqual(selectProjectionMappings(step), [
    { expression: "o.customer_id", alias: "customer_id", derivation: "direct", inputs: [{ source: "orders", column: "customer_id" }] },
    { expression: "SUM(p.amount)", alias: "captured_revenue", derivation: "aggregate", inputs: [{ source: "payments", column: "amount" }] },
  ]);
  assert.deepEqual(
    selectProjectionsForColumn(step, { name: "payments", reference: "p" }, "amount"),
    [{ expression: "SUM(p.amount)", alias: "captured_revenue", derivation: "aggregate", inputs: [{ source: "payments", column: "amount" }] }],
  );
});
