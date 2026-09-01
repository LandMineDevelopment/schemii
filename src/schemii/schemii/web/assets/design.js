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

function tableCatalogEntry(table, columns) {
  const keys = table.keys.map(key => ({
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
      name: column.name,
      ordinal: index + 1,
      dataType: column.dataType,
      nullable: column.nullable,
      defaultExpression: column.defaultExpression,
      identity: column.identity,
      generated: null,
      collationSchema: null,
      collationName: null,
    })),
    primaryKey: keys.find((_, index) => table.keys[index].kind === "primary") || null,
    uniqueConstraints: keys.filter((_, index) => table.keys[index].kind === "unique"),
    checks: table.checks.map(check => ({
      name: check.name,
      columns: [],
      definition: check.expression,
      validated: true,
      deferrable: false,
      initiallyDeferred: false,
    })),
    notNullConstraints: [],
    exclusionConstraints: [],
    indexes: table.indexes.map(index => ({
      name: index.name,
      method: index.method,
      unique: index.unique,
      valid: true,
      predicate: index.predicate,
      definition: index.expression || index.columnIds.map(id => columns.get(id)?.name || id).join(", "),
    })),
    triggers: [],
  };
}

export function designToCatalog(workspace, design) {
  const columns = columnMap(design);
  const tables = tableMap(design);
  const views = design.content.views.map(view => ({
    namespace: "desired",
    name: view.name,
    columns: [],
    queryDefinition: view.definition,
    populated: view.populated,
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
    tables: design.content.tables.map(table => tableCatalogEntry(table, columns)),
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
      namespace: "desired",
      name: routine.name,
      kind: routine.kind,
      identityArguments: routine.arguments,
      arguments: routine.arguments,
      returnType: routine.returnType,
      language: routine.language,
      definition: routine.definition,
    })),
    views: views.filter((_, index) => design.content.views[index].kind === "view"),
    materializedViews: views.filter((_, index) => design.content.views[index].kind === "materialized_view"),
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
  }));
  if (columns.some(column => !column.name)) throw new Error("Every column needs a name.");
  if (columns.some(column => byteLength(column.name) > 63)) throw new Error("Column names must be at most 63 UTF-8 bytes.");
  if (columns.some(column => !column.dataType)) throw new Error("Every column needs a PostgreSQL data type.");
  if (new Set(columns.map(column => column.name)).size !== columns.length) throw new Error("Column names must be unique within the table.");
  if (columns.filter(column => column.id).length !== new Set(columns.filter(column => column.id).map(column => column.id)).size) {
    throw new Error("A column cannot appear more than once.");
  }
  return columns;
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
    return {
      id: existing?.id || id("column", randomUUID),
      name: column.name,
      dataType: column.dataType,
      nullable: column.nullable,
      defaultExpression: existing?.defaultExpression ?? null,
      identity: existing?.identity ?? null,
    };
  });
  const primaryColumnIds = designColumns.filter((_, index) => columns[index].primary).map(column => column.id);
  const existingPrimary = existingTable?.keys.find(key => key.kind === "primary") || null;
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
  return {
    id: existingTable?.id || id("table", randomUUID),
    name: tableName,
    columns: designColumns,
    keys,
    checks: existingTable?.checks || [],
    indexes: existingTable?.indexes || [],
  };
}

function referencedRemovedColumn(content, table, retainedColumnIds) {
  const removed = new Set(table.columns.filter(column => !retainedColumnIds.has(column.id)).map(column => column.id));
  if (!removed.size) return null;
  const key = table.keys.find(item => item.kind !== "primary" && item.columnIds.some(columnId => removed.has(columnId)));
  if (key) return `unique key “${key.name}”`;
  const index = table.indexes.find(item => item.columnIds.some(columnId => removed.has(columnId)));
  if (index) return `index “${index.name}”`;
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
  const duplicateName = content.tables.some(item => item.id !== tableId && item.name === name.trim());
  if (duplicateName) throw new Error("A table with this name already exists in the design.");
  const retainedColumnIds = new Set(columnValues.map(column => column.id).filter(Boolean));
  const reference = referencedRemovedColumn(content, table, retainedColumnIds);
  if (reference) throw new Error(`Remove or update ${reference} before removing its column.`);
  const updated = designTableFromValues(table, name, columnValues, randomUUID);
  const targetKeys = new Set(updated.keys
    .filter(key => key.kind === "primary" || key.kind === "unique")
    .map(key => key.columnIds.join("\u0000")));
  const invalidRelationship = content.relationships.find(relationship => (
    relationship.targetTableId === tableId
    && !targetKeys.has(relationship.targetColumnIds.join("\u0000"))
  ));
  if (invalidRelationship) {
    throw new Error(`Relationship “${invalidRelationship.name}” targets this key. Delete the relationship before changing the key.`);
  }
  return updated;
}

export function createDesignRelationship(content, values, randomUUID = () => crypto.randomUUID()) {
  const relationshipName = validatedName(values.name, "relationship name");
  if (content.relationships.some(item => item.name === relationshipName)) {
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
    id: id("relationship", randomUUID),
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
