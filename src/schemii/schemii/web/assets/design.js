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
  const tableName = name.trim();
  if (!tableName) throw new Error("Enter a table name.");
  if (byteLength(tableName) > 63) throw new Error("The table name must be at most 63 UTF-8 bytes.");
  if (!columnValues.length) throw new Error("Add at least one column.");
  const columns = columnValues.map(value => ({
    name: value.name.trim(),
    dataType: value.dataType.trim(),
    nullable: value.primary ? false : Boolean(value.nullable),
    primary: Boolean(value.primary),
  }));
  if (columns.some(column => !column.name)) throw new Error("Every column needs a name.");
  if (columns.some(column => byteLength(column.name) > 63)) throw new Error("Column names must be at most 63 UTF-8 bytes.");
  if (columns.some(column => !column.dataType)) throw new Error("Every column needs a PostgreSQL data type.");
  if (new Set(columns.map(column => column.name)).size !== columns.length) throw new Error("Column names must be unique within the table.");

  const designColumns = columns.map(column => ({
    id: id("column", randomUUID),
    name: column.name,
    dataType: column.dataType,
    nullable: column.nullable,
    defaultExpression: null,
    identity: null,
  }));
  const primaryColumnIds = designColumns.filter((_, index) => columns[index].primary).map(column => column.id);
  const keys = primaryColumnIds.length ? [{
    id: id("key", randomUUID),
    name: `${truncateBytes(tableName, 58)}_pkey`,
    kind: "primary",
    columnIds: primaryColumnIds,
  }] : [];
  return {
    id: id("table", randomUUID),
    name: tableName,
    columns: designColumns,
    keys,
    checks: [],
    indexes: [],
  };
}
