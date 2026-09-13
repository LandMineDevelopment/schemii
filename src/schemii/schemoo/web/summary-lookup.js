import { edgeRelationship } from "./logical-relationships.js";
/** Explain catalog-backed lookup cardinality, not query validity or permissions.
 * The server remains authoritative when validating and compiling the model.
 */
export function summaryLookup(draft, catalog, source, target) {
  const nodes = new Map(draft.nodes.filter(n=>!n.derivation).map(n=>[n.id,n]));
  if (!nodes.has(source) || !nodes.has(target)) return {status:"unknown",path:[],title:"Lookup unavailable",message:"Choose an existing source object."};
  if(source===target) return {status:"source",path:[],title:"Source field · no lookup join",message:"This value comes directly from the records being summarized."};
  const graph=new Map([...nodes.keys()].map(id=>[id,[]]));
  for(const edge of draft.edges.filter(e=>e.enabled)) {
    if(!graph.has(edge.source)||!graph.has(edge.target))continue;
    graph.get(edge.source).push({next:edge.target,edge});
    graph.get(edge.target).push({next:edge.source,edge});
  }
  const parents=new Map([[source,null]]),queue=[source];let cycle=false;
  for(let i=0;i<queue.length;i++)for(const {next,edge} of graph.get(queue[i])){
    if(parents.get(queue[i])?.edge===edge)continue;
    if(parents.has(next)){cycle=true;continue;}
    parents.set(next,{previous:queue[i],edge});queue.push(next);
  }
  if(!parents.has(target))return {status:"disconnected",path:[],title:"No enabled lookup path",message:"Enable the intended relationship path or choose another object. This field cannot be queried while disconnected."};
  if(cycle)return {status:"ambiguous",path:[],title:"Lookup path needs review",message:"Enabled relationships contain a cycle. Resolve it before the server can validate this lookup."};
  const steps=[];
  for(let node=target;node!==source;){const step=parents.get(node);steps.unshift({...step,next:node});node=step.previous;}
  const path=[];let multiplying=false, logical=false;
  for(const {previous,next,edge} of steps){
    const relation=edgeRelationship(edge,catalog);
    if(!relation || (edge.kind !== "logical" && (nodes.get(edge.source).table!==relation.sourceTable || nodes.get(edge.target).table!==relation.targetTable)))
      return {status:"unknown",path:[],title:"Relationship needs refresh",message:"A relationship no longer matches the loaded schema. Refresh or repair the model before querying."};
    const forward=previous===edge.source;
    if(edge.kind === "logical") {
      logical=true;
      const table=catalog.tables?.find(table=>table.name===nodes.get(next).table);
      const column=forward?edge.targetColumn:edge.sourceColumn;
      const keys=[table?.primaryKey || [],...(table?.uniqueKeys || [])];
      multiplying ||= !keys.some(key=>Array.isArray(key) && key.length===1 && key[0]===column);
    } else multiplying ||= !forward;
    path.push(`${nodes.get(previous).label}.${forward?relation.sourceColumn:relation.targetColumn} → ${nodes.get(next).label}.${forward?relation.targetColumn:relation.sourceColumn}`);
  }
  return multiplying
    ? {status:"multiplying",path,title:"Lookup can multiply source records",message:"A joined column on this path is not known to be unique. Source-first summaries reject it; summarize those records separately before connecting them."}
    : {status:"many_to_one",path,title:logical ? "Many-to-one lookup · unique key backed" : "Many-to-one lookup · foreign-key backed",message:"Each source record matches at most one record at every step. The referenced key is unique, so these lookup joins cannot multiply the records being summarized."};
}
