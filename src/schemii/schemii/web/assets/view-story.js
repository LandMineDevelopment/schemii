import { element, emptyPanel, errorPanel, replace } from "./dom.js";

const TRANSFORMATION_LABELS = Object.freeze({
  stages: "Query stages",
  joins: "Join relations",
  filters: "Filter rows",
  groups: "Group rows",
  aggregates: "Calculate aggregates",
  windows: "Calculate windows",
  having: "Filter groups",
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

function plural(value, singular, pluralForm = `${singular}s`) {
  return `${value} ${value === 1 ? singular : pluralForm}`;
}

export function viewStorySummary(analysis) {
  if (!analysis) return "Analyze the query to reveal how data becomes this view.";
  const sources = plural(analysis.sources.length, "input");
  const outputs = plural(analysis.outputs.length, "output");
  const operations = analysis.transformations.reduce((total, item) => total + item.count, 0);
  if (!analysis.sources.length) return `${outputs} produced without a relation input.`;
  if (!operations) return `${sources} passed directly into ${outputs}.`;
  return `${sources} ${analysis.sources.length === 1 ? "passes" : "pass"} through ${plural(operations, "operation")} to produce ${outputs}.`;
}

export function transformationLabel(kind) {
  return TRANSFORMATION_LABELS[kind] || kind.replaceAll("_", " ");
}

function compactItems(items, emptyText) {
  const list = element("div", { className: "view-story-items" });
  if (!items.length) {
    list.append(element("p", { className: "view-story-none", text: emptyText }));
    return list;
  }
  for (const item of items) list.append(item);
  return list;
}

function sourceCard(source) {
  const card = element("article", { className: `view-story-item${source.resolved ? "" : " unresolved"}` });
  const identity = source.namespace ? `${source.namespace}.${source.name}` : source.name;
  card.append(
    element("strong", { text: identity, title: identity }),
    element("span", { text: `${source.kind.replaceAll("_", " ")} · ${source.resolved ? plural(source.columnCount, "known column") : "shape unresolved"}` }),
  );
  if (source.aliases?.some(alias => alias !== source.name)) {
    card.append(element("code", { text: `as ${source.aliases.join(", ")}`, title: source.aliases.join(", ") }));
  }
  return card;
}

function transformationCard(transformation) {
  const card = element("article", { className: `view-story-item transformation-${transformation.kind}` });
  card.append(
    element("strong", { text: transformationLabel(transformation.kind) }),
    element("span", { text: plural(transformation.count, "operation") }),
  );
  const evidence = transformation.items?.length ? transformation.items.join(" · ") : transformation.sql;
  if (evidence) card.append(element("code", { text: evidence, title: evidence }));
  return card;
}

function outputCard(output, selected, onSelect) {
  const label = output.name || `expression ${output.ordinal}`;
  const button = element("button", {
    className: `view-story-item view-output${selected ? " selected" : ""}`,
    type: "button",
    title: output.expression || label,
    attrs: { "aria-pressed": selected ? "true" : "false" },
  });
  button.append(
    element("strong", { text: label }),
    element("span", { text: `${output.derivation} · ${output.dataType || "type resolved by PostgreSQL"}` }),
  );
  button.addEventListener("click", () => onSelect(output));
  return button;
}

function consumerCard(consumer) {
  const card = element("article", { className: "view-story-item" });
  card.append(
    element("strong", { text: consumer.name, title: consumer.name }),
    element("span", { text: consumer.kind.replaceAll("_", " ") }),
  );
  return card;
}

function stage(number, label, count, items) {
  const section = element("section", { className: "view-story-stage" });
  const header = element("header");
  header.append(
    element("span", { className: "view-story-step", text: String(number).padStart(2, "0") }),
    element("div", {}, [element("small", { text: label }), element("strong", { text: count })]),
  );
  section.append(header, items);
  return section;
}

function tracePanel(output) {
  const panel = element("section", { className: "view-output-trace" });
  const name = output.name || `expression ${output.ordinal}`;
  panel.append(element("header", {}, [
    element("div", {}, [
      element("small", { text: `OUTPUT ${String(output.ordinal).padStart(2, "0")} · ${output.derivation}` }),
      element("strong", { text: name }),
    ]),
    element("code", { text: output.dataType || "type resolved by PostgreSQL" }),
  ]));
  const flow = element("div", { className: "view-output-trace-flow" });
  const inputs = element("div");
  inputs.append(element("small", { text: "Reads" }));
  const chips = element("div", { className: "view-input-chips" });
  if (output.inputs.length) {
    for (const input of output.inputs) {
      const identity = input.source ? `${input.source}.${input.column}` : input.column;
      chips.append(element("span", { className: input.resolved ? "" : "unresolved", text: identity, title: identity }));
    }
  } else chips.append(element("span", { text: "No input columns" }));
  inputs.append(chips);
  flow.append(
    inputs,
    element("span", { className: "view-trace-arrow", text: "→", attrs: { "aria-hidden": "true" } }),
    element("div", {}, [element("small", { text: "Expression" }), element("pre", { text: output.expression || "Not reported" })]),
  );
  panel.append(flow);
  return panel;
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
    container.append(emptyPanel("VIEW", "No view selected", "Select a designed view to see how its query turns source data into output columns."));
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
    heading.append(actions);
  }
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
    const sql = element("details", { className: "view-sql-drilldown" });
    sql.append(element("summary", { text: "Query definition" }), element("pre", { text: view.queryDefinition }));
    story.append(sql);
    container.append(story);
    return;
  }
  if (!analysis) {
    story.append(emptyPanel("SQL", "No query analysis", "Enter a valid SELECT query to reveal this view's data flow."));
    container.append(story);
    return;
  }

  const transformItems = analysis.transformations.map(transformationCard);
  const selected = analysis.outputs.find(output => output.ordinal === selectedOutputOrdinal)
    || analysis.outputs[0]
    || null;
  const flow = element("div", { className: "view-story-flow", attrs: { "aria-label": "View data flow" } });
  flow.append(
    stage(1, "Inputs", plural(analysis.sources.length, "relation"), compactItems(analysis.sources.map(sourceCard), "No relation input")),
    element("span", { className: "view-story-arrow", text: "→", attrs: { "aria-hidden": "true" } }),
    stage(2, "Transform", transformItems.length ? plural(transformItems.length, "kind") : "Pass through", compactItems(transformItems, "Direct projection")),
    element("span", { className: "view-story-arrow", text: "→", attrs: { "aria-hidden": "true" } }),
    stage(3, "Outputs", plural(analysis.outputs.length, "column"), compactItems(analysis.outputs.map(output => outputCard(output, output.ordinal === selected?.ordinal, onSelectOutput)), "No named outputs")),
    element("span", { className: "view-story-arrow", text: "→", attrs: { "aria-hidden": "true" } }),
    stage(4, "Consumers", plural(analysis.consumers.length, "view"), compactItems(analysis.consumers.map(consumerCard), "Nothing else reads this view")),
  );
  story.append(flow);
  if (selected) story.append(tracePanel(selected));
  if (analysis.warnings.length) {
    const warnings = element("details", { className: "view-story-warnings" });
    warnings.append(element("summary", { text: `${plural(analysis.warnings.length, "analysis note")}` }));
    const list = element("ul");
    for (const warning of analysis.warnings) list.append(element("li", { text: WARNING_COPY[warning] || warning.replaceAll("_", " ") }));
    warnings.append(list);
    story.append(warnings);
  }
  const sql = element("details", { className: "view-sql-drilldown" });
  sql.append(element("summary", { text: "Query definition" }), element("pre", { text: view.queryDefinition }));
  story.append(sql);
  container.append(story);
}
