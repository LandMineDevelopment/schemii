import { edgeRelationship, validateLogicalRelationship } from "./logical-relationships.js";
import { requestJson } from "#common/http.js";
import { element } from "#common/dom.js";
import { createDataGrid } from "#common/data-grid.js";
import { createIconButton, initializeUi } from "/assets/common/ui.js";
import { helpButton, helpHeading } from "./help.js";
import { modelSelect, disposeSelects } from "./select.js";
import { isAlias, ensureAliasConnections, removeModelNode, aliasImpact, suggestedAliasLabel } from "./alias-model.js";
import { createModelCanvas } from "./canvas.js";
import { renderParameterValues, renderReportFilters } from "./filter-controls.js";
import { renderModelFilters, openModelFilter, modelFilterIssue } from "./filter-dialog.js";
import { boundFilterSources, filtersAffectingNode, columnFilterBindings, describeFilterCondition } from "./model-filter-links.js";
import { createColumnFilterEditor } from "./column-filter-editor.js";
import { importedDraft, staffingDraft } from "./model-draft.js";
import { splitDraft, joinModel, changedParts, exposedFields, setFieldExposure, inheritAliasExposure, initializeExposureFromPreview } from "./model-state.js";
import { renderPreviewFields } from "./preview-fields.js";
import { createQueryPlanView, parseQueryPlan } from "#common/query-plan.js";
import { confirmAction } from "#common/confirmation.js";
import { formatElapsed } from "#common/elapsed-time.js";
import { readExecution } from "/assets/common/query-execution.js";
import { openModelLibrary, confirmModelDeletion, refreshModelLibraryActions } from "./model-library.js";
import { createPreviewLibrary } from "./preview-library.js";
import { installProductNavigation } from "#common/product-navigation.js";
import { nodeColumns } from "./model-columns.js";
import { openDerivedSource } from "./derived-dialog.js";
import { repetitionDiagnostics, repetitionNotice, summarySeed } from "./repetition.js";
import { conditionSummary } from "./derived-conditions.js";
import { reconcileSourceCatalog, acceptSourceIssue } from "./source-reconciliation.js";
import { createModelAssistant } from "#common/model-assistant.js";

const $ = id => document.getElementById(id), API = "/api/v1/schemoo";
const MODEL_EXECUTION_TIMEOUT_MS = 900_000;
let catalog, draft, canvas, plan, diagnostics = {}, selected, storageKey, timer, version = 0, busy = false;
let model, saving = false, dirty = false, conflicted = false, catalogTicket = 0;
let initialLayout;
let inspectorTab = "model", selectedFilterId = null;
let sourceAdditions = {tables:[],columns:[],relationships:[],initialized:false};
const expandedFilterColumns = new Set();
let columnEditor = null;
const consoleId = `con_${crypto.randomUUID().replaceAll("-", "")}`;
const previewHost = element("section", { className: "saved-previews", attrs: { "aria-label": "Saved previews" } });
$("explore-pane").prepend(previewHost);
const previewRootHost = element("div", { className: "stack" });
previewHost.after(previewRootHost);
const previewLibrary = createPreviewLibrary({
  host: previewHost,
  onPendingChange: refreshModelLibraryActions,
  getContext: () => ({ draft, catalog, definitionDirty: !!model && !!draft && changedParts(model, draft, $("model-name").value.trim()).definition }),
  isBlocked: () => busy || saving || conflicted || !model,
  applyExplore: explore => {
    Object.assign(draft, structuredClone(explore));
    $("results").replaceChildren(element("p", { className: "empty", text: "Run this preview to see its results." }));
    $("result-status").textContent = "Preview changed · nothing run yet";
    refreshForms(); changed();
  },
});
const name = id => draft.nodes.find(n => n.id === id)?.label || id;
const columns = id => nodeColumns(draft, catalog, draft.nodes.find(n => n.id === id));
function icon(type, label, action) { const b = createIconButton({ icon: type, label, className: "ui-button" }); b.onclick = action; return b; }
function showError(message = "") { $("error").textContent = message; $("error").hidden = !message; }
function sourceIssueNotice(issue) {
  const notice=element("div",{className:`warning source-drift-${issue.severity || "breaking"}`},[element("span",{text:issue.message})]);
  if(issue.acknowledge) {
    const accept=element("button",{type:"button",className:"ui-button",text:"Accept current source"});
    accept.onclick=()=>{
      if(!confirm(`${issue.message}\n\nAccept the current database definition as the model baseline? Changed foreign-key connections will be disabled and must be explicitly re-enabled.`))return;
      if(acceptSourceIssue(draft,catalog,issue)){
        showError();changed({structure:true});
        renderSelection();renderObjectFilters();renderObjectSourceIssues();
      }
    };
    notice.append(accept);
  }
  return notice;
}
function sourceAdditionText() {
  const parts=[];
  if(sourceAdditions.tables.length)parts.push(`${sourceAdditions.tables.length} new table${sourceAdditions.tables.length===1?"":"s"}`);
  if(sourceAdditions.columns.length)parts.push(`${sourceAdditions.columns.length} new column${sourceAdditions.columns.length===1?"":"s"}`);
  if(sourceAdditions.relationships.length)parts.push(`${sourceAdditions.relationships.length} new connection${sourceAdditions.relationships.length===1?"":"s"} (disabled)`);
  return parts.join(" · ");
}
function currentSourceIssues(){return diagnostics.issues || diagnostics.sourceIssues || [];}
function renderObjectSourceIssues(){
  $("object-source-issues")?.remove();
  if($("table-inspector").hidden||!selected)return;
  const node=draft.nodes.find(candidate=>candidate.id===selected.node);
  const edge=draft.edges.find(candidate=>candidate.id===selected.edge);
  const relevant=currentSourceIssues().filter(issue=>
    (node&&(issue.nodeId===node.id||(issue.nodeIds||[]).includes(node.id)||issue.table===node.table))||
    (edge&&(issue.edgeId===edge.id||(issue.edgeIds||[]).includes(edge.id)||(issue.relationshipId && issue.relationshipId===edge.relationshipId))));
  if(!relevant.length)return;
  const section=element("section",{className:"editor-section source-change-section",attrs:{id:"object-source-issues"}},[helpHeading("Source changes","drift"),...relevant.map(sourceIssueNotice)]);
  $("selection-inspector").prepend(section);
}
function save() {
  if (!model || !draft) return;
  dirty = Object.values(changedParts(model, draft, $("model-name").value.trim(), initialLayout)).some(Boolean);
  $("save-model").disabled = saving || conflicted || !dirty;
  $("draft-status").textContent = conflicted ? "Save conflict · reload to continue" : saving ? "Saving model…" : dirty ? "Unsaved changes" : `Saved · revision ${model.revision} · read-only preview`;
  previewLibrary.render();
}
function syncInspectorTools() {
  for (const id of ["model","filters","explore"]) {
    const active=!$("inspector").hidden && !$("workbench").classList.contains("query-maximized") && inspectorTab===id;
    $(`show-${id}`).classList.toggle("active",active);
    $(`show-${id}`).setAttribute("aria-pressed",String(active));
    $(`${id}-tab`).setAttribute("aria-pressed",String(inspectorTab===id));
  }
  $("inspector").setAttribute("aria-label",inspectorTab==="filters" ? "Model filters inspector" : inspectorTab==="explore" ? "Preview inspector" : "Model inspector");
}
function highlightFilter(scope) {
  selectedFilterId=scope?.id || null;
  canvas?.highlightFilters(scope ? [...boundFilterSources(scope)] : []);
}
function panel(tab) { maximizeQuery(false); inspectorTab=tab; $("table-inspector").hidden = true; $("inspector").hidden = false; for (const id of ["model","filters","explore"]) { $(`${id}-pane`).hidden = id !== tab; $(`${id}-tab`).classList.toggle("active", id === tab); } if(tab!=="filters")highlightFilter(null); syncInspectorTools(); }
function inspectSelection() {
  maximizeQuery(false);
  $("inspector").hidden = true;
  $("table-inspector").hidden = false;
  highlightFilter(null);syncInspectorTools();
  renderAliasTools(); renderSelection();renderObjectFilters();renderObjectSourceIssues();
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
  if($("show-filters"))syncInspectorTools();
}
function filterOptions(force = false) { return { draft, catalog, onChange: changed, activeScopes: diagnostics.activeScopes, force, onLoadDomain: loadDomainOptions, onSelect:highlightFilter, redraw:refreshFilterNavigation }; }
function refreshFilterNavigation() {
  if(!draft)return;
  renderModelFilters($("model-filters"), filterOptions());
  const issues=(draft.scopes || []).filter(scope=>modelFilterIssue(scope,draft,catalog));
  const description=`${draft.scopes.length} model filters${issues.length ? ` · ${issues.length} need attention` : ""}`;
  $("filters-status").textContent=description;
  $("show-filters").classList.toggle("has-warning",issues.length>0);
  $("show-filters").setAttribute("aria-description",description);
  $("show-filters").dataset.uiTooltip=`Model filters · ${description}. Define reusable rules and parameters.`;
  const focused=draft.scopes.find(scope=>scope.id===selectedFilterId);
  highlightFilter(focused);renderObjectFilters();
}
function openContextFilter(scope, sourceId, returnTarget) {
  panel("filters");
  openModelFilter({...filterOptions(),sourceId},scope,returnTarget);
}
function renderObjectFilters() {
  const previous=$("object-filters");previous?.remove();
  const node=draft?.nodes.find(n=>n.id===selected?.node);if(!node)return;
  const section=element("section",{className:"editor-section",attrs:{id:"object-filters","aria-label":"Filters affecting this object"}},[helpHeading("Filters affecting this object","scopes")]);
  const relevant=filtersAffectingNode(draft,node.id);
  if(!relevant.length)section.append(element("p",{className:"hint",text:"No model filters affect this object yet."}));
  for(const {scope,direct} of relevant){
    const link=element("button",{type:"button",className:"ui-button object-filter-link",text:scope.label});
    link.onclick=()=>openContextFilter(scope,null,link);
    section.append(link,element("small",{className:"hint",text:`${scope.kind==="required"?"Always evaluate":"Source-conditional"} · ${scope.requirement==="optional"?"Optional":"Required"} · ${direct?"bound to this source or its calculation inputs":"applies across the model"}`}));
  }
  if($("table-columns"))$("table-columns").before(section);else $("table-pane").append(section);
}
function refreshForms() {
  refreshFilterNavigation(); renderReportFilters($("report-filters"), filterOptions());
  renderParameterValues($("parameter-values"), filterOptions(true)); renderFields(); renderSelection(); renderRelationships();
  disposeSelects($("root"));
  const root = modelSelect("Starting model object", draft.nodes.filter(n => !n.derivation).map(n => [n.id,n.label]), draft.defaultRoot ?? draft.root, value => { draft.defaultRoot=value; draft.root=value; changed(); });
  root.id = "root"; $("root").replaceWith(root);
  disposeSelects(previewRootHost);
  previewRootHost.replaceChildren(modelSelect("Preview starting object", draft.nodes.filter(n => !n.derivation).map(n => [n.id,n.label]), draft.root,
    value => { draft.root = value; changed(); }));
  renderAliasTools();renderObjectFilters();
}
function renderFields() {
  $("field-count").textContent = draft.fields.length;
  renderPreviewFields($("fields"), { draft, catalog, onChange: changed });
}
function editLogicalRelationship(existing, source) {
  const nodes = draft.nodes.filter(node => !node.derivation && catalog.tables.some(table => table.name === node.table));
  if (!nodes.length) return;
  const value = existing ? {...existing} : {id:`logical_${crypto.randomUUID().replaceAll("-", "")}`,kind:"logical",relationshipId:null,source:source || nodes[0].id,target:nodes.find(node => node.id !== source)?.id || nodes[0].id,sourceColumn:"",targetColumn:"",cardinality:"many_to_many",enabled:true};
  const host = $("selection-inspector");
  selected = existing ? {edge:existing.id} : null;
  maximizeQuery(false); $("inspector").hidden=true; $("table-inspector").hidden=false; $("alias-tools").hidden=true;
  $("table-inspector-title").textContent="Logical relationship"; $("table-inspector-kind").textContent="MODEL CONNECTION";syncInspectorTools();
  const render = () => {
    disposeSelects(host); host.replaceChildren(element("h3",{text:existing ? "Edit logical relationship" : "Add logical relationship"}),element("p",{className:"hint",text:"Join two columns by equality. This relationship belongs to the model and does not create a database constraint. Query traversal follows the model’s starting object."}));
    for (const side of ["source","target"]) {
      host.append(labeled(side === "source" ? "From object" : "To object", modelSelect(`${side} object`,nodes.map(node=>[node.id,node.label]),value[side],id=>{value[side]=id;value[`${side}Column`]="";render();})));
      host.append(labeled("Column",modelSelect(`${side} column`,[["","Choose column"],...columns(value[side]).map(column=>[column.name,column.name])],value[`${side}Column`],column=>{value[`${side}Column`]=column;})));
    }
    const error=element("p",{className:"warning",attrs:{role:"alert"}});
    const save=element("button",{type:"button",className:"ui-button primary",text:existing ? "Save relationship" : "Add relationship"});
    save.onclick=()=>{error.textContent=validateLogicalRelationship(value,draft,catalog);if(error.textContent)return;if(existing)Object.assign(existing,value);else draft.edges.push(value);selected={edge:value.id};refreshForms();changed();inspectSelection();};
    const cancel=element("button",{type:"button",className:"ui-button",text:"Cancel"});cancel.onclick=()=>{refreshForms();if(!existing)panel("model");};
    host.append(error,element("div",{className:"actions"},[save,cancel]));
  };
  render(); host.scrollIntoView({block:"nearest"});
}
function renderRelationships() {
  const cycles = new Set(diagnostics.cycleEdges || []);
  $("relationship-count").textContent = `${draft.edges.filter(e => e.enabled).length} / ${draft.edges.length}`;
  const add = element("button",{type:"button",className:"ui-button",text:"Add logical relationship"});
  add.onclick=()=>{canvas.startConnection();$("inspector").hidden=true;$("table-inspector").hidden=true;syncInspectorTools();};
  $("relationships").replaceChildren(add,...draft.edges.map(e => {
    const r = edgeRelationship(e, catalog);
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
    const relation=edgeRelationship(edge, catalog);
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
function editDerived(owner, existing, initial) {
  openDerivedSource({draft,catalog,owner,existing,initial,onLoadDomain:loadDomainOptions,onApply:node=>{
    const exposure=exposedFields(draft,catalog);
    const previous=new Set(existing?.derivation.outputs.map(output=>output.id) || []);
    if(existing) draft.nodes[draft.nodes.indexOf(existing)]=node;
    else draft.nodes.push(node);
    const available=new Set(nodeColumns(draft,catalog,node).map(c=>c.name));
    draft.fields=draft.fields.filter(f=>f.table!==node.id || available.has(f.column));
    draft.exposedFields=exposure.filter(f=>f.table!==node.id || available.has(f.column));
    for(const output of node.derivation.outputs) if(!previous.has(output.id)) setFieldExposure(draft,catalog,node.id,output.id,true);
    selected={node:node.id}; refreshForms(); changed(); inspectSelection();
  }});
}
function renderSelection() {
  $("selection-inspector").hidden = false;
  const host = $("selection-inspector"); disposeSelects(host); host.replaceChildren();
  const selectedNode = draft.nodes.find(n => n.id === selected?.node);
  $("table-inspector-title").textContent = selectedNode?.label || (selected?.edge ? "Relationship" : "Select a table");
  $("table-inspector-kind").textContent = selectedNode ? (isAlias(selectedNode) ? "TABLE ALIAS" : "MODEL TABLE") : "MODEL CONNECTION";
  $("alias-tools").hidden = Boolean(selected);
  if (selected?.node) {
    const node = draft.nodes.find(n => n.id === selected.node); if (!node) return;
    if(node.derivation) {
      $("table-inspector-kind").textContent=node.derivation.kind==="row" ? "ROW CALCULATION" : "RELATED SUMMARY";
      host.append(element("h3",{text:node.label}),element("p",{className:"hint",text:`Source: ${name(node.derivation.source)}. ${node.derivation.kind==="row" ? "Calculated directly in SELECT; no extra join." : `One row per ${node.derivation.groupBy.join(" + ")}. Connects to ${name(node.derivation.connection?.target || node.derivation.source)}${node.derivation.connection ? ` (${node.derivation.connection.columns.map(p=>`${p.source} → ${p.target}`).join(", ")})` : ""}.`}`}),icon("edit","Edit calculated source",()=>editDerived(null,node)),icon("delete","Delete calculated source",()=>{
        if(!confirm(`Remove “${node.label}” and its output selections from this model? No database data is deleted.`))return;
        try { removeModelNode(draft,node.id);selected=null;refreshForms();changed(); } catch(error) {showError(error.message);}
      }));
      for(const output of node.derivation.outputs) {
        host.append(element("p",{className:"hint",text:`${output.label}: ${output.operation}(${name(output.nodeId || node.derivation.source)}.${output.column}${output.operand ? `, ${output.operand}` : ""})`}));
        if(output.conditions?.length) host.append(element("p",{className:"hint",text:`Only when: ${output.conditions.map(c=>`(${conditionSummary(c,draft)})`).join(" AND ")}`}));
      }
      host.append(element("section",{attrs:{id:"table-columns"}}));renderTableColumns();return;
    }
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
    if(!node.derivation) host.append(icon("delete",`${missingSource ? "Remove missing object" : "Remove from model"} ${node.label}`,()=>{
      const impact=aliasImpact(draft,node.id);
      if(!confirm(`Remove model object “${node.label}”? This removes ${impact.connections} connections and ${impact.fields} selected output fields. ${impact.bindings} filter conditions will need a new source binding. ${impact.isRoot?"Another available object becomes the starting object. ":""}The physical table or view and its data are not deleted.`))return;
      try {removeModelNode(draft,node.id);} catch(error) {showError(error.message);return;}
      selected=null;diagnostics={};refreshForms();changed();
      showError(impact.bindings ? "Object removed. Rebind its affected filter conditions before running the model." : "");
    }));
    const calculation=element("button",{type:"button",className:"ui-button",text:"Add calculated source",attrs:{"data-ui-tooltip":"Define row calculations or summarize related records without changing the database."}});
    calculation.onclick=()=>editDerived(node);
    host.append(calculation,element("section",{attrs:{id:"table-columns"}}), element("div",{attrs:{id:"node-connections"}})); renderTableColumns(); renderNodeConnections();
  } else if (selected?.edge) {
    const edge = draft.edges.find(e => e.id === selected.edge); if (!edge) return;
    const r = edgeRelationship(edge, catalog);
    if (edge.kind === "logical") {
      host.append(element("h3",{text:"Logical relationship"}),element("p",{className:"hint",text:`${name(edge.source)}.${edge.sourceColumn} = ${name(edge.target)}.${edge.targetColumn}`}),element("p",{className:"hint",text:"Model equality join; no database constraint is created."}),icon("edit","Edit logical relationship",()=>editLogicalRelationship(edge)),icon("delete","Delete logical relationship",()=>{if(!confirm("Remove this logical relationship from the model?"))return;draft.edges=draft.edges.filter(other=>other.id!==edge.id);selected=null;refreshForms();changed();}));
      const enabled=element("input",{attrs:{type:"checkbox","aria-label":"Relationship enabled"}});enabled.checked=edge.enabled;enabled.onchange=()=>{edge.enabled=enabled.checked;changed();};host.append(labeled("Enabled",enabled));
      return;
    }
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
      const select = modelSelect(`${side} occurrence`, draft.nodes.filter(n => !n.derivation && n.table === r[`${side}Table`]).map(n => [n.id,n.label]), edge[side], value => {
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
  if (columnEditor?.nodeId !== node.id) { if (columnEditor) disposeSelects(columnEditor.element); columnEditor = null; }
  columnEditor?.element.remove();
  host.replaceChildren(helpHeading("Exposed columns", "fields"), element("p", {className:"hint", text:"Choose fields Schemer users may use. Preview has its own output selections and measures. Hiding a field also removes its preview outputs; existing report filters remain visible for repair."}));
  for (const column of columns(node.id)) {
    const toggle = element("input", {attrs:{type:"checkbox", "aria-label":`Expose ${node.label}.${column.name}`}});
    toggle.checked = exposedFields(draft, catalog).some(field => field.table === node.id && field.column === column.name);
    toggle.onchange = () => {
      setFieldExposure(draft, catalog, node.id, column.name, toggle.checked);
      changed();
    };
    const key = JSON.stringify([node.id, column.name]);
    const bindings = columnFilterBindings(draft, node.id, column.name);
    const details = element("div", {className:"column-filter-details", attrs:{id:`column-filters-${crypto.randomUUID()}`}});
    details.hidden = !expandedFilterColumns.has(key);
    const expand = icon("expand", `Show filters for ${node.label}.${column.name}`, () => {
      if (expandedFilterColumns.has(key)) expandedFilterColumns.delete(key); else expandedFilterColumns.add(key);
      details.hidden = !expandedFilterColumns.has(key);
      expand.setAttribute("aria-expanded", String(!details.hidden));
    });
    expand.classList.add("column-filter-chevron");
    expand.setAttribute("aria-expanded", String(!details.hidden));
    expand.setAttribute("aria-controls", details.id);
    const add = icon("add", `Add filter to ${node.label}.${column.name}`, () => {
      if (columnEditor) disposeSelects(columnEditor.element);
      expandedFilterColumns.add(key);
      columnEditor = {nodeId:node.id, column:column.name, element:createColumnFilterEditor({draft,catalog,nodeId:node.id,column,onLoadDomain:loadDomainOptions,
        onChange:changed, onClose:()=>{columnEditor=null;renderTableColumns();}, onOpenFilters:()=>panel("filters")})};
      renderTableColumns();
      columnEditor.element.scrollIntoView({block:"nearest"});
    });
    add.disabled = !!node.derivation;
    if (node.derivation) add.dataset.uiTooltip = "Bind model filters to the calculation's source columns, not its calculated outputs.";
    const label = element("label", {className:"table-column-toggle"}, [toggle,element("span", {text:column.label || column.name}),element("small", {text:column.dataType})]);
    const row = element("section", {className:`table-filter-column${bindings.length ? " has-filters" : ""}`}, [element("div", {className:"table-filter-column__header"}, [expand,label,add]),details]);
    if (!bindings.length) details.append(element("p", {className:"hint", text:node.derivation ? "Model rules apply to this calculation through its source columns." : "No model filter conditions are bound to this column."}));
    for (const scope of new Set(bindings.map(binding=>binding.scope))) {
      for (const binding of bindings.filter(candidate=>candidate.scope===scope)) {
        const alternative=binding.alternative;
        const link=element("button", {className:"column-filter-binding__name",text:scope.label,attrs:{type:"button","aria-label":`Open filter ${scope.label}`}});
        link.onclick=()=>openContextFilter(scope,null,link);
        const group=element("div",{className:"column-filter-option"},[element("div",{className:"column-filter-binding__header"},[
          link,
          ...(scope.alternatives.length>1 ? [element("small",{className:"column-filter-binding__option",text:alternative.label})] : []),
          element("small",{className:"column-filter-binding__kind",text:scope.kind==="required" ? "Required" : "Conditional"})])]);
        binding.conditions.forEach((condition,index)=>{
          const predicate=describeFilterCondition(condition,alternative,draft,{table:node.id,column:column.name});
          const edit=icon("edit",`Edit binding ${scope.label} for ${node.label}.${column.name}`,()=>{
            if(columnEditor)disposeSelects(columnEditor.element);
            expandedFilterColumns.add(key);
            columnEditor={nodeId:node.id,column:column.name,element:createColumnFilterEditor({draft,catalog,nodeId:node.id,column,
              binding:{scopeId:scope.id,alternativeId:alternative.id,conditionIndex:binding.conditionIndexes[index]},onLoadDomain:loadDomainOptions,
              onChange:changed,onClose:()=>{columnEditor=null;renderTableColumns();},onOpenFilters:()=>panel("filters")})};
            renderTableColumns();columnEditor.element.scrollIntoView({block:"nearest"});
          });
          edit.classList.add("column-filter-binding__edit");
          group.append(element("div",{className:"column-filter-binding__condition"},[
            element("p",{className:"column-filter-predicate matches-column",text:predicate}),edit]));
        });
        details.append(group);
      }
    }
    if (columnEditor?.column===column.name) details.append(columnEditor.element);
    host.append(row);
  }
}
function addAlias(table, label, source = draft.nodes.find(n => n.table === table)) {
  const copy = { id: `alias_${crypto.randomUUID().replaceAll("-","").slice(0,12)}`, table, label,
    x: (source?.x || 0)+300, y: source?.y || 0 };
  draft.nodes.push(copy); inheritAliasExposure(draft, source?.id, copy.id); ensureAliasConnections(draft,catalog); selected={node:copy.id}; refreshForms(); changed();
  inspectSelection();
  $("table-pane").scrollTop=0;
}
function addTable(table) {
  if (draft.nodes.some(node => !node.derivation && node.id === table && node.table === table)) {
    showError(`“${table}” is already in this model.`); return;
  }
  const source = catalog.tables.find(candidate => candidate.name === table);
  if (!source) { showError(`“${table}” is not available from this source.`); return; }
  const position = catalog.positions?.find(candidate => candidate.name === table);
  const node = { id: table, table, label: table,
    ...(Number.isFinite(position?.x) && Number.isFinite(position?.y) ? { x: position.x, y: position.y } : {}) };
  draft.nodes.push(node);
  const canonical = new Map(draft.nodes.filter(candidate => !candidate.derivation && candidate.id === candidate.table)
    .map(candidate => [candidate.table, candidate.id]));
  const tuples = new Set(draft.edges.map(edge => JSON.stringify([edge.relationshipId, edge.source, edge.target])));
  const ids = new Set(draft.edges.map(edge => edge.id));
  for (const relationship of catalog.relationships) {
    const sourceId = canonical.get(relationship.sourceTable), targetId = canonical.get(relationship.targetTable);
    if (!sourceId || !targetId) continue;
    const tuple = JSON.stringify([relationship.id, sourceId, targetId]);
    if (tuples.has(tuple)) continue;
    let id = `source_fk_${relationship.id}`, suffix = 2;
    while (ids.has(id)) id = `source_fk_${relationship.id}_${suffix++}`;
    draft.edges.push({ id, relationshipId: relationship.id, source: sourceId, target: targetId, enabled: false });
    ids.add(id); tuples.add(tuple);
  }
  ensureAliasConnections(draft, catalog);
  selected = { node: node.id }; diagnostics = {}; refreshForms(); changed({ structure: true }); inspectSelection();
  $("table-pane").scrollTop = 0;
}
function renderAliasTools() {
  const host = $("alias-tools"); disposeSelects(host);
  host.replaceChildren(element("h3", { text: "Add table or view" }));
  let table = "";
  const existingSource = () => draft.nodes.find(node => !node.derivation && node.table === table);
  const alias = element("input", { attrs: { type: "checkbox", "aria-label": "Add as alias" } });
  const aliasName = element("input", { attrs: { "aria-label": "Alias name", maxlength: 100 } });
  const nameField = labeled("Alias name", aliasName);
  const hint = element("p", { className: "hint" });
  const create = element("button", { type: "button", className: "ui-button primary", text: "Add table to model" });
  const update = () => {
    const existing = existingSource();
    alias.disabled = Boolean(existing) || !table;
    const asAlias = Boolean(existing) || alias.checked;
    nameField.hidden = !asAlias;
    create.textContent = asAlias ? "Add alias to model" : "Add table to model";
    create.disabled = !table || (asAlias && !aliasName.value.trim());
    hint.textContent = existing ? "This source is already in the model, so it will be added as an alias."
      : "Choose Add as alias to give this source a separate role, even when it is not yet in the model.";
  };
  const source = modelSelect("Table to add", [["", "Choose a table or view"], ...catalog.tables.map(t => [t.name, t.name])], table, value => {
    table = value;
    alias.checked = Boolean(existingSource());
    aliasName.value = nextAliasLabel(existingSource() || { table, label: table });
    update();
  });
  alias.onchange = update;
  aliasName.oninput = update;
  create.onclick = () => {
    if (create.disabled) return;
    if (existingSource() || alias.checked) addAlias(table, aliasName.value.trim(), existingSource());
    else addTable(table);
  };
  host.append(labeled("Database table or view", source), labeled("Add as alias", alias), nameField, hint, create);
  if (!catalog.tables.length) hint.textContent = "No tables or views are available in this schema.";
  update();
}
function openAddTable() {
  if (!draft) return;
  selected = null;
  inspectSelection();
  $("table-inspector-title").textContent = "Add table";
  $("table-inspector-kind").textContent = "MODEL SOURCE";
  $("selection-inspector").hidden = true;
  $("alias-tools").hidden = false;
  $("alias-tools").querySelector("input")?.focus();
}

function changed(event = {}) {
  if (event?.layoutOnly) { save(); return; }
  version++; plan = null; diagnostics = { ...diagnostics, activeScopes: undefined }; clearTimeout(timer); $("run").disabled = true;
  $("plan-status").textContent = "Checking model and required parameters…"; $("sql").textContent = "Checking model…"; $("warnings").replaceChildren();
  if ($("results").querySelector("table")) $("result-status").textContent = "Previous preview · selections changed";
  if (event?.structure) {renderParameterValues($("parameter-values"), filterOptions(true));refreshFilterNavigation();}
  renderFields(); renderTableColumns(); canvas.render({cycleEdges:diagnostics.cycleEdges || [],usedEdges:[],sourceIssues:diagnostics.issues || diagnostics.sourceIssues || []});
  save();
  timer = setTimeout(() => compile(version),300);
}
async function compile(ticket) {
  try {
    const { definition, explore } = splitDraft(draft);
    const next = await requestJson(`${API}/models/${model.id}/validate`, { method:"POST",body:{expectedRevision:model.revision,definition,explore},timeoutMs:MODEL_EXECUTION_TIMEOUT_MS }); if (ticket !== version) return;
    plan = next; diagnostics = next; $("sql").textContent = next.sql;
    $("plan-status").textContent = `${next.usedRelationships.length} required connections · ${next.grain}`;
    const repetitionMessages = new Set(repetitionDiagnostics(next).map(item => item.message));
    $("warnings").replaceChildren(...(next.sourceIssues || []).map(sourceIssueNotice),...next.warnings.filter(w=>!repetitionMessages.has(w) && !(next.sourceIssues || []).some(issue=>issue.message===w)).map(w => element("p",{className:"warning",text:w})));
    const repetition = repetitionNotice(next, { draft,
      onCreateSummary: measure => { const initial = summarySeed(draft, measure); if (initial) editDerived(null, null, initial); },
      onEditSummary: summary => editDerived(null, summary),
    });
    if (repetition) $("warnings").prepend(repetition);
    $("run").disabled = busy;
  } catch (error) {
    if (ticket !== version) return;
    diagnostics = error.details || {}; $("plan-status").textContent = error.message; $("sql").textContent = error.message;
    const warnings = (diagnostics.issues || []).map(sourceIssueNotice);
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
  const additions=sourceAdditionText(),sourceIssues=currentSourceIssues();
  $("graph-status").textContent = sourceIssues.length ? `${sourceIssues.length} source change${sourceIssues.length===1?"":"s"} need${sourceIssues.length===1?"s":""} review` : count ? `${count} connections belong to cycles · resolve before running` : additions || `${draft.nodes.length} objects · cycle-free model`;
  $("graph-status").classList.toggle("has-cycles",count > 0);
  renderRelationships(); renderParameterValues($("parameter-values"), filterOptions());refreshFilterNavigation();
  renderObjectSourceIssues();
  canvas.render({cycleEdges:diagnostics.cycleEdges || [],usedEdges:plan?.usedRelationships || [],sourceIssues:diagnostics.issues || diagnostics.sourceIssues || []});
}
let queryController = null, comparisonPlan = null;
const stopQuery = element("button", { type: "button", className: "ui-button", text: "Stop", attrs: { hidden: "" } });
const explainQuery = element("button", { type: "button", className: "ui-button", text: "Explain" });
const analyzeQuery = element("button", { type: "button", className: "ui-button", text: "Run & Analyze" });
$("run").before(explainQuery, analyzeQuery, stopQuery);
stopQuery.onclick = () => { queryController?.abort(); stopQuery.disabled = true; };
explainQuery.onclick = () => void run(false);
analyzeQuery.onclick = () => void confirmAction({
  title: "Run & Analyze", message: "Execute the full read-only query on a fresh snapshot to measure its plan? This can take as long as the query itself.",
  confirmLabel: "Run & Analyze", onConfirm: () => { void run(true); },
});
async function run(analyze = null) {
  if (!plan || busy || saving) return;
  // A click event is not an analysis request.
  if (typeof analyze !== "boolean") analyze = null;
  busy = true; $("model-shell").inert = true; document.querySelector(".topbar").inert = true; $("run").disabled = true; explainQuery.disabled = true; analyzeQuery.disabled = true;
  queryController = new AbortController(); stopQuery.hidden = false; stopQuery.disabled = false;
  showError(); dock("results");
  const started = Date.now();
  const progress = activity => {
    const waits = activity.waitEvent ? ` · waiting: ${activity.waitEvent}` : "";
    const blockers = activity.blockerPids?.length ? ` · blocked by ${activity.blockerPids.join(", ")}` : "";
    $("result-status").textContent = `${activity.phase || "Preparing query"} · ${formatElapsed(Date.now() - started)}${waits}${blockers}${activity.monitorUnavailable || activity.monitoringAvailable === false ? " · live details unavailable" : ""}`;
  };
  progress({});
  const timer = setInterval(() => { if (!receiptReceived) progress({}); }, 100);
  let receiptReceived = false;
  try {
    if (changedParts(model, draft, $("model-name").value.trim()).definition) throw new Error("Save model changes before running. Preview executes the saved server model.");
    const response = await requestJson(`${API}/models/${model.id}/${analyze === null ? "executions" : "explain"}`, {
      method: "POST", body: { expectedRevision: model.revision, explore: splitDraft(draft).explore, consoleId, ...(analyze === null ? {} : { analyze }) }, timeoutMs: MODEL_EXECUTION_TIMEOUT_MS,
    });
    receiptReceived = true;
    const result = await readExecution(response, { signal: queryController.signal, onActivity: progress });
    if (analyze === null) {
      $("results").replaceChildren(createDataGrid(result));
      if (!result.rows.length) $("results").append(element("p", {className:"empty",text:"No matching rows. The chosen scope or dates may exclude all records."}));
    } else {
      const record = { plan: parseQueryPlan(result.rows[0]?.[0]), sql: result.plan.sql, analyze, capturedAt: new Date().toISOString() };
      $("results").replaceChildren(createQueryPlanView(record, { comparison: comparisonPlan, onCompare: value => { comparisonPlan = value; } }));
    }
    $("result-status").textContent = `${analyze === null ? `${result.rows.length} rows · preview` : analyze ? "Analyzed plan" : "Estimated plan"} · ${formatElapsed(Date.now() - started)}`;
    $("sql").textContent = result.plan.sql;
    if (result.cleanupWarning) showError(result.cleanupWarning);
  } catch (error) {
    showError(error.message); $("result-status").textContent = `${queryController.signal.aborted && error.message === "Query cancelled." ? "Cancelled" : "Query failed"} · ${formatElapsed(Date.now() - started)}`;
    $("results").replaceChildren(element("p", {className:"empty",text:error.message}));
  } finally {
    clearInterval(timer); $("model-shell").inert = false; document.querySelector(".topbar").inert = false; busy = false; queryController = null; stopQuery.hidden = true;
    $("run").disabled = !plan; explainQuery.disabled = !plan; analyzeQuery.disabled = !plan;
  }
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
$("graph-status").after(helpButton("connectionColors"));
$("table-pane").append(element("section",{className:"editor-section",attrs:{id:"alias-tools"}}), $("selection-inspector"));
$("model-pane").prepend($("root").parentElement);
$("show-model").before(icon("add", "Add table", openAddTable));
const filtersTool=icon("filter","Model filters",()=>panel("filters"));filtersTool.id="show-filters";$("show-model").after(filtersTool);
document.querySelector("#relationships").before(helpHeading("Relationship paths", "cycles"));
$("root").parentElement.before(helpHeading("Starting object", "root"));
$("fields").before(helpHeading("Field selection and measures", "fields"));
document.querySelector("#query-dock .dock-header .tabs").append(helpButton("preview"));
$("query-actions").prepend(icon("edit", "Configure preview fields and measures", () => panel("explore")));
const tooltips = {
  example: "Load the acyclic staffing example with organization and time filters. Save afterward to keep this model.",
  reset: "Replace this editable model with warehouse foreign keys, including cycles. Does not change the database. Save afterward to keep it.",
  "model-tab": "Choose the query starting table and relationship paths.",
  "filters-tab": "Author required and conditional model rules and their parameters.",
  "explore-tab": "Choose return fields, supply model parameter values, and add report filters.",
  "show-model": "Edit model: starting table and connections.",
  "show-explore": "Configure preview: independent output fields, measures, column order, parameters, and report filters.",
  "show-query": "Inspect the generated SQL and the last preview's rows.",
  "sql-tab": "SQL compiled from the selected fields, model rules, and report filters.",
  "results-tab": "Rows from your last read-only preview. Changed selections require another run.",
  run: "Run a read-only preview of up to 100 rows. Required active parameters must have values and the model must be cycle-free.",
  fit: "Zoom and center to show all model objects.",
};
for (const [id, text] of Object.entries(tooltips)) $(id).dataset.uiTooltip = text;
installProductNavigation(document.getElementById("product-navigation"), { activeProduct: "schemoo" });
const assistantButton = createIconButton({ icon: "assistant", label: "Open model assistant", className: "ui-button" });
assistantButton.id = "ai-assistant-button";
document.querySelector(".toolbar .tools").append(assistantButton);
const assistant = createModelAssistant({
  trigger: assistantButton,
  getModelId: () => model?.id || null,
  onModelChanged: async (id, revision, operations) => {
    if (id !== model?.id) return;
    if (operations?.length && operations.every(item => ["create_preview", "update_preview", "delete_preview"].includes(item.operation))) {
      if (previewLibrary.isPending()) return "Saved previews changed. Refresh the preview list after your current operation finishes.";
      await previewLibrary.load(id, false);
      return "Saved preview list refreshed. Your current query choices and model edits are preserved.";
    }
    if (operations?.some(item => item.operation === "delete_model")) {
      try { await requestJson(`${API}/models/${encodeURIComponent(id)}`); }
      catch (error) {
        if (error.status !== 404) throw error;
        conflicted = true; save();
        return "The assistant deleted the saved model. The current canvas is preserved for reference. Open the model library to choose another model.";
      }
    }
    if (dirty || saving || busy) {
      conflicted = true; save();
      return "The assistant saved model changes. Your local edits are preserved. Use Reload saved model to review the saved version when ready.";
    }
    await loadModel(id);
    return "Model refreshed with the assistant’s saved changes.";
  },
});
initializeUi();
for (const tab of ["model","filters","explore"]) { $(`${tab}-tab`).onclick=()=>panel(tab); $(`show-${tab}`).onclick=()=>panel(tab); }
for (const tab of ["sql","results"]) $(`${tab}-tab`).onclick=()=>dock(tab);
$("close-inspector").onclick=()=>{$("inspector").hidden=true;highlightFilter(null);syncInspectorTools();}; $("show-query").onclick=()=>dock("sql"); $("close-query").onclick=()=>{maximizeQuery(false); $("query-dock").hidden=true; $("show-query").focus();};
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
syncInspectorTools();
async function loadModel(id) {
  if (busy || saving || previewLibrary.isPending()) throw new Error("Wait for the current operation to finish.");
  if (dirty && !confirm("Discard unsaved model, layout, and Explore changes?")) return false;
  const ticket = ++catalogTicket; clearTimeout(timer); version++; showError();
  $("workbench").inert = true;
  let nextModel, nextCatalog;
  try {
    nextModel = await requestJson(`${API}/models/${encodeURIComponent(id)}`);
    nextCatalog = await requestJson(`${API}/catalog?connection_id=${encodeURIComponent(nextModel.connectionId)}&namespace=${encodeURIComponent(nextModel.namespace)}`,{timeoutMs:MODEL_EXECUTION_TIMEOUT_MS});
  } catch (error) { $("workbench").inert=!model; throw error; }
  if (ticket !== catalogTicket) return;
  model=nextModel; catalog=nextCatalog; draft=joinModel(model); initialLayout=undefined; selected=null; plan=null; diagnostics={}; conflicted=false;
  sourceAdditions=reconcileSourceCatalog(draft,catalog);
  if (columnEditor) disposeSelects(columnEditor.element);
  columnEditor=null; expandedFilterColumns.clear();
  $("model-name").disabled=false; $("model-name").value=model.name;
  $("reload-model").disabled=false; $("delete-model").disabled=false;
  $("target").textContent=`${catalog.database}.${catalog.namespace}`; $("limitations").textContent=catalog.notice;
  storageKey=catalog.workspaceId ? `schemoo-prototype-v2:${catalog.workspaceId}` : null;
  let saved; try { saved=storageKey && JSON.parse(localStorage.getItem(storageKey)); } catch { /* Optional prototype import, never silently replace. */ }
  $("import-browser").hidden=!saved?.draft?.nodes?.length;
  $("import-browser").onclick=()=>replaceDraft(()=>{const imported=structuredClone(saved.draft);initializeExposureFromPreview(imported);return imported;});
  $("example").hidden=!catalog.tables.some(t=>t.name==="org_hier");
  canvas?.destroy();
  canvas=createModelCanvas({host:$("canvas-host"),connectionButton:$("draw-connection"),catalog,getDraft:()=>draft,onChange:changed,
    onSelectNode:id=>{selected={node:id};inspectSelection();},
    onSelectEdge:id=>{selected={edge:id};inspectSelection();},
    onCreateConnection:edge=>{draft.edges.push(edge);selected={edge:edge.id};refreshForms();changed();inspectSelection();}});
  const url=new URL(location.href); url.searchParams.set("model",model.id); history.replaceState(null,"",url);
  $("results").replaceChildren(element("p", {className:"empty",text:"Run a read-only preview."})); $("result-status").textContent="Nothing run yet";
  refreshForms();canvas.render();initialLayout=splitDraft(draft).layout;changed();$("workbench").inert=false;
  void previewLibrary.load(model.id);
  void assistant.modelChanged();
  if (model.catalogFingerprint !== catalog.fingerprint && !sourceAdditionText()) showError("The source schema changed. Your model is preserved. Review the labeled source changes before saving. Reload never replaces your model with a fresh import.");
  return true;
}

async function saveModel() {
  if (!model || saving || busy || conflicted || previewLibrary.isPending()) return;
  if (!$("model-name").value.trim()) { showError("Give the model a name before saving."); $("model-name").focus(); return; }
  saving=true; $("workbench").inert=true; $("model-name").disabled=true; save(); showError();
  const parts=splitDraft(draft), differences=changedParts(model,draft,$("model-name").value.trim(),initialLayout);
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
    draft=joinModel(model); sourceAdditions={tables:[],columns:[],relationships:[],initialized:false}; refreshForms(); canvas.render({cycleEdges:diagnostics.cycleEdges || [],usedEdges:plan?.usedRelationships || [],sourceIssues:diagnostics.issues || diagnostics.sourceIssues || []});
    initialLayout=splitDraft(draft).layout;
    version++; await compile(version);
  } catch(error) {
    conflicted=error.status===409;
    showError(`${error.message} Your edits remain here. If another tab saved newer work, use Reload saved model to inspect it before trying again.`);
  } finally { saving=false; $("workbench").inert=false; $("model-name").disabled=false; save(); }
}

$("model-name").oninput=save;
$("save-model").onclick=saveModel;
function openLibrary() {
  return openModelLibrary(loadModel, {
    canDelete: () => !busy && !saving && !previewLibrary.isPending(),
    onDeleted: deleted => { if (deleted.id === model?.id) clearDeletedModel(); },
  });
}
function clearDeletedModel() {
  dirty=false; model=null; canvas?.destroy(); canvas=null; draft=null; clearTimeout(timer); version++;
  void previewLibrary.load(null);
  $("model-name").value=""; $("model-name").disabled=true; $("save-model").disabled=true; $("reload-model").disabled=true; $("delete-model").disabled=true; $("run").disabled=true; $("workbench").inert=true;
  $("target").textContent="Choose a saved model or create one"; $("draft-status").textContent="Model deleted";
  const url=new URL(location.href);url.searchParams.delete("model");history.replaceState(null,"",url);
  void assistant.modelChanged();
}
$("open-models").onclick=()=>{if(!busy&&!saving)openLibrary();};
$("close-model-library").onclick=()=>$("model-library").close();
$("reload-model").onclick=async()=>{try{await loadModel(model.id);}catch(error){showError(error.message);$("workbench").inert=false;}};
$("delete-model").onclick=async()=>{
  if(!model||busy||saving||previewLibrary.isPending())return;
  if(await confirmModelDeletion(model)) { clearDeletedModel(); await openLibrary(); }
};
window.addEventListener("beforeunload",event=>{if(dirty){event.preventDefault();event.returnValue="";}});
try {
  const id=new URL(location.href).searchParams.get("model");
  if(id) await loadModel(id); else await openLibrary();
}catch(error){showError(error.message);$("target").textContent="Model unavailable";$("workbench").inert=false;}
