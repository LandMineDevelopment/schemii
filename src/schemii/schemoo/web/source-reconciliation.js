import { sourceIdentity, sourceNodeId, sameSource, relationshipColumns } from "./logical-relationships.js";
import { catalogContract, importedDraft, MODEL_LIMITS } from "./model-draft.js";

const relationshipFields = ["id","name","sourceTable","sourceColumn","targetTable","targetColumn","sourceNamespace","targetNamespace","columns"];
const edgeKey = (relationshipId, source, target) => JSON.stringify([relationshipId,source,target]);

/** Offer available sources without growing a saved model or its assumptions. */
export function reconcileSourceCatalog(draft,catalog) {
  const result={tables:[],columns:[],relationships:[],initialized:false};
  const modeled=new Set(draft.nodes.filter(node=>!node.derivation).map(node=>sourceIdentity(node,catalog)));
  const baseline=draft.sourceContract;
  const priorTables=new Map((baseline?.tables || []).map(table=>[sourceIdentity(table,catalog),new Set(table.columns.map(column=>column.name))]));
  const priorRelationships=new Set((draft.edges || []).map(edge=>edge.relationshipId));
  for(const table of catalog.tables || []) {
    if(!modeled.has(sourceIdentity(table,catalog))) result.tables.push(table.name);
    else for(const column of table.columns) if(!priorTables.get(sourceIdentity(table,catalog))?.has(column.name)) result.columns.push(`${table.name}.${column.name}`);
  }
  for(const relationship of catalog.relationships || []) if(modeled.has(sourceIdentity(relationship,catalog,"source"))&&modeled.has(sourceIdentity(relationship,catalog,"target"))&&!priorRelationships.has(relationship.id)) result.relationships.push(relationship.id);
  // Missing legacy baselines are migrated on the server when saved. Never
  // initialize them from the entire catalog, which may exceed model limits.
  if(!baseline) {draft.sourceContract={scope:"model",tables:[],relationships:[]};result.initialized=true;}
  // Physical columns come from the live catalog. Discard obsolete output choices,
  // including alias choices, but preserve predicates and calculations for repair.
  const liveColumns=new Map((catalog.tables || []).map(table=>[sourceIdentity(table,catalog),new Set(table.columns.map(column=>column.name))]));
  const nodes=new Map(draft.nodes.map(node=>[node.id,node]));
  const available=field=>{
    const node=nodes.get(field.table),columns=liveColumns.get(sourceIdentity(node,catalog));
    return !node || node.derivation || !columns || columns.has(field.column);
  };
  if(Array.isArray(draft.exposedFields))draft.exposedFields=draft.exposedFields.filter(available);
  if(Array.isArray(draft.fields))draft.fields=draft.fields.filter(available);
  return result;
}

/** Add only explicitly chosen sources/fields; preserve existing roles and rules. */
export function addSourceSelection(draft,catalog,selection) {
  const imported=importedDraft(catalog,selection),next=structuredClone(draft);
  const selectedTables=new Set(imported.nodes.map(node=>sourceIdentity(node,catalog)));
  const canonical=new Map();
  for(const node of next.nodes.filter(node=>!node.derivation)) if(!canonical.has(sourceIdentity(node,catalog))||node.id===sourceNodeId(node,catalog))canonical.set(sourceIdentity(node,catalog),node.id);
  const ids=new Set(next.nodes.map(node=>node.id));
  for(const node of imported.nodes) {
    if(canonical.has(sourceIdentity(node,catalog)))continue;
    let id=node.id;for(let suffix=2;ids.has(id);suffix++)id=`${node.id}_${suffix}`;
    next.nodes.push({...node,id});canonical.set(sourceIdentity(node,catalog),id);ids.add(id);
  }
  // Legacy unrestricted models stay unrestricted; explicit models add fields
  // only to the chosen physical occurrence, never every alias.
  if(Array.isArray(next.exposedFields)) {
    const fields=new Set(next.exposedFields.map(field=>JSON.stringify([field.table,field.column])));
    for(const field of imported.exposedFields) {
      const table=canonical.get(sourceIdentity(imported.nodes.find(node=>node.id===field.table),catalog)),key=JSON.stringify([table,field.column]);
      if(!fields.has(key)) {next.exposedFields.push({...field,table});fields.add(key);}
    }
  }
  const tuples=new Set(next.edges.map(edge=>edgeKey(edge.relationshipId,edge.source,edge.target))),edgeIds=new Set(next.edges.map(edge=>edge.id));
  for(const relationship of catalog.relationships || []) {
    // An explicit field/source addition can offer connections to its selected
    // sources; it must not import unrelated newly discovered relationships.
    if(!selectedTables.has(sourceIdentity(relationship,catalog,"source"))&&!selectedTables.has(sourceIdentity(relationship,catalog,"target")))continue;
    const source=canonical.get(sourceIdentity(relationship,catalog,"source")),target=canonical.get(sourceIdentity(relationship,catalog,"target"));
    if(!source||!target||tuples.has(edgeKey(relationship.id,source,target)))continue;
    let id=`source_fk_${relationship.id}`,suffix=2;while(edgeIds.has(id))id=`source_fk_${relationship.id}_${suffix++}`;
    next.edges.push({id,relationshipId:relationship.id,source,target,enabled:false});edgeIds.add(id);tuples.add(edgeKey(relationship.id,source,target));
  }
  if(next.nodes.length>MODEL_LIMITS.sources || next.edges.length>MODEL_LIMITS.relationships || next.exposedFields?.length>MODEL_LIMITS.fields)throw new Error("This selection would exceed 100 model objects, 200 connections, or 1,000 exposed fields. Choose a smaller subset.");
  Object.assign(draft,next);
  return reconcileSourceCatalog(draft,catalog);
}

export function acceptSourceIssue(draft,catalog,issue) {
  if(!draft.sourceContract || !issue?.acknowledge)return false;
  if(["changed_primary_key","changed_unique_key","changed_column","removed_column"].includes(issue.kind)) {
    const current=catalogContract(catalog,"model").tables.find(table=>sameSource(table,issue,catalog));
    const index=draft.sourceContract.tables.findIndex(table=>sameSource(table,issue,catalog));
    if(!current||index<0)return false;
    const saved=draft.sourceContract.tables[index];
    if(issue.kind==="changed_primary_key")saved.primaryKey=[...current.primaryKey];
    if(issue.kind==="changed_unique_key")saved.uniqueKeys=structuredClone(current.uniqueKeys);
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
    draft.sourceContract.relationships[index]=Object.fromEntries(relationshipFields.filter(key=>key in current).map(key=>[key,structuredClone(current[key])]));
    for(const edge of draft.edges || []) if(edge.relationshipId===issue.relationshipId)edge.enabled=false;
    return true;
  }
  return false;
}
