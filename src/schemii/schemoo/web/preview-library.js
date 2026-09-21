import { element } from "#common/dom.js";
import { requestJson } from "#common/http.js";
import { createIconButton } from "#common/ui.js";
import { confirmAction } from "#common/confirmation.js";
import { namedAction } from "#common/named-action.js";
import { modelSelect, disposeSelects } from "./select.js";
import { previewState, reconcilePreview, samePreview } from "./preview-state.js";

function icon(name, label, callback, disabled = false) {
  const button = createIconButton({ icon: name, label, className: "ui-button" });
  button.onclick = callback; button.disabled = disabled; return button;
}

function nameDialog({ title, initial, onSave }) {
  return namedAction({ title, initial, onSave, inputLabel: "Preview name", submitLabel: "Save preview",
    description: "Saves query choices only. Model rules and PostgreSQL data are unchanged; results are never stored." });
}

/** Named query setups are independent records; the canvas/model remains shared. */
export function createPreviewLibrary({ host, getContext, applyExplore, isBlocked, onPendingChange = () => {} }) {
  let modelId = null, records = [], activeId = "", baseline = null, working = null;
  let pending = false, loaded = false, ticket = 0, notice = "", error = "";
  function setPending(value) {
    if (pending === value) return;
    pending = value;
    onPendingChange(value);
  }
  const path = () => `/api/v1/schemoo/models/${encodeURIComponent(modelId)}/previews`;
  const current = () => getContext().draft ? previewState(getContext().draft) : null;
  const active = () => records.find(preview => preview.id === activeId);
  const modified = () => baseline != null && current() != null && !samePreview(current(), baseline);
  const blocked = () => pending || isBlocked() || !loaded;
  function checkSave() {
    if (blocked()) throw new Error("Wait for the current operation to finish.");
    if (getContext().definitionDirty) throw new Error("Save model changes before saving a named preview, so its fields and filters exist in the saved model.");
  }
  async function choose(id) {
    if (blocked() || id === activeId) { render(); return; }
    if (activeId && modified() && !await confirmAction({ title: "Switch preview?", message: "Discard the unsaved changes to this preview?",
      details: "Model rules and canvas layout edits will be kept.", confirmLabel: "Switch preview", onConfirm: async () => {} })) { render(); return; }
    if (!activeId) working = current();
    const selected = id ? records.find(preview => preview.id === id)?.explore : working;
    if (!selected) { render(); return; }
    const reconciled = reconcilePreview(selected, getContext().draft, getContext().catalog);
    activeId = id; baseline = structuredClone(selected); error = "";
    notice = reconciled.changed ? `${reconciled.removedOutputs} unavailable output${reconciled.removedOutputs === 1 ? "" : "s"} removed. Review and save this updated preview. Filters are kept for repair.` : "";
    applyExplore(reconciled.explore); render();
  }
  async function saveAs() {
    try { checkSave(); } catch (failure) { error = failure.message; render(); return; }
    const origin = modelId, explore = current();
    const names = new Set(records.map(p => p.name.toLocaleLowerCase()));
    let initial = active() ? `${active().name} copy` : "Preview";
    for (let n = 2; names.has(initial.toLocaleLowerCase()); n++) initial = `Preview ${n}`;
    await nameDialog({ title: "Save preview as", initial, onSave: async name => {
      checkSave(); if (modelId !== origin) throw new Error("The selected model changed. Close this dialog and try again.");
      setPending(true); render();
      try {
        const saved = await requestJson(path(), { method: "POST", body: { name, explore } });
        records.push(saved); activeId = saved.id; baseline = structuredClone(saved.explore); error = ""; notice = "Preview saved";
      } finally { setPending(false); render(); }
    } });
  }
  async function saveSelected() {
    try {
      checkSave(); const selected = active(); if (!selected) return;
      setPending(true); render();
      const saved = await requestJson(`${path()}/${selected.id}`, { method: "PUT", body: { expectedRevision: selected.revision, name: selected.name, explore: current() } });
      records = records.map(record => record.id === saved.id ? saved : record); baseline = structuredClone(saved.explore); notice = "Preview saved"; error = "";
    } catch (failure) { error = `${failure.message} Your preview edits are kept. Refresh the saved preview list to review newer changes before retrying.`; }
    finally { setPending(false); render(); }
  }
  async function removeSelected() {
    if (blocked() || !active()) return;
    const selected = active(), origin = modelId;
    const deleted = await confirmAction({ title: "Delete saved preview?", message: `Delete “${selected.name}”?`,
      details: "Only this saved query setup is removed. The model, other previews, and PostgreSQL data remain unchanged. Your current choices stay available as a working preview.",
      confirmLabel: "Delete preview", busyLabel: "Deleting…", onConfirm: async () => {
        if (modelId !== origin || blocked()) throw new Error("The model changed or is busy. Close this dialog and try again.");
        setPending(true); render();
        try { await requestJson(`${path()}/${selected.id}?expected_revision=${selected.revision}`, { method: "DELETE" }); }
        finally { setPending(false); render(); }
      } });
    if (deleted) { records = records.filter(record => record.id !== selected.id); activeId = ""; working = current(); baseline = current(); notice = "Saved preview deleted; current choices kept"; error = ""; render(); }
  }
  async function load(id, reset = true) {
    const version = ++ticket;
    modelId = id; setPending(true); loaded = false; error = "";
    if (reset) { records = []; activeId = ""; baseline = current(); working = current(); notice = ""; }
    render();
    if (!id) { setPending(false); render(); return; }
    try {
      const response = await requestJson(path());
      if (version !== ticket) return;
      records = response.previews; loaded = true;
      // Never silently adopt a new revision as permission to overwrite it.
      if (!reset) {
        activeId = ""; working = current(); baseline = current();
        notice = "List refreshed. Current choices kept as a working preview; open a saved preview to review its latest version.";
      }
    } catch (failure) { if (version === ticket) error = failure.message; }
    finally { if (version === ticket) { setPending(false); render(); } }
  }
  function render() {
    disposeSelects(host); host.replaceChildren();
    host.append(element("h3", { text: "Saved previews" }));
    const controls = element("div", { className: "saved-preview-controls" }, [
      modelSelect("Saved preview", [["", "Working preview"], ...records.map(p => [p.id, p.name])], activeId, id => void choose(id)),
      element("div", { className: "actions" }, [
        icon("save", "Save changes to selected preview", () => void saveSelected(), blocked() || !active() || !modified()),
        icon("add", "Save preview as", () => void saveAs(), blocked()),
        icon("delete", "Delete selected preview", () => void removeSelected(), blocked() || !active()),
        icon("refresh", "Refresh saved preview list", () => void load(modelId, false), pending || isBlocked()),
      ]),
    ]);
    controls.querySelector(".ui-searchable-select").inert = blocked();
    const status = notice === "Preview saved" && modified() ? "" : notice;
    host.append(controls, element("p", { className: error ? "warning" : "hint", text: error || (pending ? "Loading or saving previews…" : status || (modified() ? "Modified preview · save changes or save as a new preview" : "Query choices only · uses current model rules")), attrs: { role: error ? "alert" : "status" } }));
  }
  return { load, render, isPending: () => pending };
}
