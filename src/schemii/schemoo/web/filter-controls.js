import { element } from "#common/dom.js";
import { createIconButton } from "/assets/common/ui.js";
import { helpButton, helpHeading } from "./help.js";
import { modelSelect, domainSelect, disposeSelects } from "./select.js";
import { exposedFields } from "./model-state.js";

const id = () => crypto.randomUUID();
const operators = [["eq", "Equals"], ["ne", "Does not equal"], ["in", "Is one of (IN)"], ["not_in", "Is not one of (NOT IN)"], ["gt", ">"], ["gte", "≥"], ["lt", "<"], ["lte", "≤"], ["contains", "Contains"], ["is_null", "Is null"], ["not_null", "Is not null"]];
const isList = operator => ["in", "not_in"].includes(operator);
const listParameter = (alternative, parameter) => alternative.conditions.some(c => c.parameterId === parameter.id && isList(c.operator));
const asList = value => Array.isArray(value) ? value : value == null || value === "" ? [] : [value];
const asSingle = value => Array.isArray(value) ? (value.length === 1 ? value[0] : "") : value;
function literalControl(label, value, callback, multiple, placeholder) {
  if (!multiple) return input(label, value, callback, { placeholder });
  const control = element("textarea", { attrs: { "aria-label": label, rows: 3, placeholder: "One value per line" } });
  control.value = Array.isArray(value) ? value.join("\n") : value ?? "";
  control.oninput = () => callback(control.value.split("\n").filter(v => v !== ""));
  return control;
}
function sourceDomain(condition, draft) {
  const node = draft.nodes.find(n => n.id === condition.table);
  return { nodeId: condition.table, table: node?.table || "", column: condition.column, labelColumn: "" };
}
const note = text => element("p", { className: "mf-help", text });
function action(icon, label, callback) {
  const button = createIconButton({ icon, label, className: "ui-button icon-only" });
  button.addEventListener("click", callback);
  return button;
}
function input(label, value, callback, attrs = {}) {
  const control = element("input", { attrs: { "aria-label": label, maxlength: 500, ...attrs } });
  control.value = value ?? "";
  control.addEventListener("input", () => callback(control.value));
  return control;
}
function select(label, choices, value, callback) {
  return modelSelect(label, choices, value, callback);
}
function labeled(label, control, topic) {
  const wrapper = element("div", { className: "mf-label" });
  const caption = element("label", { text: label });
  const focusable = control.querySelector("input") || control;
  focusable.id ||= `mf-${id()}`; caption.htmlFor = focusable.id;
  wrapper.append(element("div", { className: "mf-caption" }, [caption, ...(topic ? [helpButton(topic)] : [])]), control);
  return wrapper;
}
function section(title, children = [], open = true) {
  const node = element("details", { className: "mf-section", attrs: open ? { open: "" } : {} });
  node.append(element("summary", { text: title }), ...children);
  return node;
}
function fields(draft, catalog) {
  return draft.nodes.filter(node => !node.derivation).flatMap(node => (catalog.tables.find(t => t.name === node.table)?.columns || []).map(column => ({
    table: node.id, column: column.name, label: `${node.label || node.table} · ${column.name}`,
  })));
}
function newCondition(draft, catalog) {
  const first = fields(draft, catalog)[0];
  return { table: first?.table || "", column: first?.column || "", operator: "eq", value: "" };
}
function domainControls(domain, { draft, catalog, prefix, refresh }) {
  const host = element("div", { className: "mf-wide" });
  const sources = draft.nodes.filter(n => !n.derivation).map(n => ({ id: n.id, table: n.table, label: n.label || n.table }));
  const choices = sources.flatMap(n => (catalog.tables.find(t => t.name === n.table)?.columns || []).map(c => [JSON.stringify([n.id,n.table,c.name]), `${n.label} · ${c.name}`]));
  for (const t of catalog.tables.filter(t => !sources.some(n => n.id === t.name && n.table === t.name))) for (const c of t.columns) choices.push([JSON.stringify([null,t.name,c.name]), `${t.name} · ${c.name} (source table)`]);
  const selectedNode = domain.nodeId ?? sources.find(n => n.id === domain.table && n.table === domain.table)?.id ?? null;
  host.append(labeled("Domain value column", select(`${prefix} domain value column`, choices, JSON.stringify([selectedNode,domain.table,domain.column]), value => {
    const [nodeId, table, column] = JSON.parse(value);
    domain.nodeId = nodeId; domain.table = table; domain.column = column; domain.labelColumn = ""; refresh();
  }), "parameters"));
  const physical = domain.nodeId != null ? draft.nodes.find(n => n.id === domain.nodeId)?.table : domain.table;
  const labelChoices = (catalog.tables.find(t => t.name === physical)?.columns || []).map(c => [c.name,c.name]);
  host.append(labeled("Display label (optional)", select(`${prefix} domain label column`, [["", "Show the value"], ...labelChoices], domain.labelColumn || "", value => { domain.labelColumn=value; refresh(); })));
  return host;
}

export function conditionsEditor(conditions, { draft, catalog, onChange, refresh, inputs = [], prefix, onLoadDomain, exposedOnly = false, allowedSources = null, defaultSource = null, allowToday = false, fixedField = null, singleCondition = false, compact = false }) {
  const allowed = new Set(exposedFields(draft, catalog).map(f => JSON.stringify([f.table, f.column])));
  const choices = fields(draft, catalog).filter(f => (!allowedSources || allowedSources.has(f.table)) && (!exposedOnly || allowed.has(JSON.stringify([f.table, f.column]))));
  const body = element("div", { className: "mf-conditions" });
  conditions.forEach((condition, index) => {
    if (condition.domain) condition.domain = sourceDomain(condition, draft);
    const row = element("div", { className: "mf-condition" });
    const fieldValue = JSON.stringify([condition.table, condition.column]);
    const preselected = !condition.column && draft.nodes.find(n=>n.id===condition.table);
    const fieldChoices=preselected ? [...choices.filter(f=>f.table===condition.table),...choices.filter(f=>f.table!==condition.table)] : [...choices];
    const unavailable = condition.column && !choices.some(f => f.table === condition.table && f.column === condition.column);
    if (unavailable) fieldChoices.unshift({ table: condition.table, column: condition.column,
      label: `${draft.nodes.find(n => n.id === condition.table)?.label || condition.table} · ${condition.column} (unavailable)` });
    const field = fixedField ? element("span", {text: `${draft.nodes.find(n => n.id === fixedField.table)?.label || fixedField.table}.${fixedField.column}`}) : select(`${prefix} condition ${index + 1} field`, fieldChoices.map(f => [JSON.stringify([f.table, f.column]), f.label]), fieldValue, value => {
      [condition.table, condition.column] = JSON.parse(value);
      if (allowToday) { delete condition.valueSource; delete condition.value; delete condition.domain; }
      if (condition.domain) { condition.domain = sourceDomain(condition, draft); condition.value = isList(condition.operator) ? [] : null; }
      refresh();
    });
    if (!fixedField) field.querySelector("input").placeholder = preselected ? `Choose a column from ${preselected.label}` : "Search source or column";
    const op = select(`${prefix} condition ${index + 1} operator`, operators, condition.operator, value => {
      if (isList(value) !== isList(condition.operator)) {
        condition.value = isList(value) ? (condition.value != null && condition.value !== "" ? [condition.value] : []) : null;
        const parameter = inputs.find(p => p.id === condition.parameterId);
        if (parameter) parameter.defaultValue = isList(value) ? asList(parameter.defaultValue) : asSingle(parameter.defaultValue);
      }
      condition.operator = value;
      if (["is_null", "not_null"].includes(value)) {
        delete condition.parameterId; delete condition.value; delete condition.allowNull; delete condition.domain; delete condition.valueSource;
      }
      if (allowToday && (isList(value) || value === "contains")) delete condition.valueSource;
      refresh();
    });
    if (!compact) row.append(element("span", { className: "mf-and", text: index ? "AND · also require" : "Condition" }));
    if (!compact || !fixedField) row.append(labeled("Source · column", field));
    if (unavailable) row.append(element("p", { className: "warning mf-wide", text: `${condition.column} is no longer available here. Choose a replacement or explicitly remove this condition; it has not been dropped from the query.`, attrs: { role: "alert" } }));
    row.append(labeled("Comparison", op));
    if (!singleCondition) row.append(action("close", `Remove ${prefix} condition ${index + 1}`, () => { conditions.splice(index, 1); refresh(); }));
    if (!["is_null", "not_null"].includes(condition.operator)) {
      const binding = inputs.length ? select(`${prefix} condition ${index + 1} value source`, [["", "Literal value"], ...inputs.map(p => [p.id, p.label || "Unnamed parameter"])], condition.parameterId || "", value => {
        if (value) { condition.parameterId = value; delete condition.value; delete condition.domain; }
        else { delete condition.parameterId; condition.value = isList(condition.operator) ? [] : ""; }
        refresh();
      }) : null;
      if (binding) { const valueSource = labeled("Compare against", binding, "bindings"); valueSource.classList.add("mf-wide"); row.append(valueSource); }
      const source = draft.nodes.find(n => n.id === condition.table);
      const dataType = catalog.tables.find(t => t.name === source?.table)?.columns.find(c => c.name === condition.column)?.dataType || "";
      if (allowToday && /^(date|timestamp)/.test(dataType) && !isList(condition.operator) && condition.operator !== "contains") {
        const mode = labeled("Compare against", select(`${prefix} condition ${index + 1} value source`, [["literal", "Fixed value"], ["today", "Today (UTC) · evaluated when run"]], condition.valueSource || "literal", value => {
          condition.valueSource = value; delete condition.value; delete condition.domain; refresh();
        })); mode.classList.add("mf-wide"); row.append(mode);
      }
      if (allowToday && condition.valueSource === "today") row.append(element("p", { className: "mf-help mf-wide", text: "The server uses today's UTC date for this query. It is recalculated on the next run; no date is frozen into the saved model." }));
      else if (!condition.parameterId) {
        const label = `${prefix} condition ${index + 1} value`;
        const multiple = isList(condition.operator);
        const literalInput = condition.domain && onLoadDomain
          ? domainSelect({ label, multiple, value: condition.value, loadOptions: search => onLoadDomain({ draft, domain: sourceDomain(condition, draft), search }), onChange: value => { condition.value = value; onChange(); } })
          : literalControl(label, condition.value, value => { condition.value = value; onChange(); }, multiple, "Enter a fixed value");
        const literal = labeled(multiple ? "Fixed values" : "Fixed value", literalInput); literal.classList.add("mf-wide"); row.append(literal);
        if (onLoadDomain) {
          const toggle = element("input", { attrs: { type: "checkbox", "aria-label": `${prefix} condition ${index + 1} choose fixed value from domain` } });
          toggle.checked = !!condition.domain;
          toggle.onchange = () => {
            if (toggle.checked) { condition.domain = sourceDomain(condition, draft); condition.value = multiple ? [] : null; }
            else delete condition.domain;
            refresh();
          };
          row.append(element("label", { className: "mf-checkbox" }, [toggle, "Choose fixed value from a domain list"]));
          if (condition.domain && !compact) {
            row.append(element("p", { className: "mf-help mf-wide", text: `Choose ${multiple ? "one or more values" : "a value"} directly from this source column. Search checks the source, not the filtered result. NULL is handled separately below.` }));
          }
        }
        if (multiple && !condition.domain) row.append(element("p", { className: "mf-help mf-wide", text: "Enter one value per line. Commas remain part of a value." }));
      }
      else if (!compact) row.append(element("p", { className: "mf-binding-summary mf-wide", text: `Bound to parameter: ${inputs.find(p => p.id === condition.parameterId)?.label || "Missing parameter"}. Its Explore value will be inserted here.` }));
      const nullable = element("input", { attrs: { type: "checkbox", "aria-label": `${prefix} condition ${index + 1} also accepts null` } });
      nullable.checked = Boolean(condition.allowNull);
      nullable.addEventListener("change", () => { condition.allowNull = nullable.checked; onChange(); });
      row.append(element("label", { className: "mf-checkbox" }, [nullable, compact ? "Include NULL values" : "Include rows where this source column is NULL"]));
      if (!compact) row.append(element("p", { className: "mf-help mf-wide", text: allowToday ? "Enable this only if NULL means the condition is satisfied—for example, no expiration date means it never expires." : "This includes missing database values. It does not make the report input optional; choose a value or set a default." }));
    }
    body.append(row);
  });
  if (!singleCondition) body.append(element("div", { className: "mf-add" }, [action("add", `Add ${prefix} condition`, () => {
    const first = choices.find(f => f.table === defaultSource) || choices[0];
    conditions.push({table:first?.table || "",column:first?.column || "",operator:"eq",value:""}); refresh();
  }), "Add AND condition"]));
  return body;
}

/** Model-author controls. Mutates the shared draft; structural changes redraw only this host. */
export function renderFilterDefinition(host, options) {
  const { draft, catalog, onChange } = options;
  draft.scopes ||= [];
  draft.selections ||= {};
  const refresh = () => { renderFilterDefinition(host, options); onChange({ structure: true }); };
  disposeSelects(host);
  host.replaceChildren();
  for (const scope of draft.scopes) {
    const content = element("div", { className: "mf-content" });
    content.append(element("div", { className: "mf-heading" }, [
      labeled("Name", input(`Scope ${scope.id} name`, scope.label, value => { scope.label = value; onChange(); })),
    ]));
    content.append(labeled("Requirement", select(`Requirement for ${scope.label}`, [["required", "Required model scope"], ["conditional", "Conditional model parameter"]], scope.kind, value => { scope.kind = value; refresh(); }), "scopes"));
    content.append(note(scope.kind === "required" ? "Applies to every query. Schemoo includes the related source even when none of its columns are selected." : "Applies only when a query uses or traverses a bound source. It does not force that source into the query."));
    for (const [alternativeIndex, alternative] of scope.alternatives.entries()) {
      const prefix = `${scope.label || "Scope"} / ${alternative.label || "Alternative"}`;
      const alternativeBody = element("div", { className: "mf-content" });
      if (scope.alternatives.length > 1) alternativeBody.append(element("div", { className: "mf-heading" }, [
        labeled("Option name", input(`${prefix} name`, alternative.label, value => { alternative.label = value; onChange(); }), "alternatives"),
        action("delete", `Delete alternative ${prefix}`, () => {
          scope.alternatives.splice(alternativeIndex, 1);
          if (draft.selections[scope.id]?.alternativeId === alternative.id) delete draft.selections[scope.id];
          refresh();
        }),
      ]));
      const inputBody = element("div", { className: "mf-inputs" });
      inputBody.append(helpHeading("Report inputs", "parameters"), note("Values supplied by the report user. Bind each input to the source columns it filters."));
      for (const parameter of alternative.inputs) {
        const parameterRow = element("div", { className: "mf-input-definition" });
        const multiple = listParameter(alternative, parameter);
        const bound = alternative.conditions.filter(c => c.parameterId === parameter.id);
        const firstBinding = alternative.conditions.find(c => c.parameterId === parameter.id && c.column);
        const defaultDomain = parameter.domain || (parameter.type === "source" && firstBinding ? sourceDomain(firstBinding, draft) : null);
        const defaultControl = defaultDomain && options.onLoadDomain
          ? domainSelect({ label: `${prefix} input ${parameter.id} default`, value: parameter.defaultValue === "" ? null : parameter.defaultValue, multiple,
            loadOptions: search => options.onLoadDomain({ draft, domain: defaultDomain, search }),
            onChange: value => { parameter.defaultValue = value; onChange(); } })
          : literalControl(`${prefix} input ${parameter.id} default`, parameter.defaultValue, value => { parameter.defaultValue = value; onChange(); }, multiple, parameter.type === "date" ? "YYYY-MM-DD, today, or today - 365" : "Required at run time if blank");
        parameterRow.append(labeled("Input name", input(`${prefix} input ${parameter.id} name`, parameter.label, value => { parameter.label = value; onChange(); })),
          labeled("Type", select(`${prefix} input ${parameter.id} type`, [["source", "Choose from source"], ["text", "Text"], ["uuid", "UUID"], ["integer", "Integer (whole number)"], ["number", "Number (decimal)"], ["boolean", "Boolean (true / false)"], ["date", "Date"]], parameter.type, value => { parameter.type = value; refresh(); })),
          labeled(multiple ? "Default values (optional)" : "Default (optional)", defaultControl, "parameters"),
          action("delete", `Delete input ${parameter.label}`, () => {
            alternative.inputs = alternative.inputs.filter(x => x !== parameter);
            for (const condition of alternative.conditions) if (condition.parameterId === parameter.id) { delete condition.parameterId; condition.value = ""; }
            refresh();
          }));
        const multiToggle = element("input", { attrs: { type: "checkbox", "aria-label": `Allow multiple values for ${parameter.label}` } });
        multiToggle.checked = multiple;
        multiToggle.disabled = !bound.length || bound.some(c => !["eq", "ne", "in", "not_in"].includes(c.operator));
        multiToggle.onchange = () => {
          for (const condition of bound) condition.operator = multiToggle.checked
            ? (["ne", "not_in"].includes(condition.operator) ? "not_in" : "in")
            : (["ne", "not_in"].includes(condition.operator) ? "ne" : "eq");
          parameter.defaultValue = multiToggle.checked ? asList(parameter.defaultValue) : asSingle(parameter.defaultValue);
          const selectedValues = draft.selections?.[scope.id]?.values;
          if (selectedValues && parameter.id in selectedValues) selectedValues[parameter.id] = multiToggle.checked ? asList(selectedValues[parameter.id]) : asSingle(selectedValues[parameter.id]);
          refresh();
        };
        const defaultsLabel = defaultControl.closest(".mf-label");
        parameterRow.insertBefore(element("label", { className: "mf-checkbox" }, [multiToggle, "Allow multiple values"]), defaultsLabel);
        parameterRow.insertBefore(note(multiToggle.disabled ? "Bind a source using Equals, Does not equal, IN or NOT IN to enable multiple values. Range comparisons accept one value per input." : "Uses IN / NOT IN for every bound source. Defaults and report selections use the same single- or multi-value mode. Switching to single clears selections containing more than one value."), defaultsLabel);
        if (multiple) parameterRow.append(note("This input accepts multiple values because it is bound with IN / NOT IN. All its source bindings must accept lists."));
        const domainToggle = element("input", { attrs: { type: "checkbox", "aria-label": `Use searchable domain values for ${parameter.label}` } });
        domainToggle.checked = !!parameter.domain;
        domainToggle.onchange = () => {
          if (domainToggle.checked) {
            const binding = bound.find(c => c.column && draft.nodes.some(n => n.id === c.table));
            const node = draft.nodes.find(n => n.id === binding?.table);
            parameter.domain = { ...(node ? { nodeId: node.id } : {}), table: node?.table || "", column: binding?.column || "", labelColumn: "" };
            if (parameter.type === "source") parameter.type = "text";
          } else delete parameter.domain;
          refresh();
        };
        parameterRow.append(labeled("Use searchable domain values", domainToggle, "parameters"));
        if (parameter.domain) {
          parameterRow.append(domainControls(parameter.domain, { draft, catalog, prefix: `${prefix} input ${parameter.id}`, refresh }));
          parameterRow.append(note(`Report users search this list and select ${multiple ? "one or more values" : "one value"}. The same list is used for defaults. A display label can show a readable name while the filter receives the ID.`));
        }
        if (parameter.type === "source") parameterRow.append(note("Users choose a distinct value from the first bound source column. For a required parent organization, use Required scope and bind org_hier.parent_id with Equals. Leave the default blank to require a choice."));
        parameterRow.append(note(bound.length ? `Bound to ${bound.length} condition${bound.length === 1 ? "" : "s"}: ${bound.map(c => c.table ? `${draft.nodes.find(n => n.id === c.table)?.label || c.table}.${c.column}` : "Awaiting source selection").join(", ")}` : "Not bound to a source yet. Use Bind source below."));
        const bind = element("button", { className: "ui-button", text: "Bind source", attrs: { type: "button", "aria-label": `Bind source to ${parameter.label}`, "data-ui-tooltip": "Add a source-column condition that uses this parameter's value." } });
        bind.onclick = () => {
          alternative.conditions.push({ table: "", column: "", operator: multiple ? "in" : "eq", parameterId: parameter.id });
          refresh();
          const controls = [...host.querySelectorAll('[role="combobox"]')];
          const target = controls.find(c => c.getAttribute("aria-label") === `${prefix} condition ${alternative.conditions.length} field`);
          target?.scrollIntoView({ block: "nearest" }); target?.focus({ preventScroll: true });
        };
        parameterRow.append(bind);
        inputBody.append(parameterRow);
      }
      inputBody.append(element("div", { className: "mf-add" }, [action("add", `Add input to ${prefix}`, () => {
        alternative.inputs.push({ id: id(), label: "Parameter", type: "text", defaultValue: "" }); refresh();
      }), "Add parameter input"]));
      if (alternative.inputs.length) alternativeBody.append(inputBody);
      alternativeBody.append(helpHeading("Source conditions", "bindings"), note("All conditions below must match. Null checks need no value; other comparisons use a fixed value or a report input."), conditionsEditor(alternative.conditions, { draft, catalog, onChange, refresh, inputs: alternative.inputs, prefix, onLoadDomain: options.onLoadDomain }));
      if (!alternative.inputs.length) alternativeBody.append(section("Add report inputs (optional)", [inputBody], false));
      content.append(scope.alternatives.length > 1 ? section(`${alternativeIndex ? "OR · " : ""}${alternative.label || "Alternative"}`, [alternativeBody]) : alternativeBody);
    }
    content.append(section("Offer alternative choices (optional)", [helpHeading("Alternative choices", "alternatives"), note("Let the report user choose one set of rules, such as As of date OR All history."), element("div", { className: "mf-add" }, [action("add", `Add alternative to ${scope.label}`, () => {
      scope.alternatives.push({ id: id(), label: "Alternative", inputs: [], conditions: [] }); refresh();
    }), "Add OR alternative"])], scope.alternatives.length > 1));
    host.append(content);
  }
}

/** Report input controls; defaults remain model defaults rather than copied overrides. */
export function renderParameterValues(host, options) {
  const { draft, onChange, activeScopes } = options;
  // Compilation updates activity while a user types. Do not replace the focused input.
  if (!options.force && host.contains(document.activeElement)) {
    for (const block of host.querySelectorAll("[data-scope-id]")) {
      const scope = draft.scopes.find(s => s.id === block.dataset.scopeId);
      if (!scope) continue;
      const active = scope.kind === "required" || activeScopes?.includes(scope.id);
      block.classList.toggle("active", Boolean(active));
      block.querySelector(".mf-scope-status").textContent = scopeStatus(scope, active, activeScopes);
    }
    return;
  }
  draft.selections ||= {};
  disposeSelects(host);
  host.replaceChildren(helpHeading("Model parameter values", "parameters"));
  if (!draft.scopes?.length) { host.append(note("No model parameters configured. Add them in Model filters.")); return; }
  for (const scope of draft.scopes) {
    const active = scope.kind === "required" || activeScopes?.includes(scope.id);
    const status = scopeStatus(scope, active, activeScopes);
    const selection = draft.selections[scope.id] || { alternativeId: scope.alternatives[0]?.id || "", values: {} };
    const alternative = scope.alternatives.find(a => a.id === selection.alternativeId) || scope.alternatives[0];
    const block = element("div", { className: `mf-parameter-block${active ? " active" : ""}`, dataset: { scopeId: scope.id } });
    block.append(element("strong", { text: scope.label }), helpButton(scope.kind), element("p", { className: "mf-help mf-scope-status", text: status }));
    if (scope.alternatives.length > 1) {
      const choose = select(`Alternative for ${scope.label}`, scope.alternatives.map(a => [a.id, a.label]), alternative?.id, value => {
      draft.selections[scope.id] = { ...(draft.selections[scope.id] || selection), alternativeId: value };
      renderParameterValues(host, { ...options, force: true }); onChange();
    });
      block.append(labeled("Choose one", choose, "alternatives"));
    }
    if (alternative && !alternative.inputs.length) {
      block.append(note(alternative.conditions.length ? "Fixed model rule · no input required" : "No restriction · no input required"));
      for (const condition of alternative.conditions) {
        const source = draft.nodes.find(node => node.id === condition.table);
        const comparison = operators.find(([value]) => value === condition.operator)?.[1] || condition.operator;
        block.append(note(`${source?.label || condition.table}.${condition.column} · ${comparison}${["is_null", "not_null"].includes(condition.operator) ? "" : ` · ${condition.value ?? ""}`}`));
      }
    }
    for (const parameter of alternative?.inputs || []) {
      const defaultValue = parameter.defaultValue ?? "";
      const value = selection.values?.[parameter.id] ?? "";
      const commit = entered => {
        draft.selections[scope.id] = { alternativeId: alternative.id, values: { ...selection.values, ...draft.selections[scope.id]?.values, [parameter.id]: entered } };
        onChange();
      };
      const domainChoice = !!parameter.domain || parameter.type === "source";
      const multiple = listParameter(alternative, parameter);
      const effectiveValue = value === "" || (Array.isArray(value) && !value.length) ? defaultValue : value;
      const field = domainChoice && options.onLoadDomain
        ? domainSelect({ label: `${scope.label}: ${parameter.label}`, value: effectiveValue === "" ? null : effectiveValue, multiple,
          placeholder: "Search source values",
          loadOptions: search => options.onLoadDomain({ scope, alternative, input: parameter, search }), onChange: commit })
        : parameter.type === "boolean" && !multiple
        ? select(`${scope.label}: ${parameter.label}`, [["", "Choose true or false"], ["true", "True"], ["false", "False"]], String(value), commit)
        : literalControl(`${scope.label}: ${parameter.label}`, value, commit, multiple,
          defaultValue !== "" ? `Default: ${defaultValue}` : parameter.type === "date" ? "YYYY-MM-DD, today, or today - 365" : "Required when active");
      const fieldRow = element("div", { className: "mf-heading" }, [labeled(parameter.label, field)]);
      if (domainChoice) fieldRow.append(action("close", `Clear ${parameter.label}`, () => { commit(""); renderParameterValues(host, { ...options, force: true }); }));
      block.append(fieldRow);
      if (domainChoice && defaultValue !== "" && defaultValue != null && (!Array.isArray(defaultValue) || defaultValue.length)) block.append(note(`When cleared, uses the model default: ${Array.isArray(defaultValue) ? defaultValue.join(", ") : defaultValue}.`));
      if (multiple) block.append(note(domainChoice ? "Select one or more values. Remove a selected value to exclude it from the list." : "Enter one value per line. Commas remain part of a value."));
    }
    if (!alternative?.conditions.length) block.append(note("This alternative adds no restriction."));
    host.append(block);
  }
}

function scopeStatus(scope, active, activeScopes) {
  return scope.kind === "required" ? "Required for every query" : activeScopes === undefined ? "Conditional · checked when compiling" : active ? "Conditional · active for this query" : "Conditional · not needed by this query";
}

export function renderReportFilters(host, options) {
  const { draft, catalog, onChange } = options;
  draft.reportFilters ||= [];
  const refresh = () => { renderReportFilters(host, options); onChange({ structure: true }); };
  disposeSelects(host);
  host.replaceChildren(helpHeading("Report filters", "reportFilters"), note("Report groups are combined with AND. Related-record checks filter membership without adding duplicate output rows. Each group's conditions must match the same related record."));
  draft.reportFilters.forEach((group, index) => {
    const prefix = `Report filter ${index + 1}`;
    const content = element("div", { className: "mf-content" });
    content.append(element("div", { className: "mf-heading" }, [
      labeled("Behavior", select(`${prefix} behavior`, [["rows", "Filter returned detail"], ["exists", "Has a matching related record"], ["not_exists", "Has no matching related record"]], group.mode, value => { group.mode = value; refresh(); })),
      action("delete", `Delete ${prefix}`, () => { draft.reportFilters.splice(index, 1); refresh(); }),
    ]));
    if (group.mode !== "rows") content.append(note("Matches same related record. Use separate groups for 'has A' AND 'has B'."));
    content.append(conditionsEditor(group.conditions, { draft, catalog, onChange, refresh, prefix, onLoadDomain: options.onLoadDomain, exposedOnly: true }));
    host.append(section(prefix, [content]));
  });
  host.append(element("div", { className: "mf-add" }, [action("add", "Add report filter group", () => {
    draft.reportFilters.push({ id: id(), mode: "rows", conditions: [newCondition(draft, catalog)] }); refresh();
  }), "Add report filter"]));
}
