import assert from "node:assert/strict";
import test from "node:test";
import { createAsyncValueSelect, uniqueValueOptions } from "../../src/schemii/common/web/assets/async-value-select.js";

function documentStub() {
  class Node extends EventTarget {
    constructor() { super(); this.children=[]; this.style={}; this.attributes={}; this.classList={toggle(){}}; this.scrollHeight=200; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children=nodes; }
    setAttribute(k,v) { this.attributes[k]=v; }
    removeAttribute(k) { delete this.attributes[k]; }
    querySelectorAll() { return this.children.filter(n=>n.attributes.role==="option"); }
    closest() { return null; }
    contains(node) { return this===node || this.children.some(n=>n.contains(node)); }
    focus() { this.dispatchEvent(new Event("focus")); }
    scrollIntoView() {}
    getBoundingClientRect() { return {top:20,bottom:56,left:10,width:300}; }
    remove() {}
  }
  const doc=new EventTarget(); doc.createElement=()=>new Node(); doc.body=new Node();
  doc.defaultView=Object.assign(new EventTarget(),{innerWidth:360,innerHeight:700}); return doc;
}
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
function setup(options) {
  const documentRef=documentStub();
  const select=createAsyncValueSelect({documentRef,...options});
  const input=select.root.children[1].children[0];
  return {documentRef,select,input};
}

test("data options preserve case, whitespace, and primitive type",()=>{
  assert.deepEqual(uniqueValueOptions(["A","a"," A ",1,"1",false,"false",null,"A"].map(value=>({value}))).map(o=>o.value),["A","a"," A ",1,"1",false,"false"]);
});

test("single commits preserve typed values and do not reopen on focus",async()=>{
  let committed;
  const {documentRef,select,input}=setup({loadOptions:async()=>[{value:false,label:"No"}],onChange:v=>committed=v});
  input.dispatchEvent(new Event("click")); await settle();
  const list=documentRef.body.children[0];
  list.children[0].dispatchEvent(new Event("click"));
  assert.equal(committed,false); assert.equal(input.value,"No"); assert.equal(list.hidden,true);
  input.focus(); assert.equal(list.hidden,true); select.destroy();
});

test("multiple selections survive searches and chips remove exact values",async()=>{
  let committed;
  const {documentRef,select,input}=setup({multiple:true,value:["A"],loadOptions:async q=>q ? [{value:"a",label:"Lower"}] : [{value:"A",label:"Upper"}],onChange:v=>committed=v});
  input.dispatchEvent(new Event("click")); await settle();
  input.value="lower"; input.dispatchEvent(new Event("input"));
  await new Promise(resolve=>setTimeout(resolve,230));
  const list=documentRef.body.children[0]; list.children[0].dispatchEvent(new Event("click"));
  assert.deepEqual(committed,["A","a"]);
  assert.equal(list.children[0].attributes["aria-selected"],"true");
  select.root.children[0].children[0].dispatchEvent(new Event("click"));
  assert.deepEqual(committed,["a"]); select.destroy();
});

test("modal ownership and stale asynchronous response suppression",async()=>{
  let resolve;
  const {documentRef,select,input}=setup({loadOptions:()=>new Promise(r=>resolve=r)});
  const dialog=documentRef.createElement("dialog"); select.root.closest=()=>dialog;
  input.dispatchEvent(new Event("click"));
  assert.equal(dialog.children[0].attributes.role,"listbox");
  select.destroy(); resolve([{value:"late"}]); await settle();
  assert.equal(dialog.children[0].hidden,true);
  assert.equal(input.attributes["aria-expanded"],"false");
});

test("loader failure is visible rather than becoming an empty list",async()=>{
  const {documentRef,select,input}=setup({loadOptions:async()=>{throw new Error("Read permission denied");}});
  input.dispatchEvent(new Event("click")); await settle();
  assert.equal(documentRef.body.children[0].children[0].textContent,"Read permission denied"); select.destroy();
});
