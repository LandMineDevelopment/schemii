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
  output: "select",
  join: "join",
  filter: "filter",
  aggregate_filter: "aggregate filter",
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
  const grouping = (analysis?.grouping || []).filter(item => !item.scope);
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

export function queryStoryPhases(analysis) {
  return (analysis?.querySteps || []).flatMap(step => [
    { kind: "operation", step },
    { kind: "result", step },
  ]);
}

function inputIdentity(input) {
  return input.source ? `${input.source}.${input.column}` : input.column;
}

function outputIdentity(output) {
  return output.name || `expression ${output.ordinal}`;
}

export function selectProjectionMappings(step) {
  return (step?.outputs || []).map(output => ({
    expression: output.expression || "Expression not reported",
    alias: outputIdentity(output),
    derivation: output.derivation || "derived",
    inputs: output.inputs || [],
  }));
}

export function selectProjectionsForColumn(step, participant, columnName) {
  const sources = new Set([participant?.reference, participant?.name].filter(Boolean));
  return selectProjectionMappings(step).filter(mapping => mapping.inputs.some(input => (
    input.column === columnName && sources.has(input.source)
  )));
}

function selectedInputKeys(output) {
  return new Set((output?.inputs || []).map(input => inputIdentity(input)));
}

function stepGrain(step) {
  if (step.grouping?.length) {
    return `One row per ${compactList(step.grouping.map(item => item.expression))}`;
  }
  if (step.outputs?.some(output => output.derivation === "aggregate")) return "One aggregate row";
  if (step.distinct) return "Distinct rows";
  if (!step.participants?.length) return "One constructed row";
  return "One row per match";
}

function participantIndex(step) {
  const index = new Map();
  (step.participants || []).forEach((participant, position) => {
    index.set(participant.reference, position % 6);
    index.set(participant.name, position % 6);
  });
  return index;
}

function coloredExpression(expression, step) {
  const value = expression || "Not reported";
  const code = element("code", { className: "view-colored-expression", title: value });
  const accents = participantIndex(step);
  const pattern = /([A-Za-z_][A-Za-z0-9_$]*\.[A-Za-z_][A-Za-z0-9_$]*)/g;
  let cursor = 0;
  for (const match of value.matchAll(pattern)) {
    if (match.index > cursor) code.append(document.createTextNode(value.slice(cursor, match.index)));
    const reference = match[0].split(".", 1)[0];
    const accent = accents.get(reference);
    code.append(element("span", {
      className: accent === undefined ? "" : `view-accent-text-${accent}`,
      text: match[0],
    }));
    cursor = match.index + match[0].length;
  }
  if (cursor < value.length) code.append(document.createTextNode(value.slice(cursor)));
  return code;
}

function selectProjectionLine(mapping, step) {
  const line = element("div", {
    className: "view-step-column-select-line",
    title: `${mapping.expression} → ${mapping.alias}`,
  });
  line.append(
    coloredExpression(mapping.expression, step),
    element("span", {
      className: "view-step-column-select-arrow",
      text: "→",
      attrs: { "aria-hidden": "true" },
    }),
    element("div", {}, [
      element("strong", { text: mapping.alias }),
      element("small", { text: mapping.derivation }),
    ]),
  );
  return line;
}

function participantRow(participant, index, selectedKeys, step) {
  const row = element("article", {
    className: `view-step-participant view-accent-${index}${participant.resolved ? "" : " unresolved"}`,
  });
  const identity = participant.namespace ? `${participant.namespace}.${participant.name}` : participant.name;
  const label = element("header");
  label.append(
    element("small", { text: participant.kind.replaceAll("_", " ") }),
    element("strong", { text: participant.name, title: identity }),
  );
  if (participant.reference !== participant.name) {
    label.append(element("code", { text: `as ${participant.reference}`, title: `Query alias ${participant.reference}` }));
  }
  row.append(label);

  const columns = element("div", { className: "view-step-columns" });
  if (!participant.columns?.length) {
    columns.append(element("p", { text: "No named column reference" }));
  }
  for (const column of participant.columns || []) {
    const identityKeys = new Set([
      `${participant.reference}.${column.name}`,
      `${participant.name}.${column.name}`,
    ]);
    const projections = selectProjectionsForColumn(step, participant, column.name);
    const card = element("div", {
      className: `view-step-column${column.filterOnly ? " filter-only" : ""}${[...identityKeys].some(key => selectedKeys.has(key)) ? " lineage-active" : ""}`,
      title: `${participant.reference}.${column.name} · ${column.dataType || "type unresolved"} · ${(column.roles || []).map(role => USE_LABELS[role] || role).join(", ")}${projections.length ? ` · ${projections.map(mapping => `${mapping.expression} → ${mapping.alias}`).join("; ")}` : ""}`,
    });
    const source = element("div", { className: "view-step-column-source" });
    source.append(element("strong", { text: column.name }));
    if (column.dataType) source.append(element("code", { text: column.dataType }));
    card.append(source);

    const select = element("div", { className: "view-step-column-select" });
    projections.forEach(mapping => select.append(selectProjectionLine(mapping, step)));
    card.append(select);

    const roles = element("span", { className: "view-step-column-roles" });
    if (column.filterOnly) {
      roles.append(element("i", { className: "filter-only", text: "FILTER ONLY" }));
    } else {
      for (const role of column.roles || []) {
        if (role === "output") continue;
        roles.append(element("i", { text: USE_LABELS[role] || role }));
      }
    }
    card.append(roles);
    columns.append(card);
  }
  row.append(columns);
  return row;
}

function logicCard(kind, label, title, expressions, step) {
  const card = element("article", { className: `view-step-logic view-step-logic-${kind}` });
  const head = element("header");
  head.append(
    element("span", { text: label }),
    element("strong", { text: title, title }),
    element("small", { text: plural(expressions.length, "condition") }),
  );
  card.append(head);
  const body = element("div");
  for (const expression of expressions) body.append(coloredExpression(expression, step));
  card.append(body);
  return card;
}

function unboundProjectionMappings(step) {
  const participantInputs = new Set((step.participants || []).flatMap(participant => (
    (participant.columns || []).flatMap(column => [
      `${participant.reference}.${column.name}`,
      `${participant.name}.${column.name}`,
    ])
  )));
  return selectProjectionMappings(step).filter(mapping => (
    !mapping.inputs.some(input => participantInputs.has(inputIdentity(input)))
  ));
}

function appendUnboundProjections(participants, step) {
  const mappings = unboundProjectionMappings(step);
  if (!mappings.length) return;
  const section = element("section", { className: "view-step-unbound-select" });
  section.append(element("header", {}, [
    element("strong", { text: "SELECT" }),
    element("small", { text: "Output without a resolved source column" }),
  ]));
  const rows = element("div");
  mappings.forEach(mapping => rows.append(selectProjectionLine(mapping, step)));
  section.append(rows);
  participants.append(section);
}

function stepLogic(step) {
  const logic = element("div", { className: "view-step-logic-grid" });
  for (const join of step.joins || []) {
    const target = join.alias ? `${join.target} as ${join.alias}` : join.target;
    logic.append(logicCard("join", "JOIN", `${join.joinType} JOIN · ${target}`, [join.expression || "No join predicate"], step));
  }
  if (step.rowFilters?.length) {
    logic.append(logicCard("filter", "FILTER", "WHERE", step.rowFilters.map(item => item.expression), step));
  }
  if (step.aggregateFilters?.length) {
    logic.append(logicCard("aggregate-filter", "AGG FILTER", "Aggregate input filter", step.aggregateFilters.map(item => item.expression), step));
  }
  if (step.grouping?.length) {
    logic.append(logicCard("group", "GROUP", "GROUP BY", step.grouping.map(item => item.expression), step));
  }
  if (step.groupFilters?.length) {
    logic.append(logicCard("having", "HAVING", "Grouped-row filter", step.groupFilters.map(item => item.expression), step));
  }
  if (step.distinct) logic.append(logicCard("distinct", "DISTINCT", "Remove duplicate rows", ["DISTINCT"], step));
  if (step.ordering?.length) {
    logic.append(logicCard("order", "ORDER", "ORDER BY", step.ordering.map(item => item.expression), step));
  }
  if (step.limit) logic.append(logicCard("limit", "LIMIT", "Maximum result rows", [`LIMIT ${step.limit}`], step));
  if (!logic.childNodes.length) {
    logic.append(element("p", { className: "view-step-direct", text: "Direct projection · no join, filter, grouping, or ordering rule" }));
  }
  return logic;
}

function operationCard(step, view, selected) {
  const isFinal = step.kind === "final";
  const card = element("article", { className: `view-query-operation${isFinal ? " final" : ""}` });
  const heading = element("header", { className: "view-query-card-head" });
  const title = isFinal ? `Build ${view.name}` : `Build ${step.resultName}`;
  heading.append(
    element("span", { text: String(step.ordinal).padStart(2, "0") }),
    element("div", {}, [
      element("small", { text: isFinal ? "OUTER QUERY" : step.kind.replaceAll("_", " ") }),
      element("strong", { text: title, title }),
    ]),
    element("code", { text: `${plural(step.participants.length, "input")} · ${plural(step.outputs.length, "output")}` }),
  );
  card.append(heading);

  const participants = element("section", { className: "view-step-participants" });
  participants.append(element("header", {}, [
    element("strong", { text: "Source columns → SELECT outputs" }),
    element("small", { text: "Relation color · expression → output" }),
  ]));
  const stepSelected = isFinal
    ? step.outputs.find(output => output.ordinal === selected?.ordinal) || selected
    : null;
  const selectedKeys = selectedInputKeys(stepSelected);
  if (!step.participants.length) {
    participants.append(element("p", { className: "view-meaning-empty", text: "This step does not read a relation." }));
  } else {
    step.participants.forEach((participant, index) => participants.append(participantRow(participant, index, selectedKeys, step)));
  }
  appendUnboundProjections(participants, step);
  card.append(participants, stepLogic(step));
  return card;
}

function outputAccent(output, step) {
  const accents = participantIndex(step);
  const values = new Set((output.inputs || []).map(input => accents.get(input.source)).filter(value => value !== undefined));
  return values.size === 1 ? ` view-accent-${[...values][0]}` : values.size > 1 ? " view-accent-multi" : "";
}

function resultColumn(output, step, selected, onSelect) {
  const isFinal = step.kind === "final";
  const tag = isFinal ? "button" : "article";
  const card = element(tag, {
    className: `view-step-output${outputAccent(output, step)}${selected ? " selected" : ""}`,
    type: isFinal ? "button" : null,
    title: `${outputIdentity(output)} ← ${output.expression || "expression not reported"}`,
    attrs: isFinal ? { "aria-pressed": selected ? "true" : "false" } : {},
  });
  card.append(
    element("span", { text: String(output.ordinal).padStart(2, "0") }),
    element("div", {}, [
      element("strong", { text: outputIdentity(output) }),
      coloredExpression(output.expression, step),
    ]),
    element("small", { text: output.derivation }),
  );
  if (isFinal) card.addEventListener("click", () => onSelect(output));
  return card;
}

function resultCard(step, view, selected, onSelectOutput) {
  const isFinal = step.kind === "final";
  const card = element("article", { className: `view-query-result${isFinal ? " final" : ""}` });
  const name = isFinal ? view.name : step.resultName;
  const heading = element("header", { className: "view-query-result-head" });
  heading.append(
    element("span", { text: "↓", attrs: { "aria-hidden": "true" } }),
    element("div", {}, [
      element("small", { text: isFinal ? "VIEW RESULT" : "INTERMEDIATE TABLE" }),
      element("strong", { text: name, title: name }),
    ]),
    element("code", { text: stepGrain(step), title: stepGrain(step) }),
  );
  card.append(heading);
  const outputs = element("div", { className: "view-step-outputs" });
  if (!step.outputs.length) {
    outputs.append(element("p", { className: "view-meaning-empty", text: "No stable output columns were derived." }));
  } else {
    for (const output of step.outputs) {
      outputs.append(resultColumn(output, step, isFinal && output.ordinal === selected?.ordinal, onSelectOutput));
    }
  }
  card.append(outputs);
  return card;
}

function chronologicalStory(analysis, view, selected, onSelectOutput) {
  const section = element("section", { className: "view-query-timeline" });
  const heading = element("header", { className: "view-query-timeline-head" });
  heading.append(element("div", {}, [
    element("small", { text: "CHRONOLOGICAL QUERY STORY" }),
    element("strong", { text: "Read each operation, then the relation it produces" }),
  ]));
  heading.append(element("span", { text: plural(analysis.querySteps.length, "query step") }));
  section.append(heading);
  const flow = element("div", { className: "view-query-flow" });
  for (const phase of queryStoryPhases(analysis)) {
    flow.append(phase.kind === "operation"
      ? operationCard(phase.step, view, selected)
      : resultCard(phase.step, view, selected, onSelectOutput));
  }
  section.append(flow);
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
  if (analysis.querySteps?.length) {
    story.append(chronologicalStory(analysis, view, selected, onSelectOutput));
  } else {
    story.append(emptyPanel("SQL", "No query steps", "The query parsed, but no executable SELECT scope was derived."));
  }
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
