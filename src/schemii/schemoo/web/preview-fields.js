import { element } from "#common/dom.js";
import { createIconButton } from "/assets/common/ui.js";
import { installSortableList, reorderedValues } from "/assets/common/sortable.js";
import { modelSelect, disposeSelects } from "./select.js";
import { exposedFields } from "./model-state.js";
import { nodeColumns, fieldLabel } from "./model-columns.js";
import { tablePreviewAdditions, PREVIEW_OUTPUT_LIMIT } from "./preview-state.js";

const additions = new WeakMap();
const sorters = new WeakMap();
const fieldKey = field => JSON.stringify([field.table, field.column]);
const sameOutput = (a, b) => fieldKey(a) === fieldKey(b) && (a.aggregate || "none") === (b.aggregate || "none");
function action(icon, label, callback, disabled = false) {
  const button = createIconButton({ icon, label, className: "ui-button" });
  button.disabled = disabled; button.onclick = callback; return button;
}
function aggregates(field, draft, catalog) {
  const node = draft.nodes.find(n => n.id === field?.table);
  const type = nodeColumns(draft, catalog, node).find(c => c.name === field?.column)?.dataType || "";
  if (node?.derivation?.kind === "aggregate") return [["none", "Summary value"]];
  const numeric = /^(smallint|integer|bigint|numeric|decimal|real|double precision)/.test(type);
  const comparable = numeric || /^(date|timestamp|time|text|character|varchar)/.test(type);
  return [["none", "Plain field"], ["count", "Count"], ["count_distinct", "Count distinct"],
    ...(numeric ? [["sum", "Sum"], ["avg", "Average"]] : []), ...(comparable ? [["min", "Minimum"], ["max", "Maximum"]] : [])];
}

/** Preview-only editing. Never mutates exposedFields or other model rules. */
export function renderPreviewFields(host, { draft, catalog, onChange }) {
  sorters.get(host)?.destroy();
  disposeSelects(host); host.replaceChildren();
  const label = f => fieldLabel(draft, catalog, f);
  const available = exposedFields(draft, catalog);
  let add = additions.get(host);
  if (!add || add.draft !== draft) { add = { draft, field: "", aggregate: "none", table: draft.root }; additions.set(host, add); }
  if (!available.some(f => fieldKey(f) === add.field)) add.field = "";
  const refresh = () => renderPreviewFields(host, { draft, catalog, onChange });
  const tables = draft.nodes.map(node => ({ node, ...tablePreviewAdditions(draft, catalog, node.id) })).filter(table => table.total);
  if (!tables.some(table => table.node.id === add.table)) add.table = "";
  const table = tables.find(table => table.node.id === add.table);
  const tableHint = !tables.length ? "No exposed columns to add. Enable columns in the model first."
    : !table ? "Choose a table or alias to add its exposed columns."
    : !table.fields.length ? "All exposed columns from this table are already included."
    : !table.canAdd ? `This table needs ${table.fields.length} outputs, but only ${table.remaining} of ${PREVIEW_OUTPUT_LIMIT} slots remain. Remove outputs or add columns individually; nothing will be partially added.`
    : `Add ${table.fields.length} column${table.fields.length === 1 ? "" : "s"} in table order. Existing outputs and measures stay unchanged.`;
  const tableChooser = element("div", { className: "preview-add-table" }, [
    modelSelect("Preview table", tables.map(({ node, total }) => [node.id, `${node.label || node.table} · ${total} exposed column${total === 1 ? "" : "s"}`]), add.table, value => { add.table = value; refresh(); }),
    action("add", "Add table columns to preview", () => {
      const additions = tablePreviewAdditions(draft, catalog, add.table);
      if (additions.canAdd) { draft.fields.push(...additions.fields); onChange(); }
    }, !table?.canAdd),
  ]);
  host.append(element("div", { className: "preview-table-section" }, [
    element("strong", { text: "Add by table" }), tableChooser,
    element("p", { className: "hint", text: tableHint, attrs: { role: "status" } }),
  ]));
  const chosen = available.find(f => fieldKey(f) === add.field);
  const next = chosen && { table: chosen.table, column: chosen.column, aggregate: add.aggregate };
  const duplicate = next && draft.fields.some(f => sameOutput(f, next));
  const chooser = element("div", { className: "preview-add" }, [
    modelSelect("Preview source field", available.map(f => [fieldKey(f), label(f)]), add.field, value => { add.field = value; add.aggregate = "none"; refresh(); }),
    modelSelect("Preview aggregation", aggregates(chosen, draft, catalog), add.aggregate, value => { add.aggregate = value; refresh(); }),
    action("add", "Add preview output", () => { if (next && !duplicate && draft.fields.length < PREVIEW_OUTPUT_LIMIT) { draft.fields.push(next); onChange(); } }, !chosen || duplicate || draft.fields.length >= PREVIEW_OUTPUT_LIMIT),
  ]);
  host.append(chooser, element("p", { className: "hint", text: !available.length ? "No exposed fields. Enable columns on the canvas or in the table inspector first." : duplicate ? "That output is already in the preview. Choose another aggregation to compare the same source field." : "Add exposed fields or measures. Plain fields become GROUP BY keys when measures are selected. Preview is limited to 64 outputs." }));
  const list = element("div", { className: "preview-outputs", attrs: { "aria-label": "Ordered preview outputs" } });
  draft.fields.forEach((field, index) => {
    const options = aggregates(field, draft, catalog).filter(([aggregate]) => !draft.fields.some((other, i) => i !== index && sameOutput(other, { ...field, aggregate })));
    const handle = action("drag", `Reorder preview column ${index + 1}`, () => {});
    handle.dataset.sortHandle = "";
    handle.classList.add("preview-output-handle");
    const row = element("div", { className: "preview-output", dataset: { outputIndex: index, sortKey: JSON.stringify([field.table, field.column, field.aggregate || "none"]) } }, [
      handle,
      element("div", { className: "field-name", text: label(field) }),
      modelSelect(`Preview column ${index + 1} aggregation`, options, field.aggregate || "none", value => { field.aggregate = value; onChange(); }),
      element("div", { className: "preview-output-actions" }, [
        action("earlier", `Move preview column ${index + 1} earlier`, () => { draft.fields = reorderedValues(draft.fields, index, index - 1); onChange(); }, index === 0),
        action("later", `Move preview column ${index + 1} later`, () => { draft.fields = reorderedValues(draft.fields, index, index + 1); onChange(); }, index === draft.fields.length - 1),
        action("close", `Remove preview column ${index + 1}`, () => { draft.fields.splice(index, 1); onChange(); }),
      ]),
    ]);
    list.append(row);
  });
  host.append(list);
  sorters.set(host, installSortableList(list, {
    itemSelector: ".preview-output",
    itemLabel: row => `${label(draft.fields[Number(row.dataset.outputIndex)])}, ${draft.fields[Number(row.dataset.outputIndex)].aggregate || "none"}`,
    onReorder: (from, to, { input }) => {
      draft.fields = reorderedValues(draft.fields, from, to);
      onChange();
      if (input === "keyboard") host.querySelectorAll("[data-sort-handle]")[to]?.focus();
    },
  }));
  if (!draft.fields.length) host.append(element("p", { className: "hint", text: "Add a preview output above. Model exposure stays unchanged." }));
}
