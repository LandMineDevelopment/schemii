import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { catalogContract, importedDraft } from "../../src/schemii/schemoo/web/model-draft.js";
import { splitDraft } from "../../src/schemii/schemoo/web/model-state.js";

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

// Full shipped page/modules, intercepted before any network access. No server,
// credentials, target database or persisted user model participates in this case.
test("graph status follows idle, pending, settled, failed, switched and deleted models", async ({ page }) => {
  const origin = "https://schemoo-fixture.invalid";
  const unexpected = [], errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const catalog = { database: "fixture", namespace: "public", fingerprint: "graph-status-v1", notice: "Disposable graph fixture",
    tables: [{ name: "people", primaryKey: ["id"], columns: [{ name: "id", dataType: "integer", nullable: false }] }], relationships: [] };
  const emptyCatalog = { ...catalog, tables: [] };
  const names = { model_ready: "Ready model", model_slow: "Slow model", model_fast: "Latest model", model_empty: "Empty model", model_broken: "Unavailable model" };
  let available = [], deleting = false;
  const models = Object.fromEntries(Object.entries(names).map(([id, name]) => [id, {
    id, name, database: "fixture", namespace: "public", connectionId: id, revision: 1, layoutRevision: 1, exploreRevision: 1,
    catalogFingerprint: catalog.fingerprint, ...splitDraft(id === "model_empty"
      ? { ...importedDraft(catalog), root: null, nodes: [], edges: [], fields: [], exposedFields: [], sourceContract: catalogContract(emptyCatalog) }
      : importedDraft(catalog)),
  }]));
  const modelGates = new Map(), catalogGates = new Map(), validationGates = new Map();
  const started = new Set();
  const plan = { sql: "SELECT 1;", usedRelationships: [], grain: "detail rows", warnings: [], sourceIssues: [], activeScopes: [], cycleEdges: [] };
  await page.route("**/*", async route => {
    const url = new URL(route.request().url()), path = url.pathname;
    if (url.origin !== origin) { unexpected.push(url.href); return route.abort(); }
    if (path === "/api/v1/auth/me") return route.fulfill({ json: { user: { username: "fixture" }, capabilities: ["schemoo:access"] } });
    if (path === "/api/v1/connections") return route.fulfill({ json: { connections: [] } });
    if (path === "/api/v1/schemoo/models") return route.fulfill({ json: { models: available.map(id => models[id]) } });
    const match = /^\/api\/v1\/schemoo\/models\/(model_\w+)(?:\/(validate|previews|dependencies))?$/.exec(path);
    if (match) {
      const [, id, operation] = match;
      if (operation === "previews") return route.fulfill({ json: { previews: [] } });
      if (operation === "dependencies") return route.fulfill({ json: { dashboards: [] } });
      if (route.request().method() === "DELETE") {
        deleting = true; available = available.filter(candidate => candidate !== id);
        return route.fulfill({ status: 204 });
      }
      started.add(`${id}:${operation || "model"}`);
      const gate = (operation ? validationGates : modelGates).get(id);
      const response = gate ? await gate.promise : null;
      return route.fulfill(response || (id === "model_broken"
        ? { status: 503, json: { error: { message: "Fixture model unavailable" } } }
        : { json: operation ? plan : models[id] }));
    }
    if (path === "/api/v1/schemoo/catalog") {
      const id = url.searchParams.get("connection_id");
      started.add(`${id}:catalog`);
      await catalogGates.get(id)?.promise;
      return route.fulfill({ json: id === "model_empty" ? emptyCatalog : catalog });
    }
    const file = path === "/schemoo" ? new URL("../../src/schemii/schemoo/web/index.html", import.meta.url)
      : path.startsWith("/schemoo-assets/") ? new URL(`../../src/schemii/schemoo/web/${path.slice(16)}`, import.meta.url)
      : path.startsWith("/assets/common/") ? new URL(`../../src/schemii/common/web/assets/${path.slice(15)}`, import.meta.url) : null;
    if (file) {
      const contentType = path.endsWith(".js") ? "text/javascript" : path.endsWith(".css") ? "text/css" : "text/html";
      return route.fulfill({ contentType, body: await readFile(file) });
    }
    unexpected.push(path); return route.abort();
  });
  const status = page.locator("#graph-status"), library = page.locator("#model-library");
  const choose = async id => page.locator(".model-option").filter({ has: page.locator("strong", { hasText: names[id] }) }).click();
  const open = async () => {
    await page.locator("#open-models").click();
    await expect(page.locator(".model-list-row")).toHaveCount(available.length);
  };
  const settle = async id => {
    await expect.poll(() => started.has(`${id}:validate`)).toBe(true);
    await expect(status).toHaveText("Checking relationships…");
    validationGates.get(id).resolve({ json: plan });
    await expect(status).toHaveText("1 objects · cycle-free model");
  };

  await page.goto(`${origin}/schemoo`);
  await expect(library).toContainText("No saved models yet");
  await page.locator("#close-model-library").click();
  await expect(status).toHaveText("Choose a model to see its relationships.");
  await expect(page.locator("#save-model")).toBeDisabled();
  await expect(page.locator("#delete-model")).toBeDisabled();
  await expect(page.locator("#run")).toBeDisabled();

  available = Object.keys(models);
  modelGates.set("model_ready", deferred()); catalogGates.set("model_ready", deferred()); validationGates.set("model_ready", deferred());
  await open(); await choose("model_ready");
  await expect.poll(() => started.has("model_ready:model")).toBe(true);
  await expect(status).toHaveText("Loading model and relationships…");
  modelGates.get("model_ready").resolve({ json: models.model_ready });
  await expect.poll(() => started.has("model_ready:catalog")).toBe(true);
  await expect(status).toHaveText("Loading model and relationships…");
  catalogGates.get("model_ready").resolve();
  await settle("model_ready");
  await expect(library).not.toBeVisible();

  // Real library clicks start overlapping loads; the obsolete failure cannot
  // remove the latest loading status, re-enable the workbench or close its picker.
  modelGates.set("model_slow", deferred()); modelGates.set("model_fast", deferred()); validationGates.set("model_fast", deferred());
  await open(); await choose("model_slow"); await choose("model_fast");
  await expect.poll(() => started.has("model_fast:model")).toBe(true);
  const slowResponse = page.waitForResponse(response => response.url().endsWith("/models/model_slow"));
  modelGates.get("model_slow").resolve({ status: 503, json: { error: { message: "Obsolete failure" } } });
  await slowResponse;
  await expect(status).toHaveText("Loading model and relationships…");
  await expect(page.locator("#workbench")).toHaveAttribute("inert", "");
  await expect(library).toBeVisible();
  catalogGates.set("model_fast", deferred());
  modelGates.get("model_fast").resolve({ json: models.model_fast });
  await expect.poll(() => started.has("model_fast:catalog")).toBe(true);
  await expect(page.locator("#model-name")).toHaveValue(names.model_ready);
  await library.getByRole("button", { name: "Delete model Ready model", exact: true }).click();
  await page.getByRole("dialog", { name: "Delete model?", exact: true }).getByRole("button", { name: "Delete model", exact: true }).click();
  await expect(library).toContainText("Deleted “Ready model”");
  await expect(page.locator("#model-name")).toHaveValue("");
  await expect(status).toHaveText("Loading model and relationships…");
  await expect(library).toBeVisible();
  catalogGates.get("model_fast").resolve();
  await settle("model_fast");
  await expect(library).not.toBeVisible();
  await expect(page.locator("#model-name")).toHaveValue(names.model_fast);

  // A validation failure must not label a model cycle-free.
  validationGates.set("model_fast", deferred());
  await page.locator("#reload-model").click();
  await expect(status).toHaveText("Checking relationships…");
  validationGates.get("model_fast").resolve({ status: 422, json: { error: { message: "Fixture validation failed" } } });
  await expect(status).toHaveText("Model check failed · review Preview");

  await open(); await choose("model_broken");
  await expect(library).toContainText("Fixture model unavailable");
  await expect(status).toHaveText("Could not load model · previous model retained");
  await expect(page.locator("#model-name")).toHaveValue(names.model_fast);
  await choose("model_empty");
  await expect(status).toHaveText("No objects yet · add a table to build this model");
  await expect(page.locator(".sc-node")).toHaveCount(0);
  deleting = false;
  await page.locator("#delete-model").click();
  await page.getByRole("dialog", { name: "Delete model?", exact: true }).getByRole("button", { name: "Delete model", exact: true }).click();
  await expect.poll(() => deleting).toBe(true);
  await expect(status).toHaveText("Choose a model to see its relationships.");
  await expect(page.locator("#delete-model")).toBeDisabled();
  await expect(page.locator("#run")).toBeDisabled();

  await page.goto(`${origin}/schemoo?model=model_broken`);
  await expect(status).toHaveText("Model unavailable · choose another model");
  await expect(page.locator("#error")).toContainText("Fixture model unavailable");
  expect(unexpected).toEqual([]);
  expect(errors).toEqual([]);
});
