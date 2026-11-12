import { sourceIdentity, sourceNodeIds, sourceLabel, relationshipColumns } from "./logical-relationships.js";
// Prototype examples describe model metadata only; every edge still references a live FK.
export const MODEL_LIMITS = Object.freeze({ sources: 100, relationships: 200, fields: 1000 });
const selectionFor = table => ({table: table.name, ...(table.namespace ? {namespace: table.namespace} : {}), columns: table.columns.map(column => column.name)});

export function defaultSourceSelection(catalog) {
  const selection = (catalog.tables || []).filter(table => table.columns?.length).map(selectionFor);
  try { importedDraft(catalog, selection); return selection; } catch { return []; }
}

export function importedDraft(catalog, selection = (catalog.tables || []).map(selectionFor)) {
  if (!selection.length) throw new Error("Select at least one source and field before creating the model.");
  const tables = new Map((catalog.tables || []).map(table => [sourceIdentity(table,catalog),table]));
  const selected = new Map();
  for (const item of selection) {
    const identity = sourceIdentity(item,catalog), table = tables.get(identity);
    if (!table?.columns?.length) throw new Error(`Source ${item.table} is unavailable or has no readable fields. Reload sources and review your selection.`);
    if (selected.has(identity)) throw new Error(`Source ${item.table} was selected twice.`);
    const available = new Set(table.columns.map(column => column.name)), columns = [...new Set(item.columns || [])];
    if (columns.some(column => !available.has(column))) throw new Error(`A selected field on ${item.table} is unavailable. Reload sources and review your selection.`);
    selected.set(identity,new Set(columns));
  }
  if (selected.size > MODEL_LIMITS.sources) throw new Error("Choose at most 100 sources for one model.");
  const nodeIds = sourceNodeIds(selection, catalog);
  const exposedFields = selection.flatMap(item => [...selected.get(sourceIdentity(item,catalog))].map(column => ({table:nodeIds.get(sourceIdentity(item,catalog)),column,aggregate:"none"})));
  if (!exposedFields.length) throw new Error("Select at least one field before creating the model.");
  if (exposedFields.length > MODEL_LIMITS.fields) throw new Error("Choose at most 1,000 exposed fields for one model.");
  const relationships = (catalog.relationships || []).filter(relationship => selected.has(sourceIdentity(relationship,catalog,"source")) && selected.has(sourceIdentity(relationship,catalog,"target")));
  if (relationships.length > MODEL_LIMITS.relationships) throw new Error("These sources have more than 200 connections. Choose a smaller subset; you can add sources later.");
  const dependencies = new Map([...selected].map(([identity,columns])=>[identity,new Set(columns)]));
  for (const [identity,columns] of dependencies) for (const column of [...(tables.get(identity).primaryKey || []),...(tables.get(identity).uniqueKeys || []).flat()]) columns.add(column);
  for (const relationship of relationships) for (const pair of relationshipColumns(relationship)) for (const side of ["source","target"]) dependencies.get(sourceIdentity(relationship,catalog,side)).add(pair[side]);
  if ([...dependencies.values()].some(columns=>columns.size>MODEL_LIMITS.fields)) throw new Error("A selected source exceeds 1,000 fields including required keys and joins. Choose fewer fields or a smaller subset.");
  const nodes = selection.map(item => {
    const position=(catalog.positions || []).find(position=>sourceIdentity(position,catalog)===sourceIdentity(item,catalog));
    return {id:nodeIds.get(sourceIdentity(item,catalog)),table:item.table,label:sourceLabel(item,catalog).slice(0,100),
      ...(item.namespace ? {namespace:item.namespace} : {}),...(Number.isFinite(position?.x)&&Number.isFinite(position?.y)?{x:position.x,y:position.y}:{})};
  });
  const root=nodes.find(node=>selected.get(sourceIdentity(node,catalog)).size)?.id || nodes[0].id;
  const owner=nodes.find(node=>node.id===root), columns=tables.get(sourceIdentity(owner,catalog)).columns.filter(column=>selected.get(sourceIdentity(owner,catalog)).has(column.name));
  const ids = new Map(nodes.map(node=>[sourceIdentity(node,catalog),node.id]));
  const scoped={tables:[...dependencies].map(([identity,columns])=>({...tables.get(identity),columns:tables.get(identity).columns.filter(column=>columns.has(column.name))})),relationships};
  return {root,nodes,edges:relationships.map(r=>({id:r.id,relationshipId:r.id,source:ids.get(sourceIdentity(r,catalog,"source")),target:ids.get(sourceIdentity(r,catalog,"target")),enabled:true})),
    sourceContract:catalogContract(scoped,"model"),exposedFields,fields:columns.length?[{table:root,column:(columns.find(c=>c.name==="name")||columns[0]).name,aggregate:"none"}]:[],scopes:[],selections:{},reportFilters:[],limit:100};
}

export function catalogContract(catalog, scope="catalog") {
  return {scope,tables:(catalog.tables || []).map(table=>({name:table.name,...(table.namespace?{namespace:table.namespace}:{}),primaryKey:[...(table.primaryKey || [])],
    ...(scope==="model"?{uniqueKeys:structuredClone(table.uniqueKeys || [])}:{}),columns:(table.columns || []).map(column=>({name:column.name,dataType:column.dataType || "",nullable:column.nullable ?? true}))})),
    relationships:(catalog.relationships || []).map(relationship=>Object.fromEntries(["id","name","sourceTable","sourceColumn","targetTable","targetColumn","sourceNamespace","targetNamespace","columns"].filter(key=>key in relationship || key==="name").map(key=>[key,structuredClone(relationship[key] ?? "")])))};
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
