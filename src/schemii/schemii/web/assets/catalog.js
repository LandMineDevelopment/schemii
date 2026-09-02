import { element, emptyPanel, normalizedSearch, replace } from "./dom.js";
import { unavailableButton } from "./unavailable.js";

const MAX_BROWSER_ITEMS = 250;
const MAX_INSPECTOR_ITEMS = 250;

function valueText(value) {
  if (value === null || value === undefined || value === "") return "Not set";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.join(", ") || "None";
  return String(value);
}

function metadataGrid(entries) {
  const list = element("dl", { className: "metadata-grid" });
  for (const [label, value] of entries) {
    const item = element("div");
    item.append(element("dt", { text: label }), element("dd", { text: valueText(value) }));
    list.append(item);
  }
  return list;
}

function section(title, count, action = null) {
  const wrapper = element("section", { className: "inspector-section" });
  const heading = element("header", { className: "section-title" });
  heading.append(element("h3", { text: title }));
  if (action) heading.append(action);
  else heading.append(element("span", { text: count }));
  wrapper.append(heading);
  return wrapper;
}

function actionButton(label, onClick, { danger = false } = {}) {
  const button = element("button", {
    className: `ui-button compact${danger ? " danger-text" : ""}`,
    type: "button",
    text: label,
  });
  button.addEventListener("click", onClick);
  return button;
}

function itemCard(title, kind, entries = [], definition = null, actions = []) {
  const card = element("article", { className: "inspector-item" });
  const head = element("header", { className: "inspector-item-head" });
  head.append(element("strong", { text: title }), element("span", { text: kind }));
  card.append(head);
  if (entries.length) {
    const list = element("dl");
    for (const [label, value] of entries) list.append(element("dt", { text: label }), element("dd", { text: valueText(value) }));
    card.append(list);
  }
  if (definition) card.append(element("p", { text: definition }));
  if (actions.length) card.append(element("div", { className: "inspector-item-actions" }, actions));
  return card;
}

function listOrEmpty(wrapper, items, desired = false) {
  if (!items.length) wrapper.append(element("p", { className: "none-reported", text: desired ? "None in this design." : "None reported by the live catalog." }));
  else wrapper.append(element("div", { className: "inspector-list" }, items));
}

function boundedInspectorList(wrapper, values, renderItem, noun, desired = false) {
  const visible = values.slice(0, MAX_INSPECTOR_ITEMS);
  listOrEmpty(wrapper, visible.map(renderItem), desired);
  if (values.length > visible.length) {
    wrapper.append(element("p", {
      className: "none-reported",
      text: `Showing the first ${visible.length} of ${values.length} ${noun}. Use the downloaded ${desired ? "desired design" : "live catalog JSON"} for the complete set.`,
    }));
  }
}

export function renderCatalogStats(container, catalog) {
  replace(container);
  if (!catalog) return;
  const entries = [
    ["Tables", catalog.tables.length],
    ["Views", catalog.views.length + catalog.materializedViews.length],
    ["Types", (catalog.types || []).length],
    ["Routines", catalog.functions.length],
    ["Triggers", catalog.triggers?.length ?? catalog.tables.reduce((total, table) => total + table.triggers.length, 0)],
  ];
  for (const [label, value] of entries) {
    const item = element("div");
    item.append(element("dt", { text: label }), element("dd", { text: value }));
    container.append(item);
  }
}

export function renderInspector({
  inspector,
  empty,
  content,
  title = null,
  table,
  catalog,
  onEditTable = null,
  onAddKey = null,
  onEditKey = null,
  onDeleteKey = null,
  onAddCheck = null,
  onEditCheck = null,
  onDeleteCheck = null,
  onAddIndex = null,
  onEditIndex = null,
  onDeleteIndex = null,
  onAddTrigger = null,
  onEditTrigger = null,
  onDeleteTrigger = null,
  onAddRelationship = null,
  onEditRelationship = null,
  onDeleteRelationship = null,
}) {
  inspector.classList.toggle("is-empty", !table);
  empty.hidden = Boolean(table);
  content.hidden = !table;
  replace(content);
  if (title) title.textContent = table?.name || "Table inspector";
  if (!table || !catalog) return;
  const desired = catalog.source === "design";

  const identity = section("Table identity", 0);
  identity.append(metadataGrid([
    ["Namespace", table.namespace],
    ["Kind", table.kind],
    ["Partition", table.isPartition],
    ["Partition key", table.partitionKey],
  ]));
  content.append(identity);

  const relationshipValues = catalog.relationships.filter(relationship =>
    (relationship.sourceNamespace === table.namespace && relationship.sourceTable === table.name)
    || (relationship.targetNamespace === table.namespace && relationship.targetTable === table.name));
  const foreignKeysByColumn = new Map();
  for (const relationship of relationshipValues) {
    if (relationship.sourceNamespace !== table.namespace || relationship.sourceTable !== table.name) continue;
    for (const column of relationship.sourceColumns) {
      const names = foreignKeysByColumn.get(column) || [];
      names.push(relationship.name);
      foreignKeysByColumn.set(column, names);
    }
  }

  const columns = section(
    "Columns",
    table.columns.length,
    desired && onEditTable ? actionButton("Edit table", onEditTable) : unavailableButton("column-create", "Add column"),
  );
  boundedInspectorList(columns, table.columns, column => {
    const primary = table.primaryKey?.columns?.includes(column.name) || false;
    const collation = column.collationSchema && column.collationName ? `${column.collationSchema}.${column.collationName}` : null;
    return itemCard(column.name, `Column ${column.ordinal}`, [
      ["Data type", column.dataType],
      ["Nullable", column.nullable],
      ["Primary key", primary],
      ["Foreign keys", foreignKeysByColumn.get(column.name) || []],
      ["Default", column.defaultExpression],
      ["Identity", column.identity],
      ["Generated", column.generated],
      ["Collation", collation],
    ], null, desired ? [] : [
      unavailableButton("column-edit", "Edit"),
      unavailableButton("column-delete", "Delete"),
    ]);
  }, "columns", desired);
  content.append(columns);

  const constraintValues = [
    ...(table.primaryKey ? [{ ...table.primaryKey, displayKind: "Primary key" }] : []),
    ...table.uniqueConstraints.map(item => ({ ...item, displayKind: "Unique" })),
    ...table.checks.map(item => ({ ...item, displayKind: "Check" })),
    ...table.notNullConstraints.map(item => ({ ...item, displayKind: "Not null" })),
    ...table.exclusionConstraints.map(item => ({ ...item, displayKind: "Exclusion" })),
  ];
  const constraints = section(
    "Constraints",
    constraintValues.length,
    desired && (onAddKey || onAddCheck) ? element("span", { className: "section-actions" }, [
      ...(onAddKey ? [actionButton("Add key", onAddKey)] : []),
      ...(onAddCheck ? [actionButton("Add check", onAddCheck)] : []),
    ]) : null,
  );
  boundedInspectorList(constraints, constraintValues, constraint => {
    const editableKey = desired && constraint.designId
      && (constraint.displayKind === "Primary key" || constraint.displayKind === "Unique");
    const editableCheck = desired && constraint.designId && constraint.displayKind === "Check";
    return itemCard(constraint.name, constraint.displayKind, [
      ["Columns", constraint.columns],
      ["Validated", constraint.validated],
      ["Deferrable", constraint.deferrable],
      ["Initially deferred", constraint.initiallyDeferred],
    ], constraint.definition, editableKey ? [
      ...(onEditKey ? [actionButton("Edit", () => onEditKey(constraint))] : []),
      ...(onDeleteKey ? [actionButton("Delete", () => onDeleteKey(constraint), { danger: true })] : []),
    ] : editableCheck ? [
      ...(onEditCheck ? [actionButton("Edit", () => onEditCheck(constraint))] : []),
      ...(onDeleteCheck ? [actionButton("Delete", () => onDeleteCheck(constraint), { danger: true })] : []),
    ] : []);
  }, "constraints", desired);
  content.append(constraints);

  const indexes = section(
    "Indexes",
    table.indexes.length,
    desired && onAddIndex ? actionButton("Add index", onAddIndex) : null,
  );
  boundedInspectorList(indexes, table.indexes, index => itemCard(index.name, index.method, [
    ["Columns", index.columns || []],
    ["Expression", index.expression],
    ["Unique", index.unique],
    ["Valid", index.valid],
    ["Predicate", index.predicate],
  ], index.definition, desired && index.designId ? [
    ...(onEditIndex ? [actionButton("Edit", () => onEditIndex(index))] : []),
    ...(onDeleteIndex ? [actionButton("Delete", () => onDeleteIndex(index), { danger: true })] : []),
  ] : []), "indexes", desired);
  content.append(indexes);

  const triggers = section(
    "Triggers",
    table.triggers.length,
    desired && onAddTrigger ? actionButton("Add trigger", onAddTrigger) : null,
  );
  boundedInspectorList(triggers, table.triggers, trigger => itemCard(trigger.name, "Trigger", [
    ["Timing", trigger.timing?.replaceAll("_", " ")],
    ["Events", trigger.events],
    ["Scope", trigger.orientation],
    ["Function", trigger.functionName],
    ["Condition", trigger.whenExpression],
    ["Enabled", trigger.enabled],
  ], trigger.definition, desired && trigger.designId ? [
    ...(onEditTrigger ? [actionButton("Edit", () => onEditTrigger(trigger))] : []),
    ...(onDeleteTrigger ? [actionButton("Delete", () => onDeleteTrigger(trigger), { danger: true })] : []),
  ] : []), "triggers", desired);
  content.append(triggers);

  const relationships = section(
    "Relationships",
    relationshipValues.length,
    desired && onAddRelationship ? actionButton("Add relationship", onAddRelationship) : unavailableButton("relationship-create", "Add relationship"),
  );
  boundedInspectorList(relationships, relationshipValues, relationship => {
    const direction = relationship.sourceTable === table.name && relationship.sourceNamespace === table.namespace ? "Outgoing" : "Incoming";
    return itemCard(relationship.name, direction, [
      ["Source", `${relationship.sourceNamespace}.${relationship.sourceTable} (${relationship.sourceColumns.join(", ")})`],
      ["Target", `${relationship.targetNamespace}.${relationship.targetTable} (${relationship.targetColumns.join(", ")})`],
      ["On update", relationship.onUpdate],
      ["On delete", relationship.onDelete],
      ["Match", relationship.matchType],
      ["Validated", relationship.validated],
      ["Deferrable", relationship.deferrable],
      ["Initially deferred", relationship.initiallyDeferred],
    ], relationship.definition, desired ? [
      ...(onEditRelationship ? [actionButton("Edit", () => onEditRelationship(relationship))] : []),
      ...(onDeleteRelationship ? [actionButton("Delete", () => onDeleteRelationship(relationship), { danger: true })] : []),
    ] : [
      unavailableButton("relationship-edit", "Edit"),
      unavailableButton("relationship-delete", "Delete"),
    ]);
  }, "relationships", desired);
  content.append(relationships);
}

export function allViews(catalog) {
  if (!catalog) return [];
  return [
    ...catalog.views.map(view => ({ ...view, catalogKind: "view" })),
    ...catalog.materializedViews.map(view => ({ ...view, catalogKind: "materialized_view" })),
  ].sort((left, right) => left.name.localeCompare(right.name));
}

export function renderViewsList(container, { catalog, query = "", filter = "all", selectedName, onSelect }) {
  replace(container);
  if (!catalog) {
    container.append(emptyPanel("VIEW", "No workspace loaded", "Open a workspace to browse its views."));
    return [];
  }
  const desired = catalog.source === "design";
  const needle = normalizedSearch(query);
  const views = allViews(catalog).filter(view => (filter === "all" || view.catalogKind === filter) && (!needle || `${view.namespace}.${view.name}`.toLocaleLowerCase().includes(needle)));
  if (!views.length) {
    container.append(emptyPanel("0", "No matching views", query || filter !== "all" ? "No views match this filter." : desired ? "This design has no ordinary or materialized views yet." : "The live catalog reported no ordinary or materialized views."));
    return views;
  }
  for (const view of views.slice(0, MAX_BROWSER_ITEMS)) {
    const button = element("button", {
      className: `view-list-button${view.name === selectedName ? " active" : ""}`,
      type: "button",
      attrs: { "aria-pressed": view.name === selectedName ? "true" : "false" },
    });
    const detail = desired
      ? `${view.catalogKind === "view" ? "Ordinary view" : "Materialized view"} · designed query`
      : `${view.catalogKind === "view" ? "Ordinary view" : "Materialized view"} · ${view.columns.length} ${view.columns.length === 1 ? "column" : "columns"}`;
    button.append(element("strong", { text: view.name, title: view.name }), element("span", { text: detail }));
    button.addEventListener("click", () => onSelect(view));
    container.append(button);
  }
  if (views.length > MAX_BROWSER_ITEMS) container.append(element("p", { className: "none-reported", text: `Showing the first ${MAX_BROWSER_ITEMS} of ${views.length} matching views. Refine the search to narrow the list.` }));
  return views;
}

export function renderViewDetail(container, view) {
  replace(container);
  if (!view) {
    container.append(emptyPanel("VIEW", "No view selected", "Select an ordinary or materialized view from the live catalog."));
    return;
  }
  const head = element("header", { className: "view-detail-head" });
  const title = element("div");
  title.append(element("span", { className: "eyebrow", text: `${view.namespace} · live PostgreSQL definition` }), element("h2", { text: view.name }));
  head.append(title, element("span", { className: "view-kind", text: view.catalogKind === "view" ? "Ordinary view" : "Materialized view" }));
  container.append(head);

  if (view.catalogKind === "materialized_view") {
    const status = element("section", { className: "view-columns" });
    status.append(metadataGrid([["Populated", view.populated], ["Namespace", view.namespace]]));
    container.append(status);
  }
  const columns = element("section", { className: "view-columns" });
  columns.append(element("h3", { text: `Output columns · ${view.columns.length}` }));
  const grid = element("div", { className: "view-column-grid" });
  for (const column of view.columns) {
    const item = element("div", { className: "view-column" });
    item.append(element("strong", { text: column.name }), element("code", { text: column.dataType }));
    grid.append(item);
  }
  if (!view.columns.length) grid.append(element("p", { className: "none-reported", text: "No output columns were reported." }));
  columns.append(grid);
  container.append(columns);

  const definition = element("section", { className: "definition-panel" });
  definition.append(element("h3", { text: "Read-only query definition" }), element("pre", { text: view.queryDefinition }));
  container.append(definition);
}

export function renderFunctions(container, catalog, query = "", { onEdit = null, onDelete = null } = {}) {
  replace(container);
  if (!catalog) {
    container.append(emptyPanel("FN", "No catalog loaded", "Open a workspace to browse functions and procedures."));
    return { shown: 0, matching: 0 };
  }
  const needle = normalizedSearch(query);
  const routines = catalog.functions.filter(routine => !needle || `${routine.namespace}.${routine.name} ${routine.language} ${routine.kind} ${routine.arguments} ${routine.returnType || ""} ${routine.definition}`.toLocaleLowerCase().includes(needle));
  if (!routines.length) {
    const source = catalog.source === "design" ? "design contains" : "live catalog reported";
    container.append(emptyPanel("0", "No matching routines", query ? "No routine matches this search." : `The ${source} no functions or procedures.`));
    return { shown: 0, matching: 0 };
  }
  const visible = routines.slice(0, MAX_BROWSER_ITEMS);
  for (const routine of visible) {
    const wrapper = element("details", { className: "catalog-object" });
    const summary = element("summary");
    const identity = element("span");
    identity.append(element("strong", { text: `${routine.namespace}.${routine.name}(${routine.identityArguments})` }), element("small", { text: `${routine.kind} · ${routine.language}` }));
    summary.append(identity, element("span", { className: "object-kind", text: routine.kind }));
    const body = element("div", { className: "catalog-object-body" });
    const metadata = element("dl");
    for (const [label, value] of [["Arguments", routine.arguments], ["Returns", routine.returnType], ["Language", routine.language]]) metadata.append(element("dt", { text: label }), element("dd", { text: valueText(value) }));
    body.append(metadata, element("pre", { className: "routine-definition", text: routine.definition }));
    if (routine.designId && (onEdit || onDelete)) {
      const actions = element("div", { className: "catalog-object-actions" });
      if (onEdit) {
        const edit = element("button", { className: "ui-button compact", type: "button", text: "Edit source" });
        edit.addEventListener("click", () => onEdit(routine));
        actions.append(edit);
      }
      if (onDelete) {
        const remove = element("button", { className: "ui-button compact danger-text", type: "button", text: "Delete" });
        remove.addEventListener("click", () => onDelete(routine));
        actions.append(remove);
      }
      body.append(actions);
    }
    wrapper.append(summary, body);
    container.append(wrapper);
  }
  if (routines.length > visible.length) container.append(element("p", { className: "none-reported", text: `Showing the first ${visible.length} of ${routines.length} matching routines. Refine the search to narrow the list.` }));
  return { shown: visible.length, matching: routines.length };
}

export function renderTypes(container, catalog, query = "", filter = "all", { onEdit = null, onDelete = null } = {}) {
  replace(container);
  if (!catalog) {
    container.append(emptyPanel("TYPE", "No catalog loaded", "Open a detached design to browse its custom types."));
    return { shown: 0, matching: 0, total: 0 };
  }
  const available = catalog.types || [];
  const needle = normalizedSearch(query);
  const types = available.filter(designType => {
    if (filter !== "all" && designType.kind !== filter) return false;
    const checks = (designType.checks || []).map(check => `${check.name || ""} ${check.expression}`).join(" ");
    const searchable = `${designType.kind} ${designType.name} ${(designType.enumValues || []).join(" ")} ${designType.baseType || ""} ${designType.defaultExpression || ""} ${checks} ${designType.definition}`;
    return !needle || searchable.toLocaleLowerCase().includes(needle);
  });
  if (!types.length) {
    const message = query || filter !== "all"
      ? "No custom types match this search and filter."
      : catalog.source === "design"
        ? "This design has no enums or domains yet."
        : "Custom-type browsing is not available for this live catalog.";
    container.append(emptyPanel("0", "No matching types", message));
    return { shown: 0, matching: 0, total: available.length };
  }

  const visible = types.slice(0, MAX_BROWSER_ITEMS);
  for (const kind of ["enum", "domain"]) {
    const group = visible.filter(designType => designType.kind === kind);
    if (!group.length) continue;
    const section = element("section", { className: "catalog-object-group" });
    section.append(element("h3", { text: `${kind === "enum" ? "Enums" : "Domains"} · ${group.length}` }));
    for (const designType of group) {
      const wrapper = element("details", { className: "catalog-object type-object" });
      const summary = element("summary");
      const identity = element("span");
      const summaryDetail = kind === "enum"
        ? `${designType.enumValues.length} ${designType.enumValues.length === 1 ? "value" : "values"}`
        : `Based on ${designType.baseType}`;
      identity.append(
        element("strong", { text: designType.name }),
        element("small", { text: summaryDetail }),
      );
      summary.append(identity, element("span", { className: "object-kind", text: kind }));
      const body = element("div", { className: "catalog-object-body" });
      if (kind === "enum") {
        body.append(element("div", { className: "type-enum-values" }, designType.enumValues.map(value => (
          element("code", { text: value, title: value })
        ))));
      } else {
        body.append(metadataGrid([
          ["Base type", designType.baseType],
          ["Default", designType.defaultExpression],
          ["Not null", designType.notNull],
          ["Collation", designType.collation],
        ]));
        if (designType.checks.length) {
          const checks = element("div", { className: "type-domain-checks" });
          checks.append(element("strong", { text: `Checks · ${designType.checks.length}` }));
          for (const check of designType.checks) {
            checks.append(element("code", { text: `${check.name ? `${check.name}: ` : ""}${check.expression}` }));
          }
          body.append(checks);
        }
      }
      body.append(element("pre", { className: "routine-definition", text: designType.definition }));
      if (designType.designId && (onEdit || onDelete)) {
        const actions = element("div", { className: "catalog-object-actions" });
        if (onEdit) {
          const edit = element("button", { className: "ui-button compact", type: "button", text: "Edit source" });
          edit.addEventListener("click", () => onEdit(designType));
          actions.append(edit);
        }
        if (onDelete) {
          const remove = element("button", { className: "ui-button compact danger-text", type: "button", text: "Delete" });
          remove.addEventListener("click", () => onDelete(designType));
          actions.append(remove);
        }
        body.append(actions);
      }
      wrapper.append(summary, body);
      section.append(wrapper);
    }
    container.append(section);
  }
  if (types.length > visible.length) container.append(element("p", { className: "none-reported", text: `Showing the first ${visible.length} of ${types.length} matching types. Refine the search to narrow the list.` }));
  return { shown: visible.length, matching: types.length, total: available.length };
}

function objectDescriptors(catalog) {
  const objects = [];
  const hasTopLevelTriggers = Array.isArray(catalog.triggers);
  for (const table of catalog.tables) {
    objects.push({ kind: table.kind, name: `${table.namespace}.${table.name}`, meta: `${table.columns.length} columns`, target: "table", table: table.name });
    if (table.primaryKey) objects.push({ kind: "primary key", name: table.primaryKey.name, meta: table.primaryKey.definition, target: "table", table: table.name });
    for (const constraint of [...table.uniqueConstraints, ...table.checks, ...table.notNullConstraints, ...table.exclusionConstraints]) objects.push({ kind: "constraint", name: constraint.name, meta: constraint.definition, target: "table", table: table.name });
    for (const index of table.indexes) objects.push({ kind: "index", name: index.name, meta: index.definition, target: "table", table: table.name });
    if (!hasTopLevelTriggers) {
      for (const trigger of table.triggers) objects.push({ kind: "trigger", name: trigger.name, meta: trigger.definition, target: "table", table: table.name });
    }
  }
  for (const view of allViews(catalog)) objects.push({ kind: view.catalogKind, name: `${view.namespace}.${view.name}`, meta: `${view.columns.length} columns`, target: "view", view });
  for (const routine of catalog.functions) objects.push({ kind: routine.kind, name: `${routine.namespace}.${routine.name}(${routine.identityArguments})`, meta: routine.language, target: "routine" });
  for (const trigger of catalog.triggers || []) objects.push({
    kind: "trigger",
    name: `${trigger.relationName}.${trigger.name}`,
    meta: `${trigger.timing.replaceAll("_", " ")} ${trigger.events.join(" or ")} · ${trigger.orientation}`,
    target: "trigger",
    trigger,
  });
  for (const designType of catalog.types || []) objects.push({
    kind: designType.kind,
    name: designType.name,
    meta: designType.kind === "enum" ? designType.enumValues.join(", ") : designType.baseType,
    target: "type",
    designType,
  });
  return objects.sort((left, right) => left.kind.localeCompare(right.kind) || left.name.localeCompare(right.name));
}

export function renderObjects(container, catalog, query = "", onOpen) {
  replace(container);
  if (!catalog) {
    container.append(emptyPanel("DB", "No catalog loaded", "Open a workspace to browse its database objects."));
    return { shown: 0, matching: 0 };
  }
  const needle = normalizedSearch(query);
  const objects = objectDescriptors(catalog).filter(item => !needle || `${item.kind} ${item.name} ${item.meta}`.toLocaleLowerCase().includes(needle));
  if (!objects.length) {
    container.append(emptyPanel("0", "No matching objects", "No workspace objects match this search."));
    return { shown: 0, matching: 0 };
  }
  const visible = objects.slice(0, MAX_BROWSER_ITEMS);
  for (const object of visible) {
    const wrapper = element("details", { className: "catalog-object" });
    const summary = element("summary");
    const identity = element("span");
    identity.append(element("strong", { text: object.name }), element("small", { text: object.meta }));
    summary.append(identity, element("span", { className: "object-kind", text: object.kind.replaceAll("_", " ") }));
    const body = element("div", { className: "catalog-object-body" });
    body.append(metadataGrid([["Type", object.kind], ["Identity", object.name], ["Catalog detail", object.meta]]));
    if (object.target === "table") {
      const action = element("button", { className: "ui-button compact", type: "button", text: "Show table inspector" });
      action.addEventListener("click", () => onOpen(object));
      body.append(action);
    } else if (object.target === "view") {
      const action = element("button", { className: "ui-button compact", type: "button", text: "Open view definition" });
      action.addEventListener("click", () => onOpen(object));
      body.append(action);
    } else if (object.target === "routine") {
      const action = element("button", { className: "ui-button compact", type: "button", text: "Open routines browser" });
      action.addEventListener("click", () => onOpen(object));
      body.append(action);
    } else if (object.target === "trigger") {
      const action = element("button", { className: "ui-button compact", type: "button", text: "Edit trigger source" });
      action.addEventListener("click", () => onOpen(object));
      body.append(action);
    } else if (object.target === "type") {
      const action = element("button", { className: "ui-button compact", type: "button", text: "Open custom types" });
      action.addEventListener("click", () => onOpen(object));
      body.append(action);
    }
    wrapper.append(summary, body);
    container.append(wrapper);
  }
  if (objects.length > visible.length) container.append(element("p", { className: "none-reported", text: `Showing the first ${visible.length} of ${objects.length} matching objects. Refine the search to narrow the list.` }));
  return { shown: visible.length, matching: objects.length };
}
