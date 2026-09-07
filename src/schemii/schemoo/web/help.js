import { element } from "#common/dom.js";
import { createIconButton } from "/assets/common/ui.js";

// One vocabulary for the model editor, explorer, and canvas. Keep examples here
// rather than scattering slightly different definitions through the controls.
export const helpTopics = Object.freeze({
  overview: {
    title: "Building and exploring a model",
    intro: "A model describes which data can be combined and which filters should be applied. It does not copy or change your warehouse tables.",
    steps: ["Edit model: choose the connections, give repeated tables distinct roles with aliases, and define model filters.", "Explore: pick output fields, provide parameter values, and add report filters.", "Preview: inspect the generated SQL and run a read-only query. Red cycle connections must be resolved first."],
    note: "Save stores model rules, layout, and Explore choices on the server, independently of PostgreSQL. Save changed model rules before running previews or browsing configured parameter values. Existing browser prototypes can be imported explicitly without being erased.",
  },
  scopes: {
    title: "Model filters",
    intro: "A model filter is a rule defined by the model author. Its parameter inputs collect values; its source bindings say which table columns those values filter.",
    sections: [
      ["Required model scope", "Always applies and brings its required connection paths into the query. For example, require a parent organization even when the report only returns people’s names."],
      ["Conditional model parameter", "Applies only when a bound source is needed by the query, including a source used along a connection path or by another filter. For example, apply an as-of date to assignments only when assignments participate."],
    ],
    note: "These are query-building rules, not a replacement for database permissions or a published security policy.",
  },
  required: {
    title: "Required model scope",
    intro: "This rule applies to every query, even when the user does not select a field from the filtered source. Schemoo includes the connection path needed to enforce it.",
    sections: [["Organization example", "Bind Ancestor organization · id to a Parent organization input. A report returning only personnel names must still meet that organization restriction."], ["Returned details stay in scope", "If the report also returns assignments, those returned assignment details must meet the restriction too. Merely having some other qualifying assignment is not enough."]],
    note: "A required input needs a value or a default before the query can run. An alternative with no source bindings is unrestricted, so only add that alternative if users should be allowed to bypass this restriction.",
  },
  conditional: {
    title: "Conditional model parameter",
    intro: "This rule filters only bound sources that are already needed by the query. It does not add a source just because the rule mentions it.",
    sections: [["When it becomes active", "Selecting a field, traversing a connection path, enforcing a required scope, or adding a report filter can make a source participate. Its conditional bindings then apply and their inputs need values or defaults."], ["Preserving optional matches", "The participating source is filtered before the join. A person with no assignment at the chosen date can remain in a people report, with empty assignment details."]],
    note: "Use a report filter for ‘has a matching assignment’ if the person must be excluded when no assignment matches.",
  },
  alternatives: {
    title: "Filter alternatives",
    intro: "An alternative is one complete way to satisfy a model filter. The report user chooses one alternative; its source conditions are combined with AND.",
    sections: [["Date example", "Offer ‘As of a date’, ‘Active during a period’, or ‘All history’. The first asks for one date, the second for two dates, and the last can have no conditions."], ["OR between choices, AND within a choice", "Choosing ‘As of a date’ applies both start date ≤ As of date AND end date ≥ As of date. It does not also apply the period alternative."]],
    note: "An alternative without conditions imposes no restriction. Separate model filter groups all apply together when active.",
  },
  parameters: {
    title: "Parameter inputs and defaults",
    intro: "A parameter is a named value the report user supplies. Enable ‘Use searchable domain values’ to offer a list from a chosen source column, with an optional display label from the same table. This works alongside the parameter’s data type. Bind source fields separately to define where the selected value filters the model.",
    sections: [["Input type", "Choose from source offers distinct values from the first bound column. UUID validates UUID identifiers; Integer accepts whole numbers; Number accepts decimals; Boolean offers true or false; Date accepts a calendar date; Text accepts other text."], ["Required parent organization", "Use a Required model filter, add a Choose from source input, then Bind source to org_hier.parent_id with Equals. Leave its default blank. In Explore, choose an existing parent ID from the dropdown. The required scope includes the relationship path even when organization fields are not returned."], ["Default value", "An optional default is used when the user leaves the input blank. A date default may be today, which resolves to the current UTC date when the server compiles the query."], ["Explore values", "Choose from source and inputs with searchable domain values use the same dropdown for defaults and Explore. Other inputs accept typed values. Source choices exclude nulls and duplicates; search queries the source and shows up to 100 matches."]],
    note: "A source binding references the input itself, not its displayed name. IN / NOT IN bindings make the input and its default accept multiple values. Use the searchable dropdown to select several values, or enter one value per line. All bindings for that input must agree on single versus multiple values.",
  },
  bindings: {
    title: "Binding source fields to parameters",
    intro: "A source binding connects a real column to a parameter input. It tells Schemoo where and how to apply the value supplied in Explore. You do not need to draw a new relationship for each parameter.",
    steps: ["In Edit model, choose Add model filter → Report parameter. Name the input As of date, select Date as its type, and optionally set its default to today.", "In Source conditions, choose slate_fact · start_date in Source · column and ≤ in Comparison. The first condition is already bound to your input.", "Click Bind source for the same input to add another condition. Choose slate_fact · end_date and ≥. Enable Also accept null if a missing end date means the assignment is still active.", "Repeat for each source that needs this rule. Apply to model, then Save model to persist it. Switch to Explore to supply the date and inspect the generated SQL."],
    example: "slate_fact.start_date ≤ As of date\nAND (slate_fact.end_date ≥ As of date OR end_date IS NULL)",
    note: "For a fixed rule, select a source and enable Choose fixed value from a domain list to search values directly from that column. Is one of (IN) matches any selected value; NOT IN excludes the selected values. Select multiple options or remove their chips. Null is handled by Is null / Is not null or Include NULL rows, never as a list item. Required scope enforces the rule even when the source is not returned. Apply updates the draft; Save model persists it.",
  },
  reportFilters: {
    title: "Report filters and matching records",
    intro: "Report filters are extra restrictions chosen for this report. They can use a field even when that field is not returned; Schemoo includes the necessary connection path.",
    sections: [["Filter returned rows", "Restrict the actual details being returned. For example, return only assignment rows with a particular status. People without a matching assignment may be excluded."], ["Has a matching record", "Keep a person if a related record matches, without adding that record to the output or multiplying the person’s rows just to test membership."], ["Has no matching record", "Keep a person only when no related record matches. This is useful for people with no required certification."], ["Conditions within a group", "AND conditions in one matching-record group must hold in the same joined record combination. Put ‘has certification A’ and ‘has certification B’ in separate groups if different certification records may satisfy them."]],
    note: "All report filter groups are combined with AND. Filtering source records early must preserve the meaning of optional joins and aggregate results; it is not simply moving every condition ahead of every join.",
  },
  aliases: {
    title: "Source aliases",
    intro: "An alias is another named role for the same physical table. It does not create or copy a database table.",
    steps: ["Use Model → Add alias, or select a table card and create an alias. Give it a role name, such as Job certification requirement. Alias cards have an ALIAS badge and a dashed purple border.", "Select the alias header to see its incoming and outgoing connections. Each alias has its own copies, initially disabled. Toggle the ones this role needs; the original table's switches are independent. Enabling multiple paths can introduce a red cycle.", "Choose that alias explicitly when selecting fields or binding a model parameter. To remove it, select its header and use Delete alias, then confirm."],
    note: "Deleting an alias removes its model connections and selected fields, not the physical table or data. Affected filters remain but require a new source binding. Creating an alias alone does not redirect existing connections or remove a cycle.",
  },
  cycles: {
    title: "Connections and cycles",
    intro: "Connections come from the warehouse’s foreign keys. Red connections belong to a cycle: there is more than one route around part of the model, or a table connects back to itself.",
    sections: [["Why execution stops", "This prototype needs an unambiguous connection tree. It does not guess which route the model author intended, even if a particular preview uses only part of the model."], ["Resolve a cycle", "Disable a connection that should not participate, or create a role alias and redirect the appropriate connection to it. Disabled connections remain visible but are not used."], ["Canvas colors", "Red marks cycle connections. Purple marks connections used by the current valid preview plan. Dashed connections are disabled."]],
    note: "Changing a model connection does not change or remove the warehouse foreign key. Use Staffing example for a ready-to-test acyclic model.",
  },
  fields: {
    title: "Output fields and summaries",
    intro: "Canvas and table-inspector checkboxes control which columns the model exposes to Schemer. Preview has a separate field picker: add exposed columns as details or measures, then use the arrows to choose their result order. Preview selections do not change model exposure. Hiding a column removes its preview outputs.",
    sections: [["Detail fields", "These describe the result rows. When you add summaries, all non-summarized fields become the grouping fields."], ["Summaries", "Count counts non-null values; distinct count counts different non-null values. Sum, average, minimum, and maximum summarize the selected column."], ["Watch one-to-many connections", "Combining several child collections can multiply joined rows and inflate counts or sums. This prototype warns about fan-out but does not automatically make every aggregate safe. Inspect the SQL and result before relying on a total."]],
    note: "A matching-record report filter can test whether related rows exist without returning and multiplying those rows. It does not fix multiplication from other selected detail collections.",
  },
  root: {
    title: "Starting source",
    intro: "The starting source is the table role the query begins with. It sets the starting population, such as people rather than assignments.",
    sections: [["Why it matters", "Related output sources are joined to this starting source. Optional matches can leave related fields empty, while required scopes and report filters can restrict which starting records remain."], ["Not a uniqueness guarantee", "Selecting one-to-many detail fields can produce multiple rows per starting record. Grouping and summaries can change the final result grain."]],
    note: "Choose personnel_dim for a people report, or an assignment source for an assignment report. The enabled connection paths still need to be unambiguous.",
  },
  preview: {
    title: "Preview and saved model changes",
    intro: "The server validates the current model, generates SQL, and runs a bounded read-only preview against the connected warehouse. Preview does not change warehouse data or tables.",
    sections: [["SQL and results", "Inspect SQL to see the selected fields, connection paths, and filters. A preview is limited to 100 rows; it is not a complete data export. Query rows are not saved in model metadata."], ["Saved model", "Save stores aliases and filter rules, canvas layout, and Explore choices in separate server records. Reload returns to those saved choices and refreshes the source catalog without discarding the saved model. Concurrent edits produce a revision conflict instead of overwriting another tab."], ["Example and import", "Staffing example and Rebuild from source replace the editable model only after confirmation. Import browser prototype copies the previous browser draft without deleting it. Save afterward to keep the imported definition on the server."]],
    note: "This prototype is for testing modeling tools and query semantics, not yet production model publishing, security enforcement, or general aggregate optimization.",
  },
});

let helpDialog;
let returnTarget;

function dialog() {
  if (helpDialog) return helpDialog;
  helpDialog = element("dialog", { className: "sm-help-dialog", attrs: { "aria-labelledby": "sm-help-title" } });
  helpDialog.addEventListener("close", () => {
    if (returnTarget?.isConnected) returnTarget.focus({ preventScroll: true });
    returnTarget = null;
  });
  document.body.append(helpDialog);
  return helpDialog;
}

function showHelp(topic, trigger) {
  const content = helpTopics[topic];
  const modal = dialog();
  returnTarget = trigger;
  const close = createIconButton({ icon: "close", label: "Close information", tooltip: null, className: "ui-button" });
  close.title = "Close information";
  close.addEventListener("click", () => modal.close());
  const header = element("header", { className: "sm-help-dialog__header" }, [
    element("h2", { text: content.title, attrs: { id: "sm-help-title", tabindex: "-1" } }), close,
  ]);
  const body = element("div", { className: "sm-help-dialog__body" }, [element("p", { text: content.intro })]);
  if (content.steps) body.append(element("ol", {}, content.steps.map(text => element("li", { text }))));
  for (const [title, text] of content.sections || []) body.append(element("h3", { text: title }), element("p", { text }));
  if (content.example) body.append(element("pre", { className: "sm-help-example", text: content.example }));
  if (content.note) body.append(element("p", { className: "sm-help-note", text: content.note }));
  modal.replaceChildren(header, body);
  modal.showModal();
  header.querySelector("h2").focus({ preventScroll: true });
  body.scrollTop = 0;
}

/** Click/tap information with a shared hover/focus tooltip, not hover-only help. */
export function helpButton(topic) {
  const content = helpTopics[topic];
  if (!content) throw new TypeError(`Unknown Schemoo help topic: ${topic}`);
  const button = createIconButton({ icon: "info", label: `About ${content.title}`, tooltip: content.title, className: "ui-button sm-help-button" });
  button.setAttribute("aria-haspopup", "dialog");
  button.addEventListener("click", event => {
    // Information buttons can sit beside a details heading without toggling it.
    event.preventDefault();
    event.stopPropagation();
    showHelp(topic, button);
  });
  return button;
}

export function helpHeading(title, topic) {
  return element("div", { className: "sm-help-heading" }, [element("h3", { text: title }), helpButton(topic)]);
}
