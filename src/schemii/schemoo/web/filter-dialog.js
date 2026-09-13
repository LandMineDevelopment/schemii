import { element } from "#common/dom.js";
import { createIconButton } from "/assets/common/ui.js";
import { renderFilterDefinition } from "./filter-controls.js";
import { disposeSelects } from "./select.js";
import { helpButton, helpHeading } from "./help.js";
import { modelFilterIssue } from "./model-filter-links.js";
export { modelFilterIssue } from "./model-filter-links.js";

const uid = () => crypto.randomUUID();
const note = text => element("p", { className: "mf-help", text });
function button(text, callback, primary = false) {
  const control = element("button", { type: "button", className: `ui-button${primary ? " primary" : ""}`, text });
  control.onclick = callback;
  return control;
}
function icon(name, label, callback) {
  const control = createIconButton({ icon: name, label, className: "ui-button" });
  control.onclick = callback;
  return control;
}
const comparisons = { eq: "equals", ne: "does not equal", in: "is one of", not_in: "is not one of", gt: ">", gte: "≥", lt: "<", lte: "≤", contains: "contains", range_contains_date: "contains date", is_null: "is null", not_null: "is not null" };
function conditionText(condition, alternative, draft) {
  const source = draft.nodes.find(node => node.id === condition.table);
  const parameter = alternative.inputs.find(input => input.id === condition.parameterId);
  const unary = ["is_null", "not_null"].includes(condition.operator);
  const value = unary ? "" : parameter ? ` [${parameter.label}]` : ` ${JSON.stringify(condition.value ?? "")}`;
  return `${source?.label || "Choose source"}.${condition.column || "column"} ${comparisons[condition.operator] || condition.operator}${value}${!unary && condition.allowNull ? " (or is null)" : ""}`;
}
function summary(scope, draft) {
  const box = element("div", { className: "mf-rule-summary" });
  box.append(note(scope.kind === "required" ? "Required · every query" : "Conditional · only when its sources participate"));
  box.append(note((scope.rowBehavior || (scope.kind === "required" ? "require_matching" : "keep_unmatched")) === "keep_unmatched" ? "Keep unmatched parent rows" : "Require matching rows"));
  for (const [index, option] of scope.alternatives.entries()) {
    if (scope.alternatives.length > 1) box.append(element("strong", { text: `${index ? "OR · " : ""}${option.label}` }));
    if (!option.conditions.length) box.append(element("p", { className: "warning", text: "No conditions: this option allows unrestricted results." }));
    for (const [i, condition] of option.conditions.entries()) box.append(element("p", { text: `${i ? "AND · " : ""}${conditionText(condition, option, draft)}` }));
    box.append(note(option.inputs.length ? `Report inputs: ${option.inputs.map(p => `${p.label}${p.defaultValue !== "" && p.defaultValue != null ? ` (default: ${p.defaultValue})` : ""}`).join(", ")}` : "Fixed rule · no report input required"));
  }
  return box;
}
/** Edits one isolated filter. Cancel/Escape never mutate the model draft. */
export function openModelFilter(options, existing = null, returnTarget = null) {
  const { draft, catalog, onChange } = options;
  const scope = structuredClone(existing || { id: uid(), label: "Model filter", kind: "required", alternatives: [{ id: uid(), label: "Default", inputs: [], conditions: [] }] });
  const local = { ...draft, scopes: [scope], selections: structuredClone(draft.selections || {}) };
  const dialog = element("dialog", { className: "mf-dialog", attrs: { "aria-labelledby": "mf-dialog-title" } });
  const content = element("div", { className: "mf-dialog-body" });
  const editor = element("div", { className: "mf-dialog-editor" });
  const overview = element("aside", { className: "mf-dialog-summary", attrs: { "aria-label": "Filter summary" } });
  const error = element("p", { className: "mf-dialog-error", attrs: { role: "alert" } });
  const apply = button("Apply to model", () => {
    const message = modelFilterIssue(scope, local, catalog);
    error.textContent = message;
    if (message) return;
    if (existing) draft.scopes[draft.scopes.indexOf(existing)] = scope;
    else (draft.scopes ||= []).push(scope);
    if (local.selections?.[scope.id]) {
      draft.selections ||= {};
      draft.selections[scope.id] = local.selections[scope.id];
    }
    const selection = draft.selections?.[scope.id];
    if (selection && !scope.alternatives.some(a => a.id === selection.alternativeId)) delete draft.selections[scope.id];
    onChange?.({ structure: true });
    options.redraw?.();
    dialog.close();
  }, true);
  const update = () => {
    error.textContent = "";
    overview.replaceChildren(helpHeading("What this enforces", "scopes"), summary(scope, local));
    options.onSelect?.(scope);
  };
  const render = () => {
    content.replaceChildren(editor, overview);
    renderFilterDefinition(editor, { draft: local, catalog, onChange: update, onLoadDomain: options.onLoadDomain });
    update(); apply.hidden = false;
  };
  dialog.append(element("header", { className: "mf-dialog-header" }, [
    element("div", {}, [element("small", { text: "MODEL RULES" }), element("h2", { text: existing ? "Edit model filter" : "Add model filter", attrs: { id: "mf-dialog-title" } }), note("Configure a fixed rule or a report parameter and its source bindings.")]),
    icon("close", "Cancel filter setup", () => dialog.close()),
  ]), content, element("footer", { className: "mf-dialog-footer" }, [
    element("div", {}, [error, note("Applies to your draft. Use Save model to keep changes between sessions.")]),
    button("Cancel", () => dialog.close()), apply,
  ]));
  dialog.addEventListener("close", () => {
    disposeSelects(editor); dialog.remove();
    options.onSelect?.(null);
    if (returnTarget?.isConnected && returnTarget.getClientRects().length) returnTarget.focus({ preventScroll: true });
    else document.querySelector('#model-filters button')?.focus({ preventScroll: true });
  }, { once: true });
  if (existing) render();
  else {
    apply.hidden = true;
    const choose = parameterized => {
      const option = scope.alternatives[0];
      if (parameterized) option.inputs.push({ id: uid(), label: "Parameter", type: "text", defaultValue: "" });
      option.conditions.push({ table: options.sourceId || "", column: "", operator: parameterized ? "eq" : "not_null", ...(parameterized ? { parameterId: option.inputs[0].id } : {}) });
      render();
      editor.querySelector("input")?.focus();
    };
    const fixed = button("Fixed rule", () => choose(false));
    fixed.append(note("Always enforce a condition, such as column is not null or status equals Active. No report input."));
    const parameter = button("Report parameter", () => choose(true));
    parameter.append(note("Ask for a date, ID, or other value. Bind it to one or more source columns; defaults are optional."));
    content.append(element("div", { className: "mf-rule-choices" }, [element("h3", { text: "Where does the filter value come from?" }), fixed, parameter, note("Both can be required or conditional. You can combine fixed conditions and report inputs in the editor.")]));
  }
  document.body.append(dialog); dialog.showModal();
  options.onSelect?.(scope);
  return dialog;
}

export function renderModelFilters(host, options) {
  const redraw = () => renderModelFilters(host, options);
  host.replaceChildren(helpHeading("Model filters", "scopes"), note("Required rules, fixed conditions, and report parameters. Open a filter to edit its inputs and source bindings."));
  if (!options.draft.scopes?.length) host.append(note("No model filters yet. Add a fixed rule or a report parameter to control which records contribute to queries."));
  for (const scope of options.draft.scopes || []) {
    const title = button(scope.label, () => openModelFilter({ ...options, redraw }, scope, title));
    title.classList.add("mf-filter-title");
    title.setAttribute("aria-label", `Open filter ${scope.label}`);
    const edit = icon("edit", `Edit filter ${scope.label}`, () => openModelFilter({ ...options, redraw }, scope, edit));
    const remove = icon("delete", `Delete scope ${scope.label}`, () => {
      if (!confirm(`Remove “${scope.label}”? This removes its restrictions from the model draft, not data from the database.`)) return;
      options.draft.scopes = options.draft.scopes.filter(s => s !== scope);
      delete options.draft.selections?.[scope.id];
      options.onSelect?.(null);
      options.onChange?.({ structure: true }); redraw();
    });
    const card = element("section", { className: "mf-filter-card", attrs: { "data-filter-id": scope.id } }, [element("header", {}, [title, edit, remove]), summary(scope, options.draft)]);
    const issue = modelFilterIssue(scope, options.draft, options.catalog);
    if (issue) card.append(element("p", { className: "warning mf-filter-issue", text: issue, attrs: {role: "status"} }));
    host.append(card);
  }
  const add = button("Add model filter", () => openModelFilter({ ...options, redraw }, null, add), true);
  add.setAttribute("aria-label", "Add model filter scope");
  host.append(add);
}
