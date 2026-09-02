const COLLECTION_KINDS = Object.freeze({
  tables: "table",
  columns: "column",
  keys: "key",
  checks: "check",
  indexes: "index",
  relationships: "relationship",
  views: "view",
  functions: "routine",
  types: "type",
  triggers: "trigger",
});

const ENTITY_COLLECTIONS = new Set(Object.keys(COLLECTION_KINDS));

function sameValue(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function entityTone(kind) {
  return kind === "view" ? "purple" : "amber";
}

function entityScope(kind) {
  return kind === "view" ? "views" : "schema";
}

function ownFields(value) {
  return Object.fromEntries(Object.entries(value).filter(([key]) => (
    key !== "id" && !ENTITY_COLLECTIONS.has(key)
  )));
}

function entityIndex(content) {
  const result = new Map();
  const relations = [
    ...(Array.isArray(content?.tables) ? content.tables : []),
    ...(Array.isArray(content?.views) ? content.views : []),
  ];
  const relationIdByName = new Map(relations.map(value => [value.name, value.id]));
  const relationNameById = new Map(relations.map(value => [value.id, value.name]));

  const visitCollection = (values, collection, parent = null) => {
    if (!Array.isArray(values)) return;
    values.forEach((value, index) => {
      if (!value || typeof value !== "object" || typeof value.id !== "string") return;
      const kind = COLLECTION_KINDS[collection] || value.id.split("_", 1)[0] || "object";
      const relationName = value.relationName
        || relationNameById.get(value.sourceTableId)
        || null;
      const parentId = parent?.id
        || (kind === "relationship" ? value.sourceTableId : null)
        || (kind === "trigger" ? relationIdByName.get(relationName) : null)
        || null;
      const record = {
        id: value.id,
        kind,
        name: value.name || null,
        tone: entityTone(kind),
        scope: entityScope(kind),
        parentId,
        parentKind: parent?.kind || (parentId ? "relation" : null),
        parentName: parent?.name || relationName,
        relationName,
        collection,
        index,
        value,
        fields: ownFields(value),
      };
      result.set(record.id, record);
      for (const [key, nested] of Object.entries(value)) {
        if (ENTITY_COLLECTIONS.has(key)) visitCollection(nested, key, record);
      }
    });
  };

  for (const [collection, values] of Object.entries(content || {})) {
    if (ENTITY_COLLECTIONS.has(collection)) visitCollection(values, collection);
  }
  return result;
}

function mergeTarget(targets, target) {
  const key = `${target.operation}:${target.objectId || ""}:${target.fallbackId || ""}:${target.scope || ""}`;
  const existing = targets.get(key);
  if (!existing) {
    targets.set(key, { ...target, fields: [...new Set(target.fields || [])] });
    return;
  }
  existing.fields = [...new Set([...(existing.fields || []), ...(target.fields || [])])];
}

function changedFields(before, after) {
  const fields = new Set([...Object.keys(before.fields), ...Object.keys(after.fields)]);
  return [...fields].filter(field => !sameValue(before.fields[field], after.fields[field]));
}

function relatedColumnField(record) {
  if (record.kind === "key") return record.value.kind === "primary" ? "primary" : "unique";
  if (record.kind === "relationship") return "relationship";
  if (record.kind === "check") return "check";
  if (record.kind === "index") return "index";
  return null;
}

function relatedColumnIds(record) {
  if (record.kind === "relationship") {
    return [...(record.value.sourceColumnIds || []), ...(record.value.targetColumnIds || [])];
  }
  return record.value.columnIds || [];
}

function addRelatedColumns(targets, before, after, beforeIndex, afterIndex) {
  const field = relatedColumnField(after || before);
  if (!field) return;
  const ids = new Set([
    ...relatedColumnIds(before || { kind: "object", value: {} }),
    ...relatedColumnIds(after || { kind: "object", value: {} }),
  ]);
  for (const id of ids) {
    const column = afterIndex.get(id) || beforeIndex.get(id);
    if (!column) continue;
    mergeTarget(targets, {
      objectId: id,
      fallbackId: column.parentId,
      scope: "schema",
      kind: "column",
      name: column.name,
      parentName: column.parentName,
      operation: "related",
      fields: [field],
      tone: "amber",
    });
  }
}

export function designChangeTargets(beforeContent, afterContent) {
  const beforeIndex = entityIndex(beforeContent);
  const afterIndex = entityIndex(afterContent);
  const targets = new Map();
  const ids = new Set([...beforeIndex.keys(), ...afterIndex.keys()]);

  for (const id of ids) {
    const before = beforeIndex.get(id);
    const after = afterIndex.get(id);
    if (!before) {
      mergeTarget(targets, {
        objectId: id,
        fallbackId: after.parentId,
        scope: after.scope,
        kind: after.kind,
        name: after.name,
        parentName: after.parentName,
        ...(after.relationName ? { relationName: after.relationName } : {}),
        operation: "add",
        fields: [],
        tone: after.tone,
      });
      addRelatedColumns(targets, null, after, beforeIndex, afterIndex);
      continue;
    }
    if (!after) {
      mergeTarget(targets, {
        objectId: id,
        fallbackId: before.parentId,
        scope: before.scope,
        kind: before.kind,
        name: before.name,
        parentName: before.parentName,
        ...(before.relationName ? { relationName: before.relationName } : {}),
        operation: "remove",
        fields: [],
        tone: before.tone,
      });
      addRelatedColumns(targets, before, null, beforeIndex, afterIndex);
      continue;
    }
    const fields = changedFields(before, after);
    const moved = before.parentId === after.parentId
      && before.collection === after.collection
      && before.index !== after.index;
    if (moved) fields.push("order");
    if (!fields.length) continue;
    mergeTarget(targets, {
      objectId: id,
      fallbackId: after.parentId,
      scope: after.scope,
      kind: after.kind,
      name: after.name,
      parentName: after.parentName,
      ...(after.relationName ? { relationName: after.relationName } : {}),
      operation: "update",
      fields,
      tone: after.tone,
    });
    addRelatedColumns(targets, before, after, beforeIndex, afterIndex);
  }

  return [...targets.values()];
}

export function designChangePresentation(targets) {
  const changes = (targets || []).filter(target => target.operation !== "related");
  const primary = changes[0] || null;
  const scopes = new Set(changes.map(target => target.scope).filter(Boolean));
  const scope = scopes.size === 1 ? [...scopes][0] : null;
  const layer = scope === "views" ? "views" : scope === "schema" ? "tables" : null;
  if (!primary) return { layer, primary: null, headline: "Design changed", changeCount: 0 };
  const verb = {
    add: "Created",
    remove: "Removed",
    update: "Updated",
  }[primary.operation] || "Changed";
  const qualifiedName = primary.parentName && primary.name
    ? `${primary.parentName}.${primary.name}`
    : primary.name;
  const headline = changes.length === 1 && qualifiedName
    ? `${verb} ${primary.kind} ${qualifiedName}`
    : `${changes.length} design changes`;
  return { layer, primary, headline, changeCount: changes.length };
}

export function viewAnalysisContextSignature(content) {
  return JSON.stringify({
    tables: (content?.tables || []).map(table => ({
      id: table.id,
      name: table.name,
      columns: (table.columns || []).map(column => ({
        id: column.id,
        name: column.name,
        dataType: column.dataType,
      })),
    })),
    views: (content?.views || []).map(view => ({
      id: view.id,
      name: view.name,
      kind: view.kind,
      definition: view.definition,
    })),
  });
}
