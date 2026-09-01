import { element, emptyPanel, errorPanel, replace } from "./dom.js";

const TRANSFORMATION_LABELS = Object.freeze({
  stages: "Named query stages",
  joins: "Join relations",
  filters: "Filter source rows",
  groups: "Define result grain",
  aggregates: "Calculate aggregates",
  windows: "Calculate windows",
  having: "Filter grouped rows",
  distinct: "Remove duplicates",
  sets: "Combine result sets",
  sorts: "Order results",
  limits: "Limit results",
});

const WARNING_COPY = Object.freeze({
  recursive_reference: "The query refers to the view it is defining.",
  unresolved_relation: "At least one input relation is not present in this design.",
  unresolved_column_source: "At least one column could not be tied to one input relation.",
  unresolved_wildcard: "A wildcard output could not be expanded without a known source shape.",
  unnamed_output: "At least one expression has no stable output name.",
  set_operation_output_contract: "Output names are inferred from the first set-operation branch.",
  too_many_outputs: "The output list exceeds the analysis display limit.",
});

const USE_LABELS = Object.freeze({
  output: "result",
  join: "join",
  filter: "filter",
  group: "grain",
  having: "having",
  sort: "sort",
});

function plural(value, singular, pluralForm = `${singular}s`) {
  return `${value} ${value === 1 ? singular : pluralForm}`;
}

function compactList(values, maximum = 3) {
  if (values.length <= maximum) return values.join(" + ");
  return `${values.slice(0, maximum).join(" + ")} + ${values.length - maximum} more`;
}

export function transformationLabel(kind) {
  return TRANSFORMATION_LABELS[kind] || kind.replaceAll("_", " ");
}

export function resultGrain(analysis) {
  const grouping = analysis?.grouping || [];
  if (grouping.length) {
    return `One row per ${compactList(grouping.map(item => item.expression))}`;
  }
  if (analysis?.outputs?.some(output => output.derivation === "aggregate")) {
    return "One aggregate row";
  }
  if (analysis?.distinct) return "Distinct result rows";
  if (analysis?.setOperations?.length) return "Combined result rows";
  if (!analysis?.sources?.length) return "One constructed row";
  return "One row per matching source row";
}

export function viewStorySummary(analysis) {
  if (!analysis) return "Analyze the query to reveal its relational meaning.";
  const columns = plural(analysis.outputs.length, "column");
  const relations = plural(analysis.sources.length, "source relation");
  return `${resultGrain(analysis)}, producing ${columns} from ${relations}.`;
}

function inputIdentity(input) {
  return input.source ? `${input.source}.${input.column}` : input.column;
}

function outputIdentity(output) {
  return output.name || `expression ${output.ordinal}`;
}

function sourceIdentity(source) {
  return source.namespace ? `${source.namespace}.${source.name}` : source.name;
}

function selectedInputKeys(output) {
  return new Set((output?.inputs || []).map(input => inputIdentity(input)));
}

function sectionHeading(eyebrow, title, count = null) {
  const heading = element("header", { className: "view-meaning-section-head" });
  heading.append(element("div", {}, [
    element("small", { text: eyebrow }),
    element("strong", { text: title }),
  ]));
  if (count) heading.append(element("span", { text: count }));
  return heading;
}

function sourceCard(source, selectedKeys) {
  const identity = sourceIdentity(source);
  const aliases = (source.aliases || []).filter(alias => alias !== source.name);
  const relevantColumns = (source.columns || []).filter(column => column.uses?.length);
  const selected = relevantColumns.some(column => selectedKeys.has(`${source.name}.${column.name}`));
  const card = element("article", {
    className: `view-source-card${source.resolved ? "" : " unresolved"}${selected ? " lineage-active" : ""}`,
  });
  const header = element("header");
  header.append(element("div", {}, [
    element("small", { text: source.kind.replaceAll("_", " ") }),
    element("strong", { text: source.name, title: identity }),
  ]));
  if (aliases.length) header.append(element("code", { text: `as ${aliases.join(", ")}`, title: aliases.join(", ") }));
  card.append(header);

  const columns = element("div", { className: "view-source-columns" });
  if (!source.resolved) {
    columns.append(element("p", { text: "Relation shape is not available in this design." }));
  } else if (!relevantColumns.length) {
    columns.append(element("p", { text: `${plural(source.columnCount, "known column")} · no named column reference` }));
  } else {
    for (const column of relevantColumns) {
      const key = `${source.name}.${column.name}`;
      const row = element("div", {
        className: `view-source-column${selectedKeys.has(key) ? " lineage-active" : ""}`,
        title: `${identity}.${column.name} · ${column.dataType || "type unresolved"}`,
      });
      const uses = (column.uses || []).filter(use => use !== "read");
      row.append(
        element("strong", { text: column.name }),
        element("code", { text: column.dataType || "unresolved" }),
      );
      if (uses.length) {
        const roles = element("span", { className: "view-source-uses" });
        for (const use of uses) {
          roles.append(element("i", {
            text: USE_LABELS[use] || use,
            title: `Used by query ${USE_LABELS[use] || use}`,
          }));
        }
        row.append(roles);
      }
      columns.append(row);
    }
  }
  card.append(columns);
  return card;
}

function inputChips(inputs, emptyText = "No source column") {
  const chips = element("div", { className: "view-input-chips" });
  if (!inputs?.length) {
    chips.append(element("span", { text: emptyText }));
    return chips;
  }
  for (const input of inputs) {
    const identity = inputIdentity(input);
    chips.append(element("span", {
      className: input.resolved ? "" : "unresolved",
      text: identity,
      title: identity,
    }));
  }
  return chips;
}

function resultRow(output, selected, onSelect) {
  const name = outputIdentity(output);
  const row = element("article", { className: `view-result-row${selected ? " selected" : ""}` });
  const button = element("button", {
    type: "button",
    title: `${name} ← ${output.expression || "expression not reported"}`,
    attrs: { "aria-expanded": selected ? "true" : "false" },
  });
  button.append(
    element("span", { className: "view-result-ordinal", text: String(output.ordinal).padStart(2, "0") }),
    element("div", {}, [
      element("strong", { text: name }),
      element("code", { text: output.expression || "Expression not reported", title: output.expression || name }),
    ]),
    element("span", { className: `view-derivation view-derivation-${output.derivation}`, text: output.derivation }),
    element("small", { text: output.dataType || "type at apply" }),
  );
  button.addEventListener("click", () => onSelect(output));
  row.append(button);
  if (selected) {
    const emptyInputText = output.derivation === "aggregate" && /count\s*\(\s*\*\s*\)/i.test(output.expression || "")
      ? "Counts matching source rows"
      : output.derivation === "constant"
        ? "No source data"
        : "No resolved source column";
    const detail = element("div", { className: "view-result-detail" });
    detail.append(
      element("span", { text: "Reads" }),
      inputChips(output.inputs, emptyInputText),
      element("span", { text: "Expression" }),
      element("pre", { text: output.expression || "Not reported" }),
    );
    row.append(detail);
  }
  return row;
}

function resultContract(analysis, selected, onSelectOutput) {
  const section = element("section", { className: "view-result-contract" });
  section.append(sectionHeading("Result contract", resultGrain(analysis), plural(analysis.outputs.length, "column")));
  const rows = element("div", { className: "view-result-rows" });
  if (!analysis.outputs.length) {
    rows.append(element("p", { className: "view-meaning-empty", text: "No stable result columns were derived." }));
  } else {
    for (const output of analysis.outputs) {
      rows.append(resultRow(output, output.ordinal === selected?.ordinal, onSelectOutput));
    }
  }
  section.append(rows);
  return section;
}

function sourcesPanel(analysis, selected) {
  const section = element("section", { className: "view-source-universe" });
  section.append(sectionHeading("Relations read", "Source relations", plural(analysis.sources.length, "relation")));
  if (analysis.stages?.length) {
    const stages = element("div", { className: "view-stage-strip" });
    stages.append(element("small", { text: "Named query stages" }));
    for (const stage of analysis.stages) {
      stages.append(element("span", { text: `CTE ${stage}`, title: `Named query stage ${stage}` }));
    }
    section.append(stages);
  }
  const cards = element("div", { className: "view-source-grid" });
  const keys = selectedInputKeys(selected);
  if (!analysis.sources.length) {
    cards.append(element("p", { className: "view-meaning-empty", text: "This query does not read a relation." }));
  } else {
    for (const source of analysis.sources) cards.append(sourceCard(source, keys));
  }
  section.append(cards);
  return section;
}

function ruleCard(kind, title, expression, { scope = null, detail = null, inputs = [] } = {}) {
  const card = element("article", { className: `view-rule view-rule-${kind}` });
  const head = element("header");
  head.append(
    element("span", { text: kind.toUpperCase() }),
    element("strong", { text: title, title }),
  );
  if (scope) head.append(element("small", { text: `in ${scope}`, title: `Inside CTE ${scope}` }));
  card.append(head);
  if (expression) card.append(element("code", { text: expression, title: expression }));
  if (detail) card.append(element("p", { text: detail }));
  if (inputs.length) card.append(inputChips(inputs));
  return card;
}

function rulesPanel(analysis) {
  const section = element("section", { className: "view-query-rules" });
  const ruleCount = (analysis.joins?.length || 0)
    + (analysis.rowFilters?.length || 0)
    + (analysis.groupFilters?.length || 0)
    + (analysis.ordering?.length || 0)
    + (analysis.grouping?.length ? 1 : 0)
    + (analysis.distinct ? 1 : 0)
    + (analysis.limit ? 1 : 0)
    + (analysis.setOperations?.length || 0);
  section.append(sectionHeading("Relational meaning", "Relationships & rules", ruleCount ? plural(ruleCount, "rule") : "Direct projection"));
  const rules = element("div", { className: "view-rules-list" });

  for (const join of analysis.joins || []) {
    const target = join.alias && join.alias !== join.target ? `${join.target} as ${join.alias}` : join.target;
    rules.append(ruleCard("join", `${join.joinType} join ${target}`, join.expression || "No join predicate", {
      scope: join.scope,
      inputs: join.inputs,
    }));
  }
  for (const filter of analysis.rowFilters || []) {
    rules.append(ruleCard("where", "Keep source rows where", filter.expression, filter));
  }
  if (analysis.grouping?.length) {
    rules.append(ruleCard("group", resultGrain(analysis), compactList(analysis.grouping.map(item => item.expression), 5), {
      detail: "These columns define the result row grain.",
      inputs: analysis.grouping.flatMap(item => item.inputs || []),
    }));
  }
  for (const filter of analysis.groupFilters || []) {
    rules.append(ruleCard("having", "Keep grouped rows where", filter.expression, filter));
  }
  if (analysis.distinct) rules.append(ruleCard("distinct", "Return unique result rows", "DISTINCT"));
  for (const operation of analysis.setOperations || []) {
    rules.append(ruleCard("set", `Combine another result with ${operation}`, operation));
  }
  for (const order of analysis.ordering || []) {
    rules.append(ruleCard("order", "Present results ordered by", order.expression, order));
  }
  if (analysis.limit) rules.append(ruleCard("limit", `Return at most ${analysis.limit} rows`, `LIMIT ${analysis.limit}`));
  if (!ruleCount) {
    rules.append(element("p", { className: "view-meaning-empty", text: "Selected columns pass directly into the result." }));
  }
  section.append(rules);
  return section;
}

function appendHighlightedSql(pre, sql, expression) {
  if (!expression) {
    pre.textContent = sql;
    return;
  }
  const index = sql.toLocaleLowerCase().indexOf(expression.toLocaleLowerCase());
  if (index < 0) {
    pre.textContent = sql;
    return;
  }
  pre.append(
    document.createTextNode(sql.slice(0, index)),
    element("mark", { text: sql.slice(index, index + expression.length) }),
    document.createTextNode(sql.slice(index + expression.length)),
  );
}

function sqlPanel(analysis, view, selected) {
  const details = element("details", { className: "view-sql-panel", attrs: { open: "" } });
  const summary = element("summary");
  summary.append(
    element("span", {}, [element("small", { text: "Source of truth" }), element("strong", { text: "Query definition" })]),
    element("code", { text: selected ? `focused: ${outputIdentity(selected)}` : "formatted PostgreSQL" }),
  );
  const pre = element("pre");
  appendHighlightedSql(pre, analysis.formattedSql || view.queryDefinition, selected?.expression);
  details.append(summary, pre);
  return details;
}

function impactBadge(consumers) {
  const label = consumers.length ? `Used by ${plural(consumers.length, "view")}` : "No downstream views";
  const badge = element("span", { className: "view-impact-badge", text: label, title: label });
  if (consumers.length) badge.title = consumers.map(consumer => consumer.name).join(", ");
  return badge;
}

function kindCopy(view) {
  if (view.catalogKind !== "materialized_view") return "LIVE QUERY · recalculated when read";
  return view.populateOnCreate === false
    ? "STORED RESULT · created empty"
    : "STORED RESULT · populated when created";
}

export function renderDesignViewStory(container, {
  view,
  analysis = null,
  loading = false,
  error = null,
  selectedOutputOrdinal = null,
  onSelectOutput = () => {},
  onEdit = null,
  onDelete = null,
  onRetry = null,
  compact = false,
} = {}) {
  replace(container);
  if (!view) {
    container.append(emptyPanel("VIEW", "No view selected", "Select a designed view to see its source-derived relational meaning."));
    return;
  }
  const story = element("div", { className: `view-story${compact ? " compact" : ""}` });
  const heading = element("header", { className: "view-story-head" });
  const title = element("div");
  title.append(
    element("span", { className: "eyebrow", text: kindCopy(view) }),
    element("h2", { text: view.name, title: view.name }),
    element("p", { text: viewStorySummary(analysis) }),
  );
  heading.append(title);
  const headingActions = element("div", { className: "view-story-actions" });
  if (analysis) headingActions.append(impactBadge(analysis.consumers || []));
  if (onEdit || onDelete) {
    const actions = element("div", { className: "ui-action-group" });
    if (onEdit) {
      const edit = element("button", { className: "ui-button compact", type: "button", text: "Edit query" });
      edit.addEventListener("click", onEdit);
      actions.append(edit);
    }
    if (onDelete) {
      const remove = element("button", { className: "ui-button compact danger", type: "button", text: "Delete" });
      remove.addEventListener("click", onDelete);
      actions.append(remove);
    }
    headingActions.append(actions);
  }
  if (headingActions.childNodes.length) heading.append(headingActions);
  story.append(heading);

  if (loading) {
    story.append(element("div", { className: "view-story-loading", attrs: { role: "status" } }, [
      element("span", { attrs: { "aria-hidden": "true" } }),
      element("strong", { text: "Reading the query structure…" }),
    ]));
    container.append(story);
    return;
  }
  if (error) {
    story.append(errorPanel(error, { retryLabel: onRetry ? "Analyze again" : null, onRetry }));
    const sql = element("details", { className: "view-sql-panel", attrs: { open: "" } });
    sql.append(element("summary", { text: "Query definition" }), element("pre", { text: view.queryDefinition }));
    story.append(sql);
    container.append(story);
    return;
  }
  if (!analysis) {
    story.append(emptyPanel("SQL", "No query analysis", "Enter a valid SELECT query to reveal this view's relational meaning."));
    container.append(story);
    return;
  }

  const selected = analysis.outputs.find(output => output.ordinal === selectedOutputOrdinal)
    || analysis.outputs[0]
    || null;
  const canvas = element("div", { className: "view-meaning-canvas", attrs: { "aria-label": "Relational meaning map" } });
  canvas.append(
    resultContract(analysis, selected, onSelectOutput),
    sourcesPanel(analysis, selected),
    rulesPanel(analysis),
  );
  story.append(canvas);
  if (!compact) story.append(sqlPanel(analysis, view, selected));
  if (analysis.warnings.length) {
    const warnings = element("details", { className: "view-story-warnings" });
    warnings.append(element("summary", { text: plural(analysis.warnings.length, "analysis note") }));
    const list = element("ul");
    for (const warning of analysis.warnings) {
      list.append(element("li", { text: WARNING_COPY[warning] || warning.replaceAll("_", " ") }));
    }
    warnings.append(list);
    story.append(warnings);
  }
  container.append(story);
}
