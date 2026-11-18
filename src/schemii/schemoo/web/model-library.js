import { requestJson } from "#common/http.js";
import { element } from "#common/dom.js";
import { createIconButton } from "#common/ui.js";
import { confirmModelDeletion } from "./model-deletion.js";
export { confirmModelDeletion } from "./model-deletion.js";
import { namedAction } from "#common/named-action.js";
import { modelSelect, disposeSelects } from "./select.js";
import { importedDraft, defaultSourceSelection } from "./model-draft.js";
import { splitDraft } from "./model-state.js";

const API = "/api/v1/schemoo";

export function sourceSelectionControl(catalog, { initial = defaultSourceSelection(catalog), onChange = () => {} } = {}) {
  const selected = new Map(initial.map(item => [item.table, new Set(item.columns)]));
  const getSelection = () => [...selected].map(([table, columns]) => ({ table, columns: [...columns] }));
  const host = element("section", { className: "source-selection", attrs: { "aria-label": "Model sources and fields" } });
  const summary = element("p", { className: "hint", attrs: { role: "status" } });
  const search = element("input", { type: "search", attrs: { "aria-label": "Search sources", placeholder: "Search source names" } });
  const list = element("div", { className: "source-selection-list" });
  const error = element("p", { className: "warning", attrs: { role: "alert" } });
  const notify = () => {
    const selection = getSelection(), count = selection.reduce((total, item) => total + item.columns.length, 0);
    summary.textContent = `${selection.length} of ${catalog.tables.length} sources · ${count.toLocaleString()} fields selected. Limit: 100 sources, 200 connections, 1,000 exposed fields.`;
    let failure = ""; try { importedDraft(catalog, selection); } catch (issue) { failure = issue.message; }
    error.textContent = failure; error.hidden = !failure;
    onChange({ valid: !failure, selection });
  };
  const render = () => {
    list.replaceChildren();
    const query = search.value.trim().toLocaleLowerCase(), tables = catalog.tables.filter(table => table.name.toLocaleLowerCase().includes(query));
    for (const table of tables.slice(0, 100)) {
      const checkbox = element("input", { type: "checkbox", attrs: { "aria-label": `Include source ${table.name}` } });
      checkbox.checked = selected.has(table.name); checkbox.disabled = !table.columns.length;
      const row = element("div", { className: "source-selection-row" });
      const label = element("label", { className: "source-selection-source" }, [checkbox, element("strong", { text: table.name }), element("small", { text: `${table.columns.length.toLocaleString()} fields` })]);
      const details = element("details", { hidden: !checkbox.checked });
      const fields = element("div", { className: "source-selection-fields" });
      const fieldSearch = element("input", { type: "search", attrs: { "aria-label": `Search fields in ${table.name}`, placeholder: "Search fields" } });
      let populated = false;
      const renderFields = () => {
        fields.replaceChildren(); populated = true;
        const query = fieldSearch.value.trim().toLocaleLowerCase();
        for (const column of table.columns.filter(column => column.name.toLocaleLowerCase().includes(query)).slice(0, 100)) {
          const choice = element("input", { type: "checkbox", attrs: { "aria-label": `Expose ${table.name}.${column.name}` } });
          choice.checked = selected.get(table.name)?.has(column.name) || false;
          choice.onchange = () => { if (choice.checked) selected.get(table.name).add(column.name); else selected.get(table.name).delete(column.name); notify(); };
          fields.append(element("label", {}, [choice, element("span", { text: column.name }), element("small", { text: column.dataType || "" })]));
        }
        if (table.columns.filter(column => column.name.toLocaleLowerCase().includes(query)).length > 100) fields.append(element("p", { className: "hint", text: "Showing the first 100 matches. Search to find more fields." }));
      };
      fieldSearch.oninput = renderFields;
      const all = element("button", { type: "button", className: "ui-button", text: "All fields", attrs: { "aria-label": `Select all fields in ${table.name}` } });
      all.onclick = () => { selected.set(table.name, new Set(table.columns.map(column => column.name))); renderFields(); notify(); };
      const none = element("button", { type: "button", className: "ui-button", text: "Clear fields", attrs: { "aria-label": `Clear fields in ${table.name}` } });
      none.onclick = () => { selected.set(table.name, new Set()); renderFields(); notify(); };
      details.append(element("summary", { text: "Choose exposed fields" }), fieldSearch, element("div", { className: "source-selection-actions" }, [all, none]), fields);
      details.ontoggle = () => { if (details.open && !populated) renderFields(); };
      checkbox.onchange = () => {
        if (checkbox.checked) {
          const first = table.columns.find(column => column.name === "name") || table.columns.find(column => column.name === "id") || table.columns[0];
          selected.set(table.name, new Set([first.name]));
        } else selected.delete(table.name);
        details.hidden = !checkbox.checked; if (checkbox.checked) { details.open = true; renderFields(); } notify();
      };
      row.append(label, details); list.append(row);
    }
    if (!tables.length) list.append(element("p", { className: "hint", text: "No matching accessible sources." }));
    else if (tables.length > 100) list.append(element("p", { className: "hint", text: "Showing the first 100 matches. Search to find other sources." }));
  };
  search.oninput = render;
  const all = element("button", { type: "button", className: "ui-button", text: "Select all sources and fields" });
  all.disabled = !defaultSourceSelection(catalog).length;
  all.onclick = () => { selected.clear(); for (const item of defaultSourceSelection(catalog)) selected.set(item.table, new Set(item.columns)); render(); notify(); };
  const clear = element("button", { type: "button", className: "ui-button", text: "Clear selection" });
  clear.onclick = () => { selected.clear(); render(); notify(); };
  host.append(element("h4", { text: "Choose sources and exposed fields" }), element("p", { className: "hint", text: "Start with the sources you need. Only checked fields become report outputs; keys and joins are retained automatically. More sources and fields can be added later." }), search,
    element("div", { className: "source-selection-actions" }, [all, clear]), summary, list, error);
  render(); notify();
  return { element: host, getSelection };
}

export function openSourceSelection(catalog, onSelect) {
  return new Promise(resolve => {
    const previous = document.activeElement;
    const dialog = element("dialog", { className: "ui-dialog source-selection-dialog", attrs: { "aria-label": "Add model sources and fields" } });
    const form = element("form"), submit = element("button", { type: "submit", className: "ui-button primary", text: "Add selection" });
    const error = element("p", { className: "warning", attrs: { role: "alert" } });
    const selection = sourceSelectionControl(catalog, { initial: [], onChange: ({ valid }) => { submit.disabled = !valid; } });
    const cancel = element("button", { type: "button", className: "ui-button", text: "Cancel" }); cancel.onclick = () => dialog.close();
    form.onsubmit = event => { event.preventDefault(); if (submit.disabled) return; try { onSelect(selection.getSelection()); dialog.close(); } catch (failure) { error.textContent = failure.message; } };
    dialog.addEventListener("close", () => { dialog.remove(); if (previous?.isConnected) previous.focus(); resolve(); }, { once: true });
    form.append(element("h2", { text: "Add model sources and fields" }), selection.element, error, element("div", { className: "ui-dialog__actions" }, [cancel, submit]));
    dialog.append(form); document.body.append(dialog); dialog.showModal(); selection.element.querySelector("input").focus();
  });
}

let libraryTicket = 0;
let refreshActions = [];
export function refreshModelLibraryActions() {
  if (!document.getElementById("model-library")?.open) return;
  for (const refresh of refreshActions) refresh();
}
export async function openModelLibrary(onOpen, { onDeleted = () => {}, canDelete = () => true, onAssistant = null } = {}) {
  const ticket = ++libraryTicket;
  refreshActions = [];
  const dialog = document.getElementById("model-library"), content = document.getElementById("library-content");
  const current = () => ticket === libraryTicket && dialog.open;
  disposeSelects(content); content.replaceChildren(element("p", { text: "Loading your models and connections…", attrs: { role: "status" } }));
  if (!dialog.open) dialog.showModal();
  try {
    const [{ models }, { connections }] = await Promise.all([requestJson(`${API}/models`), requestJson("/api/v1/connections?product=schemoo")]);
    if (ticket !== libraryTicket || !dialog.open) return;
    content.replaceChildren();
    const status = element("p", { className: "hint", attrs: { role: "status" } });
    const choose = async model => {
      if (!current()) return false;
      try {
        const opened = await onOpen(model.id);
        if (opened !== false && current()) { dialog.close(); return true; }
      } catch (error) { if (current()) status.textContent = error.message; }
      return false;
    };
    if (!models.length && onAssistant) {
      const assistant = element("button", { type: "button", className: "ui-button primary", text: "Create with model assistant" });
      assistant.onclick = () => { dialog.close(); onAssistant(); };
      content.append(element("p", { className: "hint", text: "Describe the model you want and let the assistant create it." }), assistant);
    }
    if (models.length) {
      const list = element("div", { className: "model-list" });
      for (const model of models) {
        const button = element("button", { type: "button", className: "model-option" }, [element("strong", { text: model.name }), element("small", { text: `${model.database}.${model.namespace} · revision ${model.revision}` })]);
        const remove = createIconButton({ icon: "delete", label: `Delete model ${model.name}`, className: "model-option-delete" });
        remove.title = `Delete model ${model.name}`;
        remove.disabled = !canDelete(model);
        const duplicate = createIconButton({ icon: "copy", label: `Duplicate model ${model.name}`, className: "model-option-copy" });
        duplicate.disabled = !canDelete(model);
        refreshActions.push(() => {
          remove.disabled = !canDelete(model);
          duplicate.disabled = !canDelete(model);
        });
        const row = element("div", { className: "model-list-row" }, [button, duplicate, remove]);
        let copying = false;
        duplicate.onclick = async () => {
          if (copying || !canDelete(model)) return;
          // Keep the invoking button focusable so closing the naming dialog
          // restores keyboard focus. The modal and guard prevent re-entry.
          copying = true;
          let copied = null;
          try {
            const names = new Set(models.map(item => item.name.toLocaleLowerCase()));
            let initial = `${model.name.slice(0, 123)} copy`;
            for (let n = 2; names.has(initial.toLocaleLowerCase()); n++) {
              const suffix = ` copy ${n}`; initial = model.name.slice(0, 128 - suffix.length) + suffix;
            }
            await namedAction({ title: "Duplicate model", initial, inputLabel: "New model name", submitLabel: "Create copy", busyLabel: "Copying…", allowRetry: false,
              description: "Creates an independent copy of the saved model, aliases, filters, calculations, layout, working preview, and saved previews. Save pending edits first. The copy uses the same PostgreSQL connection; database data, credentials, results, and chat history are not copied.",
              onSave: async name => {
                if (!canDelete(model)) throw new Error("Wait for the current operation to finish, then reopen this dialog.");
                try {
                  copied = await requestJson(`${API}/models/${encodeURIComponent(model.id)}/duplicate`, { method: "POST", body: {
                    name, expectedRevision: model.revision, expectedLayoutRevision: model.layoutRevision, expectedExploreRevision: model.exploreRevision,
                  }, timeoutMs: 30000 });
                } catch (error) {
                  error.message += " Close this dialog and refresh the model list before trying again, to check whether a copy was created or the source changed.";
                  throw error;
                }
              },
            });
            if (copied && current()) {
              if (!await choose(copied) && current()) await openModelLibrary(onOpen, { onDeleted, canDelete, onAssistant });
            }
          } catch (error) {
            const target = content.querySelector('[role="status"]');
            if (target) target.textContent = error.message;
          } finally { copying = false; duplicate.disabled = !canDelete(model); }
        };
        let deleting = false;
        button.onclick = () => choose(model);
        remove.onclick = async () => {
          if (!canDelete(model) || remove.disabled || deleting) return;
          deleting = true;
          try {
            if (!await confirmModelDeletion(model)) return;
            row.remove(); status.textContent = `Deleted “${model.name}”. PostgreSQL data was not changed.`;
            if (!list.children.length) list.append(element("p", { className: "hint", text: "No saved models remain. Create a model below to get started." }));
            await onDeleted(model);
            if (dialog.open) (list.querySelector("button") || refresh).focus();
          } catch (error) { status.textContent = error.message; }
          finally { deleting = false; remove.disabled = !canDelete(model); }
        };
        list.append(row);
      }
      const refresh = createIconButton({ icon: "refresh", label: "Refresh models" });
      refresh.title = "Refresh models";
      refresh.onclick = () => openModelLibrary(onOpen, { onDeleted, canDelete });
      content.append(element("div", { className: "model-list-heading" }, [element("h3", { text: "Your saved models" }), refresh]), list);
    } else content.append(element("p", { className: "hint", text: "No saved models yet. Create one from an existing PostgreSQL connection. This does not change the database." }));
    if (!connections.length) { content.append(element("p", { text: "Add a PostgreSQL connection in Schemii first." }), element("a", { text: "Open Schemii", attrs: { href: "/" } }), status); return; }
    const form = element("form", { className: "model-create-form" });
    const name = element("input", { attrs: { "aria-label": "Model name", maxlength: "128", required: "", placeholder: "Organization model" } });
    let connectionId = "", namespace = "", namespaceTicket = 0, catalog = null, selection = null, creating = false, creationUncertain = false;
    const namespaceHost = element("div"), sourceHost = element("div");
    const create = element("button", { type: "submit", className: "ui-button primary", text: "Create model" }); create.disabled = true;
    const reload = element("button", { type: "button", className: "ui-button", text: "Reload sources" }); reload.hidden = true;
    const loadSources = async () => {
      const ticket = ++namespaceTicket, initial = selection?.getSelection();
      catalog = null; selection = null; sourceHost.replaceChildren(); create.disabled = true; reload.disabled = true;
      if (!connectionId || !namespace) return;
      status.textContent = "Loading accessible sources…";
      try {
        const next = await requestJson(`${API}/catalog?connection_id=${encodeURIComponent(connectionId)}&namespace=${encodeURIComponent(namespace)}`, { timeoutMs: 30000 });
        if (ticket !== namespaceTicket || !dialog.open || libraryTicket !== libraryTicketAtOpen) return;
        if (!next.tables.some(table => table.columns?.length)) throw new Error("This schema has no accessible sources with readable fields. Choose another schema or check its read permissions.");
        catalog = next;
        selection = sourceSelectionControl(catalog, { ...(initial ? { initial } : {}), onChange: ({ valid }) => { create.disabled = !valid || creationUncertain; } });
        sourceHost.append(selection.element);
        status.textContent = defaultSourceSelection(catalog).length ? "Review sources and fields before creating the model." : "This schema exceeds one model's limits. Choose a smaller set of sources and fields.";
      } catch (error) { if (ticket === namespaceTicket && dialog.open) status.textContent = error.message; }
      finally { if (ticket === namespaceTicket) reload.disabled = false; }
    };
    const libraryTicketAtOpen = ticket;
    reload.onclick = loadSources;
    const connection = modelSelect("Source connection", [["", "Choose connection"], ...connections.map(c => [c.id, `${c.name} · ${c.database} · ${c.username}`])], "", async value => {
      connectionId = value; namespace = ""; catalog = null; selection = null; create.disabled = true; reload.hidden = true;
      const ticket = ++namespaceTicket;
      disposeSelects(namespaceHost); namespaceHost.replaceChildren(); sourceHost.replaceChildren();
      if (!value) return;
      status.textContent = "Loading visible schemas…";
      try {
        const data = await requestJson(`/api/v1/connections/${value}/namespaces?product=schemoo`, { timeoutMs: 30000 });
        if (ticket !== namespaceTicket || !dialog.open || libraryTicket !== libraryTicketAtOpen) return;
        const available = data.namespaces.filter(n => !n.system);
        namespaceHost.append(modelSelect("Source schema", [["", "Choose schema"], ...available.map(n => [n.name, n.name])], "", value => {
          namespace = value; selection = null; reload.hidden = !value; void loadSources();
        }));
        status.textContent = available.length ? "" : "No accessible non-system schemas on this connection.";
      } catch (error) { if (ticket === namespaceTicket && dialog.open) status.textContent = error.message; }
    });
    const review = element("button", { type: "button", className: "ui-button", text: "Review model list" });
    review.onclick = () => openModelLibrary(onOpen, { onDeleted, canDelete, onAssistant });
    form.append(element("h3", { text: "Create a model" }), element("label", { className: "stack" }, ["Model name", name]), element("label", { className: "stack" }, ["Connection", connection]), namespaceHost, reload, sourceHost, element("div", { className: "source-selection-actions" }, [create, review]));
    form.onsubmit = async event => {
      event.preventDefault(); if (creating || create.disabled || !catalog || !selection || !name.value.trim()) return;
      creating = true; form.inert = true; status.textContent = "Creating selected model…";
      try {
        const parts = splitDraft(importedDraft(catalog, selection.getSelection()));
        const model = await requestJson(`${API}/models`, { method: "POST", body: { name: name.value.trim(), connectionId, namespace, ...parts, catalogFingerprint: catalog.fingerprint }, timeoutMs: 30000 });
        if (!current()) return;
        create.disabled = true;
        if (!await choose(model) && current()) await openModelLibrary(onOpen, { onDeleted, canDelete, onAssistant });
      } catch (error) {
        if (!current()) return;
        status.textContent = error.message;
        if (error.code === "model_source_changed") { create.disabled = true; status.textContent += " Use Reload sources to review the updated selection."; }
        else if (!error.status || error.status >= 500) { creationUncertain = true; create.disabled = true; status.textContent += " Review the model list before creating again to check whether this model was saved."; }
      }
      finally { creating = false; form.inert = false; }
    };
    content.append(form, status);
  } catch (error) { if (ticket === libraryTicket && dialog.open) content.replaceChildren(element("p", { className: "warning", text: error.message })); }
}
