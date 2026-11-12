
export const calculationRef = item => ({table:'@report',column:item.id,aggregate:'none',kind:'calculated'});
export const outputKey = item => JSON.stringify([item.table,item.column,item.aggregate || 'none',item.kind || 'value']);
export const visibleMeasures = tile => [...tile.measures,...(tile.calculations || []).filter(item=>item.visible!==false).map(item=>({...calculationRef(item),label:item.label}))];
const numeric = /^(smallint|integer|bigint|numeric|decimal|real|double precision|int[248]|float[48])(?:\b|\()/i;
const scopes=[['group','This group / cell'],['source_population','Source-filtered report population · before result filters'],['qualifying_groups','Qualifying groups · after independent result filters, before Top N']];
export function calculationErrors(tile,outputs) {
  const errors=[],items=new Map((tile.calculations || []).map(item=>[item.id,item])),keys=new Map(outputs.map(item=>[outputKey(item),item])),done=new Map(),path=[];
  function visit(id) {
    if(done.has(id)) return done.get(id);
    const item=items.get(id);if(!item){errors.push(`Calculation ${id} was removed. Rebind its references.`);return new Set();}
    if(path.includes(id)){errors.push(`Calculation cycle: ${[...path,id].join(' → ')}. Rebind an operand.`);return new Set();}
    path.push(id);const populations=new Set();
    if(!item.label?.trim()) errors.push('Name every calculated measure.');
    for(const operand of [item.left,item.right]) {
      if(operand?.constant != null) {
        if(!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(String(operand.constant).trim()) || String(operand.constant).length>200) errors.push(`${item.label}: enter a finite numeric constant. Your exact input is kept.`);
      } else {
        const output=keys.get(outputKey(operand?.output || {}));
        if(!output) errors.push(`${item.label}: an input was removed. Restore it or choose a replacement.`);
        else if(output.kind==='calculated') {
          if(operand.scope!=='group') errors.push(`${item.label}: population scope needs a base aggregate.`);
          for(const scope of visit(output.column)) populations.add(scope);
        } else {
          if(!numeric.test(output.dataType || '')) errors.push(`${item.label}: choose numeric aggregate inputs.`);
          if(operand.scope!=='group' && (output.kind!=='value' || output.aggregate==='none')) errors.push(`${item.label}: population scope needs a base aggregate.`);
          populations.add(operand.scope || 'group');
        }
      }
    }
    path.pop();done.set(id,populations);return populations;
  }
  for(const id of items.keys()) visit(id);
  for(const filter of tile.aggregateFilters || []) if(filter.kind==='calculated' && done.get(filter.column)?.has('qualifying_groups')) errors.push(`${items.get(filter.column)?.label}: this filter would define its own denominator. Use an independent filter or source population.`);
  const used=new Set((tile.calculations || []).flatMap(item=>[item.left,item.right]).filter(operand=>operand?.output).map(operand=>outputKey(operand.output)));
  for(const output of [...(tile.sort || []),...(tile.aggregateFilters || [])]) used.add(outputKey(output));
  if(tile.kpiComparison?.baseline?.output) used.add(outputKey(tile.kpiComparison.baseline.output));
  for(const field of tile.hiddenMeasures || []) if(![...used].some(key=>JSON.stringify(JSON.parse(key).slice(0,3))===JSON.stringify([field.table,field.column,field.aggregate || 'none']))) errors.push(`Hidden input ${field.column} is unused. Bind it or explicitly remove it.`);
  for(const item of tile.calculations || []) if(item.visible===false&&!used.has(outputKey(calculationRef(item)))) errors.push(`Hidden calculation ${item.label} is unused. Display it, bind it, or explicitly remove it.`);
  return [...new Set(errors)];
}
export function measureEditor(tile,{outputs,onChange,element,modelSelect}) {
  tile.calculations ||= [];tile.hiddenMeasures ||= [];
  const host=element('section',{className:'calculated-measures'},[element('h3',{text:'Calculated measures'}),element('p',{className:'hint',text:'Combine complete-query aggregates. Division by zero or a missing input returns NULL. Percent of returns percentage points: 25 means 25%. Hidden inputs remain available without becoming displayed measures.'})]);
  const labeled=(text,control)=>element('label',{className:'stack'},[text,control]);
  const input=(label,value,change)=>{const control=element('input',{attrs:{'aria-label':label,maxlength:200}});control.value=value ?? '';control.oninput=()=>change(control.value);return control;};
  const button=(text,fn)=>{const control=element('button',{type:'button',className:'ui-button',text});control.onclick=fn;return control;};
  const baseKeys=new Set([...tile.measures,...tile.hiddenMeasures].map(field=>JSON.stringify([field.table,field.column,field.aggregate || "none"])));
  const numericOutputs=outputs.filter(output=>output.kind==='calculated' || numeric.test(output.dataType || '') && baseKeys.has(JSON.stringify([output.table,output.column,output.aggregate || "none"])));
  function operandControl(item,key,label) {
    item[key] ||= {constant:'0',scope:'group'};const operand=item[key];
    const choices=[['constant','Numeric constant'],...numericOutputs.filter(output=>output.kind!=='calculated' || output.column!==item.id).map(output=>[outputKey(output),output.label])];
    const selected=operand.constant != null?'constant':outputKey(operand.output || {});
    if(!choices.some(([value])=>value===selected)) choices.unshift([selected,'Unavailable input · choose a replacement']);
    const block=element('div',{className:'measure-operand'},[labeled(label,modelSelect(`${item.label || 'Calculation'} ${label}`,choices,selected,value=>{
      if(value==='constant') item[key]={constant:'0',scope:'group'};
      else {const output=numericOutputs.find(output=>outputKey(output)===value);item[key]={output:{table:output.table,column:output.column,aggregate:output.aggregate,kind:output.kind},scope:'group'};}
      onChange();
    }))]);
    if(operand.constant != null) block.append(labeled('Exact numeric value',input(`${label} exact numeric value`,operand.constant,value=>{operand.constant=value;})));
    else block.append(labeled('Population / denominator scope',modelSelect(`${label} population scope`,scopes,operand.scope || 'group',value=>{operand.scope=value;onChange();})));
    return block;
  }
  tile.calculations.forEach((item,index)=>{
    const name=input(`Calculation ${index+1} name`,item.label,value=>{item.label=value;});
    const visible=element('input',{attrs:{type:'checkbox','aria-label':`Display calculation ${index+1}`}});visible.checked=item.visible!==false;visible.onchange=()=>{item.visible=visible.checked;onChange();};
    host.append(element('section',{className:'measure-card'},[
      labeled('Name',name),labeled('Operation',modelSelect(`Calculation ${index+1} operation`,[['add','Add'],['subtract','Subtract'],['multiply','Multiply'],['divide','Ratio · number'],['percent_of','Percent of · scale 0–100']],item.operation,value=>{item.operation=value;onChange();})),
      operandControl(item,'left','Left / numerator'),operandControl(item,'right','Right / denominator'),element('label',{},[visible,'Display this calculation']),
      button(`Remove calculation ${index+1}`,()=>{tile.calculations.splice(index,1);onChange();}),
    ]));
  });
  for(const message of calculationErrors(tile,outputs)) host.append(element('p',{className:'error-message',attrs:{role:'alert'},text:message}));
  host.append(button('Add calculated measure',()=>{tile.calculations.push({id:crypto.randomUUID(),label:'',operation:'divide',left:{constant:'0',scope:'group'},right:{constant:'1',scope:'group'},visible:true});onChange();}));
  if(tile.kind==='kpi') {
    const enabled=element('input',{attrs:{type:'checkbox','aria-label':'Compare KPI to target or baseline'}});enabled.checked=!!tile.kpiComparison;
    enabled.onchange=()=>{tile.kpiComparison=enabled.checked?{label:'Target',baseline:{constant:'0',scope:'group'},favorable:'higher'}:null;onChange();};
    host.append(element('h3',{text:'KPI target / baseline'}),element('label',{},[enabled,'Show target, absolute variance and percentage variance']));
    if(tile.kpiComparison) {
      const comparison=tile.kpiComparison;
      host.append(labeled('Target / baseline label',input('KPI baseline label',comparison.label,value=>{comparison.label=value;})),operandControl(comparison,'baseline','KPI baseline'),
        labeled('Favorable direction',modelSelect('KPI favorable direction',[['higher','Higher is better'],['lower','Lower is better'],['equal','Exactly at baseline is better']],comparison.favorable,value=>{comparison.favorable=value;onChange();})),
        element('p',{className:'hint',text:'Variance is value minus baseline. Percentage variance divides by the absolute baseline and returns NULL when it is zero. Direction is explicit; inputs are never normalized or rounded when saved.'}));
    }
  }
  return host;
}

export function chooseConstituent(metadata,onSelect,element) {
  const dialog=element('dialog',{className:'ui-dialog',attrs:{'aria-label':'Choose calculation contributing records'}});
  const close=()=>{dialog.close();dialog.remove();};
  dialog.append(element('h2',{text:`Records for ${metadata.label}`}),element('p',{text:'A composite calculation has separate input populations. Choose the constituent to inspect; its raw rows are not a detail set for the combined ratio or variance.'}));
  if(!metadata.constituents.length) dialog.append(element('p',{text:'This calculation uses constants and has no raw contributing records.'}));
  for(const operand of metadata.constituents) {
    const output=operand.output;
    const button=element('button',{type:'button',className:'ui-button',text:`${output.aggregate.toUpperCase()} · ${output.table}.${output.column} · ${scopes.find(([id])=>id===operand.scope)?.[1] || operand.scope}`});
    if(output.kind && output.kind!=="value") {button.disabled=true;button.textContent+=" · combines periods; inspect base period values";}
    button.onclick=()=>{close();onSelect(operand);};dialog.append(button);
  }
  const cancel=element('button',{type:'button',className:'ui-button',text:'Cancel'});cancel.onclick=close;dialog.append(cancel);document.body.append(dialog);dialog.showModal();
}
