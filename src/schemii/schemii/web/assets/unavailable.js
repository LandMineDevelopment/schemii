import { element } from "./dom.js";

export const UNAVAILABLE_CAPABILITIES = Object.freeze({
  "column-create": { title: "Create column", description: "The active API can inspect columns but has no contract for adding them." },
  "column-edit": { title: "Edit column", description: "The active API has no contract for changing column names, types, defaults, or constraints." },
  "column-delete": { title: "Delete column", description: "The active API has no contract for dropping PostgreSQL columns." },
  "relationship-create": { title: "Create relationship", description: "The active API can inspect foreign keys but cannot create relationships." },
  "relationship-edit": { title: "Edit relationship", description: "The active API has no contract for altering foreign-key relationships." },
  "relationship-delete": { title: "Delete relationship", description: "The active API has no contract for dropping foreign-key relationships." },
  "semantic-undo": { title: "Semantic undo", description: "Server-authoritative schema operations and their inverse operations are not available." },
  "semantic-redo": { title: "Semantic redo", description: "Server-authoritative schema operation history is not available." },
  "sql-upload": { title: "Upload SQL", description: "The active API has no contract for parsing or importing an uploaded SQL document." },
  "sql-export": { title: "Export SQL", description: "The active API returns catalog JSON but does not generate an authoritative SQL export." },
  "table-rows": { title: "Browse table rows", description: "The active API exposes catalog metadata only and has no table-row read contract." },
  "sql-console": { title: "SQL console", description: "The active API has no SQL console or statement execution endpoint." },
  "sql-run": { title: "Run SQL", description: "The active API cannot execute SQL statements or scripts." },
  "sql-save": { title: "Save SQL", description: "The active API has no saved-query contract." },
  "sql-write": { title: "SQL write mode", description: "The active API has no authorization or execution contract for SQL writes." },
  "sql-transactions": { title: "SQL transactions", description: "The active API has no server-owned transaction lifecycle contract." },
  "function-mutation": { title: "Mutate function or procedure", description: "The active API can inspect routine definitions but cannot create, replace, or delete routines." },
  "database-object-mutation": { title: "Mutate database object", description: "The active API can browse catalog objects but has no general object mutation contract." },
  "migration-preview": { title: "Preview migration", description: "The active API has no migration planner or reviewed DDL preview contract." },
  "migration-apply": { title: "Apply migration", description: "The active API has no migration authorization or apply contract." },
  "ai-assistant": { title: "AI schema assistant", description: "The active API has no assistant, model, conversation, or proposal contract." },
  "restore-examples": { title: "Restore examples", description: "The active API does not provide example content, and this frontend does not fabricate it." },
  shutdown: { title: "Shut down Schemii", description: "The active API has no authenticated process-shutdown contract." },
  "workspace-naming": { title: "Name workspace", description: "Active workspaces are target bindings and do not have a mutable name field." },
  "namespace-discovery": { title: "Discover namespaces", description: "The active API validates an entered namespace during workspace creation but cannot list namespaces." },
});

function capability(id) {
  const value = UNAVAILABLE_CAPABILITIES[id];
  if (!value) throw new Error(`Unknown unavailable capability: ${id}`);
  return value;
}

export function unavailableButton(id, label, options = {}) {
  capability(id);
  return element("button", {
    ...options,
    className: ["ui-button compact", options.className].filter(Boolean).join(" "),
    type: "button",
    text: label,
    dataset: { ...(options.dataset || {}), unavailable: id },
  });
}

export function assertUnavailableControls(root = document) {
  for (const control of root.querySelectorAll("[data-unavailable]")) capability(control.dataset.unavailable);
}

export function bindUnavailableControls({ dialog, title, description, identifier }) {
  assertUnavailableControls();
  document.addEventListener("click", event => {
    const control = event.target.closest("[data-unavailable]");
    if (!control) return;
    event.preventDefault();
    const id = control.dataset.unavailable;
    const entry = capability(id);
    control.closest("details")?.removeAttribute("open");
    title.textContent = entry.title;
    description.textContent = entry.description;
    identifier.textContent = id;
    dialog.showModal();
  });
}
