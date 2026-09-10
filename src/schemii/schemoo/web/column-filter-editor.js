import { element } from "#common/dom.js";
import { createIconButton } from "/assets/common/ui.js";
import { conditionsEditor } from "./filter-controls.js";
import { helpButton } from "./help.js";
import { modelFilterIssue } from "./model-filter-links.js";
import { modelSelect, disposeSelects } from "./select.js";

const note = text => element("p", { className: "mf-help", text });
function button(text, callback, primary = false) {
  const control = element("button", { className: `ui-button${primary ? " primary" : ""}`, attrs: { type: "button" }, text });
  control.onclick = callback;
  return control;
}
function labeled(label, control) {
  const input = control.querySelector("input") || control;
  input.id ||= `column-binding-${crypto.randomUUID()}`;
  return element("div", { className: "mf-label" }, [element("label", { text: label, attrs: { for: input.id } }), control]);
}
function conditionKey(condition) {
  const unary = ["is_null", "not_null"].includes(condition.operator);
  const value = Array.isArray(condition.value) ? [...condition.value].map(v => JSON.stringify(v)).sort() : condition.value;
  return JSON.stringify([condition.table, condition.column, condition.operator,
    unary ? null : condition.parameterId || null,
    unary || condition.parameterId ? null : value,
    unary ? false : !!condition.allowNull]);
}

/** Attach a column condition to an existing rule without leaving the table inspector.
 * The complete scope is isolated because changing IN also adjusts parameter defaults.
 */
export function createColumnFilterEditor({ draft, catalog, nodeId, column, binding, onChange, onClose, onOpenFilters, onLoadDomain }) {
  const columnName = typeof column === "string" ? column : column.name;
  const editing = !!binding;
  const host = element("section", { className: "column-filter-editor", attrs: { "aria-label": `${editing ? "Edit" : "Add"} filter binding for ${columnName}` } });
  let scope, alternative, condition, original;
  const close = () => { disposeSelects(host); onClose?.(); };
  const error = element("p", { className: "mf-dialog-error", attrs: { role: "alert" } });
  function choose(scopeId, alternativeId) {
    const existing = draft.scopes?.find(item => item.id === scopeId);
    original = existing ? JSON.stringify(existing) : null;
    scope = existing ? structuredClone(existing) : null;
    alternative = scope?.alternatives.find(item => item.id === alternativeId) || scope?.alternatives[0];
    if (alternative) {
      const input = alternative.inputs?.[0];
      condition = { table: nodeId, column: columnName, operator: input ? "eq" : "not_null", ...(input ? { parameterId: input.id } : {}) };
      (alternative.conditions ||= []).push(condition);
    }
    render();
  }
  function apply() {
    const index = draft.scopes?.findIndex(item => item.id === scope?.id) ?? -1;
    if (index < 0 || JSON.stringify(draft.scopes[index]) !== original) {
      error.textContent = "This filter changed while you were editing. Cancel and reopen the column binding to use its latest definition.";
      return;
    }
    if (alternative.conditions.some(item => item !== condition && conditionKey(item) === conditionKey(condition))) {
      error.textContent = "This column condition is already attached to that filter option.";
      return;
    }
    const issue = modelFilterIssue(scope, { ...draft, scopes: [scope] }, catalog);
    if (issue) { error.textContent = issue; return; }
    draft.scopes[index] = scope;
    disposeSelects(host);
    onChange?.({ structure: true });
    onClose?.();
  }
  function removeBinding() {
    const index = draft.scopes?.findIndex(item => item.id === binding.scopeId) ?? -1;
    if (index < 0 || JSON.stringify(draft.scopes[index]) !== original) {
      error.textContent = "This filter changed while you were editing. Cancel and reopen the binding before deleting it.";
      return;
    }
    // Delete from the saved draft snapshot, not the unapplied editor changes.
    const updated = JSON.parse(original);
    const option = updated.alternatives.find(item => item.id === binding.alternativeId);
    const [removed] = option.conditions.splice(binding.conditionIndex, 1);
    const orphan = removed.parameterId && !option.conditions.some(item => item.parameterId === removed.parameterId)
      ? option.inputs?.find(item => item.id === removed.parameterId) : null;
    const message = `Remove this binding from ${columnName}? Other column bindings stay unchanged.`
      + (orphan ? ` The now-unused input “${orphan.label}” and its selected value will also be removed from this option.` : "")
      + (!option.conditions.length ? " This option will no longer restrict results." : "")
      + " No database data is deleted. Save model to keep the change.";
    if (!confirm(message)) return;
    if (orphan) {
      option.inputs = option.inputs.filter(item => item.id !== orphan.id);
      const selection = draft.selections?.[updated.id];
      if (!selection?.alternativeId || selection.alternativeId === option.id) delete selection?.values?.[orphan.id];
    }
    draft.scopes[index] = updated;
    disposeSelects(host);
    onChange?.({ structure: true });
    onClose?.();
  }
  function render() {
    disposeSelects(host);
    host.replaceChildren(element("div", { className: "column-filter-editor__heading" }, [
      element("strong", { text: editing ? "Edit binding" : "Add binding" }), helpButton("columnBinding"),
    ]));
    error.textContent = "";
    const choices = (draft.scopes || []).map(item => [item.id, item.label]);
    if (!choices.length) {
      host.append(note("Create a model filter first, then bind this column to one of its conditions here."),
        button("Open Model filters", () => { close(); onOpenFilters?.(); }), button("Cancel", close));
      return;
    }
    if (!editing) host.append(labeled("Model filter", modelSelect("Model filter for column", choices, scope?.id || "", value => choose(value))));
    if (!alternative) {
      host.append(note("This filter has no options. Add an option in Model filters before binding a column."), button("Open Model filters", () => { close(); onOpenFilters?.(); }), button("Cancel", close));
      return;
    }
    if (!editing && scope.alternatives.length > 1) host.append(labeled("Filter option", modelSelect("Filter option for column", scope.alternatives.map(item => [item.id, item.label]), alternative.id, value => choose(scope.id, value))));
    host.append(note(`${editing ? `${scope.label} · ` : ""}${alternative.label} · ${scope.kind === "required" ? "Required" : "Conditional"} · AND`),
      conditionsEditor([condition], { draft, catalog, inputs: alternative.inputs || [], prefix: "Column binding", fixedField: { table: nodeId, column: columnName }, singleCondition: true, compact: true,
        onChange: () => { error.textContent = ""; }, refresh: render, onLoadDomain }), error,
      element("div", { className: "column-filter-editor__actions" }, [
        ...(editing ? [deleteButton()] : []), button("Cancel", close), button(editing ? "Apply edit" : "Apply binding", apply, true)]));
  }
  function deleteButton() {
    const control = createIconButton({ icon: "delete", label: "Delete column binding", className: "ui-button icon-only column-binding-delete" });
    control.onclick = removeBinding;
    return control;
  }
  if (editing) {
    const existing = draft.scopes?.find(item => item.id === binding.scopeId);
    original = existing ? JSON.stringify(existing) : null;
    scope = existing ? structuredClone(existing) : null;
    alternative = scope?.alternatives.find(item => item.id === binding.alternativeId);
    condition = alternative?.conditions?.[binding.conditionIndex];
    render();
  } else {
    const related = draft.scopes?.find(item => item.alternatives?.some(option => option.conditions?.some(binding => binding.table === nodeId)));
    choose(related?.id || draft.scopes?.[0]?.id);
  }
  return host;
}
