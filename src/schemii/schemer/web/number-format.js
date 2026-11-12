/** Report-local presentation only. No formatted row cache or numeric mutation. */
export const FORMAT_LOCALES = ['en-US','en-GB','de-DE','fr-FR','es-ES','pt-BR','ja-JP','zh-CN','hi-IN','ar-EG'];
export const formatKey = ref => JSON.stringify([ref.table,ref.column,ref.aggregate || 'none',ref.kind || 'value']);
export const numericType = type => /^(smallint|integer|bigint|numeric|decimal|real|double precision|int[248]|float[48])(?:\(\d+(?:,\s*\d+)?\))?$/i.test((type || '').trim());
const symbols = { dot:'●',up:'▲',down:'▼',check:'✓',warning:'!' };
const operatorText={lt:'below',lte:'at most',eq:'equal to',ne:'not equal to',gte:'at least',gt:'above',between:'between',is_null:'is NULL',not_null:'is not NULL'};
const tones = new Set(['neutral','positive','caution','negative']);
const operations = new Set(['lt','lte','eq','ne','gte','gt','between','is_null','not_null']);
const intlCache = new Map();
function intl(locale, options) {
  const key = JSON.stringify([locale,options]);
  if (intlCache.has(key)) { const value=intlCache.get(key);intlCache.delete(key);intlCache.set(key,value);return value; }
  const value = new Intl.NumberFormat(locale,options); intlCache.set(key,value);
  if (intlCache.size > 64) intlCache.delete(intlCache.keys().next().value);
  return value;
}
export const numberFormatCacheSize = () => intlCache.size;
export function exactDecimal(value, maximum=262144) {
  if (typeof value === 'number' && (!Number.isFinite(value) || Number.isInteger(value) && !Number.isSafeInteger(value))) return null;
  if (!['number','string','bigint'].includes(typeof value)) return null;
  const text=String(value); if(text.length>maximum) return null;
  const m=/^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:[eE]([+-]?\d+))?$/.exec(text);
  if(!m) return null;
  const fraction=m[3] ?? m[4] ?? '';let digits=((m[2] || '')+fraction).replace(/^0+/,'') || '0';
  let exponent=Number(m[5] || 0)-fraction.length;
  if(!Number.isInteger(exponent) || Math.abs(digits.length+exponent-1)>(maximum<=200?4096:131072)) return null;
  const trailing=digits.length-digits.replace(/0+$/,'').length;
  if(digits!=='0'){digits=digits.slice(0,digits.length-trailing);exponent+=trailing;}else exponent=0;
  return { coefficient:BigInt((m[1]==='-'?'-':'')+digits), exponent, digits };
}
const canonical = number => `${number.coefficient}e${number.exponent}`;
export function compareDecimals(left,right) {
  const sign=value=>value.coefficient<0n?-1:value.coefficient>0n?1:0;
  const ls=sign(left),rs=sign(right);if(ls!==rs) return ls<rs?-1:1;if(!ls) return 0;
  const digits=value=>value.digits || String(value.coefficient<0n?-value.coefficient:value.coefficient);
  const a=digits(left),b=digits(right),am=a.length+left.exponent,bm=b.length+right.exponent;
  if(am!==bm) return (am<bm?-1:1)*ls;
  const length=Math.max(a.length,b.length),ap=a.padEnd(length,'0'),bp=b.padEnd(length,'0');
  return (ap<bp?-1:ap>bp?1:0)*ls;
}
function matches(rule,raw,value,lower,upper) {
  if(rule.operator==='is_null') return raw===null;
  if(rule.operator==='not_null') return raw!==null && !!value;
  if(!value || !lower) return false;
  const comparison=compareDecimals(value,lower);
  if(rule.operator==='between') return !!upper && comparison>=0 && compareDecimals(value,upper)<=0;
  return {lt:comparison<0,lte:comparison<=0,eq:comparison===0,ne:comparison!==0,gte:comparison>=0,gt:comparison>0}[rule.operator] || false;
}
export function ruleMatches(rule,raw) {
  return matches(rule,raw,exactDecimal(raw),exactDecimal(rule.value,200),exactDecimal(rule.upper,200));
}
export function formattingOutputs(tile,outputs) {
  const hidden = new Set((tile.hiddenMeasures || []).map(ref=>JSON.stringify([ref.table,ref.column,ref.aggregate || 'none'])));
  const visible = new Set((tile.calculations || []).filter(item=>item.visible!==false).map(item=>item.id));
  const selected=outputs.filter(ref=>ref.kind==='calculated'?visible.has(ref.column):!hidden.has(JSON.stringify([ref.table,ref.column,ref.aggregate || 'none'])));
  if(tile.kind==='kpi' && tile.kpiComparison) for(const [column,label] of [['baseline','Target / baseline'],['absolute','Absolute variance'],['percent','Percentage variance']]) selected.push({table:'@kpi',column,aggregate:'none',kind:`kpi_${column}`,label,dataType:'numeric'});
  return selected;
}
export function formattingErrors(tile, outputs=[]) {
  const errors=[],formats=tile.numberFormats || [],available=new Map(outputs.map(output=>[formatKey(output),output])),seen=new Set();
  if(tile.formatLocale!=null && !FORMAT_LOCALES.includes(tile.formatLocale)) errors.push('Choose a supported report number locale or viewer default.');
  if(formats.length>64) errors.push('Choose at most 64 output formats.');
  for(const [index,setting] of formats.entries()) {
    const name=`Number format ${index+1}`,key=formatKey(setting.output || {}),output=available.get(key);
    if(!output || !numericType(output.dataType)) errors.push(`${name}: the numeric output was removed, hidden or changed type. Rebind or explicitly remove this format.`);
    if(seen.has(key)) errors.push(`${name}: this output already has a format.`);seen.add(key);
    if(!['number','currency','percent'].includes(setting.style)) errors.push(`${name}: choose a number style.`);
    if(setting.precision!=null && (!Number.isInteger(setting.precision) || setting.precision<0 || setting.precision>12)) errors.push(`${name}: precision must be 0–12, or blank for the style default.`);
    if(setting.style==='currency' && !/^[A-Z]{3}$/.test(setting.currency || '')) errors.push(`${name}: choose a three-letter uppercase currency code.`);
    if(!['auto','fraction','whole'].includes(setting.percentScale || 'auto')) errors.push(`${name}: choose how percentage input is scaled.`);
    if((setting.unit || '').length>24 || !/^[\p{L}\p{N}_ °µ²³/·%.-]*$/u.test(setting.unit || '')) errors.push(`${name}: use a short plain unit label.`);
    if((setting.rules || []).length>8) errors.push(`${name}: use at most eight ordered rules.`);
    for(const rule of setting.rules || []) {
      if(!operations.has(rule.operator) || !tones.has(rule.tone) || !symbols[rule.symbol] || !rule.label?.trim() || rule.label.length>40 || /[\u0000-\u001f]/.test(rule.label)) errors.push(`${name}: choose a rule, indicator and readable label.`);
      if(['is_null','not_null'].includes(rule.operator)) {if(rule.value!=null || rule.upper!=null) errors.push(`${name}: NULL rules have no thresholds.`);}
      else {
        const lower=exactDecimal(rule.value,200),upper=rule.operator==='between'?exactDecimal(rule.upper,200):null;
        if(!lower || rule.operator==='between' && (!upper || compareDecimals(lower,upper)>0) || rule.operator!=='between' && rule.upper!=null) errors.push(`${name}: enter exact finite thresholds in increasing order. Your entered text is retained.`);
      }
    }
  }
  return errors;
}
export function numericCsvText(value,type) {
  if(typeof value!=='string' || !/^(smallint|integer|bigint|numeric|decimal|real|double precision|int[248]|float[48])(?:\(\d+(?:,\s*\d+)?\))?$/i.test(type || '')) return false;
  const m=/^[+-]?(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:[eE]([+-]?\d+))?$/.exec(value);if(!m) return false;
  const fraction=m[2] ?? m[3] ?? '',digits=((m[1] || '')+fraction).replace(/^0+/,'') || '0';
  if((m[4] || '').replace(/^[+-]/,'').length>6) return false;
  const magnitude=digits==='0'?Number(m[4] || 0)-fraction.length:digits.length+Number(m[4] || 0)-fraction.length-1;
  return magnitude>=-16383 && magnitude<=131071;
}
export function rawCsvValue(value,type) {
  if(value!==null && typeof value==='object') value=JSON.stringify(value);
  return typeof value==='string' && /^[=+@-]/.test(value.trimStart()) && !numericCsvText(value,type)?"'"+value:value;
}
function references(tile,result) {
  const dimensions=tile.kind==='detail'?tile.detailFields:tile.kind==='pivot'?[...(tile.rowDimensions || []),...(tile.columnDimensions || [])]:tile.dimensions;
  const outputs=[...(dimensions || []).map(field=>({...field,kind:'value'}))];
  if(tile.kind!=='detail') {
    outputs.push(...tile.measures.map(field=>({...field,kind:field.kind || 'value'})));
    for(const field of tile.measures) {
      if(tile.timeAnalysis && (tile.timeAnalysis.comparison || 'none')!=='none') for(const kind of ['comparison','change','percent_change']) outputs.push({...field,kind});
      if(tile.timeAnalysis?.runningTotal) outputs.push({...field,kind:'running_total'});
    }
    outputs.push(...(tile.calculations || []).filter(item=>item.visible!==false).map(item=>({table:'@report',column:item.id,aggregate:'none',kind:'calculated'})).filter(ref=>!outputs.some(other=>formatKey(other)===formatKey(ref))));
  }
  for(const item of result.plan?.calculatedMeasures || []) if(item.index!=null) outputs[item.index]=item.output;
  if(result.plan?.kpiComparison) for(const [index,column] of ['baseline','absolute','percent'].entries()) outputs[result.plan.kpiComparison.indices[index]]={table:'@kpi',column,aggregate:'none',kind:`kpi_${column}`};
  return outputs;
}
const rawText = raw => raw===null?'NULL':typeof raw==='object'?JSON.stringify(raw):String(raw);
const excerpt = raw => {const value=rawText(raw);return value.length>256?value.slice(0,256)+'… (full raw value available in CSV)':value;};
function metadata(tile,result,ref) {
  if(ref?.kind==='calculated') return (result.plan?.calculatedMeasures || []).find(item=>formatKey(item.output)===formatKey(ref)) || {unit:tile.calculations?.find(item=>item.id===ref.column)?.operation==='percent_of'?'percent':'number',scale:100};
  if(ref?.kind==='percent_change' || ref?.kind==='kpi_percent') return {unit:'percent',scale:100};
  const kpi=result.plan?.kpiComparison;
  if(ref?.kind==='kpi_baseline') return {unit:kpi?.baselineUnit || 'number',scale:kpi?.baselineScale || 1};
  if(ref?.kind==='kpi_absolute') return {unit:kpi?.absoluteUnit || 'number',scale:kpi?.absoluteScale || 1};
  return {unit:'number',scale:1};
}
export function sumDecimalText(values) {
  const parsed=values.filter(value=>value!==null).map(value=>exactDecimal(value));if(parsed.some(value=>!value)) return null;
  const exponent=Math.min(0,...parsed.map(value=>value.exponent));
  return canonical({coefficient:parsed.reduce((sum,value)=>sum+value.coefficient*10n**BigInt(value.exponent-exponent),0n),exponent});
}
export function createNumberPresentation(tile,result={},viewerLocale) {
  // Retain only bounded display metadata, never the result's raw row arrays or SQL.
  result={columns:(result.columns || []).map(column=>({name:column.name,dataType:column.dataType})),plan:{
    calculatedMeasures:(result.plan?.calculatedMeasures || []).map(item=>({output:item.output,index:item.index,unit:item.unit,scale:item.scale})),
    kpiComparison:result.plan?.kpiComparison,
  }};
  const refs=references(tile,result);
  const settings=new Map((tile.numberFormats || []).slice(0,64).map(setting=>[formatKey(setting.output),setting]));
  const compiled=new Map([...settings.values()].map(setting=>[setting,(setting.rules || []).slice(0,8).map(rule=>({rule,lower:exactDecimal(rule.value,200),upper:exactDecimal(rule.upper,200)}))]));
  const locale=FORMAT_LOCALES.includes(tile.formatLocale)?tile.formatLocale:viewerLocale || undefined;
  function resolve(index,override={}) {
    const ref=override.ref || refs[index],meta={...metadata(tile,result,ref),...override};
    let setting=ref?settings.get(formatKey(ref)):null,displayShift=0;
    if(!setting && ref && ['comparison','change','running_total'].includes(ref.kind)) {
      const base=settings.get(formatKey({...ref,kind:'value'}));if(base) setting={...base,rules:[]};
    }
    if(!setting && ref?.kind?.startsWith('kpi_') && ref.kind!=='kpi_percent') {
      const base=refs[0] && settings.get(formatKey(refs[0]));if(base) setting={...base,rules:[],...(meta.unit==='percentage_points'?{style:'number',unit:'percentage points'}:{})};
    }
    if(!settings.has(formatKey(ref || {})) && ref?.kind==='kpi_absolute') {
      const primary=refs[0],primaryMeta=metadata(tile,result,primary),base=primary && settings.get(formatKey(primary));
      if((base?.style || (primaryMeta.unit==='percent'?'percent':'number'))==='percent') {
        const fraction=(base?.percentScale || 'auto')==='fraction' || (base?.percentScale || 'auto')==='auto' && !(primaryMeta.unit==='percent' && primaryMeta.scale===100);
        displayShift=fraction?2:0;setting={...base,style:'number',unit:'percentage points',rules:[]};
      }
    }
    const numeric=!!(setting || numericType(result.columns?.[index]?.dataType) || ref && (['count','count_distinct','sum','avg'].includes(ref.aggregate) || ['calculated','percent_change','change','running_total','kpi_baseline','kpi_absolute','kpi_percent'].includes(ref.kind)));
    const style=setting?.style || (meta.unit==='percent'?'percent':'number');
    const whole=(setting?.percentScale || 'auto')==='whole' || (setting?.percentScale || 'auto')==='auto' && (meta.unit==='percent' && meta.scale===100 || meta.unit==='percentage_points');
    const unit=setting?.unit || (meta.unit==='percentage_points'?'percentage points':'');
    const options={style:style==='number'?'decimal':style,...(style==='currency'?{currency:setting?.currency || 'USD'}:{})};
    if(setting?.precision!=null) Object.assign(options,{minimumFractionDigits:setting.precision,maximumFractionDigits:setting.precision});
    else if(style!=='currency') options.maximumFractionDigits=3;
    return {ref,meta,setting,displayShift,numeric,style,whole,unit,options};
  }
  function render(raw,index,override={},derivedAxis=false) {
    const {ref,setting,displayShift,numeric,style,whole,unit,options}=resolve(index,override);
    const eligible=derivedAxis || numeric || typeof raw==='number';
    const floatValue=typeof raw==='number' && /^(real|double precision|float[48])$/i.test(result.columns?.[index]?.dataType || '')?String(raw):raw;
    const decimal=eligible?exactDecimal(floatValue):null;
    const rule=override.suppressRules?null:(compiled.get(setting) || []).find(item=>matches(item.rule,raw,decimal,item.lower,item.upper))?.rule;
    let text=rawText(raw),warning='';
    if(raw!==null && eligible && decimal) {
      const scaled={...decimal,exponent:decimal.exponent+displayShift-(style==='percent' && whole?2:0)};
      const display={...scaled,exponent:scaled.exponent+(style==='percent'?2:0)};
      const digits=decimal.digits,adjusted=digits.length+display.exponent-1;
      const scientific=()=>`${decimal.coefficient<0n?'−':''}${digits[0]}${digits.length>1?'.'+digits.slice(1,13):''}${digits.length>13?'…':''}e${adjusted}${style==='percent'?'%':''}${style==='currency'?' '+options.currency:''}`;
      try {
        if(digits.length>512 || Math.abs(adjusted)>308) {
          text=scientific();warning='Extreme value shown in bounded scientific notation; raw CSV retains the full value.';
        } else {
          let formatter=intl(locale,options);text=formatter.format(canonical(scaled));
          // Generated coordinates may use scientific notation to keep the axis
          // readable. Source values and their labels keep the exact raw path.
          if(derivedAxis && text.length>26) {formatter=intl(locale,{...options,notation:'scientific'});text=formatter.format(canonical(scaled));}
          const places=formatter.resolvedOptions().maximumFractionDigits;
          const absolute={...display,coefficient:display.coefficient<0n?-display.coefficient:display.coefficient};
          if(decimal.coefficient!==0n && compareDecimals(absolute,{coefficient:5n,exponent:-places-1})<0) {text='≈ '+text;warning='Nonzero raw value rounds to zero at this precision.';}
          if(text.length>120 || /∞|NaN/.test(text)) {text=scientific();warning='Extreme value shown in bounded scientific notation; raw CSV retains the full value.';}
        }
      } catch {text=excerpt(raw);warning='Number locale/format unavailable; showing raw value.';}
      if(unit) text+=' '+unit;
    } else if(raw!==null && eligible && !decimal) {text=excerpt(raw);warning='Numeric precision or magnitude unavailable for display; inspect raw CSV.';}
    if(text.length>180) text=text.slice(0,177)+'…';
    const indicator=rule?`${symbols[rule.symbol]} ${rule.label}`:'';
    const description=[style==='currency'?(setting?.currency || 'USD'):style==='percent'?'%':'',unit,setting?.precision!=null?`${setting.precision} decimals`:''].filter(Boolean).join(' · ');
    return {description,text:text+(indicator?' · '+indicator:''),tone:rule?.tone || 'neutral',indicator,warning,title:`Raw: ${excerpt(raw)}${warning?' · '+warning:''}`,ref};
  }
  const present=(raw,index,override={})=>render(raw,index,override);
  // Only finite numbers produced by chart geometry enter here. Converting that
  // approximation to text must never relax validation of untrusted raw numbers.
  present.axis=(value,index)=>{
    if(typeof value!=='number' || !Number.isFinite(value)) return {text:'Axis unavailable',warning:'A finite generated axis tick is required.',title:'Axis unavailable',tone:'neutral',indicator:'',description:'',ref:null};
    const display=render(String(value),index,{suppressRules:true},true);
    const warning='Approximate axis tick computed from chart geometry; point labels and RAW CSV retain source values.';
    return {...display,warning,title:`Approximate axis tick: ${value} · ${warning}`};
  };
  // The explanation/capture consumer receives the same resolved semantics,
  // without retaining formatted values or copying any raw row arrays.
  present.describe=(index,override={})=>{
    const resolved=resolve(index,override),{ref,setting,meta,style,unit,options}=resolved;
    if(!ref || !resolved.numeric) return null;
    let actual;try{actual=intl(locale,options).resolvedOptions();}catch{return null;}
    if(actual.locale.length>64) return null;
    return {output:{table:ref.table,column:ref.column,aggregate:ref.aggregate || 'none',kind:ref.kind || 'value'},
      style,precision:setting?.precision ?? null,currency:style==='currency'?options.currency:null,
      percentScale:setting?.percentScale || 'auto',unit,locale:actual.locale,localeMode:tile.formatLocale?'report':'viewer',
      queryUnit:['number','percent','percentage_points'].includes(meta.unit)?meta.unit:'number',queryScale:meta.scale===100?100:1,
      displayScale:resolved.displayShift===2?100:1,minimumFractionDigits:actual.minimumFractionDigits,maximumFractionDigits:actual.maximumFractionDigits,
      rules:(setting?.rules || []).slice(0,8).map(rule=>({operator:rule.operator,value:rule.value ?? null,upper:rule.upper ?? null,label:rule.label,tone:rule.tone,symbol:rule.symbol}))};
  };
  return present;
}
export function decorateNumber(node,presentation) {
  node.textContent=presentation.text;node.title=presentation.title;
  node.setAttribute('data-number-tone',presentation.tone);
  node.setAttribute('aria-label',presentation.text+(presentation.warning?' · '+presentation.warning:''));
}

export function numberFormatLegend(tile,result={},element) {
  const settings=(tile.numberFormats || []).slice(0,64),withRules=settings.filter(setting=>setting.rules?.length);
  if(!withRules.length && !tile.formatLocale) return null;
  const host=element('details',{className:'number-format-legend'}),refs=references(tile,result);
  host.append(element('summary',{text:`Number display · ${tile.formatLocale || 'viewer locale'}${withRules.length?' · condition indicators':''}`}));
  if(withRules.length) host.append(element('p',{text:'First matching rule wins. Thresholds compare raw values before display scaling or rounding.'}));
  for(const setting of withRules) {
    const index=refs.findIndex(ref=>ref && formatKey(ref)===formatKey(setting.output));
    host.append(element('p',{text:result.columns?.[index]?.name || setting.output.column}));
    const list=element('ol');
    for(const rule of setting.rules.slice(0,8)) list.append(element('li',{text:`${symbols[rule.symbol]} ${rule.label} · raw ${operatorText[rule.operator]}${rule.value!=null?' '+rule.value:''}${rule.upper!=null?' to '+rule.upper:''}`,attrs:{'data-number-tone':rule.tone}}));
    host.append(list);
  }
  return host;
}

/** Real form controls; drafts keep invalid references and exact threshold text. */
export function numberFormatEditor(tile,{outputs,onChange,element,modelSelect}) {
  tile.numberFormats ||= [];
  const host=element('section',{className:'number-format-editor'}),button=(text,action,disabled=false)=>{const node=element('button',{type:'button',className:'ui-button',text});node.onclick=action;node.disabled=disabled;return node;};
  const input=(label,value,change,attrs={})=>{const node=element('input',{attrs:{'aria-label':label,...attrs}});node.value=value ?? '';node.oninput=()=>change(node.value);return node;};
  const labeled=(text,node)=>element('label',{className:'stack'},[text,node]);
  host.append(element('p',{className:'hint',text:'Presentation only. Sorting, calculations, contributing records and CSV keep raw values. Rules compare exact raw values before rounding; the first matching rule wins. Thresholds use a dot decimal separator and no grouping, regardless of locale.'}),
    labeled('Report number locale',modelSelect('Report number locale',[['','Viewer locale (default)'],...FORMAT_LOCALES.map(locale=>[locale,locale])],tile.formatLocale || '',value=>{tile.formatLocale=value || null;onChange();})));
  const numeric=outputs.filter(output=>numericType(output.dataType));
  tile.numberFormats.forEach((setting,index)=>{
    const key=formatKey(setting.output),choices=numeric.map(output=>[formatKey(output),output.label]);
    if(!choices.some(([value])=>value===key)) choices.unshift([key,'Unavailable numeric output · choose a replacement']);
    const box=element('section',{className:'number-format-card'}),prefix=`Format ${index+1}`;
    box.append(labeled('Output',modelSelect(`${prefix} output`,choices,key,value=>{const [table,column,aggregate,kind]=JSON.parse(value);setting.output={table,column,aggregate,kind};onChange();})),
      labeled('Style',modelSelect(`${prefix} style`,[['number','Number'],['currency','Currency'],['percent','Percent']],setting.style,value=>{setting.style=value;if(value==='currency') setting.currency ||= 'USD';onChange();})),
      labeled('Decimal places (blank: style default)',input(`${prefix} decimal places`,setting.precision,value=>{setting.precision=value===''?null:/^\d{1,2}$/.test(value)?Number(value):value;},{inputmode:'numeric',maxlength:20})),
      labeled('Unit suffix',input(`${prefix} unit`,setting.unit,value=>{setting.unit=value;},{maxlength:24})));
    if(setting.style==='currency') box.append(labeled('Currency code',input(`${prefix} currency`,setting.currency,value=>{setting.currency=value.toUpperCase();},{maxlength:3})));
    if(setting.style==='percent') box.append(labeled('Percentage input',modelSelect(`${prefix} percent scale`,[['auto','Automatic · use output semantics'],['fraction','Fraction · 0.25 means 25%'],['whole','Already 0–100 · 25 means 25%']],setting.percentScale || 'auto',value=>{setting.percentScale=value;})));
    for(const [ri,rule] of (setting.rules || []).entries()) {
      const name=`${prefix} rule ${ri+1}`,card=element('section',{className:'number-rule'});
      card.append(labeled('Match raw value',modelSelect(`${name} operator`,[...operations].map(op=>[op,{lt:'Less than',lte:'At most',eq:'Equals',ne:'Not equal',gte:'At least',gt:'Greater than',between:'Between (inclusive)',is_null:'Is NULL',not_null:'Is not NULL'}[op]]),rule.operator,value=>{rule.operator=value;if(['is_null','not_null'].includes(value)){rule.value=null;rule.upper=null;}else {rule.value ??='0';if(value!=='between') rule.upper=null;}onChange();})));
      if(!['is_null','not_null'].includes(rule.operator)) card.append(labeled('Exact raw threshold',input(`${name} threshold`,rule.value,value=>{rule.value=value;},{inputmode:'decimal',maxlength:200})));
      if(rule.operator==='between') card.append(labeled('Upper threshold',input(`${name} upper threshold`,rule.upper,value=>{rule.upper=value;},{inputmode:'decimal',maxlength:200})));
      card.append(labeled('Accessible label',input(`${name} label`,rule.label,value=>{rule.label=value;},{maxlength:40})),
        labeled('Tone',modelSelect(`${name} tone`,[...tones].map(tone=>[tone,tone]),rule.tone,value=>{rule.tone=value;})),
        labeled('Symbol',modelSelect(`${name} symbol`,Object.entries(symbols).map(([key,symbol])=>[key,symbol]),rule.symbol,value=>{rule.symbol=value;})),
        button(`Move ${name} earlier`,()=>{if(ri){const [item]=setting.rules.splice(ri,1);setting.rules.splice(ri-1,0,item);onChange();}},ri===0),button(`Move ${name} later`,()=>{if(ri<setting.rules.length-1){const [item]=setting.rules.splice(ri,1);setting.rules.splice(ri+1,0,item);onChange();}},ri===setting.rules.length-1),button(`Remove ${name}`,()=>{setting.rules.splice(ri,1);onChange();}));box.append(card);
    }
    const addRule=button(`Add ${prefix} rule`,()=>{setting.rules ||= [];if(setting.rules.length<8){setting.rules.push({operator:'gte',value:'0',upper:null,label:'At threshold',tone:'neutral',symbol:'dot'});onChange();}});addRule.disabled=(setting.rules || []).length>=8;
    box.append(addRule,button(`Remove ${prefix}`,()=>{tile.numberFormats.splice(index,1);onChange();}));host.append(box);
  });
  const add=button('Add output number format',()=>{const output=numeric.find(output=>!tile.numberFormats.some(setting=>formatKey(setting.output)===formatKey(output)));if(output){tile.numberFormats.push({output:{table:output.table,column:output.column,aggregate:output.aggregate,kind:output.kind},style:output.kind==='percent_change' || output.kind==='kpi_percent' || output.kind==='calculated' && tile.calculations?.find(item=>item.id===output.column)?.operation==='percent_of'?'percent':'number',precision:null,currency:null,percentScale:'auto',unit:'',rules:[]});onChange();}});
  add.disabled=tile.numberFormats.length>=64 || numeric.every(output=>tile.numberFormats.some(setting=>formatKey(setting.output)===formatKey(output)));
  host.append(add);for(const text of formattingErrors(tile,outputs)) host.append(element('p',{className:'error-message',attrs:{role:'alert'},text}));return host;
}
