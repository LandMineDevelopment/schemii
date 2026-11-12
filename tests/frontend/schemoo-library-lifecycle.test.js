import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import test from "node:test";
import { importedDraft, defaultSourceSelection } from "../../src/schemii/schemoo/web/model-draft.js";
import { splitDraft } from "../../src/schemii/schemoo/web/model-state.js";
import { addSourceSelection } from "../../src/schemii/schemoo/web/source-reconciliation.js";
import { ensureAliasConnections } from "../../src/schemii/schemoo/web/alias-model.js";

const web = new URL("../../src/schemii/schemoo/web/", import.meta.url);
const column = name => ({ name, dataType: "integer", nullable: false });
const catalog = () => ({ tables: ["a", "b", "c", "d"].map(name => ({ name, columns: [column("id"), column("name")] })), relationships: [], positions: [] });

test("the visible add-table action preserves unrelated relationships and alias exposure", () => {
  const source = catalog(), draft = importedDraft(source, source.tables.slice(0, 3).map(table => ({ table: table.name, columns: ["id"] })));
  draft.nodes.push({ id: "alias_b", table: "b", label: "Other B" });
  source.relationships = [
    { id: "selected", sourceTable: "d", sourceColumn: "id", targetTable: "a", targetColumn: "id" },
    { id: "unrelated", sourceTable: "b", sourceColumn: "id", targetTable: "c", targetColumn: "id" },
  ];
  const app = readFileSync(new URL("prototype.js", web), "utf8");
  const action = app.slice(app.indexOf("function addTable(table) {"), app.indexOf("function renderAliasTools()"));
  runInNewContext(`${action}\naddTable('d');`, {
    draft, catalog: source, addSourceSelection, ensureAliasConnections,
    selected: null, diagnostics: {},
    showError(message) { throw new Error(message); },
    refreshForms() {}, changed() {}, inspectSelection() {}, $: () => ({}),
  });
  assert.deepEqual(draft.edges.map(edge => edge.relationshipId), ["selected"]);
  assert.deepEqual(draft.exposedFields.filter(field => field.table === "d").map(field => field.column), ["id", "name"]);
  assert.equal(draft.exposedFields.some(field => field.table === "alias_b"), false);
});

class Element {
  constructor(tag, options = {}, children = []) {
    this.tag = tag;
    Object.assign(this, options);
    this.children = [];
    this.value ||= "";
    this.open ||= false;
    this.append(...children);
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  querySelector() { return null; }
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function libraryFixture() {
  const dialog = new Element("dialog"), content = new Element("div"), post = deferred();
  const selects = new Map(), opened = [];
  const small = { ...catalog(), tables: catalog().tables.slice(0, 1), fingerprint: "one" };
  const source = readFileSync(new URL("model-library.js", web), "utf8")
    .replace(/^import .*;\n/gm, "").replace(/^export \{.*;\n/gm, "").replace(/^export /gm, "");
  const runtime = runInNewContext(`${source}\n({ openModelLibrary })`, {
    importedDraft, defaultSourceSelection, splitDraft, structuredClone,
    requestJson: async (url, options = {}) => {
      if (options.method === "POST") return post.promise;
      if (url.endsWith("/models")) return { models: [] };
      if (url.includes("/connections?")) return { connections: [{ id: "pg_offline", name: "Owned", database: "offline", username: "offline" }] };
      if (url.includes("/namespaces")) return { namespaces: [{ name: "public", system: false }] };
      if (url.includes("/catalog?")) return small;
      throw new Error(`Unexpected request ${url}`);
    },
    element: (tag, options, children) => new Element(tag, options, children),
    modelSelect: (label, options, initial, callback) => { selects.set(label, callback); return new Element("select"); },
    disposeSelects() {}, confirmModelDeletion() {}, createIconButton: () => new Element("button"), namedAction() {},
    document: { getElementById: id => id === "model-library" ? dialog : content },
  });
  await runtime.openModelLibrary(async id => { opened.push(id); });
  await selects.get("Source connection")("pg_offline");
  selects.get("Source schema")("public");
  await new Promise(resolve => setImmediate(resolve));
  const form = content.children.find(item => item.tag === "form");
  form.children.find(item => item.tag === "label").children.find(item => item.tag === "input").value = "Owned model";
  return { ...runtime, dialog, content, form, post, opened };
}

for (const outcome of ["success", "failure"]) {
  test(`late create ${outcome} cannot navigate or close a reopened model library`, async () => {
    const fixture = await libraryFixture();
    const pending = fixture.form.onsubmit({ preventDefault() {} });
    fixture.dialog.close();
    await fixture.openModelLibrary(async id => { fixture.opened.push(`new:${id}`); });
    const newerContent = [...fixture.content.children];
    if (outcome === "success") fixture.post.resolve({ id: "old_created_model" });
    else fixture.post.reject(new Error("Old create failed"));
    await pending;
    assert.deepEqual(fixture.opened, []);
    assert.equal(fixture.dialog.open, true);
    assert.deepEqual(fixture.content.children, newerContent);
  });
}

test("current create completion still opens its saved model", async () => {
  const fixture = await libraryFixture();
  const pending = fixture.form.onsubmit({ preventDefault() {} });
  fixture.post.resolve({ id: "current_model" });
  await pending;
  assert.deepEqual(fixture.opened, ["current_model"]);
  assert.equal(fixture.dialog.open, false);
});
