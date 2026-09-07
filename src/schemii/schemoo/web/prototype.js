import { requestJson } from "#common/http.js";
import { element } from "#common/dom.js";
import { createDataGrid } from "#common/data-grid.js";
import { createIconButton, initializeUi } from "/assets/common/ui.js";
import { helpButton, helpHeading } from "./help.js";
import { modelSelect, disposeSelects } from "./select.js";
import { isAlias, ensureAliasConnections, removeModelNode, aliasImpact, suggestedAliasLabel } from "./alias-model.js";
import { createModelCanvas } from "./canvas.js";
import { renderParameterValues, renderReportFilters } from "./filter-controls.js";
import { renderModelFilters } from "./filter-dialog.js";
import { importedDraft, staffingDraft } from "./model-draft.js";
import { splitDraft, joinModel, changedParts, exposedFields, setFieldExposure, inheritAliasExposure, initializeExposureFromPreview } from "./model-state.js";
import { renderPreviewFields } from "./preview-fields.js";
import { readExecution } from "/assets/common/query-execution.js";
import { openModelLibrary } from "./model-library.js";

const $ = id => document.getElementById(id), API = "/api/v1/schemoo";
let catalog, draft, canvas, plan, diagnostics = {}, selected, storageKey, timer, version = 0, busy = false;
let model, saving = false, dirty = false, conflicted = false, catalogTicket = 0;
const consoleId = `con_${crypto.randomUUID().replaceAll("-", "")}`;
const name = id => draft.nodes.find(n => n.id === id)?.label || id;
const columns = id => catalog.tables.find(t => t.name === draft.nodes.find(n => n.id === id)?.table)?.columns || [];
function icon(type, label, action) { const b = createIconButton({ icon: type, label, className: "ui-button" }); b.onclick = action; return b; }
function showError(message = "") { $("error").textContent = message; $("error").hidden = !message; }
function save() {
  if (!model || !draft) return;
  dirty = Object.values(changedParts(model, draft, $("model-name").value.trim())).some(Boolean);
  $("save-model").disabled = saving || conflicted || !dirty;
  $("draft-status").textContent = conflicted ? "Save conflict · reload to continue" : saving ? "Saving model…" : dirty ? "Unsaved changes" : `Saved · revision ${model.revision} · read-only preview`;
}
function panel(tab) { maximizeQuery(false); $("table-inspector").hidden = true; $("inspector").hidden = false; for (const id of ["model","explore"]) { $(`${id}-pane`).hidden = id !== tab; $(`${id}-tab`).classList.toggle("active", id === tab); } }
function inspectSelection() {
  maximizeQuery(false);
  $("inspector").hidden = true;
  $("table-inspector").hidden = false;
  renderAliasTools(); renderSelection();
  $("table-pane").scrollTop = 0;
}
function dock(tab) { $("query-dock").hidden = false; for (const id of ["sql","results"]) { $(id).hidden = id !== tab; $(`${id}-tab`).classList.toggle("active",id === tab); } }
function maximizeQuery(maximized) {
  $("workbench").classList.toggle("query-maximized", maximized);
  const button = $("maximize-query"), label = maximized ? "Restore split view" : "Maximize query preview";
  button.setAttribute("aria-pressed", String(maximized));
  button.setAttribute("aria-label", label);
  button.dataset.uiTooltip = label;
  button.classList.toggle("active", maximized);
}
function filterOptions(force = false) { return { draft, catalog, onChange: changed, activeScopes: diagnostics.activeScopes, force, onLoadDomain: loadDomainOptions }; }
function refreshForms() {
  renderModelFilters($("model-filters"), filterOptions()); renderReportFilters($("report-filters"), filterOptions());
  renderParameterValues($("parameter-values"), filterOptions(true)); renderFields(); renderSelection(); renderRelationships();
  disposeSelects($("root"));
  const root = modelSelect("Starting model object", draft.nodes.map(n => [n.id,n.label]), draft.defaultRoot ?? draft.root, value => { draft.defaultRoot=value; draft.root=value; changed(); });
  root.id = "root"; $("root").replaceWith(root);
  renderAliasTools();
}
function renderFields() {
  $("field-count").textContent = draft.fields.length;
  renderPreviewFields($("fields"), { draft, catalog, onChange: changed });
}
function renderRelationships() {
  const cycles = new Set(diagnostics.cycleEdges || []);
  $("relationship-count").textContent = `${draft.edges.filter(e => e.enabled).length} / ${draft.edges.length}`;
  $("relationships").replaceChildren(...draft.edges.map(e => {
    const r = catalog.relationships.find(r => r.id === e.relationshipId);
    const checkbox = element("input", { attrs: { type: "checkbox", "aria-label": `Enable ${e.id}` } }); checkbox.checked = e.enabled;
    checkbox.onchange = () => { e.enabled = checkbox.checked; changed(); };
    const label = element("button", { className: "edge-name", type: "button", text: `${name(e.source)}.${r?.sourceColumn || "missing FK"} → ${name(e.target)}.${r?.targetColumn || "missing FK"}${cycles.has(e.id) ? " · CYCLE" : ""}` });
    label.onclick = () => { selected = { edge: e.id }; inspectSelection(); };
    return element("div", { className: `relationship${cycles.has(e.id) ? " cyclic" : ""}` }, [checkbox,label]);
  }));
  renderNodeConnections();
}
function renderNodeConnections() {
  const host=$("node-connections"); if(!host || !selected?.node) return;
  const cycles=new Set(diagnostics.cycleEdges || []);
  const edges=draft.edges.filter(e=>e.source===selected.node || e.target===selected.node);
  host.replaceChildren(element("h3",{text:`Connections · ${edges.length}`}),element("p",{className:"hint",text:"Toggle each connection independently. Dashed lines are disabled; red lines belong to a cycle."}));
  for(const edge of edges) {
    const relation=catalog.relationships.find(r=>r.id===edge.relationshipId);
    const text=`${name(edge.source)}.${relation?.sourceColumn || "missing FK"} → ${name(edge.target)}.${relation?.targetColumn || "missing FK"}`;
    const enabled=element("input",{attrs:{type:"checkbox","aria-label":`Enable connection ${text}`}}); enabled.checked=edge.enabled;
    enabled.onchange=()=>{edge.enabled=enabled.checked;changed();};
    const edit=element("button",{type:"button",className:"edge-name",text:`${text}${cycles.has(edge.id)?" · CYCLE":""}`});
    edit.className="node-edge-name";
    edit.onclick=()=>{selected={edge:edge.id};inspectSelection();};
    host.append(element("div",{className:`relationship${cycles.has(edge.id)?" cyclic":""}`},[enabled,edit]));
  }
}
function labeled(text, control) { return element("label", { className: "stack" }, [text,control]); }
function nextAliasLabel(node) {
  return suggestedAliasLabel(draft.nodes, node);
}
function renderSelection() {
  const host = $("selection-inspector"); disposeSelects(host); host.replaceChildren();
  const selectedNode = draft.nodes.find(n => n.id === selected?.node);
  $("table-inspector-title").textContent = selectedNode?.label || (selected?.edge ? "Relationship" : "Select a table");
  $("table-inspector-kind").textContent = selectedNode ? (isAlias(selectedNode) ? "TABLE ALIAS" : "MODEL TABLE") : "MODEL CONNECTION";
  $("alias-tools").hidden = Boolean(selected);
  if (selected?.node) {
    const node = draft.nodes.find(n => n.id === selected.node); if (!node) return;
    host.append(helpHeading(isAlias(node)?"Alias table":"Model table", "aliases"),element("p", { className: "hint", text: `Physical source: ${node.table}` }));
    if (!catalog.tables.some(t => t.name === node.table)) {
      host.append(element("p", { className:"warning", text:"This source table is unavailable. Choose its intended replacement; columns and relationships will be validated, not guessed." }),
        modelSelect("Replacement source table", [["", "Choose replacement source"], ...catalog.tables.map(t => [t.name,t.name])], "", value => {
          if (!value) return; node.table=value; ensureAliasConnections(draft,catalog); refreshForms(); changed();
        }));
    }
    const label = element("input", { attrs: { "aria-label": "Object label", maxlength: 100 } }); label.value = node.label;
    label.onchange = () => { node.label = label.value.trim() || node.table; changed(); refreshForms(); };
    const alias = element("button", { type: "button", className: "ui-button", text: "Create alias" });
    const aliasForm = element("form", { className: "selected-alias-form" }); aliasForm.hidden = true;
    const aliasName = element("input", { attrs: { "aria-label": `Alias name for ${node.label}`, maxlength: 100 } });
    aliasName.value = nextAliasLabel(node);
    const createAlias = element("button", { type: "submit", className: "ui-button primary", text: "Add alias to model" });
    const cancelAlias = element("button", { type: "button", className: "ui-button", text: "Cancel" });
    aliasName.oninput = () => { createAlias.disabled = !aliasName.value.trim(); };
    aliasForm.onsubmit = event => { event.preventDefault(); if (aliasName.value.trim()) addAlias(node.table, aliasName.value.trim(), node); };
    cancelAlias.onclick = () => { aliasForm.hidden = true; alias.hidden = false; };
    aliasForm.append(labeled("Alias / role name", aliasName), element("div", { className: "actions" }, [createAlias,cancelAlias]));
    alias.onclick = () => {
      alias.hidden = true; aliasForm.hidden = false; aliasName.focus(); aliasName.select();
    };
    host.append(labeled("Business label",label),alias,aliasForm,element("p",{className:"hint",text:"Aliases have their own available foreign-key connections. Enable only the paths needed for this role."}));
    const missingSource=!catalog.tables.some(t=>t.name===node.table);
    if(isAlias(node)||missingSource) host.append(icon("delete",`${missingSource ? "Remove missing object" : "Delete alias"} ${node.label}`,()=>{
      const impact=aliasImpact(draft,node.id);
      if(!confirm(`Remove model object “${node.label}”? This removes ${impact.connections} connections and ${impact.fields} selected output fields. ${impact.bindings} filter conditions will need a new source binding. ${impact.isRoot?"Another available object becomes the starting object. ":""}The physical table and its data are not deleted.`))return;
      removeModelNode(draft,node.id);selected=null;diagnostics={};refreshForms();changed();
      showError(impact.bindings ? "Object removed. Rebind its affected filter conditions before running the model." : "");
    }));
    host.append(element("section",{attrs:{id:"table-columns"}}), element("div",{attrs:{id:"node-connections"}})); renderTableColumns(); renderNodeConnections();
  } else if (selected?.edge) {
    const edge = draft.edges.find(e => e.id === selected.edge); if (!edge) return;
    const r = catalog.relationships.find(r => r.id === edge.relationshipId);
    if (!r) {
      const source=draft.nodes.find(n=>n.id===edge.source),target=draft.nodes.find(n=>n.id===edge.target);
      const replacements=catalog.relationships.filter(relation=>relation.sourceTable===source?.table && relation.targetTable===target?.table && !draft.edges.some(other=>other.id!==edge.id && other.relationshipId===relation.id && other.source===edge.source && other.target===edge.target));
      host.append(element("p", { className:"warning", text:"This foreign key is no longer present. Choose a compatible replacement or remove this relationship. Your model has been preserved." }),
        modelSelect("Replacement foreign key", [["","Choose replacement foreign key"],...replacements.map(relation=>[relation.id,`${relation.sourceTable}.${relation.sourceColumn} → ${relation.targetTable}.${relation.targetColumn}`])],"",value=>{
          if(!value)return;edge.relationshipId=value;refreshForms();changed();
        }),
        icon("delete", "Remove missing relationship", () => { if(!confirm("Remove this missing relationship from the model? No database objects or data will be changed."))return;draft.edges = draft.edges.filter(e => e.id !== edge.id); selected = null; refreshForms(); changed(); }));
      if(!replacements.length)host.append(element("p",{className:"hint",text:"No unused foreign key connects these physical source tables. Repair missing tables first or remove this connection."}));
      return;
    }
    host.append(helpHeading("Relationship role", "aliases"),element("p", { className: "hint", text: `${r.sourceColumn} → ${r.targetColumn}. Endpoints must be occurrences of the FK's physical tables.` }));
    for (const side of ["source","target"]) {
      const select = modelSelect(`${side} occurrence`, draft.nodes.filter(n => n.table === r[`${side}Table`]).map(n => [n.id,n.label]), edge[side], value => {
        const next={...edge,[side]:value};
        if(draft.edges.some(e=>e.id!==edge.id && e.relationshipId===edge.relationshipId && e.source===next.source && e.target===next.target)) {
          showError("That connection is already available. Toggle the existing connection instead.");renderSelection();return;
        }
        edge[side]=value;showError();changed();
      });
      host.append(labeled(`${side === "source" ? "From" : "To"} object`,select));
    }
    const checkbox = element("input", { attrs: { type:"checkbox","aria-label":"Relationship enabled" } }); checkbox.checked = edge.enabled;
    checkbox.onchange = () => { edge.enabled = checkbox.checked; changed(); };
    host.append(labeled("Enabled",checkbox));
  } else host.append(element("p", { className:"hint",text:"Click a table header to label it or create an alias. Click a connection to choose its source and target occurrences." }));
}
function renderTableColumns() {
  const host = $("table-columns"), node = draft.nodes.find(n => n.id === selected?.node);
  if (!host || !node) return;
  host.replaceChildren(helpHeading("Exposed columns", "fields"), element("p", {className:"hint", text:"Choose fields Schemer users may use. Preview has its own output selections and measures. Hiding a field also removes its preview outputs; existing report filters remain visible for repair."}));
  for (const column of columns(node.id)) {
    const toggle = element("input", {attrs:{type:"checkbox", "aria-label":`Expose ${node.label}.${column.name}`}});
    toggle.checked = exposedFields(draft, catalog).some(field => field.table === node.id && field.column === column.name);
    toggle.onchange = () => {
      setFieldExposure(draft, catalog, node.id, column.name, toggle.checked);
      changed();
    };
    host.append(element("label", {className:"table-column-toggle"}, [toggle, element("span", {text:column.name}), element("small", {text:column.dataType})]));
  }
}
function addAlias(table, label, source = draft.nodes.find(n => n.table === table)) {
  const copy = { id: `alias_${crypto.randomUUID().replaceAll("-","").slice(0,12)}`, table, label,
    x: (source?.x || 0)+300, y: source?.y || 0 };
  draft.nodes.push(copy); inheritAliasExposure(draft, source?.id, copy.id); ensureAliasConnections(draft,catalog); selected={node:copy.id}; refreshForms(); changed();
  inspectSelection();
  $("table-pane").scrollTop=0;
}
function renderAliasTools() {
  const host=$("alias-tools"); disposeSelects(host); host.replaceChildren(helpHeading("Table aliases", "aliases"));
  const open=element("button", {type:"button",className:"ui-button",text:"Add alias",attrs:{"data-ui-tooltip":"Create another named role for an existing table; no database table is copied."}});
  const form=element("div", {attrs:{hidden:""}});
  const selectedSource=draft.nodes.find(node=>node.id===selected?.node && catalog.tables.some(table=>table.name===node.table));
  let table=selectedSource?.table || "";
  const nameInput=element("input",{attrs:{"aria-label":"Alias name",placeholder:"e.g. Job certification requirement",maxlength:100}});
  nameInput.value=selectedSource ? nextAliasLabel(selectedSource) : "";
  const create=element("button",{type:"button",className:"ui-button",text:"Create alias table"}); create.disabled=true;
  let nameTouched=false;
  const validate=()=>{create.disabled=!table || !nameInput.value.trim();};
  nameInput.oninput=()=>{nameTouched=true;validate();};
  const source=modelSelect("Alias source",catalog.tables.map(t=>[t.name,t.name]),table,value=>{
    table=value;
    if(!nameTouched) {
      const node=draft.nodes.find(node=>node.id===value) || {table:value,label:value};
      nameInput.value=nextAliasLabel(node);
    }
    validate();
  });
  form.append(labeled("Existing source table",source),labeled("Alias / role name",nameInput),create,
    element("p",{className:"hint",text:"The alias gets its own available incoming and outgoing connections, initially disabled. Select its table header to toggle them. Its columns also appear in source bindings and return-field choices."}));
  create.onclick=()=>addAlias(table,nameInput.value.trim());
  open.onclick=()=>{form.hidden=!form.hidden;if(!form.hidden)(table ? nameInput : source.querySelector("input")).focus();};
  validate();
  host.append(open,form);
}
function changed(event = {}) {
  if (event?.layoutOnly) { save(); return; }
  version++; plan = null; diagnostics = { ...diagnostics, activeScopes: undefined }; clearTimeout(timer); $("run").disabled = true;
  $("plan-status").textContent = "Checking model and required parameters…"; $("sql").textContent = "Checking model…"; $("warnings").replaceChildren();
  if ($("results").querySelector("table")) $("result-status").textContent = "Previous preview · selections changed";
  if (event?.structure) renderParameterValues($("parameter-values"), filterOptions(true));
  renderFields(); renderTableColumns(); canvas.render({cycleEdges:diagnostics.cycleEdges || [],usedEdges:[]});
  save();
  timer = setTimeout(() => compile(version),300);
}
async function compile(ticket) {
  try {
    const { definition, explore } = splitDraft(draft);
    const next = await requestJson(`${API}/models/${model.id}/validate`, { method:"POST",body:{expectedRevision:model.revision,definition,explore},timeoutMs:30000 }); if (ticket !== version) return;
    plan = next; diagnostics = next; $("sql").textContent = next.sql;
    $("plan-status").textContent = `${next.usedRelationships.length} required connections · ${next.grain}`;
    $("warnings").replaceChildren(...next.warnings.map(w => element("p",{className:"warning",text:w})));
    $("run").disabled = busy;
  } catch (error) {
    if (ticket !== version) return;
    diagnostics = error.details || {}; $("plan-status").textContent = error.message; $("sql").textContent = error.message;
    const warnings = (diagnostics.issues || []).map(issue => element("p",{className:"warning",text:issue.message}));
    if (diagnostics.suggestedRoot?.id && draft.nodes.some(node => node.id === diagnostics.suggestedRoot.id)) {
      const resolution = element("div", { className:"warning" }, [
        element("span", { text:`A connected starting object is available. ` }),
        element("button", { type:"button", className:"ui-button", text:`Use ${diagnostics.suggestedRoot.label}` }),
      ]);
      resolution.querySelector("button").onclick = () => {
        draft.root = diagnostics.suggestedRoot.id;
        refreshForms();
        changed();
      };
      warnings.unshift(resolution);
    }
    $("warnings").replaceChildren(...warnings);
  }
  const count = diagnostics.cycleEdges?.length || 0;
  $("graph-status").textContent = diagnostics.issues?.length ? `${diagnostics.issues.length} source references need repair` : count ? `${count} connections belong to cycles · resolve before running` : `${draft.nodes.length} objects · cycle-free model`;
  $("graph-status").classList.toggle("has-cycles",count > 0);
  renderRelationships(); renderParameterValues($("parameter-values"), filterOptions());
  canvas.render({cycleEdges:diagnostics.cycleEdges || [],usedEdges:plan?.usedRelationships || []});
}
async function execute() {
  const response = await requestJson(`${API}/models/${model.id}/executions`,{method:"POST",body:{expectedRevision:model.revision,explore:splitDraft(draft).explore,consoleId},timeoutMs:30000});
  return readExecution(response);
}
async function run() {
  if (!plan || busy || saving) return;
  busy = true; $("workbench").inert = true; $("run").disabled = true; showError(); dock("results");
  $("result-status").textContent = "Running read-only preview…";
  try {
    if (changedParts(model, draft, $("model-name").value.trim()).definition) throw new Error("Save model changes before running. Preview executes the saved server model.");
    const result = await execute(); $("results").replaceChildren(createDataGrid(result));
    if (!result.rows.length) $("results").append(element("p",{className:"empty",text:"No matching rows. The chosen scope or dates may exclude all records."}));
    $("result-status").textContent = `${result.rows.length} rows · ${result.rows.length===100 ? "preview limit" : "read-only"}`;
    $("sql").textContent = result.plan.sql;
  } catch (error) { showError(error.message); $("result-status").textContent = "Preview failed"; $("results").replaceChildren(element("p",{className:"empty",text:error.message})); }
  finally { busy = false; $("workbench").inert = false; $("run").disabled = !plan; }
}
async function loadDomainOptions(context) {
  const authoring = !!context.draft;
  if (!authoring && changedParts(model, draft, $("model-name").value.trim()).definition) throw new Error("Save model changes before browsing its configured values.");
  const domain = context.domain || context.input?.domain;
  const target = authoring ? "domain-values" : "parameter-values";
  const binding = authoring ? { definition: splitDraft(context.draft).definition, domain } : { scopeId: context.scope.id, alternativeId: context.alternative.id, parameterId: context.input.id };
  const response = await requestJson(`${API}/models/${model.id}/${target}`, { method: "POST", body: { expectedRevision: model.revision, ...binding, search: context.search, consoleId } });
  const result = await readExecution(response);
  const hasLabel = domain?.labelColumn && domain.labelColumn !== domain.column;
  return result.rows.filter(row => row[0] !== null).map(row => ({ value: row[0], label: String(hasLabel ? row[1] ?? row[0] : row[0]), description: hasLabel ? String(row[0]) : "" }));
}

document.querySelector(".top-actions").prepend(helpButton("overview"));
$("table-pane").append(element("section",{className:"editor-section",attrs:{id:"alias-tools"}}), $("selection-inspector"));
$("model-pane").prepend($("root").parentElement);
$("show-model").before(icon("tables", "Inspect table", () => { if (draft) inspectSelection(); }));
document.querySelector("#relationships").before(helpHeading("Relationship paths", "cycles"));
$("root").parentElement.before(helpHeading("Starting object", "root"));
$("fields").before(helpHeading("Field selection and measures", "fields"));
document.querySelector("#query-dock .dock-header .tabs").append(helpButton("preview"));
$("query-actions").prepend(icon("edit", "Configure preview fields and measures", () => panel("explore")));
const tooltips = {
  example: "Load the acyclic staffing example with organization and time filters. Save afterward to keep this model.",
  reset: "Replace this editable model with warehouse foreign keys, including cycles. Does not change the database. Save afterward to keep it.",
  "model-tab": "Choose the query starting table, relationship paths, and reusable model filters.",
  "explore-tab": "Choose return fields, supply model parameter values, and add report filters.",
  "show-model": "Edit model: starting table, connections, and reusable filters.",
  "show-explore": "Configure preview: independent output fields, measures, column order, parameters, and report filters.",
  "show-query": "Inspect the generated SQL and the last preview's rows.",
  "sql-tab": "SQL compiled from the selected fields, model rules, and report filters.",
  "results-tab": "Rows from your last read-only preview. Changed selections require another run.",
  run: "Run a read-only preview of up to 100 rows. Required active parameters must have values and the model must be cycle-free.",
  fit: "Zoom and center to show all model objects.",
};
for (const [id, text] of Object.entries(tooltips)) $(id).dataset.uiTooltip = text;
initializeUi();
for (const tab of ["model","explore"]) { $(`${tab}-tab`).onclick=()=>panel(tab); $(`show-${tab}`).onclick=()=>panel(tab); }
for (const tab of ["sql","results"]) $(`${tab}-tab`).onclick=()=>dock(tab);
$("close-inspector").onclick=()=>{$("inspector").hidden=true;}; $("show-query").onclick=()=>dock("sql"); $("close-query").onclick=()=>{maximizeQuery(false); $("query-dock").hidden=true; $("show-query").focus();};
$("maximize-query").onclick=()=>maximizeQuery(!$("workbench").classList.contains("query-maximized"));
document.querySelector("#query-dock .dock-header").addEventListener("contextmenu", event => {
  if (event.target.closest("#query-actions")) return;
  event.preventDefault();
  maximizeQuery(!$("workbench").classList.contains("query-maximized"));
});
$("close-table-inspector").onclick=()=>{$("table-inspector").hidden=true;};
$("fit").onclick=()=>canvas?.fit(); $("zoom-in").onclick=()=>canvas?.zoomBy(1.2); $("zoom-out").onclick=()=>canvas?.zoomBy(1/1.2);
$("run").onclick=run;
function replaceDraft(factory) { if (!catalog || busy || saving) return; if (!confirm("Replace the editable model? Save afterward to keep it. The warehouse will not be changed.")) return; try {draft=factory(catalog);ensureAliasConnections(draft,catalog);selected=null;diagnostics={};refreshForms();changed();panel("model");requestAnimationFrame(()=>canvas.fit());}catch(error){showError(error.message);} }
$("reset").onclick=()=>replaceDraft(importedDraft); $("example").onclick=()=>replaceDraft(staffingDraft);
// Set the initial mobile layout before loading, so a user's toolbar choice
// made during catalog loading is not overwritten when the request completes.
if (matchMedia("(max-width:850px)").matches) $("inspector").hidden=true;
async function loadModel(id) {
  if (busy || saving) throw new Error("Wait for the current operation to finish.");
  if (dirty && !confirm("Discard unsaved model, layout, and Explore changes?")) return;
  const ticket = ++catalogTicket; clearTimeout(timer); version++; showError();
  $("workbench").inert = true;
  let nextModel, nextCatalog;
  try {
    nextModel = await requestJson(`${API}/models/${encodeURIComponent(id)}`);
    nextCatalog = await requestJson(`${API}/catalog?connection_id=${encodeURIComponent(nextModel.connectionId)}&namespace=${encodeURIComponent(nextModel.namespace)}`,{timeoutMs:30000});
  } catch (error) { $("workbench").inert=!model; throw error; }
  if (ticket !== catalogTicket) return;
  model=nextModel; catalog=nextCatalog; draft=joinModel(model); selected=null; plan=null; diagnostics={}; conflicted=false;
  $("model-name").disabled=false; $("model-name").value=model.name;
  $("reload-model").disabled=false; $("delete-model").disabled=false;
  $("target").textContent=`${catalog.database}.${catalog.namespace}`; $("limitations").textContent=catalog.notice;
  storageKey=catalog.workspaceId ? `schemoo-prototype-v2:${catalog.workspaceId}` : null;
  let saved; try { saved=storageKey && JSON.parse(localStorage.getItem(storageKey)); } catch { /* Optional prototype import, never silently replace. */ }
  $("import-browser").hidden=!saved?.draft?.nodes?.length;
  $("import-browser").onclick=()=>replaceDraft(()=>{const imported=structuredClone(saved.draft);initializeExposureFromPreview(imported);return imported;});
  $("example").hidden=!catalog.tables.some(t=>t.name==="org_hier");
  canvas?.destroy();
  canvas=createModelCanvas({host:$("canvas-host"),catalog,getDraft:()=>draft,onChange:changed,
    onSelectNode:id=>{selected={node:id};inspectSelection();},
    onSelectEdge:id=>{selected={edge:id};inspectSelection();}});
  const url=new URL(location.href); url.searchParams.set("model",model.id); history.replaceState(null,"",url);
  $("results").replaceChildren(element("p", {className:"empty",text:"Run a read-only preview."})); $("result-status").textContent="Nothing run yet";
  refreshForms();changed();$("workbench").inert=false;
  if (model.catalogFingerprint !== catalog.fingerprint) showError("The source schema changed. Your model is preserved. Review the validation messages, repair missing bindings, then save. Reload never replaces your model with a fresh import.");
}

async function saveModel() {
  if (!model || saving || busy || conflicted) return;
  if (!$("model-name").value.trim()) { showError("Give the model a name before saving."); $("model-name").focus(); return; }
  saving=true; $("workbench").inert=true; $("model-name").disabled=true; save(); showError();
  const parts=splitDraft(draft), differences=changedParts(model,draft,$("model-name").value.trim());
  // Capture every expected revision before saving: a semantic response must not
  // silently authorize overwriting a layout/Explore edit from another tab.
  const expected={definition:model.revision,layout:model.layoutRevision,explore:model.exploreRevision};
  const acceptSaved = (next, part) => {
    const expectedRevisions={revision:expected.definition,layoutRevision:expected.layout,exploreRevision:expected.explore};
    const ownKey={definition:"revision",layout:"layoutRevision",explore:"exploreRevision"}[part];
    for(const [key,revision] of Object.entries(expectedRevisions)) {
      if(key!==ownKey && next[key]!==revision) {
        const error=new Error("Another tab changed this model while saving. Some requested changes may already be saved; reload to review the current model.");
        error.status=409;throw error;
      }
    }
    expected[part]=next[ownKey]; model=next;
  };
  try {
    if (differences.definition || model.catalogFingerprint !== catalog.fingerprint) acceptSaved(await requestJson(`${API}/models/${model.id}`, {method:"PUT",body:{expectedRevision:expected.definition,name:$("model-name").value.trim(),definition:parts.definition,catalogFingerprint:catalog.fingerprint}}),"definition");
    if (differences.layout) acceptSaved(await requestJson(`${API}/models/${model.id}/layout`, {method:"PUT",body:{expectedRevision:expected.layout,layout:parts.layout}}),"layout");
    if (differences.explore) acceptSaved(await requestJson(`${API}/models/${model.id}/explore`, {method:"PUT",body:{expectedRevision:expected.explore,explore:parts.explore}}),"explore");
    draft=joinModel(model); refreshForms(); canvas.render({cycleEdges:diagnostics.cycleEdges || [],usedEdges:plan?.usedRelationships || []});
    version++; await compile(version);
  } catch(error) {
    conflicted=error.status===409;
    showError(`${error.message} Your edits remain here. If another tab saved newer work, use Reload saved model to inspect it before trying again.`);
  } finally { saving=false; $("workbench").inert=false; $("model-name").disabled=false; save(); }
}

$("model-name").oninput=save;
$("save-model").onclick=saveModel;
$("open-models").onclick=()=>{if(!busy&&!saving)openModelLibrary(loadModel);};
$("close-model-library").onclick=()=>$("model-library").close();
$("reload-model").onclick=async()=>{try{await loadModel(model.id);}catch(error){showError(error.message);$("workbench").inert=false;}};
$("delete-model").onclick=async()=>{
  if(!model||busy||saving||!confirm(`Delete semantic model “${model.name}” and its saved layout and Explore choices? This cannot be undone. PostgreSQL tables and data will not be deleted.`))return;
  try {
    await requestJson(`${API}/models/${model.id}?expected_revision=${model.revision}`,{method:"DELETE"});
    dirty=false; model=null; canvas?.destroy(); canvas=null; draft=null; clearTimeout(timer); version++;
    $("model-name").value=""; $("model-name").disabled=true; $("save-model").disabled=true; $("reload-model").disabled=true; $("delete-model").disabled=true; $("run").disabled=true; $("workbench").inert=true;
    $("target").textContent="Choose a saved model or create one"; $("draft-status").textContent="Model deleted";
    const url=new URL(location.href);url.searchParams.delete("model");history.replaceState(null,"",url); await openModelLibrary(loadModel);
  }catch(error){showError(error.message);}
};
window.addEventListener("beforeunload",event=>{if(dirty){event.preventDefault();event.returnValue="";}});
try {
  const id=new URL(location.href).searchParams.get("model");
  if(id) await loadModel(id); else await openModelLibrary(loadModel);
}catch(error){showError(error.message);$("target").textContent="Model unavailable";$("workbench").inert=false;}
