import { ensureAliasConnections } from "./alias-model.js";
import { catalogContract } from "./model-draft.js";

const relationshipFields = ["id","name","sourceTable","sourceColumn","targetTable","targetColumn"];
const edgeKey = (relationshipId, source, target) => JSON.stringify([relationshipId,source,target]);

function mergeContract(contract, current) {
  const tables = new Map((contract.tables || []).map(table=>[table.name,table]));
  for (const table of current.tables) {
    if (!tables.has(table.name)) { contract.tables.push(structuredClone(table)); continue; }
    const saved=tables.get(table.name), columns=new Set((saved.columns || []).map(column=>column.name));
    for (const column of table.columns) if (!columns.has(column.name)) saved.columns.push(structuredClone(column));
  }
  const relationships = new Set((contract.relationships || []).map(relationship=>relationship.id));
  for (const relationship of current.relationships) if (!relationships.has(relationship.id)) contract.relationships.push(structuredClone(relationship));
}

/** Reconcile available fields while retaining structural assumptions for review. */
export function reconcileSourceCatalog(draft,catalog) {
  const result={tables:[],columns:[],relationships:[],initialized:false};
  const current=catalogContract(catalog);
  if (!draft.sourceContract) {
    draft.sourceContract=structuredClone(current);result.initialized=true;
  } else {
    const priorTables=new Map(draft.sourceContract.tables.map(table=>[table.name,new Set(table.columns.map(column=>column.name))]));
    const priorRelationships=new Set(draft.sourceContract.relationships.map(relationship=>relationship.id));
    for(const table of current.tables) {
      if(!priorTables.has(table.name)) result.tables.push(table.name);
      else for(const column of table.columns) if(!priorTables.get(table.name).has(column.name)) result.columns.push(`${table.name}.${column.name}`);
    }
    for(const relationship of current.relationships) if(!priorRelationships.has(relationship.id)) result.relationships.push(relationship.id);
    mergeContract(draft.sourceContract,current);
  }
  const canonical=new Map(draft.nodes.filter(node=>!node.derivation && node.id===node.table).map(node=>[node.table,node.id]));
  const positions=new Map((catalog.positions || []).map(position=>[position.name,position]));
  const newTables=new Set(result.tables);
  for(const table of catalog.tables || []) {
    // A table omitted from an existing model may be intentionally out of scope.
    // Only materialize tables that are genuinely new relative to its saved source contract.
    if(canonical.has(table.name)||!newTables.has(table.name)) continue;
    let id=table.name;
    for(let suffix=2;draft.nodes.some(node=>node.id===id);suffix++)id=`${table.name}_${suffix}`;
    const position=positions.get(table.name);
    draft.nodes.push({id,table:table.name,label:table.name,...(Number.isFinite(position?.x)&&Number.isFinite(position?.y)?{x:position.x,y:position.y}:{})});
    canonical.set(table.name,id);
  }
  const tuples=new Set((draft.edges || []).map(edge=>edgeKey(edge.relationshipId,edge.source,edge.target)));
  const ids=new Set((draft.edges || []).map(edge=>edge.id));
  for(const relationship of catalog.relationships || []) {
    const source=canonical.get(relationship.sourceTable),target=canonical.get(relationship.targetTable);
    if(!source||!target||tuples.has(edgeKey(relationship.id,source,target)))continue;
    let id=`source_fk_${relationship.id}`,suffix=2;
    while(ids.has(id))id=`source_fk_${relationship.id}_${suffix++}`;
    draft.edges.push({id,relationshipId:relationship.id,source,target,enabled:false});
    ids.add(id);tuples.add(edgeKey(relationship.id,source,target));
  }
  ensureAliasConnections(draft,catalog);
  // Physical columns come from the live catalog. Discard obsolete output choices,
  // including alias choices, but preserve predicates and calculations for repair.
  // Missing tables remain explicit structural issues rather than losing selections.
  const liveColumns=new Map((catalog.tables || []).map(table=>[table.name,new Set(table.columns.map(column=>column.name))]));
  const nodes=new Map(draft.nodes.map(node=>[node.id,node]));
  const available=field=>{
    const node=nodes.get(field.table),columns=liveColumns.get(node?.table);
    return !node || node.derivation || !columns || columns.has(field.column);
  };
  if(Array.isArray(draft.exposedFields))draft.exposedFields=draft.exposedFields.filter(available);
  if(Array.isArray(draft.fields))draft.fields=draft.fields.filter(available);
  return result;
}

export function acceptSourceIssue(draft,catalog,issue) {
  if(!draft.sourceContract || !issue?.acknowledge)return false;
  if(["changed_primary_key","changed_column","removed_column"].includes(issue.kind)) {
    const current=catalogContract(catalog).tables.find(table=>table.name===issue.table);
    const index=draft.sourceContract.tables.findIndex(table=>table.name===issue.table);
    if(!current||index<0)return false;
    const saved=draft.sourceContract.tables[index];
    if(issue.kind==="changed_primary_key")saved.primaryKey=[...current.primaryKey];
    if(issue.kind==="changed_column") {
      const replacement=current.columns.find(column=>column.name===issue.column),columnIndex=saved.columns.findIndex(column=>column.name===issue.column);
      if(!replacement||columnIndex<0)return false;
      saved.columns[columnIndex]=structuredClone(replacement);
    }
    if(issue.kind==="removed_column")saved.columns=saved.columns.filter(column=>column.name!==issue.column);
    return true;
  }
  if(issue.kind==="removed_relationship") {
    const before=draft.sourceContract.relationships.length;
    draft.sourceContract.relationships=draft.sourceContract.relationships.filter(relationship=>relationship.id!==issue.relationshipId);
    return draft.sourceContract.relationships.length<before;
  }
  if(issue.kind==="changed_relationship") {
    const current=catalogContract(catalog).relationships.find(relationship=>relationship.id===issue.relationshipId);
    const index=draft.sourceContract.relationships.findIndex(relationship=>relationship.id===issue.relationshipId);
    if(!current||index<0)return false;
    draft.sourceContract.relationships[index]=Object.fromEntries(relationshipFields.map(key=>[key,current[key]||""]));
    for(const edge of draft.edges || []) if(edge.relationshipId===issue.relationshipId)edge.enabled=false;
    return true;
  }
  return false;
}
