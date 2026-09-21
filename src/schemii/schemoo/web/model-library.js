import { requestJson } from "#common/http.js";
import { element } from "#common/dom.js";
import { createIconButton } from "#common/ui.js";
import { confirmModelDeletion } from "./model-deletion.js";
export { confirmModelDeletion } from "./model-deletion.js";
import { namedAction } from "#common/named-action.js";
import { modelSelect, disposeSelects } from "./select.js";
import { importedDraft } from "./model-draft.js";
import { splitDraft } from "./model-state.js";

const API = "/api/v1/schemoo";

let libraryTicket = 0;
let refreshActions = [];
export function refreshModelLibraryActions() {
  if (!document.getElementById("model-library")?.open) return;
  for (const refresh of refreshActions) refresh();
}
export async function openModelLibrary(onOpen, { onDeleted = () => {}, canDelete = () => true } = {}) {
  const ticket = ++libraryTicket;
  refreshActions = [];
  const dialog = document.getElementById("model-library"), content = document.getElementById("library-content");
  disposeSelects(content); content.replaceChildren(element("p", { text: "Loading your models and connections…", attrs: { role: "status" } }));
  if (!dialog.open) dialog.showModal();
  try {
    const [{ models }, { connections }] = await Promise.all([requestJson(`${API}/models`), requestJson("/api/v1/connections")]);
    if (ticket !== libraryTicket || !dialog.open) return;
    content.replaceChildren();
    const status = element("p", { className: "hint", attrs: { role: "status" } });
    const choose = async model => { try { if (await onOpen(model.id) !== false) dialog.close(); } catch (error) { status.textContent = error.message; } };
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
            if (copied) {
              await openModelLibrary(onOpen, { onDeleted, canDelete });
              if (await onOpen(copied.id) !== false) dialog.close();
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
    let connectionId = "", namespace = "", namespaceTicket = 0;
    const namespaceHost = element("div");
    const create = element("button", { type: "submit", className: "ui-button primary", text: "Create model" }); create.disabled = true;
    const connection = modelSelect("Source connection", [["", "Choose connection"], ...connections.map(c => [c.id, `${c.name} · ${c.database} · ${c.username}`])], "", async value => {
      connectionId = value; namespace = ""; create.disabled = true;
      const ticket = ++namespaceTicket;
      disposeSelects(namespaceHost); namespaceHost.replaceChildren();
      if (!value) return;
      status.textContent = "Loading visible schemas…";
      try {
        const data = await requestJson(`/api/v1/connections/${value}/namespaces`, { timeoutMs: 30000 });
        if (ticket !== namespaceTicket) return;
        const available = data.namespaces.filter(n => !n.system);
        namespaceHost.append(modelSelect("Source schema", [["", "Choose schema"], ...available.map(n => [n.name, n.name])], "", value => { namespace = value; create.disabled = !namespace; }));
        status.textContent = available.length ? "" : "No accessible non-system schemas on this connection.";
      } catch (error) { if (ticket === namespaceTicket) status.textContent = error.message; }
    });
    form.append(element("h3", { text: "Create a model" }), element("label", { className: "stack" }, ["Model name", name]), element("label", { className: "stack" }, ["Connection", connection]), namespaceHost, create);
    form.onsubmit = async event => {
      event.preventDefault(); if (!connectionId || !namespace || !name.value.trim()) return;
      form.inert = true; status.textContent = "Inspecting source and creating model…";
      try {
        const catalog = await requestJson(`${API}/catalog?connection_id=${encodeURIComponent(connectionId)}&namespace=${encodeURIComponent(namespace)}`, { timeoutMs: 30000 });
        if (!catalog.tables.length) throw new Error("This schema has no visible tables to model.");
        const parts = splitDraft(importedDraft(catalog));
        const model = await requestJson(`${API}/models`, { method: "POST", body: { name: name.value.trim(), connectionId, namespace, ...parts, catalogFingerprint: catalog.fingerprint }, timeoutMs: 30000 });
        await choose(model);
      } catch (error) { status.textContent = error.message; }
      finally { form.inert = false; }
    };
    content.append(form, status);
  } catch (error) { if (ticket === libraryTicket && dialog.open) content.replaceChildren(element("p", { className: "warning", text: error.message })); }
}
