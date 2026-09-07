import { requestJson } from "#common/http.js";
import { element } from "#common/dom.js";
import { modelSelect, disposeSelects } from "./select.js";
import { importedDraft } from "./model-draft.js";
import { splitDraft } from "./model-state.js";

const API = "/api/v1/schemoo";
export async function openModelLibrary(onOpen) {
  const dialog = document.getElementById("model-library"), content = document.getElementById("library-content");
  disposeSelects(content); content.replaceChildren(element("p", { text: "Loading your models and connections…", attrs: { role: "status" } }));
  if (!dialog.open) dialog.showModal();
  try {
    const [{ models }, { connections }] = await Promise.all([requestJson(`${API}/models`), requestJson("/api/v1/connections")]);
    content.replaceChildren();
    const status = element("p", { className: "hint", attrs: { role: "status" } });
    const choose = async model => { try { await onOpen(model.id); dialog.close(); } catch (error) { status.textContent = error.message; } };
    if (models.length) {
      const list = element("div", { className: "model-list" });
      for (const model of models) {
        const button = element("button", { type: "button", className: "model-option" }, [element("strong", { text: model.name }), element("small", { text: `${model.database}.${model.namespace} · revision ${model.revision}` })]);
        button.onclick = () => choose(model); list.append(button);
      }
      content.append(element("h3", { text: "Your saved models" }), list);
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
  } catch (error) { content.replaceChildren(element("p", { className: "warning", text: error.message })); }
}
