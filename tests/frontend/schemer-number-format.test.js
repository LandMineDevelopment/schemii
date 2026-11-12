import test from 'node:test';
import assert from 'node:assert/strict';
import { createNumberPresentation, exactDecimal, compareDecimals, ruleMatches, formattingErrors, formatKey, numberFormatCacheSize, sumDecimalText } from '../../src/schemii/schemer/web/number-format.js';
import { renderVisualization } from '../../src/schemii/schemer/web/visualizations.js';
import { csvContent } from '../../src/schemii/schemer/web/report-state.js';
const field=(column,aggregate='none')=>({table:'facts',column,aggregate});
const ref=(column='amount',aggregate='sum',kind='value')=>({...field(column,aggregate),kind});
const setting=(changes={})=>({output:ref(),style:'currency',currency:'EUR',precision:2,percentScale:'auto',unit:'',rules:[],...changes});
const tile=(changes={})=>({id:'tile',title:'Revenue',kind:'aggregate',dimensions:[field('region')],measures:[field('amount','sum')],detailFields:[field('id')],numberFormats:[setting()],formatLocale:'de-DE',...changes});
const result=(rows=[['East','1234.567']],extra={})=>({columns:[{name:'Region',dataType:'text'},{name:'Amount',dataType:'numeric'}],rows,plan:{},...extra});

test('exact decimal text survives locale currency rounding and does not become a JS Number',()=>{
  const raw='9007199254740993.123456789', data=result([['East',raw]]), before=JSON.stringify(data);
  assert.equal(createNumberPresentation(tile(),data)(raw,1).text,'9.007.199.254.740.993,12 €');
  assert.equal(createNumberPresentation(tile({formatLocale:'en-US'}),data)(raw,1).text,'€9,007,199,254,740,993.12');
  assert.equal(createNumberPresentation(tile({formatLocale:null}),data,'hi-IN')(raw,1).text,'€9,00,71,99,25,47,40,993.12');
  assert.equal(JSON.stringify(data),before);
  assert.equal(compareDecimals(exactDecimal(raw),exactDecimal('9007199254740993.123456788')),1);
});

test('ordered rules compare raw negative/small/high-precision values before display rounding',()=>{
  const rules=[{operator:'gt',value:'1.002',label:'Above',tone:'positive',symbol:'up'}, {operator:'gt',value:'0',label:'Positive',tone:'caution',symbol:'dot'}, {operator:'is_null',label:'Missing',tone:'negative',symbol:'warning'}];
  const present=createNumberPresentation(tile({numberFormats:[setting({rules})]}),result());
  assert.equal(present('1.004',1).text,'1,00 € · ▲ Above');
  assert.equal(present(null,1).text,'NULL · ! Missing');
  assert.equal(present('-1.004',1).text,'-1,00 €');
  assert.equal(ruleMatches({operator:'eq',value:'9007199254740993.00001'},'9007199254740993.000010'),true);
  assert.equal(ruleMatches({operator:'lt',value:'1e-12'},'9e-13'),true);
  assert.equal(ruleMatches({operator:'between',value:'-2.5',upper:'-1.1'},'-1.1'),true);
  assert.equal(ruleMatches({operator:'is_null'},0),false);
  assert.equal(ruleMatches({operator:'not_null'},'NaN'),false);
});

test('fraction, already-whole and calculated/time percentage metadata share exact display semantics',()=>{
  const data=result([['East','0.00125']]), config=tile({formatLocale:'en-US',numberFormats:[setting({style:'percent',precision:3})]});
  assert.equal(createNumberPresentation(config,data)('0.00125',1).text,'0.125%');
  assert.equal(createNumberPresentation(tile({...config,numberFormats:[setting({style:'percent',precision:3,percentScale:'whole'})]}),data)('0.00125',1).text,'0.001%');
  const calc={table:'@report',column:'share',aggregate:'none',kind:'calculated'};
  const computed=tile({measures:[],calculations:[{id:'share',label:'Share',operation:'percent_of'}],formatLocale:'en-US',numberFormats:[]});
  assert.equal(createNumberPresentation(computed,result([['East','25']],{plan:{calculatedMeasures:[{output:calc,index:1,unit:'percent',scale:100}]}}))('25',1).text,'25%');
  const time=tile({formatLocale:'en-US',timeAnalysis:{comparison:'previous_period'},numberFormats:[]});
  assert.equal(createNumberPresentation(time,result([['East','5','3','2','66.666666']]))('66.666666',4).text,'66.667%');
});

test('KPI baseline and variance scale defaults do not multiply already-whole percentages',()=>{
  const comparison={indices:[1,2,3,4],baselineUnit:'percent',baselineScale:100,absoluteUnit:'percentage_points',absoluteScale:1,percentUnit:'percent',percentScale:100};
  const calc={table:'@report',column:'share',aggregate:'none',kind:'calculated'};
  const config=tile({kind:'kpi',dimensions:[],measures:[],calculations:[{id:'share',operation:'percent_of',visible:true}],numberFormats:[],formatLocale:'en-US'});
  const data={columns:Array.from({length:5},(_,i)=>({name:String(i),dataType:'numeric'})),rows:[['25','10','15','150','favorable']],plan:{calculatedMeasures:[{output:calc,index:0,unit:'percent',scale:100}],kpiComparison:comparison}};
  const present=createNumberPresentation(config,data);
  assert.equal(present('25',0).text,'25%');assert.equal(present('10',1).text,'10%');assert.equal(present('15',2).text,'15 percentage points');assert.equal(present('150',3).text,'150%');
  const fractions=tile({kind:'kpi',dimensions:[],formatLocale:'en-US',numberFormats:[setting({style:'percent',precision:1})]});
  const fractional=createNumberPresentation(fractions,{...data,plan:{kpiComparison:{...comparison,baselineUnit:'number',baselineScale:1,absoluteUnit:'number'}}});
  assert.equal(fractional('0.25',0).text,'25.0%');assert.equal(fractional('0.1',1).text,'10.0%');assert.equal(fractional('0.15',2).text,'15.0 percentage points');
});

test('output bindings survive reorder and label changes while stale/non-numeric bindings require explicit repair',()=>{
  const config=tile({measures:[field('other','sum'),field('amount','sum')]}),data=result([['East','5','1234.567']]);
  data.columns.push({name:'Renamed amount',dataType:'numeric'});
  assert.equal(createNumberPresentation(config,data)('1234.567',2).text,'1.234,57 €');
  const available=[{...ref(),dataType:'numeric'}];assert.deepEqual(formattingErrors(tile(),available),[]);
  assert.ok(formattingErrors(tile(),[]).some(error=>/removed, hidden/.test(error)));
  const stale=JSON.stringify(tile().numberFormats);formattingErrors(tile(),[]);assert.equal(stale,JSON.stringify(tile().numberFormats));
  assert.ok(formattingErrors(tile(),[{...ref(),dataType:'text'}]).length);
  assert.equal(formatKey(ref()),formatKey({...ref(),label:'New label'}));
});

test('NULL, nonfinite/unsafe values, tiny percentages and extreme strings have bounded honest presentation',()=>{
  const present=createNumberPresentation(tile({formatLocale:'en-US',numberFormats:[setting({style:'percent',precision:3})]}),result());
  assert.match(present('0.000000000001',1).text,/^≈ 0\.000%/);
  assert.match(present('0.000000000001',1).warning,/Nonzero/);
  for(const value of [NaN,Infinity,9007199254740992,'not-a-number']) assert.match(present(value,1).warning,/precision|magnitude/);
  for(const value of ['1e4096','9'.repeat(5000)]) {const display=present(value,1);assert.ok(display.text.length<=180);assert.ok(display.title.length<400);}
  assert.equal(sumDecimalText(['9007199254740993.1','0.9']), '90071992547409940e-1');
  for(let i=0;i<100;i++) createNumberPresentation(tile({numberFormats:[setting({currency:`X${String.fromCharCode(65+Math.floor(i/26))}${String.fromCharCode(65+i%26)}`})]}),result())('1',1);
  assert.equal(numberFormatCacheSize(),64);
});

class FakeNode {
  constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.dataset={};this.style={setProperty(name,value){this[name]=value;}};this.scrollTop=0;this.events={};this.classList={add:(...values)=>{this.className=[this.className,...values].filter(Boolean).join(' ');},toggle(){},contains:value=>(this.className || '').split(' ').includes(value)};}
  append(...nodes){for(const node of nodes){if(node?.tag==='fragment') this.append(...node.children);else this.children.push(node);}}
  replaceChildren(...nodes){this.children=[];this.content=undefined;this.append(...nodes);}
  set textContent(value){this.content=String(value);this.children=[];}
  get textContent(){return (this.content || '')+this.children.map(child=>child?.textContent ?? child ?? '').join('');}
  setAttribute(name,value){this.attributes[name]=String(value);if(name==='class')this.className=value;}
  getAttribute(name){return this.attributes[name];}
  addEventListener(name,callback){this.events[name]=callback;}
  querySelector(selector){return this.querySelectorAll(selector)[0] || null;}
  querySelectorAll(selector){const all=this.children.filter(child=>child instanceof FakeNode).flatMap(child=>[child,...child.querySelectorAll('*')]);if(selector==='*')return all;if(selector==='tbody tr')return all.filter(node=>node.tag==='tbody').flatMap(node=>node.children.filter(child=>child.tag==='tr'));if(selector.startsWith('.'))return all.filter(node=>node.className?.split(' ').includes(selector.slice(1)));return all.filter(node=>node.tag===selector);}
}
function dom(){globalThis.Node=FakeNode;globalThis.document={createElement:tag=>new FakeNode(tag),createElementNS:(_,tag)=>new FakeNode(tag),createTextNode:text=>({textContent:text}),createDocumentFragment:()=>new FakeNode('fragment')};}

test('real table/bar/line/donut/KPI/pivot renderers share number text, indicators and raw drill membership',()=>{
  dom();const rule={operator:'gte',value:'1000',label:'High',tone:'positive',symbol:'up'};
  const expected='1.234,57 € · ▲ High';
  for(const kind of ['aggregate','detail','bar','line','donut','kpi','pivot']) {
    let config=tile({kind,numberFormats:[setting({rules:[rule]})]}),data=result(),selected;
    if(kind==='detail') config={...config,detailFields:[field('region'),field('amount')],numberFormats:[setting({output:ref('amount','none'),rules:[rule]})]};
    if(kind==='kpi') {config={...config,dimensions:[]};data={...data,columns:data.columns.slice(1),rows:[['1234.567']]};}
    if(kind==='pivot') {config={...config,dimensions:[],rowDimensions:[field('region')],columnDimensions:[field('category')],pivotTotals:{maximumColumns:12,maximumRows:100}};data={...data,columns:[data.columns[0],{name:'Category',dataType:'text'},data.columns[1]],rows:[['East','A','1234.567',1,1,'data',1,false,1,'cell']]};}
    const before=JSON.stringify(data.rows),host=new FakeNode('main');renderVisualization(host,config,data,{onDrill:value=>{selected=value;}});
    assert.ok(host.textContent.includes(expected),`${kind}: ${host.textContent}`);
    assert.equal(JSON.stringify(data.rows),before);
    assert.ok(!host.querySelectorAll('img').length);
    if(kind==='bar') {const number=host.querySelectorAll('.bar-number')[0];assert.equal(number.attributes['data-number-tone'],'positive');const mark=host.querySelectorAll('.bar-value-row')[0];mark.onclick({stopPropagation(){}});assert.equal(selected.dimensions[0].value,'East');}
    if(kind==='pivot') {const button=host.querySelectorAll('button')[0];button.onclick({stopPropagation(){}});assert.deepEqual(selected.dimensions.map(value=>value.value),['East','A']);}
  }
});

test('format rule labels are text and raw CSV excludes all display artifacts',()=>{
  dom();const data=result([['East','-9007199254740993.123456789']]),config=tile({numberFormats:[setting({rules:[{operator:'lt',value:'0',label:'<img src=x onerror=alert(1)>',tone:'negative',symbol:'down'}]})]});
  const host=new FakeNode('main');renderVisualization(host,config,data,{});
  assert.ok(host.textContent.includes('<img'));assert.equal(host.querySelectorAll('img').length,0);
  const csv=csvContent(data);assert.ok(csv.includes('-9007199254740993.123456789'));assert.ok(!csv.includes('€'));assert.ok(!csv.includes('img'));
});

test('cached raw CSV applies spreadsheet text protection with the same exact numeric exemption as full CSV',()=>{
  const values=['-9007199254740993','-12345678901234567890.123456789','-1e-100','+0','0','-0'];
  for(const value of values) {
    const numeric=csvContent({columns:[{name:'n',dataType:'numeric'}],rows:[[value]]});assert.equal(numeric.split('\r\n')[1],`"${value}"`);
    for(const dataType of ['text','jsonb','json','numeric[]']) {
      const escaped=csvContent({columns:[{name:'value',dataType}],rows:[[value]]});
      assert.equal(escaped.split('\r\n')[1],`"${/^[+-]/.test(value)?"'":''}${value}"`);
    }
  }
  for(const value of ['-1;=2','-1\n=2',' -1','\t+1','-1e99999999999999999999','-Infinity','+NaN','@SUM(1)','=1+1']) {
    for(const dataType of ['numeric','text','jsonb']) {
      const csv=csvContent({columns:[{name:'value',dataType}],rows:[[value]]});assert.ok(csv.includes('"\''+value));
    }
  }
});

test('percentage conditions use raw fractions and pivot calculations keep their semantic identity',()=>{
  const rules=[{operator:'gt',value:'20',label:'Raw above 20',tone:'negative',symbol:'warning'},{operator:'gt',value:'0.2',label:'Raw above .2',tone:'positive',symbol:'check'}];
  assert.equal(createNumberPresentation(tile({formatLocale:'en-US',numberFormats:[setting({style:'percent',precision:1,rules})]}),result())('0.25',1).text,'25.0% · ✓ Raw above .2');
  const calc={table:'@report',column:'share',aggregate:'none',kind:'calculated'};
  const config=tile({kind:'pivot',dimensions:[],rowDimensions:[field('region')],columnDimensions:[field('category')],measures:[calc],calculations:[{id:'share',operation:'percent_of',visible:true}],numberFormats:[setting({output:calc,style:'percent',precision:2})],formatLocale:'en-US'});
  const data={columns:[{name:'r'},{name:'c'},{name:'Share',dataType:'numeric'}],rows:[['East','A','25']],plan:{calculatedMeasures:[{output:calc,index:2,unit:'percent',scale:100}]}};
  assert.equal(createNumberPresentation(config,data)('25',2).text,'25.00%');
});

test('extreme finite SQL numerics still match exact thresholds without allocating a huge formatted string',()=>{
  const config=tile({formatLocale:'en-US',numberFormats:[setting({style:'number',rules:[{operator:'gt',value:'1e4096',label:'Extreme',tone:'caution',symbol:'warning'}]})]});
  const present=createNumberPresentation(config,result());
  const value=present('1e5000',1);assert.ok(value.text.includes('! Extreme'));assert.ok(value.text.length<100);
  assert.equal(ruleMatches({operator:'gt',value:'9.9'},'9'.repeat(5000)+'e-4999'),true);
  assert.equal(ruleMatches({operator:'not_null'},'1e5000'),true);
  assert.equal(compareDecimals(exactDecimal('-1e5000'),exactDecimal('-1e4096')),-1);
});

test('capture descriptor comes from the same resolver, is bounded and does not contain row values',()=>{
  const config=tile({numberFormats:[setting({rules:[{operator:'gt',value:'1',label:'High',tone:'positive',symbol:'up'}]})]});
  const data=result([['East','9007199254740993.123456789']]),before=JSON.stringify(data);
  const descriptor=createNumberPresentation(config,data).describe(1);
  assert.deepEqual(descriptor.output,ref());assert.equal(descriptor.currency,'EUR');assert.equal(descriptor.locale,'de-DE');
  assert.equal(descriptor.maximumFractionDigits,2);assert.equal(descriptor.rules[0].value,'1');
  assert.ok(!JSON.stringify(descriptor).includes('9007199254740993'));assert.equal(JSON.stringify(data),before);
  assert.equal(createNumberPresentation(config,data).describe(0),null);
});

test('loading and absent result metadata never creates fake numeric zeroes or crashes',()=>{
  dom();const config=tile(),host=new FakeNode('main');renderVisualization(host,config,{loading:true},{});
  assert.ok(host.textContent.includes('Waiting for the first rows'));
  assert.equal(createNumberPresentation(config)(null,1).text,'NULL');
});
