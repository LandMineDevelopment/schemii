import assert from "node:assert/strict";
import test from "node:test";

import {
  compositeProjectionMappings,
  expressionSegments,
  queryStoryPhases,
  resultGrain,
  selectProjectionMappings,
  selectProjectionsForColumn,
  transformationLabel,
  queryStorySummary,
} from "../../src/schemii/common/web/assets/query-story.js";

test("query story summary leads with the source-derived result grain", () => {
  const analysis = {
    sources: [{ name: "orders" }, { name: "customers" }],
    outputs: [{ name: "customer_id" }, { name: "total" }],
    grouping: [{ expression: "customers.id" }],
  };
  assert.equal(resultGrain(analysis), "One row per customers.id");
  assert.equal(queryStorySummary(analysis), "One row per customers.id, producing 2 columns from 2 source relations.");
  assert.equal(transformationLabel("aggregates"), "Calculate aggregates");
});

test("query story distinguishes pass-through and relation-free queries", () => {
  assert.equal(resultGrain({ sources: [{ name: "orders" }], outputs: [{ name: "id", derivation: "direct" }] }), "One row per matching source row");
  assert.equal(resultGrain({ sources: [{ name: "orders" }], outputs: [{ name: "total", derivation: "aggregate" }] }), "One aggregate row");
  assert.equal(resultGrain({ sources: [], outputs: [{ name: "one", derivation: "constant" }] }), "One constructed row");
});

test("query story keeps grouping expressions compact at a glance", () => {
  const analysis = {
    sources: [{ name: "orders" }],
    outputs: [{ name: "total", derivation: "aggregate" }],
    grouping: ["region", "year", "month", "channel"].map(expression => ({ expression })),
  };
  assert.equal(resultGrain(analysis), "One row per region + year + month + 1 more");
});

test("query story derives final grain without flattening CTE grouping", () => {
  const analysis = {
    sources: [{ name: "orders" }],
    outputs: [{ name: "customer_id" }, { name: "lifetime_value", derivation: "aggregate" }],
    grouping: [
      { expression: "customer_id", scope: "paid_orders" },
      { expression: "region", scope: null },
    ],
  };
  assert.equal(resultGrain(analysis), "One row per region");
  assert.equal(queryStorySummary(analysis), "One row per region, producing 2 columns from 1 source relation.");
});

test("query story keeps intermediate outputs in their operation and renders only the final result card", () => {
  const analysis = {
    querySteps: [
      { ordinal: 1, kind: "cte", resultName: "paid_orders" },
      { ordinal: 2, kind: "final", resultName: "query result" },
    ],
  };
  assert.deepEqual(
    queryStoryPhases(analysis).map(phase => [phase.kind, phase.step.resultName]),
    [
      ["operation", "paid_orders"],
      ["operation", "query result"],
      ["result", "query result"],
    ],
  );
});

test("query story distinguishes output aliases from source-column references", () => {
  const step = {
    participants: [{ name: "payments", reference: "p" }],
    outputs: [{ ordinal: 1, name: "captured_revenue" }],
  };
  const segments = expressionSegments("p.amount + captured_revenue DESC", step);
  assert.equal(segments.find(segment => segment.text === "p.amount")?.className, "query-accent-text-0");
  assert.equal(segments.find(segment => segment.text === "captured_revenue")?.className, "query-output-reference");
  assert.equal(
    expressionSegments("'captured_revenue' = captured_revenue", step)
      .filter(segment => segment.className === "query-output-reference").length,
    1,
  );
});

test("query story defines selected output aliases from their source expressions", () => {
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

test("query story renders multi-input transformations once instead of beside every input", () => {
  const mapping = {
    ordinal: 1,
    name: "recognized_revenue",
    expression: "items.gross - items.discount - payments.uncaptured",
    derivation: "expression",
    inputs: [
      { source: "items", column: "gross" },
      { source: "items", column: "discount" },
      { source: "payments", column: "uncaptured" },
    ],
  };
  const step = { outputs: [mapping] };

  assert.deepEqual(compositeProjectionMappings(step), [{
    expression: mapping.expression,
    alias: mapping.name,
    derivation: mapping.derivation,
    inputs: mapping.inputs,
  }]);
  assert.deepEqual(
    selectProjectionsForColumn(step, { name: "items", reference: "items" }, "gross"),
    [],
  );
});
