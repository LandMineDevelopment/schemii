import { columnComparisonIssue } from "./column-comparisons.js";
import { element } from "#common/dom.js";
import { createIconButton } from "/assets/common/ui.js";
import { modelSelect, disposeSelects } from "./select.js";
import { conditionsEditor } from "./filter-controls.js";
import { conditionSources } from "./derived-conditions.js";
import { helpHeading } from "./help.js";
import { suggestSummaryConnection } from "./summary-connection.js";
import { summaryLookup } from "./summary-lookup.js";
import { validationKey, clearEditorValidation, showEditorValidation } from "./editor-validation.js";

const arithmetic = [["add", "Add (+)"], ["subtract", "Subtract (−)"], ["multiply", "Multiply (×)"], ["divide", "Divide (÷)"]];
const summaries = [["list", "Text list"], ["count", "Count non-null values"], ["count_distinct", "Count distinct values"], ["sum", "Sum"], ["avg", "Average"], ["min", "Minimum"], ["max", "Maximum"]];
const note = text => element("p", { className: "hint", text });
const labeled = (text, control) => element("label", { className: "stack" }, [text, control]);
const uid = prefix => `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0,12)}`;

/** A local, cancellable definition editor. SQL/type/grain validation belongs to the server. */
export function openDerivedSource({ draft, catalog, owner, existing, initial, onApply, onLoadDomain }) {
  const physical = draft.nodes.filter(node => !node.derivation);
  let source = existing?.derivation.source || initial?.derivation.source || owner?.id || physical[0]?.id;
  if (!source) return;
  const definition = structuredClone(existing?.derivation || initial?.derivation || { kind: "row", source, groupBy: [], outputs: [] });
  let target = definition.connection?.target || owner?.id || source;
  if (definition.kind === "aggregate" && !definition.connection) definition.connection = {target, columns: definition.groupBy.map(column => ({source:column,target:column}))};
  const columns = id => catalog.tables.find(t => t.name === physical.find(n => n.id === id)?.table)?.columns || [];
  const dialog = element("dialog", { className: "mf-dialog derived-dialog", attrs: { "aria-label": existing ? "Edit calculated source" : "Add calculated source" } });
  const body = element("div", { className: "derived-body" });
  const error = element("p", { attrs: { role: "alert" }, className: "mf-dialog-error" });
  const name = element("input", { attrs: { "aria-label": "Calculated source name", maxlength: 100 } });
  validationKey(name, "derived:name");
  name.value = existing?.label || initial?.label || `${physical.find(n => n.id === source)?.label} calculations`;
  const button = (text, fn) => { const b = element("button", { type: "button", className: "ui-button", text }); b.onclick = fn; return b; };
  const newOutput = () => ({ id: uid("field"), label: "", operation: definition.kind === "row" ? "add" : "list", column: "", nodeId: source, distinct: false, delimiter: ", " });
  const suggestConnection = () => {
    const mapping = suggestSummaryConnection(draft,catalog,source,target);
    definition.groupBy = mapping.map(pair=>pair.source);
    definition.connection = {target,columns:mapping};
  };
  if (!definition.outputs.length) definition.outputs.push(newOutput());
  function render() {
    clearEditorValidation(body, error);
    disposeSelects(body); body.replaceChildren();
    body.append(labeled("Name on the canvas", name), labeled("Calculation kind", modelSelect("Calculation kind", [["row", "Row calculation · same record"], ["aggregate", "Grouped summary · one row per group"]], definition.kind, kind => {
      definition.kind = kind; definition.outputs = []; definition.outputs.push(newOutput()); definition.groupBy = [];
      if (kind === "aggregate") suggestConnection(); else delete definition.connection;
      render();
    })), helpHeading(definition.kind === "aggregate" ? "1. Source and grouping" : "Source", "summarySetup"), labeled(definition.kind === "aggregate" ? "Summarize records from" : "Calculate using", modelSelect(definition.kind === "aggregate" ? "Summarize records from" : "Calculate using", physical.map(n => [n.id,n.label]), source, id => {
      source = id; definition.source = id; definition.groupBy = []; definition.outputs = [newOutput()];
      if (definition.kind === "aggregate") suggestConnection();
      render();
    })));
    body.append(note(definition.kind === "row" ? "Calculated directly in SELECT. No extra table, join, or stored data. Missing inputs and division by zero produce NULL." : "Choose the records to summarize, then the columns that define each group. This summary is calculated before connecting it to the report. No database object is created."));
    if (definition.kind === "aggregate") {
      const keys = validationKey(element("fieldset", { className: "derived-keys" }, [element("legend", { text: "One summary row per · grouping columns" })]), "derived:grouping");
      for (const column of columns(source)) {
        const input = element("input", { attrs: { type: "checkbox" } }); input.checked = definition.groupBy.includes(column.name);
        input.onchange = () => {
          definition.groupBy = input.checked ? [...definition.groupBy,column.name] : definition.groupBy.filter(n => n !== column.name);
          const previous = definition.connection.columns;
          definition.connection.columns = definition.groupBy.map(key=>previous.find(pair=>pair.source===key) || {source:key,target:source===target ? key : ""});
          render();
        };
        keys.append(element("label", {}, [input, column.name]));
      }
      body.append(keys, note("For certifications per employee, choose personnel_id—not the certification record's own id."), helpHeading("2. Calculated fields", "calculatedConditions"));
    }
    for (const [index, output] of definition.outputs.entries()) {
      const card = element("section", { className: "derived-output" });
      const remove = createIconButton({ icon: "delete", label: `Remove calculated field ${index + 1}`, className: "ui-button" });
      remove.disabled = definition.outputs.length === 1;
      remove.onclick = () => { definition.outputs.splice(index,1); render(); };
      const title = element("input", { attrs: { "aria-label": `Field ${index+1} name`, placeholder: "e.g. Certification names", maxlength: 100 } }); title.value = output.label;
      validationKey(title, `derived:output:${index}:name`);
      title.oninput = () => { output.label = title.value; };
      card.append(element("header", {}, [element("strong", { text: `Field ${index+1}` }), remove]), labeled("Field name", title), labeled("Operation", modelSelect(`Field ${index+1} operation`, definition.kind === "row" ? arithmetic : summaries, output.operation, value => { output.operation = value; render(); })));
      if (definition.kind === "aggregate") {
        card.append(labeled("Values from object", modelSelect(`Field ${index+1} source`, physical.map(n => [n.id,n.label]), output.nodeId || source, id => { output.nodeId=id; output.column=""; render(); })));
        const lookup=summaryLookup(draft,catalog,source,output.nodeId || source);
        card.append(element("section",{className:`derived-lookup${["source","many_to_one"].includes(lookup.status)?"":" derived-lookup--warning"}`,attrs:{"aria-label":`Field ${index+1} lookup`,role:"status"}},[
          helpHeading(lookup.title,"summaryLookup"),
          ...lookup.path.map(text=>element("div",{className:"derived-lookup-path",text})),
          note(lookup.message),
        ]));
      }
      const numeric = definition.kind === "row" || ["sum","avg"].includes(output.operation);
      const choices = columns(output.nodeId || source).filter(c => !numeric || /^(smallint|integer|bigint|numeric|decimal|real|double precision)/.test(c.dataType)).map(c => [c.name,`${c.name} · ${c.dataType}`]);
      card.append(labeled("Source column", validationKey(modelSelect(`Field ${index+1} column`, choices, output.column, value => { output.column=value; }), `derived:output:${index}:column`)));
      if (definition.kind === "row") card.append(labeled("Second column", validationKey(modelSelect(`Field ${index+1} second column`, choices, output.operand || "", value => { output.operand=value; }), `derived:output:${index}:operand`)));
      if (output.operation === "list") {
        const delimiter = element("input", { attrs: { "aria-label": `Field ${index+1} separator`, maxlength: 20 } }); delimiter.value = output.delimiter;
        delimiter.oninput = () => { output.delimiter=delimiter.value; };
        const distinct = element("input", { attrs: { type: "checkbox" } }); distinct.checked=output.distinct;
        distinct.onchange = () => { output.distinct=distinct.checked; };
        card.append(labeled("Separator",delimiter),element("label",{},[distinct,"Unique values only"]),note("Removes repeated display values, not duplicate source records. Different IDs with the same displayed value appear once when enabled; this does not make an unsafe join safe."));
      }
      const conditions = element("section", { className: "derived-conditions", attrs: { "aria-label": `Field ${index+1} conditions` } });
      const renderConditions = () => {
        disposeSelects(conditions); conditions.replaceChildren(helpHeading("Conditions (optional)", "calculatedConditions"), note(definition.kind === "aggregate"
          ? "Only matching records contribute to this field. Other fields and the connected report row remain unchanged. All conditions below must match."
          : "Calculate this field only when all conditions match; otherwise return NULL. The source row remains in the report."));
        conditions.append(conditionsEditor(output.conditions ||= [], {draft,catalog,onChange:()=>clearEditorValidation(body,error),refresh:renderConditions,prefix:`Field ${index+1}`,validationPrefix:`derived:output:${index}:condition`,allowedSources:conditionSources(draft,definition,output),defaultSource:output.nodeId || source,allowToday:true,onLoadDomain}));
      };
      renderConditions(); card.append(conditions);
      body.append(card);
    }
    body.append(button("Add calculated field", () => { definition.outputs.push(newOutput()); render(); }));
    if (definition.kind === "aggregate") {
      const connection = element("section", {className:"derived-output"}, [helpHeading("3. Model connection", "summarySetup"),
        labeled("Connect summary to",modelSelect("Connect summary to",physical.map(n=>[n.id,n.label]),target,id=>{
          target=id; definition.connection.target=id;
          const suggested=suggestSummaryConnection(draft,catalog,source,target);
          definition.connection.columns=definition.groupBy.map(key=>suggested.find(pair=>pair.source===key) || {source:key,target:""});render();
        })), note("Map every grouping column to a unique record in the connected table. Existing foreign keys prefill the mapping when unambiguous; you can change it.")]);
      for (const pair of definition.connection.columns) connection.append(labeled(`${pair.source} →`,validationKey(modelSelect(`Connect ${pair.source} to column`,columns(target).map(c=>[c.name,`${c.name} · ${c.dataType}`]),pair.target,column=>{pair.target=column;updateSummary();}), `derived:mapping:${pair.source}`)));
      const summary=note("");summary.setAttribute("role","status");
      function updateSummary(){
        const label=id=>physical.find(n=>n.id===id)?.label || id;
        summary.textContent=`One row per ${definition.groupBy.join(" + ") || "(choose grouping columns)"} from ${label(source)}. Connects to ${label(target)} using ${definition.connection.columns.map(p=>`${p.source} → ${p.target || "(choose column)"}`).join(", ") || "(choose mapping)"}. Missing groups return 0 for counts and NULL for other summaries.`;
      }
      updateSummary();connection.append(summary,note("Source-only fields need no lookup joins. Related names or conditions may require a lookup before grouping. Model filters still apply."));body.append(connection);
    }
  }
  body.addEventListener("input", () => clearEditorValidation(body,error));
  body.addEventListener("change", () => clearEditorValidation(body,error));
  const apply = button("Apply to model", () => {
    const issues=[];
    const add=(key,message)=>issues.push({key,message});
    if (!name.value.trim()) add("derived:name", "Give the calculated source a name.");
    if (definition.kind === "aggregate" && !definition.groupBy.length) add("derived:grouping", "Choose at least one grouping column.");
    for (const [index,output] of definition.outputs.entries()) {
      if (!output.label.trim()) add(`derived:output:${index}:name`, `Give field ${index+1} a name.`);
      const sourceColumns=columns(output.nodeId || source);
      if (!sourceColumns.some(column=>column.name===output.column)) add(`derived:output:${index}:column`, `Choose a valid source column for field ${index+1}.`);
      if (definition.kind === "row" && !sourceColumns.some(column=>column.name===output.operand)) add(`derived:output:${index}:operand`, `Choose a valid second column for field ${index+1}.`);
    }
    if(definition.kind === "aggregate") for(const pair of definition.connection.columns) if(!columns(target).some(column=>column.name===pair.target)) add(`derived:mapping:${pair.source}`,`Choose a destination column for ${pair.source}.`);
    for(const [index,output] of definition.outputs.entries()) for(const [conditionIndex,condition] of (output.conditions || []).entries()) {
      const key=`derived:output:${index}:condition:${conditionIndex}`;
      const comparisonIssue = columnComparisonIssue(condition, draft, catalog);
      if (comparisonIssue) add(`${key}:comparison`, comparisonIssue);
      const allowed=conditionSources(draft,definition,output);
      if(!allowed.has(condition.table) || !columns(condition.table).some(c=>c.name===condition.column)) add(`${key}:field`, `Choose a valid condition column for field ${index+1}.`);
      if(["in","not_in"].includes(condition.operator) && (!Array.isArray(condition.value) || !condition.value.length)) add(`${key}:value`, `Choose at least one value for field ${index+1}'s list condition.`);
      if(!["is_null","not_null"].includes(condition.operator) && condition.valueSource!=="today" && condition.compareColumn == null && condition.value == null) add(`${key}:value`, `Choose a comparison value for field ${index+1}'s condition.`);
    }
    if (showEditorValidation(body,error,issues)) return;
    const parent = physical.find(n => n.id === (definition.kind === "aggregate" ? target : source));
    const savedDefinition=structuredClone(definition);
    // Editing a label/condition must not change the grain of an existing
    // owner-rooted summary. A new source or mapping explicitly opts it in.
    if(existing?.derivation.kind === "aggregate" && !existing.derivation.connection && source===existing.derivation.source && target===source &&
      JSON.stringify(definition.groupBy)===JSON.stringify(existing.derivation.groupBy) && definition.connection?.columns.every(pair=>pair.source===pair.target)) delete savedDefinition.connection;
    const node = { ...(existing || { id:uid("derived"), x:(parent.x || 0)+340, y:parent.y || 0 }), table:physical.find(n=>n.id===source).table, label:name.value.trim(), derivation:savedDefinition };
    onApply(node); dialog.close();
  }); apply.classList.add("primary");
  dialog.append(element("header", { className: "mf-dialog-header" }, [element("div",{},[element("small",{text:"VIRTUAL MODEL SOURCE"}),element("h2",{text:existing ? "Edit calculated source" : "Add calculated source"})]),button("Cancel",()=>dialog.close())]),body,element("footer",{className:"mf-dialog-footer"},[element("div",{},[error,note("Applies to your draft. Save model to keep it between sessions.")]),apply]));
  dialog.addEventListener("close",()=>{disposeSelects(body);dialog.remove();},{once:true});
  render(); document.body.append(dialog); dialog.showModal();
}
