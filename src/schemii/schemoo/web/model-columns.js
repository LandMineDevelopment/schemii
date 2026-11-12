import { catalogTable } from "./logical-relationships.js";
const numeric = ["smallint", "integer", "bigint", "numeric", "real", "double precision"];
const aliases = {int2:"smallint",int4:"integer",int:"integer",int8:"bigint",decimal:"numeric",float4:"real",float8:"double precision",timestamptz:"timestamp with time zone",timetz:"time with time zone"};
const type = value => { const name=(value || "").toLowerCase().replace(/\([^)]*\)/g, "").trim().replace(/\s+/g, " ");return aliases[name] || name; };
const arithmetic = new Set(["add","subtract","multiply","divide"]);
export function calculationAnchor(draft, id, path = new Set()) {
  if(path.has(id)) return null;
  const node=draft.nodes.find(node=>node.id===id);
  if(!node) return null;
  if(!node.derivation) return id;
  return calculationAnchor(draft,node.derivation.connection?.target || node.derivation.source,new Set([...path,id]));
}
function outputType(operation,left,right) {
  if(["count","count_distinct"].includes(operation)) return "bigint";
  if(operation==="list") return "text";
  if(["min","max"].includes(operation)) return left;
  if(operation==="sum") return {smallint:"bigint",integer:"bigint",bigint:"numeric"}[left] || left;
  if(operation==="avg") return ["real","double precision"].includes(left)?"double precision":"numeric";
  if(operation==="divide") left="numeric";
  return numeric[Math.max(numeric.indexOf(left),numeric.indexOf(right))] || "";
}
/** Resolve the complete signature graph before reading any authored field order.
 * Invalid saved references remain visible, with an issue that editors can repair.
 */
export function nodeColumns(draft, catalog, node) {
  if(!node) return [];
  const memo=new Map(), path=[];
  const describe=(id,name)=>[id,name].every(value=>/^[A-Za-z_][A-Za-z0-9_]*$/.test(value)) ? `${id}.${name}` : JSON.stringify([id,name]);
  const dependencyPath=(tail=[])=>[...path.map(reference=>reference.label),...tail].join(" → ");
  function resolve(id,name) {
    const key=JSON.stringify([id,name]);
    if(memo.has(key)) return memo.get(key);
    const label=describe(id,name);
    if(path.some(reference=>reference.key===key)) throw Error(`Calculated dependency cycle: ${dependencyPath([label])}`);
    const owner=draft.nodes.find(value=>value.id===id);
    if(!owner) throw Error(`Missing calculated source: ${dependencyPath([label])}`);
    if(!owner.derivation) {
      const column=catalogTable(owner,catalog)?.columns.find(value=>value.name===name);
      if(!column) throw Error(`Missing calculated field: ${dependencyPath([label])}`);
      return column;
    }
    path.push({key,label});
    const definition=owner.derivation, output=definition.outputs.find(value=>value.id===name);
    let result;
    if(!output && (definition.groupBy || []).includes(name)) result={...resolve(definition.source,name),name};
    else if(!output) throw Error(`Missing calculated field: ${dependencyPath()}`);
    else {
      const left=type(resolve(output.nodeId || definition.source,output.column).dataType);
      const right=output.operand ? type(resolve(output.operandNodeId || definition.source,output.operand).dataType):"";
      for(const condition of output.conditions || []) {resolve(condition.table,condition.column);if(condition.compareColumn) resolve(condition.table,condition.compareColumn);}
      if((arithmetic.has(output.operation) || ["sum","avg"].includes(output.operation)) && (!numeric.includes(left) || (arithmetic.has(output.operation)&&!numeric.includes(right)))) throw Error(`Numeric inputs required: ${dependencyPath()}`);
      const refs=[output.nodeId || definition.source,...(output.operand?[output.operandNodeId || definition.source]:[]),...(output.conditions || []).map(value=>value.table)];
      if(definition.kind==="row" && refs.some(id=>calculationAnchor(draft,id)!==calculationAnchor(draft,definition.source))) throw Error(`Inputs must share the owning record or mapped summary grain: ${dependencyPath()}`);
      result={name,label:output.label,dataType:outputType(output.operation,left,right),nullable:true,grain:calculationAnchor(draft,id)};
    }
    path.pop();memo.set(key,result);return result;
  }
  if(!node.derivation) return catalogTable(node,catalog)?.columns || [];
  if(!Array.isArray(node.derivation.outputs)) return [];
  return [...(node.derivation.kind==="aggregate"?(node.derivation.groupBy || []):[]),...node.derivation.outputs.map(output=>output.id)].map(name=>{
    try{return resolve(node.id,name);}catch(error){path.length=0;return {name,label:node.derivation.outputs.find(output=>output.id===name)?.label,dataType:"",issue:error.message};}
  });
}
export function fieldLabel(draft, catalog, field) {
  const node=draft.nodes.find(n=>n.id===field.table);
  const column=nodeColumns(draft,catalog,node).find(c=>c.name===field.column);
  return `${node?.label || field.table} · ${column?.label || field.column}`;
}
