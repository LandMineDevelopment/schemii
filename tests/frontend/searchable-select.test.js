import assert from "node:assert/strict";
import test from "node:test";

import {
  filterSearchableSelectOptions,
  matchingSearchableSelectOption,
  normalizeSearchableSelectOptions,
  createSearchableSelect,
  searchableSelectPlacement,
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

test("searchable select optionally matches unique labels without guessing duplicate labels", () => {
  const options = normalizeSearchableSelectOptions([
    { value: "physical_id", label: "Organization" },
    { value: "alias_1", label: "Personnel" },
    { value: "alias_2", label: "Personnel" },
  ]);
  assert.equal(matchingSearchableSelectOption(options, "organization"), null);
  assert.equal(matchingSearchableSelectOption(options, " Organization ", { matchLabel: true })?.value, "physical_id");
  assert.equal(matchingSearchableSelectOption(options, "Personnel", { matchLabel: true }), null);
});

test("searchable select placement stays inside small and offset visual viewports", () => {
  for (const viewport of [
    { top: 0, left: 0, width: 360, height: 700 },
    { top: 300, left: 20, width: 320, height: 130 },
    { top: 0, left: 0, width: 200, height: 90 },
  ]) {
    for (const rect of [
      { top: 20, bottom: 56, left: 5, width: 450 },
      { top: 380, bottom: 416, left: 300, width: 160 },
      { top: 650, bottom: 686, left: -20, width: 120 },
    ]) {
      const placement = searchableSelectPlacement(rect, viewport, 900);
      assert.ok(placement.left >= viewport.left);
      assert.ok(placement.left + placement.width <= viewport.left + viewport.width);
      assert.ok(placement.top >= viewport.top);
      assert.ok(placement.top + placement.maxHeight <= viewport.top + viewport.height);
    }
  }
});

// Minimal DOM surface used by this component; no browser layout is simulated here.
function selectDocument() {
  class Node extends EventTarget {
    constructor() {
      super(); this.children = []; this.dataset = {}; this.style = {}; this.attributes = {};
      this.classList = { toggle() {} }; this.scrollHeight = 200; this.value = "";
    }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setCustomValidity(value) { this.validationMessage = value; }
    select() {}
    focus() { this.dispatchEvent(new Event("focus")); }
    scrollIntoView() {}
    contains(node) { return this === node || this.children.some(child => child.contains(node)); }
    querySelectorAll() { return this.children.filter(node => node.attributes.role === "option"); }
    getBoundingClientRect() { return { top: 20, bottom: 56, left: 20, width: 200 }; }
    remove() {}
  }
  const doc = new EventTarget();
  doc.createElement = () => new Node();
  doc.body = new Node();
  doc.defaultView = Object.assign(new EventTarget(), { innerWidth: 360, innerHeight: 700 });
  return doc;
}

test("dropdown remains owned by its open modal dialog rather than an inert body portal", () => {
  const documentRef=selectDocument(), dialog=documentRef.createElement("dialog");
  const control=createSearchableSelect({documentRef,options:["text","uuid"]});
  control.root.closest=selector=>selector==="dialog[open]" ? dialog : null;
  control.input.focus();
  assert.equal(dialog.children.length,1);
  assert.equal(dialog.children[0].attributes.role,"listbox");
  control.destroy();
});

test("label display preserves raw committed values and emits change only for commits", () => {
  const documentRef = selectDocument();
  const control = createSearchableSelect({
    documentRef, displayLabel: true, value: "org",
    options: [{ value: "org", label: "Organization" }, { value: "staff", label: "Personnel" }],
  });
  let changes = 0;
  control.input.addEventListener("change", () => { changes += 1; });
  assert.equal(control.input.value, "Organization");
  assert.equal(control.getValue(), "org");
  control.input.value = "Personnel";
  control.input.dispatchEvent(new Event("change"));
  assert.equal(control.getValue(), "staff");
  assert.equal(changes, 1);
  control.input.dispatchEvent(new Event("change"));
  assert.equal(changes, 1);
  control.input.value = "not an option";
  control.input.dispatchEvent(new Event("change"));
  assert.equal(control.input.value, "Personnel");
  assert.equal(control.getValue(), "staff");
  assert.equal(changes, 1);
  control.input.focus();
  control.input.value = "unfinished";
  control.input.dispatchEvent(new Event("input"));
  const escape = new Event("keydown");
  Object.defineProperty(escape, "key", { value: "Escape" });
  control.input.dispatchEvent(escape);
  assert.equal(control.input.value, "Personnel");
  assert.equal(control.input.validationMessage, "");
  control.setOptions([{ value: "staff", label: "Staff members" }]);
  assert.equal(control.input.value, "Staff members");
  assert.equal(control.getValue(), "staff");
  control.setValue("missing");
  assert.equal(control.getValue(), "");
  assert.equal(changes, 1);
  control.destroy();
});

test("existing searchable selects retain raw value display by default", () => {
  const control = createSearchableSelect({ documentRef: selectDocument(), value: "org", options: [{ value: "org", label: "Organization" }] });
  assert.equal(control.input.value, "org");
  assert.equal(control.getValue(), "org");
  control.destroy();
});

test("destroy suppresses reentrant blur changes and prevents detached controls reopening", () => {
  const documentRef = selectDocument();
  const control = createSearchableSelect({ documentRef, value: "org", options: ["org", "staff"] });
  let changes = 0;
  control.input.addEventListener("change", () => { changes += 1; });
  control.input.focus();
  control.input.value = "staff";
  const popup = documentRef.body.children[0];
  // DOM removal may synchronously blur the editor and emit its native change.
  popup.remove = () => { control.input.dispatchEvent(new Event("change")); };
  control.destroy();
  assert.equal(changes, 0);
  assert.equal(control.getValue(), "org");
  control.input.focus();
  control.input.dispatchEvent(new Event("input"));
  const enter = new Event("keydown");
  Object.defineProperty(enter, "key", { value: "Enter" });
  control.input.dispatchEvent(enter);
  assert.equal(control.input.attributes["aria-expanded"], "false");
  assert.equal(popup.hidden, true);
  assert.equal(changes, 0);
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
