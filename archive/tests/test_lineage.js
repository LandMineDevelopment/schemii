const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.textContent = "";
    this.listeners = {};
  }
  append(...children) { this.children.push(...children); }
  addEventListener(type, callback) { this.listeners[type] = callback; }
}

async function main() {
  const source = fs.readFileSync("src/schemii/schemer_web/app.js", "utf8");
  const start = source.indexOf("function lineageSection");
  const end = source.indexOf("function appendRelationColumns", start);
  assert.notEqual(start, -1, "lineage renderer start is missing");
  assert.notEqual(end, -1, "lineage renderer end is missing");
  const copied = [];
  const context = vm.createContext({
    document: { createElement: tag => new Element(tag) },
    navigator: { clipboard: { writeText: async value => { copied.push(value); } } },
    elements: { lineageBody: new Element("main"), lineageStatus: new Element("p") },
  });
  vm.runInContext(`${source.slice(start, end)}
    globalThis.appendLineageCode = appendLineageCode;
    globalThis.copyLineageValue = copyLineageValue;
  `, context);

  const body = new Element("section");
  const untrusted = '</code><script>globalThis.compromised = true</script>';
  context.appendLineageCode(body, "View query", untrusted, "view query");
  const code = body.children[0].children[1].children[0];
  assert.equal(code.textContent, untrusted, "definition text must remain inert text content");
  assert.equal(context.compromised, undefined);
  await context.copyLineageValue("SELECT * FROM t WHERE id = %s", "aggregation SQL");
  assert.deepEqual(copied, ["SELECT * FROM t WHERE id = %s"]);
  assert.equal(context.elements.lineageStatus.textContent, "aggregation SQL copied.");
  console.log("Data lineage rendering and copy tests passed");
}

main().catch(error => { console.error(error); process.exitCode = 1; });
