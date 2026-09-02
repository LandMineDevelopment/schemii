import assert from "node:assert/strict";
import test from "node:test";

import {
  filterSearchableSelectOptions,
  matchingSearchableSelectOption,
  normalizeSearchableSelectOptions,
} from "../../src/schemii/common/web/assets/searchable-select.js";
import {
  composePostgresTypeModifier,
  parsePostgresTypeModifier,
  postgresTypeModifierSummary,
  postgresTypeOptions,
} from "../../src/schemii/common/web/assets/postgres-types.js";

test("searchable select matches labels, groups, descriptions, and aliases", () => {
  const options = normalizeSearchableSelectOptions([
    { value: "timestamp with time zone", group: "Date and time", keywords: "timestamptz" },
    { value: "jsonb", group: "Structured data", description: "Binary JSON" },
  ]);

  assert.deepEqual(filterSearchableSelectOptions(options, "timestamptz").map(option => option.value), ["timestamp with time zone"]);
  assert.deepEqual(filterSearchableSelectOptions(options, "binary").map(option => option.value), ["jsonb"]);
  assert.deepEqual(filterSearchableSelectOptions(options, "structured").map(option => option.value), ["jsonb"]);
});

test("searchable select requires an exact option while matching case-insensitively", () => {
  const options = normalizeSearchableSelectOptions(["uuid", "text"]);

  assert.equal(matchingSearchableSelectOption(options, " UUID ")?.value, "uuid");
  assert.equal(matchingSearchableSelectOption(options, "uuid-ish"), null);
});

test("PostgreSQL type options include source-derived types and preserve existing modifiers", () => {
  const options = postgresTypeOptions({
    customTypes: [{ name: "order_status", kind: "enum", enumValues: ["draft", "paid"] }],
    currentValue: "numeric(19,4)",
  });

  assert.equal(options.find(option => option.value === "order_status")?.group, "Designed types");
  assert.equal(options.find(option => option.value === "numeric(19,4)")?.group, "Current type");
  assert.equal(options.filter(option => option.value === "uuid").length, 1);
});

test("PostgreSQL type modifiers round-trip character and numeric limits", () => {
  const varchar = parsePostgresTypeModifier("varchar(80)");
  const numeric = parsePostgresTypeModifier("NUMERIC(14, 4)");

  assert.equal(varchar.length, 80);
  assert.equal(postgresTypeModifierSummary(varchar), "Length 80");
  assert.equal(composePostgresTypeModifier(varchar, { length: "160" }), "varchar(160)");
  assert.equal(numeric.precision, 14);
  assert.equal(numeric.scale, 4);
  assert.equal(composePostgresTypeModifier(numeric, { precision: "20", scale: "-2" }), "numeric(20,-2)");
});

test("PostgreSQL time precision keeps the modifier before the zone qualifier", () => {
  const timestamp = parsePostgresTypeModifier("timestamp(3) with time zone");

  assert.equal(timestamp.precision, 3);
  assert.equal(composePostgresTypeModifier(timestamp, { precision: "6" }), "timestamp(6) with time zone");
});

test("PostgreSQL type modifier limits reject invalid ranges", () => {
  const numeric = parsePostgresTypeModifier("numeric");
  const varchar = parsePostgresTypeModifier("varchar");

  assert.throws(() => composePostgresTypeModifier(numeric, { scale: "2" }), /Set precision/);
  assert.throws(() => composePostgresTypeModifier(numeric, { precision: "1001" }), /between 1 and 1000/);
  assert.throws(() => composePostgresTypeModifier(varchar, { length: "0" }), /between 1/);
});
