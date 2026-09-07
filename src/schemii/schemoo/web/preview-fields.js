import { element } from "#common/dom.js";
import { createIconButton } from "/assets/common/ui.js";
import { reorderedValues } from "/assets/common/sortable.js";
import { modelSelect, disposeSelects } from "./select.js";
import { exposedFields } from "./model-state.js";

const additions = new WeakMap();
const fieldKey = field => JSON.stringify([field.table, field.column]);
const sameOutput = (a, b) => fieldKey(a) === fieldKey(b) && (a.aggregate || "none") === (b.aggregate || "none");
function action(icon, label, callback, disabled = false) {
  const button = createIconButton({ icon, label, className: "ui-button" });
  button.disabled = disabled; button.onclick = callback; return button;
}
function aggregates(field, draft, catalog) {
  const table = draft.nodes.find(n => n.id === field?.table)?.table;
  const type = catalog.tables.find(t => t.name === table)?.columns.find(c => c.name === field?.column)?.dataType || "";
  const numeric = /^(smallint|integer|bigint|numeric|decimal|real|double precision)/.test(type);
  const comparable = numeric || /^(date|timestamp|time|text|character|varchar)/.test(type);
  return [["none", "Plain field"], ["count", "Count"], ["count_distinct", "Count distinct"],
    ...(numeric ? [["sum", "Sum"], ["avg", "Average"]] : []), ...(comparable ? [["min", "Minimum"], ["max", "Maximum"]] : [])];
}

/** Preview-only editing. Never mutates exposedFields or other model rules. */
export function renderPreviewFields(host, { draft, catalog, onChange }) {
  disposeSelects(host); host.replaceChildren();
  const label = f => `${draft.nodes.find(n => n.id === f.table)?.label || f.table} · ${f.column}`;
  const available = exposedFields(draft, catalog);
  let add = additions.get(host);
  if (!add || add.draft !== draft) { add = { draft, field: "", aggregate: "none" }; additions.set(host, add); }
  if (!available.some(f => fieldKey(f) === add.field)) add.field = "";
  const refresh = () => renderPreviewFields(host, { draft, catalog, onChange });
  const chosen = available.find(f => fieldKey(f) === add.field);
  const next = chosen && { table: chosen.table, column: chosen.column, aggregate: add.aggregate };
  const duplicate = next && draft.fields.some(f => sameOutput(f, next));
  const chooser = element("div", { className: "preview-add" }, [
    modelSelect("Preview source field", available.map(f => [fieldKey(f), label(f)]), add.field, value => { add.field = value; add.aggregate = "none"; refresh(); }),
    modelSelect("Preview aggregation", aggregates(chosen, draft, catalog), add.aggregate, value => { add.aggregate = value; refresh(); }),
    action("add", "Add preview output", () => { if (next && !duplicate && draft.fields.length < 64) { draft.fields.push(next); onChange(); } }, !chosen || duplicate || draft.fields.length >= 64),
  ]);
  host.append(chooser, element("p", { className: "hint", text: !available.length ? "No exposed fields. Enable columns on the canvas or in the table inspector first." : duplicate ? "That output is already in the preview. Choose another aggregation to compare the same source field." : "Add exposed fields or measures. Plain fields become GROUP BY keys when measures are selected. Preview is limited to 64 outputs." }));
  const list = element("div", { className: "preview-outputs", attrs: { "aria-label": "Ordered preview outputs" } });
  draft.fields.forEach((field, index) => {
    const options = aggregates(field, draft, catalog).filter(([aggregate]) => !draft.fields.some((other, i) => i !== index && sameOutput(other, { ...field, aggregate })));
    const row = element("div", { className: "preview-output", dataset: { outputIndex: index } }, [
      element("span", { className: "badge", text: String(index + 1) }),
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
  if (!draft.fields.length) host.append(element("p", { className: "hint", text: "Add a preview output above. Model exposure stays unchanged." }));
}
