// Prototype examples describe model metadata only; every edge still references a live FK.
export const MODEL_LIMITS = Object.freeze({ sources: 100, relationships: 200, fields: 1000 });

export function defaultSourceSelection(catalog) {
  const selection = (catalog.tables || []).filter(table => table.columns?.length)
    .map(table => ({ table: table.name, columns: table.columns.map(column => column.name) }));
  try { importedDraft(catalog, selection); return selection; } catch { return []; }
}

export function importedDraft(catalog, selection = (catalog.tables || []).map(table => ({ table: table.name, columns: (table.columns || []).map(column => column.name) }))) {
  if (!selection.length) throw new Error("Select at least one source and field before creating the model.");
  const tables = new Map((catalog.tables || []).map(table => [table.name, table]));
  const selected = new Map();
  for (const item of selection) {
    const table = tables.get(item.table);
    if (!table?.columns?.length) throw new Error(`Source ${item.table} is unavailable or has no readable fields. Reload sources and review your selection.`);
    if (selected.has(item.table)) throw new Error(`Source ${item.table} was selected twice.`);
    const available = new Set(table.columns.map(column => column.name));
    const columns = [...new Set(item.columns || [])];
    if (columns.some(column => !available.has(column))) throw new Error(`A selected field on ${item.table} is unavailable. Reload sources and review your selection.`);
    selected.set(item.table, new Set(columns));
  }
  if (selected.size > MODEL_LIMITS.sources) throw new Error("Choose at most 100 sources for one model.");
  const exposedFields = selection.flatMap(item => [...selected.get(item.table)].map(column => ({ table: item.table, column, aggregate: "none" })));
  if (!exposedFields.length) throw new Error("Select at least one field before creating the model.");
  if (exposedFields.length > MODEL_LIMITS.fields) throw new Error("Choose at most 1,000 exposed fields for one model.");
  const relationships = (catalog.relationships || []).filter(relationship => selected.has(relationship.sourceTable) && selected.has(relationship.targetTable));
  if (relationships.length > MODEL_LIMITS.relationships) throw new Error("These sources have more than 200 connections. Choose a smaller subset; you can add sources later.");
  // Hidden join/key columns remain source dependencies, not exposed outputs.
  const dependencies = new Map([...selected].map(([name, columns]) => [name, new Set(columns)]));
  for (const [name, columns] of dependencies) {
    const table = tables.get(name);
    for (const column of [...(table.primaryKey || []), ...(table.uniqueKeys || []).flat()]) columns.add(column);
  }
  for (const relationship of relationships) {
    dependencies.get(relationship.sourceTable).add(relationship.sourceColumn);
    dependencies.get(relationship.targetTable).add(relationship.targetColumn);
  }
  if ([...dependencies.values()].some(columns => columns.size > MODEL_LIMITS.fields)) throw new Error("A selected source exceeds 1,000 fields including required keys and joins. Choose fewer fields or a smaller subset.");
  const positions = new Map((catalog.positions || []).map(position => [position.name, position]));
  const nodes = selection.map(item => {
    const position = positions.get(item.table);
    return { id: item.table, table: item.table, label: item.table.slice(0, 100),
      ...(Number.isFinite(position?.x) && Number.isFinite(position?.y) ? { x: position.x, y: position.y } : {}) };
  });
  const root = nodes.find(node => selected.get(node.id).size)?.id || nodes[0].id;
  const columns = tables.get(root).columns.filter(column => selected.get(root).has(column.name));
  const scoped = { tables: [...dependencies].map(([name, columns]) => ({ ...tables.get(name), columns: tables.get(name).columns.filter(column => columns.has(column.name)) })), relationships };
  return { root, nodes, edges: relationships.map(r => ({ id: r.id, relationshipId: r.id, source: r.sourceTable, target: r.targetTable, enabled: true })),
    sourceContract: catalogContract(scoped, "model"), exposedFields,
    fields: columns.length ? [{ table: root, column: (columns.find(c => c.name === "name") || columns[0]).name, aggregate: "none" }] : [],
    scopes: [], selections: {}, reportFilters: [], limit: 100 };
}

export function catalogContract(catalog, scope = "catalog") {
  return {
    scope,
    tables:(catalog.tables || []).map(table=>({name:table.name,primaryKey:[...(table.primaryKey || [])],
      ...(scope === "model" ? {uniqueKeys:structuredClone(table.uniqueKeys || [])} : {}),
      columns:(table.columns || []).map(column=>({name:column.name,dataType:column.dataType || "",nullable:column.nullable ?? true}))})),
    relationships:(catalog.relationships || []).map(relationship=>Object.fromEntries(["id","name","sourceTable","sourceColumn","targetTable","targetColumn"].map(key=>[key,relationship[key] || ""]))),
  };
}

export function staffingDraft(catalog) {
  const draft = importedDraft(catalog);
  if (!draft.nodes.some(n => n.id === "org_hier")) throw new Error("The staffing example requires the organization warehouse.");
  draft.root="personnel_dim";
  draft.fields=[{table:"personnel_dim",column:"name",aggregate:"none"}];
  const aliases = [
    { id: "ancestor_org", table: "org_dim", label: "Ancestor organization" },
    { id: "job_requirement_1", table: "certification_dim", label: "Job certification requirement 1" },
    { id: "job_requirement_2", table: "certification_dim", label: "Job certification requirement 2" },
  ];
  draft.nodes.push(...aliases);
  for (const alias of aliases) draft.exposedFields.push(...draft.exposedFields.filter(field => field.table === alias.table)
    .map(field => ({ ...field, table: alias.id })));
  const keep = new Set([
    "slate_fact.personnel_id", "slate_fact.job_slot_id", "job_slot_fact.job_slot_id",
    "job_slot_fact.org_id", "org_hier.child_id", "org_hier.parent_id", "slate_status.slate_id",
    "personnel_certification_fact.personnel_id", "personnel_certification_fact.certification_id",
    "job_slot_fact.required_certification_1_id", "job_slot_fact.required_certification_2_id",
    "job_slot_fact.job_field_id", "job_slot_fact.pay_band_class_id", "personnel_pay_band_class_fact.personnel_id",
  ]);
  for (const edge of draft.edges) {
    const r = catalog.relationships.find(r => r.id === edge.relationshipId);
    const key = `${r.sourceTable}.${r.sourceColumn}`;
    edge.enabled = keep.has(key);
    if (key === "org_hier.parent_id") edge.target = "ancestor_org";
    if (key === "job_slot_fact.required_certification_1_id") edge.target = "job_requirement_1";
    if (key === "job_slot_fact.required_certification_2_id") edge.target = "job_requirement_2";
  }
  const input = (id, label, type, defaultValue = "") => ({ id, label, type, defaultValue });
  const binding = (table, column, operator, parameterId, allowNull = false) => ({ table, column, operator, parameterId, allowNull });
  const periods = [["slate_fact", "start_date", "end_date"], ["job_slot_fact", "slice_start_date", "slice_end_date"],
    ["org_dim", "slice_start_date", "slice_end_date"], ["ancestor_org", "slice_start_date", "slice_end_date"],
    ["slate_status", "status_start_date", "status_end_date"], ["personnel_certification_fact", "effective_date", "expiration_date"]];
  draft.scopes = [
    { id: "org_scope", label: "Organization tree", kind: "required", alternatives: [
      { id: "parent", label: "Within an ancestor organization", inputs: [{ ...input("parent_id", "Parent organization", "text"), domain: { nodeId: "ancestor_org", table: "org_dim", column: "id", labelColumn: "name" } }], conditions: [binding("ancestor_org", "id", "eq", "parent_id")] },
    ] },
    { id: "time_scope", label: "Time scope", kind: "conditional", alternatives: [
      { id: "as_of", label: "As of a date", inputs: [input("as_of", "As of date", "date", "today")], conditions: periods.flatMap(([table,start,end]) => [binding(table,start,"lte","as_of"),binding(table,end,"gte","as_of",true)]) },
      { id: "period", label: "Active at any point during a period", inputs: [input("from", "Period start", "date"),input("until", "Period end", "date")], conditions: periods.flatMap(([table,start,end]) => [binding(table,start,"lte","until"),binding(table,end,"gte","from",true)]) },
      { id: "history", label: "All history (no date restriction)", inputs: [], conditions: [] },
    ] },
  ];
  return draft;
}
