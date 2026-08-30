const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function field(value = "") { return { value }; }

async function main() {
  const context = vm.createContext({
    window: {},
    Option: class Option { constructor(text, value) { this.text = text; this.value = value; } },
    TypeError,
    encodeURIComponent, URL, URLSearchParams
  });
  vm.runInContext(fs.readFileSync("src/schemii/shared_web/profile-manager.js", "utf8"), context);
  const shared = context.window.SchemiiShared;
  const fields = {
    id: field(), name: field(), host: field(), port: field(), database: field(),
    user: field(), password: field("secret"), sslmode: field(), timeout: field()
  };
  const form = shared.createProfileForm({ fields, defaults: { name: "Analytics" } });
  form.fill();
  assert.equal(fields.name.value, "Analytics");
  assert.equal(fields.password.value, "");
  Object.assign(fields.name, { value: " Local " });
  Object.assign(fields.host, { value: " db " });
  Object.assign(fields.port, { value: "5433" });
  Object.assign(fields.database, { value: " demo " });
  Object.assign(fields.user, { value: " reader " });
  Object.assign(fields.password, { value: " unchanged " });
  Object.assign(fields.sslmode, { value: "require" });
  Object.assign(fields.timeout, { value: "12" });
  assert.deepEqual(JSON.parse(JSON.stringify(form.read())), {
    name: "Local", host: "db", port: 5433, dbname: "demo", user: "reader",
    password: " unchanged ", sslmode: "require", timeout: 12
  });

  const calls = [];
  const repository = shared.createProfileRepository({ postgresClient: { request: async (path, options) => {
    calls.push([path, options]);
    if (path.includes("/namespaces?")) {
      const cursor = new URL(`http://local${path}`).searchParams.get("cursor");
      const entry = cursor
        ? { name: "pg_catalog", classification: "pg_catalog", system: true }
        : { name: "public", classification: "user", system: false };
      return {
        profileId: "profile one", profileFingerprint: "a".repeat(64), database: "demo", scope: "all",
        catalogFingerprint: "b".repeat(64), entries: [entry], namespaces: [entry.name],
        page: { pageSize: 1, returned: 1, hasMore: !cursor, nextCursor: cursor ? null : "next" },
      };
    }
    if (path.includes("/relations?")) {
      const cursor = new URL(`http://local${path}`).searchParams.get("cursor");
      const name = cursor ? "z_after_old_cutoff" : "orders";
      const entry = { profileId: "profile one", database: "demo", namespace: "public", relation: name, name, kind: cursor ? "foreign_table" : "table" };
      return {
        profileId: "profile one", profileFingerprint: "a".repeat(64), database: "demo", namespace: "public",
        catalogFingerprint: "c".repeat(64), entries: [entry], relations: [entry],
        page: { pageSize: 1, returned: 1, hasMore: !cursor, nextCursor: cursor ? null : "next" },
      };
    }
    if (path === "/api/postgres/profiles") return { profiles: [] };
    return { id: "saved" };
  } } });
  assert.deepEqual(Array.from(await repository.list()), []);
  await repository.save("profile one", { name: "Demo" });
  assert.equal(calls[1][0], "/api/postgres/profiles/profile%20one");
  assert.deepEqual(Array.from(await repository.namespaces("profile one", "demo", { scope: "all", pageSize: 1 })), ["public", "pg_catalog"]);
  const relations = await repository.relationCatalog("profile one", "demo", "public", { pageSize: 1 });
  assert.deepEqual(Array.from(relations.relations, item => item.name), ["orders", "z_after_old_cutoff"]);

  const select = {
    value: "", disabled: true, options: [],
    replaceChildren(...options) { this.options = options; }
  };
  assert.equal(shared.initializeNamespaceSelect(select, ["one", "two"], { preferred: "two" }), "two");
  assert.equal(select.value, "two");
  assert.equal(select.disabled, false);
  assert.equal(shared.initializeNamespaceSelect(select, []), null);
  assert.equal(select.disabled, true);
  shared.initializeNamespaceSelect(select, [{ name: "pg_catalog", classification: "pg_catalog", system: true }]);
  assert.equal(select.options[0].text, "pg_catalog (pg catalog)");
  const target = shared.targetPresentation({ state: "verified", profileName: "Demo", profileId: "local", database: "demo", namespace: "public", relation: "orders", verifiedAt: "2026-08-14T12:00:00Z", verificationSource: "PostgreSQL relation verification" });
  assert.equal(target.label, "Verified");
  assert.match(shared.formatTargetPresentation(target), /Verified: Demo \(local\) · demo\.public\.orders · Source: PostgreSQL relation verification/);
  for (const state of ["suggested", "selected", "linked", "verified"]) assert.equal(shared.targetPresentation({ state, profileId: "local", database: "demo", namespace: "public" }).label.toLowerCase(), state);
  const deletion = shared.profileDeletionConfirmation({ name: "Demo", dbname: "demo" }, { profileId: "local", impact: { schemas: ["design-a"], dashboards: [{ id: "dash-a" }] } });
  assert.match(deletion, /Impact \(2\):[\s\S]*schemas: design-a[\s\S]*dashboards: \{"id":"dash-a"\}/);
  const schemiiSource = fs.readFileSync("src/schemii/web/app.js", "utf8");
  const deleteFlow = schemiiSource.slice(schemiiSource.indexOf('if (action === "delete")'), schemiiSource.indexOf("postgresState.selectedProfileId = profileId", schemiiSource.indexOf('if (action === "delete")')));
  assert.match(deleteFlow, /await postgresProfileRepository\.deletionImpact\(profileId\)[\s\S]*confirm\(window\.SchemiiShared\.profileDeletionConfirmation/, "profile impact must be fetched before confirmation");
  assert.equal((deleteFlow.match(/confirm\(/g) || []).length, 1, "profile deletion must require exactly one confirmation");
  const schemiiHtml = fs.readFileSync("src/schemii/web/index.html", "utf8");
  const schemerHtml = fs.readFileSync("src/schemii/schemer_web/index.html", "utf8");
  for (const html of [schemiiHtml, schemerHtml]) assert.match(html, />Connection timeout \(seconds\)<input/, "profile forms must name connection timeout units without changing the timeout field");
  console.log("Shared profile manager tests passed");
}

main().catch(error => { console.error(error); process.exitCode = 1; });
