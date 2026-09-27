import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import test from "node:test";

const source = readFileSync(new URL("../../src/schemii/schemii/web/assets/ai-assistant.js", import.meta.url), "utf8");
const reviewSource = source.slice(source.indexOf("function proposalDetailsNode("), source.indexOf("function openProposalReview("));

class Element {
  constructor(tagName) { this.tagName = tagName; this.children = []; this.ownText = ""; this.className = ""; }
  set textContent(value) { this.ownText = String(value); this.children = []; }
  get textContent() { return this.ownText + this.children.map(child => child.textContent).join(""); }
  append(...children) { this.children.push(...children); }
}

const render = runInNewContext(`${reviewSource}\nproposalDetailsNode`, {
  document: { createElement: tagName => new Element(tagName) },
  actionPresentation: () => ({ scope: "Saves the workspace design only." }),
  design: { content: { tables: [{ id: "table_existing", name: "existing_table" }] } },
});

function nodes(root, className) {
  return [root, ...root.children.flatMap(child => nodes(child, className))]
    .filter(node => node.className === className);
}

function columnProperties(column) {
  const entries = nodes(column, "ai-proposal-review__column-properties")[0].children;
  return Object.fromEntries(Array.from({ length: entries.length / 2 }, (_, index) => [
    entries[index * 2].textContent, entries[index * 2 + 1].textContent,
  ]));
}

test("two-table approval shows identity, defaults, generated expressions and full types", () => {
  const review = render({
    summary: "Create project and task tables", capability: "design_changes", actionType: "design_change", destructive: false,
    details: { type: "batch", actions: [
      { type: "add_table", name: "qa_projects", columns: [
        { name: "project_id", data_type: "bigint", nullable: false, identity: "always" },
        { name: "budget", data_type: "numeric(12,2)", nullable: false, default_expression: "0" },
      ], keys: [{ kind: "primary", columns: ["project_id"] }] },
      { type: "add_table", name: "qa_tasks", columns: [
        { name: "task_id", dataType: "bigint", nullable: false, identity: "by_default" },
        { name: "total_hours", data_type: "numeric(6,2)", nullable: true,
          generated_expression: "hours + overtime_hours", generated_source_column_ids: ["column_hours", "column_overtime"] },
      ], keys: [] },
    ] },
  });
  const columns = nodes(review, "ai-proposal-review__column");
  assert.equal(columns.length, 4);
  assert.match(review.textContent, /Scroll through every change before saving/);
  assert.match(review.textContent, /primary: project_id/);
  assert.deepEqual(columnProperties(columns[0]), { Identity: "always", Default: "none", Generated: "none" });
  assert.deepEqual(columnProperties(columns[1]), { Identity: "none", Default: "0", Generated: "none" });
  assert.match(columns[1].textContent, /numeric\(12,2\)/);
  assert.deepEqual(columnProperties(columns[2]), { Identity: "by default", Default: "none", Generated: "none" });
  assert.deepEqual(columnProperties(columns[3]), {
    Identity: "none", Default: "none", Generated: "hours + overtime_hours",
    "Source columns": "column_hours, column_overtime",
  });
});

test("added column approval shows its target and nullable default", () => {
  const review = render({
    summary: "Add state", capability: "design_changes", actionType: "design_change", destructive: false,
    details: { type: "add_column", table_id: "table_existing", column: {
      name: "state", data_type: "varchar(16)", nullable: false, default_expression: "'todo'",
    } },
  });
  assert.match(review.textContent, /COLUMN FOR existing_table/);
  const column = nodes(review, "ai-proposal-review__column")[0];
  assert.match(column.textContent, /statevarchar\(16\)required/);
  assert.deepEqual(columnProperties(column), { Identity: "none", Default: "'todo'", Generated: "none" });
});
