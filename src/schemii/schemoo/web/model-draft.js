// Prototype examples describe model metadata only; every edge still references a live FK.
export function importedDraft(catalog) {
  const positions = new Map((catalog.positions || []).map(position => [position.name, position]));
  const nodes = catalog.tables.map(t => {
    const position = positions.get(t.name);
    return { id: t.name, table: t.name, label: t.name,
      ...(Number.isFinite(position?.x) && Number.isFinite(position?.y) ? { x: position.x, y: position.y } : {}) };
  });
  const root = nodes[0].id;
  const columns = catalog.tables.find(t => t.name === root).columns;
  return { root, nodes, edges: catalog.relationships.map(r => ({ id: r.id, relationshipId: r.id, source: r.sourceTable, target: r.targetTable, enabled: true })),
    exposedFields: catalog.tables.flatMap(table => table.columns.map(column => ({ table: table.name, column: column.name, aggregate: "none" }))),
    fields: columns.length ? [{ table: root, column: (columns.find(c => c.name === "name") || columns[0]).name, aggregate: "none" }] : [],
    scopes: [], selections: {}, reportFilters: [], limit: 100 };
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
