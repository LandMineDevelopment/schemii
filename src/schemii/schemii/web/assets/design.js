function columnMap(design) {
  return new Map(design.content.tables.flatMap(table => (
    table.columns.map(column => [column.id, column])
  )));
}

function tableMap(design) {
  return new Map(design.content.tables.map(table => [table.id, table]));
}

function keyDefinition(key, columns) {
  const kind = key.kind === "primary" ? "PRIMARY KEY" : "UNIQUE";
  return `${kind} (${key.columnIds.map(id => columns.get(id)?.name || id).join(", ")})`;
}

function tableCatalogEntry(table, columns, triggers = []) {
  const keys = table.keys.map(key => ({
    designId: key.id,
    name: key.name,
    columns: key.columnIds.map(id => columns.get(id)?.name || id),
    definition: keyDefinition(key, columns),
    validated: true,
    deferrable: false,
    initiallyDeferred: false,
  }));
  return {
    designId: table.id,
    namespace: "desired",
    name: table.name,
    kind: "table",
    isPartition: false,
    partitionKey: null,
    columns: table.columns.map((column, index) => ({
      designId: column.id,
      name: column.name,
      ordinal: index + 1,
      dataType: column.dataType,
      nullable: column.nullable,
      defaultExpression: column.defaultExpression,
      identity: column.identity,
      generated: column.generatedExpression,
      collationSchema: null,
      collationName: null,
    })),
    primaryKey: keys.find((_, index) => table.keys[index].kind === "primary") || null,
    uniqueConstraints: keys.filter((_, index) => table.keys[index].kind === "unique"),
    checks: table.checks.map(check => ({
      designId: check.id,
      name: check.name,
      columns: (check.columnIds || []).map(id => columns.get(id)?.name || id),
      definition: check.expression,
      validated: true,
      deferrable: false,
      initiallyDeferred: false,
    })),
    notNullConstraints: [],
    exclusionConstraints: [],
    indexes: table.indexes.map(index => ({
      designId: index.id,
      name: index.name,
      method: index.method,
      columns: index.columnIds.map(id => columns.get(id)?.name || id),
      unique: index.unique,
      valid: true,
      predicate: index.predicate,
      expression: index.expression,
      definition: [
        ...index.columnIds.map(id => columns.get(id)?.name || id),
        ...(index.expression ? [index.expression] : []),
      ].join(", "),
    })),
    triggers,
  };
}

export function designToCatalog(workspace, design) {
  const columns = columnMap(design);
  const tables = tableMap(design);
  const triggers = (design.content.triggers || []).map(trigger => ({
    designId: trigger.id,
    namespace: "desired",
    name: trigger.name,
    table: trigger.relationName,
    relationName: trigger.relationName,
    timing: trigger.timing,
    events: trigger.events,
    orientation: trigger.orientation,
    functionName: trigger.functionName,
    functionArguments: trigger.functionArguments,
    updateColumns: trigger.updateColumns,
    referencedColumns: trigger.referencedColumns,
    whenExpression: trigger.whenExpression,
    transitionRelations: trigger.transitionRelations,
    constraint: trigger.constraint,
    deferrable: trigger.deferrable,
    initiallyDeferred: trigger.initiallyDeferred,
    enabled: "origin",
    definition: trigger.definition,
  }));
  const views = design.content.views.map(view => ({
    designId: view.id,
    namespace: "desired",
    name: view.name,
    columns: [],
    queryDefinition: view.definition,
    populateOnCreate: view.populateOnCreate,
  }));
  return {
    source: "design",
    database: workspace.name,
    namespace: "desired",
    serverVersion: null,
    serverVersionNum: null,
    serverTimezone: null,
    capturedAt: null,
    fingerprint: design.fingerprint,
    designRevision: design.revision,
    tables: design.content.tables.map(table => tableCatalogEntry(
      table,
      columns,
      triggers.filter(trigger => trigger.relationName === table.name),
    )),
    relationships: design.content.relationships.map(relationship => {
      const source = tables.get(relationship.sourceTableId);
      const target = tables.get(relationship.targetTableId);
      return {
        designId: relationship.id,
        name: relationship.name,
        sourceNamespace: "desired",
        sourceTable: source.name,
        sourceColumns: relationship.sourceColumnIds.map(id => columns.get(id).name),
        targetNamespace: "desired",
        targetTable: target.name,
        targetColumns: relationship.targetColumnIds.map(id => columns.get(id).name),
        onUpdate: relationship.onUpdate,
        onDelete: relationship.onDelete,
        matchType: "SIMPLE",
        validated: true,
        deferrable: relationship.deferrable,
        initiallyDeferred: relationship.initiallyDeferred,
        definition: `${source.name} → ${target.name}`,
      };
    }),
    functions: design.content.functions.map(routine => ({
      designId: routine.id,
      namespace: "desired",
      name: routine.name,
      kind: routine.kind,
      identityArguments: routine.identityArguments,
      arguments: routine.arguments,
      returnType: routine.returnType,
      language: routine.language,
      definition: routine.definition,
    })),
    views: views.filter((_, index) => design.content.views[index].kind === "view"),
    materializedViews: views.filter((_, index) => design.content.views[index].kind === "materialized_view"),
    triggers,
  };
}

export function designPositions(design, layout) {
  const tableNames = new Map(design.content.tables.map(table => [table.id, table.name]));
  return layout.content.objects.flatMap(position => {
    if (position.layer !== "tables" || !tableNames.has(position.objectId)) return [];
    return [{ name: tableNames.get(position.objectId), x: position.x, y: position.y }];
  });
}

export function designLayoutContent(design, positions, existingObjects = []) {
  const tableIds = new Map(design.content.tables.map(table => [table.name, table.id]));
  const objects = existingObjects.filter(position => position.layer !== "tables");
  for (const position of positions) {
    const objectId = tableIds.get(position.name);
    if (objectId) objects.push({ objectId, layer: "tables", x: position.x, y: position.y });
  }
  return { objects };
}

function id(prefix, randomUUID) {
  return `${prefix}_${randomUUID().replaceAll("-", "").toLowerCase()}`;
}

function byteLength(value) {
  return new TextEncoder().encode(value).length;
}

function truncateBytes(value, maximum) {
  let output = "";
  for (const character of value) {
    if (byteLength(output + character) > maximum) break;
    output += character;
  }
  return output;
}

function nameWithSuffix(value, suffix) {
  return `${truncateBytes(value, 63 - byteLength(suffix))}${suffix}`;
}

export function createDesignTable(name, columnValues, randomUUID = () => crypto.randomUUID()) {
  return designTableFromValues(null, name, columnValues, randomUUID);
}

function normalizedColumns(columnValues) {
  if (!columnValues.length) throw new Error("Add at least one column.");
  const columns = columnValues.map(value => ({
    id: value.id || null,
    name: value.name.trim(),
    dataType: value.dataType.trim(),
    nullable: value.primary ? false : Boolean(value.nullable),
    primary: Boolean(value.primary),
    defaultExpression: value.defaultExpression === undefined ? undefined : optionalExpression(value.defaultExpression),
    identity: value.identity === undefined ? undefined : value.identity || null,
    generatedExpression: value.generatedExpression === undefined ? undefined : optionalExpression(value.generatedExpression),
  }));
  if (columns.some(column => !column.name)) throw new Error("Every column needs a name.");
  if (columns.some(column => byteLength(column.name) > 63)) throw new Error("Column names must be at most 63 UTF-8 bytes.");
  if (columns.some(column => !column.dataType)) throw new Error("Every column needs a PostgreSQL data type.");
  for (const column of columns) {
    const behaviors = [column.defaultExpression, column.identity, column.generatedExpression].filter(value => value !== null && value !== undefined);
    if (behaviors.length > 1) throw new Error(`Choose one value behavior for “${column.name}”.`);
    if (column.identity && !["always", "by_default"].includes(column.identity)) throw new Error(`Choose a valid identity behavior for “${column.name}”.`);
    if (column.defaultExpression) validateExpression(column.defaultExpression, `default for “${column.name}”`);
    if (column.generatedExpression) validateExpression(column.generatedExpression, `generated expression for “${column.name}”`);
  }
  if (new Set(columns.map(column => column.name)).size !== columns.length) throw new Error("Column names must be unique within the table.");
  if (columns.filter(column => column.id).length !== new Set(columns.filter(column => column.id).map(column => column.id)).size) {
    throw new Error("A column cannot appear more than once.");
  }
  return columns;
}

function optionalExpression(value) {
  const expression = String(value || "").trim();
  return expression || null;
}

function validateExpression(expression, label) {
  if (["\0", ";", "--", "/*", "*/"].some(boundary => expression.includes(boundary))) {
    throw new Error(`The ${label} contains an unsupported SQL statement boundary.`);
  }
}

function sqlIdentifierTokens(expression) {
  const tokens = [];
  let index = 0;
  while (index < expression.length) {
    const character = expression[index];
    if (character === "'") {
      index += 1;
      while (index < expression.length) {
        if (expression[index] !== "'") { index += 1; continue; }
        if (expression[index + 1] === "'") { index += 2; continue; }
        index += 1;
        break;
      }
      continue;
    }
    if (character === "$") {
      const delimiter = expression.slice(index).match(/^\$[A-Za-z_0-9]*\$/)?.[0];
      if (delimiter) {
        const closing = expression.indexOf(delimiter, index + delimiter.length);
        index = closing < 0 ? expression.length : closing + delimiter.length;
        continue;
      }
    }
    if (character === '"') {
      const start = index;
      let name = "";
      index += 1;
      while (index < expression.length) {
        if (expression[index] !== '"') { name += expression[index]; index += 1; continue; }
        if (expression[index + 1] === '"') { name += '"'; index += 2; continue; }
        index += 1;
        break;
      }
      tokens.push({ start, end: index, name, quoted: true, call: false });
      continue;
    }
    if (/[A-Za-z_]/.test(character)) {
      const start = index;
      index += 1;
      while (index < expression.length && /[A-Za-z0-9_$]/.test(expression[index])) index += 1;
      let lookahead = index;
      while (/\s/.test(expression[lookahead] || "")) lookahead += 1;
      tokens.push({
        start,
        end: index,
        name: expression.slice(start, index),
        quoted: false,
        call: expression[lookahead] === "(",
      });
      continue;
    }
    index += 1;
  }
  return tokens;
}

function tokenReferencesColumn(token, columnName) {
  if (token.call) return false;
  return token.quoted ? token.name === columnName : token.name.toLowerCase() === columnName;
}

export function expressionColumnIds(expression, columns) {
  const identifiers = sqlIdentifierTokens(expression);
  return columns
    .filter(column => identifiers.some(token => tokenReferencesColumn(token, column.name)))
    .map(column => column.id);
}

function quotedIdentifier(name) {
  return `"${name.replaceAll('"', '""')}"`;
}

function rewriteColumnReferences(expression, oldColumns, newColumns, referencedColumnIds) {
  if (!expression) return expression;
  const oldById = new Map(oldColumns.map(column => [column.id, column]));
  const newById = new Map(newColumns.map(column => [column.id, column]));
  const renames = [...newById.entries()].flatMap(([columnId, column]) => {
    const old = oldById.get(columnId);
    return old && old.name !== column.name && referencedColumnIds.has(columnId)
      ? [{ columnId, oldName: old.name, newName: column.name }]
      : [];
  });
  if (!renames.length) return expression;
  const replacements = [];
  for (const token of sqlIdentifierTokens(expression)) {
    const rename = renames.find(item => tokenReferencesColumn(token, item.oldName));
    if (!rename) continue;
    const replacement = token.quoted || !/^[a-z_][a-z0-9_$]*$/.test(rename.newName)
      ? quotedIdentifier(rename.newName)
      : rename.newName;
    replacements.push({ start: token.start, end: token.end, replacement });
  }
  let rewritten = expression;
  for (const item of replacements.reverse()) {
    rewritten = `${rewritten.slice(0, item.start)}${item.replacement}${rewritten.slice(item.end)}`;
  }
  return rewritten;
}

function validatedName(value, label) {
  const name = value.trim();
  if (!name) throw new Error(`Enter a ${label}.`);
  if (byteLength(name) > 63) throw new Error(`The ${label} must be at most 63 UTF-8 bytes.`);
  return name;
}

function designTableFromValues(existingTable, name, columnValues, randomUUID) {
  const tableName = validatedName(name, "table name");
  const columns = normalizedColumns(columnValues);
  const existingColumns = new Map((existingTable?.columns || []).map(column => [column.id, column]));
  for (const column of columns) {
    if (column.id && !existingColumns.has(column.id)) throw new Error("The table changed while this editor was open. Reload the design and try again.");
  }

  const designColumns = columns.map(column => {
    const existing = existingColumns.get(column.id);
    const identity = column.identity === undefined ? existing?.identity ?? null : column.identity;
    return {
      id: existing?.id || id("column", randomUUID),
      name: column.name,
      dataType: column.dataType,
      nullable: identity ? false : column.nullable,
      defaultExpression: column.defaultExpression === undefined ? existing?.defaultExpression ?? null : column.defaultExpression,
      identity,
      generatedExpression: column.generatedExpression === undefined ? existing?.generatedExpression ?? null : column.generatedExpression,
      generatedSourceColumnIds: [],
    };
  });
  for (const column of designColumns) {
    if (!column.generatedExpression) continue;
    const existing = existingColumns.get(column.id);
    const previousReferences = new Set(
      existing?.generatedSourceColumnIds?.length
        ? existing.generatedSourceColumnIds
        : expressionColumnIds(existing?.generatedExpression || column.generatedExpression, existingTable?.columns || designColumns),
    );
    column.generatedExpression = rewriteColumnReferences(
      column.generatedExpression,
      existingTable?.columns || designColumns,
      designColumns,
      previousReferences,
    );
    column.generatedSourceColumnIds = expressionColumnIds(column.generatedExpression, designColumns);
    if (column.generatedSourceColumnIds.includes(column.id)) {
      throw new Error(`Generated column “${column.name}” cannot reference itself.`);
    }
  }
  const generatedColumnIds = new Set(
    designColumns.filter(column => column.generatedExpression).map(column => column.id),
  );
  for (const column of designColumns) {
    if (!column.generatedExpression) continue;
    if (column.generatedSourceColumnIds.some(columnId => generatedColumnIds.has(columnId))) {
      throw new Error(`Generated column “${column.name}” cannot reference another generated column.`);
    }
  }
  const existingPrimary = existingTable?.keys.find(key => key.kind === "primary") || null;
  const selectedPrimaryColumnIds = designColumns.filter((_, index) => columns[index].primary).map(column => column.id);
  const selectedPrimarySet = new Set(selectedPrimaryColumnIds);
  const primaryColumnIds = existingPrimary
    ? [
        ...existingPrimary.columnIds.filter(columnId => selectedPrimarySet.has(columnId)),
        ...selectedPrimaryColumnIds.filter(columnId => !existingPrimary.columnIds.includes(columnId)),
      ]
    : selectedPrimaryColumnIds;
  const otherKeys = (existingTable?.keys || []).filter(key => key.kind !== "primary");
  const keys = [...otherKeys];
  if (primaryColumnIds.length) {
    keys.unshift({
      id: existingPrimary?.id || id("key", randomUUID),
      name: existingPrimary?.name || `${truncateBytes(tableName, 58)}_pkey`,
      kind: "primary",
      columnIds: primaryColumnIds,
    });
  }
  const checks = (existingTable?.checks || []).map(check => {
    const previousReferences = new Set(
      check.columnIds?.length
        ? check.columnIds
        : expressionColumnIds(check.expression, existingTable.columns),
    );
    const expression = rewriteColumnReferences(
      check.expression,
      existingTable.columns,
      designColumns,
      previousReferences,
    );
    return {
      ...check,
      expression,
      columnIds: expressionColumnIds(expression, designColumns),
    };
  });
  const indexes = (existingTable?.indexes || []).map(index => {
    const expressionReferences = new Set(
      index.expressionSourceColumnIds?.length
        ? index.expressionSourceColumnIds
        : expressionColumnIds(index.expression || "", existingTable.columns),
    );
    const predicateReferences = new Set(
      index.predicateColumnIds?.length
        ? index.predicateColumnIds
        : expressionColumnIds(index.predicate || "", existingTable.columns),
    );
    const expression = rewriteColumnReferences(
      index.expression,
      existingTable.columns,
      designColumns,
      expressionReferences,
    );
    const predicate = rewriteColumnReferences(
      index.predicate,
      existingTable.columns,
      designColumns,
      predicateReferences,
    );
    return {
      ...index,
      expression,
      expressionSourceColumnIds: expressionColumnIds(expression || "", designColumns),
      predicate,
      predicateColumnIds: expressionColumnIds(predicate || "", designColumns),
    };
  });
  return {
    id: existingTable?.id || id("table", randomUUID),
    name: tableName,
    columns: designColumns,
    keys,
    checks,
    indexes,
  };
}

function referencedRemovedColumn(content, table, retainedColumnIds) {
  const removed = new Set(table.columns.filter(column => !retainedColumnIds.has(column.id)).map(column => column.id));
  if (!removed.size) return null;
  const key = table.keys.find(item => item.kind !== "primary" && item.columnIds.some(columnId => removed.has(columnId)));
  if (key) return `unique key “${key.name}”`;
  const index = table.indexes.find(item => (
    item.columnIds.some(columnId => removed.has(columnId))
    || (item.expressionSourceColumnIds?.length
      ? item.expressionSourceColumnIds
      : expressionColumnIds(item.expression || "", table.columns)
    ).some(columnId => removed.has(columnId))
    || (item.predicateColumnIds?.length
      ? item.predicateColumnIds
      : expressionColumnIds(item.predicate || "", table.columns)
    ).some(columnId => removed.has(columnId))
  ));
  if (index) return `index “${index.name}”`;
  const generated = table.columns.find(item => (
    (item.generatedSourceColumnIds?.length
      ? item.generatedSourceColumnIds
      : expressionColumnIds(item.generatedExpression || "", table.columns)
    ).some(columnId => removed.has(columnId))
  ));
  if (generated) return `generated column “${generated.name}”`;
  const check = table.checks.find(item => (
    (item.columnIds?.length ? item.columnIds : expressionColumnIds(item.expression, table.columns))
      .some(columnId => removed.has(columnId))
  ));
  if (check) return `check constraint “${check.name}”`;
  const relationship = content.relationships.find(item => (
    (item.sourceTableId === table.id && item.sourceColumnIds.some(columnId => removed.has(columnId)))
    || (item.targetTableId === table.id && item.targetColumnIds.some(columnId => removed.has(columnId)))
  ));
  if (relationship) return `relationship “${relationship.name}”`;
  return null;
}

export function updateDesignTable(content, tableId, name, columnValues, randomUUID = () => crypto.randomUUID()) {
  const table = content.tables.find(item => item.id === tableId);
  if (!table) throw new Error("The selected table is no longer in this design.");
  if (table.name !== name.trim() && (content.triggers || []).some(trigger => trigger.relationName === table.name)) {
    throw new Error(`Delete or retarget the triggers on “${table.name}” before renaming it.`);
  }
  const duplicateName = content.tables.some(item => item.id !== tableId && item.name === name.trim());
  if (duplicateName) throw new Error("A table with this name already exists in the design.");
  const retainedColumnIds = new Set(columnValues.map(column => column.id).filter(Boolean));
  const reference = referencedRemovedColumn(content, table, retainedColumnIds);
  if (reference) throw new Error(`Remove or update ${reference} before removing its column.`);
  const updated = designTableFromValues(table, name, columnValues, randomUUID);
  const updatedColumns = new Map(updated.columns.map(column => [column.id, column]));
  const referencedRename = (content.triggers || []).find(trigger => (
    trigger.relationName === table.name
    && table.columns.some(column => (
      trigger.referencedColumns?.includes(column.name)
      && updatedColumns.get(column.id)?.name !== column.name
    ))
  ));
  if (referencedRename) {
    throw new Error(`Update or delete trigger “${referencedRename.name}” before renaming one of its referenced columns.`);
  }
  const invalidRelationship = relationshipWithoutTargetKey(content, tableId, updated.keys);
  if (invalidRelationship) {
    throw new Error(`Relationship “${invalidRelationship.name}” targets this key. Delete the relationship before changing the key.`);
  }
  return updated;
}

function relationshipWithoutTargetKey(content, tableId, keys) {
  const targetKeys = new Set(keys
    .filter(key => key.kind === "primary" || key.kind === "unique")
    .map(key => key.columnIds.join("\u0000")));
  return content.relationships.find(relationship => (
    relationship.targetTableId === tableId
    && !targetKeys.has(relationship.targetColumnIds.join("\u0000"))
  ));
}

export function suggestDesignKeyName(content, { tableId, kind, columnIds, keyId = null }) {
  const table = content.tables.find(item => item.id === tableId);
  if (!table) return "";
  const columns = new Map(table.columns.map(column => [column.id, column.name]));
  const base = kind === "primary"
    ? nameWithSuffix(table.name, "_pkey")
    : nameWithSuffix(`${table.name}_${columnIds.map(columnId => columns.get(columnId)).filter(Boolean).join("_")}`, "_key");
  const used = new Set([
    ...table.keys.filter(key => key.id !== keyId).map(key => key.name),
    ...table.checks.map(check => check.name),
  ]);
  if (!used.has(base)) return base;
  for (let index = 2; index < 10_000; index += 1) {
    const candidate = nameWithSuffix(base, `_${index}`);
    if (!used.has(candidate)) return candidate;
  }
  return base;
}

export function saveDesignKey(content, values, randomUUID = () => crypto.randomUUID()) {
  const table = content.tables.find(item => item.id === values.tableId);
  if (!table) throw new Error("The selected table is no longer in this design.");
  const existing = values.keyId ? table.keys.find(key => key.id === values.keyId) : null;
  if (values.keyId && !existing) throw new Error("The selected key is no longer in this design.");
  if (!["primary", "unique"].includes(values.kind)) throw new Error("Choose a primary or unique key.");
  const name = validatedName(values.name, "key name");
  const columnIds = Array.isArray(values.columnIds) ? [...values.columnIds] : [];
  const ownedColumnIds = new Set(table.columns.map(column => column.id));
  if (!columnIds.length || columnIds.some(columnId => !ownedColumnIds.has(columnId))) {
    throw new Error("Select at least one column from this table.");
  }
  if (new Set(columnIds).size !== columnIds.length) throw new Error("Each column can appear only once in a key.");
  const duplicateName = [...table.keys, ...table.checks].some(item => item.id !== values.keyId && item.name === name);
  if (duplicateName) throw new Error("A constraint with this name already exists on the table.");
  if (values.kind === "primary" && table.keys.some(key => key.id !== values.keyId && key.kind === "primary")) {
    throw new Error("This table already has a primary key.");
  }
  const duplicateShape = table.keys.some(key => (
    key.id !== values.keyId
    && key.kind === values.kind
    && key.columnIds.join("\u0000") === columnIds.join("\u0000")
  ));
  if (duplicateShape) throw new Error(`This table already has the same ${values.kind} key.`);

  const key = {
    id: existing?.id || id("key", randomUUID),
    name,
    kind: values.kind,
    columnIds,
  };
  const revised = structuredClone(content);
  const revisedTable = revised.tables.find(item => item.id === table.id);
  if (existing) revisedTable.keys = revisedTable.keys.map(item => item.id === existing.id ? key : item);
  else revisedTable.keys.push(key);
  if (key.kind === "primary") {
    const primaryIds = new Set(key.columnIds);
    for (const column of revisedTable.columns) {
      if (primaryIds.has(column.id)) column.nullable = false;
    }
  }
  const invalidRelationship = relationshipWithoutTargetKey(revised, table.id, revisedTable.keys);
  if (invalidRelationship) {
    throw new Error(`Relationship “${invalidRelationship.name}” targets this key. Delete the relationship before changing the key columns.`);
  }
  return { content: revised, key };
}

export function deleteDesignKey(content, tableId, keyId) {
  const table = content.tables.find(item => item.id === tableId);
  const key = table?.keys.find(item => item.id === keyId);
  if (!table || !key) throw new Error("The selected key is no longer in this design.");
  const revised = structuredClone(content);
  const revisedTable = revised.tables.find(item => item.id === table.id);
  revisedTable.keys = revisedTable.keys.filter(item => item.id !== key.id);
  const invalidRelationship = relationshipWithoutTargetKey(revised, table.id, revisedTable.keys);
  if (invalidRelationship) {
    throw new Error(`Relationship “${invalidRelationship.name}” targets this key. Delete the relationship before deleting it.`);
  }
  return { content: revised, key };
}

export function suggestDesignCheckName(content, { tableId, expression = "", checkId = null }) {
  const table = content.tables.find(item => item.id === tableId);
  if (!table) return "";
  const columnIds = expressionColumnIds(expression, table.columns);
  const columnNames = new Map(table.columns.map(column => [column.id, column.name]));
  const stem = columnIds.length
    ? `${table.name}_${columnIds.map(columnId => columnNames.get(columnId)).join("_")}`
    : table.name;
  const base = nameWithSuffix(stem, "_check");
  const used = new Set([
    ...table.keys.map(key => key.name),
    ...table.checks.filter(check => check.id !== checkId).map(check => check.name),
  ]);
  if (!used.has(base)) return base;
  for (let index = 2; index < 10_000; index += 1) {
    const candidate = nameWithSuffix(stem, `_check_${index}`);
    if (!used.has(candidate)) return candidate;
  }
  return base;
}

export function saveDesignCheck(content, values, randomUUID = () => crypto.randomUUID()) {
  const table = content.tables.find(item => item.id === values.tableId);
  if (!table) throw new Error("The selected table is no longer in this design.");
  const existing = values.checkId ? table.checks.find(check => check.id === values.checkId) : null;
  if (values.checkId && !existing) throw new Error("The selected check is no longer in this design.");
  const name = validatedName(values.name, "check name");
  const expression = String(values.expression || "").trim();
  if (!expression) throw new Error("Enter the condition this check must enforce.");
  if (/^CHECK\s*\(/i.test(expression)) throw new Error("Enter only the condition; Schemii adds CHECK (…) during export.");
  validateExpression(expression, `check “${name}”`);
  const duplicateName = [...table.keys, ...table.checks].some(item => item.id !== values.checkId && item.name === name);
  if (duplicateName) throw new Error("A constraint with this name already exists on the table.");
  const check = {
    id: existing?.id || id("check", randomUUID),
    name,
    expression,
    columnIds: expressionColumnIds(expression, table.columns),
  };
  const revised = structuredClone(content);
  const revisedTable = revised.tables.find(item => item.id === table.id);
  if (existing) revisedTable.checks = revisedTable.checks.map(item => item.id === existing.id ? check : item);
  else revisedTable.checks.push(check);
  return { content: revised, check };
}

export function deleteDesignCheck(content, tableId, checkId) {
  const table = content.tables.find(item => item.id === tableId);
  const check = table?.checks.find(item => item.id === checkId);
  if (!table || !check) throw new Error("The selected check is no longer in this design.");
  const revised = structuredClone(content);
  const revisedTable = revised.tables.find(item => item.id === table.id);
  revisedTable.checks = revisedTable.checks.filter(item => item.id !== check.id);
  return { content: revised, check };
}

export function suggestDesignIndexName(content, {
  tableId,
  columnIds = [],
  expression = "",
  indexId = null,
}) {
  const table = content.tables.find(item => item.id === tableId);
  if (!table) return "";
  const names = new Map(table.columns.map(column => [column.id, column.name]));
  const expressionIds = expressionColumnIds(expression, table.columns);
  const parts = [...columnIds, ...expressionIds]
    .filter((columnId, index, values) => values.indexOf(columnId) === index)
    .map(columnId => names.get(columnId))
    .filter(Boolean);
  const stem = parts.length ? `${table.name}_${parts.join("_")}` : table.name;
  const base = nameWithSuffix(stem, "_idx");
  const used = new Set(table.indexes.filter(index => index.id !== indexId).map(index => index.name));
  if (!used.has(base)) return base;
  for (let index = 2; index < 10_000; index += 1) {
    const candidate = nameWithSuffix(stem, `_idx_${index}`);
    if (!used.has(candidate)) return candidate;
  }
  return base;
}

export function saveDesignIndex(content, values, randomUUID = () => crypto.randomUUID()) {
  const table = content.tables.find(item => item.id === values.tableId);
  if (!table) throw new Error("The selected table is no longer in this design.");
  const existing = values.indexId ? table.indexes.find(index => index.id === values.indexId) : null;
  if (values.indexId && !existing) throw new Error("The selected index is no longer in this design.");
  const name = validatedName(values.name, "index name");
  const method = validatedName(values.method || "btree", "index method");
  const columnIds = Array.isArray(values.columnIds) ? [...values.columnIds] : [];
  const ownedColumnIds = new Set(table.columns.map(column => column.id));
  if (columnIds.some(columnId => !ownedColumnIds.has(columnId))) {
    throw new Error("Index columns must come from their own table.");
  }
  if (new Set(columnIds).size !== columnIds.length) throw new Error("Each column can appear only once in an index.");
  const expression = optionalExpression(values.expression);
  const predicate = optionalExpression(values.predicate);
  if (!columnIds.length && !expression) throw new Error("Select a column or enter an index expression.");
  if (expression) validateExpression(expression, `expression for index “${name}”`);
  if (predicate) validateExpression(predicate, `predicate for index “${name}”`);
  if (table.indexes.some(index => index.id !== values.indexId && index.name === name)) {
    throw new Error("An index with this name already exists on the table.");
  }
  const duplicateShape = table.indexes.some(index => (
    index.id !== values.indexId
    && index.method === method
    && index.unique === Boolean(values.unique)
    && index.columnIds.join("\u0000") === columnIds.join("\u0000")
    && (index.expression || "") === (expression || "")
    && (index.predicate || "") === (predicate || "")
  ));
  if (duplicateShape) throw new Error("This table already has the same index definition.");
  const index = {
    id: existing?.id || id("index", randomUUID),
    name,
    method,
    columnIds,
    expression,
    expressionSourceColumnIds: expressionColumnIds(expression || "", table.columns),
    predicate,
    predicateColumnIds: expressionColumnIds(predicate || "", table.columns),
    unique: Boolean(values.unique),
  };
  const revised = structuredClone(content);
  const revisedTable = revised.tables.find(item => item.id === table.id);
  if (existing) revisedTable.indexes = revisedTable.indexes.map(item => item.id === existing.id ? index : item);
  else revisedTable.indexes.push(index);
  return { content: revised, index };
}

export function deleteDesignIndex(content, tableId, indexId) {
  const table = content.tables.find(item => item.id === tableId);
  const index = table?.indexes.find(item => item.id === indexId);
  if (!table || !index) throw new Error("The selected index is no longer in this design.");
  const revised = structuredClone(content);
  const revisedTable = revised.tables.find(item => item.id === table.id);
  revisedTable.indexes = revisedTable.indexes.filter(item => item.id !== index.id);
  return { content: revised, index };
}

function designRelationshipFromValues(content, values, relationshipId, randomUUID) {
  const relationshipName = validatedName(values.name, "relationship name");
  if (content.relationships.some(item => item.id !== relationshipId && item.name === relationshipName)) {
    throw new Error("A relationship with this name already exists in the design.");
  }
  const source = content.tables.find(table => table.id === values.sourceTableId);
  const target = content.tables.find(table => table.id === values.targetTableId);
  if (!source || !target) throw new Error("Choose source and target tables from this design.");
  const targetKey = target.keys.find(key => key.id === values.targetKeyId && ["primary", "unique"].includes(key.kind));
  if (!targetKey) throw new Error("Choose a primary or unique key on the target table.");
  const sourceColumnIds = [...values.sourceColumnIds];
  const sourceIds = new Set(source.columns.map(column => column.id));
  if (sourceColumnIds.length !== targetKey.columnIds.length || sourceColumnIds.some(columnId => !sourceIds.has(columnId))) {
    throw new Error("Map one source column to every target key column.");
  }
  if (new Set(sourceColumnIds).size !== sourceColumnIds.length) throw new Error("Each source column can appear only once in a relationship.");
  if (values.initiallyDeferred && !values.deferrable) throw new Error("Initially deferred relationships must be deferrable.");
  return {
    id: relationshipId || id("relationship", randomUUID),
    name: relationshipName,
    sourceTableId: source.id,
    sourceColumnIds,
    targetTableId: target.id,
    targetColumnIds: [...targetKey.columnIds],
    onUpdate: values.onUpdate,
    onDelete: values.onDelete,
    deferrable: Boolean(values.deferrable),
    initiallyDeferred: Boolean(values.initiallyDeferred),
  };
}

export function createDesignRelationship(content, values, randomUUID = () => crypto.randomUUID()) {
  return designRelationshipFromValues(content, values, null, randomUUID);
}

export function updateDesignRelationship(content, relationshipId, values, randomUUID = () => crypto.randomUUID()) {
  if (!content.relationships.some(relationship => relationship.id === relationshipId)) {
    throw new Error("The selected relationship is no longer in this design.");
  }
  return designRelationshipFromValues(content, values, relationshipId, randomUUID);
}

export function relationshipDraftFromExisting(content, relationshipId) {
  const relationship = content.relationships.find(item => item.id === relationshipId);
  if (!relationship) throw new Error("The selected relationship is no longer in this design.");
  const source = content.tables.find(table => table.id === relationship.sourceTableId);
  const target = content.tables.find(table => table.id === relationship.targetTableId);
  if (!source || !target) throw new Error("The relationship references a table that is no longer in this design.");
  const targetKeys = target.keys.filter(key => key.kind === "primary" || key.kind === "unique");
  const targetKey = targetKeys.find(key => (
    key.columnIds.join("\u0000") === relationship.targetColumnIds.join("\u0000")
  ));
  if (!targetKey) throw new Error("The relationship target no longer matches a primary or unique key.");
  return {
    sourceTableId: source.id,
    sourceColumnId: null,
    sourceColumnIds: [...relationship.sourceColumnIds],
    targetTableId: target.id,
    targetColumnId: null,
    targetColumnIds: [...targetKey.columnIds],
    targetKeyId: targetKey.id,
    eligibleTargetKeyIds: targetKeys.map(key => key.id),
  };
}

export function relationshipDraftFromColumns(content, selection) {
  const source = content.tables.find(table => table.id === selection.sourceTableId);
  const target = content.tables.find(table => table.id === selection.targetTableId);
  if (!source || !target) throw new Error("Choose columns from tables in this design.");
  const sourceColumn = source.columns.find(column => column.id === selection.sourceColumnId);
  const targetColumn = target.columns.find(column => column.id === selection.targetColumnId);
  if (!sourceColumn || !targetColumn) throw new Error("The selected column is no longer in this design.");
  if (source.id === target.id && sourceColumn.id === targetColumn.id) throw new Error("Choose a different referenced column.");
  const eligibleKeys = target.keys.filter(key => (
    (key.kind === "primary" || key.kind === "unique")
    && key.columnIds.includes(targetColumn.id)
  ));
  const targetKey = selection.targetKeyId
    ? eligibleKeys.find(key => key.id === selection.targetKeyId)
    : [...eligibleKeys].sort((left, right) => (
      left.columnIds.length - right.columnIds.length
      || Number(right.kind === "primary") - Number(left.kind === "primary")
    ))[0];
  if (!targetKey) throw new Error("Select a target column that belongs to a primary or unique key.");
  if (source.columns.length < targetKey.columnIds.length) {
    throw new Error(`The source table needs at least ${targetKey.columnIds.length} columns to reference this composite key.`);
  }

  const targetColumns = new Map(target.columns.map(column => [column.id, column]));
  const used = new Set([sourceColumn.id]);
  const selectedIndex = targetKey.columnIds.indexOf(targetColumn.id);
  const sourceColumnIds = targetKey.columnIds.map((targetColumnId, index) => {
    if (index === selectedIndex) return sourceColumn.id;
    const targetName = targetColumns.get(targetColumnId)?.name;
    const match = source.columns.find(column => column.name === targetName && !used.has(column.id))
      || source.columns.find(column => !used.has(column.id));
    used.add(match.id);
    return match.id;
  });
  return {
    sourceTableId: source.id,
    sourceColumnId: sourceColumn.id,
    sourceColumnIds,
    targetTableId: target.id,
    targetColumnId: targetColumn.id,
    targetColumnIds: [...targetKey.columnIds],
    targetKeyId: targetKey.id,
    eligibleTargetKeyIds: eligibleKeys.map(key => key.id),
  };
}

export function alignRelationshipColumnTypes(content, relationship) {
  const source = content.tables.find(table => table.id === relationship.sourceTableId);
  const target = content.tables.find(table => table.id === relationship.targetTableId);
  if (!source || !target) throw new Error("Choose source and target tables from this design.");
  const sourceColumnIds = [...relationship.sourceColumnIds];
  const targetColumnIds = [...relationship.targetColumnIds];
  if (!sourceColumnIds.length || sourceColumnIds.length !== targetColumnIds.length) {
    throw new Error("Map one source column to every target key column.");
  }
  const sourceColumns = new Map(source.columns.map(column => [column.id, column]));
  const targetColumns = new Map(target.columns.map(column => [column.id, column]));
  const changes = sourceColumnIds.map((sourceColumnId, index) => {
    const sourceColumn = sourceColumns.get(sourceColumnId);
    const targetColumn = targetColumns.get(targetColumnIds[index]);
    if (!sourceColumn || !targetColumn) throw new Error("A mapped relationship column is no longer in this design.");
    return {
      sourceTableId: source.id,
      sourceTableName: source.name,
      sourceColumnId: sourceColumn.id,
      sourceColumnName: sourceColumn.name,
      targetTableId: target.id,
      targetTableName: target.name,
      targetColumnId: targetColumn.id,
      targetColumnName: targetColumn.name,
      from: sourceColumn.dataType,
      to: targetColumn.dataType,
    };
  }).filter(change => change.from !== change.to);

  const revised = structuredClone(content);
  const revisedSource = revised.tables.find(table => table.id === source.id);
  const revisedColumns = new Map(revisedSource.columns.map(column => [column.id, column]));
  for (const change of changes) revisedColumns.get(change.sourceColumnId).dataType = change.to;
  return { content: revised, changes };
}

export function saveDesignView(content, values, randomUUID = crypto.randomUUID.bind(crypto)) {
  const name = validatedName(values.name, "view name");
  const definition = String(values.definition || "").trim().replace(/;+\s*$/, "");
  const kind = values.kind === "materialized_view" ? "materialized_view" : "view";
  if (!definition) throw new Error("Enter the SELECT query that defines this view.");
  if (/^create\s+/i.test(definition)) {
    throw new Error("Enter only the SELECT query body. Schemii generates CREATE VIEW and the view name.");
  }
  const existing = values.viewId
    ? content.views.find(view => view.id === values.viewId)
    : null;
  if (values.viewId && !existing) throw new Error("The view changed while this editor was open. Reload the design and try again.");
  if (existing && existing.name !== name && (content.triggers || []).some(trigger => trigger.relationName === existing.name)) {
    throw new Error(`Delete or retarget the triggers on “${existing.name}” before renaming it.`);
  }
  const duplicateTable = content.tables.some(table => table.name === name);
  const duplicateView = content.views.some(view => view.id !== existing?.id && view.name === name);
  if (duplicateTable || duplicateView) throw new Error(`A table or view named “${name}” already exists in the design.`);
  const view = {
    id: existing?.id || id("view", randomUUID),
    name,
    kind,
    definition,
    populateOnCreate: kind === "materialized_view" ? values.populateOnCreate !== false : null,
  };
  const revised = structuredClone(content);
  if (existing) revised.views = revised.views.map(item => item.id === existing.id ? view : item);
  else revised.views.push(view);
  return { content: revised, view };
}

export function deleteDesignView(content, viewId) {
  const view = content.views.find(item => item.id === viewId);
  if (!view) throw new Error("The selected view is no longer in this design.");
  const revised = structuredClone(content);
  revised.views = revised.views.filter(view => view.id !== viewId);
  revised.triggers = (revised.triggers || []).filter(trigger => trigger.relationName !== view.name);
  return { content: revised };
}

export function saveDesignRoutine(content, values, randomUUID = crypto.randomUUID.bind(crypto)) {
  const definition = String(values.definition || "").trim();
  if (!definition) throw new Error("Enter one CREATE FUNCTION or CREATE PROCEDURE statement.");
  const next = structuredClone(content);
  const routineId = values.routineId || id("function", randomUUID);
  const routine = { id: routineId, definition };
  const existingIndex = next.functions.findIndex(item => item.id === routineId);
  if (values.routineId && existingIndex < 0) throw new Error("The selected routine is no longer in this design.");
  if (existingIndex >= 0) next.functions[existingIndex] = routine;
  else next.functions.push(routine);
  return { content: next, routine };
}

export function deleteDesignRoutine(content, routineId) {
  const next = structuredClone(content);
  if (!next.functions.some(routine => routine.id === routineId)) {
    throw new Error("The selected routine is no longer in this design.");
  }
  next.functions = next.functions.filter(routine => routine.id !== routineId);
  return next;
}

export function saveDesignTrigger(content, values, randomUUID = crypto.randomUUID.bind(crypto)) {
  const definition = String(values.definition || "").trim();
  if (!definition) throw new Error("Enter one CREATE TRIGGER statement.");
  const next = structuredClone(content);
  if (!next.triggers) next.triggers = [];
  const triggerId = values.triggerId || id("trigger", randomUUID);
  const trigger = { id: triggerId, definition };
  const existingIndex = next.triggers.findIndex(item => item.id === triggerId);
  if (values.triggerId && existingIndex < 0) throw new Error("The selected trigger is no longer in this design.");
  if (existingIndex >= 0) next.triggers[existingIndex] = trigger;
  else next.triggers.push(trigger);
  return { content: next, trigger };
}

export function deleteDesignTrigger(content, triggerId) {
  const next = structuredClone(content);
  if (!(next.triggers || []).some(trigger => trigger.id === triggerId)) {
    throw new Error("The selected trigger is no longer in this design.");
  }
  next.triggers = next.triggers.filter(trigger => trigger.id !== triggerId);
  return next;
}
